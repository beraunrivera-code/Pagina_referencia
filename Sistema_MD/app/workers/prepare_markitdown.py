"""Preparación explícita en otra PC; nunca copiar una .venv de la PC anterior.

Sin --install solo muestra el plan. Con --install crea el worker nuevo y usa PyPI.
Esta es una preparación técnica, no un instalador comercial firmado/autónomo.
El lock actual corresponde únicamente a Windows x64, CPython 3.12.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "workers" / "markitdown" / ".venv"
LOCK = Path(__file__).with_name("requirements-markitdown-win-py312.lock")


def _marker_path():
    return TARGET.parent / "preparacion.json"


def _marker_value(python, state):
    return {"version": 1, "owner": "sistema-md-markitdown", "root": str(ROOT.resolve()),
        "target": str(TARGET.resolve()), "base_python": str(Path(python).resolve()),
        "lock_sha256": hashlib.sha256(LOCK.read_bytes()).hexdigest(), "state": state}


def _write_marker(value):
    path = _marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".preparacion-" + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _resume_state(python, resume):
    marker = _marker_path()
    if not resume:
        if TARGET.exists():
            raise ValueError("Existe worker; no se sobrescribe")
        if marker.exists():
            if not marker.is_file() or marker.stat().st_size > 16000:
                raise ValueError("Marcador inválido; se conserva")
            previous = json.loads(marker.read_text(encoding="utf-8"))
            if (not isinstance(previous, dict) or previous.get("owner") != "sistema-md-markitdown"
                    or previous.get("version") != 1 or previous.get("state") != "ready"):
                raise ValueError("Preparación anterior incompleta/ajena; revisar antes de instalar")
            return "new_without_runtime"
        return "new"
    if not marker.is_file() or marker.stat().st_size > 16000:
        raise ValueError("No hay marcador válido de preparación propia")
    saved = json.loads(marker.read_text(encoding="utf-8"))
    if not isinstance(saved, dict) or saved.get("state") not in {"creating", "installing"}:
        raise ValueError("Solo puede reanudarse una preparación propia incompleta; ready no se modifica")
    if saved != _marker_value(python, saved["state"]):
        raise ValueError("La raíz, Python base o lock cambiaron; no se reanuda otro entorno")
    return saved["state"]


def command_plan(python):
    python = Path(python).resolve(strict=True)
    if not python.is_file() or python.name.lower() not in {"python.exe", "python3.exe"}:
        raise ValueError("Selecciona python.exe local de CPython 3.12 x64")
    if str(python).startswith(("\\\\", "//")):
        raise ValueError("No se ejecutan intérpretes desde una ruta de red")
    if not TARGET.resolve().is_relative_to(ROOT.resolve()) or TARGET.name != ".venv":
        raise ValueError("Destino del worker fuera de la instalación")
    executable = TARGET / "Scripts/python.exe"
    return [
        [str(python), "-I", "-m", "venv", str(TARGET)],
        [str(executable), "-I", "-m", "pip", "--isolated", "install", "--index-url", "https://pypi.org/simple",
         "--only-binary=:all:", "--no-deps", "--disable-pip-version-check", "--retries", "0", "--timeout", "30",
         "-r", str(LOCK)],
        [str(executable), "-I", "-m", "pip", "--isolated", "check"],
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, help='Ruta al Python base local; ej. "C:\\Python312\\python.exe"')
    parser.add_argument("--install", action="store_true", help="Autoriza crear la venv nueva y descargar ruedas de PyPI")
    parser.add_argument("--resume", action="store_true", help="Reanuda solo una instalación incompleta propia, con el mismo Python/raíz/lock")
    args = parser.parse_args(argv)
    try:
        commands = command_plan(args.python)
        if args.resume and not args.install:
            raise ValueError("--resume requiere --install explícito")
        if not args.install:
            print(json.dumps({"action": "plan_only", "target": str(TARGET), "commands": commands,
                "notice": "Requiere Windows x64 / Python 3.12 local. No copia .venv ni instala sin --install."}, ensure_ascii=False, indent=2))
            return 0
        if os.name != "nt":
            raise ValueError("El lock validado es para Windows x64; otra plataforma requiere su propio control")
        state = _resume_state(args.python, args.resume)
        check = subprocess.run([str(Path(args.python).resolve()), "-I", "-c",
            "import json,sys,struct;print(json.dumps([sys.version_info[:2],struct.calcsize('P')*8]))"],
            capture_output=True, text=True, timeout=20, check=True)
        if json.loads(check.stdout) != [[3, 12], 64]:
            raise ValueError("Este lock requiere Python 3.12 de 64 bits")
        if state == "new_without_runtime":
            # Al copiar el programa sin su .venv puede viajar este recibo.
            # Preservar procedencia y preparar un runtime NUEVO, no reanudar el antiguo.
            previous = _marker_path()
            archive = previous.with_name("preparacion.anterior-" + uuid.uuid4().hex + ".json")
            with archive.open("xb") as stream:
                stream.write(previous.read_bytes())
        if state in {"new", "creating", "new_without_runtime"}:
            _write_marker(_marker_value(args.python, "creating"))
            subprocess.run(commands[0], check=True, timeout=900,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            _write_marker(_marker_value(args.python, "installing"))
        elif not (TARGET / "Scripts/python.exe").is_file():
            raise ValueError("Worker parcial sin Python; no se continúa como si estuviera creado")
        for command in commands[1:]:
            subprocess.run(command, check=True, timeout=900,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _write_marker(_marker_value(args.python, "ready"))
        print("Worker preparado. Ejecuta el control sintético local antes de usar documentos; Docling no se instala.")
        return 0
    except (ValueError, OSError, subprocess.SubprocessError):
        print("No se preparó el worker. Comprueba Python 3.12 x64, destino y acceso a PyPI. Si quedó parcial con marcador propio, repite el mismo comando con --install --resume. No se borra ni modifica un worker ready/ajeno.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
