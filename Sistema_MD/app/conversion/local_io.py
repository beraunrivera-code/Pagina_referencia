"""Temporales por operación y límites explícitos; sin cambiar permisos del usuario."""
from contextlib import contextmanager
from pathlib import Path
import shutil
import uuid
import warnings
import zipfile


@contextmanager
def exclusive(root, name="lote-local"):
    """Bloqueo OS no bloqueante; se libera incluso si termina el proceso."""
    parent = Path(root).resolve()
    parent.mkdir(parents=True, exist_ok=True)
    with (parent / (name + ".lock")).open("a+b") as stream:
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b"0"); stream.flush()
        stream.seek(0)
        try:
            if __import__("os").name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("Otro proceso está ejecutando esta operación; no se duplica") from None
        try:
            yield
        finally:
            stream.seek(0)
            if __import__("os").name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


@contextmanager
def scratch(root):
    parent = Path(root).resolve() / "temporales"
    parent.mkdir(parents=True, exist_ok=True)
    folder = parent / ("nativo-" + uuid.uuid4().hex)
    folder.mkdir()
    try:
        yield str(folder)
    finally:
        target = folder.resolve()
        if target.parent != parent.resolve() or not target.name.startswith("nativo-"):
            raise ValueError("Temporal fuera de su ámbito")
        try:
            shutil.rmtree(target)
        except OSError:
            warnings.warn(f"Temporal conservado para diagnóstico: {target}", RuntimeWarning)


def check_archive(source):
    """Rechaza entradas ambiguas y tamaños descomprimidos excesivos antes del parser."""
    if Path(source).stat().st_size > 100_000_000:
        raise ValueError("Documento >100 MB: requiere un alcance especial, no se recorta")
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if len(entries) > 20000 or sum(e.file_size for e in entries) > 200_000_000:
            raise ValueError("ZIP excede 20.000 entradas o 200 MB descomprimidos")
        if len({e.filename for e in entries}) != len(entries):
            raise ValueError("ZIP contiene nombres duplicados")
        for entry in entries:
            if entry.flag_bits & 1 or entry.file_size > 50_000_000:
                raise ValueError("Entrada ZIP cifrada o >50 MB; requiere revisión")


def md_cell(value):
    import html
    return html.escape(str(value), quote=False).replace("\\", "\\\\").replace("|", "&#124;").replace("\r\n", "<br>").replace("\n", "<br>").replace("\r", "<br>")
