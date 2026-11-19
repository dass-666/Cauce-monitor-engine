@echo off
rem v2.3 - Demostracion automatica del recorrido de Ventanilla con la interfaz (unos 30 s).
rem Datos FICTICIOS; carpetas temporales; no toca data\. Necesita Node.js y el navegador
rem (python scripts\preparar_navegador.py). Resultado: resultados\recorrido_v23\recorrido_v23.json
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
.venv\Scripts\python.exe scripts\recorrido_ventanilla_v23.py %*
set RESULTADO=%ERRORLEVEL%
if "%RESULTADO%"=="0" echo RESULTADO: el recorrido completo paso
if not "%RESULTADO%"=="0" echo RESULTADO: algo fallo - revisa resultados\recorrido_v23\recorrido_v23.json
if not "%MONITOREO_SIN_PAUSA%"=="1" pause
exit /b %RESULTADO%
