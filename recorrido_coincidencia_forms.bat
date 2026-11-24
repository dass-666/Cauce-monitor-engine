@echo off
rem LABORATORIO - Recorrido de principio a fin: expediente -> plan -> observador -> coincidencia -> Forms -> folio verificado.
rem Datos FICTICIOS, carpeta temporal nueva y portal de laboratorio propio en 127.0.0.1: no toca data\, C:\cauce-msi ni respaldos.
rem Forms se busca en ..\cauce-forms; para otra carpeta: recorrido_coincidencia_forms.bat C:\ruta\cauce-forms
rem Los pasos del operador (Autorrellenar y folios) se hacen con la PANTALLA de Ventanilla (--interfaz).
rem Para VER la ventana de Forms: recorrido_coincidencia_forms.bat C:\ruta\cauce-forms --con-ventana
rem Resultado: resultados\recorrido_coincidencia_forms\recorrido_coincidencia_forms.json  (guia: docs\COINCIDENCIA_FORMS_FOLIO.md)
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
set "FORMS=%~1"
if "%FORMS%"=="" set "FORMS=%~dp0..\cauce-forms"
.venv\Scripts\python.exe scripts\recorrido_coincidencia_forms.py --forms "%FORMS%" --interfaz %2
echo.
echo Envia resultados\recorrido_coincidencia_forms\recorrido_coincidencia_forms.json
pause
