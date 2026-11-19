@echo off
rem v2.0 - Modo VENTANILLA (atencion presencial). Inicia el motor y el portal de PRUEBA
rem y abre el navegador directamente en la pantalla Ventanilla cuando el servidor responde.
rem Datos ficticios: el portal de prueba corre en este mismo equipo (127.0.0.1).
rem Guia: docs\GUIA_VENTANILLA_WINDOWS.md
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
if not "%MONITOREO_SIN_PORTAL%"=="1" start "Portal de prueba (datos ficticios)" /min cmd /c iniciar_portal.bat
echo Iniciando el motor en modo ventanilla. Para detenerlo pulsa Ctrl+C en esta ventana.
echo (El portal de prueba queda en otra ventana minimizada: cierrala al terminar.)
echo Entra con tu usuario de OPERADOR (el administrador lo crea en Configuracion).
set MONITOREO_ABRIR_NAVEGADOR=1
set MONITOREO_ABRIR_PANTALLA=ventanilla
.venv\Scripts\python.exe -m monitoreo
if errorlevel 1 pause
