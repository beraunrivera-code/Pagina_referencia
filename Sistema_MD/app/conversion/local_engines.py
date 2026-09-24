"""Workers locales explícitos. Entorno separado y auditoría Python, NO sandbox OS.

El contrato publicado sigue siendo v1, con crudos y cobertura visibles. No sustituye
F2/v2 ni certifica fidelidad o ausencia de egress desde bibliotecas nativas.
"""
from pathlib import Path
import hashlib
import os
import re
import subprocess
import time
import uuid

from .documents import digest, json_bytes, load_json, parse_markdown, read_stable
from .local_io import exclusive, scratch
from .storage import publish, report, verify_artifacts

ENGINES = ("docling", "markitdown")
CONDITIONAL_ENGINES = ("marker", "mineru")
WORKER = Path(__file__).resolve().parents[1] / "workers" / "local_engine_worker.py"
WORKERS_ROOT = Path(__file__).resolve().parents[2] / "workers"
CONFIG_PATH = Path(__file__).resolve().parents[2] / "motores.local.json"
MAX_SOURCE_BYTES = 100_000_000
MAX_OUTPUT_BYTES = 32_000_000
MAX_LOG_BYTES = 1_000_000
MAX_PAGES = 500


class LocalEngineError(ValueError):
    def __init__(self, code):
        self.code = code
        messages = {
            "python_missing": "Falta el Python del worker; configura su ruta. No se instala automáticamente.",
            "engine_missing": "El motor no está instalado en ese worker.",
            "assets_missing": "Faltan activos locales Docling/EasyOCR; no se descargan ni se usa API.",
            "network_blocked": "El worker intentó una conexión y la política Python la bloqueó.",
            "process_blocked": "El worker intentó iniciar otro programa no permitido.",
            "worker_timeout": "Tiempo del worker agotado; no hay reintento ni cambio de motor automático.",
            "worker_output_limit": "Salida del worker supera el límite; no se publica truncada.",
            "worker_failed": "El motor local falló; originales intactos. Revisa entorno y alcance.",
            "worker_protocol": "Respuesta del worker inválida; no se publica.",
            "format_rejected": "Formato fuera del alcance selectivo de este motor local.",
        }
        super().__init__(messages.get(code, messages["worker_failed"]))


def _config():
    if not CONFIG_PATH.exists():
        return {"version": 1, "engines": {}}
    if CONFIG_PATH.stat().st_size > 16000:
        raise ValueError("Configuración de motores demasiado grande")
    value = load_json(CONFIG_PATH.read_bytes())
    if (not isinstance(value, dict) or value.get("version") != 1
            or not isinstance(value.get("engines"), dict)
            or any(k not in ENGINES for k in value["engines"])
            or any(not isinstance(v, dict) or any(k not in {"python", "models_path"} for k in v)
                   or any(not isinstance(s, str) or not s.strip() for s in v.values())
                   for v in value["engines"].values())):
        raise ValueError("Configuración de motores inválida")
    return value


def _interpreter(path):
    """Ruta canónica de un intérprete SIN seguir su enlace en POSIX.

    En Linux ``.venv/bin/python`` es un enlace al Python base: resolverlo lanza el Python
    del sistema, que ya no ve la venv (la detecta por la ruta invocada) y el motor «falta».
    En Windows ``python.exe`` es una copia real y se conserva la resolución completa.
    """
    return Path(path).resolve() if os.name == "nt" else Path(os.path.abspath(path))


def _portable(path, interpreter=False):
    path = _interpreter(path) if interpreter else Path(path).resolve()
    base = CONFIG_PATH.parent.resolve()
    return path.relative_to(base).as_posix() if path.is_relative_to(base) else str(path)


def _configured_path(value):
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    path = (CONFIG_PATH.parent / path).resolve()
    if not path.is_relative_to(CONFIG_PATH.parent.resolve()):
        raise ValueError("Ruta relativa fuera de la instalación")
    return path


def engine_configuration(engine):
    """Lectura de rutas efectivas sin ejecutar, importar ni validar su presencia."""
    if engine not in ENGINES:
        raise LocalEngineError("engine_missing")
    saved = _config()["engines"].get(engine, {})
    python = os.environ.get(f"SISTEMA_MD_{engine.upper()}_PYTHON")
    if not python:
        python = _configured_path(saved.get("python")) or WORKERS_ROOT / engine / ".venv" / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python")
    models = (os.environ.get("SISTEMA_MD_DOCLING_MODELS") or _configured_path(saved.get("models_path"))) if engine == "docling" else None
    return {"engine": engine, "python": str(python), "models_path": str(models) if models else None,
            "source": str(CONFIG_PATH), "external_calls": 0}


def configure_engine(engine, python, models_path=None):
    """Guarda rutas locales (str/Path), nunca claves. No instala ni ejecuta modelos."""
    executable = engine_python(engine, python)
    forced = os.environ.get(f"SISTEMA_MD_{engine.upper()}_PYTHON")
    if forced and _interpreter(forced) != executable:
        raise ValueError(f"SISTEMA_MD_{engine.upper()}_PYTHON fija otro intérprete; ajusta esa variable antes de guardar otra ruta")
    entry = {"python": _portable(executable, interpreter=True)}
    if models_path:
        if engine != "docling":
            raise ValueError("MarkItDown no usa una carpeta de pesos Docling")
        if str(models_path).startswith(("\\\\", "//")):
            raise ValueError("Los modelos deben estar en un disco local, no en una ruta de red")
        models = Path(models_path).resolve(strict=True)
        if not models.is_dir():
            raise ValueError("Selecciona la carpeta local de modelos")
        forced_models = os.environ.get("SISTEMA_MD_DOCLING_MODELS")
        if forced_models and Path(forced_models).resolve() != models:
            raise ValueError("SISTEMA_MD_DOCLING_MODELS fija otros activos; ajusta esa variable antes de guardar otra ruta")
        entry["models_path"] = _portable(models)
    with exclusive(CONFIG_PATH.parent, "config-motores"):
        config = _config()
        config["engines"][engine] = entry
        temporary = CONFIG_PATH.with_name(".motores-" + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(json_bytes(config))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, CONFIG_PATH)
        finally:
            temporary.unlink(missing_ok=True)
    return {"engine": engine, "configuration": entry, "path": str(CONFIG_PATH), "external_calls": 0,
            "notice": "Rutas guardadas; importación, activos y fidelidad pendientes de comprobar."}


def engine_python(engine, python=None):
    if engine not in ENGINES:
        raise LocalEngineError("engine_missing")
    configured = (python or os.environ.get(f"SISTEMA_MD_{engine.upper()}_PYTHON")
                  or _configured_path(_config()["engines"].get(engine, {}).get("python")))
    path = Path(configured) if configured else WORKERS_ROOT / engine / ".venv" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not path.is_absolute() or str(path).startswith(("\\\\", "//")):
        raise LocalEngineError("python_missing")
    if not re.fullmatch(r"python(?:\d+(?:\.\d+)*)?(?:\.exe)?", path.name, re.I) or not path.is_file():
        raise LocalEngineError("python_missing")
    return _interpreter(path)


def _environment(folder):
    # Allowlist, no perfiles del usuario ni claves/tokens heredados. -I ignora
    # PYTHONPATH y user-site; el Python configurado sigue siendo software confiado.
    result = {k: v for k, v in os.environ.items() if k.upper() in {
        "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "PATH", "COMSPEC", "PATHEXT"}}
    result.update(HOME=str(folder), USERPROFILE=str(folder), APPDATA=str(folder),
        LOCALAPPDATA=str(folder), TEMP=str(folder), TMP=str(folder),
        HF_HUB_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", TRANSFORMERS_OFFLINE="1",
        DO_NOT_TRACK="1", OMP_NUM_THREADS="2", TOKENIZERS_PARALLELISM="false")
    return result


def _run_worker(python, job, folder, timeout):
    """Stdout/stderr en archivos acotados; nunca captura una salida ilimitada en RAM."""
    out_path, err_path = folder / "worker.stdout", folder / "worker.stderr"
    with out_path.open("wb") as out, err_path.open("wb") as err:
        process = subprocess.Popen([str(python), "-I", "-B", str(WORKER)], cwd=folder,
            env=_environment(folder), stdin=subprocess.PIPE, stdout=out, stderr=err,
            shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            try:
                process.stdin.write(json_bytes(job))
                process.stdin.close()
            except OSError:
                raise LocalEngineError("worker_failed") from None
            end = time.monotonic() + timeout
            while process.poll() is None:
                if out_path.stat().st_size > MAX_OUTPUT_BYTES or err_path.stat().st_size > MAX_LOG_BYTES:
                    raise LocalEngineError("worker_output_limit")
                if time.monotonic() >= end:
                    raise LocalEngineError("worker_timeout")
                time.sleep(0.03)
            if out_path.stat().st_size > MAX_OUTPUT_BYTES or err_path.stat().st_size > MAX_LOG_BYTES:
                raise LocalEngineError("worker_output_limit")
            if process.returncode:
                raise LocalEngineError("worker_failed")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    try:
        payload = load_json(out_path.read_bytes())
        if not isinstance(payload, dict) or payload.get("protocol") != 1 or type(payload.get("ok")) is not bool:
            raise ValueError
    except (OSError, ValueError, TypeError, RecursionError):
        raise LocalEngineError("worker_protocol") from None
    if not payload["ok"]:
        code = payload.get("code")
        # El texto arbitrario del motor no llega a diagnósticos.
        raise LocalEngineError(code if code in {"engine_missing", "assets_missing", "network_blocked",
            "process_blocked", "format_rejected", "worker_output_limit"} else "worker_failed")
    return payload


def _probe(engine, python, folder):
    return _run_worker(python, {"protocol": 1, "op": "probe", "engine": engine}, folder, 15)


def probe_engines():
    """Estado local: sin importar motores, inferir modelos ni descargar activos."""
    rows = []
    for engine in ENGINES:
        row = {"engine": engine, "integrated": True, "python_present": False,
            "present": False, "importable": None, "models_ready": None,
            "control_passed": None, "offline_verified": False, "version": None,
            "python_variable": f"SISTEMA_MD_{engine.upper()}_PYTHON"}
        try:
            python = engine_python(engine)
            row.update(python_present=True, python=str(python))
            with scratch(WORKERS_ROOT / "diagnostico") as temporary:
                result = _probe(engine, python, Path(temporary))
            row.update(present=result["present"], version=result.get("version"))
            row["status"] = "presente_sin_control" if result["present"] else "no_instalado"
        except (OSError, ValueError) as exc:
            row["status"] = exc.code if isinstance(exc, LocalEngineError) else "worker_failed"
        rows.append(row)
    rows.extend({"engine": name, "integrated": False, "status": "condicional_pendiente",
        "present": None, "importable": None, "models_ready": None,
        "control_passed": None, "offline_verified": False} for name in CONDITIONAL_ENGINES)
    return {"engines": rows, "external_calls": 0,
        "notice": "Presencia no prueba importación, activos, fidelidad ni offline. Auditoría Python no es sandbox OS."}


def _inventory(source, engine, pages):
    if engine == "markitdown":
        if pages is not None:
            raise ValueError("MarkItDown conserva un documento lógico; no admite paginación artificial")
        if source.suffix.lower() not in {".docx", ".xlsx", ".pptx", ".html", ".htm", ".epub", ".txt", ".md", ".csv"}:
            raise LocalEngineError("format_rejected")
        return [1], "document"
    if source.suffix.lower() == ".pdf":
        import pymupdf
        with pymupdf.open(source) as document:
            if document.needs_pass:
                raise ValueError("PDF cifrado; no se intenta eludir protección")
            count = document.page_count
    elif source.suffix.lower() in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}:
        from PIL import Image
        with Image.open(source) as document:
            count = getattr(document, "n_frames", 1)
            if document.width * document.height > 40_000_000:
                raise ValueError("Imagen supera 40 millones de píxeles")
    else:
        raise LocalEngineError("format_rejected")
    if not 1 <= count <= MAX_PAGES:
        raise ValueError("Documento fuera del límite de 1–500 unidades")
    requested = list(range(1, count + 1)) if pages is None else pages
    if (not isinstance(requested, list) or not requested
            or any(type(n) is not int or not 1 <= n <= count for n in requested)
            or len(set(requested)) != len(requested)):
        raise ValueError("Alcance de páginas inválido")
    return sorted(requested), "page"


def _models_fingerprint(models):
    if models is None or not models.is_dir() or not (models / "EasyOcr").is_dir():
        raise LocalEngineError("assets_missing")
    files = sorted(p for p in models.rglob("*") if p.is_file())
    if not files or len(files) > 1000 or sum(p.stat().st_size for p in files) > 8_000_000_000:
        raise LocalEngineError("assets_missing")
    fingerprints = {}
    for path in files:
        if not path.resolve().is_relative_to(models.resolve()) or path.is_symlink():
            raise LocalEngineError("assets_missing")
        before = path.stat()
        checksum = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                checksum.update(chunk)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise LocalEngineError("assets_missing")
        fingerprints[path.relative_to(models).as_posix()] = checksum.hexdigest()
    return digest(json_bytes(fingerprints))


def _cached(root, signature):
    for row in report(root):
        if not row["integrity_ok"]:
            continue
        folder = Path(row["output"])
        if (folder / "document.json").stat().st_size > MAX_OUTPUT_BYTES:
            continue
        doc = load_json((folder / "document.json").read_bytes())
        if doc.get("local_engine_signature") == signature and verify_artifacts(folder):
            qa = load_json((folder / "verificacion.json").read_bytes())
            return {"id": row["id"], "output": str(folder), "reused": True, **qa}
    return None


def _normalise(payload, source, source_hash, engine, expected, locator_kind, signature):
    if (payload.get("engine") != engine or not isinstance(payload.get("units"), list)
            or not isinstance(payload.get("raw"), (dict, list)) or not isinstance(payload.get("markdown"), str)):
        raise LocalEngineError("worker_protocol")
    warnings = ["Extracción parcial por canales; fidelidad pendiente. Contrato v1, no normalización v2 completa.",
        "Figuras, vínculos, tablas/celdas y geometría detallada requieren revisión del crudo y original.",
        "Auditoría Python bloquea sockets/subprocesos; aislamiento de red del sistema operativo no verificado."]
    units, seen = [], set()
    for item in payload["units"]:
        if not isinstance(item, dict):
            raise LocalEngineError("worker_protocol")
        number, markdown = item.get("number"), item.get("markdown")
        if (type(number) is not int or number not in expected or number in seen or not isinstance(markdown, str)):
            raise LocalEngineError("worker_protocol")
        seen.add(number)
        units.append({"number": number, "locator": {"kind": locator_kind, "value": number if locator_kind == "page" else source.name},
            "blocks": parse_markdown(markdown, number)})
    if payload.get("partial"):
        warnings.append("El motor informó extracción parcial; no se autopromueve a completa.")
    doc = {"schema_version": 1, "title": source.stem or source.name, "input_kind": "local_" + engine,
        "source_path": str(source), "source_sha256": source_hash, "expected_units": expected,
        "units": units, "producer_warnings": warnings, "structure_asset": f"crudos/{engine}.json",
        "local_engine_signature": signature, "engine": engine, "engine_version": payload.get("version"),
        "coverage": {"channels": "partial", "locator_kind": locator_kind, "missing": sorted(set(expected) - seen)},
        "offline_verified": False}
    assets = {f"crudos/{engine}.json": json_bytes(payload["raw"]),
              f"crudos/{engine}.md": payload["markdown"].encode("utf-8")}
    if sum(map(len, assets.values())) > MAX_OUTPUT_BYTES:
        raise LocalEngineError("worker_output_limit")
    return doc, assets


def convert_with_engine(root, source, engine, pages=None, *, python=None, models_path=None, timeout=180):
    """Convierte con motor EXPLÍCITO; publica en la misma biblioteca MD/JSON.

    Entrada local <=100 MB; Docling <=500 páginas. Original conservado como activo.
    MarkItDown es documento lógico sin números de página inventados. Ningún fallback.
    """
    root = Path(root).resolve()
    if not isinstance(source, (str, Path)) or str(source).startswith(("http:", "https:", "file:", "data:", "\\\\", "//")):
        raise LocalEngineError("format_rejected")
    source = Path(source).resolve(strict=True)
    if not source.is_file() or source.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Se requiere un archivo local de hasta 100 MB")
    if type(timeout) not in (int, float) or not 1 <= timeout <= 1800:
        raise ValueError("Tiempo del worker: 1–1800 segundos")
    python = engine_python(engine, python)
    models_value = (models_path or os.environ.get("SISTEMA_MD_DOCLING_MODELS")
                    or _configured_path(_config()["engines"].get("docling", {}).get("models_path"))) if engine == "docling" else None
    if models_value and str(models_value).startswith(("\\\\", "//")):
        raise LocalEngineError("assets_missing")
    try:
        models = Path(models_value).resolve(strict=True) if models_value else None
    except OSError:
        raise LocalEngineError("assets_missing") from None
    models_hash = _models_fingerprint(models) if engine == "docling" else None
    with exclusive(root, "worker-local"), scratch(root) as temporary:
        folder = Path(temporary)
        probe = _probe(engine, python, folder)
        if not probe.get("present"):
            raise LocalEngineError("engine_missing")
        source_data = read_stable(source)
        if len(source_data) > MAX_SOURCE_BYTES:
            raise ValueError("La fuente creció y supera 100 MB")
        copied = folder / ("entrada" + source.suffix.lower())
        copied.write_bytes(source_data)
        expected, locator_kind = _inventory(copied, engine, pages)
        signature = digest(json_bytes({"source": digest(source_data), "engine": engine, "pages": expected,
            "dependencies": probe.get("dependency_fingerprint"), "python": probe.get("python_version"),
            "worker": digest(WORKER.read_bytes()), "normalizer": digest(Path(__file__).read_bytes()),
            "runtime": str(python), "models": models_hash, "contract": 1}))
        existing = _cached(root, signature)
        if existing:
            return {**existing, "engine": engine, "source_sha256": digest(source_data),
                    "external_calls": 0, "offline_verified": False}
        job = {"protocol": 1, "op": "convert", "engine": engine, "source": str(copied),
            "source_sha256": digest(source_data), "pages": expected if engine == "docling" else None,
            "models_path": str(models) if models else None}
        payload = _run_worker(python, job, folder, timeout)
        doc, assets = _normalise(payload, source, digest(source_data), engine, expected, locator_kind, signature)
        assets["fuente/original" + source.suffix.lower()] = source_data
        result = publish(root, doc, assets)
    return {**result, "engine": engine, "source_sha256": digest(source_data),
            "external_calls": 0, "offline_verified": False}
