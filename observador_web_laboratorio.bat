@echo off
rem LABORATORIO - Observador universal (fuente por URL + perfil) de principio a fin: observador web -> coincidencia
rem -> Forms -> folio verificado. Datos FICTICIOS, carpeta temporal nueva, sitios locales 127.0.0.1. No toca data\ ni C:\cauce-msi.
rem Uso: observador_web_laboratorio.bat C:\ruta\cauce-forms   (guia: docs\OBSERVADOR_WEB_UNIVERSAL.md)
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
set "FORMS=%~1"
if "%FORMS%"=="" set "FORMS=%~dp0..\cauce-forms"
.venv\Scripts\python.exe scripts\preparar_navegador.py --comprobar >nul 2>nul
if errorlevel 1 (
  echo Falta el navegador del observador: se instala en .browsers y node_modules de esta carpeta ^(requiere Internet^).
  .venv\Scripts\python.exe scripts\preparar_navegador.py
  if errorlevel 1 (
    echo No se pudo preparar el navegador. Revisa el mensaje de arriba.
    pause
    exit /b 1
  )
)
.venv\Scripts\python.exe -m pytest -q tests\test_observador_web.py
.venv\Scripts\python.exe scripts\recorrido_coincidencia_forms.py --forms "%FORMS%" --observador web --interfaz --salida resultados\recorrido_observador_web
.venv\Scripts\python.exe scripts\recorrido_fuentes_web_ui.py --salida resultados\recorrido_fuentes_web_ui
echo.
echo Envia resultados\recorrido_observador_web\recorrido_coincidencia_forms.json,
echo resultados\recorrido_fuentes_web_ui\recorrido_fuentes_web_ui.json y la salida de las pruebas.
pause
