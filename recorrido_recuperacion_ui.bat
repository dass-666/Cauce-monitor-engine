@echo off
rem Recuperacion segura desde la INTERFAZ: tras restaurar un respaldo, un administrador concilia y levanta
rem el bloqueo con clics en la pantalla Recuperacion. Datos FICTICIOS, carpeta temporal, no toca data\.
rem Necesita Node.js y el navegador (python scripts\preparar_navegador.py).
rem Resultado: resultados\recorrido_recuperacion_ui\ (json y capturas)
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
.venv\Scripts\python.exe scripts\recorrido_recuperacion_ui.py %*
set RESULTADO=%ERRORLEVEL%
if "%RESULTADO%"=="0" echo RESULTADO: el recorrido de recuperacion por la interfaz paso
if not "%RESULTADO%"=="0" echo RESULTADO: algo fallo - revisa resultados\recorrido_recuperacion_ui\recorrido_recuperacion_ui.json
if not "%MONITOREO_SIN_PAUSA%"=="1" pause
exit /b %RESULTADO%
