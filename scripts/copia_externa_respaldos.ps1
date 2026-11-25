# F3 · Copia de recuperación FUERA de la MSI de los tres respaldos cifrados de Cauce Monitor.
#
# Lo ejecuta el administrador en SU PowerShell (sin elevar). Dos pasos:
#   1) Identificar el destino (no copia nada):
#        powershell -NoProfile -ExecutionPolicy Bypass -File copia_externa_respaldos.ps1 -Listar
#   2) Copiar y verificar en la unidad elegida (letra y etiqueta deben coincidir con lo listado en el paso 1):
#        powershell -NoProfile -ExecutionPolicy Bypass -File copia_externa_respaldos.ps1 -Destino E: -Etiqueta "MI_USB" -Confirmo
#
# Copia SOLO los .7z y sus .sha256 (no los manifiestos: revelarían los nombres de archivo que el cifrado oculta) y un
# LEEME sin secretos. Comprueba los SHA-256 en origen (antes y después) y en destino, y abre cada copia con 7-Zip
# («t», prueba completa de descifrado y CRC) usando la clave que escribe el administrador, entregada por la entrada
# estándar (nunca como argumento). No borra ni modifica nada en C: ni en D:.
param([switch]$Listar, [string]$Destino, [string]$Etiqueta, [switch]$Confirmo)
$ErrorActionPreference = "Continue"
$CIF = "D:\cauce-respaldos\cifrados"
$VALIDOS = [ordered]@{
  "cauce-msi_20261125_1009.7z"      = "29BA19AFB041B5F0362852C88392D0E3E7378C89E3D2147990DB704231ED23F4"
  "historico_2026-11-19.7z"         = "405866AFD4DDC712B16B7D858713A5F20A9C08425798659462E5E22778985821"
  "historico_f3_0_20261125_0627.7z" = "F2927DA9BE59844B487A34C6FA57B0B25F8928C5D1C75B378E2B7970A7AA9E39"
}
$TS = Get-Date -Format "yyyyMMdd_HHmm"
$LOGDIR = "C:\Users\MSIGF\Cauce-AI-Center\f3_7\copia_$TS"
New-Item -ItemType Directory -Force $LOGDIR | Out-Null
Start-Transcript -LiteralPath "$LOGDIR\consola.txt" -Force | Out-Null
$res = [ordered]@{ inicio = (Get-Date).ToString("s") }
function Fin($estado) {
  $res.estado = $estado; $res.fin = (Get-Date).ToString("s")
  $res | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath "$LOGDIR\resultado.json" -Encoding UTF8
  Write-Host ""; Write-Host "ESTADO: $estado   (detalle en $LOGDIR\resultado.json)" -ForegroundColor $(if ($estado -like "OK*") { "Green" } else { "Red" })
  Stop-Transcript | Out-Null; exit 0
}
function PedirClave([string]$msg) {
  $sec = Read-Host $msg -AsSecureString
  $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
  try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}
function SieteZip([string]$argumentos, [string]$entrada) {
  $si = New-Object System.Diagnostics.ProcessStartInfo
  $si.FileName = $script:SZ; $si.Arguments = $argumentos; $si.UseShellExecute = $false; $si.RedirectStandardInput = $true
  $pr = [System.Diagnostics.Process]::Start($si)
  $pr.StandardInput.Write($entrada); $pr.StandardInput.Close()
  $pr.WaitForExit(); return $pr.ExitCode
}
function HashesOrigen {
  $h = [ordered]@{}
  foreach ($n in $VALIDOS.Keys) {
    $p = Join-Path $CIF $n
    $h[$n] = if (Test-Path -LiteralPath $p) { (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash } else { "FALTA" }
  }
  return $h
}
function Unidades {
  # Todas las unidades con letra salvo C: y D:, con el disco físico al que pertenecen
  Get-Volume | Where-Object { $_.DriveLetter -and $_.DriveLetter -notin @('C', 'D') } | ForEach-Object {
    $v = $_
    $disco = $null
    try { $disco = Get-Partition -DriveLetter $v.DriveLetter -ErrorAction Stop | Get-Disk -ErrorAction Stop } catch {}
    [pscustomobject]@{
      unidad = "$($v.DriveLetter):"; etiqueta = $v.FileSystemLabel; sistema_archivos = $v.FileSystem; tipo = "$($v.DriveType)"
      bus = if ($disco) { "$($disco.BusType)" } else { "?" }; modelo = if ($disco) { $disco.FriendlyName } else { "?" }
      serie = if ($disco) { "$($disco.SerialNumber)".Trim() } else { "?" }
      libre_GB = [math]::Round($v.SizeRemaining / 1GB, 2); total_GB = [math]::Round($v.Size / 1GB, 2)
    }
  }
}

$SZ = @("C:\Program Files\7-Zip\7z.exe", "C:\Program Files (x86)\7-Zip\7z.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $SZ) { Fin "FALTA_7ZIP" }

# ---- origen: los tres archivos válidos deben estar y tener su hash conocido
$res.origen_antes = HashesOrigen
$malos = @($VALIDOS.Keys | Where-Object { $res.origen_antes[$_] -ne $VALIDOS[$_] })
if ($malos.Count) { $res.origen_distintos = $malos; Fin "ORIGEN_ALTERADO_O_FALTA (no se copia nada)" }
$necesario = ($VALIDOS.Keys | ForEach-Object { (Get-Item -LiteralPath (Join-Path $CIF $_)).Length } | Measure-Object -Sum).Sum
$res.bytes_necesarios = $necesario

# ---- paso 1: listar destinos posibles
$res.unidades = @(Unidades)
if ($Listar -or -not $Destino) {
  Write-Host ""; Write-Host "Los 3 archivos válidos están intactos en D: (hashes verificados). Necesitan $([math]::Round($necesario / 1MB)) MB." -ForegroundColor Green
  if ($res.unidades.Count) { $res.unidades | Format-Table -AutoSize | Out-String | Write-Host }
  else { Write-Host "No hay ninguna unidad aparte de C: y D:. Conecta la memoria USB y repite." -ForegroundColor Yellow }
  Fin $(if ($res.unidades.Count) { "OK_LISTADO" } else { "SIN_DISPOSITIVO_EXTERNO" })
}

# ---- paso 2: identificar y comprobar el destino elegido
if (-not $Confirmo) { Fin "FALTA_CONFIRMO" }
$letra = $Destino.TrimEnd('\').TrimEnd(':').ToUpper()
if ($letra -in @('C', 'D') -or $letra.Length -ne 1) { Fin "DESTINO_NO_PERMITIDO (debe ser una unidad externa, no C: ni D:)" }
$u = $res.unidades | Where-Object { $_.unidad -eq "${letra}:" }
if (-not $u) { Fin "DESTINO_NO_ENCONTRADO" }
$res.destino = $u
if ($u.etiqueta -cne $Etiqueta) { Fin "LA_ETIQUETA_NO_COINCIDE (se esperaba '$Etiqueta', la unidad tiene '$($u.etiqueta)')" }
if ($u.libre_GB * 1GB -lt $necesario * 1.1) { Fin "ESPACIO_INSUFICIENTE" }
if ($u.bus -ne "USB" -and $u.tipo -ne "Removable") { Write-Host "Aviso: la unidad no se identifica como USB/extraíble (bus $($u.bus)). Se continúa porque la eligió el administrador." -ForegroundColor Yellow }
$dir = "${letra}:\cauce-respaldos-cifrados\$TS"
if (Test-Path -LiteralPath $dir) { Fin "LA_CARPETA_DESTINO_YA_EXISTE" }
New-Item -ItemType Directory -Force $dir | Out-Null

# ---- copiar
foreach ($n in $VALIDOS.Keys) {
  Write-Host "Copiando $n ..."
  Copy-Item -LiteralPath (Join-Path $CIF $n) -Destination $dir
  if (Test-Path -LiteralPath (Join-Path $CIF "$n.sha256")) { Copy-Item -LiteralPath (Join-Path $CIF "$n.sha256") -Destination $dir }
}
@"
Respaldos CIFRADOS de Cauce Monitor (7-Zip, AES-256, nombres de archivo ocultos). Copiados el $(Get-Date -Format "yyyy-MM-dd HH:mm").
Para abrirlos hace falta 7-Zip (https://www.7-zip.org) y la CLAVE DEL RESPALDO, que NO está en esta memoria.
SHA-256 esperados:
$(($VALIDOS.Keys | ForEach-Object { "  $($VALIDOS[$_])  $_" }) -join "`r`n")
Comprobar:  Get-FileHash <archivo> -Algorithm SHA256        Abrir:  7z x <archivo> -o<carpeta>
cauce-msi_20261125_1009.7z = instalación completa del 25-nov (tras la rotación de la contraseña).
historico_*.7z = respaldos anteriores; contienen la contraseña de administrador ANTIGUA: tras restaurarlos, fijar una nueva
con scripts\rotar_contrasena_admin.py emergencia --confirmo.
"@ | Set-Content -LiteralPath "$dir\LEEME.txt" -Encoding UTF8

# ---- comparar SHA-256 de destino
$res.destino_hashes = [ordered]@{}
foreach ($n in $VALIDOS.Keys) { $res.destino_hashes[$n] = (Get-FileHash -LiteralPath (Join-Path $dir $n) -Algorithm SHA256).Hash }
$distintos = @($VALIDOS.Keys | Where-Object { $res.destino_hashes[$_] -ne $VALIDOS[$_] })
if ($distintos.Count) { $res.destino_distintos = $distintos; Fin "COPIA_CON_HASH_DISTINTO (la copia en $dir no es fiable)" }

# ---- prueba de apertura en el destino con la clave del administrador (entrada estándar)
Write-Host ""; Write-Host "Prueba de apertura de las copias: escribe la CLAVE DEL RESPALDO (verás asteriscos)." -ForegroundColor Yellow
$clave = $null
for ($n = 0; $n -lt 3 -and -not $clave; $n++) {
  $k = PedirClave "Clave del respaldo"
  Write-Host "Probando con cauce-msi_20261125_1009.7z ..."
  if ((SieteZip "t `"$dir\cauce-msi_20261125_1009.7z`" -bso0 -bsp1" "$k`n") -eq 0) { $clave = $k } else { Write-Host "No abre. ¿Error al escribir? Otra vez." -ForegroundColor Yellow }
  $k = $null
}
if (-not $clave) { Fin "LA_CLAVE_NO_ABRE_LA_COPIA (los hashes sí coinciden: revisa la clave guardada)" }
$res.prueba_apertura = [ordered]@{ "cauce-msi_20261125_1009.7z" = 0 }
foreach ($n in @("historico_2026-11-19.7z", "historico_f3_0_20261125_0627.7z")) {
  Write-Host "Probando $n ..."
  $res.prueba_apertura[$n] = SieteZip "t `"$dir\$n`" -bso0 -bsp1" "$clave`n"
}
$clave = $null
if (@($res.prueba_apertura.Values | Where-Object { $_ -ne 0 }).Count) { Fin "ALGUNA_COPIA_NO_ABRE" }

# ---- el origen sigue intacto
$res.origen_despues = HashesOrigen
if (@($VALIDOS.Keys | Where-Object { $res.origen_despues[$_] -ne $VALIDOS[$_] }).Count) { Fin "ORIGEN_ALTERADO_AL_FINAL" }
$res.carpeta_copia = $dir
Write-Host ""; Write-Host "Copia verificada en $dir. Expulsa la memoria con 'Quitar hardware de forma segura' antes de desconectarla." -ForegroundColor Green
Fin "OK"
