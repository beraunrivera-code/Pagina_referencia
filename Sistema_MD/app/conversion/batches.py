"""Lotes locales explícitos, reanudables, sin importación de motores de IA."""
from contextlib import closing
from pathlib import Path
from collections import Counter
import os
import uuid
import time

from .documents import digest, json_bytes, load_json
from .local_io import exclusive
from .pipeline import NATIVE_VERSION, convert_native, convert_text, detect, file_hash
from .storage import connect, output_folder, verify_artifacts
from .workflow import WorkQueue
from .diagnostics import record_failure

# Legado vía LibreOffice e imágenes vía OCR también son locales; CAD y PDF siguen aparte.
LOCAL = {"docx", "xlsx", "pptx", "vsdx", "txt", "md", "text", "xls", "xlsb", "doc", "ppt", "png", "jpg", "tif", "gif", "webp"}


def database(root):
    db = connect(root)
    db.execute("CREATE TABLE IF NOT EXISTS local_batches (id TEXT PRIMARY KEY, plan TEXT NOT NULL)")
    db.execute("BEGIN IMMEDIATE")
    if 'updated' not in {row[1] for row in db.execute('PRAGMA table_info(local_batches)')}:
        db.execute("ALTER TABLE local_batches ADD COLUMN updated INTEGER NOT NULL DEFAULT 0")
    db.execute("""CREATE TABLE IF NOT EXISTS local_items (
        batch TEXT, number INTEGER, state TEXT, result TEXT,
        PRIMARY KEY(batch,number))""")
    db.execute("""CREATE TABLE IF NOT EXISTS local_cache (
        hash TEXT, version INTEGER, result TEXT, PRIMARY KEY(hash,version))""")
    db.commit()
    return db


def plan_queue(root):
    """Planifica la cola ya catalogada: no reabre nativos ni hace conversiones."""
    queue = WorkQueue(root)
    if not queue.items:
        raise ValueError("Primero agrega archivos o una carpeta a la cola")
    if len(queue.items) > 2000:
        raise ValueError("Piloto: máximo 2.000 archivos por plan; divide la selección")
    seen, items = {}, []
    for item in queue.items:
        row = {k: item.get(k) for k in ("path", "format", "bytes", "sha256")}
        kind, checksum = row["format"], row["sha256"]
        if kind not in LOCAL or not checksum:
            row.update(route="pendiente", reason={"pdf": "Elegir páginas/valor; no se envía a IA",
                "office-legacy": "OLE que no es Word/Excel/PowerPoint: sin adaptador", "dwg": "CAD: procesar individualmente con revisión de geometría",
                "dxf": "CAD: procesar individualmente con revisión de geometría", "deferred": "Descargar localmente primero",
                "zip-damaged": "ZIP dañado: revisar fuente"}.get(kind, "Formato fuera del lote local; no se elimina"))
        elif checksum in seen:
            row.update(route="duplicado", duplicate_of=seen[checksum], reason="Mismos bytes SHA-256")
        else:
            row.update(route="local", reason="Extracción local; fidelidad requiere revisión")
            seen[checksum] = len(items)
        items.append(row)
    plan = {"version": NATIVE_VERSION, "items": items, "external_calls": 0}
    ident = digest(json_bytes(plan))
    with closing(database(root)) as db, db:
        db.execute("INSERT INTO local_batches(id,plan,updated) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET updated=excluded.updated",
                   (ident, json_bytes(plan).decode(), time.time_ns()))
        for number, row in enumerate(items):
            db.execute("INSERT OR IGNORE INTO local_items VALUES (?,?,?,?)",
                       (ident, number, "pendiente" if row["route"] == "pendiente" else "listo", None))
    result = batch_status(root, ident)
    result["report"] = export_batch(root, result)
    return result


def export_batch(root, result):
    folder = Path(root).resolve() / "lotes"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (result['id'] + '.md')
    lines = ["# Lote local\n\n", f"ID: {result['id']}\n\n",
             f"Estados: {result['counts']} · Rutas: {result['routes']}\n\n",
             "Sin llamadas a IA. Terminado = paquete íntegro, no fidelidad certificada.\n\n"]
    for row in result['items']:
        lines.append(f"## Archivo {row['number']+1}: {Path(row['path']).name}\n\n")
        lines.append(f"- Fuente: `{row['path']}`\n- Ruta: {row['route']} · Estado: {row['state']}\n- Motivo: {row['reason']}\n")
        data = row.get('result') or {}
        if data.get('output'):
            local = output_folder(Path(root), data['output']) / 'documento.md'
            relative = os.path.relpath(local, folder).replace('\\', '/')
            lines.append(f"- [Abrir Markdown](<{relative}>)\n")
        if data.get('error'):
            lines.append(f"- Error: {data['error']} · diagnóstico: `{data['signature']}`\n")
        lines.append('\n')
    temporary = folder / (uuid.uuid4().hex + '.tmp')
    temporary.write_text(''.join(lines), encoding='utf-8')
    os.replace(temporary, target)
    return str(target)


def batch_status(root, ident=None):
    with closing(database(root)) as db:
        row = (db.execute("SELECT id,plan FROM local_batches WHERE id=?", (ident,)).fetchone() if ident else
               db.execute("SELECT id,plan FROM local_batches ORDER BY updated DESC,rowid DESC LIMIT 1").fetchone())
        if not row:
            raise ValueError("No hay un plan local; usa planificar-lote")
        ident, raw = row
        plan = load_json(raw)
        results = db.execute("SELECT number,state,result FROM local_items WHERE batch=? ORDER BY number", (ident,)).fetchall()
    items = [{**plan["items"][n], "number": n, "state": state,
              "result": load_json(result) if result else None} for n, state, result in results]
    return {"id": ident, "version": plan["version"], "counts": dict(Counter(i["state"] for i in items)),
            "routes": dict(Counter(i["route"] for i in items)), "items": items, "external_calls": 0}


def cached(root, checksum):
    with closing(database(root)) as db:
        row = db.execute("SELECT result FROM local_cache WHERE hash=? AND version=?", (checksum, NATIVE_VERSION)).fetchone()
    if row:
        result = load_json(row[0])
        folder = output_folder(root, result["output"])
        if verify_artifacts(folder):
            return {**result, "output": str(folder), "reused": True}
    return None


def run_batch(root, ident=None, *, limit=20, stop=None, retry_errors=False):
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("Cada ejecución procesa entre 1 y 200 archivos; predeterminado 20")
    root = Path(root).resolve()
    with exclusive(root):
        snapshot = batch_status(root, ident)
        ident = snapshot["id"]
        if snapshot["version"] != NATIVE_VERSION:
            raise ValueError("El motor cambió: vuelve a planificar; el plan anterior se conserva")
        processed = 0
        for row in snapshot["items"]:
            if processed >= limit or (stop and stop.is_set()):
                break
            if row["state"] == "pendiente" or (row["state"] == "error" and not retry_errors):
                continue
            # Revisa derivados terminados, no relee sus nativos al reanudar.
            old = row.get("result")
            if row["state"] == "terminado" and old and verify_artifacts(output_folder(root, old["output"])):
                continue
            with closing(database(root)) as db, db:
                db.execute("UPDATE local_items SET state='en_curso' WHERE batch=? AND number=?", (ident, row["number"]))
            try:
                source = Path(row["path"])
                attrs = getattr(source.stat(), "st_file_attributes", 0)
                if source.is_symlink() or attrs & (0x1000 | 0x400000):
                    raise ValueError("Fuente enlazada/en nube: vuelve a seleccionarla localmente")
                if source.resolve().is_relative_to(root):
                    raise ValueError("La memoria de salida no se convierte sobre sí misma")
                if source.stat().st_size != row["bytes"] or file_hash(source) != row["sha256"]:
                    raise ValueError("La fuente cambió desde el catálogo: vuelve a agregarla y planificar")
                if detect(source) != row["format"]:
                    raise ValueError("Formato distinto al plan; vuelve a catalogar")
                result = cached(root, row["sha256"])
                if result is None:
                    result = (convert_text(root, source) if row["format"] in {"txt", "md", "text"}
                              else convert_native(root, source))
                if result["status"] == "parcial" or not verify_artifacts(Path(result["output"])):
                    raise ValueError("Resultado parcial o dañado; no se marca como terminado")
                with closing(database(root)) as db, db:
                    db.execute("INSERT OR REPLACE INTO local_cache VALUES (?,?,?)",
                               (row["sha256"], NATIVE_VERSION, json_bytes(result).decode()))
                    db.execute("INSERT OR IGNORE INTO origins VALUES (?,?)", (result["id"], str(source)))
                WorkQueue(root)._finish(row["path"], row["sha256"], result, False)
                state = "terminado"
            except Exception as exc:
                failure = record_failure(root, "lote_local", exc, {"batch": ident, "source": row["path"]})
                result = {"error": failure["message"], "signature": failure["signature"]}
                state = "error"
            with closing(database(root)) as db, db:
                db.execute("UPDATE local_items SET state=?,result=? WHERE batch=? AND number=?",
                           (state, json_bytes(result).decode(), ident, row["number"]))
            processed += 1
        result = batch_status(root, ident)
        has_pending = any(i["state"] in {"listo", "en_curso"} for i in result["items"])
        result.update(status="pausado" if has_pending else "finalizado_local", processed=processed,
                      notice="Terminado significa paquete íntegro, no fidelidad certificada. PDF/CAD/antiguos se mantienen pendientes.")
        result['report'] = export_batch(root, result)
        return result
