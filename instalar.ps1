# Instalación en Windows (PowerShell 5.1 o 7). Ejecuta en la carpeta del proyecto:
#     powershell -ExecutionPolicy Bypass -File .\instalar.ps1
#     powershell -ExecutionPolicy Bypass -File .\instalar.ps1 -ConPostgres   (si usarás PostgreSQL)
#
# Nota técnica: PowerShell NO se detiene cuando falla un programa externo (python, pip);
# por eso se comprueba $LASTEXITCODE después de cada uno. Se usa "Continue" para que los
# mensajes de error de Python al probar versiones no corten el script en PowerShell 5.1.
param([switch]$ConPostgres)
$ErrorActionPreference = "Continue"
Set-Location -LiteralPath $PSScriptRoot

function Fallo([string]$msg) {
  Write-Host ""
  Write-Host "ERROR: $msg" -ForegroundColor Red
  exit 1
}

if ($PSScriptRoot -match "OneDrive") {
  Write-Host "AVISO: esta carpeta está dentro de OneDrive. Si OneDrive sincroniza la base SQLite mientras" -ForegroundColor Yellow
  Write-Host "el motor la usa, puede bloquearse o dañarse. Recomendado: mover la carpeta a C:\motor-monitoreo" -ForegroundColor Yellow
}

# 1) Buscar un Python compatible (3.11 a 3.14). El lanzador "py" permite elegir la versión.
$check = "import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 14) else 1)"
$python = $null
$pyArgs = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
  foreach ($v in @("3.13", "3.12", "3.11", "3.14")) {
    & py "-$v" -c $check 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $python = "py"; $pyArgs = @("-$v"); break }
  }
}
if (-not $python -and (Get-Command python -ErrorAction SilentlyContinue)) {
  # Ojo: "python" puede ser el acceso directo de Microsoft Store (no es Python real); la prueba lo descarta.
  & python -c $check 2>$null | Out-Null
  if ($LASTEXITCODE -eq 0) { $python = "python" }
}
if (-not $python) {
  Fallo ("No se encontró Python 3.11, 3.12, 3.13 o 3.14. Instala Python 3.13 desde " +
         "https://www.python.org/downloads/ (marca 'Add python.exe to PATH') y vuelve a ejecutar este script.")
}
$ver = & $python @pyArgs -c "import sys; print('%d.%d.%d' % sys.version_info[:3])"
Write-Host "Usando Python $ver"

# 2) Entorno virtual (.venv)
if (Test-Path ".venv\Scripts\python.exe") {
  & .\.venv\Scripts\python.exe -c $check 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Fallo "La carpeta .venv existe pero está dañada o usa un Python no compatible. Bórrala y vuelve a ejecutar: Remove-Item -Recurse -Force .venv"
  }
} else {
  & $python @pyArgs -m venv .venv
  if ($LASTEXITCODE -ne 0) { Fallo "No se pudo crear el entorno virtual .venv" }
}

# 3) Dependencias (solo paquetes precompilados: si falta alguno, falla con un mensaje claro
#    en lugar de intentar compilar, lo que en Windows necesitaría herramientas de C/Rust).
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { Fallo "No se pudo actualizar pip. ¿Hay conexión a Internet? ¿Proxy corporativo?" }
$req = "requirements.txt"
if ($ConPostgres -or ((Test-Path ".env") -and (Select-String -Path ".env" -Pattern "^MONITOREO_DB_URL=postgresql" -Quiet))) {
  $req = "requirements-postgres.txt"
}
& .\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r $req
if ($LASTEXITCODE -ne 0) { Fallo "No se pudieron instalar las dependencias de $req (revisa el mensaje de pip arriba)." }

# 4) Archivo .env con la contraseña del administrador (no se muestra en pantalla)
if (-not (Test-Path ".env")) {
  $ok = $false
  for ($i = 0; $i -lt 3 -and -not $ok; $i++) {
    $s1 = Read-Host "Contraseña para el usuario 'admin' (mín. 10 caracteres, letras y números)" -AsSecureString
    $s2 = Read-Host "Repite la contraseña" -AsSecureString
    $b1 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s1)
    $b2 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s2)
    try {
      $p1 = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b1)
      $p2 = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b2)
    } finally {
      [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b1)
      [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b2)
    }
    if ($p1 -ne $p2) { Write-Host "Las contraseñas no coinciden." -ForegroundColor Yellow; continue }
    # Se pasa por variable de entorno (no en la línea de comandos) y se borra al terminar.
    $env:MON_PWD = $p1
    & .\.venv\Scripts\python.exe scripts\crear_env.py
    if ($LASTEXITCODE -eq 0) { $ok = $true }
    Remove-Item Env:MON_PWD -ErrorAction SilentlyContinue
  }
  $p1 = $null; $p2 = $null
  if (-not $ok) { Fallo "No se creó .env. Vuelve a ejecutar el instalador." }
}
Write-Host ""
Write-Host "Instalación terminada. Inicia con:  .\iniciar.bat     Pruebas:  .\pruebas.bat" -ForegroundColor Green
