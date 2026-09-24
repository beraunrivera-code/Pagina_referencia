"""Adaptadores locales. Importar no significa certificar la fuente."""

from __future__ import annotations

import importlib.util
import codecs
import os
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from .documents import SCHEMA_VERSION, digest, json_bytes, parse_markdown, read_stable
from .native import (KINDS as NATIVE_KINDS, ODA_EXE, como, docx_texto, dwg_md, excel_tres_capas,
                     limpiar_temporales, pptx_texto, visio_md)
from .storage import publish, prepared_cache
from .local_io import scratch, check_archive, exclusive

NATIVE_VERSION = 5

# El render es local, pero las imágenes PNG y el texto se retienen hasta publish().
# Tope del contenido acumulado (no promesa de máximo RSS del proceso).
MAX_PREPARED_BYTES = 256 * 1024 * 1024


def detect(path: Path) -> str:
    with path.open("rb") as stream:
        head = stream.read(1024)
    if not head:
        return "empty"
    for signature, kind in [(b"%PDF", "pdf"), (b"\x89PNG\r\n\x1a\n", "png"),
                            (b"\xff\xd8\xff", "jpg"), (b"AC10", "dwg"),
                            (b"AutoCAD Binary DXF", "dxf"),
                            (b"\xd0\xcf\x11\xe0", "office-legacy"),
                            (b"II*\0", "tif"), (b"MM\0*", "tif")]:
        if head.startswith(signature):
            return kind
    # DXF ASCII: grupo 0 + SECTION (tolera comentarios 999 iniciales). Sin esta firma un .dxf
    # caía en "text" y se habría publicado como prosa.
    if re.match(rb"(?:\s*999[^\r\n]*\r?\n[^\r\n]*\r?\n)*\s*0\s*\r?\n\s*SECTION\b", head):
        return "dxf"
    if head.startswith(b"PK"):
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                kinds = [kind for prefix, kind in [("word/", "docx"), ("xl/", "xlsx"),
                                                    ("ppt/", "pptx"), ("visio/", "vsdx")]
                         if any(n.startswith(prefix) for n in names)]
                return kinds[0] if len(kinds) == 1 else ("zip-ambiguous" if kinds else "zip")
        except zipfile.BadZipFile:
            return "zip-damaged"
    try:
        codecs.getincrementaldecoder("utf-8-sig")().decode(head, final=False)
        if b"\0" not in head:
            return path.suffix.lower().lstrip(".") if path.suffix.lower() in {".txt", ".md", ".csv", ".json", ".html", ".xml"} else "text"
    except UnicodeDecodeError:
        pass
    return "unsupported"


def file_hash(path: Path) -> str:
    import hashlib
    before = path.stat()
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"Archivo en modificación: {path}")
    return value.hexdigest()


def inventory(source: Path, limit: int = 200, exclude: Path | None = None) -> dict:
    source = source.resolve(strict=True)
    if limit < 1:
        raise ValueError("El límite debe ser positivo")
    rows, errors = [], []
    truncated = False

    def files():
        if source.is_file():
            yield source
            return
        for folder, dirs, names in os.walk(source, followlinks=False,
                                            onerror=lambda e: errors.append(str(e))):
            dirs[:] = sorted(d for d in dirs if d not in {".git", ".claude", ".codex", ".venv", "__pycache__"}
                             and not (Path(folder) / d).is_symlink()
                             and not (hasattr(Path(folder) / d, "is_junction") and (Path(folder) / d).is_junction())
                             and (exclude is None or not (Path(folder) / d).resolve().is_relative_to(exclude.resolve())))
            for name in sorted(names):
                yield Path(folder) / name

    for path in files():
        if exclude and path.resolve().is_relative_to(exclude.resolve()):
            continue
        if len(rows) >= limit:
            truncated = True
            break
        try:
            stat = path.stat()
            # No hidratar archivos de nube ni seguir symlinks al inventariar.
            attrs = getattr(stat, "st_file_attributes", 0)
            if path.is_symlink() or attrs & (0x1000 | 0x400000):
                rows.append({"path": str(path), "format": "deferred", "bytes": stat.st_size})
                continue
            rows.append({"path": str(path), "format": detect(path), "bytes": stat.st_size,
                         "sha256": file_hash(path)})
        except (OSError, ValueError) as exc:
            errors.append(f"{path}: {exc}")
    return {"source": str(source), "files": rows, "count": len(rows),
            "truncated": truncated, "limit": limit, "errors": errors}


def doctor() -> dict:
    return {"python": sys.version.split()[0], "executable": sys.executable,
            "local_core": "disponible", "agy": shutil.which("agy"),
            "libraries": {m: bool(importlib.util.find_spec(m))
                          for m in ["fitz", "openpyxl", "docx", "pptx", "ezdxf"]},
            "oda_file_converter": os.path.isfile(ODA_EXE),
            "accounts_declared_by_user": 3, "account_sessions_verified": 0,
            "external_calls": 0, "note": "Detectar una librería/CLI no prueba conversión ni cuotas."}


def import_pages(root: Path, pages_dir: Path, prefix: str, expected: list[int], provider: str,
                 title: str, images_dir: Path | None = None, contract: Path | None = None) -> dict:
    pages_dir = pages_dir.resolve(strict=True)
    if not pages_dir.is_dir() or not re.fullmatch(r"[A-Za-z0-9_-]+", prefix):
        raise ValueError("Carpeta o prefijo inválido")
    # Evita que datos arbitrarios escriban archivos fuera de un trabajo.
    if not expected or len(expected) != len(set(expected)) or any(type(n) is not int or n < 1 for n in expected):
        raise ValueError("Alcance inválido")
    if images_dir is not None:
        images_dir = images_dir.resolve(strict=True)
        if not images_dir.is_dir():
            raise ValueError("La ruta de imágenes debe ser una carpeta")
    units, skipped, assets, missing_images = [], [], {}, []
    for number in sorted(expected):
        candidates = list(pages_dir.glob(f"{prefix}_p*.md"))
        matches = [p for p in candidates if re.fullmatch(re.escape(prefix) + r"_p0*" + str(number) + r"\.md", p.name)]
        if len(matches) > 1:
            raise ValueError(f"Página {number}: hay nombres ambiguos")
        if not matches:
            skipped.append({"unit": number, "reason": "missing"})
            continue
        path = matches[0]
        data = read_stable(path)
        text = data.decode("utf-8-sig")
        if not text.strip():
            skipped.append({"unit": number, "reason": "empty"})
            continue
        unit = {"number": number, "source_path": str(path), "sha256": digest(data),
                "blocks": parse_markdown(text, number)}
        if images_dir:
            image = images_dir / f"{prefix}_p{number:02d}.png"
            if image.exists():
                content = read_stable(image)
                if not content.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ValueError(f"Referencia PNG inválida: {image}")
                asset = f"imagenes/p{number:04d}.png"
                assets[asset] = content
                unit.update(image_asset=asset, image_source=str(image.resolve()), image_sha256=digest(content))
            else:
                missing_images.append(number)
        units.append(unit)
    doc = {"schema_version": SCHEMA_VERSION, "title": title, "input_kind": "legacy_pages",
           "source_path": str(pages_dir), "source_native": None,
           "expected_units": sorted(expected), "scope": "páginas indicadas; total del nativo no verificado",
           "provider": provider, "producer_contract_sha256": None,
           "reference_contract_sha256": digest(read_stable(contract)) if contract else None,
           "units": units, "skipped": skipped, "missing_images": missing_images}
    return publish(root, doc, assets)


def convert_text(root: Path, source: Path) -> dict:
    source = source.resolve(strict=True)
    if detect(source) not in {"md", "txt", "text"}:
        raise ValueError("Esta versión convierte texto/MD. Usa preparar-pdf o importar-paginas para PDF.")
    data = read_stable(source)
    text = data.decode("utf-8-sig")
    if not text.strip():
        raise ValueError("No se publica una fuente vacía")
    doc = {"schema_version": SCHEMA_VERSION, "title": source.stem, "input_kind": "text",
           "source_path": str(source), "source_sha256": digest(data), "expected_units": [1],
           "provider": "local", "scope": "texto completo; enlaces externos no importados",
           "units": [{"number": 1, "source_path": str(source), "sha256": digest(data),
                      "blocks": parse_markdown(text, 1)}]}
    return publish(root, doc)


def convert_native(root: Path, source: Path) -> dict:
    with exclusive(root, "conversion-nativa"):
        return _convert_native(root, source)


def _convert_native(root: Path, source: Path) -> dict:
    """Word, Excel, PowerPoint, Visio y CAD a paquete trazable con motores locales; 0 llamadas a IA.

    Reproduce por formato el ensamblado de ``convertir.py::main()`` (SISTEMA_CONVERSION, 14-sep):
    cuerpo del motor + ``## Testigo``. Las imágenes embebidas y los CSV derivados se guardan como
    recursos del paquete (``imagenes/``, ``derivados/``); describirlas es otra fase y se declara con
    ``[PENDIENTE: …]`` para que la verificación lo avise en lugar de fingir cobertura.
    """
    source = source.resolve(strict=True)
    kind = detect(source)
    if kind not in NATIVE_KINDS:
        raise ValueError(f"El adaptador {kind} todavía no está implementado")
    original_hash = file_hash(source)
    from .storage import native_cache
    cached = native_cache(root, original_hash, NATIVE_VERSION, source)
    if cached:
        return cached
    if kind in {"docx", "xlsx", "pptx", "vsdx"}:
        check_archive(source)
    assets: dict[str, bytes] = {}
    producer_warnings: list[str] = []
    pending: list[str] = []
    with scratch(root) as dest:
        try:
            # La extensión miente (medido: 113 de 13.646): las librerías validan por extensión. Los
            # motores de Office aplican como() por dentro y así conservan el nombre real en el MD;
            # solo CAD necesita la copia desde aquí, porque ODA filtra por *.DWG;*.DXF. El nativo no se toca.
            ruta = como(str(source), kind) if kind in {"dwg", "dxf"} else str(source)
            if kind == "docx":
                body, images = docx_texto(ruta, dest)
                witness = body.strip().splitlines()[0][:90] if body.strip() else source.name
                provider, scope = "python-docx-local", "párrafos por estilo, tablas y %d imágenes embebidas registradas; sin describir" % images
                if images:
                    pending.append("[PENDIENTE: %d imágenes embebidas guardadas en imagenes/ del paquete, sin describir en esta fase.]" % images)
            elif kind == "xlsx":
                body, formulas, images, structure = excel_tres_capas(ruta, dest)
                lines = body.splitlines()
                witness = lines[2][:90] if len(lines) > 2 else source.name
                provider = "openpyxl-local"
                scope = ("lectura estructurada desde tablas/rangos nativos, rejilla completa, %d fórmulas en "
                         "derivados/formulas.csv y %d imágenes embebidas registradas; sin describir"
                         % (formulas, len(images)))
                if images:
                    pending.append("[PENDIENTE: %d imágenes embebidas guardadas en imagenes/ del paquete, sin describir en esta fase.]" % len(images))
            elif kind == "pptx":
                body, visuals, visual_slides = pptx_texto(ruta, dest)
                lines = body.strip().splitlines()
                witness = lines[1][:90] if len(lines) > 1 else source.name
                provider = "python-pptx-local"
                scope = "texto y notas del orador; %d formas visuales en %d diapositivas, sin renderizar ni describir" % (visuals, len(visual_slides))
                if visual_slides:
                    pending.append("[PENDIENTE: %d diapositivas con contenido visual (%s) sin renderizar ni describir en esta fase.]"
                                   % (len(visual_slides), ", ".join(str(n) for n in sorted(visual_slides))))
            elif kind == "vsdx":
                body, edges = visio_md(ruta, source.name, dest)
                witness = None  # visio_md ya cierra con su propio testigo
                provider, scope = "visio-xml-local", "formas y %d conectores del XML; flujo reconstruido, no descrito" % edges
                producer_warnings.append("Visio: revisar formas agrupadas, conectores incompletos, orden de páginas y geometría contra el original")
                if not edges:
                    producer_warnings.append("sin conectores legibles: el diagrama NO se reconstruyó")
            else:  # dwg / dxf
                body, entities = dwg_md(ruta, source.name, dest)
                witness = None  # dwg_md ya cierra con su propio testigo
                provider = "oda-ezdxf-local" if kind == "dwg" else "ezdxf-local"
                scope = "textos, bloques, capas y geometría medidos (%d entidades); el dibujo no se transcribe" % entities
                producer_warnings.append("CAD: longitudes aproximadas; arcos, polilíneas y bloques anidados requieren validación antes de metrados")
            if kind == "docx":
                producer_warnings.append("Word: cuerpo principal; encabezados, pies, revisiones, tablas anidadas y geometría de celdas combinadas requieren revisión")
            if kind == "xlsx":
                producer_warnings.append("Excel: valores cacheados sin recalcular; estilos, combinaciones, gráficos y macros no equivalen a transcripción visual")
            for name in sorted(os.listdir(dest)):
                path = Path(dest) / name
                if path.is_file():
                    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)
                    extension = Path(safe).suffix.lower()
                    folder = "imagenes" if extension in {".png", ".jpg", ".jpeg", ".gif", ".emf"} else "derivados"
                    assets[f"{folder}/{safe}"] = path.read_bytes()
        finally:
            limpiar_temporales()
    if file_hash(source) != original_hash:
        raise ValueError("El archivo cambió durante la conversión; paquete descartado")
    if not body.strip() and not assets:
        raise ValueError("No se publica una fuente vacía")
    text = body
    if pending:
        text += "\n\n" + "\n\n".join(pending)
    if witness is not None:
        text += "\n\n## Testigo\n> «%s»" % witness
    if assets:
        text += "\n\n## Recursos conservados\n\n" + "\n".join(f"- [{name}](<{name}>)" for name in assets)
    doc = {"schema_version": SCHEMA_VERSION, "title": source.stem, "input_kind": kind,
           "native_version": NATIVE_VERSION,
           "source_path": str(source), "source_sha256": original_hash, "expected_units": [1],
           "provider": provider, "scope": scope,
           "units": [{"number": 1, "source_path": str(source), "sha256": original_hash,
                       "blocks": parse_markdown(text, 1)}]}
    if kind == "xlsx":
        doc["structure_asset"] = "derivados/estructura.json"
        doc["audit_asset"] = "derivados/rejilla_original.md"
        doc["structure_version"] = structure.get("schema_version")
    if producer_warnings:
        doc["producer_warnings"] = producer_warnings
    return publish(root, doc, assets)


def prepare_pdf(root: Path, source: Path, pages: list[int], dpi: int = 150) -> dict:
    """Extrae solo páginas indicadas a un paquete local reanudable, sin inferencia."""
    source = source.resolve(strict=True)
    if detect(source) != "pdf" or not 72 <= dpi <= 300:
        raise ValueError("Se requiere PDF real y resolución entre 72 y 300 dpi")
    # La preparación no consume cuota API. Los topes de píxeles por página y bytes
    # acumulados son independientes: muchos PNG pequeños también pueden agotar memoria.
    if (not pages or len(pages) > 2000 or any(type(n) is not int or n < 1 for n in pages)
            or len(set(pages)) != len(pages)):
        raise ValueError("Alcance inválido: de 1 a 2000 páginas por llamada, sin duplicados")
    pages = sorted(pages)
    original_hash = file_hash(source)
    cached = prepared_cache(root, original_hash, pages, dpi)
    if cached:
        return cached
    import fitz
    units, assets, avisos = [], {}, []
    prepared_bytes = 0
    with fitz.open(source) as pdf:
        if pdf.needs_pass:
            raise ValueError("PDF protegido: requiere acceso explícito")
        if any(n < 1 or n > len(pdf) for n in pages):
            raise ValueError(f"Fuera de rango: el PDF tiene {len(pdf)} páginas")
        total = len(pdf)
        for n in pages:
            page = pdf[n - 1]
            pixels = page.rect.width * page.rect.height * (dpi / 72) ** 2
            if pixels > 25_000_000:
                raise ValueError(f"Página {n}: render >25 MP; usa menos dpi o prepara recortes")
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            name = f"imagenes/p{n:04d}.png"
            content = pix.tobytes("png")
            # MEDIDO el 2026-09-20 sobre un PDF real de 48 páginas, tres estrategias:
            #   sort=False (orden de pintado)  -> 0,0 de destrozo; títulos en su sitio
            #   sort=True  (orden geométrico)  -> PEOR en 47 de 48 páginas (21,5 de media):
            #                                     despedaza el texto rotado de las láminas
            #   bloques ordenados por (y, x)   -> reordena 43 de 48 y en prosa pone el cuerpo
            #                                     ANTES del título
            # El orden de pintado gana. Cuando el título aparece "al final" es porque la página
            # es un diagrama: ahí no existe orden lineal correcto y se avisa más abajo.
            text = page.get_text(sort=False)
            prepared_bytes += len(content) + len(text.encode("utf-8"))
            if prepared_bytes > MAX_PREPARED_BYTES:
                raise ValueError(
                    f"Página {n}: límite de memoria del paquete preparado "
                    f"({MAX_PREPARED_BYTES // (1024 * 1024)} MiB de contenido). "
                    "Selecciona menos páginas y prepara lotes separados o reduce los dpi. "
                    "No se publicó un paquete parcial.")
            assets[name] = content
            blocks = [{"id": f"p{n}-b1", "kind": "paragraph",
                       "text": text if text.strip() else "[PENDIENTE: lectura visual/OCR.]\n"}]
            # Umbral CALIBRADO contra un PDF real de 48 páginas, con control conocido
            # (láminas 1/15/19/20 frente a prosa 2/3/44/45/46). "3 elementos y <1200
            # caracteres" marcaba 44 de 48 páginas, incluida prosa corrida: un aviso que
            # salta siempre no informa. Esta regla marca 23 y no confunde ninguna de las dos.
            dibujos, imagenes = len(page.get_drawings()), len(page.get_images())
            if dibujos + imagenes >= 6 or len(text.strip()) < 150:
                avisos.append(
                    f"Unidad {n}: página predominantemente gráfica ({dibujos} dibujos, "
                    f"{imagenes} imágenes, {len(text.strip())} caracteres de texto). El texto "
                    "embebido son etiquetas sueltas; su relación requiere lectura visual.")
            units.append({"number": n, "blocks": blocks, "image_asset": name,
                          "image_sha256": digest(content), "embedded_characters": len(text),
                          "images": imagenes, "drawings": dibujos,
                          "annotations": sum(1 for _ in (page.annots() or []))})
    if file_hash(source) != original_hash:
        raise ValueError("El PDF cambió durante la extracción; paquete descartado")
    doc = {"schema_version": SCHEMA_VERSION, "title": source.stem, "input_kind": "pdf_prepared",
           "source_path": str(source), "source_sha256": original_hash, "source_units": total,
           "expected_units": pages, "provider": "pymupdf-local", "dpi": dpi,
           "scope": "texto embebido en orden de pintado y referencia visual; orden de lectura, OCR y semántica pendientes",
           "producer_warnings": avisos, "units": units}
    return publish(root, doc, assets)
