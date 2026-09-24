"""Preferencias locales de conexión, nunca credenciales ni autorización de envío.

La sesión siempre inicia en modo Local; este archivo solo recuerda elecciones.
Un archivo corrupto se conserva para diagnóstico y se carga con valores seguros.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile

from .providers import PROVIDERS, profiles_for


SETTINGS_FILE = "conexiones.json"
MAX_BYTES = 32_768
MODEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}\Z")


def default_connection() -> dict:
    return {"model": "", "key_slot": 1, "cli_profile": "principal"}


def default_settings() -> dict:
    return {"version": 1, "selected_provider": "gemini-api", "connections": {}}


def validate_connection(provider: str, value: dict) -> dict:
    if provider not in PROVIDERS or not isinstance(value, dict):
        raise ValueError("Proveedor o selección de conexión inválido")
    if set(value) != {"model", "key_slot", "cli_profile"}:
        raise ValueError("Solo se permiten modelo, perfil API y perfil CLI")
    model, slot, profile = value["model"], value["key_slot"], value["cli_profile"]
    if not isinstance(model, str) or (model and not MODEL_PATTERN.fullmatch(model)):
        raise ValueError("Modelo inválido: usa el identificador exacto, sin espacios ni claves")
    if type(slot) is not int or not 1 <= slot <= 5:
        raise ValueError("El perfil de clave debe estar entre 1 y 5")
    if not isinstance(profile, str) or profile not in profiles_for(provider):
        raise ValueError("Perfil CLI incompatible con el proveedor")
    return {"model": model, "key_slot": slot, "cli_profile": profile}


def validate_settings(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != {"version", "selected_provider", "connections"}:
        raise ValueError("Formato de preferencias inválido; no se admiten secretos ni permisos")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("Versión de preferencias no compatible")
    if value["selected_provider"] not in PROVIDERS or not isinstance(value["connections"], dict):
        raise ValueError("Proveedor seleccionado o conexiones inválidas")
    connections = {provider: validate_connection(provider, connection)
                   for provider, connection in value["connections"].items()}
    return {"version": 1, "selected_provider": value["selected_provider"], "connections": connections}


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Campo duplicado en preferencias")
        value[key] = item
    return value


def load_settings(root: Path) -> tuple[dict, str]:
    path = Path(root) / SETTINGS_FILE
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("Preferencias demasiado grandes")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        return validate_settings(value), "Selección recuperada. Acceso y saldo no verificados; modo Local."
    except FileNotFoundError:
        return default_settings(), "Sin selección guardada. Configurar no autoriza envíos."
    except (OSError, ValueError, TypeError, RecursionError):
        return default_settings(), ("No se pudieron cargar las preferencias; se conservaron sin modificar. "
                                    "Selección segura y modo Local. Revisa y guarda para reemplazarlas.")


def save_settings(root: Path, value: dict) -> Path:
    normalized = validate_settings(value)
    payload = (json.dumps(normalized, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(payload) > MAX_BYTES:
        raise ValueError("Preferencias demasiado grandes")
    folder = Path(root)
    folder.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".conexiones-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, folder / SETTINGS_FILE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return folder / SETTINGS_FILE
