# Matriz de compatibilidad Monitor · Network Engine · Forms (2026-11-27)

Marcas: **[V]** probado (pruebas o ejecución) · **[I]** inferido leyendo el código · **[P]** no probado.
Nada de esto está fusionado. Las ramas *operativas* (lo instalado en la MSI) no cambian.

## Componentes

| Componente | Rama / commit | Versión declarada | Puerto | Estado |
|---|---|---|---|---|
| Monitor operativo | `claude/lab-puerto-estable` `c532b42` | `2.2.2-dev` (desfasada) | 8000 (portal lab 8200/8201) | Base de esta rama |
| Monitor + Red (esta rama) | `claude/project-thread-so1hjy` | `2.2.2-dev` (sin cambiar: ver «Versión») | 8000 | PR borrador; sustituye al PR #5 sin el archivo `False` |
| Monitor PR #5 | `claude/consumidor-network-engine` `4f81607` | `2.2.2-dev` | — | Sustituido por esta rama; no fusionar |
| Monitor PR #17 | depende de Forms #5 | — | — | Sin tocar |
| Network Engine `main` | `d6db87f` | `0.1.0` | **8765** | Operativo en la MSI (`%LOCALAPPDATA%\CauceNetworkEngine`) |
| Network Engine nueva | `claude/consolidacion-integracion` `9111b7c` | `0.2.0.dev0` | **8780** (`CAUCE_NE_PORT`) | PR borrador |
| Network Engine PR #8 | `e9e1366` | `0.1.0` | 8765 | Borrador; latidos de sesión (se solapa con el F9 nuevo) |
| Forms `main` | `aa1bb02` | `0.1.0` | 8765 (`--port`) | |
| Forms #3 → #4 → #5 | `16d3e87` → `ff117da` → `d3b230a` | `0.1.0` | 8765 | Cadena apilada sin fusionar |

## Combinaciones

| Monitor | Network Engine | Forms | Resultado |
|---|---|---|---|
| c532b42 | d6db87f (8765) | aa1bb02 (8765) | **Choque de puerto** NE/Forms: el segundo en arrancar falla [I, auditoría D5] |
| esta rama | 9111b7c (8780) | aa1bb02 o d3b230a (8765) | **Compatible.** Pruebas del consumidor contra NE real en simulación [V: 35 pruebas]; tres motores a la vez en CI Windows de NE (`TRES_MOTORES.ps1`) [V en CI si pasa; ver PR] |
| esta rama | d6db87f (8765) | sin Forms | Compatible definiendo `MONITOREO_NETWORK_PUERTO=8765`: las rutas `status/connectivity` y `status/events` existen en 0.1.0 [I, no probado] |
| esta rama | ninguno | cualquiera | Monitor funciona; pantalla *Red* dice «no configurado» o «no disponible» [V] |
| c532b42 | 9111b7c | cualquiera | Sin efecto: ese Monitor no lee NE [V] |

## Orden recomendado de fusión (solo con autorización de Diego)
1. NE `claude/consolidacion-integracion` → `main` (puerto 8780). Después, cerrar o rehacer NE PR #8 encima (su página de latidos
   coincide con la de `labserver`).
2. Monitor esta rama → `claude/lab-puerto-estable`; cerrar Monitor PR #5.
3. Forms #3, #4, #5 en ese orden, subiendo la versión a `0.2.0` al fusionar #5; luego Monitor #17.

## Versión de Monitor
`2.2.2-dev` no refleja RC1, v2.4, piloto ni Forms. **No se cambió aquí** porque `jarvis/project.yaml` cuenta como código en el
contrato con Jarvis (`monitoreo/interop/comun.py`) y la prueba `test_version_unica_en_api_y_exportador` exige `2.2.*`. Cambiarla
obliga a volver a fijar `integracion.commit_fijado`, que es decisión humana. Propuesta: `2.5.0-dev` al fusionar esta rama.
