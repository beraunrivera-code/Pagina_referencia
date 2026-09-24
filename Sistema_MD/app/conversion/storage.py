"""Publicación inmutable, deduplicación e índice local transaccional."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

from . import __version__
from .documents import digest, json_bytes, load_json, render, validate


def connect(root: Path) -> sqlite3.Connection:
    """Inicializa sin competir por cambiar a WAL en cada apertura.

    WAL persiste en disco. Dos conexiones que intentan activarlo a la vez pueden
    recibir SQLITE_BUSY inmediatamente (el busy handler no siempre espera ante
    una promoción de bloqueo). Se libera la conexión y se reinicia SOLO esta
    inicialización idempotente, dentro del presupuesto original de 20 segundos.
    Las operaciones de cola/publicación nunca se repiten aquí.
    """
    import time

    root.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 20
    while True:
        db = None
        try:
            db = sqlite3.connect(root / "indice.sqlite", timeout=max(0, deadline - time.monotonic()))
            mode = db.execute("PRAGMA journal_mode").fetchone()[0]
            if mode.lower() != "wal":
                mode = db.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                if mode.lower() != "wal":
                    raise sqlite3.OperationalError("No se pudo activar WAL para el índice")
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"documents", "origins", "native_cache"} <= tables:
                # Solo el bootstrap incompleto necesita escribir. Una apertura
                # corriente debe funcionar aunque el llamador tenga otra conexión
                # escribiendo. DDL atómico: nadie ve el esquema a medio crear.
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("""CREATE TABLE IF NOT EXISTS documents
                        (id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL,
                         output TEXT NOT NULL, updated TEXT NOT NULL)""")
                    db.execute("""CREATE TABLE IF NOT EXISTS origins
                        (id TEXT NOT NULL, source_path TEXT NOT NULL,
                         PRIMARY KEY(id, source_path))""")
                    db.execute("CREATE TABLE IF NOT EXISTS native_cache (hash TEXT, version INTEGER, id TEXT, PRIMARY KEY(hash,version))")
            # La espera gastada en el bootstrap no reduce el timeout histórico
            # de las transacciones de negocio que hará el llamador después.
            db.execute("PRAGMA busy_timeout=20000")
            return db
        except BaseException as exc:
            if db is not None:
                db.close()
            code = getattr(exc, "sqlite_errorcode", None)
            busy = isinstance(exc, sqlite3.OperationalError) and (
                (code is not None and code & 255 == sqlite3.SQLITE_BUSY)
                or (code is None and str(exc) == "database is locked"))
            remaining = deadline - time.monotonic()
            if not busy or remaining <= 0:
                raise
            time.sleep(min(0.025, remaining))


def output_folder(root: Path, stored: str) -> Path:
    """Resuelve paquetes en ESTA memoria, incluso al copiarla desde otra PC.

    Las rutas históricas no autorizan leer fuera de la memoria elegida.
    No modifica manifiestos ni la procedencia del documento.
    """
    name = PureWindowsPath(stored).name
    if not re.fullmatch(r"[0-9a-f]{64}(?:-recuperado-[0-9a-f]{8})?", name):
        raise ValueError("Identificador de paquete inválido en el índice")
    folder = (root.resolve() / "documentos" / name).resolve()
    if not folder.is_relative_to(root.resolve()):
        raise ValueError("Paquete fuera de la memoria elegida")
    return folder


def verify_artifacts(folder: Path) -> bool:
    try:
        manifest = load_json((folder / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
            return False
        files = manifest["files"]
        if not {"document.json", "documento.md", "verificacion.json"} <= set(files):
            return False
        for name, checksum in files.items():
            path = (folder / name).resolve()
            if not path.is_relative_to(folder.resolve()):
                return False
            if digest(path.read_bytes()) != checksum:
                return False
        doc = load_json((folder / "document.json").read_bytes())
        if not isinstance(doc, dict) or not isinstance(doc.get("units"), list):
            return False
        for key in ("structure_asset", "audit_asset"):
            if doc.get(key) and doc[key] not in files:
                return False
        for unit in doc["units"]:
            if not isinstance(unit, dict):
                return False
            if "image_asset" in unit:
                asset = unit["image_asset"]
                if not isinstance(asset, str) or asset not in files:
                    return False
                if unit.get("image_sha256") != files[asset]:
                    return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def publish(root: Path, doc: dict, assets: dict[str, bytes] | None = None) -> dict:
    root = root.resolve()
    qa = validate(doc)
    assets = assets or {}
    reserved = {"document.json", "documento.md", "verificacion.json", "manifest.json"}
    if set(assets) & reserved:
        raise ValueError("Un recurso no puede reemplazar archivos de control")
    for unit in doc["units"]:
        if "image_asset" in unit:
            name = unit["image_asset"]
            if name not in assets or unit.get("image_sha256") != digest(assets[name]):
                raise ValueError("Imagen referenciada ausente o hash inconsistente")
    # Identidad independiente de ubicación: un mismo resultado conserva sus rutas.
    identity = {"version": __version__, "doc": doc, "assets": {k: digest(v) for k, v in assets.items()}}
    identity["doc"] = json.loads(json.dumps(doc))
    for unit in identity["doc"]["units"]:
        unit.pop("source_path", None)
        unit.pop("image_source", None)
    identity["doc"].pop("source_path", None)
    ident = digest(json_bytes(identity))
    payloads = {"document.json": json_bytes(doc), "documento.md": render(doc, qa).encode("utf-8"),
                "verificacion.json": json_bytes(qa), **assets}
    now = datetime.now(timezone.utc).isoformat()
    manifest = {"conversion_id": ident, "version": __version__, "created": now,
                "files": {k: digest(v) for k, v in payloads.items()}}
    with closing(connect(root)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT output FROM documents WHERE id=?", (ident,)).fetchone()
        origin = doc.get("source_path", "")
        if row and verify_artifacts(output_folder(root, row[0])):
            db.execute("INSERT OR IGNORE INTO origins VALUES (?,?)", (ident, origin))
            return {"id": ident, "output": str(output_folder(root, row[0])), "reused": True, **qa}
        final = root / "documentos" / ident
        if final.exists():
            # Conservar la versión dañada o publicación huérfana; nunca sobrescribir.
            final = final.with_name(ident + "-recuperado-" + uuid.uuid4().hex[:8])
        stage = root / "pendientes" / uuid.uuid4().hex
        stage.mkdir(parents=True)
        for name, data in {**payloads, "manifest.json": json_bytes(manifest)}.items():
            path = (stage / name).resolve()
            if not path.is_relative_to(stage.resolve()):
                raise ValueError("Recurso fuera de la carpeta de publicación")
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        final.parent.mkdir(parents=True, exist_ok=True)
        # Windows puede mantener una referencia breve al directorio recién escrito
        # (WinError 5/32/33). Repetir SOLO la promoción del MISMO paquete completo:
        # cinco intentos y 0,75 s de espera total; no repetir conversión ni llamada IA.
        # No usar replace/copy ni borrar el stage si el bloqueo resulta permanente.
        import errno
        import time
        for attempt in range(5):
            if final.exists() or final.is_symlink():
                raise FileExistsError(errno.EEXIST, "El destino apareció; no se sobrescribe", str(final))
            try:
                stage.rename(final)  # Windows: falla si otro proceso creó el destino.
                break
            except OSError as exc:
                if final.exists() or final.is_symlink():
                    raise FileExistsError(errno.EEXIST, "El destino apareció; no se sobrescribe", str(final)) from exc
                if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 4 or not stage.is_dir():
                    raise
                time.sleep(0.05 * (2 ** attempt))
        db.execute("INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?)",
                   (ident, doc["title"], qa["status"], str(final), now))
        db.execute("INSERT OR IGNORE INTO origins VALUES (?,?)", (ident, origin))
        if doc.get("native_version"):
            db.execute("INSERT OR REPLACE INTO native_cache VALUES (?,?,?)",
                       (doc["source_sha256"], doc["native_version"], ident))
    return {"id": ident, "output": str(final), "reused": False, **qa}


def prepared_cache(root: Path, source_hash: str, pages: list[int], dpi: int) -> dict | None:
    """Consulta antes de renderizar: solo reutiliza alcance, motor e integridad iguales."""
    database = root.resolve() / "indice.sqlite"
    if not database.exists():
        return None
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        rows = db.execute("SELECT id,output FROM documents ORDER BY updated DESC").fetchall()
    for ident, output in rows:
        folder = output_folder(root, output)
        if not folder.is_relative_to(root.resolve()) or not verify_artifacts(folder):
            continue
        document = load_json((folder / "document.json").read_bytes())
        manifest = load_json((folder / "manifest.json").read_bytes())
        if (document.get("input_kind") == "pdf_prepared"
                and document.get("source_sha256") == source_hash
                and document.get("expected_units") == pages and document.get("dpi") == dpi
                and manifest.get("version") == __version__):
            qa = load_json((folder / "verificacion.json").read_bytes())
            return {"id": ident, "output": str(folder), "reused": True, **qa}
    return None


def native_cache(root: Path, source_hash: str, version: int, source: Path) -> dict | None:
    """Índice de caché derivado; no reutiliza motores viejos ni artefactos dañados."""
    with closing(connect(root)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS native_cache (hash TEXT, version INTEGER, id TEXT, PRIMARY KEY(hash,version))")
        row = db.execute("SELECT d.id,d.output FROM native_cache c JOIN documents d ON d.id=c.id WHERE c.hash=? AND c.version=?",
                         (source_hash, version)).fetchone()
        candidates = [row] if row else []
        for ident, stored in candidates:
            folder = output_folder(root, stored)
            try:
                doc = load_json((folder / "document.json").read_bytes())
                if doc.get("source_sha256") != source_hash or doc.get("native_version") != version:
                    continue
                if not verify_artifacts(folder):
                    continue
                qa = load_json((folder / "verificacion.json").read_bytes())
                db.execute("INSERT OR REPLACE INTO native_cache VALUES (?,?,?)", (source_hash, version, ident))
                db.execute("INSERT OR IGNORE INTO origins VALUES (?,?)", (ident, str(source)))
                return {"id": ident, "output": str(folder), "reused": True, **qa}
            except (OSError, ValueError):
                continue
    return None


def report(root: Path, query: str | None = None) -> list[dict]:
    if not (root / "indice.sqlite").exists():
        return []
    # Consultas no crean ni modifican la base.
    db = sqlite3.connect((root / "indice.sqlite").resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        result = []
        for row in db.execute("SELECT * FROM documents ORDER BY title,id"):
            entry = dict(row)
            folder = output_folder(root, row["output"])
            entry["output"] = str(folder)
            entry["integrity_ok"] = verify_artifacts(folder)
            # Tipo de paquete: deja a la interfaz distinguir el DOCUMENTO de las páginas
            # sueltas que un lote de IA publica como piezas internas (structured_response).
            entry["kind"] = ""
            if entry["integrity_ok"]:
                try:
                    entry["kind"] = str(json.loads(
                        (folder / "document.json").read_text(encoding="utf-8")).get("input_kind", ""))
                except (OSError, ValueError):
                    entry["kind"] = ""
            if query is not None:
                if not entry["integrity_ok"]:
                    continue
                doc = json.loads((folder / "document.json").read_text(encoding="utf-8"))
                hits = [u["number"] for u in doc["units"]
                        if query.casefold() in "".join(b["text"] for b in u["blocks"]).casefold()]
                if not hits:
                    continue
                entry["units"] = hits
            result.append(entry)
        return result
    finally:
        db.close()


def export_index(root: Path) -> dict:
    """Índice legible y navegable; solo escribe dentro de la salida elegida."""
    root = root.resolve()
    rows = report(root)
    text = ["# Documentos convertidos\n\n",
            "Las coberturas e integridad se comprueban al generar este índice. "
            "La fidelidad contra el original sigue pendiente salvo revisión documentada.\n\n"]
    for row in rows:
        title = row["title"].replace("\n", " ").replace("[", "(").replace("]", ")")
        folder = Path(row["output"]).resolve()
        if not folder.is_relative_to(root):
            raise ValueError("Un resultado del índice apunta fuera de la carpeta de salida")
        link = (folder / "documento.md").relative_to(root).as_posix()
        coverage = "integridad dañada"
        if row["integrity_ok"]:
            qa = load_json((folder / "verificacion.json").read_bytes())
            coverage = f"{qa['received']}/{qa['expected']} unidades; {len(qa['warnings'])} avisos"
        text.append(f"- [{title}](<{link}>) — **{row['status']}**, {coverage}.\n")
    if not rows:
        text.append("Todavía no hay documentos importados.\n")
    root.mkdir(parents=True, exist_ok=True)
    temp = root / (".indice-" + uuid.uuid4().hex + ".tmp")
    temp.write_text("".join(text), encoding="utf-8")
    path = root / "INDICE.md"
    os.replace(temp, path)
    return {"path": str(path), "documents": len(rows)}


# --- Eliminación de resultados -------------------------------------------------------------
# Un resultado vive en DOS sitios: su carpeta y las tablas que la citan. Tocar solo uno deja
# el índice apuntando al vacío («DAÑADO») o una carpeta huérfana. Todo borrado pasa por aquí.

def recycle(folder: Path) -> None:
    """Envía una carpeta a la Papelera de Windows (recuperable), nunca borrado definitivo.

    SHFileOperationW exige RUTA COMPLETA: con una relativa borra de forma permanente aunque se
    pida FOF_ALLOWUNDO, y falla con el prefijo ``\\\\?\\``. Requiere doble terminación nula.
    """
    import ctypes
    from ctypes import wintypes

    target = str(Path(folder).resolve())
    if os.name != "nt":
        raise OSError("La Papelera solo está disponible en Windows")
    if target.startswith("\\\\?\\"):
        raise OSError("Ruta con prefijo extendido: no admite Papelera")

    class _Op(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    op = _Op()
    op.wFunc = 0x0003                                   # FO_DELETE
    op.pFrom = target + "\0"                            # ctypes añade el segundo nulo
    op.fFlags = 0x0040 | 0x0010 | 0x0004 | 0x0400       # ALLOWUNDO|NOCONFIRMATION|SILENT|NOERRORUI
    code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if code or op.fAnyOperationsAborted or Path(target).exists():
        raise OSError(f"La Papelera rechazó la carpeta (código {code})")


def pending_batches(root: Path, identifiers) -> dict[str, str]:
    """{id de paquete: id de lote IA sin finalizar que lo usa como fuente}.

    Borrar ese paquete rompe «Enviar / reanudar lote»: la interfaz debe avisarlo antes.
    """
    wanted = set(identifiers)
    found: dict[str, str] = {}
    if not wanted or not (root / "indice.sqlite").exists():
        return found
    with closing(connect(root)) as db:
        try:
            rows = db.execute("SELECT id,plan,state FROM ai_batches").fetchall()
        except sqlite3.OperationalError:
            return found                                # memoria sin lotes de IA
    for batch, raw_plan, state in rows:
        if state == "finalizado":
            continue
        try:
            source = json.loads(raw_plan).get("source_package_id")
        except ValueError:
            continue
        if source in wanted:
            found[source] = batch
    return found


def delete_documents(root: Path, identifiers, discard=recycle) -> dict:
    """Elimina resultados de forma coherente: filas del índice + carpeta a la Papelera.

    Por documento: se borran sus filas dentro de una transacción y SOLO se confirma si la
    carpeta salió del disco; si la Papelera falla se deshace y el resultado queda intacto.
    Una fila cuya carpeta ya no existe (resultado «dañado») se limpia sin tocar disco.
    Los recibos de consumo (ai_batches, ai_items, provider_runs) no se borran: son historial.
    """
    root = root.resolve()
    deleted, failed = [], []
    for ident in dict.fromkeys(identifiers):
        try:
            ident = str(ident)
            if not re.fullmatch(r"[0-9a-f]{64}(?:-recuperado-[0-9a-f]{8})?", ident):
                raise ValueError("Identificador de documento inválido")
            with closing(connect(root)) as db:
                db.execute("BEGIN IMMEDIATE")
                try:
                    row = db.execute("SELECT output FROM documents WHERE id=?", (ident,)).fetchone()
                    if row is None:
                        raise ValueError("Documento no registrado: no se elimina una carpeta no seleccionada")
                    # El ID permanece estable al recuperar una publicación dañada,
                    # pero su carpeta termina en -recuperado-...: manda el índice.
                    folder = output_folder(root, row[0])
                    db.execute("DELETE FROM documents WHERE id=?", (ident,))
                    db.execute("DELETE FROM origins WHERE id=?", (ident,))
                    db.execute("DELETE FROM native_cache WHERE id=?", (ident,))
                    if folder.exists():
                        discard(folder)
                        if folder.exists():
                            raise OSError("El descarte no retiró la carpeta; se conserva el índice")
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
            deleted.append(ident)
        except Exception as exc:                        # un fallo no detiene a los demás
            failed.append({"id": str(ident), "error": f"{type(exc).__name__}: {exc}"})
    orphans = []
    if deleted:
        # Un lote de IA que apunte a lo recién eliminado quedaría bloqueando el programa.
        orphans = purge_orphan_batches(root)
        export_index(root)
    return {"deleted": deleted, "failed": failed, "batches_purged": orphans, "external_calls": 0}


def purge_orphan_batches(root: Path) -> list[str]:
    """Descarta los lotes de IA cuyo paquete fuente o resultado unido ya no existe.

    Sin esto, un lote que apunta al vacío deja al programa repitiendo «El resultado combinado
    está dañado» para siempre, sin forma de quitarlo desde la ventana. El consumo ya quedó
    registrado en ``provider_runs`` y en los informes de ``datos/lotes_ia``: lo que se borra
    aquí es el puntero roto, no el recibo.
    """
    root = root.resolve()
    if not (root / "indice.sqlite").exists():
        return []
    huerfanos = []
    with closing(connect(root)) as db, db:
        try:
            filas = db.execute("SELECT id,plan,result FROM ai_batches").fetchall()
        except sqlite3.OperationalError:
            return []
        for ident, raw_plan, raw_result in filas:
            rutas = []
            for crudo, clave in ((raw_plan, "source_package_id"), (raw_result, "output")):
                try:
                    valor = json.loads(crudo or "{}").get(clave)
                except ValueError:
                    valor = None
                if valor:
                    rutas.append(valor)
            roto = False
            for valor in rutas:
                try:
                    roto = roto or not verify_artifacts(output_folder(root, str(valor)))
                except ValueError:
                    roto = True
            if roto:
                db.execute("DELETE FROM ai_items WHERE batch=?", (ident,))
                db.execute("DELETE FROM ai_batches WHERE id=?", (ident,))
                huerfanos.append(ident)
    return huerfanos


def prune_missing(root: Path) -> list[str]:
    """Quita del índice las filas cuya carpeta ya no existe. No toca disco."""
    root = root.resolve()
    if not (root / "indice.sqlite").exists():
        return []
    removed = []
    with closing(connect(root)) as db, db:
        for ident, stored in db.execute("SELECT id,output FROM documents").fetchall():
            try:
                missing = not output_folder(root, stored).exists()
            except ValueError:
                missing = True
            if missing:
                db.execute("DELETE FROM documents WHERE id=?", (ident,))
                db.execute("DELETE FROM origins WHERE id=?", (ident,))
                db.execute("DELETE FROM native_cache WHERE id=?", (ident,))
                removed.append(ident)
    return removed


def adopt_orphans(root: Path) -> list[str]:
    """Vuelve a registrar carpetas íntegras sin fila: es el «deshacer» de una eliminación
    (restaurar desde la Papelera + Reparación segura)."""
    root = root.resolve()
    base = root / "documentos"
    if not base.is_dir():
        return []
    adopted = []
    with closing(connect(root)) as db, db:
        known = {row[0] for row in db.execute("SELECT id FROM documents")}
        for folder in sorted(base.iterdir()):
            if folder.name in known or not folder.is_dir() or not verify_artifacts(folder):
                continue
            try:
                output_folder(root, folder.name)
                doc = load_json((folder / "document.json").read_bytes())
                qa = load_json((folder / "verificacion.json").read_bytes())
                created = load_json((folder / "manifest.json").read_bytes()).get("created", "")
            except (OSError, ValueError, KeyError):
                continue
            db.execute("INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?)",
                       (folder.name, str(doc.get("title", folder.name)), str(qa.get("status", "revisar")),
                        str(folder), created or datetime.now(timezone.utc).isoformat()))
            db.execute("INSERT OR IGNORE INTO origins VALUES (?,?)",
                       (folder.name, str(doc.get("source_path", ""))))
            adopted.append(folder.name)
    return adopted


# --- Exportación del Markdown con su nombre real ---------------------------------------------
# Dentro de la memoria cada resultado se llama ``documento.md`` en una carpeta de 64 caracteres:
# íntegro y deduplicado, pero imposible de ubicar a ojo. Al usuario le importa EL ARCHIVO .md,
# así que se copia a una carpeta legible con el título como nombre. El paquete no se toca.

_INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)),
                   *(f"LPT{n}" for n in range(1, 10))}


def safe_filename(title: str) -> str:
    name = _INVALID_NAME.sub(" ", str(title))
    name = re.sub(r"\s+", " ", name).strip(" .")[:150].rstrip(" .")
    if not name or name.split(".", 1)[0].upper() in _RESERVED_NAMES:
        name = "documento"
    return name


def _export_source_id(doc: dict, package_id: str) -> str:
    """Identidad conservadora: fuente, productor y alcance, nunca solo el título.

    No usamos source_sha256: cambia al corregir el MISMO archivo. Las páginas
    importadas pueden compartir carpeta, por eso sus rutas también identifican
    la fuente. Si falta procedencia, solo el mismo paquete acredita propiedad.
    Mover una fuente de PC/ruta crea otra identidad hasta revisión explícita.
    """
    source = doc.get("source_path")
    if not isinstance(source, str) or not source.strip():
        return "package:" + package_id

    def normalized(value):
        return os.path.normcase(os.path.normpath(str(value)))

    identity = {"source": normalized(source), "kind": doc.get("input_kind"),
                "expected_units": doc.get("expected_units"),
                "unit_sources": sorted({normalized(unit["source_path"])
                                        for unit in doc.get("units", [])
                                        if unit.get("source_path")})}
    return "source:" + digest(json_bytes(identity))


def _export_registry(path: Path) -> dict:
    try:
        registry = load_json(path.read_bytes())
    except FileNotFoundError:
        return {"version": 2, "exports": {}}
    if not isinstance(registry, dict):
        raise ValueError("Registro de exportaciones inválido; se conserva sin modificar")
    if "version" not in registry:
        # V1 solo guardaba {ruta: título}. No demuestra qué FUENTE lo escribió.
        return {"version": 2, "exports": {}, "legacy_unverified": registry}
    if registry.get("version") != 2 or not isinstance(registry.get("exports"), dict):
        raise ValueError("Versión de registro de exportaciones no soportada")
    return registry


def _write_synced(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _publish_new_export(temporary: Path, target: Path) -> None:
    """Publicación completa que falla, en vez de pisar, si otro crea el destino."""
    if os.name == "nt":
        # En Windows rename no reemplaza destinos existentes (replace sí).
        os.rename(temporary, target)
    else:
        # rename POSIX sí sobrescribe. link publica el inode completo sin reemplazo.
        os.link(temporary, target)
        temporary.unlink()


def export_markdown(root: Path, identifier: str, destination: Path) -> dict:
    """Copia ``documento.md`` a ``destination`` como «<título>.md». Devuelve la ruta final.

    - Imágenes, control y verificación usan enlaces relativos al destino cuando
      comparten unidad. Mover biblioteca + MD conservando su estructura mantiene
      esos enlaces. Unidades distintas requieren URI absoluto y aviso explícito.
      No se duplican activos ni se migran exportaciones existentes en lote.
    - Solo actualiza una exportación de la misma fuente Y alcance cuyo contenido
      coincide con la última huella registrada. Homónimos y ediciones manuales
      se conservan usando « (2)», « (3)»…; registros v1 no acreditan propiedad.
    - La transacción SQLite serializa GUI/CLI de esta memoria; publicación y
      registro son atómicos por archivo. Un corte entre ambos puede producir una
      copia adicional, nunca autoriza sobrescribir un archivo no reconocido.
    """
    root = root.resolve()
    folder = output_folder(root, str(identifier))
    database = root / "indice.sqlite"
    if database.exists():
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
            row = db.execute("SELECT output FROM documents WHERE id=?", (folder.name,)).fetchone()
        if row:
            folder = output_folder(root, row[0])
    if not verify_artifacts(folder):
        raise ValueError("Resultado dañado: no se exporta un Markdown sin integridad")
    doc = load_json((folder / "document.json").read_bytes())
    destination = Path(destination).resolve()
    if destination.is_relative_to(root / "documentos"):
        raise ValueError("La exportación legible no puede escribirse dentro de paquetes inmutables")
    # Un .md que se IMPORTÓ desde la propia carpeta de destino ya está ahí con su nombre:
    # re-exportarlo solo fabricaría un duplicado « (2)». Ese archivo ES su Markdown.
    origin = Path(str(doc.get("source_path") or ""))
    try:
        if (origin.suffix.lower() == ".md" and origin.is_file()
                and origin.resolve().parent == destination.resolve()):
            return {"path": str(origin.resolve()), "created": False, "bytes": origin.stat().st_size,
                    "already_there": True, "portable_links": None, "link_mode": "unchanged",
                    "link_warning": "Markdown ya existente: se conserva sin reescribir ni certificar sus enlaces."}
    except OSError:
        pass
    text = (folder / "documento.md").read_text(encoding="utf-8")
    from urllib.parse import quote
    try:
        relative_base = Path(os.path.relpath(folder, destination)).as_posix()
        link_info = {"portable_links": True, "link_mode": "relative"}
    except ValueError:
        # Windows no representa con ../ un destino situado en otra unidad/share.
        relative_base = None
        link_info = {"portable_links": False, "link_mode": "absolute",
                     "link_warning": "MD y biblioteca están en unidades distintas. Se usan enlaces absolutos; "
                                     "no serán portables al moverlos. Conserva ambos en una misma unidad para exportar enlaces relativos."}
    manifest = load_json((folder / "manifest.json").read_bytes())
    references = [name for name in manifest["files"]
                  if name in {"document.json", "verificacion.json"} or name.startswith("imagenes/")]
    for name in references:
        # Solo recursos publicados/verificados; nunca reinterpretar rutas arbitrarias
        # que aparezcan dentro del texto. También admite espacios y paréntesis.
        url = (quote(relative_base + "/" + name, safe="/") if relative_base is not None
               else folder.as_uri() + "/" + quote(name, safe="/"))
        for previous in ("](" + name + ")", "](<" + name + ">)"):
            text = text.replace(previous, "](<" + url + ">)")
    data = text.encode("utf-8")
    destination.mkdir(parents=True, exist_ok=True)
    stem = safe_filename(doc.get("title") or folder.name)
    source_id = _export_source_id(doc, folder.name)
    checksum = digest(data)
    registro_path = root / "exportados.json"
    with closing(connect(root)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        registro = _export_registry(registro_path)
        exports = registro["exports"]
        for attempt in range(1, 1000):
            target = destination / (stem + ("" if attempt == 1 else f" ({attempt})") + ".md")
            # No seguir un enlace que haya sustituido una exportación anterior.
            if target.is_symlink() or target.is_dir():
                continue
            clave = os.path.normcase(str(target))
            previous = exports.get(clave)
            exists = target.exists()
            current = target.read_bytes() if exists else None
            owned = (isinstance(previous, dict) and previous.get("source_id") == source_id
                     and previous.get("content_sha256") == (digest(current) if current is not None else None))
            if exists and current == data:
                return {"path": str(target), "created": False, "bytes": len(data), **link_info}
            if exists and not owned:
                continue
            temporary = destination / (".exportando-" + uuid.uuid4().hex + ".tmp")
            try:
                _write_synced(temporary, data)
                if owned:
                    # Segundo control después de escribir el temporal: no perder una
                    # edición externa ocurrida durante ese trabajo. Los escritores de
                    # esta memoria ya están serializados por BEGIN IMMEDIATE.
                    if target.is_symlink() or target.read_bytes() != current:
                        continue
                    os.replace(temporary, target)
                else:
                    try:
                        _publish_new_export(temporary, target)
                    except FileExistsError:
                        continue
                exports[clave] = {"source_id": source_id, "content_sha256": checksum,
                                  "package_id": folder.name, "title": doc.get("title", "")}
                temp_reg = root / (".exportados-" + uuid.uuid4().hex + ".tmp")
                try:
                    _write_synced(temp_reg, json_bytes(registro))
                    os.replace(temp_reg, registro_path)
                finally:
                    temp_reg.unlink(missing_ok=True)
                return {"path": str(target), "created": True, "bytes": len(data), "overwritten": owned, **link_info}
            finally:
                temporary.unlink(missing_ok=True)
    raise OSError("Demasiados archivos con el mismo nombre en la carpeta de destino")
