# Instalación en Windows (PowerShell 5.1 o 7). Ejecuta en la carpeta del proyecto:
#     powershell -ExecutionPolicy Bypass -File .\instalar.ps1
#     powershell -ExecutionPolicy Bypass -File .\instalar.ps1 -ConPostgres    (si usarás PostgreSQL)
#     powershell -ExecutionPolicy Bypass -File .\instalar.ps1 -Produccion     (uso real: ver docs\PRODUCCION.md)
#     -NoInteractivo: no pregunta nada; la contraseña se toma de $env:MONITOREO_ADMIN_PASSWORD
#                     (para instalaciones automáticas y para las pruebas de GitHub Actions).
#
# Se puede ejecutar las veces que haga falta: conserva .env y los datos, y antes de tocar nada
# respalda data\ y .env en respaldos-instalacion\<fecha>\. "-ExecutionPolicy Bypass" afecta solo
# a esta ejecución: no cambia ninguna política de Windows.
#
# Nota técnica: PowerShell NO se detiene cuando falla un programa externo (python, pip);
# por eso se comprueba el código de salida de cada uno. Se usa "Continue" para que los
# mensajes de error de Python al probar versiones no corten el script en PowerShell 5.1.
param([switch]$ConPostgres, [switch]$Produccion, [switch]$NoInteractivo, [switch]$DesdeActualizacion)
$ErrorActionPreference = "Continue"
Set-Location -LiteralPath $PSScriptRoot
. (Join-Path $PSScriptRoot "scripts\comun.ps1")
Start-Registro "instalacion"
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

# 1) Python
Write-Paso "1/7 Buscando Python 3.11 a 3.14"
$py = Find-Python
if (-not $py) {
  Fallo "No se encontró Python 3.11, 3.12, 3.13 o 3.14." ("Instala Python 3.13 desde https://www.python.org/downloads/ " +
        "(marca 'Add python.exe to PATH'), cierra esta ventana, abre otra y vuelve a ejecutar el instalador.")
}
$pyExe = $py[0]; $pyArgs = @($py | Select-Object -Skip 1)
$ver = & $pyExe @pyArgs -c "import sys; print('%d.%d.%d' % sys.version_info[:3])"
Write-Host "Usando Python $ver ($pyExe $pyArgs)"

# 2) Comprobación previa (solo biblioteca estándar: funciona antes de instalar nada)
Write-Paso "2/7 Comprobando requisitos del equipo"
$code = Invoke-Externo $pyExe ($pyArgs + @("-I", "scripts\preparar.py", "requisitos"))
if ($code -eq 2) { Fallo "El equipo no cumple un requisito (marcado como CRÍTICO arriba)." "Resuélvelo y vuelve a ejecutar el instalador." }

# 3) Respaldo de lo existente antes de cambiar nada
if (-not $DesdeActualizacion) {
  Write-Paso "3/7 Respaldando datos existentes"
  $code = Invoke-Externo $pyExe ($pyArgs + @("-I", "scripts\preparar.py", "respaldar"))
  if ($code -ne 0) { Fallo "No se pudo hacer el respaldo. No se ha cambiado nada." "Cierra el motor si está abierto y repite." }
} else {
  Write-Paso "3/7 Respaldo: no hace falta (los datos se acaban de copiar; la instalación anterior queda intacta)"
}

# 4) Entorno virtual (.venv): una carpeta propia con las dependencias, sin tocar el Python del sistema
Write-Paso "4/7 Preparando el entorno virtual .venv"
if (Test-Path $venvPy) {
  & $venvPy -c "import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 14) else 1)" 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Fallo "La carpeta .venv existe pero está dañada o usa un Python no compatible." `
          "Bórrala (no contiene datos, solo programas) y vuelve a ejecutar:  Remove-Item -Recurse -Force .venv"
  }
} else {
  $code = Invoke-Externo $pyExe ($pyArgs + @("-m", "venv", ".venv"))
  if ($code -ne 0) { Fallo "No se pudo crear el entorno virtual .venv" "Comprueba que la carpeta no está en una ruta protegida." }
}

# 5) Dependencias (solo paquetes precompilados: si falta alguno, falla con un mensaje claro
#    en lugar de intentar compilar, lo que en Windows necesitaría herramientas de C/Rust).
Write-Paso "5/7 Instalando dependencias (requiere Internet la primera vez)"
$code = Invoke-Externo $venvPy @("-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "pip")
if ($code -ne 0) { Fallo "No se pudo actualizar pip." "¿Hay conexión a Internet? Si usas un proxy corporativo, configura HTTPS_PROXY." }
$req = "requirements.txt"
if ($ConPostgres -or ((Test-Path ".env") -and (Select-String -Path ".env" -Pattern "^MONITOREO_DB_URL=postgresql" -Quiet))) {
  $req = "requirements-postgres.txt"
}
$code = Invoke-Externo $venvPy @("-m", "pip", "install", "--disable-pip-version-check", "--only-binary=:all:", "-r", $req)
if ($code -ne 0) { Fallo "No se pudieron instalar las dependencias de $req." "Revisa el mensaje de pip arriba. Vuelve a ejecutar el instalador cuando haya conexión." }

# 6) Archivo .env (la contraseña no se muestra ni se pasa por la línea de comandos)
Write-Paso "6/7 Configuración (.env)"
if (Test-Path ".env") {
  Write-Host "Ya existe .env: se conserva sin cambios."
} else {
  if ($Produccion) { $env:MON_ENTORNO = "produccion" }
  $ok = $false
  if ($NoInteractivo) {
    $env:MON_PWD = $env:MONITOREO_ADMIN_PASSWORD
    if (-not $env:MON_PWD) { Fallo "Con -NoInteractivo hay que definir la variable MONITOREO_ADMIN_PASSWORD." }
    $ok = ((Invoke-Externo $venvPy @("scripts\crear_env.py")) -eq 0)
    Remove-Item Env:MON_PWD -ErrorAction SilentlyContinue
  } else {
    $min = if ($Produccion) { 12 } else { 10 }
    for ($i = 0; $i -lt 3 -and -not $ok; $i++) {
      $s1 = Read-Host "Contraseña para el usuario 'admin' (mín. $min caracteres, letras y números)" -AsSecureString
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
      # -cne distingue mayúsculas y minúsculas (-ne no lo hace en PowerShell).
      if ($p1 -cne $p2) { Write-Host "Las contraseñas no coinciden." -ForegroundColor Yellow; continue }
      # Se pasa por variable de entorno (no en la línea de comandos) y se borra al terminar.
      $env:MON_PWD = $p1
      $ok = ((Invoke-Externo $venvPy @("scripts\crear_env.py")) -eq 0)
      Remove-Item Env:MON_PWD -ErrorAction SilentlyContinue
    }
    $p1 = $null; $p2 = $null
  }
  Remove-Item Env:MON_ENTORNO -ErrorAction SilentlyContinue
  if (-not $ok) { Fallo "No se creó .env." "Vuelve a ejecutar el instalador." }
}

# 7) Base de datos y comprobación final
Write-Paso "7/7 Preparando la base de datos y comprobando la instalación"
$code = Invoke-Externo $venvPy @("-m", "monitoreo.migrate", "actualizar")
if ($code -ne 0) {
  Fallo "No se pudo crear o actualizar la base de datos." ("Tus datos no se han perdido: hay copia en respaldos-instalacion\ " +
        "y en data\respaldos\. Envía el registro indicado abajo a quien te dé soporte.")
}
$code = Invoke-Externo $venvPy @("-m", "monitoreo.diagnostico", "--motor-detenido")
Stop-Registro
Write-Host ""
if ($code -eq 2) {
  Write-Host "Instalación terminada, PERO el autodiagnóstico encontró problemas CRÍTICOS (ver arriba)." -ForegroundColor Red
  Write-Host "Corrígelos (cada uno indica qué hacer) antes de usar el motor." -ForegroundColor Red
  exit 2
}
Write-Host "Instalación terminada y comprobada." -ForegroundColor Green
Write-Host "Inicia con:  .\iniciar.bat      Diagnóstico:  .\diagnostico.bat      Pruebas:  .\pruebas.bat" -ForegroundColor Green
exit 0
