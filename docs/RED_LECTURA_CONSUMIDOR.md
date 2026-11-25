# Consumidor opcional de Network Engine (solo lectura) — laboratorio

Marcas: **[V]** verificado con una ejecución (local y/o CI de esta rama) · **[I]** inferido · **[P]** pendiente / no verificado.
Proveedor: `dass-666/cauce-network-engine` fijado en `d6db87f4ebb203ae0f8cd7a20c663f1de3fdb514` (no se modificó). Guía: `docs/GUIA_CONSUMIDOR_MONITOR.md` de ese repositorio.

## Qué es
`monitoreo/red_lectura/`: un adaptador que responde «¿hay alguna conexión utilizable según Network Engine?» con tres estados:
`DISPONIBLE`, `NO_DISPONIBLE`, `DESCONOCIDO`. Usa `CauceReadClient` (solo `GET /api/v1/status/connectivity`). **No** está conectado a
ninguna pantalla, ruta ni arranque de Monitor [V: prueba de que `import monitoreo.app` no carga ni el módulo ni `cauce`]. Quien lo use decide qué hacer con el estado.

## Configuración (todo opcional)
| Variable | Efecto |
|---|---|
| `MONITOREO_NETWORK_TOKEN_FILE` | Ruta del archivo de la credencial `read` (el texto del token nunca pasa por Monitor). Sin ella: inactivo (`no_configurado`). |
| `MONITOREO_NETWORK_PUERTO` | Puerto de la API local (1024–65535; por defecto 8765). |
| `MONITOREO_NETWORK_DESACTIVADO=1` o `CAUCE_STATUS_DISABLED=1` | Interruptor: cero peticiones. |
| `MONITOREO_NETWORK_ARCHIVO_APAGADO` | Si el archivo existe: mismo efecto que el interruptor. |

## Reglas y cómo se comprueban [V: `tests/test_red_lectura.py`, 25 pruebas, ver CI]
- **`null` ≠ `false`**: `usable=None`, motor apagado, 503, JSON ilegible o respuesta incoherente ⇒ `DESCONOCIDO`, nunca `NO_DISPONIBLE`.
- **Esperas**: intervalo ≥ 5 s (10 s por defecto); con fallos 2×, 4×, 8×… hasta 60 s; al primer éxito vuelve al normal. Una lectura buena anterior no se reutiliza tras un fallo.
- **401 (incluida revocación), 403, 400**: el lector se **detiene** (cero peticiones) y avisa una vez, solo con el *código*; únicamente `reanudar()` explícito lo reactiva.
- **503** (`credentials_locked`) y otros 5xx: transitorio, `DESCONOCIDO` con espera creciente.
- **Reinicio del motor**: cambio de `engine.epoch` o `last_seq` que retrocede ⇒ contador `reinicios`; la lectura nueva reemplaza a la anterior.
- **Nada sensible**: la lectura contiene solo estado, razón saneada, `degradada` y dos contadores; sin IP, MAC, identificadores de conexión, rutas, texto de excepciones ni token (prueba con datos plantados en respuestas, errores y logs).
- **Permisos**: se rechaza (`ClienteNoPermitido`) cualquier cliente con métodos de administración (p. ej. `CauceClient`). Contra el motor real en simulación, una credencial `read` recibe `403 insufficient_scope` en 12 rutas de administración (conexiones, eventos, diagnósticos, tokens, rutas, selección, stop-all, shutdown, rotación, control del simulador), `400` con parámetros extra, y se comprueba que no cambió nada ni se apagó el motor. El registro `access.jsonl` del motor muestra que la única ruta usada por «monitor» fue `GET /status/connectivity`.
- **Independencia**: sin el paquete `cauce`, sin configuración o con el interruptor, el lector está inactivo y no hace peticiones.

## Límites (no demostrados) [P]
- Validado solo contra el **simulador** en loopback con credencial ficticia; no con hardware, ni con Windows físico, ni con dos proveedores de internet.
- DPAPI no aísla procesos del mismo usuario de Windows (límite del proveedor, ver su guía §10): la credencial `read` protege de errores del consumidor, no de malware con el mismo usuario.
- El adaptador **no está cableado** a Monitor: falta decidir dónde se muestra o usa (decisión de Diego) y su prueba de extremo a extremo.
- La lectura no dice si un servicio externo funciona; solo si el equipo tiene una conexión utilizable según el motor.

## Para validar en Windows físico (cuando se autorice)
1. Network Engine real (no `sim`) en la MSI de pruebas, `python -m cauce token create --name monitor --out monitor.token` con el mismo usuario de Windows que ejecuta Monitor.
2. Definir `MONITOREO_NETWORK_TOKEN_FILE` apuntando a ese archivo; instalar el paquete `cauce` en el entorno de Monitor.
3. Con dos proveedores: desconectar uno, luego ambos; comprobar `DISPONIBLE` → `DISPONIBLE (degradada)`/`NO_DISPONIBLE` y que apagar el motor da `DESCONOCIDO`.
4. Revocar la credencial (`cauce token revoke`) y comprobar que el lector se detiene y Monitor sigue funcionando.
5. Revisar logs y base de datos: sin MAC, IP ni token.
