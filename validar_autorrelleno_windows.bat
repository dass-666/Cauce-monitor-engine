@echo off
rem LABORATORIO - Validacion fisica del autorrellenado Ventanilla -> Forms en este equipo (Windows 11).
rem Un solo paso: pruebas con Forms real y ventana visible, recorrido por la interfaz y reinicio de Monitor.
rem Datos FICTICIOS y carpetas temporales nuevas: no toca data\, C:\cauce-msi, D:\cauce-demo-8328e2c ni respaldos.
rem Tarda unos 10 minutos. Veras abrirse y cerrarse ventanas de Chromium: no las toques.
rem Resultado: resultados\validacion_autorrelleno\RESUMEN.txt   (guia: docs\AUTORRELLENO_FORMS_LAB.md)
rem Forms se busca en ..\cauce-forms; para otra carpeta: validar_autorrelleno_windows.bat C:\ruta\cauce-forms
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
if not "%~1"=="" set "CAUCE_FORMS_DIR=%~1"
.venv\Scripts\python.exe scripts\validar_autorrelleno_windows.py
echo.
echo Abre resultados\validacion_autorrelleno\RESUMEN.txt y enviamelo junto con esa carpeta.
pause
