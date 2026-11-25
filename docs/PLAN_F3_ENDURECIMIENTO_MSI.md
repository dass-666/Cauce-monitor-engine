# F3: endurecer la instalación de Cauce Monitor en la MSI (plan, SIN aplicar)

Fecha: 2026-11-25. Basado en un diagnóstico de **solo lectura** hecho en la MSI como `MSI\MSIGF`, sin elevación. Las
salidas están en `C:\Users\MSIGF\Cauce-AI-Center\f3_diagnostico\` (archivos `0_resumen.txt` a `5_respaldos.txt`).
Marcas: **[V]** verificado en la MSI · **[I]** inferido · **[P]** pendiente de comprobar.
**No se ha cambiado nada.** Cada fase pide una autorización específica de Diego.

## 1. Qué encontró el diagnóstico

| # | Hallazgo | Gravedad | Marca |
|---|---|---|---|
| D1 | `C:\cauce-msi` (commit 3cda555) es la única instalación con datos de uso. No tiene arranque automático: se inicia a mano con `iniciar.bat`. La base no recibe escrituras desde el 19-nov; el 22-nov se abrió. | — | V |
| D2 | Todo `C:\cauce-msi` (6846 rutas) hereda de `C:\` **«Usuarios autentificados: Modificar»**. Eso incluye el código, `.venv`, `.env`, `data\monitoreo.db`, los `.bat` y `.ps1`, y `data\contrasena_admin_modo_normal.txt`. | **Alta** | V |
| D3 | Hay dos cuentas locales habilitadas además de MSIGF: `CodexSandboxOffline` y `CodexSandboxOnline`. Con D2, pueden modificar el código, el venv, la configuración y la base de Cauce. | **Alta** | V |
| D4 | `data\contrasena_admin_modo_normal.txt` guarda una contraseña en texto plano, y hay una copia en los respaldos. | **Alta** | V |
| D5 | `.venv\Scripts\python.exe` y `pythonw.exe` tienen además una entrada **explícita** «Usuarios autentificados: Modificar». Quitar la herencia no la elimina. | Media | V |
| D6 | `D:\` da Modificar a Usuarios autentificados. Por eso es modificable lo que cuelga de D:, por ejemplo `D:\cauce-forms-interfaz` (con su `.venv`), `D:\cauce-validacion-pr11-lab\secretos` y las evidencias de demostración. | Media | V |
| D7 | `C:\cauce-respaldos` es correcto en exposición: herencia rota y solo MSIGF con Control total. Pero **no** tienen permiso ni SYSTEM ni Administradores, así que un respaldo o restauración elevada o como servicio fallaría. | Baja | V |
| D8 | El último respaldo es del 19-nov 22:08, hace más de 5 días. No hay copia en D: ni fuera del equipo. | **Alta** antes de cambiar nada | V |
| D9 | El venv de Monitor y el de Forms usan el **Python de Microsoft Store** (3.13). Ese Python **no** lo puede ejecutar otra cuenta (H2), y sus datos viven en `AppData\Local\Packages\…\LocalCache`, carpeta que comparte con Jarvis. | Bloquea F4 | V (H2 demostrado en F2) |
| D10 | `C:\cauce-msi` (3cda555) **no contiene** el autorrelleno ni el aislamiento: es anterior a #12–#16. Endurecerla protege los datos, pero no prepara el lanzamiento aislado de Forms. | Importante para el alcance | V |
| D11 | Solo quedan 14,2 GB libres en C:. D: tiene 570 GB libres. | Media | V |
| D12 | BitLocker sin confirmar: consultarlo exige elevación. | P | P |
| D13 | MSIGF es administrador; UAC está activo con los valores por omisión. | — | V |

Fuera de alcance, solo para informar: el Python de Store guarda los datos de Jarvis (incluida su carpeta `secrets`) en el mismo paquete, y el grupo `CodexSandboxUsers` tiene lectura sobre ese paquete. **No se toca** porque Jarvis no forma parte de este trabajo.

## 2. Decisiones que necesito de Diego antes de aplicar

1. **¿Qué se endurece?** Recomendación: `C:\cauce-msi` y `C:\cauce-respaldos` ahora (fases F3-1 y F3-2). `D:\cauce-forms-interfaz` va en una fase aparte (F3-3), después de que funcione lo anterior.
2. **Cuentas Codex.** ¿Usas Codex sobre las carpetas de Cauce? Si es así, el endurecimiento le **quita el acceso**. Recomendación: que Codex no tenga acceso a la instalación con datos; si lo necesita, que trabaje en una copia.
3. **Contraseña en texto plano (D4).** Recomendación: cambiar la contraseña desde la propia aplicación, borrar el `.txt` y guardar la nueva en un gestor de contraseñas. Esto cambia una configuración operativa, así que la autorización va aparte.
4. **Python para la futura cuenta restringida (F4).** Recomendación: un CPython 3.13 portable en `C:\cauce-python\` endurecido, como el que funcionó en F2, y un venv de Forms basado en él. Es una instalación nueva, así que va en otra autorización.

## 3. Procedimiento exacto

Todo se ejecuta en una **PowerShell como administrador**, con Monitor **detenido**. Cada bloque comprueba su resultado y se detiene si algo no cuadra.

### F3-0. Comprobaciones previas y respaldo (no cambia permisos)
```powershell
$I = "C:\cauce-msi"; $R = "C:\cauce-respaldos"; $F = "$R\f3_$(Get-Date -Format yyyyMMdd_HHmm)"
# 0.1 Monitor no está en marcha
Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object CommandLine -match 'cauce-msi' |
  ForEach-Object { throw "Monitor está en marcha (PID $($_.ProcessId)): detenlo antes" }
# 0.2 Espacio libre: tamaño de la instalación × 2 + 1 GB
$tam = (Get-ChildItem $I -Recurse -Force -File | Measure-Object Length -Sum).Sum
if ((Get-PSDrive C).Free -lt 2*$tam + 1GB) { throw "Poco espacio en C:" }
# 0.3 Respaldo completo y verificable de la instalación (copia + hashes), también en D:
New-Item -ItemType Directory $F | Out-Null
robocopy $I "$F\cauce-msi" /E /COPY:DAT /R:1 /W:1 /NFL /NDL /NP /LOG:"$F\robocopy.log"; if ($LASTEXITCODE -ge 8) { throw "robocopy $LASTEXITCODE" }
Get-ChildItem $I -Recurse -Force -File | Get-FileHash -Algorithm SHA256 | Select-Object Hash,Path | Export-Csv "$F\hashes_antes.csv" -NoTypeInformation
# 0.4 Copia de los ACL actuales (sirve para revertir exactamente)
icacls $I /save "$F\acl_cauce-msi.txt" /T /C | Out-Null; icacls $R /save "$F\acl_respaldos.txt" | Out-Null
# 0.5 Línea base de funcionamiento (antes): arrancar Monitor con iniciar.bat como MSIGF y SIN elevar, y comprobar
#     /api/salud (o la página de inicio) e iniciar sesión. Anotar qué se ve. Detenerlo.
```
Si `robocopy` copia la base mientras está abierta, la copia puede quedar inconsistente; por eso 0.1 es obligatoria. La copia a D: se hace después de F3-1: hoy D: también permite escribir a todos (D6), así que la copia va a una carpeta de D: creada ya endurecida (ver F3-2).

### F3-1. Endurecer `C:\cauce-msi`
```powershell
$yo = ([Security.Principal.WindowsIdentity]::GetCurrent()).User.Value   # en PowerShell elevada sigue siendo MSIGF
# Quitar la herencia de C:\ y dejar solo Administradores, SYSTEM y MSIGF con control total
icacls $I /inheritance:r /grant:r "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" "*${yo}:(OI)(CI)F" /Q
if ($LASTEXITCODE) { throw "icacls $LASTEXITCODE" }
# Quitar las entradas EXPLÍCITAS de Usuarios autentificados (D5) en todo el árbol
icacls $I /remove:g "*S-1-5-11" /T /C /Q
```
Esto **no** cambia el propietario, el contenido ni la configuración. Las subcarpetas recalculan la herencia solas.

### F3-2. Respaldos: añadir SYSTEM y Administradores, y crear la copia en D:
```powershell
icacls $R /grant "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" /Q          # sigue sin herencia y sin cuentas débiles
$D = "D:\cauce-respaldos"; New-Item -ItemType Directory $D -Force | Out-Null
icacls $D /inheritance:r /grant:r "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" "*${yo}:(OI)(CI)F" /Q
robocopy $F "$D\$(Split-Path $F -Leaf)" /E /COPY:DAT /R:1 /W:1 /NP /LOG:"$D\robocopy_f3.log"
```

### F3-3 (aparte, después). `D:\cauce-forms-interfaz` y `D:\cauce-validacion-pr11-lab\secretos`
Se usan los mismos comandos que en F3-1, con su propio respaldo de ACL. Forms seguirá funcionando para MSIGF. Para que lo use una cuenta restringida hace falta el Python de la decisión 4: con el de Store no basta (D9).

## 4. Comprobaciones posteriores (lo que demuestra que sigue funcionando y que quedó protegido)

**Protección [se verifica]:**
1. `icacls C:\cauce-msi /T /C | Select-String "S-1-5-11|Usuarios autentificados|Authenticated Users"` → **0 líneas**.
2. `icacls C:\cauce-msi` muestra exactamente tres entradas: Administradores, SYSTEM y MSIGF, todas `(OI)(CI)(F)`.
3. Prueba negativa real, opcional y con autorización: una cuenta **temporal** que se borra al terminar intenta leer `data\monitoreo.db` y `.env` y escribir en `monitoreo\`. Debe recibir «Acceso denegado» en las tres. Se reutilizaría la lógica del script de F2, apuntándola a la instalación en modo solo lectura de prueba. Sin esta prueba solo está verificado el ACL, no el efecto real.
4. `C:\cauce-respaldos` y `D:\cauce-respaldos` tienen Administradores, SYSTEM y MSIGF, y nada más.

**Funcionamiento [se verifica]:**
5. Los hashes coinciden con `hashes_antes.csv`. Solo pueden diferir `-wal`, `-shm` y los registros si Monitor se arrancó.
6. `iniciar.bat` como MSIGF **sin elevar** arranca Monitor; `/api/salud` responde; se inicia sesión; se abre un expediente de prueba; un cambio de laboratorio se guarda y se ve tras reiniciar.
7. Desde la interfaz se hace un respaldo, que debe guardarse en `C:\cauce-respaldos`, y una restauración **simulada** sobre una copia. Restaurar sobre la instalación real no se hace.
8. `comprobar_equipo.bat`, si existe en 3cda555, termina sin errores nuevos.
9. Forms (`Cauce Forms.lnk`) sigue abriendo: F3-1 no toca D:.

## 5. Reversión
- **Permisos exactos:** `icacls C:\ /restore "$F\acl_cauce-msi.txt" /C` y `icacls C:\ /restore "$F\acl_respaldos.txt"`. Esto devuelve también la herencia débil, que es el estado anterior.
- **Reversión rápida:** `icacls C:\cauce-msi /reset /T /C` vuelve a heredar de `C:\`. Es equivalente al estado de hoy salvo las entradas explícitas de D5.
- **Datos:** si algo se dañara, se restaura la copia `$F\cauce-msi` con `robocopy` y se comprueba con los hashes.
- Ninguna fase borra datos, cuentas ni archivos. La única excepción es el `.txt` de la decisión 3, que tiene autorización aparte.

## 6. Riesgos
- **Arranque elevado:** si Diego arranca Monitor «como administrador», sigue funcionando, porque Administradores tiene Control total.
- **Otras herramientas (Codex):** pierden el acceso a la instalación (decisión 2).
- **El Python de Store** no cambia; F3 no lo toca.
- **Base abierta durante el respaldo:** 0.1 lo impide.
- **Espacio en C::** la copia completa ocupa lo mismo que la instalación. 0.2 lo comprueba.
- **Lo que F3 no resuelve:**
  - el lanzamiento aislado de Forms (requiere actualizar a c532b42, el Python de la decisión 4 y F4);
  - DNS (H11);
  - la lista de destinos (H12);
  - que la sonda compruebe el intérprete de Monitor (H13).
