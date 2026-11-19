@echo off
rem RC1 - Recorrido administrativo automatico con la interfaz: importar 6 clientes ficticios,
rem consentimiento, plan, coincidencia, folio (confirmacion SIMULADA) y reimportacion. Carpeta temporal,
rem no toca data\. Necesita Node.js y el navegador (python scripts\preparar_navegador.py).
rem Resultado: resultados\recorrido_rc1\recorrido_rc1.json
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
.venv\Scripts\python.exe scripts\recorrido_rc1.py %*
set RESULTADO=%ERRORLEVEL%
if "%RESULTADO%"=="0" echo RESULTADO: el recorrido RC1 paso
if not "%RESULTADO%"=="0" echo RESULTADO: algo fallo - revisa resultados\recorrido_rc1\recorrido_rc1.json
if not "%MONITOREO_SIN_PAUSA%"=="1" pause
exit /b %RESULTADO%
