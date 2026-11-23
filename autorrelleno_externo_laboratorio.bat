@echo off
rem LABORATORIO - Ventanilla con "Autorrellenar formulario" sobre un formulario EXTERNO AUTORIZADO.
rem Solo datos FICTICIOS: Forms corre con el mismo usuario de Windows que Monitor (sin aislamiento).
rem   autorrelleno_externo_laboratorio.bat RUTA\perfil_externo.json HOST_AUTORIZADO [CARPETA_FORMS]
rem Guia: docs\PERFIL_FORMULARIO_EXTERNO.md
cd /d "%~dp0"
if "%~2"=="" (
  echo Uso: autorrelleno_externo_laboratorio.bat RUTA\perfil_externo.json HOST_AUTORIZADO [CARPETA_FORMS]
  pause
  exit /b 2
)
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
set "FORMS=%~3"
if "%FORMS%"=="" set "FORMS=%~dp0..\cauce-forms"
if not exist "%FORMS%\.venv\Scripts\python.exe" (
  echo No encuentro Forms instalado en "%FORMS%".
  pause
  exit /b 1
)
.venv\Scripts\python.exe scripts\ventanilla_laboratorio.py --abrir --carpeta "%~dp0resultados\autorrelleno_externo_lab" --forms "%FORMS%" --perfil-externo "%~1" --host-autorizado "%~2"
if errorlevel 1 pause
