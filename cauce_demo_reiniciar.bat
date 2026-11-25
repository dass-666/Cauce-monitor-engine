@echo off
rem Borra SOLO la carpeta de la demostracion (..\demo-integral) y vuelve a empezar con datos ficticios nuevos.
echo Esto borra la demostracion anterior (solo ..\demo-integral, datos ficticios) y empieza de cero.
choice /c SN /m "Continuar"
if errorlevel 2 exit /b 0
call "%~dp0cauce_demo.bat" --nuevo
