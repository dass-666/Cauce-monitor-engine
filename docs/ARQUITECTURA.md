# Arquitectura

## Vista general

```
              ┌──────────────── Proceso único (python -m monitoreo) ────────────────┐
              │                                                                      │
 Navegador ──▶│  Panel web (HTML+JS sin compilación)  ──▶  API REST (FastAPI)        │
              │                                              │  autenticación, roles,│
              │                                              │  validación, auditoría│
              │  Planificador (asyncio) ── cada tick ──┐     │                       │
              │    · elige fuentes que tocan           ▼     ▼                       │
              │    · semáforo de concurrencia     poll_source()                      │
              │    · cubeta de fichas / circuito       │                             │
              │                                        ▼                             │
              │   Conector (simulación | JSON | CSV | HTTP)  ──▶ Snapshot (contrato) │
              │                                        │                             │
              │                                        ▼                             │
              │   Detector de cambios ──▶ change_events (outbox, event_key UNIQUE)   │
              │                                        │                             │
              │                                        ▼                             │
              │   Asignación (UPDATE condicionales) ──▶ assignments                  │
              │                                        │                             │
              │                                        ▼                             │
              │   Alertas (dedup_key UNIQUE) ──▶ deliveries (outbox) ──▶ canales     │
              └──────────────────────────────────────┬───────────────────────────────┘
                                                     ▼
                                   SQLite (por defecto) o PostgreSQL
```

## Módulos (carpeta `monitoreo/`)

| # | Módulo pedido | Dónde está |
|---|---|---|
| 1 | Motor central de monitoreo | `engine/pipeline.py` (consulta→detección→asignación→alerta), `engine/scheduler.py` (planificador) |
| 2 | Administrador de fuentes y conectores | `connectors/` (interfaz `base.py`, registro `registry.py`), rutas `/api/sources` |
| 3 | Detección de cambios | `engine/detector.py` |
| 4 | Colas y prioridades | `engine/assignment.py` (máquina de estados, cola, asignación) |
| 5 | Gestor de operadores | tabla `operators`, `candidate_operators()`, rutas `/api/operators` y `/api/assignments` |
| 6 | Alertas | `engine/alerts.py` (canales, deduplicación, reintentos, confirmación) |
| 7 | Panel web | `web/` (index.html, app.js, app.css) |
| 8 | Base de datos e historial | `models.py`, `db.py` |
| 9 | Laboratorio de simulación | `simulator/` (mundo ficticio, siembra, simulación acelerada), `api/test_api.py` |
| 10 | Pruebas y métricas | `tests/`, `scripts/benchmark.py`, `scripts/medir_servidor.py`, `scripts/e2e_panel.js`, `/api/metrics`, `engine/integrity.py` |

## Decisiones técnicas (y por qué)

**1. Sin Redis ni Celery en esta fase.** La base de datos hace de cola durable con el
*patrón outbox*: los eventos (`change_events.processed`) y los envíos de alertas
(`deliveries.status`) se guardan en la misma transacción que el cambio de estado y se
procesan después. Si el proceso muere, al reiniciar se retoman. Esto da las mismas
garantías que una cola externa con una pieza menos que instalar, asegurar y vigilar.
*Cuándo sí añadir Redis/colas:* cuando haga falta más de un proceso trabajador o miles de
fuentes; los reclamos atómicos (`UPDATE ... WHERE status='PENDIENTE'`) ya lo permiten.

**2. SQLite por defecto, PostgreSQL recomendado en producción.** SQLite no requiere
instalar nada en Windows. Se configura con WAL y `BEGIN IMMEDIATE` (cada transacción toma
el candado de escritura al empezar, evitando errores de "database is locked" a mitad de
camino). Coste: las transacciones se serializan. Toda la batería de pruebas se ejecuta en
ambos motores.

**3. Doble asignación imposible por diseño, no por suerte.** Cada asignación es una
transacción con tres `UPDATE` condicionales (unidades libres, estado de la solicitud,
capacidad del operador). Si alguno afecta 0 filas, otro proceso se adelantó y todo se
deshace. Además `assignments.live_request_id` es `UNIQUE`: una solicitud no puede tener dos
asignaciones vivas aunque fallara la lógica. Funciona igual en SQLite y PostgreSQL y no
necesita candados distribuidos.

**4. Idempotencia en dos niveles.** `change_events.event_key` = SHA-256 de (fuente, recurso,
tipo, versión anterior, huella nueva): reprocesar la misma transición produce la misma
clave y la BD rechaza el duplicado. `alerts.dedup_key` (`evt:<event_key>` o `asg:<id>`)
impide alertas repetidas. Los webhooks se entregan *al menos una vez* con la cabecera
`X-Id-Alerta` para que el receptor descarte repeticiones.

**5. Un error nunca parece "agotado".** Si una consulta falla, no se compara nada: el
último estado conocido se conserva. Las desapariciones solo se infieren con respuestas
marcadas como completas (`Snapshot.complete=True`) y tras `ausencias_para_cancelar`
consultas seguidas.

**6. Reloj inyectable.** Todo el motor recibe un `clock`. En el panel es el reloj real; en
el laboratorio y las pruebas es un `VirtualClock`, lo que permite simular 24 horas en ~2
minutos **con el mismo código** que corre en producción.

**7. Simulación acelerada aislada.** Se ejecuta sobre una BD temporal para no mezclar miles
de eventos con fechas virtuales con los datos de operación.

## Decisiones de la versión 1.1

**8. Migraciones con Alembic y respaldo previo obligatorio.** `0001_v1_0` reproduce exactamente
el esquema de la v1.0 (comprobado contra una base creada por el código real de la v1.0);
`0002_v1_1` solo **añade** tablas, columnas que admiten nulos e índices, así que no reescribe
datos. Una base v1.0 sin versionar se reconoce por sus tablas y se marca como `0001` sin
tocarla. Si la base tiene tablas que no reconoce, no se modifica nada. Antes de cambiar un
esquema con datos: copia consistente (API de respaldo de SQLite, verificada con
`integrity_check`) o `pg_dump`. Dos arranques simultáneos no migran a la vez
(`BEGIN IMMEDIATE` / `pg_advisory_xact_lock`).

**9. Un solo planificador activo, coordinado por la base de datos (arrendamiento).** La tabla
`scheduler_leases` tiene una fila con titular y vencimiento. Cada ciclo el líder la renueva con
un `UPDATE` condicional; otra instancia solo la toma si venció. Cada instancia se considera líder
solo durante 2/3 del plazo desde su última renovación confirmada, para que dos relojes con
pequeñas diferencias no se solapen. Con `fencing_token` creciente en cada relevo. Las consultas
en curso comprueban el liderazgo (`guard`) antes de guardar resultados. Se eligió la BD y no
Redis porque ya es la pieza compartida y la exactitud no depende del reloj de otra máquina.
Limitación: si el líder se congela más que el plazo y luego vuelve, una entrega que estaba
enviando puede repetirse (entrega "al menos una vez"; ver punto 4).

**10. Recuperación al asumir el liderazgo, no al arrancar.** Quien se convierte en líder
reencola entregas que quedaron en `ENVIANDO` (en un relevo, solo las reclamadas hace más que el
plazo del arrendamiento, para no pisar a una instancia que sigue viva), marca simulaciones
interrumpidas y deja un informe en `settings['recuperacion.ultima']`. Lo demás ya era durable:
eventos sin procesar (outbox), asignaciones y su caducidad, estado del circuito y próxima
consulta (incluido el `Retry-After` del proveedor) están en la BD. La cubeta de límite por
minuto se reconstruye desde `last_polled_at`, así que un reinicio no provoca una ráfaga.

**11. Historial: resumir antes de borrar.** Cada hora cerrada se resume en `poll_stats_hourly`
y cada día en `poll_stats_daily`; una marca de agua (`retencion.agregado_hasta`) registra hasta
dónde se resumió y **nunca se borra una consulta no resumida**. El borrado va por lotes
pequeños con pausas, en una tarea aparte del ciclo del motor. Nunca se borran: auditoría,
solicitudes y su historial, asignaciones, alertas, entregas, eventos que generaron alerta o
asignación, ni los agregados diarios.

**12. Observabilidad sin dependencias nuevas.** `/api/observabilidad` y
`/api/diagnostico/fuentes/{id}` calculan el estado a partir de la BD y del planificador.
El diagnóstico ordena causas por gravedad (CRÍTICO, AVISO, INFO) con la acción sugerida.
Prometheus/Grafana quedan como opción futura si hay varios servidores.

**13. Regla para el código: nunca abrir una sesión dentro de otra.** En SQLite cada transacción
toma el candado de escritura al empezar; una sesión anidada en el mismo hilo espera a la
exterior hasta agotar el tiempo (30 s). Las pruebas activan `MONITOREO_DETECTAR_ANIDADAS=1`,
que convierte ese error en un fallo inmediato. Se encontraron y corrigieron dos casos en v1.1.

## Decisiones de la versión 1.2

**14. Tres entornos y "fallar al arrancar".** `MONITOREO_ENTORNO` (desarrollo, pruebas,
produccion). En producción las rutas de laboratorio, de prueba y del emulador **no se
registran** (no basta con protegerlas: si no existen no pueden abrirse por error), no se crean
datos de demostración y una configuración insegura impide arrancar con una lista de qué
cambiar. Es preferible un servidor que no arranca a uno expuesto con la clave de ejemplo.
Detalle en `PRODUCCION.md`.

**15. Límite de peticiones a la API en memoria.** Cubeta de fichas por IP, independiente del
límite por fuente. No se añadió Redis: con 1–2 instancias la cuenta por proceso basta, y un
límite compartido solo haría falta con muchas instancias detrás de un balanceador.

**16. Testigo de reclamo en las entregas.** Cada reclamo de una entrega genera un
`claim_token` aleatorio; el resultado solo se guarda si el testigo sigue siendo el mismo. Así un
despachador que se quedó colgado y vuelve tarde no pisa la entrega que ya hizo otro. Además, el
avance por destinatario de Telegram se guarda tras cada chat, y los reclamos de más de
`entrega_reclamo_vence_s` se reencolan aunque no cambie el líder. Las garantías, y lo que
depende del receptor, están en `NOTIFICACIONES.md`.

**17. Autodiagnóstico como lista de comprobaciones independientes.** Cada comprobación abre su
propia sesión y un fallo en una no oculta las demás; si la base no responde, no se intentan las
que dependen de ella. El mismo módulo sirve para la consola (`diagnostico.bat`, códigos 0/1/2),
el panel (solo administradores) y el planificador (cada 10 min, un aviso SISTEMA por problema
crítico y día). Con `--motor-detenido` se usa antes y después de instalar o actualizar.

**18. Instalación: la lógica en Python, PowerShell solo como envoltorio.** Lo delicado (respaldo
verificado, copia de datos sin sobrescribir, detectar el motor en marcha) está en
`scripts/preparar.py`, que solo usa la biblioteca estándar (funciona antes de instalar nada) y
tiene pruebas automáticas en cualquier sistema. Los `.ps1` buscan Python, encadenan pasos y
muestran mensajes. La actualización es "lado a lado": se copia a una carpeta nueva y la anterior
queda intacta como vuelta atrás inmediata.

## Decisiones de la versión 1.3

**19. El proveedor de prueba es otro proceso con su propio almacenamiento.** `proveedor_prueba/`
no importa nada de `monitoreo` y guarda su estado en su propio JSON (escritura atómica). El
motor solo lo ve por HTTP. Así las pruebas miden red, códigos y cortes reales, y la "verdad"
(su historial de cambios) es independiente de lo que el motor registró.

**20. Estado del conector persistido junto con los cambios.** El ETag y la versión se guardan en
la misma transacción que los eventos. Si se guardaran aparte, un fallo entre ambos dejaría el
ETag avanzado sin los eventos y la siguiente consulta recibiría un 304: el cambio se perdería
para siempre (cubierto por `test_db_error_while_saving_poll_loses_nothing`).

**21. Paginación coherente o consulta incompleta.** Si los datos cambian entre páginas, el
conector reinicia una vez; si vuelve a pasar, entrega lo leído como incompleto. Una consulta
incompleta nunca provoca cancelaciones por ausencia ni guarda el ETag.

**22. El ETag incluye el número de arranque del proveedor.** Tras un corte brusco el proveedor
puede perder sus últimos cambios y volver a pasar por una versión ya usada con otros datos; con
la versión sola el motor recibiría un 304 falso.

**23. Avisos solo de lo que todavía existe.** Si una oferta ya desapareció cuando se procesa su
evento, no se emite "disponibilidad sin solicitud compatible": nadie podría usarla. Y una
asignación creada en un intento que falló antes de avisar recibe su aviso al reprocesar (o en el
mantenimiento): nunca queda una asignación viva sin que el operador se entere.

## Decisiones de la versión 1.5 (captura y reservaciones de laboratorio)

```
 Panel (Captura) ──API /api/expedientes, /api/reservaciones──► monitoreo/captura/
                                                                 ├─ validacion.py   datos y confirmaciones (no se guardan copias)
 Expediente ──crea──► Request del motor (holder = código EXP-…, sin datos personales)
                                                                 ├─ expedientes.py  estados, historial, permisos, huella anti-duplicado
 Conector portal_reservas ──► Resource "agenda:<sucursal>" ──► motor asigna (núcleo sin cambios)
                                                                 ├─ seguimiento.py  refleja la asignación en el expediente
 Operador revisa ──► formularios/contrato.py (JSON de campos) + navegador.py (cómo se navega)
                                                                 ├─ reservaciones.py máquina de estados con evidencia
 portal_reservas/ (proceso aparte, 127.0.0.1:8200) ◄── HTTP ── navegador.py
```

- **Reutilizar el núcleo.** Cada expediente crea una solicitud normal del motor (`resource_type="cita"`).
  Asignación, operadores, alertas, auditoría, autenticación y recuperación son las de siempre. El
  conector publica **un recurso por sucursal** (no uno por horario) para no generar cientos de alertas.
- **Contrato separado de la navegación.** `contratos/portal_laboratorio.json` dice qué campo del
  portal sale de qué dato del expediente; `navegador.py` sabe cómo recorrer las pantallas. Un
  cambio de nombre de campo en el portal se corrige en el JSON, y el contrato se probó también
  llenando el portal con Playwright directamente.
- **Sin CONFIRMADA sin evidencia.** Estados: PREPARADA → DATOS_ENVIADOS → RETENIDA → CONFIRMANDO →
  CONFIRMADA (o RECHAZADA / EXPIRADA / CANCELADA). El paso a CONFIRMANDO se guarda **antes** de llamar
  al portal; la evidencia es la API del portal devolviendo el folio CONFIRMADO con la misma clave
  de idempotencia. Si la respuesta se pierde, el resultado es "incierto" (HTTP 202) y se verifica
  por clave; un trabajo de recuperación cada 2 s resuelve los CONFIRMANDO pendientes.
- **Sin doble reserva.** En el motor: índice único `viva_expediente_id` (un intento vivo por
  expediente) y UPDATE condicional RETENIDA→CONFIRMANDO (solo un operador gana). En el portal:
  capacidad por horario. La prueba de carga lo comprueba contra el portal, no contra el motor.
- **Datos personales.** Solo ficticios (dominios `.test`/`example`, teléfonos 555; se puede
  desactivar el filtro con `MONITOREO_CAPTURA_SOLO_SINTETICOS=0` solo en laboratorio). Listados
  enmascarados; la ficha completa solo para el operador asignado y administradores, con auditoría
  sin valores. Las confirmaciones (teléfono/correo) se validan y se descartan.
- **Solo laboratorio.** Las rutas de captura no se montan en modo producción (prueba automática).
  El navegador solo acepta hosts de `MONITOREO_WEBHOOK_HOSTS` (por defecto 127.0.0.1).

## Decisiones de la versión 1.6 (reservación por día)

**Unidad de disponibilidad.** En modo `POR_DIA` la unidad es *región + sucursal + trámite + fecha*,
sin hora. Cada día del portal llega con `estado`, `libres`, `capacidad`, `version` y, si aplica,
`disponible_hasta`. El motor la guarda en `calendario_dias` y lo resume por (sucursal, trámite)
para su núcleo de siempre: **el motor de asignación no cambió**, solo recibe un recurso nuevo
(`cita_dia`). El modo `POR_HORARIO` de la v1.5 sigue igual; el modo de cada expediente se fija al
crearlo (`captura_modo`).

**Dos mecanismos para enterarse de los cambios, comparables:**

```
           Portal de PRUEBA (127.0.0.1)
      ┌──────────────┴──────────────┐
  A: eventos del servidor        B: consulta periódica
  (flujo con secuencia,          (lista completa cada N s)
   reanudable, latido)
      └──────────────┬──────────────┘
         calendario_dias.aplicar()  ← compara por VERSIÓN, nunca por hora
                     │
     calendario_cambios (tipo, antes, después, versión, detectado_en)
                     │
     resumen por sucursal y trámite → núcleo del motor → asignación
```

- A se reanuda desde la última secuencia; si el portal ya no la tiene, pide **resincronizar** con
  una lectura completa. Un evento con versión menor o igual se descarta (`FUERA_DE_ORDEN`).
- Con A activo, si el flujo cae, la sesión caduca o falta el latido, el motor **vuelve a B** hasta
  reconectar.
- **Un fallo de comunicación nunca se traduce en "agotado"**: los días quedan `DESCONOCIDO`, el
  panel muestra lo almacenado con la hora de la **última verificación real**. Ningún paso de la
  reservación (enviar datos, elegir día, confirmar) se hace con datos guardados: cada uno vuelve a
  leer el portal en vivo y, si no responde, se detiene con "sin comunicación". *No verificado:* que
  el núcleo nunca cree una asignación nueva a partir del último resumen guardado durante una caída
  (la consulta fallida no actualiza el recurso, y el núcleo conserva su último estado).
- **Zona horaria.** "Hoy" se calcula en la zona que **declara el portal** en cada respuesta
  (`calendario_fuentes.zona_horaria`), no en la del servidor del motor. Defecto real encontrado
  en esta fase: con el motor en UTC y el portal en Ciudad de México, el día de "mañana" aparecía
  como no habilitado.

**Expediente con 10 estados.** `NUEVO → EN_VALIDACION → LISTO | INCOMPLETO | REQUIERE_REVISION →
ASIGNADO → CAPTURA_EN_PROCESO → RESERVACION_PENDIENTE → RESERVACION_CONFIRMADA`, y `CERRADO` con
motivo. Solo `cambiar_estado()` cambia el estado: valida la transición y deja rastro. La solicitud
del motor se crea **solo en LISTO**, así un incompleto no puede asignarse ni por error. Tres
identificadores separados: registro previo, código interno (`EXP-000123`, ronda `-R2`) y folio.

**Confirmación segura.** `CONFIRMADA` solo con evidencia del portal (folio y clave). Si la respuesta
se pierde, el servidor falla o el folio choca con otro, la reservación queda **EN_VERIFICACION** y
la recuperación pregunta al portal por la clave (idempotente). Solo si el portal responde "no
existe" pasado un margen, el intento se reabre. Nunca se reintenta a ciegas.

**Campos de captura manual.** El contrato del formulario marca campos `MANUAL_REQUIRED` (p. ej. la
clave de identidad que el portal no deja pegar). El motor los valida y los manda al portal solo en
memoria: **no se guardan ni se registran**; el historial guarda únicamente sus nombres.

**Retención.** `captura/retencion.py`: anonimiza (o borra, si nunca llegó al portal) expedientes
confirmados, cerrados o incompletos pasado su plazo; **nunca** toca uno con operación activa ni con
reservación apartada o en verificación. Respaldos con permisos solo del dueño (POSIX) y purga que
siempre conserva los 3 últimos. Exportación de expedientes enmascarada por defecto; la completa
exige motivo y queda auditada.

## Decisiones de la versión 1.7 (centro de operaciones y consola del operador)

**Nada nuevo en el núcleo de detección.** La v1.7 trabaja encima de lo que ya existía: lee las
mismas tablas y añade solo cinco columnas (migración **0008**, sin borrar nada): `operators.visto_en`,
`assignments.explicacion` y `alerts.categoria / nivel / grupo / repeticiones / ultima_en`. Las
alertas antiguas se clasifican al migrar con una tabla fija.

```
 panel (app.js / captura.js)
   ├─ Inicio ─────────── GET /api/centro            (cifras reales + filtros; operador = solo lo suyo)
   ├─ Mi trabajo ─────── GET /api/captura/mi-trabajo (paso 1..12, a medias, avisos; sin datos personales)
   ├─ Supervisión ────── GET /api/supervision        (presencia: token vivo + visto_en)
   │                     POST …/operadores/{id}/redistribuir  (UPDATE condicional por asignación + auditoría)
   ├─ Notificaciones ─── GET /api/notificaciones, POST /api/notificaciones/confirmar
   └─ Autodiagnóstico ── GET/POST /api/diagnostico/reparaciones
```

- **Presencia sin tráfico extra.** Cada petición autenticada marca `visto_en` como mucho cada 15 s,
  en una transacción anidada que nunca hace fallar la petición. La presencia combina eso con la
  sesión: sesión revocada o caducada = DESCONECTADO; sesión viva y sin señal = CONEXION_PERDIDA y,
  pasado `operador_inactivo_s`, INACTIVO.
- **Redistribución que no pisa a nadie.** Cada asignación se libera con `UPDATE … WHERE estado =
  el leído`: si el operador la tomó o terminó entre medias, no se toca. Las tomadas solo se liberan
  a petición y sin señal reciente; las que tienen una confirmación en curso o en verificación,
  nunca (las resuelve el motor contra el portal).
- **Explicación de la asignación.** `elegir_operadores()` devuelve los candidatos y los descartes con
  su motivo; se guarda un resumen legible en la asignación. Al operador se le corta la parte de
  otros candidatos. El reparto `equitativo_dia` es opcional; el predeterminado sigue siendo el de la
  v1.6 (`menor_carga`), para no cambiar el comportamiento a nadie que actualice.
- **Notificaciones.** Diez categorías fijas con nivel. Silenciar = la alerta se guarda con estado
  `SILENCIADA` y sin entregas (las CRÍTICAS no se pueden silenciar). Agrupar = un `UPDATE` atómico
  suma repeticiones a la alerta abierta del mismo grupo; solo para causas de sistema repetidas.
  El titular viaja con iniciales. **Despacho:** además del presupuesto por pasada (v1.4) y de
  apartar un canal que falla (v1.4), cada canal tiene su propio tiempo por pasada
  (`despacho_canal_presupuesto_s`): un canal lento que sí responde ya no retrasa a los demás
  (defecto real encontrado por el laboratorio v1.7).
- **Visibilidad por operador.** Una única función (`alertas_visibles`, `_solicitudes_visibles`)
  decide qué ve un operador; la usan la lista, el detalle, la confirmación y el panel de inicio.
- **Reparaciones.** `reparaciones.py` declara cada reparación como *segura* (recalcular contadores,
  devolver a la cola) o *destructiva* (borrar filas). Las seguras las aplica el autodiagnóstico y
  quedan auditadas; las destructivas exigen confirmación y motivo.
- **Calendario a fin de mes.** Si el mes actual no tiene días libres, se pide el siguiente y se
  avisa (defecto real de la v1.6 que dependía de la fecha).

## Máquina de estados de solicitudes

```
PENDIENTE ──validar──▶ ELEGIBLE ──asignar──▶ ASIGNADO ──tomar──▶ EN_PROCESO ──▶ COMPLETADO
    │                     │                     │                    │
    └──▶ ERROR            ├──▶ EXPIRADO          ├─ expira/rechaza/   ├─ rechaza ──▶ REINTENTO
         (condiciones     │   (vigencia)         │  recurso agotado   └─▶ ERROR
          inválidas)      └──▶ CANCELADO         ▼
                                              REINTENTO ──asignar──▶ ASIGNADO
                                                 └─ intentos agotados ──▶ EXPIRADO
```
Las transiciones permitidas están en `assignment.TRANSITIONS`; cualquier otra lanza
`TransitionError`. Cada cambio queda en `request_events` (historial).

Orden de la cola: prioridad (1 = máxima) → antigüedad → id. Operador elegido: activo, que
cubra la región, con capacidad libre, menor carga y el que hace más tiempo no recibe una.

## Planificador y resiliencia (por fuente)

| Mecanismo | Implementación |
|---|---|
| Intervalo configurable | `sources.interval_s` |
| Variación temporal controlada | `sources.jitter_pct` (± %) |
| Límite de solicitudes | cubeta de fichas `rate_limit_per_min` (en memoria; desde v1.1 se reconstruye tras un reinicio) |
| Reintentos con retroceso exponencial | dentro de una consulta (`max_retries`) y entre consultas fallidas (`backoff_max_s`) |
| Pausa automática | circuit breaker: tras `umbral_circuito` fallos se abre. **v1.4 (modo `adaptable`, por defecto):** la pausa es `max(enfriamiento_min_s, intervalo × enfriamiento_intervalos) × 2^(aperturas−1)`, con tope `enfriamiento_max_s`; al vencer se hace UNA consulta de prueba (`SEMIABIERTO`, marcada en la base antes de consultar); cada éxito resta una apertura (recuperación gradual); el estado vive en la base y sobrevive a reinicios; un 429/`Retry-After` durante la prueba se respeta y no cuenta como fallo. Pasa a `ERROR` solo con `aperturas_hasta_error` aperturas **y** `minutos_sin_exito_hasta_error` sin éxito. Modo `fijo`: comportamiento v1.3 (`enfriamiento_circuito_s`) |
| Control de concurrencia | semáforo global `concurrencia_maxima`; una fuente nunca en paralelo consigo misma |
| Suspensión manual | `POST /api/sources/{id}/suspender` |
| Respeto de limitación del proveedor | HTTP 429/503 → `RateLimited`: no se reintenta y se espera `Retry-After` (o 2× el intervalo). 401/403 → la fuente pasa a `ERROR`, sin reintentos |

Lo que **no** hace (a propósito): rotar identidades, proxies o agentes de usuario, resolver
captchas ni sortear bloqueos. El conector HTTP se identifica con un `User-Agent` honesto.

## Seguridad

- Contraseñas con scrypt + sal; política mínima (10 caracteres, letras y números).
- Tokens de sesión aleatorios (256 bits); en BD solo su SHA-256; caducan y se revocan al salir.
- Roles `admin` y `operador`. El operador ve y actúa solo sobre sus asignaciones; laboratorio,
  fuentes, canales, usuarios, parámetros y auditoría son solo de administrador.
- Bloqueo temporal tras 5 inicios de sesión fallidos (usuario+IP, 5 min).
- Validación estricta con Pydantic (`extra="forbid"`, rangos, patrones).
- Auditoría de cada acción que modifica datos, con secretos redactados (`***`).
- Secretos: en `.env` (fuera del repositorio). La API nunca devuelve secretos (`********`) y
  un `********` reenviado no sobrescribe el valor guardado. Las credenciales de conectores HTTP
  se referencian por nombre de variable de entorno (`token_env`), no se guardan en la BD.
- Mitigación de SSRF: webhooks y conectores HTTP solo hacia hosts en `MONITOREO_WEBHOOK_HOSTS`.
- Conectores de archivo confinados a `data/feeds` (sin `../`); claves internas `_*` no se aceptan del usuario.
- Cabeceras: CSP estricta (sin scripts ni estilos en línea), `X-Frame-Options: DENY`, `nosniff`,
  `no-referrer`, `Cache-Control: no-store` en la API. Errores internos sin trazas al cliente.
- Panel: todo el contenido dinámico se inserta con `textContent` (previene XSS).
- Servidor escucha en `127.0.0.1` por defecto.

**Pendiente de seguridad antes de exponerlo en red:** HTTPS (proxy inverso), limitación de
tasa general de la API, rotación de la clave de firma y gestión de secretos en un almacén
dedicado. Ver INFORME.md.
