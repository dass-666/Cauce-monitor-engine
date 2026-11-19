@echo off
rem Autodiagnostico: comprueba base de datos, esquema, planificador, conectores, notificaciones,
rem recursos e integridad, y dice que hacer con cada problema. No modifica nada.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m monitoreo.diagnostico %*
set RESULTADO=%ERRORLEVEL%
if "%RESULTADO%"=="0" echo RESULTADO: todo correcto
if "%RESULTADO%"=="1" echo RESULTADO: hay avisos - revisa la lista de arriba
if "%RESULTADO%"=="2" echo RESULTADO: hay problemas CRITICOS - sigue las indicaciones de cada uno
if not "%MONITOREO_SIN_PAUSA%"=="1" pause
exit /b %RESULTADO%
