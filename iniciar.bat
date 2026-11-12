@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
echo Abriendo http://127.0.0.1:8000  (Ctrl+C para detener)
start "" http://127.0.0.1:8000
.venv\Scripts\python.exe -m monitoreo
