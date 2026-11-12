# Funciones compartidas por instalar.ps1 y actualizar.ps1 (se cargan con ". scripts\comun.ps1").
# No cambia ninguna política de Windows: se ejecuta con -ExecutionPolicy Bypass solo para esta llamada.

# Acentos: Python escribe en UTF-8 y la consola lee en UTF-8 (solo en esta ventana; no cambia
# ninguna configuración de Windows). Sin esto, "CRÍTICO" se veía como "CR�TICO". Verificado en
# GitHub Actions (windows-latest) con PowerShell 5.1 y 7.
$env:PYTHONIOENCODING = "utf-8"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Write-Paso([string]$msg) {
  Write-Host ""
  Write-Host "== $msg" -ForegroundColor Cyan
}

function Fallo([string]$msg, [string]$recuperacion = "") {
  Write-Host ""
  Write-Host "ERROR: $msg" -ForegroundColor Red
  if ($recuperacion) {
    Write-Host ""
    Write-Host "Qué hacer:" -ForegroundColor Yellow
    Write-Host $recuperacion -ForegroundColor Yellow
  }
  if ($script:Registro) {
    Write-Host ""
    Write-Host "El detalle completo quedó en: $script:Registro" -ForegroundColor Yellow
    try { Stop-Transcript | Out-Null } catch { }
  }
  exit 1
}

# Nota: la coma de "return ,@(...)" evita que PowerShell convierta una lista de un solo
# elemento en un texto suelto (entonces $py[0] sería la primera LETRA, no el programa).
# Busca un Python compatible (3.11 a 3.14). Devuelve @(ejecutable, argumentos) o $null.
# El lanzador "py" permite elegir versión; "python" puede ser el acceso directo de Microsoft
# Store (no es Python real) y la prueba lo descarta.
function Find-Python {
  $check = "import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 14) else 1)"
  if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($v in @("3.13", "3.12", "3.11", "3.14")) {
      & py "-$v" -c $check 2>$null | Out-Null
      if ($LASTEXITCODE -eq 0) { return ,@("py", "-$v") }
    }
  }
  if (Get-Command python -ErrorAction SilentlyContinue) {
    & python -c $check 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { return ,@("python") }
  }
  return $null
}

function Start-Registro([string]$nombre) {
  $dir = Join-Path $PSScriptRoot "..\data\logs"
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  $script:Registro = (Join-Path (Resolve-Path $dir) ("$nombre-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log"))
  try { Start-Transcript -Path $script:Registro | Out-Null } catch { $script:Registro = $null }
}

function Stop-Registro {
  if ($script:Registro) { try { Stop-Transcript | Out-Null } catch { } }
}

# Ejecuta un programa externo mostrando su salida y dejándola en el registro (Start-Transcript
# de PowerShell 5.1 no guarda la salida de programas externos si no pasa por Out-Host).
# Devuelve el código de salida del programa.
function Invoke-Externo([string]$exe, [string[]]$argumentos) {
  & $exe @argumentos 2>&1 | ForEach-Object { "$_" } | Out-Host
  return $LASTEXITCODE
}
