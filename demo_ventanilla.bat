@echo off
rem v2.0 - Demostracion AUTOMATICA de la ventanilla con datos FICTICIOS (unos 3 minutos).
rem Arranca un portal de prueba y un motor nuevos en carpetas temporales (no toca data\)
rem y, con un navegador automatizado, atiende 6 personas hasta su folio, una sin cita,
rem un duplicado, una atencion retomada tras reiniciar el motor, la vista de telefono,
rem la pantalla Conexiones y Tramites a mano. Resultado: resultados\demo_v20\demo_v20.json
rem Requiere Node.js y el navegador de Playwright (ver docs\GUIA_VENTANILLA_WINDOWS.md).
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
where node >nul 2>nul
if errorlevel 1 (
  echo Falta Node.js. Instala la version LTS desde https://nodejs.org y vuelve a ejecutar.
  if not "%MONITOREO_SIN_PAUSA%"=="1" pause
  exit /b 3
)
rem Cierre v2.1: comprueba de verdad que Chromium arranca (no solo que exista node_modules).
.venv\Scripts\python.exe scripts\preparar_navegador.py --comprobar >nul 2>nul
if errorlevel 1 (
  echo Preparando el navegador automatizado (solo la primera vez, descarga unos 150 MB^)...
  .venv\Scripts\python.exe scripts\preparar_navegador.py
  if errorlevel 1 goto sin_navegador
)
.venv\Scripts\python.exe scripts\demo_v20.py --salida resultados\demo_v20 --capturas resultados\capturas_v20 %*
set RESULTADO=%ERRORLEVEL%
if "%RESULTADO%"=="0" echo RESULTADO: todas las comprobaciones de la demostracion pasaron
if not "%RESULTADO%"=="0" echo RESULTADO: hay comprobaciones fallidas - revisa resultados\demo_v20\demo_v20.json
if not "%MONITOREO_SIN_PAUSA%"=="1" pause
exit /b %RESULTADO%

:sin_navegador
echo No se pudo preparar el navegador automatizado (sin internet o sin permisos).
if not "%MONITOREO_SIN_PAUSA%"=="1" pause
exit /b 3
