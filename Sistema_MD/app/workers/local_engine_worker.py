"""Protocolo local JSON v1: probe/convert, un encargo por proceso.

No importar desde la aplicación para convertir: ejecutar con Python aislado -I.
Auditoría Python no es contención OS de código nativo. Nunca habilitar red/remotos.
Fuentes: docling-project.github.io/docling/usage/advanced_options/
github.com/docling-project/docling (document_converter, pipeline_options)
github.com/microsoft/markitdown (MarkItDown, convert_local, register_converter).
"""
import contextlib
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import zipfile

MAX_SOURCE = 100_000_000
MAX_OUTPUT = 32_000_000
ENGINES = ("docling", "markitdown")
SELECTIVE = {".docx": "DocxConverter", ".xlsx": "XlsxConverter", ".pptx": "PptxConverter",
    ".html": "HtmlConverter", ".htm": "HtmlConverter", ".epub": "EpubConverter",
    ".txt": "PlainTextConverter", ".md": "PlainTextConverter", ".csv": "CsvConverter"}


class GuardError(PermissionError):
    pass


def install_local_guard():
    """Bloquea eventos auditados de red/procesos ANTES de importar un motor."""
    def guard(event, args):
        if event in {"socket.connect", "socket.bind", "socket.sendto", "socket.sendmsg",
                     "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr", "socket.getnameinfo"}:
            raise GuardError("network_blocked")
        if event in {"subprocess.Popen", "os.system", "os.posix_spawn", "os.spawn", "os.exec"}:
            raise GuardError("process_blocked")
    sys.addaudithook(guard)
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1")


def _strict(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("constant")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def _probe(engine):
    present = importlib.util.find_spec(engine) is not None
    try:
        version = importlib.metadata.version(engine) if present else None
    except importlib.metadata.PackageNotFoundError:
        version = None
    dependencies = sorted((d.metadata.get("Name", ""), d.version) for d in importlib.metadata.distributions())
    return {"present": present, "version": version, "importable": None, "models_ready": None,
        "python_version": sys.version.split()[0],
        "dependency_fingerprint": hashlib.sha256(json.dumps(dependencies).encode()).hexdigest()}


def _archive_safe(source):
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if len(entries) > 20000 or sum(e.file_size for e in entries) > 200_000_000:
            raise ValueError("archive_limit")
        if len({e.filename for e in entries}) != len(entries):
            raise ValueError("archive_duplicate")
        for entry in entries:
            name = PurePosixPath(entry.filename.replace("\\", "/"))
            if (name.is_absolute() or ".." in name.parts or ":" in entry.filename
                    or entry.flag_bits & 1 or entry.file_size > 50_000_000
                    or stat.S_ISLNK(entry.external_attr >> 16)):
                raise ValueError("archive_rejected")
        names = {e.filename for e in entries}
        suffix = source.suffix.lower()
        expected = {".docx": "word/document.xml", ".xlsx": "xl/workbook.xml", ".pptx": "ppt/presentation.xml"}
        if suffix in expected and (expected[suffix] not in names or "[Content_Types].xml" not in names):
            raise ValueError("signature_mismatch")
        if suffix == ".epub" and ("mimetype" not in names or archive.read("mimetype") != b"application/epub+zip"):
            raise ValueError("signature_mismatch")


def _source(job):
    source = Path(job["source"])
    if (not source.is_absolute() or source.is_symlink() or source.resolve().parent != Path.cwd().resolve()
            or source.name != "entrada" + source.suffix.lower() or not source.is_file()
            or source.stat().st_size > MAX_SOURCE):
        raise ValueError("source_rejected")
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != job.get("source_sha256"):
        raise ValueError("source_changed")
    suffix = source.suffix.lower()
    if suffix in {".docx", ".xlsx", ".pptx", ".epub"}:
        _archive_safe(source)
    elif data.startswith(b"PK"):
        raise ValueError("disguised_archive")
    return source


def _markitdown(source, pages):
    if source.suffix.lower() not in SELECTIVE or pages is not None:
        raise ValueError("format_rejected")
    from markitdown import MarkItDown
    from markitdown import converters
    # Ningún ZIP/audio/URL/YouTube/OCR/LLM ni plugin registrado. El tipo elegido
    # debe admitir el archivo; no existe fallback al parser de texto plano.
    converter = MarkItDown(enable_builtins=False, enable_plugins=False)
    converter.register_converter(getattr(converters, SELECTIVE[source.suffix.lower()])())
    result = converter.convert_local(source)
    text = result.text_content
    if not isinstance(text, str):
        raise ValueError("invalid_result")
    return {"raw": {"text_content": text, "title": getattr(result, "title", None)}, "markdown": text,
            "units": [{"number": 1, "markdown": text}], "partial": True}


def _docling(source, pages, models_path):
    if source.suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}:
        raise ValueError("format_rejected")
    models = Path(models_path) if models_path else None
    if not models or not models.is_dir() or not (models / "EasyOcr").is_dir():
        raise ValueError("assets_missing")
    if (not isinstance(pages, list) or not pages or len(pages) > 500
            or any(type(n) is not int or not 1 <= n <= 500 for n in pages)
            or pages != sorted(set(pages))):
        raise ValueError("invalid_pages")
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, EasyOcrOptions, AcceleratorOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption, ImageFormatOption
    options = PdfPipelineOptions(artifacts_path=models, enable_remote_services=False,
        do_ocr=True, do_table_structure=True, do_picture_description=False, do_picture_classification=False)
    options.ocr_options = EasyOcrOptions(download_enabled=False, lang=["es", "en"], use_gpu=False)
    options.accelerator_options = AcceleratorOptions(device="cpu", num_threads=2)
    converter = DocumentConverter(allowed_formats=[InputFormat.PDF, InputFormat.IMAGE], format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=options),
        InputFormat.IMAGE: ImageFormatOption(pipeline_options=options)})
    ranges = []
    for page in pages:
        if ranges and page == ranges[-1][1] + 1:
            ranges[-1][1] = page
        else:
            ranges.append([page, page])
    raw, units, markdown, partial = [], [], [], False
    for first, last in ranges:
        result = converter.convert(source, page_range=(first, last), max_num_pages=500, max_file_size=MAX_SOURCE)
        document = result.document
        content = document.export_to_markdown()
        raw.append({"page_range": [first, last], "document": document.export_to_dict()})
        markdown.append(content)
        partial |= str(getattr(result.status, "value", result.status)) != "success"
        # Solo publicar páginas que Docling localizó. Faltantes permanecen ausentes
        # para que el inventario independiente del padre los marque pendientes.
        for page in range(first, last + 1):
            if page in document.pages:
                units.append({"number": page, "markdown": document.export_to_markdown(page_no=page)})
    return {"raw": raw, "markdown": "\n\n".join(markdown), "units": units, "partial": partial}


def handle(job):
    if (not isinstance(job, dict) or job.get("protocol") != 1 or job.get("engine") not in ENGINES
            or job.get("op") not in {"probe", "convert"}):
        raise ValueError("invalid_job")
    engine = job["engine"]
    probe = _probe(engine)
    if job["op"] == "probe":
        return probe
    if not probe["present"]:
        raise ValueError("engine_missing")
    source = _source(job)
    result = (_markitdown(source, job.get("pages")) if engine == "markitdown"
              else _docling(source, job.get("pages"), job.get("models_path")))
    return {**result, "engine": engine, "version": probe["version"], "importable": True}


def main():
    install_local_guard()
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError("job_limit")
        job = _strict(raw)
        with contextlib.redirect_stdout(sys.stderr):
            result = {"protocol": 1, "ok": True, **handle(job)}
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_OUTPUT:
            raise ValueError("worker_output_limit")
    except Exception as exc:
        code = str(exc) if isinstance(exc, (ValueError, GuardError)) else "worker_failed"
        if code not in {"engine_missing", "assets_missing", "network_blocked", "process_blocked",
                        "format_rejected", "worker_output_limit"}:
            code = "worker_failed"
        encoded = json.dumps({"protocol": 1, "ok": False, "code": code}).encode("utf-8")
    sys.stdout.buffer.write(encoded + b"\n")
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
