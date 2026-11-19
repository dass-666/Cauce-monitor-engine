@echo off
rem RC1 - Cauce Monitor Engine: MESA DE CONTROL del administrador (laboratorio, datos FICTICIOS).
rem Arranca el portal de laboratorio y el motor en resultados\cauce_rc1 (no toca data\) y abre el navegador.
rem Guia: docs\rc1\GUIA_WINDOWS_RC1.md
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
.venv\Scripts\python.exe scripts\cauce_rc1.py --abrir %*
if errorlevel 1 pause
