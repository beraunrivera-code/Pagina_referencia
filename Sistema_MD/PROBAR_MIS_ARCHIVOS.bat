@echo off
rem Prueba TUS archivos reales: copia PDF, DWG, Excel, Word... en evaluacion\corpus
rem y haz doble clic aqui. Convierte cada uno aislado, revisa el Markdown y deja
rem el resumen en evaluacion\RESUMEN.txt. No toca tu biblioteca ni sube nada a internet.
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"
set "PY=%~dp0.venv-local\Scripts\python.exe"
if not exist "%PY%" goto sin_entorno
if not exist "evaluacion\corpus" mkdir "evaluacion\corpus"
dir /b /a-d "evaluacion\corpus" 2>nul | findstr /v /i /x "esperado.json" >nul
if errorlevel 1 goto sin_archivos

echo Probando los archivos de evaluacion\corpus ... puede tardar unos minutos.
echo.
"%PY%" tortura\ejecutar_tortura.py --suite evaluacion\corpus --salida evaluacion\resultado > evaluacion\RESUMEN.txt 2>&1
"%PY%" tortura\validar_salidas_md.py --salida evaluacion\resultado --json evaluacion\validacion.json >> evaluacion\RESUMEN.txt 2>&1
type evaluacion\RESUMEN.txt
echo.
echo Resumen guardado en: %~dp0evaluacion\RESUMEN.txt
echo Resultados por archivo en: %~dp0evaluacion\resultado
echo Envia RESUMEN.txt a Claude para analizar los fallos.
start "" notepad "%~dp0evaluacion\RESUMEN.txt"
pause
exit /b 0

:sin_archivos
echo La carpeta evaluacion\corpus esta vacia.
echo Copia ahi tus archivos (PDF, DWG, Excel, Word, imagenes...) y vuelve a hacer doble clic.
start "" explorer "%~dp0evaluacion\corpus"
pause
exit /b 0

:sin_entorno
echo [ERROR] Falta preparar el programa en esta PC. En PowerShell, dentro de esta carpeta:
echo   py -3.12 preparar_equipo.py --instalar
pause
exit /b 1
