@echo off
rem Revisa una carpeta para la copia externa del diario de eventos criticos SIN activarla:
rem que exista, que este en OTRA unidad, fuera de data\ y que se pueda escribir.
rem No cambia .env ni ninguna configuracion. Ver docs\RESPALDO_EXTERNO_DIARIO.md
rem Uso: revisar_copia_diario.bat E:\CauceDiario   (sin carpeta: revisa la configurada)
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m monitoreo.recuperacion revisar-copia %*
set RESULTADO=%ERRORLEVEL%
if "%RESULTADO%"=="0" echo RESULTADO: la carpeta es apta para la copia externa
if not "%RESULTADO%"=="0" echo RESULTADO: la carpeta NO es apta - revisa los problemas de arriba
if not "%MONITOREO_SIN_PAUSA%"=="1" pause
exit /b %RESULTADO%
