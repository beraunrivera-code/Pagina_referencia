"""Claves por usuario en Windows Credential Manager; nunca en archivos del proyecto.

Fuera de Windows (Linux/contenedor) la única fuente es la variable de entorno indicada:
el almacén nativo no existe y no se sustituye por un archivo en disco.
"""
import ctypes
from ctypes import wintypes
import os

ENV_NAMES = {
    "gemini-api": "GEMINI_API_KEY",
    "deepseek-api": "DEEPSEEK_API_KEY",
    "openai-api": "OPENAI_API_KEY",
    "anthropic-api": "ANTHROPIC_API_KEY",
    "qwen-api": "DASHSCOPE_API_KEY",
}
KEY_SLOTS = (1, 2, 3, 4, 5)


class Credential(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR)]


def _last_error():
    """``ctypes.get_last_error`` solo existe en Windows; aislado para poder simularlo."""
    return ctypes.get_last_error()


def _library():
    lib = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    lib.CredWriteW.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
    lib.CredWriteW.restype = wintypes.BOOL
    lib.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            ctypes.POINTER(ctypes.POINTER(Credential))]
    lib.CredReadW.restype = wintypes.BOOL
    lib.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    lib.CredDeleteW.restype = wintypes.BOOL
    lib.CredFree.argtypes = [ctypes.c_void_p]
    lib.CredFree.restype = None
    return lib


def key_location(provider: str, slot: int = 1) -> tuple[str, str]:
    if provider not in ENV_NAMES or type(slot) is not int or slot not in KEY_SLOTS:
        raise ValueError("Proveedor API o perfil de clave inválido (1–5)")
    # La cuenta 1 conserva el target y la variable heredados. No migrar secretos.
    suffix = "" if slot == 1 else f"_{slot}"
    target = "SistemaMD/" + provider + ("" if slot == 1 else f"/{slot}")
    return ENV_NAMES[provider] + suffix, target


def get_key(provider: str, slot: int = 1) -> str | None:
    name, target = key_location(provider, slot)
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip()
    if os.name != "nt":
        return None
    lib = _library()
    pointer = ctypes.POINTER(Credential)()
    if not lib.CredReadW(target, 1, 0, ctypes.byref(pointer)):
        if _last_error() == 1168:
            return None
        raise OSError("Windows no permite leer la credencial de SistemaMD")
    try:
        entry = pointer.contents
        if not 0 < entry.CredentialBlobSize <= 5120 or not entry.CredentialBlob:
            raise OSError("La credencial guardada de SistemaMD no tiene formato válido")
        try:
            return ctypes.string_at(entry.CredentialBlob, entry.CredentialBlobSize).decode("utf-8")
        except UnicodeError:
            raise OSError("La credencial guardada de SistemaMD no tiene formato válido") from None
    finally:
        lib.CredFree(pointer)


def save_key(provider: str, value: str, slot: int = 1) -> None:
    if (provider not in ENV_NAMES or not isinstance(value, str) or not value.strip()
            or len(value) > 2000
            or any(ord(char) < 33 or ord(char) > 126 for char in value.strip())):
        raise ValueError("Proveedor API o clave inválidos")
    _, target = key_location(provider, slot)
    if os.name != "nt":
        raise OSError("Fuera de Windows usa la variable de entorno indicada")
    data = value.strip().encode("utf-8")
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    entry = Credential()
    entry.Type = 1
    entry.TargetName = target
    entry.CredentialBlobSize = len(data)
    entry.CredentialBlob = buffer
    entry.Persist = 2  # Máquina local, usuario actual.
    entry.UserName = "SistemaMD"
    try:
        if not _library().CredWriteW(ctypes.byref(entry), 0):
            raise OSError("Windows no pudo guardar la credencial de SistemaMD")
    finally:
        # Limpia la copia nativa temporal, no promete borrar los strings de Python.
        ctypes.memset(buffer, 0, len(data))


def delete_key(provider: str, slot: int = 1) -> bool:
    """Borra SOLO este target del almacén Windows; no toca variables de entorno.

    Retorna False si no existía. Una variable de entorno puede seguir prevaleciendo:
    borrar el almacén no revoca la clave en el proveedor ni modifica el entorno.
    """
    _, target = key_location(provider, slot)
    if os.name != "nt":
        raise OSError("Fuera de Windows gestiona la variable de entorno indicada")
    if _library().CredDeleteW(target, 1, 0):
        return True
    if _last_error() == 1168:
        return False
    raise OSError("Windows no pudo borrar la credencial seleccionada de SistemaMD")
