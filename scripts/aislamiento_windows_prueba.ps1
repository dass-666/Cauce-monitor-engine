<#
 Prueba NEGATIVA de aislamiento en Windows para el conector (especificación: docs/AISLAMIENTO_WINDOWS_ESPECIFICACION.md).
 Crea una cuenta local restringida, un árbol de prueba con datos FICTICIOS, aplica ACL NTFS y ejecuta una sonda COMO esa cuenta.
 Pasa solo si TODAS las lecturas/escrituras prohibidas dan PermissionError y las permitidas funcionan.
 Requiere administrador. Solo para entornos de prueba efímeros (runner de CI o equipo de laboratorio); no toca la instalación real.
 Uso:  pwsh -File scripts/aislamiento_windows_prueba.ps1 [-Salida resultados\aislamiento.json] [-Python python]
#>
# La clave es aleatoria, de un solo uso, de una cuenta local efimera que se borra al terminar: no es un secreto.
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingConvertToSecureStringWithPlainText', '')]
param([string]$Salida = "aislamiento_windows.json", [string]$Python = "python", [string]$Cuenta = "cauce_conector_t")
$ErrorActionPreference = "Stop"
$esAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $esAdmin) { throw "Requiere administrador (crea una cuenta local de prueba)." }
if ($env:OS -ne "Windows_NT") { throw "Solo Windows." }

$raiz = Join-Path ([IO.Path]::GetTempPath()) ("aisl_" + [guid]::NewGuid().ToString("N").Substring(0, 8))
$yo = "$env:USERDOMAIN\$env:USERNAME"
$clave = "P@" + [guid]::NewGuid().ToString("N") + "aA1!"
$resultado = [ordered]@{ cuenta = $Cuenta; raiz = $raiz; sonda = $null; veredicto = "NO_EJECUTADO"; fallos = @(); diagnostico = "" }
try {
    # --- árbol de prueba (datos ficticios)
    foreach ($d in "data", "respaldos", "conector\codigo", "conector\trabajo") { New-Item -ItemType Directory -Force -Path (Join-Path $raiz $d) | Out-Null }
    Set-Content (Join-Path $raiz "data\monitoreo.db") "BD FICTICIA zzsecreto"
    Set-Content (Join-Path $raiz ".env") "TOKEN=zzsecreto-ficticio"
    Set-Content (Join-Path $raiz "respaldos\copia.bak") "copia ficticia zzsecreto"
    Set-Content (Join-Path $raiz "conector\codigo\leeme.txt") "codigo del conector (solo lectura)"
    $sonda = Join-Path $raiz "conector\codigo\sonda.py"
    @'
import json, os, sys
from pathlib import Path
raiz = Path(sys.argv[1]); trabajo = raiz / "conector" / "trabajo"
def prueba(nombre, fn, debe_fallar):
    try:
        fn(); ok = not debe_fallar; obs = "permitido"
    except PermissionError:
        ok = debe_fallar; obs = "PermissionError"
    except Exception as e:
        ok = False; obs = type(e).__name__
    return {"prueba": nombre, "esperado": "PermissionError" if debe_fallar else "permitido", "observado": obs, "ok": ok}
def escribe(p):
    Path(p).write_text("x")
r = [
 prueba("leer data/monitoreo.db", lambda: (raiz/"data"/"monitoreo.db").read_bytes(), True),
 prueba("listar data", lambda: list((raiz/"data").iterdir()), True),
 prueba("escribir en data", lambda: escribe(raiz/"data"/"nuevo.txt"), True),
 prueba("leer .env", lambda: (raiz/".env").read_text(), True),
 prueba("listar respaldos", lambda: list((raiz/"respaldos").iterdir()), True),
 prueba("leer respaldos/copia.bak", lambda: (raiz/"respaldos"/"copia.bak").read_bytes(), True),
 prueba("escribir en el código del conector", lambda: escribe(raiz/"conector"/"codigo"/"x.txt"), True),
 prueba("modificar sonda.py (código)", lambda: (raiz/"conector"/"codigo"/"sonda.py").write_text("x"), True),
 prueba("leer el código del conector", lambda: (raiz/"conector"/"codigo"/"leeme.txt").read_text(), False),
 prueba("escribir en su carpeta de trabajo", lambda: escribe(trabajo/"salida.txt"), False),
 prueba("leer lo que escribió", lambda: (trabajo/"salida.txt").read_text(), False),
]
import ctypes
_b = ctypes.create_unicode_buffer(256); _n = ctypes.c_ulong(256)
ctypes.windll.advapi32.GetUserNameW(_b, ctypes.byref(_n))   # identidad real del token, no variables de entorno
print(json.dumps({"usuario": _b.value, "pruebas": r}))
'@ | Set-Content $sonda -Encoding UTF8

    # --- cuenta local restringida
    $o = net user $Cuenta $clave /add /expires:never /y 2>&1
    if ($LASTEXITCODE -ne 0) { throw "net user /add falló ($LASTEXITCODE): $o" }
    $resultado.diagnostico = (net user $Cuenta 2>&1 | Out-String)
    # --- ACL NTFS: sin herencia; el conector solo recorre la raíz, lee su código y escribe en su trabajo
    icacls $raiz /inheritance:r /grant:r "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" "${yo}:(OI)(CI)F" | Out-Null
    icacls $raiz /grant "${Cuenta}:(RX)" | Out-Null                                     # solo esta carpeta (para atravesar)
    icacls (Join-Path $raiz "conector") /grant "${Cuenta}:(RX)" | Out-Null              # solo atravesar
    icacls (Join-Path $raiz "conector\codigo") /grant "${Cuenta}:(OI)(CI)RX" | Out-Null
    icacls (Join-Path $raiz "conector\trabajo") /grant "${Cuenta}:(OI)(CI)M" | Out-Null
    foreach ($p in "data", "respaldos") { icacls (Join-Path $raiz $p) /deny "${Cuenta}:(OI)(CI)F" | Out-Null }   # defensa en profundidad
    icacls (Join-Path $raiz ".env") /deny "${Cuenta}:F" | Out-Null

    # --- ejecutar la sonda COMO la cuenta restringida
    $cred = New-Object System.Management.Automation.PSCredential("$env:COMPUTERNAME\$Cuenta", (ConvertTo-SecureString $clave -AsPlainText -Force))
    $out = Join-Path $raiz "conector\trabajo\sonda_salida.txt"; $err = Join-Path $raiz "conector\trabajo\sonda_error.txt"
    $py = (Get-Command $Python).Source
    $p = Start-Process -FilePath $py -ArgumentList @($sonda, $raiz) -Credential $cred -WorkingDirectory (Join-Path $raiz "conector\trabajo") `
         -RedirectStandardOutput $out -RedirectStandardError $err -Wait -PassThru -LoadUserProfile
    $texto = if (Test-Path $out) { (Get-Content $out -Raw) } else { "" }
    if (-not $texto) { $resultado.veredicto = "NO_EJECUTADO"; $resultado.fallos += "la sonda no produjo salida: " + ((Get-Content $err -Raw -ErrorAction SilentlyContinue)); }
    else {
        $s = $texto | ConvertFrom-Json
        $resultado.sonda = $s
        $resultado.fallos = @($s.pruebas | Where-Object { -not $_.ok } | ForEach-Object { "$($_.prueba): esperado $($_.esperado), observado $($_.observado)" })
        if ($s.usuario -ne $Cuenta) { $resultado.fallos += "la sonda no corrió como la cuenta restringida (usuario=$($s.usuario))" }
        $resultado.veredicto = if ($resultado.fallos.Count -eq 0) { "AISLAMIENTO_DEMOSTRADO_EN_ESTE_ENTORNO" } else { "FALLA" }
    }
} catch {
    $resultado.veredicto = "NO_EJECUTADO"; $resultado.fallos += "$($_.Exception.Message)"
} finally {
    net user $Cuenta /delete 2>$null | Out-Null
    Remove-Item -Recurse -Force $raiz -ErrorAction SilentlyContinue
}
$resultado | ConvertTo-Json -Depth 6 | Set-Content $Salida -Encoding UTF8
$resultado | ConvertTo-Json -Depth 6
if ($resultado.veredicto -ne "AISLAMIENTO_DEMOSTRADO_EN_ESTE_ENTORNO") { exit 1 }
