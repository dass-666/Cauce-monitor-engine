@echo off
rem Cauce - INSTALAR en ESTA carpeta (laboratorio, datos FICTICIOS). Guia: docs\EMPEZAR_CAUCE_WINDOWS.md
rem   1) comprueba el equipo (Python, Node.js, puertos, permisos) y se detiene si falta algo
rem   2) instala o completa .venv, dependencias, .env y base (instalar.ps1; respalda antes y conserva .env)
rem   3) prepara Playwright y Chromium SOLO si faltan (en .browsers, dentro de esta carpeta)
rem   4) comprueba todo de nuevo, incluido arrancar Chromium sin ventana
rem No borra datos ni carpetas, no activa produccion y no conecta servicios externos. Se puede repetir.
setlocal
cd /d "%~dp0"
echo.
echo === Cauce: instalacion de laboratorio en %CD% ===
echo.

rem --- Python (se prueba de verdad: el "python" de la Microsoft Store no cuenta)
set "PY="
for %%V in (3.13 3.12 3.11 3.14) do (
  if not defined PY ( py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V" )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] <= (3,14) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [CRITICO] No se encontro Python 3.11 a 3.14.
  echo           Instala Python 3.13 de 64 bits desde https://www.python.org/downloads/
  echo           marcando "Add python.exe to PATH", abre una ventana nueva y repite.
  goto :fallo
)

echo --- Paso 1/4: comprobacion previa del equipo (solo lectura)
%PY% -I scripts\comprobar_equipo.py --antes --sin-informe
if errorlevel 2 (
  echo.
  echo No se instalo nada. Resuelve lo marcado como CRITICO y vuelve a ejecutar instalar_cauce.bat
  goto :fallo
)

echo.
echo --- Paso 2/4: entorno, dependencias, configuracion y base (instalar.ps1)
if exist .env (
  echo Ya existe .env: se conserva.
) else (
  echo Te pedira una contrasena para el usuario "admin" del modo NORMAL ^(iniciar.bat^).
  echo El LABORATORIO no la usa: alli el acceso es admin / Admin-Laboratorio-2026.
)
set "NOINT="
if defined MONITOREO_ADMIN_PASSWORD set "NOINT=-NoInteractivo"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1" %NOINT%
if errorlevel 1 (
  echo.
  echo La instalacion se detuvo. El registro esta en data\logs\instalacion-*.log
  goto :fallo
)

echo.
echo --- Paso 3/4: navegador del observador (Playwright y Chromium)
.venv\Scripts\python.exe scripts\preparar_navegador.py --comprobar >nul 2>nul
if errorlevel 1 (
  echo Falta el navegador: se instala en .browsers dentro de esta carpeta ^(requiere Internet^).
  .venv\Scripts\python.exe scripts\preparar_navegador.py
  if errorlevel 1 (
    echo No se pudo preparar el navegador. Revisa el mensaje de arriba.
    goto :fallo
  )
) else (
  echo Ya estaba preparado: no se reinstala.
)

echo.
echo --- Paso 4/4: comprobacion final
.venv\Scripts\python.exe scripts\comprobar_equipo.py --probar
if errorlevel 2 goto :fallo
echo.
echo ============================================================
echo  LISTO. Para abrir Cauce: doble clic en  cauce_rc1.bat
echo  Usuario admin  /  contrasena Admin-Laboratorio-2026  (solo laboratorio)
echo ============================================================
if not "%MONITOREO_SIN_PAUSA%"=="1" pause
exit /b 0

:fallo
echo.
echo Si necesitas ayuda, comparte resultados\comprobacion_equipo.txt y data\logs\ (ver la guia).
if not "%MONITOREO_SIN_PAUSA%"=="1" pause
exit /b 1
