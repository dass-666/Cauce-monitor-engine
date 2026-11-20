@echo off
rem Cauce - COMPROBAR el equipo y esta carpeta. SOLO LECTURA: no instala, no borra y no cambia datos.
rem Revisa rama/commit, Python, .venv, Node.js, Playwright y Chromium (lo arranca sin ventana y lo cierra),
rem puertos locales, permisos, espacio, que produccion siga desactivada y que datos de laboratorio hay.
rem Unico archivo que escribe: resultados\comprobacion_equipo.txt (el informe, para compartirlo si algo falla).
setlocal
cd /d "%~dp0"
set "PY="
if exist .venv\Scripts\python.exe set "PY=.venv\Scripts\python.exe"
if not defined PY (
  for %%V in (3.13 3.12 3.11 3.14) do (
    if not defined PY ( py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V" )
  )
)
if not defined PY (
  python -c "import sys" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [CRITICO] No se encontro Python. Instala Python 3.13 desde https://www.python.org/downloads/
  if not "%MONITOREO_SIN_PAUSA%"=="1" pause
  exit /b 2
)
%PY% -I scripts\comprobar_equipo.py --probar %*
set RESULTADO=%ERRORLEVEL%
if not "%MONITOREO_SIN_PAUSA%"=="1" pause
exit /b %RESULTADO%
