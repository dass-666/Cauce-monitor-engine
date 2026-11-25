# F3 · Cifrado y verificación de los respaldos HISTÓRICOS de Cauce Monitor (19-nov y F3-0) con 7-Zip AES-256.
#
# Lo ejecuta el administrador del equipo en SU PowerShell (sin elevar):
#   powershell -NoProfile -ExecutionPolicy Bypass -File respaldo_historicos_cifrado.ps1 -Confirmo
#
# NO borra ni modifica las carpetas históricas originales (no abre sus bases: abrir una base SQLite puede reescribir su
# -shm), ni los .7z fallidos, ni el respaldo cifrado del 25-nov. Solo borra sus propias copias temporales de prueba.
#
# Clave: se pide con Read-Host (asteriscos) dos veces y se compara; debe ser LA MISMA del respaldo del 25-nov.
# Se entrega a 7-Zip por su ENTRADA ESTÁNDAR (no como argumento: no aparece en la línea de comandos de ningún proceso).
# Antes de crear nada se comprueba el mecanismo: 7-Zip debe abrir el respaldo del 25-nov con esa clave por la entrada
# estándar. Así se demuestra a la vez que la clave es la correcta y que llega intacta.
param([switch]$Confirmo)
$ErrorActionPreference = "Continue"
$PY = "C:\cauce-msi\.venv\Scripts\python.exe"
$CIF = "D:\cauce-respaldos\cifrados"
$REF = "$CIF\cauce-msi_20261125_1009.7z"
$REF_SHA = "29BA19AFB041B5F0362852C88392D0E3E7378C89E3D2147990DB704231ED23F4"
$TS = Get-Date -Format "yyyyMMdd_HHmm"
$LOGDIR = "C:\Users\MSIGF\Cauce-AI-Center\f3_6\historicos_$TS"
New-Item -ItemType Directory -Force $LOGDIR | Out-Null
Start-Transcript -LiteralPath "$LOGDIR\consola.txt" -Force | Out-Null
$res = [ordered]@{ inicio = (Get-Date).ToString("s"); trabajos = [ordered]@{} }
$CLAVE = $null
function Fin($estado) {
  $script:CLAVE = $null
  $res.estado = $estado; $res.fin = (Get-Date).ToString("s")
  $res | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath "$LOGDIR\resultado.json" -Encoding UTF8
  Write-Host ""; Write-Host "ESTADO: $estado   (detalle en $LOGDIR\resultado.json)" -ForegroundColor $(if ($estado -eq "OK") { "Green" } else { "Red" })
  Stop-Transcript | Out-Null; exit 0
}
function PedirClave([string]$msg) {
  $sec = Read-Host $msg -AsSecureString
  $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
  try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}
function SieteZip([string]$argumentos, [string]$entrada) {
  # 7-Zip hereda la consola (se ve su progreso); la clave va solo por la entrada estándar.
  $si = New-Object System.Diagnostics.ProcessStartInfo
  $si.FileName = $script:SZ; $si.Arguments = $argumentos; $si.UseShellExecute = $false; $si.RedirectStandardInput = $true
  $pr = [System.Diagnostics.Process]::Start($si)
  $pr.StandardInput.Write($entrada); $pr.StandardInput.Close()
  $pr.WaitForExit(); return $pr.ExitCode
}
function Manifiesto([string]$raiz, [string]$csv) {
  $cuenta = 0
  Get-ChildItem -LiteralPath $raiz -Recurse -Force -File | ForEach-Object {
    $cuenta++
    [pscustomobject]@{ ruta = $_.FullName.Substring($raiz.Length).TrimStart('\'); tamano = $_.Length
                       sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }
  } | Export-Csv -LiteralPath $csv -NoTypeInformation -Encoding UTF8
  return $cuenta
}
function Mapa([string]$csv) { $m = @{}; Import-Csv -LiteralPath $csv | ForEach-Object { $m[$_.ruta] = $_.sha256 }; return $m }
function Protegida([string]$dir) { -not ((icacls $dir | Out-String) -match 'S-1-5-11|Usuarios autentificados|Authenticated Users|Todos|Everyone') }

# Manifiestos ORIGINALES de cada respaldo -> @{ ruta relativa a la carpeta del respaldo = sha256 }
function ManifiestoOriginal($t) {
  $m = @{}
  if ($t.tipo -eq "json19") {
    # 2026-11-19: MANIFIESTO_SHA256.json usa "cauce_rc1/..." (carpeta resultados_cauce_rc1) y ".env (valor no mostrado)" (env_copia.txt)
    foreach ($e in (Get-Content -LiteralPath "$($t.origen)\MANIFIESTO_SHA256.json" -Raw -Encoding UTF8 | ConvertFrom-Json)) {
      $r = $e.archivo
      if ($r -like ".env*") { $r = "env_copia.txt" } elseif ($r -like "cauce_rc1/*") { $r = "resultados_" + $r }
      $m[($r -replace '/', '\')] = $e.sha256.ToUpper()
    }
  } else {
    # F3-0: hashes_copia.csv (ruta relativa a cauce-msi\, sha256 de la copia)
    Import-Csv -LiteralPath "$($t.origen)\hashes_copia.csv" | ForEach-Object { $m["cauce-msi\" + $_.ruta] = $_.sha256.ToUpper() }
  }
  return $m
}
function ContrastarOriginal($m, [string]$raiz) {
  $dist = @(); $falt = @()
  foreach ($k in $m.Keys) {
    $p = Join-Path $raiz $k
    if (-not (Test-Path -LiteralPath $p)) { $falt += $k; continue }
    if ((Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash -ne $m[$k]) { $dist += $k }
  }
  return [ordered]@{ entradas = $m.Count; distintos = $dist; faltantes = $falt }
}

if (-not $Confirmo) { Fin "FALTA_CONFIRMO" }
$SZ = @("C:\Program Files\7-Zip\7z.exe", "C:\Program Files (x86)\7-Zip\7z.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $SZ) { Fin "FALTA_7ZIP" }
$TRABAJOS = @(
  [ordered]@{ nombre = "historico_2026-11-19"; origen = "C:\cauce-respaldos\2026-11-19_antes_de_7a3386d"; tipo = "json19"; copias = @() },
  [ordered]@{ nombre = "historico_f3_0_20261125_0627"; origen = "C:\cauce-respaldos\f3_0_20261125_0627"; tipo = "csvf30";
              copias = @("D:\cauce-respaldos\f3_0_20261125_0627") }
)
foreach ($t in $TRABAJOS) {
  if (-not (Test-Path -LiteralPath $t.origen)) { $res.falta = $t.origen; Fin "FALTA_ORIGEN" }
  if (Test-Path -LiteralPath "$CIF\$($t.nombre).7z") { $res.ya_existe = "$CIF\$($t.nombre).7z"; Fin "YA_EXISTE_EL_ARCHIVO (no se sobrescribe)" }
}
if (-not (Test-Path -LiteralPath $REF)) { Fin "FALTA_RESPALDO_25NOV" }
$res.ref_sha256_antes = (Get-FileHash -LiteralPath $REF -Algorithm SHA256).Hash
if ($res.ref_sha256_antes -ne $REF_SHA) { Fin "RESPALDO_25NOV_ALTERADO" }
if (-not (Protegida $CIF)) { Fin "DESTINO_NO_PROTEGIDO" }

# ---- 1. manifiestos originales (antes de cifrar) y manifiesto completo de cada origen
foreach ($t in $TRABAJOS) {
  $j = [ordered]@{ origen = $t.origen }
  $res.trabajos[$t.nombre] = $j
  Write-Host "[$($t.nombre)] comprobando el manifiesto original..."
  $mo = ManifiestoOriginal $t
  $j.original_antes = ContrastarOriginal $mo $t.origen
  foreach ($c in $t.copias) { $j["original_copia_" + ($c -replace '[:\\]', '_')] = ContrastarOriginal $mo $c }
  $j.archivos_origen = Manifiesto $t.origen "$LOGDIR\$($t.nombre)_origen.csv"
  $j.bytes_origen = (Get-ChildItem -LiteralPath $t.origen -Recurse -Force -File | Measure-Object Length -Sum).Sum
}
$malos = @($res.trabajos.Values | Where-Object { $_.original_antes.distintos.Count -or $_.original_antes.faltantes.Count })
if ($malos.Count) { Fin "MANIFIESTO_ORIGINAL_NO_CUADRA (no se cifró nada)" }

# ---- 2. clave: dos veces, comparada, y comprobada contra el respaldo del 25-nov por la entrada estándar
Write-Host ""
Write-Host "Escribe la MISMA clave que usaste para el respaldo del 25-nov (verás asteriscos)." -ForegroundColor Yellow
for ($n = 0; $n -lt 3 -and -not $CLAVE; $n++) {
  $a1 = PedirClave "Clave del respaldo"; $a2 = PedirClave "Repítela"
  if ($a1 -cne $a2) { Write-Host "No coinciden. Otra vez." -ForegroundColor Yellow }
  elseif ($a1 -cnotmatch '^[a-z0-9-]{16,}$') { Write-Host "Debe ser solo minúsculas, números y guiones (16 o más). Otra vez." -ForegroundColor Yellow }
  else { $CLAVE = $a1 }
  $a1 = $null; $a2 = $null
}
if (-not $CLAVE) { Fin "CLAVE_NO_VALIDA" }
Write-Host "Comprobando la clave y el mecanismo con el respaldo del 25-nov (7-Zip la recibe por la entrada estándar)..."
$res.prueba_mecanismo_codigo = SieteZip "t `"$REF`" -bso0 -bsp1" "$CLAVE`n"
if ($res.prueba_mecanismo_codigo -ne 0) {
  Fin "LA_CLAVE_NO_ABRE_EL_RESPALDO_25NOV (o 7-Zip no la acepta por la entrada estándar); no se creó nada" }

# ---- 3. cifrar, restaurar en temporal protegida, verificar, borrar la temporal
foreach ($t in $TRABAJOS) {
  $j = $res.trabajos[$t.nombre]
  $arch = "$CIF\$($t.nombre).7z"
  Write-Host ""; Write-Host "[$($t.nombre)] cifrando..." -ForegroundColor Cyan
  # 'a' con -p sin valor: 7-Zip pide la clave (y quizá su confirmación) y la lee de la entrada estándar.
  $j.siete_zip_crear = SieteZip "a -t7z -mhe=on -mx=5 -bsp1 -p `"$arch`" `"$($t.origen)\*`"" "$CLAVE`n$CLAVE`n"
  if ($j.siete_zip_crear -ne 0 -or -not (Test-Path -LiteralPath $arch)) { Fin "CIFRADO_FALLIDO_$($t.nombre)" }
  $j.archivo = $arch
  $j.archivo_bytes = (Get-Item -LiteralPath $arch).Length
  $j.archivo_sha256 = (Get-FileHash -LiteralPath $arch -Algorithm SHA256).Hash
  "$($j.archivo_sha256)  $($t.nombre).7z" | Set-Content -LiteralPath "$arch.sha256" -Encoding ASCII
  Copy-Item -LiteralPath "$LOGDIR\$($t.nombre)_origen.csv" -Destination "$CIF\$($t.nombre).manifiesto.csv"
  if ($t.tipo -eq "json19") { Copy-Item -LiteralPath "$($t.origen)\MANIFIESTO_SHA256.json" -Destination "$CIF\$($t.nombre).MANIFIESTO_ORIGINAL.json" }
  else { Copy-Item -LiteralPath "$($t.origen)\hashes_copia.csv" -Destination "$CIF\$($t.nombre).hashes_copia_ORIGINAL.csv" }

  $tmp = "D:\cauce-respaldos\prueba_hist_$($t.nombre)_$TS"
  New-Item -ItemType Directory -Force $tmp | Out-Null
  if (-not (Protegida $tmp)) { Fin "TEMPORAL_NO_PROTEGIDA_$($t.nombre)" }
  Write-Host "[$($t.nombre)] restaurando en $tmp ..." -ForegroundColor Cyan
  # 'x' sin -p: con cabeceras cifradas 7-Zip pide la clave y la lee de la entrada estándar (-p vacío sería clave vacía).
  $j.siete_zip_restaurar = SieteZip "x `"$arch`" `"-o$tmp`" -bso0 -bsp1" "$CLAVE`n"
  if ($j.siete_zip_restaurar -ne 0) { Fin "RESTAURACION_FALLIDA_$($t.nombre) (temporal conservada en $tmp)" }
  $j.archivos_restaurados = Manifiesto $tmp "$LOGDIR\$($t.nombre)_restaurado.csv"
  $o = Mapa "$LOGDIR\$($t.nombre)_origen.csv"; $d = Mapa "$LOGDIR\$($t.nombre)_restaurado.csv"
  $j.restaurado_distintos = @($o.Keys | Where-Object { $d.ContainsKey($_) -and $d[$_] -ne $o[$_] })
  $j.restaurado_faltantes = @($o.Keys | Where-Object { -not $d.ContainsKey($_) })
  $j.restaurado_sobrantes = @($d.Keys | Where-Object { -not $o.ContainsKey($_) })
  $j.original_restaurado = ContrastarOriginal (ManifiestoOriginal $t) $tmp
  # integridad de las bases RESTAURADAS (nunca se abren las originales)
  $j.integridad_bases = [ordered]@{}
  foreach ($db in @(Get-ChildItem -LiteralPath $tmp -Recurse -Force -File -Filter *.db | Where-Object { $_.FullName -notmatch '\\(\.venv|node_modules|\.browsers)\\' })) {
    $uri = "file:" + ($db.FullName -replace '\\', '/') + "?mode=ro"
    $j.integridad_bases[$db.FullName.Substring($tmp.Length + 1)] = "" + (& $PY -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1], uri=True).execute('PRAGMA integrity_check').fetchone()[0])" $uri 2>&1)
  }
  $basesMal = @($j.integridad_bases.Keys | Where-Object { $j.integridad_bases[$_] -ne "ok" })
  $ok = ($j.restaurado_distintos.Count -eq 0) -and ($j.restaurado_faltantes.Count -eq 0) -and ($j.restaurado_sobrantes.Count -eq 0) `
        -and ($j.original_restaurado.distintos.Count -eq 0) -and ($j.original_restaurado.faltantes.Count -eq 0) `
        -and ($j.integridad_bases.Count -gt 0) -and ($basesMal.Count -eq 0)
  if (-not $ok) { Fin "VERIFICACION_FALLIDA_$($t.nombre) (temporal conservada en $tmp)" }
  Remove-Item -LiteralPath $tmp -Recurse -Force
  $j.temporal_borrada = -not (Test-Path -LiteralPath $tmp)
  Write-Host "[$($t.nombre)] verificado: $($j.archivos_restaurados) archivos idénticos, $($j.integridad_bases.Count) bases ok." -ForegroundColor Green
}

# ---- 4. el respaldo del 25-nov sigue intacto; inventario de lo pendiente de retirar (no se toca)
$res.ref_sha256_despues = (Get-FileHash -LiteralPath $REF -Algorithm SHA256).Hash
$res.inventario_cifrados = @(Get-ChildItem -LiteralPath $CIF -File | ForEach-Object { "$($_.Name)  $($_.Length)" })
$res.pendiente_retirar = [ordered]@{
  carpetas_historicas = @("C:\cauce-respaldos\2026-11-19_antes_de_7a3386d", "C:\cauce-respaldos\f3_0_20261125_0627", "D:\cauce-respaldos\f3_0_20261125_0627") |
    Where-Object { Test-Path -LiteralPath $_ } | ForEach-Object {
      $f = @(Get-ChildItem -LiteralPath $_ -Recurse -Force -File); "$_  ($($f.Count) archivos, $(($f | Measure-Object Length -Sum).Sum) bytes)" }
  archivos_contrasena_antigua = @("C:\cauce-respaldos\2026-11-19_antes_de_7a3386d\env_copia.txt",
    "C:\cauce-respaldos\2026-11-19_antes_de_7a3386d\data\contrasena_admin_modo_normal.txt",
    "C:\cauce-respaldos\f3_0_20261125_0627\cauce-msi\.env", "C:\cauce-respaldos\f3_0_20261125_0627\cauce-msi\data\contrasena_admin_modo_normal.txt",
    "D:\cauce-respaldos\f3_0_20261125_0627\cauce-msi\.env", "D:\cauce-respaldos\f3_0_20261125_0627\cauce-msi\data\contrasena_admin_modo_normal.txt") |
    Where-Object { Test-Path -LiteralPath $_ }
  siete_zip_fallidos = @(Get-ChildItem -LiteralPath $CIF -File | Where-Object { $_.Name -like "cauce-msi_20261125_0949*" -or $_.Name -like "cauce-msi_20261125_0954*" } | ForEach-Object { $_.FullName })
  carpetas_prueba_vacias = @(Get-ChildItem -LiteralPath "D:\cauce-respaldos" -Directory | Where-Object { $_.Name -like "prueba_restauracion_*" } |
    ForEach-Object { "$($_.FullName)  ($(@(Get-ChildItem -LiteralPath $_.FullName -Recurse -Force -File).Count) archivos)" })
}
if ($res.ref_sha256_despues -ne $REF_SHA) { Fin "RESPALDO_25NOV_ALTERADO_AL_FINAL" }
Fin "OK"
