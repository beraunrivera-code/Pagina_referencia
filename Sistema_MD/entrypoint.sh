#!/usr/bin/env bash
# Punto de entrada POSIX de Sistema MD (contenedor Docker o Linux nativo).
#
#   entrypoint.sh                  -> interfaz si hay $DISPLAY; si no, ayuda de la CLI
#   entrypoint.sh gui              -> interfaz Tk (exige $DISPLAY: X11/Wayland o VNC)
#   entrypoint.sh pruebas [args]   -> suite app/tests (Xvfb automático sin escritorio)
#   entrypoint.sh --check          -> control de arranque de solo lectura
#   entrypoint.sh <orden CLI> ...  -> la misma CLI que SISTEMA_MD.bat (convertir, informe, ...)
#
# No instala nada, no llama a IA y no modifica cuentas. Equivale a SISTEMA_MD.bat.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${ROOT}/app${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONUTF8=1
export PYTHONDONTWRITEBYTECODE=1

PYTHON="${SISTEMA_MD_PYTHON:-}"
if [[ -z "${PYTHON}" ]]; then
    for candidate in "${ROOT}/.venv-local/bin/python" python3.12 python3; do
        if command -v "${candidate}" >/dev/null 2>&1; then
            PYTHON="${candidate}"
            break
        fi
    done
fi
if [[ -z "${PYTHON}" ]]; then
    echo "[ERROR] No se encontró Python 3.12. Prepara el entorno con: python3.12 preparar_equipo.py --instalar" >&2
    exit 1
fi

has_display() { [[ -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]]; }

# Tk necesita un servidor X aunque no se vea ninguna ventana (pruebas y --check).
exec_with_display() {
    if has_display; then
        exec "$@"
    elif command -v xvfb-run >/dev/null 2>&1; then
        if [[ $$ -eq 1 ]]; then
            # PID 1 del contenedor: xvfb-run espera la señal SIGUSR1 de Xvfb y, como init,
            # nunca le llega (se colgaba indefinidamente). Como hijo de este shell sí funciona.
            xvfb-run -a "$@"
            exit $?
        fi
        exec xvfb-run -a "$@"
    else
        echo "[ERROR] Sin \$DISPLAY y sin xvfb-run: instala xvfb para las pruebas de interfaz." >&2
        exit 1
    fi
}

case "${1:-}" in
    pruebas|test|tests)
        shift
        cd "${ROOT}/app"
        export PYTHONPATH="${ROOT}/app:${ROOT}/app/tests"
        if [[ $# -eq 0 ]]; then
            set -- discover -s tests
        fi
        exec_with_display "${PYTHON}" -m unittest "$@"
        ;;
    --check)
        exec_with_display "${PYTHON}" -B "${ROOT}/iniciar.py" --check
        ;;
    gui|--gui)
        if ! has_display; then
            echo "[ERROR] La interfaz necesita \$DISPLAY (X11/Wayland). En modo nube usa la CLI: entrypoint.sh --help" >&2
            exit 2
        fi
        exec "${PYTHON}" -B "${ROOT}/iniciar.py" --gui
        ;;
    "")
        if has_display; then
            exec "${PYTHON}" -B "${ROOT}/iniciar.py" --gui
        fi
        exec "${PYTHON}" -B "${ROOT}/iniciar.py" --cli --help
        ;;
    *)
        exec "${PYTHON}" -B "${ROOT}/iniciar.py" "$@"
        ;;
esac
