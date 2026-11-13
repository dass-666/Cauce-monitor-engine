@echo off
rem Inicia el PORTAL DE RESERVACIONES DE PRUEBA (v1.5): datos ficticios, solo en este equipo.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
echo Portal de prueba en http://127.0.0.1:8200/reservar  (Ctrl+C para detenerlo)
.venv\Scripts\python.exe scripts\iniciar_portal.py
if errorlevel 1 pause
