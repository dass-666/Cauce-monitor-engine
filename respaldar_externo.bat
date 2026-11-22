@echo off
rem Respaldo de la base a un medio EXTERNO (USB cifrada, segundo disco o red), verificado:
rem copia consistente, se relee del medio, SHA-256, integridad y manifiesto. Conserva los 14 mas recientes.
rem Se puede ejecutar con el motor en marcha. Ver docs\PROTECCION_DATOS_PILOTO.md
rem Uso: respaldar_externo.bat              (usa MONITOREO_RESPALDO_EXTERNO de .env)
rem      respaldar_externo.bat E:\Cauce\respaldos
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m monitoreo.respaldo_externo respaldar %*
set RESULTADO=%ERRORLEVEL%
if "%RESULTADO%"=="0" echo RESULTADO: respaldo externo hecho y verificado
if not "%RESULTADO%"=="0" echo RESULTADO: el respaldo externo NO se hizo - revisa el mensaje de arriba
if not "%MONITOREO_SIN_PAUSA%"=="1" pause
exit /b %RESULTADO%
