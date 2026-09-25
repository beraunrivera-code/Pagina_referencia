"""Rutas de la instalación actual, sin nombre de usuario/unidad fijos.

Configuración portable (relativa al directorio del programa) o ruta externa explícita.
No mueve datos ni copia credenciales. Una selección inválida falla sin sobrescribirla.
"""
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import uuid


def config_path(app_root):
    return Path(app_root).resolve().parent / "sistema_md.json"


def read_config(app_root):
    path = config_path(app_root)
    if not path.exists():
        return {"schema_version": 1}
    if path.stat().st_size > 16384:
        raise ValueError("Configuración de rutas demasiado grande")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("sistema_md.json inválido; se conserva sin reemplazar")
    return value


def resolve_setting(base, value):
    if not isinstance(value, str) or not value.strip() or '\0' in value:
        raise ValueError("Ruta configurada inválida")
    if os.name != "nt" and PureWindowsPath(value).is_absolute():
        raise ValueError("Ruta de Windows en otro sistema: vuelve a elegir la carpeta")
    path = Path(value).expanduser()
    return (path if path.is_absolute() else Path(base) / path).resolve()


def data_root(app_root):
    root = Path(app_root).resolve()
    config = read_config(root)
    value = os.environ.get("SISTEMA_MD_DATA") or config.get("data_dir")
    if value:
        selected = resolve_setting(root.parent, value)
        if not selected.is_dir():
            raise ValueError("La carpeta de datos elegida no está disponible. Reconecta la unidad o selecciona otra carpeta; no se creó una biblioteca vacía.")
        return selected
    return root / "datos"


def configure_data_root(app_root, path):
    app_root = Path(app_root).resolve()
    selected = Path(path).expanduser().resolve()
    if not selected.is_dir():
        raise ValueError("Selecciona una carpeta de datos existente")
    forced = os.environ.get("SISTEMA_MD_DATA")
    if forced and resolve_setting(app_root.parent, forced) != selected:
        raise ValueError("SISTEMA_MD_DATA fija otra carpeta. Ajusta o retira esa variable antes de guardar esta selección; no se cambió la configuración.")
    config = read_config(app_root)
    # Dentro del programa, la ruta viaja con él. Externa: no inventar otra ubicación.
    config["data_dir"] = (selected.relative_to(app_root.parent).as_posix()
                          if selected.is_relative_to(app_root.parent) else str(selected))
    destination = config_path(app_root)
    temporary = destination.with_name(".rutas-" + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, destination)
    return destination


def export_root(app_root, root):
    config = Path(root) / "exportacion.json"
    if config.exists():
        value = json.loads(config.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Configuración de exportación inválida")
        if value.get("carpeta_md"):
            target = resolve_setting(root, value["carpeta_md"])
            # No resucitar en silencio rutas absolutas de otra PC/unidad.
            if Path(value["carpeta_md"]).is_absolute() and not target.is_dir():
                raise ValueError("La carpeta MD corresponde a otra ubicación. Selecciónala de nuevo.")
            return target
    return Path(app_root).resolve().parent / "MD"


def configure_export_root(app_root, root, path):
    """Selección explícita; conserva rutas internas relativas y no mueve documentos."""
    selected = Path(path).expanduser().resolve()
    if not selected.is_dir():
        raise ValueError("Selecciona una carpeta MD existente")
    root = Path(root).resolve()
    config = root / "exportacion.json"
    previous = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
    if not isinstance(previous, dict):
        raise ValueError("Configuración de exportación inválida; se conserva sin reemplazar")
    install = Path(app_root).resolve().parent
    previous["carpeta_md"] = (Path(os.path.relpath(selected, root)).as_posix()
                              if selected.is_relative_to(install) and root.is_relative_to(install)
                              else str(selected))
    root.mkdir(parents=True, exist_ok=True)
    temporary = config.with_name(".exportacion-" + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(previous, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, config)
    return config


# Ubicaciones del paquete oficial ODA File Converter en Linux (.deb/.rpm y extracción manual).
ODA_LINUX_PATHS = ("/usr/bin/ODAFileConverter", "/opt/ODAFileConverter/ODAFileConverter",
                   "/usr/local/bin/ODAFileConverter")
ODA_LINUX_GLOBS = (("/usr/bin", "ODAFileConverter_*/ODAFileConverter"),
                   ("/opt", "ODAFileConverter*/ODAFileConverter"))


def find_oda():
    """Ejecutable de ODA: SISTEMA_MD_ODA, PATH y las rutas estándar de cada sistema."""
    explicit = os.environ.get("SISTEMA_MD_ODA")
    if explicit:
        return str(Path(explicit).expanduser().resolve())
    found = shutil.which("ODAFileConverter")
    if found:
        return found
    if os.name == "nt":
        program_files = os.environ.get("ProgramFiles")
        if program_files:
            candidates = sorted((Path(program_files) / "ODA").glob("ODAFileConverter*/ODAFileConverter.exe"))
            if candidates:
                return str(candidates[-1])
        return ""
    for candidate in ODA_LINUX_PATHS:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    for base, pattern in ODA_LINUX_GLOBS:
        candidates = sorted(path for path in Path(base).glob(pattern) if os.access(path, os.X_OK))
        if candidates:
            return str(candidates[-1])
    return ""
