# F3 · Respaldo CIFRADO de la instalación de Cauce Monitor (7-Zip, AES-256, nombres cifrados) y prueba de restauración.
#
# Lo ejecuta el administrador del equipo en SU PowerShell (sin elevar). La clave del cifrado la escribe él cuando
# 7-Zip la pide (no se ve en pantalla, no se pasa por la línea de comandos y no queda en el registro).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File respaldo_cifrado_msi.ps1 -Confirmo
#
# Pasos (se detiene en el primero que falle, sin borrar nada salvo su propia copia de prueba):
#   1. Monitor detenido; contraseña nueva intacta (tabla users igual que tras la rotación) y tablas protegidas
#      sin cambios; la contraseña ANTIGUA no aparece en la instalación.
#   2. Manifiesto SHA-256 de la instalación.
#   3. Archivo .7z cifrado en D:\cauce-respaldos\cifrados\ (7-Zip pide la clave dos veces) y su SHA-256.
#   4. Restauración en D:\cauce-respaldos\prueba_restauracion_<fecha> (carpeta protegida; 7-Zip pide la clave):
#      manifiesto idéntico, integridad de todas las bases, base igual tabla a tabla, sin la contraseña antigua.
#   5. Borra SOLO la copia de prueba sin cifrar.
#   6. Arranca Monitor sin portal, comprueba /api/health, lo detiene y comprueba que los datos protegidos no cambiaron.
param([switch]$Confirmo)
$ErrorActionPreference = "Continue"
$I = "C:\cauce-msi"
$PY = "$I\.venv\Scripts\python.exe"
$W = "C:\Users\MSIGF\Cauce-AI-Center\f3_5"
$T = "$W\rotar_contrasena_admin.py"
$S4 = "C:\Users\MSIGF\Cauce-AI-Center\f3_4\salida"
$ENV_ANTIGUO = "C:\cauce-respaldos\f3_0_20261125_0627\cauce-msi\.env"
$DEST = "D:\cauce-respaldos\cifrados"
$TS = Get-Date -Format "yyyyMMdd_HHmm"
$L = "$W\respaldo_$TS"
$S = "$L\salida"
$R = "D:\cauce-respaldos\prueba_restauracion_$TS"
$ARCH = "$DEST\cauce-msi_$TS.7z"
$PROTEGIDAS = "expedientes","expediente_eventos","reservaciones","consentimientos","consentimiento_eventos","atenciones",
              "importaciones","importacion_filas","capturas_manuales","planes_diarios","plan_elementos","operators",
              "requests","request_events","assignments"
New-Item -ItemType Directory -Force $L | Out-Null
Start-Transcript -LiteralPath "$L\consola.txt" -Force | Out-Null
$env:PYTHONIOENCODING = "utf-8"
$res = [ordered]@{ inicio = (Get-Date).ToString("s"); archivo = $ARCH }
function Fin($estado) {
  $res.estado = $estado; $res.fin = (Get-Date).ToString("s")
  $res | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath "$L\resultado.json" -Encoding UTF8
  Write-Host ""; Write-Host "ESTADO: $estado   (detalle en $L\resultado.json)" -ForegroundColor $(if ($estado -eq "OK") { "Green" } else { "Red" })
  Stop-Transcript | Out-Null; exit 0
}
function Herr([string[]]$a) { & $PY $T @a | ForEach-Object { Write-Host $_ }; return $LASTEXITCODE }
function Puerto8000 { [bool](Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) }
function ProcCauce { @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'cauce-msi' -and $_.ProcessId -ne $PID }) }
function Manifiesto($raiz, $csv) {
  $n = 0
  Get-ChildItem -LiteralPath $raiz -Recurse -Force -File | ForEach-Object {
    $n++
    [pscustomobject]@{ ruta = $_.FullName.Substring($raiz.Length).TrimStart('\'); tamano = $_.Length
                       sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }
  } | Export-Csv -LiteralPath $csv -NoTypeInformation -Encoding UTF8
  return $n
}

if (-not $Confirmo) { Fin "FALTA_CONFIRMO" }
$SZ = @("C:\Program Files\7-Zip\7z.exe", "C:\Program Files (x86)\7-Zip\7z.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $SZ) { Fin "FALTA_7ZIP" }
$res.siete_zip = (& $SZ | Select-Object -First 2 | Where-Object { $_ }) -join " "
foreach ($p in $PY, $T, $ENV_ANTIGUO, "$S4\instantanea_despues.json") { if (-not (Test-Path $p)) { $res.falta = $p; Fin "FALTA_ARCHIVO" } }

# ---- 1. estado previo
if ((Puerto8000) -or (ProcCauce).Count) { Fin "MONITOR_EN_MARCHA" }
if ((Herr @("--instalacion", $I, "--salida", $S, "instantanea", "ahora")) -ne 0) { Fin "INSTANTANEA_FALLIDA" }
$ahora = (Get-Content "$S\instantanea_ahora.json" -Raw -Encoding UTF8 | ConvertFrom-Json).tablas
$tras = (Get-Content "$S4\instantanea_despues.json" -Raw -Encoding UTF8 | ConvertFrom-Json).tablas
$res.users_igual_que_tras_rotacion = ($ahora.users.sha256 -eq $tras.users.sha256)
$res.protegidas_cambiadas_desde_rotacion = @($PROTEGIDAS | Where-Object { $ahora.$_.sha256 -ne $tras.$_.sha256 })
$res.env_sin_clave_admin = -not (Select-String -LiteralPath "$I\.env" -Pattern '^\s*MONITOREO_ADMIN_PASSWORD\s*=' -Quiet)
$res.sin_txt_de_credencial = -not ((Test-Path "$I\data\contrasena_admin_modo_normal.txt") -or (Test-Path "$I\data\CREDENCIAL_INICIAL.txt"))
Herr @("--instalacion", $I, "--salida", $S, "escanear", "--valor-desde", $ENV_ANTIGUO, $I) | Out-Null
$esc = Get-ChildItem "$S\escaneo_*.json" | Sort-Object LastWriteTime | Select-Object -Last 1
$res.antigua_en_instalacion = @((Get-Content $esc.FullName -Raw -Encoding UTF8 | ConvertFrom-Json).archivos_con_la_contrasena).Count
if (-not $res.users_igual_que_tras_rotacion -or $res.protegidas_cambiadas_desde_rotacion.Count -or -not $res.env_sin_clave_admin `
    -or -not $res.sin_txt_de_credencial -or $res.antigua_en_instalacion) { Fin "ESTADO_PREVIO_INESPERADO" }

# ---- 2. manifiesto de origen
Write-Host "Calculando el manifiesto SHA-256 de la instalación (unos minutos)..."
$res.archivos_origen = Manifiesto $I "$L\manifiesto_origen.csv"

# ---- 3. archivo cifrado
New-Item -ItemType Directory -Force $DEST | Out-Null
$res.acl_destino = @(icacls $DEST | Select-Object -SkipLast 2)
if (($res.acl_destino -join " ") -match 'S-1-5-11|Usuarios autentificados|Authenticated Users|Todos|Everyone') { Fin "DESTINO_NO_PROTEGIDO" }
Write-Host ""
Write-Host "7-Zip pedirá ahora la CLAVE DEL RESPALDO dos veces (no se ve al escribir). Usa una frase larga (16+ caracteres)" -ForegroundColor Yellow
Write-Host "y guárdala FUERA de este equipo (gestor de contraseñas o papel en lugar seguro). Sin ella el respaldo no se puede abrir." -ForegroundColor Yellow
& $SZ a -t7z -mhe=on -mx=5 -bsp1 -p $ARCH "$I\*"
$res.siete_zip_crear = $LASTEXITCODE
if ($res.siete_zip_crear -ne 0 -or -not (Test-Path $ARCH)) { Fin "CIFRADO_FALLIDO" }
$res.archivo_bytes = (Get-Item $ARCH).Length
$res.archivo_sha256 = (Get-FileHash -LiteralPath $ARCH -Algorithm SHA256).Hash
"$($res.archivo_sha256)  $(Split-Path $ARCH -Leaf)" | Set-Content -LiteralPath "$ARCH.sha256" -Encoding ASCII
Copy-Item "$L\manifiesto_origen.csv" "$DEST\cauce-msi_$TS.manifiesto.csv"

# ---- 4. restauración en copia aislada
New-Item -ItemType Directory -Force $R | Out-Null
$res.acl_prueba = @(icacls $R | Select-Object -SkipLast 2)
if (($res.acl_prueba -join " ") -match 'S-1-5-11|Usuarios autentificados|Authenticated Users|Todos|Everyone') { Fin "PRUEBA_NO_PROTEGIDA" }
Write-Host ""
Write-Host "Prueba de restauración: 7-Zip pedirá la CLAVE DEL RESPALDO una vez más." -ForegroundColor Yellow
& $SZ x $ARCH "-o$R" -bsp1 -p
$res.siete_zip_restaurar = $LASTEXITCODE
if ($res.siete_zip_restaurar -ne 0) { Fin "RESTAURACION_FALLIDA" }
$res.archivos_restaurados = Manifiesto $R "$L\manifiesto_restaurado.csv"
$o = @{}; Import-Csv "$L\manifiesto_origen.csv" | ForEach-Object { $o[$_.ruta] = $_.sha256 }
$d = @{}; Import-Csv "$L\manifiesto_restaurado.csv" | ForEach-Object { $d[$_.ruta] = $_.sha256 }
$res.restaurado_distintos = @($o.Keys | Where-Object { $d.ContainsKey($_) -and $d[$_] -ne $o[$_] })
$res.restaurado_faltantes = @($o.Keys | Where-Object { -not $d.ContainsKey($_) })
$res.restaurado_sobrantes = @($d.Keys | Where-Object { -not $o.ContainsKey($_) })
$dbs = @(Get-ChildItem -LiteralPath $R -Recurse -Force -File -Include *.db | Where-Object { $_.FullName -notmatch '\\(\.venv|node_modules|\.browsers)\\' })
$res.integridad_bases = [ordered]@{}
foreach ($db in $dbs) {
  $uri = "file:" + ($db.FullName -replace '\\', '/') + "?mode=ro"
  $res.integridad_bases[$db.FullName.Substring($R.Length + 1)] = (& $PY -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1], uri=True).execute('PRAGMA integrity_check').fetchone()[0])" $uri)
}
Herr @("--instalacion", $R, "--salida", $S, "instantanea", "restaurada") | Out-Null
$rest = (Get-Content "$S\instantanea_restaurada.json" -Raw -Encoding UTF8 | ConvertFrom-Json).tablas
$res.tablas_distintas_restaurada = @($ahora.PSObject.Properties.Name | Where-Object { $ahora.$_.sha256 -ne $rest.$_.sha256 })
Herr @("--instalacion", $I, "--salida", $S, "escanear", "--valor-desde", $ENV_ANTIGUO, $R) | Out-Null
$esc = Get-ChildItem "$S\escaneo_*.json" | Sort-Object LastWriteTime | Select-Object -Last 1
$res.antigua_en_restaurada = @((Get-Content $esc.FullName -Raw -Encoding UTF8 | ConvertFrom-Json).archivos_con_la_contrasena).Count
$okRest = ($res.restaurado_distintos.Count -eq 0) -and ($res.restaurado_faltantes.Count -eq 0) -and ($res.restaurado_sobrantes.Count -eq 0) `
          -and (@($res.integridad_bases.Values | Where-Object { $_ -ne "ok" }).Count -eq 0) -and ($res.integridad_bases.Count -gt 0) `
          -and ($res.tablas_distintas_restaurada.Count -eq 0) -and ($res.antigua_en_restaurada -eq 0)
if (-not $okRest) { Fin "RESTAURACION_NO_IDENTICA (copia de prueba conservada en $R)" }

# ---- 5. borrar SOLO la copia de prueba sin cifrar
Remove-Item -LiteralPath $R -Recurse -Force
$res.copia_prueba_borrada = -not (Test-Path $R)

# ---- 6. Monitor sigue funcionando y sus datos no cambian
$env:MONITOREO_ABRIR_NAVEGADOR = "0"
$p = Start-Process -FilePath $PY -ArgumentList "-m", "monitoreo" -WorkingDirectory $I -WindowStyle Minimized -PassThru
$ok = $false
for ($i = 0; $i -lt 60 -and -not $ok; $i++) {
  Start-Sleep -Seconds 2
  try { $h = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 5; $ok = [bool]$h.ok; $res.health = $h } catch {}
}
try { $res.raiz_http = (Invoke-WebRequest -Uri "http://127.0.0.1:8000/" -UseBasicParsing -TimeoutSec 10).StatusCode } catch { $res.raiz_http = "$_" }
taskkill /PID $p.Id /T /F | Out-Null
Start-Sleep -Seconds 3
foreach ($q in ProcCauce) { Stop-Process -Id $q.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2
$res.monitor_detenido = -not (Puerto8000) -and -not (ProcCauce).Count
Herr @("--instalacion", $I, "--salida", $S, "instantanea", "final") | Out-Null
$fin = (Get-Content "$S\instantanea_final.json" -Raw -Encoding UTF8 | ConvertFrom-Json)
$res.integridad_final = $fin.integridad
$res.protegidas_cambiadas_final = @($PROTEGIDAS | Where-Object { $fin.tablas.$_.sha256 -ne $ahora.$_.sha256 })
$res.users_igual_final = ($fin.tablas.users.sha256 -eq $ahora.users.sha256)
if (-not $ok -or -not $res.monitor_detenido -or $res.integridad_final -ne "ok" -or $res.protegidas_cambiadas_final.Count -or -not $res.users_igual_final) {
  Fin "MONITOR_O_DATOS_INESPERADOS" }
Fin "OK"
