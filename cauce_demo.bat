@echo off
rem DEMOSTRACION INTEGRAL de Cauce Monitor (laboratorio, datos FICTICIOS, solo este equipo).
rem Arranca portal de laboratorio + Monitor con Forms (ventana visible) y abre el navegador.
rem Guia de clics: ..\demo-integral\GUIA_DEMO.txt - Para empezar de cero: cauce_demo.bat --nuevo
title Cauce Monitor - version candidata (deja esta ventana abierta)
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
if not exist .venv\Scripts\python.exe (
  echo Falta la instalacion de Monitor en esta carpeta.
  pause
  exit /b 1
)
.venv\Scripts\python.exe scripts\demo_integral.py --abrir %*
if errorlevel 1 pause
