@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m pytest -q
set RESULTADO=%ERRORLEVEL%
if "%RESULTADO%"=="0" echo TODAS LAS PRUEBAS PASARON
if not "%RESULTADO%"=="0" echo HAY PRUEBAS FALLIDAS - revisa el detalle de arriba
pause
exit /b %RESULTADO%
