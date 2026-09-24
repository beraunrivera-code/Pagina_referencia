"""Diagnóstico persistente: observa, clasifica y propone reparaciones seguras."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import platform
import shutil
import sqlite3
import traceback
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .documents import load_json
from .pipeline import doctor
from .storage import adopt_orphans, connect, export_index, prune_missing, purge_orphan_batches, report
from .provider_errors import ProviderAttemptError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_schema(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS failures
        (signature TEXT PRIMARY KEY, operation TEXT NOT NULL, error_type TEXT NOT NULL,
         message TEXT NOT NULL, context TEXT NOT NULL, first_seen TEXT NOT NULL,
         last_seen TEXT NOT NULL, occurrences INTEGER NOT NULL, status TEXT NOT NULL,
         probable_cause TEXT NOT NULL, remedy TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS resolutions
        (signature TEXT NOT NULL, resolved_at TEXT NOT NULL,
         cause TEXT NOT NULL, evidence TEXT NOT NULL)""")


def resolve_failure(root: Path, signature: str, cause: str, evidence: str) -> dict:
    if not cause.strip() or not evidence.strip():
        raise ValueError("Se exige causa confirmada y evidencia de verificación")
    with closing(connect(root)) as db, db:
        _ensure_schema(db)
        if not db.execute("SELECT 1 FROM failures WHERE signature=?", (signature,)).fetchone():
            raise ValueError("Firma de fallo inexistente")
        db.execute("INSERT INTO resolutions VALUES (?,?,?,?)", (signature, _now(), cause, evidence))
        db.execute("UPDATE failures SET status='resuelto' WHERE signature=?", (signature,))
    return {"signature": signature, "status": "resuelto", "cause": cause,
            "evidence": evidence, "notice": "Evidencia declarada por el revisor; el programa no ejecuta su texto."}


def classify_error(exc: BaseException) -> tuple[str, str]:
    """Devuelve causa probable y siguiente acción; no afirma una causa no probada."""
    if isinstance(exc, ProviderAttemptError):
        return f"Fallo de IA en fase {exc.phase}; código {exc.code}.", exc.remedy
    if isinstance(exc, FileNotFoundError):
        return "La ruta cambió, fue movida o ya no existe.", "Volver a seleccionar la fuente; no recrear datos a ciegas."
    if isinstance(exc, PermissionError):
        return "Windows o una aplicación bloquea el acceso a la ruta.", "Cerrar el archivo si está abierto y comprobar permisos de esa carpeta."
    if isinstance(exc, UnicodeError):
        return "La codificación del texto no coincide con UTF-8.", "Conservar el original y usar un adaptador de codificación explícito."
    if isinstance(exc, ImportError):
        return "Falta una dependencia del adaptador solicitado.", "Ejecutar diagnóstico; instalar solo tras aprobación."
    if isinstance(exc, sqlite3.Error):
        return "El índice local está bloqueado o no es legible.", "Cerrar otros procesos y reintentar la reparación segura."
    if isinstance(exc, ValueError):
        return "Una precondición o control de integridad rechazó la operación.", "Corregir el dato indicado; no omitir el control."
    return "Causa todavía no confirmada; requiere reproducción mínima.", "Repetir una vez con la misma fuente y conservar este registro."


def record_failure(root: Path, operation: str, exc: BaseException, context: dict | None = None) -> dict:
    """Agrupa fallos equivalentes para no investigar el mismo error desde cero."""
    root = root.resolve()
    message = str(exc).strip() or exc.__class__.__name__
    normalized = getattr(exc, "diagnostic_key", " ".join(message.casefold().split()))
    signature = hashlib.sha256(f"{operation}|{exc.__class__.__name__}|{normalized}".encode()).hexdigest()[:16]
    probable_cause, remedy = classify_error(exc)
    timestamp = _now()
    evidence = dict(context or {})
    if isinstance(exc, ProviderAttemptError):
        evidence.update(run_folder=exc.folder, phase=exc.phase, code=exc.code)
    evidence["frames"] = [{"file": frame.filename, "line": frame.lineno, "function": frame.name}
                          for frame in traceback.extract_tb(exc.__traceback__)[-8:]]
    with closing(connect(root)) as db, db:
        _ensure_schema(db)
        row = db.execute("SELECT occurrences, first_seen FROM failures WHERE signature=?", (signature,)).fetchone()
        occurrences, first_seen = (row[0] + 1, row[1]) if row else (1, timestamp)
        db.execute("""INSERT OR REPLACE INTO failures VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (
            signature, operation, exc.__class__.__name__, message,
            json.dumps(evidence, ensure_ascii=False, sort_keys=True), first_seen,
            timestamp, occurrences, "abierto", probable_cause, remedy,
        ))
    return {"signature": signature, "operation": operation, "type": exc.__class__.__name__,
            "message": message, "occurrences": occurrences, "probable_cause": probable_cause,
            "remedy": remedy, "status": "abierto"}


def list_failures(root: Path) -> list[dict]:
    database = root.resolve() / "indice.sqlite"
    if not database.exists():
        return []
    with closing(connect(root.resolve())) as db, db:
        _ensure_schema(db)
        db.row_factory = sqlite3.Row
        failures = [dict(row) for row in db.execute(
            "SELECT * FROM failures ORDER BY last_seen DESC, signature")]
        for item in failures:
            item["resolutions"] = [dict(row) for row in db.execute(
                "SELECT resolved_at,cause,evidence FROM resolutions WHERE signature=? ORDER BY resolved_at",
                (item["signature"],))]
        return failures


def run_diagnostics(root: Path) -> dict:
    """Chequeos locales y reproducibles. No inicia proveedores ni consume créditos."""
    root = root.resolve()
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str, repairable: bool = False) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "repairable": repairable})

    local = doctor()
    add("Python", True, f"{local['python']} · {platform.system()}")
    add("Interfaz", importlib.util.find_spec("tkinter") is not None, "Tkinter local; no requiere Angular ni servidor")
    add("PDF", bool(local["libraries"]["fitz"]), "PyMuPDF para texto e imágenes de referencia")
    add("Excel", bool(local["libraries"]["openpyxl"]), "openpyxl: hojas, fórmulas e imágenes registradas; adaptador local, sin IA")
    add("Word", bool(local["libraries"]["docx"]), "python-docx: párrafos por estilo, tablas e imágenes registradas; sin IA")
    add("PowerPoint", bool(local["libraries"]["pptx"]), "python-pptx: texto y notas; diapositivas visuales declaradas pendientes")
    add("CAD", bool(local["libraries"]["ezdxf"]) and bool(local["oda_file_converter"]),
        "ODA File Converter + ezdxf: DWG/DXF medidos (textos, bloques, capas, geometría)")
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".diagnostico-escritura.tmp"
        probe.write_bytes(b"ok")
        probe.unlink()
        add("Carpeta de resultados", True, str(root))
    except OSError as exc:
        add("Carpeta de resultados", False, str(exc), True)
    try:
        rows = report(root)
        damaged = sum(not row["integrity_ok"] for row in rows)
        add("Integridad de resultados", damaged == 0,
            f"{len(rows) - damaged}/{len(rows)} paquetes íntegros")
    except (OSError, ValueError, sqlite3.Error) as exc:
        add("Integridad de resultados", False, str(exc))
    queue_file = root / "cola.json"
    if queue_file.exists():
        try:
            from .workflow import WorkQueue
            work_queue = WorkQueue(root)
            add("Cola persistente", True, f"{len(work_queue.items)} elementos")
        except (OSError, ValueError) as exc:
            add("Cola persistente", False, str(exc))
    else:
        add("Cola persistente", True, "Vacía; se creará al seleccionar documentos")
    free_gb = shutil.disk_usage(root).free / (1024 ** 3)
    add("Espacio libre", free_gb >= 1, f"{free_gb:.1f} GB disponibles", free_gb < 1)
    add("Consumo externo", True, "0 llamadas en este chequeo; los envíos explícitos tienen su recibo")
    from .providers import run_history
    try:
        runs = run_history(root)['runs']
        attention = sum(row['state'] != 'validado_estructuralmente' or bool(row['error_code']) for row in runs)
        add("Intentos de IA", attention == 0, f"{len(runs)} intentos recientes; {attention} requieren revisión. No mide cuota ni calidad.")
    except (OSError, ValueError, sqlite3.Error) as exc:
        add("Intentos de IA", False, str(exc))
    try:
        with closing(connect(root)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_items'").fetchone():
                states = dict(db.execute("SELECT state,count(*) FROM local_items GROUP BY state").fetchall())
                add("Lotes locales", not states.get('error'), f"Estados persistidos: {states}. No mide fidelidad ni el lote externo de Claude.")
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_batches'").fetchone():
                states = dict(db.execute("SELECT state,count(*) FROM ai_batches GROUP BY state").fetchall())
                attention = sum(states.get(name, 0) for name in
                                ("requiere_revision", "consumo_desconocido", "resultado_dañado"))
                add("Lotes IA", attention == 0,
                    f"Planes por estado: {states}; {attention} requieren revisión. No contacta proveedores.")
        failure_count = len(list_failures(root))
    except (OSError, sqlite3.Error) as exc:
        failure_count = None
        add("Registro de fallos", False, str(exc))
    return {"status": "OK" if all(c["ok"] for c in checks) else "ATENCION",
            "checks": checks, "failures": failure_count, "external_calls": 0}


def repair_safe(root: Path) -> dict:
    """Recrea infraestructura derivada; nunca toca ni elimina documentos fuente."""
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with closing(connect(root)) as db, db:
        _ensure_schema(db)
    # El índice y las carpetas deben decir lo mismo: se dan de baja las filas sin carpeta
    # (resultados «dañados» por una carpeta movida o borrada a mano) y se vuelven a registrar
    # las carpetas íntegras sin fila (lo restaurado desde la Papelera). No se toca ningún paquete.
    removed = prune_missing(root)
    adopted = adopt_orphans(root)
    batches = purge_orphan_batches(root)
    index = export_index(root)
    actions = ["Esquema SQLite verificado", "Índice Markdown regenerado"]
    if removed:
        actions.append(f"{len(removed)} fila(s) sin carpeta dadas de baja")
    if adopted:
        actions.append(f"{len(adopted)} carpeta(s) íntegra(s) registradas de nuevo")
    if batches:
        actions.append(f"{len(batches)} lote(s) de IA huérfano(s) descartado(s)")
    return {"status": "reparado", "actions": actions, "rows_removed": len(removed),
            "folders_adopted": len(adopted), "batches_purged": len(batches),
            "index": index["path"], "sources_modified": 0, "external_calls": 0}


def export_failure_report(root: Path) -> dict:
    root = root.resolve()
    failures = list_failures(root)
    lines = ["# Fallos diagnosticados\n\n",
             "Registro acumulativo del programa. Una causa probable no se considera confirmada hasta reproducirla.\n\n"]
    for item in failures:
        lines.extend([
            f"## {item['signature']} · {item['error_type']}\n\n",
            f"- Operación: `{item['operation']}`\n",
            f"- Apariciones: {item['occurrences']}\n",
            f"- Estado: {item['status']}\n",
            f"- Última vez: {item['last_seen']}\n",
            f"- Síntoma: {item['message']}\n",
            f"- Causa probable: {item['probable_cause']}\n",
            f"- Blindaje/siguiente prueba: {item['remedy']}\n\n",
        ])
        for frame in json.loads(item["context"]).get("frames", []):
            lines.append(f"- Ubicación: {frame['file']}:{frame['line']} ({frame['function']})\n")
        for fix in item["resolutions"]:
            lines.append(f"- Corrección [{fix['resolved_at']}]: {fix['cause']}\n"
                         f"  Evidencia declarada: {fix['evidence']}\n")
    if not failures:
        lines.append("No hay fallos registrados.\n")
    folder = root / "diagnosticos"
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / "FALLOS.md"
    temporary = folder / ".FALLOS.md.tmp"
    temporary.write_text("".join(lines), encoding="utf-8")
    os.replace(temporary, destination)
    return {"path": str(destination), "failures": len(failures)}
