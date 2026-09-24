@echo off
rem Compatibilidad: GUI, CLI y arrastrar archivos usan el mismo entorno local.
call "%~dp0..\SISTEMA_MD.bat" %*
exit /b %errorlevel%
