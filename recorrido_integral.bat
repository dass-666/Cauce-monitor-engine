@echo off
rem v2.3 - Recorrido integral automatico: respaldo, restauracion, continuidad, cierre y retencion
rem con fecha SIMULADA (+2 dias; el reloj del equipo no cambia). Datos FICTICIOS, carpeta temporal,
rem no toca data\. Necesita Node.js y el navegador (python scripts\preparar_navegador.py).
rem Resultado: resultados\recorrido_integral\recorrido_integral.json
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
.venv\Scripts\python.exe scripts\recorrido_integral.py %*
set RESULTADO=%ERRORLEVEL%
if "%RESULTADO%"=="0" echo RESULTADO: el recorrido integral paso
if not "%RESULTADO%"=="0" echo RESULTADO: algo fallo - revisa resultados\recorrido_integral\recorrido_integral.json
if not "%MONITOREO_SIN_PAUSA%"=="1" pause
exit /b %RESULTADO%
