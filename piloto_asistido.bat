@echo off
rem Piloto asistido: abre Cauce EN MODO PILOTO (produccion + flujo administrativo, sin portal) en una
rem carpeta APARTE (resultados\piloto_asistido). No toca la instalacion de la MSI ni data\. Datos FICTICIOS.
rem   piloto_asistido.bat               queda en marcha para recorrerlo a mano (Ctrl+C para terminar)
rem   piloto_asistido.bat --recorrido   recorrido automatico con el navegador y capturas
rem Resultado del recorrido: resultados\piloto_asistido\recorrido_asistido.json y capturas\
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
.venv\Scripts\python.exe scripts\piloto_asistido.py %*
set RESULTADO=%ERRORLEVEL%
if "%RESULTADO%"=="0" echo RESULTADO: piloto asistido sin errores
if not "%RESULTADO%"=="0" echo RESULTADO: algo fallo - revisa resultados\piloto_asistido\recorrido_asistido.json
if not "%MONITOREO_SIN_PAUSA%"=="1" pause
exit /b %RESULTADO%
