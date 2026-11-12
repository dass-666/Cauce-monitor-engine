# Actualizar a una versión nueva SIN tocar la instalación anterior (PowerShell 5.1 o 7).
#
# Cómo se usa (recomendado, "lado a lado"):
#   1. Cierra el motor (Ctrl+C en la ventana de iniciar.bat).
#   2. Descomprime la versión nueva en una carpeta NUEVA, por ejemplo C:\motor-monitoreo-1.2
#   3. En esa carpeta nueva ejecuta:
#        powershell -ExecutionPolicy Bypass -File .\actualizar.ps1 -Anterior C:\motor-monitoreo
#
# Qué hace: comprueba que el motor está parado, COPIA (no mueve) data\ y .env de la carpeta
# anterior verificando cada base, instala dependencias, migra la base (con respaldo previo)
# y ejecuta el autodiagnóstico. La carpeta anterior no se modifica en ningún momento:
# volver atrás es tan sencillo como seguir usando su iniciar.bat.
#
# Si en cambio descomprimiste la versión nueva ENCIMA de la anterior, no uses este script:
# ejecuta instalar.ps1, que respalda data\ y .env antes de cambiar nada.
param([string]$Anterior, [switch]$NoInteractivo)
$ErrorActionPreference = "Continue"
Set-Location -LiteralPath $PSScriptRoot
. (Join-Path $PSScriptRoot "scripts\comun.ps1")

if (-not $Anterior) {
  if ($NoInteractivo) { Fallo "Falta -Anterior <carpeta de la instalación anterior>." }
  Write-Host "Escribe la carpeta donde está instalada la versión anterior (la que tiene iniciar.bat)."
  $Anterior = Read-Host "Carpeta anterior (por ejemplo C:\motor-monitoreo)"
}
$Anterior = $Anterior.Trim().Trim('"')
if (-not (Test-Path -LiteralPath $Anterior -PathType Container)) {
  Fallo "No existe la carpeta '$Anterior'." "Comprueba la ruta (puedes copiarla de la barra de direcciones del Explorador)."
}

Write-Paso "Buscando Python"
$py = Find-Python
if (-not $py) { Fallo "No se encontró Python 3.11 a 3.14." "Instala Python 3.13 desde https://www.python.org/downloads/ y repite." }
$pyExe = $py[0]; $pyArgs = @($py | Select-Object -Skip 1)

Write-Paso "Copiando y verificando los datos de la instalación anterior"
$code = Invoke-Externo $pyExe ($pyArgs + @("-I", "scripts\preparar.py", "copiar-datos", "--desde", $Anterior))
if ($code -eq 2) {
  Fallo "No se copiaron los datos (motivo arriba)." "La instalación anterior no se ha modificado. Resuelve el motivo y repite."
}

Write-Paso "Instalando la versión nueva con los datos copiados"
# Tabla (hashtable) y no lista: con una lista de textos PowerShell no activa los modificadores.
$instArgs = @{ NoInteractivo = $true; DesdeActualizacion = $true }
if ((Test-Path ".env") -and (Select-String -Path ".env" -Pattern "^MONITOREO_DB_URL=postgresql" -Quiet)) { $instArgs.ConPostgres = $true }
& (Join-Path $PSScriptRoot "instalar.ps1") @instArgs
$code = $LASTEXITCODE

Write-Host ""
if ($code -eq 0) {
  Write-Host "Actualización terminada y comprobada." -ForegroundColor Green
  Write-Host "Inicia la versión nueva con  .\iniciar.bat  (en ESTA carpeta)." -ForegroundColor Green
} else {
  Write-Host "La actualización NO terminó bien (detalle arriba)." -ForegroundColor Red
}
Write-Host ""
Write-Host "Para volver a la versión anterior en cualquier momento:" -ForegroundColor Yellow
Write-Host "  1. Cierra el motor nuevo si está abierto." -ForegroundColor Yellow
Write-Host "  2. Ejecuta  $Anterior\iniciar.bat  (sus datos son los de antes de actualizar)." -ForegroundColor Yellow
Write-Host "  Con PostgreSQL la base es compartida: para volver atrás hay que restaurar el respaldo" -ForegroundColor Yellow
Write-Host "  de data\respaldos\ (ver README, apartado Respaldos)." -ForegroundColor Yellow
exit $code
