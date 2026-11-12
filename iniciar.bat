@echo off
rem Inicia el motor y el panel. El navegador se abre solo cuando el servidor ya responde.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
echo Iniciando el motor. Para detenerlo pulsa Ctrl+C en esta ventana.
set MONITOREO_ABRIR_NAVEGADOR=1
.venv\Scripts\python.exe -m monitoreo
if errorlevel 1 pause
