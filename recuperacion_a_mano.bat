@echo off
rem Restauracion con discrepancias y conciliacion A MANO en la pantalla Recuperacion (datos FICTICIOS).
rem Prepara el escenario solo (respaldo, cambios posteriores, restauracion), deja Cauce BLOQUEADO,
rem muestra la direccion, el usuario y el folio del portal de laboratorio, y espera a que concilies.
rem Carpeta temporal: no toca data\. Guia: docs\GUIA_VALIDACION_WINDOWS.md (paso 5)
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
.venv\Scripts\python.exe scripts\recorrido_recuperacion_ui.py --manual --salida resultados\recuperacion_a_mano %*
set RESULTADO=%ERRORLEVEL%
if "%RESULTADO%"=="0" echo RESULTADO: la conciliacion a mano quedo bien (comprobado por la API)
if not "%RESULTADO%"=="0" echo RESULTADO: algo no quedo bien - revisa resultados\recuperacion_a_mano\recorrido_recuperacion_ui.json
if not "%MONITOREO_SIN_PAUSA%"=="1" pause
exit /b %RESULTADO%
