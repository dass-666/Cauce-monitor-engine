@echo off
rem Inicia el motor y el panel. El navegador se abre solo cuando el servidor ya responde.
rem v1.5: tambien abre (minimizado) el portal de reservaciones de PRUEBA en http://127.0.0.1:8200
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
if not "%MONITOREO_SIN_PORTAL%"=="1" start "Portal de prueba (datos ficticios)" /min cmd /c iniciar_portal.bat
echo Iniciando el motor. Para detenerlo pulsa Ctrl+C en esta ventana.
echo (El portal de prueba queda en otra ventana minimizada: cierrala al terminar.)
set MONITOREO_ABRIR_NAVEGADOR=1
.venv\Scripts\python.exe -m monitoreo
if errorlevel 1 pause
