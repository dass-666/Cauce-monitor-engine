@echo off
rem DEMOSTRACION del piloto asistido (datos FICTICIOS). NO es el piloto: el piloto real se prepara con
rem preparar_piloto.bat y se abre con iniciar.bat (docs\INICIAR_PILOTO_WINDOWS.md).
rem Abre Cauce en modo piloto (produccion + flujo administrativo, sin portal) en una carpeta de demostracion
rem propia, con contrasenas aleatorias que imprime en la consola. Los respaldos de Cauce NO cubren esa carpeta.
rem   piloto_asistido.bat               demostracion a mano  (resultados\demo_asistido; Ctrl+C para terminar)
rem   piloto_asistido.bat --recorrido   recorrido automatico (resultados\demo_asistido_recorrido, con capturas)
rem Solo borra carpetas que el mismo creo (marca DEMOSTRACION_FICTICIA.json); ante cualquier otra, se niega.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
.venv\Scripts\python.exe scripts\piloto_asistido.py %*
set RESULTADO=%ERRORLEVEL%
if "%RESULTADO%"=="0" echo RESULTADO: demostracion sin errores
if not "%RESULTADO%"=="0" echo RESULTADO: algo fallo - revisa el mensaje de arriba y resultados\demo_asistido_recorrido\recorrido_asistido.json
if not "%MONITOREO_SIN_PAUSA%"=="1" pause
exit /b %RESULTADO%
