@echo off
rem Comprueba un respaldo externo SIN restaurarlo: huella SHA-256 contra su manifiesto, integridad,
rem y ensayo de restauracion en una carpeta temporal (no toca la base real ni el diario).
rem Uso: verificar_respaldo.bat E:\Cauce\respaldos\cauce-respaldo-AAAAMMDD-....db
rem      verificar_respaldo.bat          (lista los respaldos de MONITOREO_RESPALDO_EXTERNO)
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
if "%~1"=="" goto listar
.venv\Scripts\python.exe -m monitoreo.respaldo_externo ensayar "%~1"
set RESULTADO=%ERRORLEVEL%
if "%RESULTADO%"=="0" echo RESULTADO: el respaldo es integro y se puede restaurar
if not "%RESULTADO%"=="0" echo RESULTADO: el respaldo NO supera la verificacion - no lo uses para restaurar
goto fin
:listar
.venv\Scripts\python.exe -m monitoreo.respaldo_externo listar
set RESULTADO=%ERRORLEVEL%
:fin
if not "%MONITOREO_SIN_PAUSA%"=="1" pause
exit /b %RESULTADO%
