@echo off
rem v1.7 - Laboratorio local con datos FICTICIOS (unos 10 minutos). No sale de este equipo.
rem Arranca un portal de prueba y un motor nuevos en carpetas temporales (no toca data\),
rem simula 10 operadores, errores de captura, operadores sin respuesta, confirmaciones
rem inciertas, un canal de notificaciones lento, y reinicios del portal y del motor.
rem Al final compara todo contra el portal y escribe resultados\laboratorio_v17\laboratorio.json
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
.venv\Scripts\python.exe scripts\laboratorio_dias.py --v17 --mecanismo A --expedientes 200 --operadores 10 --noche-s 180 --salida resultados\laboratorio_v17 %*
set RESULTADO=%ERRORLEVEL%
if "%RESULTADO%"=="0" echo RESULTADO: todas las comprobaciones del laboratorio pasaron
if not "%RESULTADO%"=="0" echo RESULTADO: hay comprobaciones fallidas - revisa resultados\laboratorio_v17\A\laboratorio.json
if not "%MONITOREO_SIN_PAUSA%"=="1" pause
exit /b %RESULTADO%
