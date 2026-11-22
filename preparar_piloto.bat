@echo off
rem Prepara ESTA carpeta como instalacion del PILOTO asistido (produccion + flujo administrativo, sin portal).
rem El piloto se abre despues con iniciar.bat y se respalda con respaldar_externo.bat: todos usan la MISMA base
rem (data\monitoreo.db de esta carpeta). Guia: docs\INICIAR_PILOTO_WINDOWS.md
rem   preparar_piloto.bat D:\CaucePiloto          carpeta en OTRA unidad para la copia del diario y los respaldos
rem   preparar_piloto.bat D:\CaucePiloto 8100     (opcional) puerto fijo del panel
rem   preparar_piloto.bat --comprobar             solo revisa la configuracion actual
rem No borra nada ni toca otras carpetas. Pide TU contrasena de administrador (no se muestra).
setlocal
cd /d "%~dp0"
if "%~1"=="" (
  echo Indica la carpeta externa, por ejemplo:  preparar_piloto.bat D:\CaucePiloto
  if not "%MONITOREO_SIN_PAUSA%"=="1" pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe (
  echo --- Primero se instala el entorno en esta carpeta ^(instalar.ps1 -Produccion^).
  echo     Te pedira la contrasena del administrador del piloto ^(minimo 12 caracteres, letras y numeros^).
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1" -Produccion
  if errorlevel 1 (
    echo La instalacion se detuvo. Revisa el mensaje de arriba y data\logs\instalacion-*.log
    if not "%MONITOREO_SIN_PAUSA%"=="1" pause
    exit /b 1
  )
)
if /i "%~1"=="--comprobar" (
  .venv\Scripts\python.exe scripts\preparar_piloto.py --comprobar
) else (
  if "%~2"=="" (
    if not exist "%~1" mkdir "%~1"
    .venv\Scripts\python.exe scripts\preparar_piloto.py --externo "%~1"
  ) else (
    if not exist "%~1" mkdir "%~1"
    .venv\Scripts\python.exe scripts\preparar_piloto.py --externo "%~1" --puerto %~2
  )
)
set RESULTADO=%ERRORLEVEL%
if "%RESULTADO%"=="0" echo RESULTADO: piloto preparado
if not "%RESULTADO%"=="0" echo RESULTADO: el piloto NO quedo preparado - revisa el mensaje de arriba
if not "%MONITOREO_SIN_PAUSA%"=="1" pause
exit /b %RESULTADO%
