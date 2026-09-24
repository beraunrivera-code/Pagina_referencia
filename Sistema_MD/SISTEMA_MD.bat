@echo off
setlocal
set "SISTEMA_MD_PY=%~dp0.venv\Scripts\python.exe"
set "SISTEMA_MD_PYW=%~dp0.venv\Scripts\pythonw.exe"
if exist "%~dp0.venv-local\sistema_md_ready.json" (
  set "SISTEMA_MD_PY=%~dp0.venv-local\Scripts\python.exe"
  set "SISTEMA_MD_PYW=%~dp0.venv-local\Scripts\pythonw.exe"
)

if not exist "%SISTEMA_MD_PY%" goto entorno_ausente
"%SISTEMA_MD_PY%" --version >nul 2>&1
if errorlevel 1 goto entorno_ausente
if "%~1"=="" goto interfaz
if /i "%~1"=="--gui" goto interfaz

"%SISTEMA_MD_PY%" -B "%~dp0iniciar.py" %*
exit /b %errorlevel%

:interfaz
if not exist "%SISTEMA_MD_PYW%" goto entorno_ausente
start "" "%SISTEMA_MD_PYW%" -B "%~dp0iniciar.py" --gui
exit /b 0

:entorno_ausente
echo [ERROR] Falta el entorno local .venv de Sistema MD.
echo Consulta INICIO_RAPIDO.md. No se instalara nada automaticamente.
echo En otra PC prepara un entorno nuevo con Python 3.12:
echo   py -3.12 "%~dp0preparar_equipo.py" --instalar
echo No copies la carpeta .venv de otra computadora.
if "%~1"=="" pause
exit /b 1
