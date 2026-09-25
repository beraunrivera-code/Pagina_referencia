"""Office binario heredado (XLS/DOC/PPT) y XLSB: identificación y conversión controlada.

ADN FMT14: OLE/CFB no es OOXML descomprimible. El subtipo se identifica por los nombres
de stream del directorio CFB (``Workbook``, ``WordDocument``, ``PowerPoint Document``),
no por la extensión, que miente. La conversión usa LibreOffice sobre una COPIA, con perfil
aislado por llamada y sin interfaz; el original nunca se abre en escritura. El derivado
OOXML pasa después por el mismo motor nativo que un .xlsx/.docx/.pptx y el paquete declara
que la ruta no es neutra (revisiones, objetos y maquetación pueden diferir).
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import struct
import subprocess

from .local_io import ConversionError

OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
TARGETS = {"xls": "xlsx", "xlsb": "xlsx", "doc": "docx", "ppt": "pptx"}
ENGINE_KIND = {"xls": "xlsx", "xlsb": "xlsx", "doc": "docx", "ppt": "pptx"}
_END_OF_CHAIN = 0xFFFFFFFE
_MAX_SECTORS = 1 << 20


def ole_streams(path) -> set[str]:
    """Nombres del directorio CFB ([MS-CFB] 2.2 y 2.6). Lectura acotada; ante datos
    incoherentes devuelve lo leído hasta ahí, nunca entra en bucle."""
    with open(path, "rb") as stream:
        header = stream.read(512)
        if len(header) < 512 or not header.startswith(OLE_SIGNATURE):
            return set()
        sector_size = 1 << struct.unpack_from("<H", header, 0x1E)[0]
        if sector_size not in (512, 4096):
            return set()
        fat_count, first_dir = struct.unpack_from("<II", header, 0x2C)
        first_difat, difat_count = struct.unpack_from("<II", header, 0x44)
        difat = list(struct.unpack_from("<109I", header, 0x4C))

        def sector(number):
            stream.seek((number + 1) * sector_size)
            return stream.read(sector_size)

        following, seen = first_difat, 0
        while following < _END_OF_CHAIN and seen < min(difat_count, 4096):
            data = sector(following)
            if len(data) < sector_size:
                break
            values = struct.unpack(f"<{sector_size // 4}I", data)
            difat.extend(values[:-1])
            following, seen = values[-1], seen + 1
        fat = []
        for number in difat[:min(fat_count, 65536)]:
            if number >= _END_OF_CHAIN:
                continue
            data = sector(number)
            if len(data) == sector_size:
                fat.extend(struct.unpack(f"<{sector_size // 4}I", data))
        names, current, visited = set(), first_dir, set()
        while current < _END_OF_CHAIN and current not in visited and len(visited) < _MAX_SECTORS:
            visited.add(current)
            data = sector(current)
            for offset in range(0, len(data) - 127, 128):
                entry = data[offset:offset + 128]
                length = struct.unpack_from("<H", entry, 64)[0]
                if 2 <= length <= 64 and entry[66] in (1, 2, 5):
                    names.add(entry[:length - 2].decode("utf-16-le", "replace"))
            current = fat[current] if current < len(fat) else _END_OF_CHAIN
        return names


def ole_subtype(path) -> str:
    """xls / doc / ppt según el contenido; ``office-legacy`` si es otro OLE (msg, vsd…)."""
    try:
        names = ole_streams(path)
    except (OSError, struct.error):
        return "office-legacy"
    if names & {"Workbook", "Book"}:
        return "xls"
    if "WordDocument" in names:
        return "doc"
    if "PowerPoint Document" in names:
        return "ppt"
    return "office-legacy"


def soffice_path() -> str | None:
    explicit = os.environ.get("SISTEMA_MD_SOFFICE")
    if explicit:
        return explicit if Path(explicit).is_file() else None
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    for candidate in (Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "LibreOffice/program/soffice.exe",
                      Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),
                      Path("/opt/libreoffice/program/soffice")):
        if candidate.is_file():
            return str(candidate)
    return None


def soffice_version(executable) -> str:
    try:
        result = subprocess.run([executable, "--version"], stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=60, shell=False)
        return result.stdout.decode("utf-8", "replace").strip().splitlines()[0][:80]
    except (OSError, subprocess.SubprocessError, IndexError):
        return "LibreOffice (versión no informada)"


def convert_copy(source, kind, workdir, timeout=300) -> tuple[Path, str]:
    """Convierte una copia de ``source`` a OOXML en ``workdir``. Devuelve (ruta, versión)."""
    if kind not in TARGETS:
        raise ConversionError(f"«{kind}» no es un formato heredado convertible")
    executable = soffice_path()
    if not executable:
        raise ConversionError(f"El formato .{kind} requiere LibreOffice (soffice) para una conversión controlada. "
                              f"Instálalo o guarda el archivo como .{TARGETS[kind]}; no se adivina su contenido.")
    workdir = Path(workdir)
    inbox, outbox, profile = workdir / "entrada", workdir / "salida", workdir / "perfil"
    for folder in (inbox, outbox, profile):
        folder.mkdir(parents=True, exist_ok=True)
    copy = inbox / f"archivo.{kind}"          # la extensión real guía al filtro de importación
    shutil.copyfile(source, copy)
    command = [executable, "--headless", "--norestore", "--nolockcheck", "--nodefault", "--nologo",
               f"-env:UserInstallation={profile.resolve().as_uri()}",
               "--convert-to", TARGETS[kind], "--outdir", str(outbox), str(copy)]
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
                                shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise ConversionError(f"LibreOffice superó {timeout} s convirtiendo el .{kind}; no se publica nada.") from None
    except OSError as error:
        raise ConversionError(f"No se pudo ejecutar LibreOffice: {error}") from None
    converted = outbox / f"archivo.{TARGETS[kind]}"
    if not converted.is_file() or converted.stat().st_size == 0:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["sin detalle"]
        raise ConversionError(f"LibreOffice no pudo leer el .{kind} (archivo dañado o variante no soportada): "
                              f"{detail[0][:160]}")
    return converted, soffice_version(executable)
