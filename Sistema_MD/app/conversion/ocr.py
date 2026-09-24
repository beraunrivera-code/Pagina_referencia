"""OCR local con Tesseract (CLI), sin red ni modelos remotos.

El resultado es un CANDIDATO: se publica marcado «OCR sin verificar» con motor, versión e
idiomas; nunca sustituye a una lectura verificada. Si Tesseract no está instalado, la
conversión sigue y la página queda ``[PENDIENTE: OCR …]`` en lugar de fallar o inventar.

Configuración: ``SISTEMA_MD_TESSERACT`` (ejecutable) y ``SISTEMA_MD_OCR_LANG``
(por defecto ``spa+eng``, filtrado a los idiomas realmente instalados).
"""
from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from .local_io import clean_control

OCR_TIMEOUT = 180


@lru_cache(maxsize=1)
def tesseract_path() -> str | None:
    explicit = os.environ.get("SISTEMA_MD_TESSERACT")
    if explicit:
        return explicit if Path(explicit).is_file() else None
    found = shutil.which("tesseract")
    if found:
        return found
    candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tesseract-OCR/tesseract.exe"
    return str(candidate) if candidate.is_file() else None


def _run(arguments, timeout=60):
    return subprocess.run(arguments, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, shell=False,
                          env={**os.environ, "OMP_THREAD_LIMIT": "1"},
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


@lru_cache(maxsize=1)
def engine() -> dict | None:
    """Motor disponible: ejecutable, versión e idiomas elegidos. None si no hay Tesseract."""
    executable = tesseract_path()
    if not executable:
        return None
    try:
        version = _run([executable, "--version"]).stdout.decode("utf-8", "replace").splitlines()[0].strip()
        listed = _run([executable, "--list-langs"]).stdout.decode("utf-8", "replace").splitlines()[1:]
    except (OSError, subprocess.SubprocessError, IndexError):
        return None
    available = {item.strip() for item in listed if item.strip() and item.strip() != "osd"}
    wanted = [lang for lang in os.environ.get("SISTEMA_MD_OCR_LANG", "spa+eng").split("+") if lang in available]
    if not wanted:
        wanted = sorted(available)[:1]
    if not wanted:
        return None
    return {"executable": executable, "version": version or "tesseract", "lang": "+".join(wanted)}


def describe() -> str:
    info = engine()
    return f"{info['version']} ({info['lang']})" if info else "no disponible"


def ocr_png(png: bytes) -> str | None:
    """Texto reconocido de una imagen PNG, o None si no hay motor / falló el reconocimiento."""
    info = engine()
    if info is None:
        return None
    with tempfile.TemporaryDirectory(prefix="sistema_md_ocr_") as folder:
        image = Path(folder) / "pagina.png"
        image.write_bytes(png)
        try:
            result = _run([info["executable"], str(image), "stdout", "-l", info["lang"], "--psm", "3"],
                          timeout=OCR_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            return None
    if result.returncode:
        return None
    return clean_control(result.stdout.decode("utf-8", "replace")).strip()
