@echo off
rem v2.3 - Ventanilla de LABORATORIO de principio a fin (datos FICTICIOS, solo este equipo).
rem Arranca el portal de laboratorio y el motor en resultados\ventanilla_lab (no toca data\),
rem crea admin y operador1 y abre el navegador. Todo lo demas se hace desde la pantalla.
rem Guia: docs\GUIA_RECORRIDO_VENTANILLA_v2.3.md
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
.venv\Scripts\python.exe scripts\ventanilla_laboratorio.py --abrir %*
if errorlevel 1 pause
