<#
 Diagnostico de SOLO LECTURA para preparar el aislamiento de Forms en Windows (fase 0 del plan:
 docs/PLAN_AISLAMIENTO_FORMS_WINDOWS.md). No crea cuentas, no cambia ACL, no abre archivos de datos:
 solo lee permisos (icacls), rutas de Python y estado de servicios. Escribe UN archivo JSON.
 No requiere administrador (si no lo es, algunas lecturas quedan como "sin_permiso").
 Uso: powershell -ExecutionPolicy Bypass -File scripts\aislamiento_diagnostico_windows.ps1 `
        -Rutas C:\cauce-msi,C:\cauce-autorrelleno,D:\ -FormsDir C:\cauce-autorrelleno\forms -Salida diag.json
#>
param([string[]]$Rutas = @("C:\cauce-msi", "C:\cauce-autorrelleno", "D:\"), [string]$FormsDir = "",
      [string]$Salida = "aislamiento_diagnostico.json")
$ErrorActionPreference = "Continue"
$r = [ordered]@{ fecha = (Get-Date).ToString("s"); equipo = $env:COMPUTERNAME; solo_lectura = $true }
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$r.usuario = $id.Name
$r.es_administrador = ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$r.windows = (Get-CimInstance Win32_OperatingSystem | Select-Object -ExpandProperty Caption)
# Permisos de cada ruta: quien hereda acceso. "Usuarios autenticados" o "Usuarios" con lectura = cualquier cuenta nueva lee.
$acl = @()
foreach ($p in $Rutas) {
    if (-not (Test-Path -LiteralPath $p)) { $acl += [ordered]@{ ruta = $p; existe = $false }; continue }
    $t = (icacls $p 2>&1 | Out-String)
    $amplio = $t -match "(Authenticated Users|Usuarios autentificados|Usuarios autenticados|BUILTIN\\Users|BUILTIN\\Usuarios|Everyone|Todos)"
    $acl += [ordered]@{ ruta = $p; existe = $true; acceso_amplio = [bool]$amplio; icacls = $t.Trim() }
}
$r.acl = $acl
# Python: donde esta instalado. Si esta dentro de C:\Users\<persona>, otra cuenta no podra ejecutarlo.
$py = @()
foreach ($c in "python", "py") { $g = Get-Command $c -ErrorAction SilentlyContinue; if ($g) { $py += $g.Source } }
$r.python_en_path = $py
if ($FormsDir) {
    $cfg = Join-Path $FormsDir ".venv\pyvenv.cfg"
    $r.forms_venv = if (Test-Path -LiteralPath $cfg) { (Get-Content -LiteralPath $cfg | Where-Object { $_ -match "^(home|version)" }) } else { "no_encontrado" }
    $r.forms_python_en_perfil_de_usuario = [bool](($r.forms_venv | Out-String) -match "\\Users\\")
    $r.forms_navegadores = Test-Path -LiteralPath (Join-Path $FormsDir ".pw-browsers")
}
# Servicio de inicio de sesion secundario (CreateProcessWithLogonW lo necesita)
$s = Get-Service seclogon -ErrorAction SilentlyContinue
$r.seclogon = if ($s) { [ordered]@{ estado = "$($s.Status)"; inicio = "$($s.StartType)" } } else { "no_encontrado" }
$r.firewall_perfiles = @(Get-NetFirewallProfile -ErrorAction SilentlyContinue | ForEach-Object { [ordered]@{ perfil = $_.Name; activo = "$($_.Enabled)" } })
$r.cuentas_locales = @(Get-LocalUser -ErrorAction SilentlyContinue | ForEach-Object { [ordered]@{ nombre = $_.Name; activa = $_.Enabled } })
$r.bitlocker = @(Get-BitLockerVolume -ErrorAction SilentlyContinue | ForEach-Object { [ordered]@{ unidad = $_.MountPoint; proteccion = "$($_.ProtectionStatus)" } })
$r | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Salida -Encoding UTF8
$r | ConvertTo-Json -Depth 5
