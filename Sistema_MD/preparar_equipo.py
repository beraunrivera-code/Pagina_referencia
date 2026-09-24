"""Preparación explícita por PC; no copia venv, claves ni sesiones de otra máquina."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent


def main(argv=None):
    parser = argparse.ArgumentParser(description='Prepara el núcleo en esta PC con Python 3.12. No descarga modelos.')
    parser.add_argument('--instalar', action='store_true', help='Autoriza crear .venv-local e instalar las dependencias de PyPI')
    parser.add_argument('--reanudar', action='store_true', help='Reintenta una preparación incompleta creada aquí; requiere --instalar')
    args = parser.parse_args(argv)
    target = ROOT / '.venv-local'
    python = target / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    if args.reanudar and not args.instalar:
        parser.error('--reanudar requiere --instalar; no se instala nada sin esa autorización')
    if not args.instalar:
        print(json.dumps({'python':sys.version.split()[0], 'python_compatible':sys.version_info[:2] == (3,12),
                          'tk_disponible':importlib.util.find_spec('tkinter') is not None,
                          'destino':str(target), 'entorno_preparado':python.is_file(), 'instalado_ahora':False,
                          'aviso':'Solo inspección. --instalar descarga dependencias. No es un instalador comercial certificado.'},ensure_ascii=False,indent=2))
        return 0
    if sys.version_info[:2] != (3,12):
        parser.error('Usa Python 3.12 de esta PC para mantener el entorno ensayado; no se modifica tu Python.')
    preparation = target / 'sistema_md_preparacion.json'
    identity = {'installation_root':str(ROOT.resolve()), 'base_python':str(Path(sys.executable).resolve())}
    if target.exists() and not args.reanudar:
        parser.error('.venv-local ya existe. Se conserva; no se reemplaza automáticamente.')
    if args.reanudar:
        if not preparation.is_file() or json.loads(preparation.read_text(encoding='utf-8')) != identity:
            parser.error('No corresponde a una preparación iniciada aquí con este Python. No se modifica el entorno.')
        if (target / 'sistema_md_ready.json').exists():
            parser.error('El entorno ya está aceptado. --reanudar no actualiza instalaciones terminadas.')
    if importlib.util.find_spec('tkinter') is None:
        parser.error('Falta Tcl/Tk en Python. Completa su instalación oficial antes de preparar el programa.')
    if not args.reanudar:
        target.mkdir()
        preparation.write_text(json.dumps(identity), encoding='utf-8')
    venv.EnvBuilder(with_pip=True, system_site_packages=False, clear=False).create(target)
    result = subprocess.run([str(python),'-I','-m','pip','--isolated','install','--index-url','https://pypi.org/simple',
                             '--disable-pip-version-check','-r',str(ROOT/'requirements-local.txt')],shell=False)
    if result.returncode:
        print('Preparación incompleta; se conserva .venv-local. Para reintentar: --instalar --reanudar. No se activa ni borra nada.')
        return result.returncode
    verify = subprocess.run([str(python),'-B',str(ROOT/'iniciar.py'),'--check'],shell=False)
    if verify.returncode:
        print('El control de arranque falló. Entorno no aceptado; revisa el diagnóstico.')
        return verify.returncode
    (target/'sistema_md_ready.json').write_text(json.dumps({'python':sys.version.split()[0],
              'installation_root':str(ROOT),'check':'arranque','extraction_quality':'no_certificada'}),encoding='utf-8')
    print('Núcleo preparado en esta PC. Motores/modelos y cuentas se configuran por separado.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
