"""Integración con el sistema operativo: Windows, escritorio Linux (X11/Wayland) o nube headless.

Un único punto para «abrir con la aplicación asociada», «mostrar en carpeta» y detectar si
hay pantalla. En un contenedor sin escritorio nada se lanza: el archivo ya está generado
dentro del paquete y solo se registra su ruta. Nunca se usa shell=True.
"""
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys

logger = logging.getLogger("sistema_md")


def is_windows():
    return os.name == "nt"


def has_display():
    """Hay escritorio gráfico al que enviar ventanas (X11 o Wayland)."""
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _desktop_opener():
    """Lanzador del escritorio POSIX: xdg-open (Linux) u open (macOS)."""
    return shutil.which("open" if sys.platform == "darwin" else "xdg-open")


def _detached(command):
    # Popen y no run(): algunos xdg-open esperan a que se cierre la aplicación y
    # congelarían la ventana Tk. Sesión propia para que no muera con el programa.
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)


def open_with_system(target):
    """Abre una ruta o URI con la aplicación predeterminada del usuario.

    Devuelve cómo se resolvió: ``asociacion`` (Windows), ``xdg-open``/``open`` (escritorio
    POSIX) o ``headless`` cuando no hay escritorio y solo se informa la ruta generada.
    """
    if is_windows():
        os.startfile(str(target))
        return "asociacion"
    opener = _desktop_opener() if has_display() or sys.platform == "darwin" else None
    if opener:
        _detached([opener, str(target)])
        return Path(opener).name
    logger.info("Modo headless activo: archivo generado en %s", target)
    return "headless"


def reveal_in_folder(path):
    """Muestra el archivo en su carpeta. En Windows lo deja seleccionado en el Explorador."""
    path = Path(path)
    if is_windows():
        subprocess.Popen('explorer /select,"%s"' % path)   # la coma va pegada a la ruta
        return "explorador"
    return open_with_system(path.parent)


def open_with_chooser(path):
    """Diálogo «Abrir con» de Windows; en POSIX no hay uno universal y se usa la asociación."""
    if is_windows():
        subprocess.Popen(["rundll32.exe", "shell32.dll,OpenAs_RunDLL", str(path)])
        return "abrir_con"
    return open_with_system(path)
