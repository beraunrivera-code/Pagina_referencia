"""Adaptadores explícitos de una página. Sin reintentos ni selección de cuentas implícitos."""
import base64
from contextlib import closing
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import time
from urllib import request, error
import uuid

from .credentials import get_key, ENV_NAMES, key_location
from .documents import digest, json_bytes, load_json, read_stable
from .jobs import load_job, import_answer
from .storage import connect
from .provider_errors import ProviderFault, ProviderAttemptError, PriorAttemptError

PROVIDERS = ("gemini-api", "deepseek-api", "openai-api", "anthropic-api", "qwen-api",
             "gemini-cli", "antigravity-cli", "codex-cli", "claude-cli")
CLI_PROFILES = ("principal", "cuenta-1", "cuenta-2", "cuenta-3", "cuenta-4", "cuenta-5",
                "pro-1", "pro-2", "pro-3", "fab1", "fab2", "fab3", "fab4", "fab5")
ADAPTER_VERSION = 1
MAX_RESPONSE = 4_000_000

# Modelos de visión admitidos por proveedor. El catálogo de una cuenta CAMBIA: comprobado el
# 2026-09-13 contra /models y contra una página de control de respuesta conocida.
#   deepseek-flash               -> acertó los 5 campos del control (código, versión, estado,
#                                   fecha, foliación). Único con visión publicado hoy en la cuenta.
#   deepseek-v4-pro              -> devuelve contenido vacío ante una imagen: NO se admite.
#   deepseek-v4-flash-vision-exp -> ya no aparece en /models; se conserva por si vuelve.
# Antes se exigía ese último literal, así que el adaptador de imagen quedaba inalcanzable.
VISION_MODELS = {"deepseek-api": ("deepseek-flash", "deepseek-v4-flash-vision-exp")}


def profiles_for(provider):
    if provider == "antigravity-cli":
        return ("principal", "fab1", "fab2", "fab3", "fab4", "fab5")
    if provider in {"gemini-cli", "codex-cli", "claude-cli"}:
        return CLI_PROFILES[:9]
    if provider in ENV_NAMES:
        return ("principal",)
    raise ValueError("Proveedor desconocido")


def cli_binary(provider):
    if provider == "antigravity-cli":
        executable = shutil.which("agy")
        if not executable and os.name == "nt":
            candidate = Path("C:/FABRICA/bin/agy.exe")
            if candidate.is_file():
                executable = str(candidate)
        if executable:
            return [executable]
    elif provider == "gemini-cli":
        shim = shutil.which("gemini")
        node = shutil.which("node")
        if shim and os.name == "nt":
            bundle = Path(shim).parent / "node_modules/@google/gemini-cli/bundle/gemini.js"
            if node and bundle.is_file():
                return [node, str(bundle)]
        elif shim:
            return [shim]
    elif provider in {"codex-cli", "claude-cli"}:
        name = "codex" if provider == "codex-cli" else "claude"
        executable = shutil.which(name)
        if not executable and name == "claude":
            candidate = (Path(os.environ.get("USERPROFILE", "")) / ".local/bin/claude.exe" if os.name == "nt"
                         else Path.home() / ".local/bin/claude")
            if candidate.is_file():
                executable = str(candidate)
        if executable and Path(executable).suffix.lower() not in {".bat", ".cmd", ".ps1"}:
            return [executable]
    raise ValueError("CLI no localizado en una instalación compatible; ejecuta proveedores")


def cli_profile_dir(provider, profile):
    if provider not in {"gemini-cli", "codex-cli", "claude-cli"} or profile not in profiles_for(provider):
        raise ValueError("Perfil CLI inválido")
    base = os.environ.get("LOCALAPPDATA")
    if not base and os.name != "nt":
        # Equivalente XDG de %LOCALAPPDATA%: datos por usuario, fuera del proyecto.
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    if not base:
        raise ValueError("Windows no informó LOCALAPPDATA para aislar el perfil CLI")
    return Path(base).resolve() / "SistemaMD" / "perfiles_cli" / provider / profile


def cli_environment(provider, profile="principal"):
    """Aísla sesión por proceso y evita heredar claves de otros proveedores."""
    if profile not in profiles_for(provider):
        raise ValueError("Perfil CLI inválido")
    environment = {key: value for key, value in os.environ.items()
                   if not any(word in key.upper() for word in
                              ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "BASE_URL"))
                   and key not in {"CLAUDECODE", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "GEMINI_CLI_HOME",
                                   "ANTHROPIC_PROFILE", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                                   "CLAUDE_CODE_USE_FOUNDRY", "GOOGLE_GENAI_USE_VERTEXAI",
                                   "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION"}
                   and not key.startswith(("ANTHROPIC_FOUNDRY_", "ANTHROPIC_VERTEX_", "ANTHROPIC_BEDROCK_",
                                           "AWS_", "AZURE_"))}
    if provider == "antigravity-cli":
        if profile != "principal":
            raise ValueError("Los perfiles fab usan su usuario Windows, no un HOME simulado")
    elif provider != "gemini-cli" or profile != "principal":
        folder = cli_profile_dir(provider, profile)
        variable = {"gemini-cli": "GEMINI_CLI_HOME", "codex-cli": "CODEX_HOME",
                    "claude-cli": "CLAUDE_CONFIG_DIR"}[provider]
        environment[variable] = str(folder)
    if provider == "claude-cli":
        environment["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
        environment["CLAUDE_CODE_RESUME_INTERRUPTED_TURN"] = "0"
    return environment


def provider_status():
    rows = []
    for provider in PROVIDERS:
        try:
            ready = bool(get_key(provider)) if provider in ENV_NAMES else bool(cli_binary(provider))
            detail = "Clave presente" if provider in ENV_NAMES else "Ejecutable localizado"
        except (OSError, ValueError):
            ready, detail = False, "No disponible"
        slots = []
        if provider in ENV_NAMES:
            for slot in range(1, 6):
                try:
                    present = bool(get_key(provider, slot))
                except (OSError, ValueError):
                    present = False
                slots.append({"slot": slot, "configured_locally": present,
                              "authentication_verified": False, "key_variable": key_location(provider, slot)[0]})
            ready = any(item["configured_locally"] for item in slots)
            detail = "Claves locales detectadas; no se ha comprobado autenticación, cuota o modelo"
        rows.append({"provider": provider, "configured_locally": ready, "key_slots": slots,
                     "detail": detail if ready else "Falta configurar",
                     "authentication_verified": False, "end_to_end_verified": False,
                     "key_variable": ENV_NAMES.get(provider), "profiles": profiles_for(provider), "external_calls": 0})
    return {"providers": rows, "notice": "Presencia local no prueba autenticación, saldo ni visión."}


def _http(url, headers, body, timeout):
    class NoRedirect(request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    req = request.Request(url, data=json_bytes(body), headers={"Content-Type": "application/json", **headers})
    try:
        with request.build_opener(NoRedirect).open(req, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE + 1)
            partial = response.headers.get("x-dashscope-partialresponse", "").lower() == "true"
    except error.HTTPError as exc:
        raise ProviderFault(f"http_{exc.code}") from None
    except (error.URLError, TimeoutError):
        raise ProviderFault("network_timeout") from None
    if len(raw) > MAX_RESPONSE:
        raise ProviderFault("response_size")
    payload = load_json(raw)
    if partial and isinstance(payload, dict):
        payload["_sistema_md_partial_response"] = True
    return payload


def _api(provider, model, contents, max_tokens, timeout, *, key=None):
    key = key or get_key(provider)
    if not key:
        raise ValueError("Falta configurar " + ENV_NAMES[provider])
    if provider in {"openai-api", "anthropic-api", "qwen-api"}:
        from .api_adapters import api_request
        return api_request(provider, model, contents, max_tokens, timeout, key=key, http=_http)
    instruction = contents["solicitud.md"].decode("utf-8") + "\nEsquema JSON:\n" + contents["respuesta.schema.json"].decode("utf-8")
    image = base64.b64encode(contents["pagina.png"]).decode("ascii")
    if provider == "gemini-api":
        body = {"contents": [{"role": "user", "parts": [{"text": instruction},
                 {"inlineData": {"mimeType": "image/png", "data": image}}]}],
                "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": max_tokens}}
        return _http(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                     {"x-goog-api-key": key}, body, timeout)
    body = {"model": model, "messages": [{"role": "user", "content": [
            {"type": "text", "text": instruction},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + image}}]}],
            "max_tokens": max_tokens, "response_format": {"type": "json_object"}, "stream": False}
    return _http("https://api.deepseek.com/chat/completions", {"Authorization": "Bearer " + key}, body, timeout)


def _cli(provider, model, folder, timeout, *, cli_profile="principal"):
    if provider == "antigravity-cli" and cli_profile.startswith("fab"):
        from .fab_bridge import run_fab
        return run_fab(cli_profile, model, folder, timeout)
    if provider in {"codex-cli", "claude-cli"}:
        from .session_cli import run_session_cli
        return run_session_cli(provider, model, folder, timeout, cli_profile,
                               binary=cli_binary(provider), environment=cli_environment(provider, cli_profile))
    prompt = ("Lee solo solicitud.md, respuesta.schema.json y la imagen pagina.png de esta carpeta. "
              "Cumple el encargo de conversión visual y devuelve el JSON. No ejecutes comandos ni modifiques archivos.")
    command = cli_binary(provider)
    command += ["--model", model, "--output-format", "json", "-p", prompt]
    if provider == "antigravity-cli":
        command += ["--mode", "plan", "--disable-slash-commands", "--json-schema", "respuesta.schema.json",
                    "--print-timeout", str(timeout) + "s"]
    else:
        command += ["--approval-mode", "plan"]
    # Carpeta mínima por intento. No equivale a aislamiento del sistema operativo.
    # No incluye flags de aprobación global ni credenciales en argumentos.
    with (folder / "stdout.json").open("xb") as out, (folder / "stderr.txt").open("xb") as err:
        try:
            options = {
                "cwd": folder,
                "stdin": subprocess.DEVNULL,
                "stdout": out,
                "stderr": err,
                "timeout": timeout,
                "shell": False,
                "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
            }
            environment = cli_environment(provider, cli_profile)
            if environment is not None:
                options["env"] = environment
            process = subprocess.run(command, **options)
        except subprocess.TimeoutExpired:
            raise ProviderFault("cli_timeout") from None
    if process.returncode:
        raise ProviderFault("cli_exit")
    path = folder / "stdout.json"
    if path.stat().st_size > MAX_RESPONSE:
        raise ProviderFault("response_size")
    return load_json(path.read_bytes())


def decode_response(provider, payload):
    if not isinstance(payload, dict) or payload.get("error"):
        raise ProviderFault("provider_error")
    if payload.get("_sistema_md_partial_response") is True:
        raise ProviderFault("incomplete_response")
    if provider in {"openai-api", "anthropic-api", "qwen-api"}:
        from .api_adapters import decode_api
        return decode_api(provider, payload)
    if provider == "gemini-api":
        choices = payload.get("candidates") or []
        if not choices or choices[0].get("finishReason") != "STOP":
            raise ProviderFault("incomplete_response")
        parts = choices[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        usage = payload.get("usageMetadata")
    elif provider == "deepseek-api":
        choices = payload.get("choices") or []
        if not choices or choices[0].get("finish_reason") != "stop":
            raise ProviderFault("incomplete_response")
        message = choices[0].get("message", {})
        text = message.get("content")
        # Un modelo de razonamiento consume el presupuesto ANTES de escribir: entonces devuelve
        # content vacío con finish_reason "stop", sin error. Medido el 2026-09-13: con
        # max_tokens 500 el razonamiento traía 4.106 caracteres correctos y content 0.
        if not (text or "").strip() and message.get("reasoning_content"):
            raise ProviderFault("empty_reasoning")
        usage = payload.get("usage")
    else:
        if provider == "claude-cli" and (payload.get("_exit_code", 0) != 0 or payload.get("is_error") or payload.get("subtype") != "success"):
            raise ProviderFault("incomplete_response")
        if provider == "codex-cli" and payload.get("status") != "SUCCESS":
            raise ProviderFault("incomplete_response")
        if provider == "antigravity-cli" and payload.get("status") != "SUCCESS":
            raise ProviderFault("provider_error")
        text = payload.get("structured_output", payload.get("response", payload.get("result")))
        usage = usage_from_payload(provider, payload)
    if isinstance(text, str):
        value = text.strip()
        match = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", value, re.S)
        text = load_json(match[1] if match else value)
    if not isinstance(text, dict):
        raise ValueError("No llegó un objeto JSON de conversión")
    return text, usage


def usage_from_payload(provider, payload):
    if not isinstance(payload, dict):
        return None
    if provider == "codex-cli" and isinstance(payload.get("usage"), dict):
        usage = dict(payload["usage"])
        values = [usage.get(name) for name in ("input_tokens", "output_tokens")]
        if all(type(value) is int and value >= 0 for value in values):
            usage["total_tokens"] = sum(values)
        return usage
    if provider in {"openai-api", "anthropic-api", "qwen-api", "claude-cli"}:
        from .api_adapters import usage_for_api
        return usage_for_api("anthropic-api" if provider == "claude-cli" else provider, payload)
    return payload.get("usageMetadata", payload.get("usage", payload.get("stats")))


def reported_tokens(usage):
    """Normaliza solo totales comunicados; ausencia nunca se convierte en cero."""
    if not isinstance(usage, dict):
        return None
    for name in ("totalTokenCount", "total_tokens", "totalTokens"):
        value = usage.get(name)
        if type(value) is int and value >= 0:
            return value
    return None


def execute_job(root, job_dir, provider, model, *, send=False, max_tokens=4096, timeout=120,
                key_slot=1, cli_profile="principal"):
    root, job_dir = Path(root).resolve(), Path(job_dir).resolve(strict=True)
    if provider not in PROVIDERS or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", model):
        raise ValueError("Proveedor o identificador de modelo inválidos")
    if provider in ENV_NAMES:
        key_location(provider, key_slot)
        if cli_profile != "principal":
            raise ValueError("El perfil CLI no se aplica a una API")
    elif key_slot != 1:
        raise ValueError("El perfil de clave es solo para API, no cambia cuentas CLI")
    elif cli_profile not in profiles_for(provider):
        raise ValueError("Perfil CLI inválido")
    if provider in {"openai-api", "anthropic-api", "qwen-api"}:
        from .api_adapters import validate_api_model
        validate_api_model(provider, model)
    if not 256 <= max_tokens <= 16384 or not 10 <= timeout <= 600:
        raise ValueError("Salida API: 256–16384 tokens; tiempo: 10–600 segundos")
    job, contents = load_job(job_dir)
    if len(contents["pagina.png"]) > 8_000_000 or not contents["pagina.png"].startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Se requiere PNG real de hasta 8 MB")
    if provider in VISION_MODELS and model not in VISION_MODELS[provider]:
        raise ValueError("Para imagen usa un modelo de visión comprobado: " + ", ".join(VISION_MODELS[provider]))
    signature = digest(json_bytes({"package": {n: digest(data) for n, data in contents.items()},
                                  "provider": provider, "model": model, "max_tokens": max_tokens,
                                  "adapter": ADAPTER_VERSION}))
    if not send:
        return {"status": "vista_previa", "provider": provider, "model": model, "key_slot": key_slot,
                "cli_profile": cli_profile,
                "job_id": job["job_id"], "unit": job["unit"], "external_calls": 0,
                "max_output_tokens": max_tokens if provider in ENV_NAMES else None,
                "notice": "Enviar consume cuota. CLI no ofrece un tope de tokens garantizado. fab usa otro usuario Windows y una carpeta con permisos restringidos.",
                "signature": signature}
    with closing(connect(root)) as db, db:
        db.execute("""CREATE TABLE IF NOT EXISTS provider_runs
                    (signature TEXT PRIMARY KEY, folder TEXT NOT NULL, state TEXT NOT NULL)""")
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT folder,state FROM provider_runs WHERE signature=?", (signature,)).fetchone()
        if row:
            folder = (root / row[0]).resolve()
            if not folder.is_relative_to(root / "ejecuciones"):
                raise ValueError("Intento fuera de la carpeta de ejecuciones")
            # Reimportación local: nunca factura por una respuesta ya guardada.
            if (folder / "respuesta.json").exists():
                reuse_folder = folder
            else:
                raise PriorAttemptError(row[1], folder)
        else:
            # La caché no necesita claves ni CLI. Solo un NUEVO envío los exige.
            if provider in ENV_NAMES:
                selected_key = get_key(provider) if key_slot == 1 else get_key(provider, key_slot)
                if not selected_key:
                    raise ValueError("Configura la clave del proveedor antes de enviar")
            else:
                cli_binary(provider)
            reuse_folder = None
            folder = root / "ejecuciones" / uuid.uuid4().hex
            folder.mkdir(parents=True)
            db.execute("INSERT INTO provider_runs VALUES (?,?,?)", (signature, str(folder.relative_to(root)), "reservado"))
    if reuse_folder:
        saved = load_json(read_stable(reuse_folder / "recibo.json"))
        if saved["answer_sha256"] != digest(read_stable(reuse_folder / "respuesta.json")):
            raise ValueError("Respuesta guardada alterada; no se reenvía automáticamente")
        result = import_answer(root, job_dir, reuse_folder / "respuesta.json", provider + ":" + model)
        return {**result, "provider": provider, "external_calls": 0, "response_reused": True,
                "run_folder": str(reuse_folder), "usage": saved["usage"]}
    started = time.monotonic()
    phase = "preparacion"
    try:
        for name, data in contents.items():
            (folder / name).write_bytes(data)
        (folder / "paquete.json").write_bytes(json_bytes({"files": {n: digest(d) for n, d in contents.items()}}))
        phase = "transporte"
        payload = (_api(provider, model, contents, max_tokens, timeout, key=selected_key) if provider in ENV_NAMES
                   else _cli(provider, model, folder, timeout, cli_profile=cli_profile))
        phase = "guardar_respuesta"
        (folder / "proveedor.json").write_bytes(json_bytes(payload))
        # El consumo se conserva ANTES de decodificar/validar: una respuesta mala puede cobrar.
        receipt = {"provider": provider, "requested_model": model, "key_slot": key_slot,
                   "cli_profile": cli_profile,
                   "reported_model": payload.get("model", payload.get("modelVersion")) if isinstance(payload, dict) else None,
                   "usage": usage_from_payload(provider, payload),
                   "elapsed_seconds": round(time.monotonic() - started, 3), "signature": signature}
        reported_cost = payload.get("total_cost_usd") if isinstance(payload, dict) else None
        if type(reported_cost) in (int, float) and reported_cost >= 0 and reported_cost < float('inf'):
            receipt["reported_cost_usd"] = reported_cost
        (folder / "recibo.json").write_bytes(json_bytes(receipt))
        phase = "decodificacion"
        answer, usage = decode_response(provider, payload)
        data = json_bytes(answer)
        receipt.update(usage=usage, answer_sha256=digest(data))
        (folder / "recibo.json").write_bytes(json_bytes(receipt))
        (folder / "respuesta.json").write_bytes(data)
        phase = "validacion_publicacion"
        result = import_answer(root, job_dir, folder / "respuesta.json", provider + ":" + model)
        state = "validado_estructuralmente"
    except Exception as exc:
        with closing(connect(root)) as db, db:
            db.execute("UPDATE provider_runs SET state='requiere_revision' WHERE signature=?", (signature,))
        code = exc.code if isinstance(exc, ProviderFault) else type(exc).__name__
        failure = ProviderAttemptError(provider, phase, code, folder)
        try:
            (folder / "error.json").write_bytes(json_bytes({"provider": provider, "model": model, "key_slot": key_slot,
                "cli_profile": cli_profile,
                "phase": phase, "code": code, "remedy": failure.remedy, "consumption_unknown": phase == "transporte"}))
        except OSError:
            pass  # El error principal no se oculta si el disco no permite guardar evidencia.
        raise failure from None
    with closing(connect(root)) as db, db:
        db.execute("UPDATE provider_runs SET state=? WHERE signature=?", (state, signature))
    return {**result, "provider": provider, "external_calls": 1, "response_reused": False,
            "run_folder": str(folder), "usage": usage,
            "notice": "Una invocación CLI puede hacer varias llamadas internas. Fidelidad aún pendiente."}


def run_history(root, limit=50):
    """Historial local acotado: no abre nativos, secretos ni contacta proveedores."""
    root = Path(root).resolve()
    if not 1 <= limit <= 200:
        raise ValueError("Límite de historial: 1–200")
    result = {"runs": [], "external_calls": 0,
              "notice": "Últimos intentos de este programa, no toda la actividad de Claude. Sin dato no significa cero consumo. Un recibo no valida fidelidad."}
    database = root / "indice.sqlite"
    if not database.exists():
        return result
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='provider_runs'").fetchone():
            return result
        rows = db.execute("SELECT folder,state FROM provider_runs ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
    for relative, state in rows:
        folder = (root / relative).resolve()
        if not folder.is_relative_to(root / "ejecuciones"):
            raise ValueError("Historial apunta fuera de ejecuciones")
        item = {"folder": str(folder), "state": state, "provider": "sin dato", "model": "sin dato",
                "total_tokens": None, "error_code": None}
        try:
            for filename in ("recibo.json", "error.json"):
                path = folder / filename
                if not path.exists():
                    continue
                if path.stat().st_size > MAX_RESPONSE:
                    raise ValueError("Recibo excede tamaño permitido")
                data = load_json(path.read_bytes())
                item['provider'] = data.get('provider', item['provider'])
                item['model'] = data.get('requested_model', data.get('model', item['model']))
                usage = data.get('usage')
                if isinstance(usage, dict):
                    item['total_tokens'] = reported_tokens(usage)
                item['reported_cost_usd'] = data.get('reported_cost_usd', item.get('reported_cost_usd'))
                if filename == 'error.json':
                    item['error_code'] = data.get('code')
        except (OSError, ValueError, TypeError, AttributeError):
            item['error_code'] = 'registro_ilegible'
        result['runs'].append(item)
    return result
