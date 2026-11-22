@echo off
rem Simulacro completo de proteccion de datos con un medio EXTERNO real (p. ej. una USB en E:).
rem Laboratorio aislado en una carpeta temporal y datos FICTICIOS: no toca data\ ni ninguna otra instalacion.
rem Copia del diario y respaldo de la base en la USB, ensayo, cambios posteriores, restauracion DESDE la USB,
rem bloqueo, conciliacion, continuidad y retencion. Al terminar borra su subcarpeta de la USB.
rem Necesita Node.js y el navegador (python scripts\preparar_navegador.py).
rem Uso: simulacro_proteccion.bat E:\CaucePrueba
rem Resultado: resultados\simulacro_proteccion\recorrido_integral.json
cd /d "%~dp0"
if "%~1"=="" (
  echo Indica la carpeta en la USB, por ejemplo: simulacro_proteccion.bat E:\CaucePrueba
  if not "%MONITOREO_SIN_PAUSA%"=="1" pause
  exit /b 2
)
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
if not exist "%~1" mkdir "%~1"
.venv\Scripts\python.exe scripts\recorrido_integral.py --externa "%~1" --salida resultados\simulacro_proteccion
set RESULTADO=%ERRORLEVEL%
if "%RESULTADO%"=="0" echo RESULTADO: el simulacro de proteccion paso
if not "%RESULTADO%"=="0" echo RESULTADO: algo fallo - revisa resultados\simulacro_proteccion\recorrido_integral.json
if not "%MONITOREO_SIN_PAUSA%"=="1" pause
exit /b %RESULTADO%
