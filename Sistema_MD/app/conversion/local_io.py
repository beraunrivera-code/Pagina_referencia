"""Temporales por operación y límites explícitos; sin cambiar permisos del usuario."""
from contextlib import contextmanager
from pathlib import Path
import re
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


class ConversionError(ValueError):
    """Rechazo explicado de un archivo: dañado, vacío, ilegible o sin motor disponible.

    Subclase de ValueError: la CLI, la cola y la interfaz ya lo tratan como un rechazo
    controlado. Nunca debe escapar una excepción cruda de una librería de formato.
    """


# ------------------------------------------------------------ texto seguro para Markdown
# Controles invisibles que ningún Markdown debe transportar. VT/FF/NEL son saltos reales
# (Word, PDF): se convierten en salto de línea; el resto se elimina.
_CONTROL = re.compile("[\x00-\x08\x0e-\x1f\x7f-\x84\x86-\x9f\ufffe\uffff]")
_SALTOS = str.maketrans({"\x0b": "\n", "\x0c": "\n", "\x85": "\n"})
_SURROGADO = re.compile("[\ud800-\udfff]")
_ESCAPE_OOXML = re.compile(r"_x([0-9A-Fa-f]{4})_")
# Inicio de línea con significado Markdown: título, cita, tabla, valla de código, regla,
# lista o subrayado setext. Se neutraliza con barra invertida: el texto se conserva.
_INICIO_MD = re.compile(r"^(\s{0,3})(#|>|\||`{3,}|~{3,}|[-*_](?:\s*[-*_]){2,}\s*$|[-+*](?=\s|$)|\d{1,9}(?=[.)](?:\s|$))|=+\s*$)")


def clean_control(value) -> str:
    """Quita NUL y controles invisibles; sustituye surrogados sueltos (no codificables)."""
    text = str(value).translate(_SALTOS)
    return _SURROGADO.sub("\ufffd", _CONTROL.sub("", text))


def decode_ooxml_escapes(value):
    """SpreadsheetML guarda caracteres no XML como ``_xHHHH_`` (``_x000D_`` = retorno).

    openpyxl devuelve el escape literal; aquí se decodifica. ``_x005F_`` es el guion bajo
    escapado, por eso ``_x005F_x000D_`` vuelve a ser el texto literal «_x000D_».
    """
    if not isinstance(value, str) or "_x" not in value:
        return value
    return _ESCAPE_OOXML.sub(lambda match: chr(int(match.group(1), 16)), value)


def md_inline(value) -> str:
    """Texto dentro de una línea: sin HTML activo ni entidades que cambien de sentido."""
    text = clean_control(value)
    text = re.sub(r"&(?=#?[A-Za-z0-9]+;)", "&amp;", text)
    return re.sub(r"<(?=[A-Za-z/!?])", "&lt;", text)


def md_text(value) -> str:
    """Prosa de un documento como Markdown literal: cada línea conserva su texto, pero un
    «#», «|», «>», «```» o «1.» iniciales ya no crean títulos, tablas, citas o código."""
    def neutral(match):
        indent, marker = match.group(1), match.group(2)
        if marker[0].isdigit():               # «1.» → «1\.»: una barra antes de un dígito no escapa
            return indent + marker + "\\"
        return indent + "\\" + marker
    text = md_inline(value).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(_INICIO_MD.sub(neutral, line, count=1) for line in text.split("\n"))


def md_cell(value):
    import html
    text = clean_control(value)
    return (html.escape(text, quote=False).replace("\\", "\\\\").replace("|", "&#124;")
            .replace("\r\n", "<br>").replace("\n", "<br>").replace("\r", "<br>"))
