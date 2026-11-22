@echo off
rem LABORATORIO - Ventanilla con "Autorrellenar formulario" usando Forms (Conector Universal).
rem Datos FICTICIOS, solo este equipo. Carpeta propia: resultados\autorrelleno_lab (no toca data\ ni otras
rem instalaciones). Forms debe estar instalado (su instalar_windows.bat) en la carpeta que se indique:
rem   autorrelleno_laboratorio.bat C:\cauce-forms
rem Sin argumento se usa ..\cauce-forms (junto a esta carpeta). Guia: docs\AUTORRELLENO_FORMS_LAB.md
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
set "FORMS=%~1"
if "%FORMS%"=="" set "FORMS=%~dp0..\cauce-forms"
if not exist "%FORMS%\.venv\Scripts\python.exe" (
  echo No encuentro Forms instalado en "%FORMS%".
  echo Clona CAUCE-UNIVERSAL-CONEXION ahi y ejecuta su instalar_windows.bat.
  pause
  exit /b 1
)
.venv\Scripts\python.exe scripts\ventanilla_laboratorio.py --abrir --carpeta "%~dp0resultados\autorrelleno_lab" --forms "%FORMS%"
if errorlevel 1 pause
