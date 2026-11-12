# Instalación en Windows (PowerShell). Ejecuta:  powershell -ExecutionPolicy Bypass -File .\instalar.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) { $python = "py" ; $pyArgs = @("-3") } else { $python = "python" ; $pyArgs = @() }
& $python @pyArgs --version
if ($LASTEXITCODE -ne 0) { Write-Error "No se encontró Python 3.11+. Instálalo desde https://www.python.org/downloads/ (marca 'Add to PATH')." }
if (-not (Test-Path ".venv")) { & $python @pyArgs -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path ".env")) {
  # La contraseña se pasa por variable de entorno a Python (evita problemas con caracteres especiales).
  $env:MON_PWD = Read-Host "Contraseña para el usuario 'admin' (mín. 10 caracteres, letras y números)"
  & .\.venv\Scripts\python.exe scripts\crear_env.py
  if ($LASTEXITCODE -ne 0) { Remove-Item Env:MON_PWD; exit 1 }
  Remove-Item Env:MON_PWD
}
Write-Host "Instalación terminada. Inicia con:  .\iniciar.bat"
