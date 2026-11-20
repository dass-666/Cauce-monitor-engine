@echo off
rem RC1 - Cauce Monitor Engine: MESA DE CONTROL del administrador (laboratorio, datos FICTICIOS).
rem Arranca el portal de laboratorio y el motor en resultados\cauce_rc1 (no toca data\) y abre el navegador.
rem Guia: docs\EMPEZAR_CAUCE_WINDOWS.md  -  Cerrar: Ctrl+C en esta ventana (detiene motor y portal).
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta instalar_cauce.bat ^(guia: docs\EMPEZAR_CAUCE_WINDOWS.md^)
  pause
  exit /b 1
)
rem Sin Chromium el observador del calendario no arranca: se avisa (no se instala nada desde aqui).
.venv\Scripts\python.exe scripts\preparar_navegador.py --comprobar >nul 2>nul
if errorlevel 1 (
  echo AVISO: falta Node.js, Playwright o Chromium: el observador del calendario no podra arrancar.
  echo        Cierra esta ventana y ejecuta instalar_cauce.bat ^(no borra datos^).
  echo.
)
.venv\Scripts\python.exe scripts\cauce_rc1.py --abrir %*
if errorlevel 1 pause
