"""Puente opt-in para sesiones Antigravity fab1..fab5 ya autorizadas en Windows.

No crea usuarios, guarda contraseñas, extrae OAuth ni reutiliza scripts de Claude.
El llamador debe registrar intención y obtener consentimiento ANTES de invocarlo.
La ACL protege el paquete; NO convierte al usuario fab ni al CLI en un sandbox.
Un resultado incierto se conserva y nunca se reenvía automáticamente.

Plataforma: Authenticode, ACL NTFS, runas y el worker PowerShell son garantías de Windows.
En Linux/contenedor NO se omiten ni se simulan: todas las llamadas nativas pasan por
``_require_windows``/``_windows_tool`` y fallan cerradas con ``fab_windows`` antes de
tocar documentos, en lugar de un FileNotFoundError por powershell.exe o runas.exe.
"""
from contextlib import ExitStack, contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time
import uuid

from .documents import load_json
from .provider_errors import ProviderFault


FAB_ROOT = Path(r"C:\FABRICA")
FAB_BASE = FAB_ROOT / "sistema_md"
AGY_BINARY = FAB_ROOT / "bin" / "agy.exe"
WINDOWS_ROOT = Path(os.environ.get('SystemRoot', 'C:/Windows'))
POWERSHELL = str(WINDOWS_ROOT / 'System32/WindowsPowerShell/v1.0/powershell.exe')
RUNAS = str(WINDOWS_ROOT / 'System32/runas.exe')
HELPER = Path(__file__).with_name("fab_worker.ps1")
PROFILES = frozenset(f"fab{i}" for i in range(1, 6))
# 2026-09-21: raíz verificó Authenticode Valid/Google LLC para este hash.
# Identidad del binario, NO prueba de sesión, disponibilidad de modelos ni control IA.
APPROVED_AGY_SHA256 = "f61a443428f15d545a69e57767f5b45fb35f2e96fcc2654cc6b692dae1cbd4e5"
PACKAGE_FILES = ("solicitud.md", "respuesta.schema.json", "pagina.png")
MAX_PACKAGE = 32 * 1024 * 1024
MAX_OUTPUT = 4_000_000
MAX_STDERR = 1_000_000
MAX_RECEIPT = 8192
_SID = re.compile(r"S-1-5-(?:\d+-)*\d+")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SAFE_ENV = frozenset({"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "SYSTEMDRIVE", "TEMP", "TMP",
                      "USERPROFILE", "APPDATA", "LOCALAPPDATA", "USERNAME", "USERDOMAIN",
                      "HOMEDRIVE", "HOMEPATH", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
                      "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)"})


class FabBridgeFault(ProviderFault):
    def __init__(self, code, message):
        super().__init__(code)
        self.remedy = message
        self.args = (message,)


def _fault(code, message):
    raise FabBridgeFault(code, message)


def _require_windows():
    """Única puerta de plataforma del puente; se evalúa antes de cualquier E/S."""
    if os.name != "nt":
        _fault("fab_windows", "El puente fab requiere Windows (runas, Authenticode y ACL NTFS). "
                              "En Linux usa una conexión API o un CLI de sesión propio.")


def _windows_tool(command, *, what):
    """Ejecuta powershell.exe/runas.exe sin shell; fuera de Windows falla cerrado sin lanzar."""
    _require_windows()
    try:
        return subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=30, shell=False, env=_environment(),
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        if what == "acl":
            _fault("fab_acl", "No se pudo comprobar la ACL. No se copiaron documentos ni se inició IA.")
        _fault("fab_dispatch_unknown", "Despacho fab incierto. Conservado el intento; no repetir ni borrar trabajos en vuelo.")


def _environment():
    return {key: value for key, value in os.environ.items() if key.upper() in _SAFE_ENV}


def _regular(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        _fault("fab_path", "El puente rechaza enlaces y archivos no regulares.")
    return info


def _bounded(path, limit):
    before = _regular(path)
    if before.st_size > limit:
        _fault("response_size", "Archivo excede el límite local. No se reenvía.")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    after = _regular(path)
    if len(raw) > limit or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        _fault("fab_changed", "Un archivo cambió o excedió el límite durante la lectura.")
    return raw


def _json_file(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=True, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def _locked_path(path, *, directory=True):
    """Impide cambiar/borrar los directorios y el ejecutable mientras se despacha."""
    _require_windows()
    info = path.lstat()
    if getattr(info, "st_file_attributes", 0) & 0x400 or directory != stat.S_ISDIR(info.st_mode):
        _fault("fab_path", "Ruta insegura: enlace/reparse point o tipo incorrecto.")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                   ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.GetFinalPathNameByHandleW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
    kernel.GetFinalPathNameByHandleW.restype = ctypes.c_uint32
    # Directorios sin FILE_SHARE_DELETE; binario sin FILE_SHARE_WRITE/DELETE.
    handle = kernel.CreateFileW(str(path), 0x80, 3 if directory else 1, None, 3,
                                0x02000000 if directory else 0, None)
    if handle == ctypes.c_void_p(-1).value:
        _fault("fab_path_lock", "No se pudo inmovilizar la ruta del puente; envío bloqueado.")
    try:
        final = ctypes.create_unicode_buffer(32768)
        count = kernel.GetFinalPathNameByHandleW(handle, final, len(final), 0)
        actual = final.value.removeprefix("\\\\?\\")
        if not count or count >= len(final) or os.path.normcase(actual) != os.path.normcase(str(path.absolute())):
            _fault("fab_path", "La ruta cambió o fue redirigida; envío bloqueado.")
        yield
    finally:
        kernel.CloseHandle(handle)


def _powershell(script):
    # El script se construye exclusivamente con constantes, UUID y perfiles allowlist.
    # No contiene documentos, secretos ni valores remotos.
    import base64
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = _windows_tool([POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], what="acl")
    if result.returncode or len(result.stdout) > MAX_RECEIPT:
        _fault("fab_acl", "Windows rechazó la preparación segura; envío bloqueado.")
    try:
        return load_json(result.stdout.decode("utf-8-sig"))
    except (ValueError, UnicodeError):
        _fault("fab_acl", "Windows no devolvió evidencia válida de permisos; envío bloqueado.")


def _ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def verified_binary(executable=None, expected_hash=None):
    """Ruta elegida, hash fijado y Authenticode Google; no busca ni ejecuta CLIs."""
    path = Path(executable) if executable is not None else AGY_BINARY
    if not path.is_absolute():
        _fault("fab_binary_path", "Selecciona una ruta absoluta del ejecutable Antigravity.")
    _regular(path)
    approved = expected_hash or (APPROVED_AGY_SHA256 if path == AGY_BINARY else "")
    if not isinstance(approved, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", approved):
        _fault("fab_binary_unverified", "Esta instalación necesita un hash autorizado explícito; no se ejecutará por descubrimiento.")
    approved = approved.lower()
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != approved:
        _fault("fab_binary_changed", "El ejecutable no coincide con el hash autorizado; envío bloqueado.")
    signature = _powershell("$ErrorActionPreference='Stop'; "
                            "$s=Get-AuthenticodeSignature -LiteralPath " + _ps_literal(path) + "; "
                            "@{status=$s.Status.ToString();subject=$s.SignerCertificate.Subject}|ConvertTo-Json -Compress")
    if (not isinstance(signature, dict) or signature.get("status") != "Valid"
            or not re.search(r"(?:^|,\s*)(?:CN|O)=Google LLC(?:,|$)", str(signature.get("subject", "")))):
        _fault("fab_binary_signature", "Antigravity requiere una firma Authenticode válida de Google LLC.")
    return path, approved


def _secure_folder(folder, profile):
    # Ruta elegida por el operador, nunca por el documento; último tramo UUID propio.
    if not folder.is_absolute() or not re.fullmatch(r"[a-f0-9]{32}", folder.name) or profile not in PROFILES:
        _fault("fab_path", "Destino fab inválido.")
    script = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding $false
$folder=__FOLDER__
$current=[Security.Principal.WindowsIdentity]::GetCurrent().User
$account=New-Object Security.Principal.NTAccount ([Environment]::MachineName + '\__PROFILE__')
$fab=$account.Translate([Security.Principal.SecurityIdentifier])
if($current.Value -eq $fab.Value){throw 'same_identity'}
$sids=@($current.Value,$fab.Value,'S-1-5-18','S-1-5-32-544')
$acl=New-Object Security.AccessControl.DirectorySecurity
$acl.SetOwner($current)
$acl.SetAccessRuleProtection($true,$false)
foreach($sid in $sids){
  $identity=New-Object Security.Principal.SecurityIdentifier $sid
  $rule=New-Object Security.AccessControl.FileSystemAccessRule($identity,'FullControl','ContainerInherit,ObjectInherit','None','Allow')
  $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $folder -AclObject $acl
$actual=Get-Acl -LiteralPath $folder
$rows=@($actual.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]) | ForEach-Object {
  @{sid=$_.IdentityReference.Value;rights=[int]$_.FileSystemRights;inherited=$_.IsInherited;
    type=$_.AccessControlType.ToString();inheritance=[int]$_.InheritanceFlags;propagation=[int]$_.PropagationFlags}
})
@{protected=$actual.AreAccessRulesProtected;owner=$actual.GetOwner([Security.Principal.SecurityIdentifier]).Value;
  current=$current.Value;fab=$fab.Value;rules=$rows} | ConvertTo-Json -Depth 4 -Compress
""".replace("__FOLDER__", _ps_literal(folder)).replace("__PROFILE__", profile)
    evidence = _powershell(script)
    if not isinstance(evidence, dict):
        _fault("fab_acl", "ACL sin evidencia estructurada; envío bloqueado.")
    current, fab = evidence.get("current"), evidence.get("fab")
    if not isinstance(current, str) or not isinstance(fab, str) or not _SID.fullmatch(current) or not _SID.fullmatch(fab):
        _fault("fab_acl", "Windows no resolvió las identidades autorizadas.")
    allowed = {current, fab, "S-1-5-18", "S-1-5-32-544"}
    rules = evidence.get("rules")
    good = (len(allowed) == 4 and evidence.get("protected") is True and evidence.get("owner") == current
            and isinstance(rules, list) and len(rules) == 4)
    if good:
        good = {r.get("sid") for r in rules if isinstance(r, dict)} == allowed and all(
            isinstance(r, dict) and r.get("rights") == 2032127 and r.get("inherited") is False
            and r.get("type") == "Allow" and r.get("inheritance") == 3 and r.get("propagation") == 0
            for r in rules)
    if not good:
        _fault("fab_acl", "La ACL no es privada y exacta. No se copiaron documentos ni se inició IA.")
    return evidence


def _launch(stage, profile):
    helper = stage / "worker.ps1"
    # Quoting Windows de argumentos; nunca shell ni interpolación de documentos.
    command = subprocess.list2cmdline([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                     "-WindowStyle", "Hidden", "-File", str(helper), "-Folder", str(stage)])
    result = _windows_tool([RUNAS, "/profile", "/savecred", f"/user:.\\{profile}", command], what="dispatch")
    if result.returncode:
        _fault("fab_dispatch_unknown", "runas no confirmó el despacho. Revisa la conexión fab; no se reintenta automáticamente.")


def _wait_result(stage, nonce, fab_sid, timeout):
    deadline = time.monotonic() + timeout + 15
    receipt_path = stage / "fin.json"
    while not receipt_path.exists():
        if time.monotonic() >= deadline:
            _fault("cli_timeout", "Tiempo agotado; consumo desconocido. El trabajo se conserva y no se reenvía.")
        time.sleep(0.1)
    try:
        receipt = load_json(_bounded(receipt_path, MAX_RECEIPT))
    except (ValueError, UnicodeError):
        _fault("fab_receipt", "Finalización ilegible; consumo desconocido. No reenviar.")
    if (not isinstance(receipt, dict) or receipt.get("nonce") != nonce or receipt.get("sid") != fab_sid
            or receipt.get("state") != "finished" or type(receipt.get("exit_code")) is not int):
        _fault("fab_receipt", "Finalización no verificable; consumo desconocido. No reenviar.")
    if receipt["exit_code"] != 0:
        _fault("cli_exit", "Antigravity terminó con error. El intento queda conservado; no se reenvía.")
    raw = _bounded(stage / "stdout.json", MAX_OUTPUT)
    if receipt.get("stdout_sha256") != hashlib.sha256(raw).hexdigest():
        _fault("fab_receipt", "La salida no coincide con su recibo; consumo desconocido. No reenviar.")
    try:
        payload = load_json(raw)
    except (ValueError, UnicodeError):
        _fault("fab_response", "Antigravity no devolvió JSON válido. No se reenvía.")
    if not isinstance(payload, dict):
        _fault("fab_response", "La salida Antigravity no es un objeto JSON. No se reenvía.")
    return payload, raw


def run_fab(profile, model, folder, timeout, *, executable=None, expected_hash=None, shared_base=None):
    """Un único despacho, sin login interactivo, rotación, retry ni borrado de evidencia."""
    if profile not in PROFILES or not isinstance(model, str) or not _MODEL.fullmatch(model):
        _fault("fab_selection", "Elige un perfil fab1–fab5 y un identificador de modelo explícito válido.")
    if type(timeout) is not int or not 5 <= timeout <= 1200:
        _fault("fab_timeout", "El tiempo del puente debe ser un entero entre 5 y 1200 segundos.")
    _require_windows()
    binary_path = Path(executable) if executable is not None else AGY_BINARY
    base = Path(shared_base) if shared_base is not None else FAB_BASE
    if (not base.is_absolute() or not binary_path.is_absolute() or str(base).startswith("\\\\")
            or any(ord(char) < 32 for char in str(base) + str(binary_path))):
        _fault("fab_path", "El puente exige rutas locales absolutas elegidas por el operador.")
    if not base.parent.is_dir():
        _fault("fab_path", "No existe la ubicación compartida autorizada. El puente no crea una instalación global.")
    folder = Path(folder).resolve(strict=True)
    if not folder.is_dir() or any((folder / name).exists() for name in ("fab-bridge.json", "stdout.json", "stderr.txt")):
        _fault("fab_prior_attempt", "Ya existe evidencia de un intento fab. Revisa el resultado; no se duplica.")
    # Sólo inspección de tamaño/tipo antes de los permisos, sin leer contenido sensible.
    if sum(_regular(folder / name).st_size for name in PACKAGE_FILES) > MAX_PACKAGE:
        _fault("fab_package_size", "El paquete supera 32 MiB; no se inició IA.")
    with ExitStack() as locks:
        # Inmoviliza ancestros para evitar redirección por renombre/reparse en pleno despacho.
        for ancestor in reversed(base.parents):
            locks.enter_context(_locked_path(ancestor))
        base.mkdir(exist_ok=True)
        locks.enter_context(_locked_path(base))
        locks.enter_context(_locked_path(binary_path, directory=False))
        binary_path, binary_hash = verified_binary(binary_path, expected_hash)
        nonce = uuid.uuid4().hex
        stage = base / nonce
        stage.mkdir(exist_ok=False)
        locks.enter_context(_locked_path(stage))
        acl = _secure_folder(stage, profile)
        # x + fsync: reserva durable local ANTES de poner datos/despachar. Nunca se elimina.
        _json_file(folder / "fab-bridge.json", {"schema": 1, "nonce": nonce, "profile": profile,
                   "model": model, "stage": str(stage), "binary_sha256": binary_hash, "state": "reserved"})
        # El ejecutable de C:\\FABRICA es compartido: el worker usa una copia privada,
        # contrastada y mantenida sin permisos de cambio mientras el puente espera.
        with binary_path.open("rb") as source, (stage / "agy.exe").open("xb") as destination:
            shutil.copyfileobj(source, destination, 1024 * 1024)
        locks.enter_context(_locked_path(stage / "agy.exe", directory=False))
        with (stage / "agy.exe").open("rb") as binary:
            if hashlib.file_digest(binary, "sha256").hexdigest() != binary_hash:
                _fault("fab_binary_changed", "La copia privada del binario no coincide; envío bloqueado.")
        package_hashes = {}
        for name in PACKAGE_FILES:
            raw = _bounded(folder / name, MAX_PACKAGE)
            with (stage / name).open("xb") as destination:
                destination.write(raw)
            package_hashes[name] = hashlib.sha256(raw).hexdigest()
        helper = _bounded(HELPER, 128_000)
        with (stage / "worker.ps1").open("xb") as destination:
            destination.write(helper)
        _json_file(stage / "control.json", {"nonce": nonce, "model": model, "timeout": timeout,
                   "fab_sid": acl["fab"], "allowed_sids": [acl["current"], acl["fab"], "S-1-5-18", "S-1-5-32-544"],
                   "binary_sha256": binary_hash, "package_sha256": package_hashes})
        _launch(stage, profile)
        payload, raw = _wait_result(stage, nonce, acl["fab"], timeout)
        reported = payload.get("model")
        if isinstance(reported, str) and reported != model:
            _fault("fab_model_mismatch", "El CLI informó un modelo distinto del elegido. Resultado retenido; no reenviar.")
        with (folder / "stdout.json").open("xb") as output:
            output.write(raw)
        if (stage / "stderr.txt").exists():
            stderr = _bounded(stage / "stderr.txt", MAX_STDERR)
            with (folder / "stderr.txt").open("xb") as output:
                output.write(stderr)
        return payload
