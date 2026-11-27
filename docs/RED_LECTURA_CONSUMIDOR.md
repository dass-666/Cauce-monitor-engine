# Consumidor opcional de Network Engine (solo lectura) — laboratorio

Marcas: **[V]** verificado con una ejecución (local y/o CI de esta rama) · **[I]** inferido · **[P]** pendiente / no verificado.
Proveedor: `dass-666/cauce-network-engine` fijado en `9111b7ceb27839af1b0077a862a0520024f648f2` (rama `claude/consolidacion-integracion`, v0.2.0.dev0; antes `d6db87f`). Guía: `docs/GUIA_CONSUMIDOR_MONITOR.md` de ese repositorio.

## Qué es
`monitoreo/red_lectura/`: un adaptador que responde «¿hay alguna conexión utilizable según Network Engine?» con tres estados:
`DISPONIBLE`, `NO_DISPONIBLE`, `DESCONOCIDO`. Usa `CauceReadClient` (solo `GET /api/v1/status/connectivity` y `GET /api/v1/status/events`).

**Desde esta rama está cableado a la interfaz** (pantalla *Sistema → Red*, ruta `GET /api/red` con sesión iniciada):
`monitoreo/red_lectura/servicio.py` consulta en un hilo propio y la ruta solo devuelve la última foto, así que **la ruta nunca
espera a Network Engine** [V: `test_ruta_de_monitor_no_espera_a_network_engine`]. Muestra si Network Engine está disponible, el estado de
conectividad, las interfaces con nombres genéricos (`Wi-Fi 1`, `Cable (Ethernet) 1`; nunca el identificador, IP ni MAC), su salud,
latencia y disponibilidad, y los eventos de pérdida y recuperación. Sin `MONITOREO_NETWORK_TOKEN_FILE` no se crea ni el hilo; la
importación de `monitoreo.app` sigue sin cargar `cauce` [V].

## Configuración (todo opcional)
| Variable | Efecto |
|---|---|
| `MONITOREO_NETWORK_TOKEN_FILE` | Ruta del archivo de la credencial `read` (el texto del token nunca pasa por Monitor). Sin ella: inactivo (`no_configurado`). |
| `MONITOREO_NETWORK_PUERTO` | Puerto de la API local (1024–65535; por defecto **8780** desde Network Engine 0.2.0; antes 8765, que chocaba con la interfaz de Forms). |
| `MONITOREO_NETWORK_DIR` | Carpeta del repositorio de Network Engine si el paquete `cauce` no está instalado en el entorno de Monitor. Si no existe: `directorio_invalido`. |
| `MONITOREO_NETWORK_DESACTIVADO=1` o `CAUCE_STATUS_DISABLED=1` | Interruptor: cero peticiones. |
| `MONITOREO_NETWORK_ARCHIVO_APAGADO` | Si el archivo existe: mismo efecto que el interruptor. |

## Reglas y cómo se comprueban [V: `tests/test_red_lectura.py` (25) y `tests/test_red_servicio.py` (10), ver CI `red-lectura.yml`]
- **`null` ≠ `false`**: `usable=None`, motor apagado, 503, JSON ilegible o respuesta incoherente ⇒ `DESCONOCIDO`, nunca `NO_DISPONIBLE`.
- **Esperas**: intervalo ≥ 5 s (10 s por defecto); con fallos 2×, 4×, 8×… hasta 60 s; al primer éxito vuelve al normal. Una lectura buena anterior no se reutiliza tras un fallo.
- **401 (incluida revocación), 403, 400**: el lector se **detiene** (cero peticiones) y avisa una vez, solo con el *código*; únicamente `reanudar()` explícito lo reactiva.
- **503** (`credentials_locked`) y otros 5xx: transitorio, `DESCONOCIDO` con espera creciente.
- **Reinicio del motor**: cambio de `engine.epoch` o `last_seq` que retrocede ⇒ contador `reinicios`; la lectura nueva reemplaza a la anterior.
- **Nada sensible**: la lectura contiene solo estado, razón saneada, `degradada` y dos contadores; sin IP, MAC, identificadores de conexión, rutas, texto de excepciones ni token (prueba con datos plantados en respuestas, errores y logs).
- **Permisos**: se rechaza (`ClienteNoPermitido`) cualquier cliente con métodos de administración (p. ej. `CauceClient`). Contra el motor real en simulación, una credencial `read` recibe `403 insufficient_scope` en 12 rutas de administración (conexiones, eventos, diagnósticos, tokens, rutas, selección, stop-all, shutdown, rotación, control del simulador), `400` con parámetros extra, y se comprueba que no cambió nada ni se apagó el motor. El registro `access.jsonl` del motor muestra que las rutas usadas por «monitor» son solo de lectura.
- **Independencia**: sin el paquete `cauce`, sin configuración o con el interruptor, el lector está inactivo y no hace peticiones.

## Límites (no demostrados) [P]
- Validado solo contra el **simulador** en loopback con credencial ficticia; no con hardware, ni con Windows físico, ni con dos proveedores de internet.
- DPAPI no aísla procesos del mismo usuario de Windows (límite del proveedor, ver su guía §10): la credencial `read` protege de errores del consumidor, no de malware con el mismo usuario.
- La pantalla solo **informa**: Monitor no decide nada con este estado (no pausa tareas ni cambia de conexión). Usarlo para decidir es otro paso.
- El gestor propio `monitoreo/conexiones/` sigue existiendo y lo usa el portal (`captura/portal.py`); ver `docs/RETIRO_GESTOR_CONEXIONES.md`.
- **Datos viejos nunca se muestran como sanos** [V: `test_medicion_vieja_de_network_engine_no_se_muestra_como_sana`, `test_copia_vieja_en_monitor_pasa_a_desactualizado`]: si Network Engine marca una medición como vieja (`stale`), la interfaz sale «SIN VERIFICAR»; si Monitor no recibe respuesta nueva en `max(30 s, 3 × intervalo)`, la pantalla dice «Datos desactualizados» y ninguna interfaz cuenta como sana.
- Tras reiniciar Network Engine, Monitor se reengancha solo con la misma credencial y lo anota [V: prueba contra el motor real simulado].
- La recuperación puede tardar hasta ~65 s en verse (espera creciente de Network Engine tras fallos).
- La lectura no dice si un servicio externo funciona; solo si el equipo tiene una conexión utilizable según el motor.

## Para validar en Windows físico (cuando se autorice)
1. Network Engine real (no `sim`) en la MSI de pruebas, `python -m cauce token create --name monitor --out monitor.token` con el mismo usuario de Windows que ejecuta Monitor.
2. Definir `MONITOREO_NETWORK_TOKEN_FILE` apuntando a ese archivo; instalar el paquete `cauce` en el entorno de Monitor.
3. Con dos proveedores: desconectar uno, luego ambos; comprobar `DISPONIBLE` → `DISPONIBLE (degradada)`/`NO_DISPONIBLE` y que apagar el motor da `DESCONOCIDO`.
4. Revocar la credencial (`cauce token revoke`) y comprobar que el lector se detiene y Monitor sigue funcionando.
5. Revisar logs y base de datos: sin MAC, IP ni token.
