"""Arranque local de Sistema MD. No instala, configura cuentas ni llama a IA."""
from __future__ import annotations

import importlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import sys
import traceback
from datetime import datetime, timezone
from contextlib import redirect_stderr, redirect_stdout


ROOT = Path(__file__).absolute().parent
APP = ROOT / "app"
RUNTIME = ROOT / ".runtime" / "tcl"
DEPENDENCIES = (
    ("fitz", "PyMuPDF"), ("ezdxf", "ezdxf"), ("docx", "python-docx"),
    ("pptx", "python-pptx"), ("openpyxl", "openpyxl"), ("PIL", "Pillow"),
)


def configure_process() -> None:
    """Rutas relativas al lanzador; los cambios desaparecen al cerrar este proceso."""
    sys.dont_write_bytecode = True
    if str(APP) not in sys.path:
        sys.path.insert(0, str(APP))
    os.chdir(APP)
    if Path(sys.prefix).name == ".venv-local":
        return  # Entorno preparado en esta PC: usar SU Tcl/Tk, no el del runtime anterior.
    for variable, directory in (("TCL_LIBRARY", "tcl8.6"), ("TK_LIBRARY", "tk8.6")):
        path = (RUNTIME / directory).as_posix()
        if not (RUNTIME / directory).is_dir():
            continue  # Una instalación normal usa Tcl/Tk de su propio Python.
        # Tcl puede perder el segmento OneDrive al normalizar una ruta cuya
        # enumeración rechaza el sandbox. La ruta extendida evita esa
        # normalización, sin ampliar permisos ni cambiar el entorno global.
        os.environ[variable] = "//?/" + path if os.name == "nt" and not path.startswith("//?/") else path


def safe_error(error: BaseException, phase: str) -> dict:
    """Sin mensajes arbitrarios, argumentos, entorno ni contenido documental."""
    return {
        "phase": phase,
        "exception_type": type(error).__name__,
        "frames": [dict(file=Path(frame.filename).name, function=frame.name, line=frame.lineno)
                   for frame in traceback.extract_tb(error.__traceback__)[-8:]],
        "action": "Ejecuta SISTEMA_MD.bat --check y revisa INICIO_RAPIDO.md.",
    }


def write_failure(error: BaseException, phase: str) -> Path | None:
    log = ROOT / "logs" / "arranque.jsonl"
    record = {"time_utc": datetime.now(timezone.utc).isoformat(), **safe_error(error, phase)}
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        return None
    return log


def _collect_checks() -> dict:
    result = {
        "ok": True, "mode": "check_read_only",
        "python": {"version": sys.version.split()[0], "executable": sys.executable},
        "application": str(APP), "checks": [], "external_calls": 0,
        "notice": "Comprueba arranque y dependencias; no certifica extracción, portabilidad ni acceso API.",
    }
    for module, distribution in DEPENDENCIES:
        try:
            importlib.import_module(module)
            result["checks"].append({"name": distribution, "ok": True,
                                     "version": importlib.metadata.version(distribution)})
        except Exception as error:
            result["ok"] = False
            result["checks"].append({"name": distribution, "ok": False,
                                     **safe_error(error, "import")})
    window = None
    try:
        import tkinter as tk
        window = tk.Tk()
        window.withdraw()
        window.update_idletasks()
        result["checks"].append({"name": "Tk", "ok": True,
                                 "version": str(window.tk.call("info", "patchlevel"))})
    except Exception as error:
        result["ok"] = False
        result["checks"].append({"name": "Tk", "ok": False, **safe_error(error, "tk_init")})
    finally:
        if window is not None:
            window.destroy()
    try:
        importlib.import_module("conversion.app")
        importlib.import_module("conversion.__main__")
        result["checks"].append({"name": "Sistema MD GUI/CLI", "ok": True})
    except Exception as error:
        result["ok"] = False
        result["checks"].append({"name": "Sistema MD GUI/CLI", "ok": False,
                                 **safe_error(error, "application_import")})
    return result


def check() -> int:
    """Importaciones reales con efectos Python de escritura/red bloqueados durante el control.

    Algunas dependencias intentan crear cachés al importarse. Esto no es un
    sandbox del sistema operativo: delimita el control conocido, no código hostil.
    """
    guard = {"active": True, "write_attempts": 0, "network_attempts": 0}
    mutating = {"os.mkdir", "os.remove", "os.rename", "os.rmdir", "os.symlink", "os.link",
                "os.chmod", "os.utime", "os.truncate", "shutil.copyfile", "winreg.SetValue"}

    def audit(event, arguments):
        if not guard["active"]:
            return
        write = event in mutating
        if event == "open":
            mode = arguments[1] if len(arguments) > 1 else None
            flags = arguments[2] if len(arguments) > 2 else 0
            write = (isinstance(mode, str) and any(value in mode for value in "wax+")) or (
                isinstance(flags, int) and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)))
        if write:
            guard["write_attempts"] += 1
            raise PermissionError("Control de arranque de solo lectura")
        if event in {"socket.connect", "socket.getaddrinfo", "subprocess.Popen", "os.system"}:
            guard["network_attempts"] += 1
            raise PermissionError("El control no permite red ni subprocesos")

    sys.addaudithook(audit)
    captured = io.StringIO()
    try:
        with redirect_stdout(captured), redirect_stderr(captured):
            result = _collect_checks()
    finally:
        guard["active"] = False
    result["blocked_write_attempts"] = guard["write_attempts"]
    result["blocked_network_or_process_attempts"] = guard["network_attempts"]
    result["dependency_notices_suppressed"] = bool(captured.getvalue())
    if guard["network_attempts"]:
        result["ok"] = False
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def show_failure(error: BaseException, *, gui: bool) -> None:
    log = write_failure(error, "gui_start" if gui else "cli_start")
    message = ("Sistema MD no pudo iniciar.\n\n"
               f"Tipo de error: {type(error).__name__}\n"
               "Ejecuta SISTEMA_MD.bat --check para revisar el entorno.\n"
               + (f"Registro seguro: {log}" if log else "No se pudo guardar el registro local."))
    if gui and os.name == "nt":
        # Sirve incluso si Tcl/Tk no arranca.
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "Sistema MD · error de inicio", 0x10)
            return
        except Exception:
            pass
    if sys.stderr is not None:
        print(message, file=sys.stderr)


def main(arguments: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    argv = list(sys.argv[1:] if arguments is None else arguments)
    if len(argv) == 2 and argv[0] in {"--datos", "--exportacion"}:
        argv[1] = str(Path(argv[1]).expanduser().absolute())
    # Resolver arrastres ANTES de cambiar cwd conserva las rutas relativas del usuario.
    dragged = bool(argv) and all(not value.startswith("-") and Path(value).exists() for value in argv)
    if dragged:
        argv = [str(Path(value).absolute()) for value in argv]
    gui = not argv or argv == ["--gui"]
    checking = argv == ["--check"]
    try:
        configure_process()
        if argv[:1] == ["--datos"]:
            if len(argv) != 2:
                raise ValueError("Usa --datos seguido de una carpeta existente entre comillas")
            from conversion.runtime_paths import configure_data_root
            configure_data_root(APP, Path(argv[1]))
            print("Carpeta elegida para la próxima apertura. No se movieron documentos.")
            return 0
        if argv[:1] == ["--exportacion"]:
            if len(argv) != 2:
                raise ValueError("Usa --exportacion seguido de una carpeta existente entre comillas")
            from conversion.runtime_paths import configure_export_root, data_root
            configure_export_root(APP, data_root(APP), Path(argv[1]))
            print("Carpeta MD elegida. No se movieron documentos ni se reemplazó contenido.")
            return 0
        if checking:
            return check()
        # Arrastrar solo encola: no convierte ni autoriza un envío.
        if dragged:
            from conversion.__main__ import main as cli_main
            for value in argv:
                code = cli_main(["encolar", value])
                if code:
                    return code
            gui = True
        if gui:
            from conversion.app import ConversionApp
            app = ConversionApp()
            app.mainloop()
            return 0
        from conversion.__main__ import main as cli_main
        return cli_main(argv[1:] if argv[:1] == ["--cli"] else argv)
    except Exception as error:
        if checking:
            print(json.dumps({"ok": False, "mode": "check_read_only",
                              **safe_error(error, "configure_process")}, ensure_ascii=False, indent=2))
        else:
            show_failure(error, gui=gui)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
