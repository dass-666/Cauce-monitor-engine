<#
 F2 del plan (docs/PLAN_AISLAMIENTO_FORMS_WINDOWS.md): prueba del aislamiento de Forms en un Windows REAL (la MSI),
 en una carpeta de LABORATORIO NUEVA, con datos FICTICIOS. No toca ninguna instalación que funcione.

 Qué hace, en este orden:
   1. Comprueba que corre como administrador y que el código está DENTRO de la carpeta de laboratorio.
   2. Crea una cuenta local de UN SOLO USO (cauce_conector_t, no administradora, clave aleatoria que no se muestra).
   3. Crea una regla temporal de Firewall: esa cuenta no sale a la red (salvo 127.0.0.1).
   4. Ejecuta las pruebas de Ventanilla aislada y del lanzador (las mismas del runner efímero; con -Forms, también
      Forms REAL como la cuenta). Las ACL se aplican solo a carpetas temporales dentro del laboratorio.
   5. SIEMPRE, aunque algo falle: borra la regla, la cuenta y su perfil, y quita las ACL que puso.
 Deja el resultado en <Carpeta>\resultados (JUnit + resumen JSON, sin datos personales).

 No modifica C:\cauce-msi, C:\cauce-autorrelleno, D:\cauce-*, la base, los respaldos ni otras instalaciones. No
 activa aislamiento.puede_lanzar() (sigue en False). No conecta portales externos: el portal es el de laboratorio
 en 127.0.0.1; la prueba de red solo abre una conexión TCP a -RedDestino (por omisión 1.1.1.1:443) sin enviar nada.

 Uso (PowerShell COMO ADMINISTRADOR, desde la copia del código en la carpeta de laboratorio):
   powershell -ExecutionPolicy Bypass -File scripts\validar_aislamiento_msi.ps1 -Confirmo `
       -Carpeta C:\cauce-aislamiento-lab -Python C:\cauce-aislamiento-lab\venv\Scripts\python.exe `
       [-Forms C:\cauce-aislamiento-lab\forms] [-QuitarSidHuerfano S-1-5-21-...]
 Reversión manual si se corta a mitad: net user cauce_conector_t /delete ;
   Remove-NetFirewallRule -DisplayName cauce-conector-sin-red ; borrar la carpeta de laboratorio.
#>
param([switch]$Confirmo, [string]$Carpeta = "C:\cauce-aislamiento-lab", [string]$Python = "",
      [string]$Forms = "", [string]$RedDestino = "1.1.1.1:443", [string[]]$QuitarSidHuerfano = @())
$ErrorActionPreference = "Stop"
$sid = $null
$cuenta = "cauce_conector_t"
$regla = "cauce-conector-sin-red"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Carpeta = [IO.Path]::GetFullPath($Carpeta)

function Falla($m) { Write-Host "NO SE EJECUTA: $m" -ForegroundColor Red; exit 2 }
if (-not $Confirmo) { Falla "falta -Confirmo (crea y borra una cuenta local y una regla de Firewall temporales)." }
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Falla "abre PowerShell como administrador (clic derecho > Ejecutar como administrador)." }
if (-not $repo.StartsWith($Carpeta + "\", [StringComparison]::OrdinalIgnoreCase)) {
    Falla "el código ($repo) debe estar DENTRO de la carpeta de laboratorio ($Carpeta)." }
foreach ($prohibida in "C:\cauce-msi", "C:\cauce-autorrelleno", "C:\cauce-respaldos") {
    if ($Carpeta.StartsWith($prohibida, [StringComparison]::OrdinalIgnoreCase)) { Falla "$Carpeta es una instalación que funciona." } }
if ($Forms) {
    $Forms = [IO.Path]::GetFullPath($Forms)
    if (-not $Forms.StartsWith($Carpeta + "\", [StringComparison]::OrdinalIgnoreCase)) {
        Falla "-Forms debe ser una COPIA dentro de $Carpeta (se le dan permisos de lectura a la cuenta)." } }
if (-not $Python) { $Python = (Get-Command python).Source }
if (Get-LocalUser -Name $cuenta -ErrorAction SilentlyContinue) {
    Falla "ya existe la cuenta $cuenta (¿una ejecución anterior cortada?). Bórrala: net user $cuenta /delete" }
if (Get-NetFirewallRule -DisplayName $regla -ErrorAction SilentlyContinue) {
    Falla "ya existe la regla de Firewall $regla. Revísala y bórrala: Remove-NetFirewallRule -DisplayName $regla" }

$res = Join-Path $Carpeta "resultados"
$tmp = Join-Path $Carpeta "tmp"
New-Item -ItemType Directory -Force -Path $res, $tmp | Out-Null
# Toda la consola queda en resultados\f2_consola.txt (la ventana elevada se cierra sola y se pierde lo que mostró).
try { Start-Transcript -LiteralPath (Join-Path $res "f2_consola.txt") -Force | Out-Null } catch { }
$resumen = [ordered]@{ fecha = (Get-Date).ToString("s"); equipo = $env:COMPUTERNAME; usuario = $id.Name
    carpeta = $Carpeta; python = $Python; forms = $Forms; red_destino = $RedDestino }
# Diagnóstico informativo: nada de esto puede cortar el script. En Windows PowerShell 5.1, con
# ErrorActionPreference=Stop, cualquier texto en stderr de un programa nativo (git, python, net, icacls)
# redirigido con 2>$null o 2>&1 se convierte en error terminante; por eso aquí y en la limpieza se usa Continue.
$ErrorActionPreference = "Continue"
try { $resumen.windows = (Get-CimInstance Win32_OperatingSystem).Caption } catch { $resumen.windows = "?" }
$resumen.commit = "$(git -c safe.directory=* -C $repo rev-parse --short HEAD 2>&1)".Trim()
$resumen.python_base = "$(& $Python -c "import sys; print(sys.base_prefix)" 2>&1)".Trim()
$resumen.python_base_en_perfil = [bool]($resumen.python_base -match "\\Users\\")   # H2: otra cuenta no lo ejecutaría
try { $resumen.firewall_perfiles = @(Get-NetFirewallProfile | ForEach-Object { "$($_.Name)=$($_.Enabled)" }) } catch { }
try { $s0 = Get-Service seclogon; $resumen.seclogon = "$($s0.Status)/$($s0.StartType)" } catch { $resumen.seclogon = "?" }
# Permisos huérfanos de una ejecución anterior (cuenta ya borrada): solo se quitan si el SID es local de esta
# máquina y ya no corresponde a ninguna cuenta (si se resuelve a un nombre, no se toca). Se comprueba el
# resultado: si el permiso sigue, se detiene ANTES de crear nada y queda la evidencia en el resumen.
# icacls /remove "*SID" devuelve 1332 (ERROR_NONE_MAPPED) cuando la cuenta ya no existe y no quita nada (visto en la
# MSI). Por eso, con .NET, se quitan solo las entradas EXPLÍCITAS de ese SID; las heredadas desaparecen solas.
function Quitar-ExplicitasDeSid($ruta, $s) {
    $n = 0
    $objetivo = New-Object Security.Principal.SecurityIdentifier($s)
    foreach ($it in @(Get-Item -LiteralPath $ruta -Force) + @(Get-ChildItem -LiteralPath $ruta -Recurse -Force -ErrorAction SilentlyContinue)) {
        try {
            $acl = Get-Acl -LiteralPath $it.FullName
            $mias = @($acl.GetAccessRules($true, $false, [Security.Principal.SecurityIdentifier]) | Where-Object { $_.IdentityReference -eq $objetivo })
            if ($mias.Count) {
                foreach ($r in $mias) { [void]$acl.RemoveAccessRuleSpecific($r) }
                Set-Acl -LiteralPath $it.FullName -AclObject $acl
                $n += $mias.Count }
        } catch { } }
    return $n
}
function Cuenta-Sid($ruta, $s) { @(icacls $ruta /T /C 2>$null | Select-String -SimpleMatch $s).Count }
$resumen.huerfanos = @()
if ($QuitarSidHuerfano) { $prefijoLocal = (Get-LocalUser | Select-Object -First 1).SID.AccountDomainSid.Value }
foreach ($h in $QuitarSidHuerfano) {
    if ($h -notmatch '^S-1-5-21-\d+-\d+-\d+-\d+$' -or -not $h.StartsWith($prefijoLocal + "-")) { Falla "$h no es un SID local de este equipo." }
    $resuelto = $null
    try { $resuelto = (New-Object Security.Principal.SecurityIdentifier($h)).Translate([Security.Principal.NTAccount]).Value } catch { }
    if ($resuelto) { Falla "$h corresponde a la cuenta ${resuelto}: no es huérfano, no se toca." }
    $ErrorActionPreference = "Continue"
    $antes = Cuenta-Sid $Carpeta $h
    $salida = @(icacls $Carpeta /remove "*$h" /T /C 2>&1 | Select-Object -Last 3 | ForEach-Object { "$_" })
    $codigo = $LASTEXITCODE
    $tras_icacls = Cuenta-Sid $Carpeta $h
    $quitados_net = 0
    if ($tras_icacls) {
        $quitados_net = Quitar-ExplicitasDeSid $Carpeta $h
    }
    $despues = Cuenta-Sid $Carpeta $h
    $ErrorActionPreference = "Stop"
    $resumen.huerfanos += [ordered]@{ sid = $h; lineas_antes = $antes; icacls_codigo = $codigo; icacls_salida = $salida
        lineas_tras_icacls = $tras_icacls; explicitas_quitadas_con_net = $quitados_net; lineas_despues = $despues }
    Write-Host "Permiso huérfano $h en $Carpeta : $antes líneas antes, $tras_icacls tras icacls, $despues al final"
    if ($despues) {
        $resumen.error = "el permiso huérfano $h sigue en $despues líneas: no se ejecuta F2"
        $resumen | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $res "msi_resumen.json") -Encoding UTF8
        try { Stop-Transcript | Out-Null } catch { }
        Falla $resumen.error }
}

$ErrorActionPreference = "Stop"

try {
    # 2. cuenta de un solo uso (RandomNumberGenerator.GetBytes(int) no existe en .NET Framework: se usa Create())
    $azar = New-Object byte[] 24
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($azar)
    $clave = "Aa1!" + [Convert]::ToBase64String($azar)
    net user $cuenta $clave /add /expires:never /y | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "net user /add devolvió $LASTEXITCODE" }
    $sid = (New-Object Security.Principal.NTAccount($cuenta)).Translate([Security.Principal.SecurityIdentifier]).Value
    # Grupos por SID, no por el texto de "net user" (cambia con el idioma de Windows). Si la cuenta quedara en
    # Administradores (S-1-5-32-544), la prueba no tiene sentido: se detiene.
    $grupos = @()
    foreach ($g in Get-LocalGroup) {
        try { if (@(Get-LocalGroupMember -Group $g -ErrorAction Stop | Where-Object { "$($_.SID)" -eq $sid }).Count) { $grupos += "$($g.SID)" } }
        catch { $grupos += "$($g.SID)=?" } }
    $resumen.cuenta_grupos = $grupos
    if ($grupos -contains "S-1-5-32-544") { throw "la cuenta temporal quedó en Administradores: no se prueba" }
    # el Python que usará la cuenta: si está en el perfil de otra persona (H2), no podrá ejecutarlo y todo fallará
    # cerrado; la carpeta de laboratorio sí se le abre en lectura.
    # La carpeta de laboratorio cuelga de C:\ y hereda "Usuarios autentificados: Modificar" (H1): la cuenta podría
    # escribir el código de Forms, el Python y el venv que usa Monitor. Se cierra la herencia y quedan solo
    # Administradores, SYSTEM y quien ejecuta (control total); la cuenta recibe solo lectura. Esto reproduce el
    # endurecimiento que F3 exigirá en la instalación real. Reversión: icacls <Carpeta> /reset /T
    $yo = $id.User.Value
    icacls $Carpeta /inheritance:r /grant:r "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" "*${yo}:(OI)(CI)F" /Q | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "icacls /inheritance:r devolvió $LASTEXITCODE" }
    $resumen.laboratorio_endurecido = $true
    icacls $Carpeta /grant "*${sid}:(OI)(CI)RX" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "icacls /grant devolvió $LASTEXITCODE" }
    # 3. Firewall: la cuenta no sale salvo a 127.0.0.1
    New-NetFirewallRule -DisplayName $regla -Direction Outbound -Action Block -LocalUser "D:(A;;CC;;;$sid)" `
        -RemoteAddress '0.0.0.0-126.255.255.255', '128.0.0.0-255.255.255.255', '::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff' | Out-Null
    # 4. pruebas
    $env:CAUCE_AISLAMIENTO_EFIMERO = "1"; $env:CAUCE_AISLAMIENTO_CUENTA = $cuenta; $env:CAUCE_AISLAMIENTO_CLAVE = $clave
    $env:CAUCE_AISLAMIENTO_REGLA_RED = $regla; $env:CAUCE_AISLAMIENTO_RED_DESTINO = $RedDestino
    $env:CAUCE_AISLAMIENTO_RAIZ_TMP = $tmp
    if ($Forms) { $env:FORMS_REPO = $Forms } else { Remove-Item Env:FORMS_REPO -ErrorAction SilentlyContinue }
    Push-Location $repo
    $ErrorActionPreference = "Continue"   # avisos de pytest en stderr no deben cortar la corrida
    & $Python -m pytest -v -rs -p no:cacheprovider --basetemp (Join-Path $tmp "pytest") `
        tests/test_aislamiento_ventanilla.py tests/test_lanzador_restringido.py `
        --junitxml (Join-Path $res "msi_aislamiento.xml") 2>&1 | Tee-Object -FilePath (Join-Path $res "msi_aislamiento.txt")
    $resumen.pytest_codigo = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    Pop-Location
} catch {
    $resumen.error = "$($_.Exception.GetType().Name): $($_.Exception.Message) (línea $($_.InvocationInfo.ScriptLineNumber))"
    Write-Host "ERROR: $($resumen.error)" -ForegroundColor Red
} finally {
    # 5. limpieza SIEMPRE; ningún paso puede impedir los siguientes
    $ErrorActionPreference = "Continue"
    Remove-Item Env:CAUCE_AISLAMIENTO_CLAVE -ErrorAction SilentlyContinue
    Get-Process -IncludeUserName -ErrorAction SilentlyContinue | Where-Object { $_.UserName -like "*\$cuenta" } |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Remove-NetFirewallRule -DisplayName $regla -ErrorAction SilentlyContinue
    # Permisos POR SID y ANTES de borrar la cuenta (borrada, su nombre ya no se resuelve y /remove por nombre no
    # encuentra nada: en el intento 3 quedó un permiso huérfano). Quita concesiones y denegaciones en todo el árbol.
    if ($sid) {
        icacls $Carpeta /remove:g "*$sid" /T /C /Q 2>$null | Out-Null
        icacls $Carpeta /remove:d "*$sid" /T /C /Q 2>$null | Out-Null }
    net user $cuenta /delete 2>$null | Out-Null
    Get-CimInstance Win32_UserProfile | Where-Object { $_.LocalPath -like "*\$cuenta*" } | Remove-CimInstance -ErrorAction SilentlyContinue
    $restoAcl = 0
    if ($sid) {   # tras borrar la cuenta, cualquier permiso suyo aparece como SID: se cuenta y, si queda, se reintenta
        $restoAcl = @(icacls $Carpeta /T /C 2>$null | Select-String -SimpleMatch $sid).Count
        if ($restoAcl) {
            $resumen.limpieza_explicitas_net = Quitar-ExplicitasDeSid $Carpeta $sid
            $restoAcl = @(icacls $Carpeta /T /C 2>$null | Select-String -SimpleMatch $sid).Count } }
    $resumen.limpieza = [ordered]@{
        procesos_de_la_cuenta = @(Get-Process -IncludeUserName -ErrorAction SilentlyContinue |
            Where-Object { $_.UserName -like "*\$cuenta" }).Count
        permisos_residuales_en_laboratorio = $restoAcl
        carpetas_temporales_de_prueba = @(Get-ChildItem $tmp -Directory -Filter "cauce_aisl_*" -ErrorAction SilentlyContinue).Count
        cuenta_existe = [bool](Get-LocalUser -Name $cuenta -ErrorAction SilentlyContinue)
        regla_existe = [bool](Get-NetFirewallRule -DisplayName $regla -ErrorAction SilentlyContinue)
        perfil_existe = [bool](Get-CimInstance Win32_UserProfile | Where-Object { $_.LocalPath -like "*\$cuenta*" }) }
    # La carpeta temporal de pytest se crea elevada y solo es legible elevado: se revisa arriba y se borra aquí.
    Remove-Item -LiteralPath (Join-Path $tmp "pytest") -Recurse -Force -ErrorAction SilentlyContinue
    $x = Join-Path $res "msi_aislamiento.xml"
    if (Test-Path $x) { try {
        [xml]$j = Get-Content $x
        $s = $j.testsuites.testsuite
        $resumen.pruebas = [ordered]@{ total = [int]$s.tests; fallos = [int]$s.failures; errores = [int]$s.errors; omitidas = [int]$s.skipped }
        $resumen.omitidas = @($j.SelectNodes("//testcase[skipped]") | ForEach-Object { "$($_.name): $($_.skipped.message)" })
        $resumen.fallidas = @($j.SelectNodes("//testcase[failure or error]") | ForEach-Object { $_.name })
    } catch { $resumen.junit_ilegible = $_.Exception.Message } }
    $resumen | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $res "msi_resumen.json") -Encoding UTF8
    $resumen | ConvertTo-Json -Depth 5
    try { Stop-Transcript | Out-Null } catch { }
}
