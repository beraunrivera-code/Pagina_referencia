"""Lotes visuales página a página: gasto explícito, estado durable y unión final."""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from pathlib import Path
import os
import re
import sqlite3
import time
import uuid

from .documents import SCHEMA_VERSION, digest, json_bytes, load_json, read_stable
from .diagnostics import record_failure
from .jobs import prepare_job
from .local_io import exclusive
from .provider_errors import PriorAttemptError, ProviderAttemptError
from .providers import PROVIDERS, execute_job, reported_tokens
from .storage import connect, output_folder, publish, verify_artifacts

AI_BATCH_VERSION = 1
FINAL_STATES = {"terminado", "reutilizado"}
STOP_STATES = {"requiere_revision", "consumo_desconocido"}


def database(root: Path):
    db = connect(Path(root).resolve())
    db.execute("""CREATE TABLE IF NOT EXISTS ai_batches (
        id TEXT PRIMARY KEY, plan TEXT NOT NULL, state TEXT NOT NULL,
        result TEXT, created_ns INTEGER NOT NULL, updated_ns INTEGER NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS ai_items (
        batch TEXT NOT NULL, position INTEGER NOT NULL, unit INTEGER NOT NULL,
        job_id TEXT NOT NULL, state TEXT NOT NULL, result TEXT,
        PRIMARY KEY(batch,position))""")
    db.commit()
    return db


def _current_package(root: Path, document_dir: Path) -> Path:
    root, document_dir = Path(root).resolve(), Path(document_dir).resolve(strict=True)
    expected = output_folder(root, str(document_dir))
    if expected != document_dir or not verify_artifacts(document_dir):
        raise ValueError("Selecciona un paquete íntegro de la memoria actual")
    return document_dir


def plan_ai_batch(root: Path, document_dir: Path, provider: str, model: str, *, pages=None,
                  call_budget=20, reported_token_budget=200000, max_tokens=8192,
                  timeout=120, key_slot=1, cli_profile="principal"):
    """Crea encargos locales y un plan reproducible. Nunca lee credenciales ni envía."""
    root = Path(root).resolve()
    document_dir = _current_package(root, document_dir)
    if provider not in PROVIDERS:
        raise ValueError("Proveedor inválido")
    if (type(call_budget) is not int or not 1 <= call_budget <= 200 or
            type(reported_token_budget) is not int or not 1 <= reported_token_budget <= 100_000_000):
        raise ValueError("Presupuesto: 1–200 invocaciones y 1–100.000.000 tokens reportados")
    document_bytes = read_stable(document_dir / "document.json")
    document = load_json(document_bytes)
    available = {unit["number"] for unit in document.get("units", []) if unit.get("image_asset")}
    selected = sorted(available if pages is None else pages)
    if (not selected or len(selected) > 200 or len(selected) != len(set(selected)) or
            any(type(number) is not int or number not in available for number in selected)):
        raise ValueError("Elige 1–200 páginas con imagen disponibles en el paquete")
    jobs = []
    for number in selected:
        job = prepare_job(root, document_dir, number)
        # Validación local completa del proveedor/modelo/límite por cada PNG, sin credenciales.
        preview = execute_job(root, Path(job["folder"]), provider, model, max_tokens=max_tokens,
                              timeout=timeout, key_slot=key_slot, cli_profile=cli_profile)
        jobs.append({"unit": number, "job_id": job["job_id"], "signature": preview["signature"]})
    plan = {"version": AI_BATCH_VERSION, "source_package_id": document_dir.name,
            "source_document_sha256": digest(document_bytes), "title": document["title"],
            "provider": provider, "model": model, "key_slot": key_slot,
            "cli_profile": cli_profile,
            "max_tokens": max_tokens, "timeout": timeout, "call_budget": call_budget,
            "reported_token_budget": reported_token_budget, "jobs": jobs}
    ident = digest(json_bytes(plan))
    now = time.time_ns()
    with closing(database(root)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""INSERT INTO ai_batches VALUES (?,?,?,NULL,?,?)
            ON CONFLICT(id) DO UPDATE SET updated_ns=excluded.updated_ns""",
                   (ident, json_bytes(plan).decode(), "listo", now, now))
        for position, job in enumerate(jobs):
            db.execute("INSERT OR IGNORE INTO ai_items VALUES (?,?,?,?,?,NULL)",
                       (ident, position, job["unit"], job["job_id"], "listo"))
    result = ai_batch_status(root, ident)
    result["report"] = export_ai_batch(root, result)
    return result


def _expand_result(root: Path, raw):
    if not raw:
        return None
    result = load_json(raw) if isinstance(raw, str) else dict(raw)
    if result.get("output"):
        result["output"] = str(output_folder(root, result["output"]))
    if result.get("run_folder"):
        name = Path(result["run_folder"]).name
        if not re.fullmatch(r"[0-9a-f]{32}", name):
            raise ValueError("Carpeta de ejecución inválida en lote IA")
        result["run_folder"] = str((root / "ejecuciones" / name).resolve())
    return result


def _stored_result(root: Path, result: dict):
    safe = {key: result.get(key) for key in (
        "id", "status", "reused", "provider", "response_reused", "usage",
        "reported_tokens", "started_call", "consumption_unknown", "error",
        "signature", "phase", "code") if key in result}
    if result.get("output"):
        safe["output"] = output_folder(root, result["output"]).name
    if result.get("run_folder"):
        safe["run_folder"] = Path(result["run_folder"]).name
    return safe


def ai_batch_status(root: Path, ident=None):
    root = Path(root).resolve()
    with closing(database(root)) as db:
        row = (db.execute("SELECT id,plan,state,result FROM ai_batches WHERE id=?", (ident,)).fetchone()
               if ident else db.execute("SELECT id,plan,state,result FROM ai_batches ORDER BY updated_ns DESC LIMIT 1").fetchone())
        if not row:
            raise ValueError("No hay lote IA; créalo desde un resultado con imágenes")
        ident, raw_plan, state, raw_result = row
        plan = load_json(raw_plan)
        rows = db.execute("SELECT position,unit,job_id,state,result FROM ai_items WHERE batch=? ORDER BY position",
                          (ident,)).fetchall()
    items = [{"position": position, "unit": unit, "job_id": job_id, "state": item_state,
              "result": _expand_result(root, result)} for position, unit, job_id, item_state, result in rows]
    counts = dict(Counter(item["state"] for item in items))
    started = [item["result"] for item in items if item["result"] and item["result"].get("started_call")]
    spent_calls = len(started)
    known_tokens = sum(item.get("reported_tokens") or 0 for item in started)
    unknown_calls = sum(item.get("reported_tokens") is None for item in started)
    combined = _expand_result(root, raw_result)
    if combined and (not combined.get("output") or not verify_artifacts(Path(combined["output"]))):
        state = "resultado_dañado"
    return {"id": ident, "state": state, "policy": {key: plan[key] for key in (
                "provider", "model", "key_slot", "cli_profile", "max_tokens", "timeout",
                "call_budget", "reported_token_budget")},
            "source_package": str(output_folder(root, plan["source_package_id"])),
            "title": plan["title"], "counts": counts, "items": items,
            "spent_calls": spent_calls, "reported_tokens": known_tokens,
            "unknown_calls": unknown_calls, "combined": combined, "external_calls": 0,
            "notice": "Tokens = total comunicado; ausencia no significa cero. CLI puede hacer llamadas internas."}


def _save_item(root, ident, position, state, result):
    raw = json_bytes(_stored_result(root, result)).decode() if result else None
    with closing(database(root)) as db, db:
        db.execute("UPDATE ai_items SET state=?,result=? WHERE batch=? AND position=?",
                   (state, raw, ident, position))
        db.execute("UPDATE ai_batches SET state=?,updated_ns=? WHERE id=?",
                   (state, time.time_ns(), ident))


def _reservation(root, signature):
    with closing(connect(root)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='provider_runs'").fetchone():
            return None
        return db.execute("SELECT folder,state FROM provider_runs WHERE signature=?", (signature,)).fetchone()


def _attempt_failure(root, exc, started_call):
    folder = Path(exc.folder).resolve()
    if not folder.is_relative_to(Path(root).resolve() / "ejecuciones"):
        raise ValueError("Intento previo fuera de la memoria actual")
    usage, tokens = None, None
    receipt = folder / "recibo.json"
    if receipt.is_file() and receipt.stat().st_size <= 4_000_000:
        data = load_json(read_stable(receipt))
        usage, tokens = data.get("usage"), reported_tokens(data.get("usage"))
    failure = record_failure(root, "lote_ia", exc,
                             {"phase": getattr(exc, "phase", "reserva"), "run_folder": str(folder)})
    return {"error": failure["message"], "signature": failure["signature"],
            "phase": getattr(exc, "phase", "reserva"), "code": getattr(exc, "code", "intento_previo"),
            "run_folder": str(folder), "usage": usage, "reported_tokens": tokens,
            "started_call": bool(started_call), "consumption_unknown": bool(started_call and tokens is None)}


def _merge(root, snapshot):
    units, assets, warnings = [], {}, []
    for item in snapshot["items"]:
        result = item["result"] or {}
        folder = Path(result.get("output", ""))
        if not verify_artifacts(folder):
            raise ValueError(f"Página {item['unit']}: paquete ausente o dañado")
        document = load_json(read_stable(folder / "document.json"))
        found = [unit for unit in document["units"] if unit["number"] == item["unit"]]
        if len(found) != 1:
            raise ValueError(f"Página {item['unit']}: resultado corresponde a otra unidad")
        unit = found[0]
        asset_name = f"imagenes/p{item['unit']:04d}.png"
        image = read_stable(folder / unit["image_asset"])
        if digest(image) != unit["image_sha256"]:
            raise ValueError(f"Página {item['unit']}: imagen alterada")
        assets[asset_name] = image
        units.append({"number": item["unit"], "blocks": unit["blocks"],
                      "image_asset": asset_name, "image_sha256": digest(image)})
        warnings.extend(f"Página {item['unit']}: {warning}" for warning in document.get("producer_warnings", []))
    source = Path(snapshot["source_package"])
    source_document = read_stable(source / "document.json")
    policy = snapshot["policy"]
    doc = {"schema_version": SCHEMA_VERSION, "title": snapshot["title"],
           "input_kind": "structured_batch", "source_path": str(source),
           "source_sha256": digest(source_document),
           "expected_units": [item["unit"] for item in snapshot["items"]],
           "scope": "páginas seleccionadas; respuesta estructural por página; fidelidad pendiente",
           "provider": policy["provider"] + ":" + policy["model"],
           "producer_warnings": warnings, "units": units}
    return publish(root, doc, assets)


def run_ai_batch(root: Path, ident=None, *, send=False, stop=None):
    """Sin ``send`` solo previsualiza. Con ``send`` procesa de una en una y se detiene ante duda."""
    root = Path(root).resolve()
    snapshot = ai_batch_status(root, ident)
    if not send:
        snapshot.update(status="vista_previa", external_calls=0,
                        report=export_ai_batch(root, snapshot))
        return snapshot
    new_calls = 0
    with exclusive(root, "lote-ia"):
        snapshot = ai_batch_status(root, snapshot["id"])
        if snapshot["state"] == "resultado_dañado":
            raise ValueError("El resultado combinado está dañado; revisar antes de otro envío")
        for item in snapshot["items"]:
            if item["state"] in FINAL_STATES:
                continue
            if item["state"] in STOP_STATES:
                break
            if stop and stop.is_set():
                break
            plan = snapshot["policy"]
            job = root / "encargos" / item["job_id"]
            preview = execute_job(root, job, plan["provider"], plan["model"],
                                  max_tokens=plan["max_tokens"], timeout=plan["timeout"],
                                  key_slot=plan["key_slot"], cli_profile=plan["cli_profile"])
            recovering = item["state"] == "en_curso"
            if recovering and _reservation(root, preview["signature"]) is None:
                recovering = False  # Cayó antes de reservar: todavía no hubo un intento.
            if not recovering:
                if snapshot["spent_calls"] >= plan["call_budget"]:
                    break
                if snapshot["reported_tokens"] >= plan["reported_token_budget"]:
                    break
                _save_item(root, snapshot["id"], item["position"], "en_curso", None)
            try:
                result = execute_job(root, job, plan["provider"], plan["model"], send=True,
                                     max_tokens=plan["max_tokens"], timeout=plan["timeout"],
                                     key_slot=plan["key_slot"], cli_profile=plan["cli_profile"])
                started_call = recovering or result["external_calls"] == 1
                tokens = reported_tokens(result.get("usage"))
                result.update(started_call=started_call, reported_tokens=tokens,
                              consumption_unknown=bool(started_call and tokens is None))
                state = ("consumo_desconocido" if result["consumption_unknown"] else
                         ("reutilizado" if result.get("response_reused") and not started_call else "terminado"))
                _save_item(root, snapshot["id"], item["position"], state, result)
                new_calls += result["external_calls"]
            except (ProviderAttemptError, PriorAttemptError) as exc:
                started_call = recovering or (isinstance(exc, ProviderAttemptError) and exc.phase != "preparacion")
                result = _attempt_failure(root, exc, started_call)
                _save_item(root, snapshot["id"], item["position"], "requiere_revision", result)
                new_calls += int(started_call and not recovering)
                break
            except Exception as exc:
                failure = record_failure(root, "lote_ia_preparacion", exc,
                                         {"batch": snapshot["id"], "unit": item["unit"]})
                result = {"error": failure["message"], "signature": failure["signature"],
                          "started_call": False, "consumption_unknown": False}
                _save_item(root, snapshot["id"], item["position"], "bloqueado", result)
                break
            snapshot = ai_batch_status(root, snapshot["id"])
            if state == "consumo_desconocido":
                break
        snapshot = ai_batch_status(root, snapshot["id"])
        states = {item["state"] for item in snapshot["items"]}
        if states <= FINAL_STATES:
            combined = _merge(root, snapshot)
            with closing(database(root)) as db, db:
                db.execute("UPDATE ai_batches SET state='finalizado',result=?,updated_ns=? WHERE id=?",
                           (json_bytes(_stored_result(root, combined)).decode(), time.time_ns(), snapshot["id"]))
            status = "finalizado"
        elif states & STOP_STATES:
            status = "requiere_revision"
        elif "bloqueado" in states:
            status = "bloqueado"
        elif stop and stop.is_set():
            status = "pausado"
        elif snapshot["spent_calls"] >= snapshot["policy"]["call_budget"]:
            status = "presupuesto_llamadas_agotado"
        elif snapshot["reported_tokens"] >= snapshot["policy"]["reported_token_budget"]:
            status = "presupuesto_tokens_reportados_agotado"
        else:
            status = "pausado"
        with closing(database(root)) as db, db:
            db.execute("UPDATE ai_batches SET state=?,updated_ns=? WHERE id=?",
                       (status, time.time_ns(), snapshot["id"]))
        snapshot = ai_batch_status(root, snapshot["id"])
        snapshot.update(status=status, external_calls=new_calls,
                        report=export_ai_batch(root, snapshot))
        return snapshot


def export_ai_batch(root: Path, snapshot):
    root = Path(root).resolve()
    folder = root / "lotes_ia"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (snapshot["id"] + ".md")
    policy = snapshot["policy"]
    lines = [f"# Lote IA — {snapshot['title']}\n\n", f"ID: `{snapshot['id']}` · estado: **{snapshot['state']}**\n\n",
             f"Proveedor/modelo: `{policy['provider']}` / `{policy['model']}` · perfil API: {policy['key_slot']} · perfil CLI: `{policy['cli_profile']}`\n\n",
             f"Presupuesto: {policy['call_budget']} invocaciones · {policy['reported_token_budget']} tokens reportados. "
             f"Acumulado: {snapshot['spent_calls']} · {snapshot['reported_tokens']}; desconocidas: {snapshot['unknown_calls']}.\n\n",
             "> Este control limita invocaciones del programa. Un CLI puede realizar llamadas internas. "
             "Los tokens reportados se conocen después de la respuesta y pueden superar el umbral en la última página.\n\n"]
    for item in snapshot["items"]:
        lines.append(f"- Página {item['unit']}: **{item['state']}**")
        result = item.get("result") or {}
        if result.get("output"):
            relative = os.path.relpath(Path(result["output"]) / "documento.md", folder).replace("\\", "/")
            lines.append(f" · [MD](<{relative}>)")
        if result.get("reported_tokens") is not None:
            lines.append(f" · {result['reported_tokens']} tokens")
        if result.get("error"):
            lines.append(f" · error `{result.get('signature', 'sin-firma')}`: {result['error']}")
        lines.append("\n")
    combined = snapshot.get("combined") or {}
    if combined.get("output"):
        relative = os.path.relpath(Path(combined["output"]) / "documento.md", folder).replace("\\", "/")
        lines.append(f"\n## Resultado unido\n\n[Abrir Markdown ordenado](<{relative}>)\n")
    temporary = folder / (uuid.uuid4().hex + ".tmp")
    temporary.write_text("".join(lines), encoding="utf-8")
    os.replace(temporary, target)
    return str(target)
