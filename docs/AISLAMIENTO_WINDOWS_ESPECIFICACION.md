# Especificación verificable: aislar el conector en Windows (cuenta restringida + permisos NTFS)

Estado: **ESPECIFICACIÓN. El aislamiento NO está demostrado ni implementado en Monitor** (`aislamiento.puede_lanzar()` sigue en `False`).
Solo se puede afirmar «aislado» cuando **todas** las pruebas del §4 pasen en Windows, con el lanzador real, y el resultado esté archivado.
Marcas: **[V]** verificado · **[I]** inferido · **[P]** pendiente.

## 1. Amenaza que se quiere cerrar
Un proceso del conector (o Chromium, o algo que el conector cargue) corre hoy con el usuario y los permisos de Monitor [V: informe previo]. Puede leer `data\*.db`, `.env`, los respaldos y escribir en el código. Job Objects, entorno limpio y «no darle credenciales» **no** impiden leer archivos del mismo usuario. Lo que sí lo impide es otro **principal** de Windows + ACL NTFS.

## 2. Controles requeridos
| # | Control | Cómo | Estado |
|---|---|---|---|
| C1 | Cuenta local dedicada `cauce_conector` | `net user` / `New-LocalUser`; no es administrador; sin sesión interactiva (política «Denegar inicio de sesión local» y «Denegar acceso desde la red») | [P] |
| C2 | ACL NTFS sin herencia en la raíz de la instalación | `icacls <raiz> /inheritance:r` y permisos explícitos a Administradores, SYSTEM y la cuenta de Monitor | [P] |
| C3 | El conector solo **lee** su código | `icacls conector\codigo /grant cauce_conector:(OI)(CI)RX` | [P] |
| C4 | El conector solo **escribe** en su carpeta de trabajo | `icacls conector\trabajo /grant cauce_conector:(OI)(CI)M`; el temporal del navegador (`TEMP`, `TMP`, perfil de Chromium) se redirige ahí | [P] |
| C5 | Denegación explícita sobre `data\`, `.env`, `respaldos\` (defensa en profundidad: gana a cualquier concesión heredada) | `icacls <ruta> /deny cauce_conector:(OI)(CI)F` | [P] |
| C6 | El proceso corre **como** esa cuenta | lanzador de Monitor con `CreateProcessWithLogonW`/`CreateProcessAsUser` (o tarea programada con esa cuenta); credencial guardada con DPAPI de la cuenta de Monitor, nunca en `.env` | [P] |
| C7 | Árbol de procesos acotado | Job Object `KILL_ON_JOB_CLOSE` (ya implementado en `scripts/lanzador_lab.py`, **sin verificar en Windows**) + terminación del árbol | [P] |
| C8 | Red: solo `127.0.0.1` | regla de Firewall de salida para la cuenta (`New-NetFirewallRule -LocalUser`) que bloquee todo salvo loopback | [P] |
| C9 | Entorno mínimo | como `Entorno.env()` del arnés de pruebas: sin variables ni `.env` de Monitor | [V] en el arnés |

## 3. Qué NO resuelve (no afirmar lo contrario)
- Un administrador local o SYSTEM siempre puede leerlo todo. Un exploit de elevación en Windows/Chromium rompe el aislamiento.
- Chromium con su propio sandbox sigue siendo superficie de ataque; este aislamiento es adicional, no sustituto.
- Cifrado en reposo y rol «conector» en Monitor: aparte.

## 4. Pruebas de aceptación (todas deben dar el resultado esperado, ejecutadas COMO `cauce_conector`)
| # | Prueba | Resultado esperado |
|---|---|---|
| A1 | leer `data\monitoreo.db` | `PermissionError` (WinError 5) |
| A2 | listar `data\` · escribir en `data\` | `PermissionError` |
| A3 | leer `.env` | `PermissionError` |
| A4 | listar y leer `respaldos\` | `PermissionError` |
| A5 | escribir o modificar archivos del código (`conector\codigo`) | `PermissionError` |
| A6 | leer su código | permitido |
| A7 | escribir y leer en `conector\trabajo` | permitido |
| A8 | el proceso corre con el usuario `cauce_conector` (no el de Monitor) | verdadero |
| A9 | conexión a una IP no loopback | fallo (regla C8) — **[P]: no cubierta por el script actual** |
| A10 | tras cancelar, no queda ningún proceso de la cuenta (conector, Chromium, hijos) | lista vacía |
| A11 | repetir A1–A7 con el conector **real** (no solo una sonda) y datos ficticios | igual |

`scripts/aislamiento_windows_prueba.ps1` automatiza **A1–A8** con una cuenta local y un árbol de prueba (datos ficticios) y devuelve un JSON con `veredicto`. `AISLAMIENTO_DEMOSTRADO_EN_ESTE_ENTORNO` significa solo eso: que la técnica funciona **en ese runner efímero**; no prueba nada sobre la instalación real ni la MSI. Para declarar aislamiento hace falta ejecutarlo en la máquina Windows de pruebas con la instalación real, añadir A9–A11 y archivar el resultado.

## 5. Criterio de salida para habilitar el lanzamiento operativo (todo, no una parte)
1. A1–A11 pasan en Windows con el lanzador real. 2. Documento de resultados firmado por una persona. 3. `puede_lanzar()` solo devuelve `True` si lee ese resultado vigente y la cuenta/ACL existen en ese momento (comprobación activa, no una bandera). 4. Prueba de regresión que falla si alguien amplía permisos de la cuenta.
