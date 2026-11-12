# Informe de la versión 1.3: integración HTTP real, laboratorio externo y validación operativa

Fecha: 12 de noviembre de 2026 · Rama `fase-1.3` (sale de `fase-1.2`; **sin fusionar en `master`**)
Repositorio: `dass-666/Cauce-monitor-engine` (privado)

Leyenda usada en todo el informe:

- **MEDIDO**: cifra obtenida en una ejecución real, con su archivo de resultados.
- **SIMULADO**: lo ficticio a propósito (inventario, reservas, operadores, solicitudes).
- **ESTIMADO**: deducción a partir de lo medido. No es una medición.
- **PENDIENTE**: no se pudo verificar desde aquí.

> **Qué se probó y qué no.** Todas las pruebas de esta fase usan **HTTP real** (sockets,
> peticiones, latencias, códigos de error y cortes de conexión reales) entre **procesos
> independientes** en `127.0.0.1`, contra **nuestro propio servicio de prueba** con **datos
> ficticios**. No hay ninguna conexión a servicios de terceros. Esto **no** es una integración
> con un proveedor externo: una integración así necesitará su propio conector, escrito a partir
> del contrato y de las condiciones de uso de ese proveedor (ver §9).

## 1. Resumen

| Objetivo | Estado | Evidencia |
|---|---|---|
| Servidor de disponibilidad independiente | Terminado y probado. Proceso aparte, su propio archivo JSON, contrato documentado, credenciales de prueba, fallos inyectables | `proveedor_prueba/`, `CONTRATO.md` |
| Conector HTTP | Terminado y probado. Paginación con cursor, ETag/304, respuestas antiguas, datos que cambian entre páginas, estado que sobrevive a reinicios | 21 pruebas con red real (§4) |
| Motor central sin código específico del proveedor | Comprobado por prueba automática (busca el nombre del proveedor en el núcleo) | `test_core_has_no_provider_specific_code` |
| Escenario operativo (5 regiones, 1.000 solicitudes, 12 operadores) | MEDIDO en 3 ejecuciones completas: SQLite, PostgreSQL, y SQLite con enfriamiento de circuito de 10 s | §5, `docs/resultados/v1.3/` |
| Pruebas de fiabilidad | 11 situaciones de fallo probadas, todas con prueba automática | §4 |
| Métricas de punta a punta | Nueva ruta `/api/metricas/servicio` y recorrido de un evento `/api/eventos/{id}/recorrido` | §5 |
| Panel | Añadidos mínimos (sin rediseño): fuentes HTTP, servicio medido, operadores, recorrido de un evento | prueba de navegador 37/37 |
| Telegram | De punta a punta con el **emulador**; no se pidió token real. Guía de prueba manual | `docs/TELEGRAM.md` |
| GitHub Actions | **12/12 trabajos en verde** (ejecución 43313525646), incluidos los dos servicios como procesos en Linux y en Windows | §6 |
| Compatibilidad | Las 258 pruebas pasan en SQLite y PostgreSQL; migración 0004 solo añade columnas que admiten vacío | §6 |

**Defectos reales del motor encontrados y corregidos: 3** (dos existían desde la v1.0), más 2
del servicio de prueba y 1 refinamiento del conector. Cada uno tiene prueba de regresión (§3).
Aparte, corregí 9 errores del propio banco de pruebas y 2 pruebas sensibles a tiempos de
Windows; los cuento por separado porque no eran fallos del motor.

**Dos hallazgos operativos que conviene decidir** (§7): el enfriamiento de circuito de 60 s deja
una región "a ciegas" mucho más que el fallo que lo provoca, y una respuesta perdida justo al
"tomar" una asignación la deja EN_PROCESO sin nadie que la atienda.

## 2. Qué se ha hecho

### 2.1 Servicio de prueba independiente (`proveedor_prueba/`)

- Se ejecuta como **otro proceso** (`python -m proveedor_prueba`), solo acepta direcciones
  locales (si se le pide otra, termina con código 2) y guarda su estado en **su propio archivo
  JSON** con escritura atómica. **No comparte la base de datos con el motor**: el motor solo lo
  ve por HTTP.
- Modelo ficticio: 5 regiones; recursos con fecha, capacidad y tipo; liberaciones estables y
  **ventanas que vencen solas**; reservas y cancelaciones; **liberaciones programadas**;
  cancelación de recursos; simulación de actividad concurrente de fondo. Cada cambio queda en
  un historial numerado, que es la "verdad" contra la que se audita al motor.
- API `/v1` con `Authorization: Bearer` (credencial de prueba por variable de entorno),
  paginación con cursor (409 si los datos cambian entre páginas), `ETag`/304, cabecera con el
  tiempo del servidor y **cuota real** por credencial (429 con `Retry-After`).
- Rutas `/control` (con otra credencial) para provocar fallos: 429, 500, 503, 401, lentitud,
  conexión colgada, conexión cortada a mitad del cuerpo, elemento mal formado, respuesta antigua
  repetida, cambios entre páginas, latencia variable. Un proveedor real nunca ofrecería esto.
- Contrato completo en `proveedor_prueba/CONTRATO.md`.

### 2.2 Conector HTTP (`monitoreo/connectors/proveedor_prueba.py`)

Todo lo específico del proveedor vive en el conector; el motor solo recibe el `Snapshot` común.

- Recorre las páginas; si los datos cambian a mitad reinicia **una** vez y, si vuelve a pasar,
  entrega la consulta como **INCOMPLETA** para que el motor no deduzca desapariciones falsas.
- Envía `If-None-Match`; un 304 no compara nada.
- Rechaza **respuestas antiguas** (versión y fecha anteriores a lo ya procesado: resultado
  `OBSOLETA`, que no cuenta como fallo) y distingue un **reinicio del proveedor** (versión menor
  pero generada después).
- Descuenta las ventanas que **vencieron mientras la respuesta viajaba**, y fecha el cambio en el
  momento del vencimiento.
- Descarta elementos mal formados (y marca la consulta como incompleta).
- Mide el tiempo de cada petición y el que informa el servidor.

Capacidades genéricas añadidas al motor (sirven a cualquier conector, no solo a este):
`Snapshot.not_modified`, `Snapshot.state` (guardado en `sources.connector_state` **en la misma
transacción** que los eventos, así sobrevive a reinicios), `Snapshot.meta`, el error
`StaleResponse` y `context["refresh"]` (pide datos completos cuando el motor sabe que su copia
local puede estar desfasada).

### 2.3 Métricas, panel y Telegram

- `GET /api/metricas/servicio?minutos=N`: percentiles p50/p95/p99 de **detección**,
  **procesamiento**, **hasta la asignación**, **hasta la alerta** y **hasta cada canal de
  notificación**, por separado; % de consultas correctas, por resultado y por región; ofertas
  que desaparecieron antes de usarse; asignaciones anuladas por la fuente; retrasos; CPU y
  memoria del proceso.
- `GET /api/eventos/{id}/recorrido`: los pasos de un cambio (cambio en la fuente, detección,
  procesamiento, asignaciones, alertas, entregas) con sus horas.
- Panel: columna de fuentes con host y resumen HTTP; en "Salud", tarjetas "Servicio en la última
  hora (medido)" y "Operadores"; al pulsar un evento se abre su recorrido. Sin rediseño.
- Telegram: el escenario envía las alertas al **emulador** (proceso aparte) y comprueba que cada
  entrega ENVIADA llegó a los dos chats. `docs/TELEGRAM.md` tiene la prueba manual con un bot
  real en 8 pasos, que **no** se ha hecho (no se pidió token).

### 2.4 Banco de pruebas operativo (`scripts/integracion_http.py`)

Arranca **tres procesos independientes** (proveedor, motor, emulador de Telegram), configura el
motor solo por su API, crea 5 fuentes HTTP, 12 operadores y 1.000 solicitudes, y ejecuta 7
fases: línea base; 60 s de liberaciones controladas (para medir tiempos exactos); 120 s de carga
(actividad aleatoria cada 120 ms, latencia 5–120 ms, 3 % de errores 500); 5 fallos de 12 s
(429 en una región, conexiones cortadas al 40 %, proveedor colgado en una región, respuestas
antiguas, cambios entre páginas); corte brusco del motor (`kill`); corte brusco del proveedor;
y drenaje. Los operadores simulados toman sus asignaciones, **reservan de verdad** en el
proveedor y completan o rechazan; el 15 % de las veces dos operadores pulsan "tomar" a la vez.

Al final audita al motor contra el historial del proveedor: cada cambio del proveedor se
clasifica (detectado, demasiado breve para verse con consultas cada 1 s, ya descontado por el
motor, sin cambio neto, no detectado durante un fallo, o **no detectado fuera de fallos**, que
sería un defecto).

## 3. Defectos encontrados y corregidos

### 3.1 Defectos del motor (con prueba de regresión)

| # | Defecto | Desde | Impacto | Corrección |
|---|---|---|---|---|
| 1 | Si la oferta ya había desaparecido cuando se procesaba el evento, se enviaba una alerta "sin solicitud compatible" engañosa | v1.0 | Ruido para el operador | Se comprueba si la oferta sigue existiendo antes de alertar |
| 2 | Un fallo de base de datos entre crear la asignación y su alerta dejaba una asignación **sin aviso** | v1.0 | Un operador podía no enterarse nunca | Al reprocesar el evento y en el mantenimiento se alertan las asignaciones ACTIVA sin alerta |
| 3 | Al completar una asignación, el motor resta la unidad localmente; si la fuente seguía igual (o respondía 304) la huella coincidía y el motor **se quedaba contando de menos indefinidamente** | v1.0 | Ofertas reales invisibles | El descuento local invalida la huella y fuerza una consulta completa (`refresh`) |

### 3.2 Servicio de prueba y conector

- **ETag repetido tras `kill -9` del proveedor** (podía dar un 304 falso con datos distintos):
  el ETag incluye ahora el número de arranque.
- Un fallo limitado (`restantes`) dejaba el contador en 0 e impedía aplicar fallos posteriores.
- Conector: una ventana que vence en tránsito ahora fecha el cambio en su vencimiento (antes usaba
  la hora de publicación de la liberación).

### 3.3 Correcciones del banco de pruebas y de las pruebas (no son defectos del motor)

- **Bloqueo mutuo** del banco en GitHub Actions con PostgreSQL: los 8 hilos de los operadores
  esperaban "tomas" encoladas en el mismo grupo de hilos. Ahora usan grupos separados y una
  barrera con límite de tiempo.
- Cuatro errores de clasificación de la auditoría que contaban como "perdidos" cambios que el
  motor sí tenía bien (descuento local aplicado a subidas, ventana vencida detectada por el
  conector, ida y vuelta sin cambio neto, cambio de otra hora). La auditoría ahora reconstruye
  lo que el motor creía en cada momento antes de declarar una pérdida.
- Liberaciones controladas que caían sobre el mismo recurso en menos de una consulta: ahora se
  aíslan (10 s entre usos del mismo recurso) y, si otro cambio las tapa, se cuentan aparte.
- El emulador devuelve como máximo 100 mensajes: se usa su total.
- Operadores que fallaban al completar mientras el motor se reiniciaba: ahora reintentan.
- El resumen decía "15/15" aunque fallara una comprobación no crítica: ahora lo dice.
- Dos pruebas sensibles a Windows: (a) un puerto cerrado en Windows tarda ~2 s en fallar (el
  sistema reintenta la conexión) y con 2 s de límite se ve como "tiempo de espera" y no como
  "conexión rechazada"; (b) tras reiniciar, la cubeta de ritmo no arranca llena (decisión de la
  v1.1) y la prueba necesitaba esperar unos milisegundos.

## 4. Pruebas de fiabilidad (MEDIDO, HTTP real contra el servicio de prueba)

`tests/test_integracion_http.py`: 21 pruebas, el proveedor corre como proceso real. Se ejecutan
con SQLite y con PostgreSQL (40 casos en Linux, 21 en Windows).

| Situación | Qué se verifica |
|---|---|
| Proveedor caído (parada ordenada y `kill -9`) | Error temporal, sin cambios falsos al volver; detecta el reinicio del proveedor |
| HTTP 429 (inyectado y cuota real) | Espera lo pedido (LIMITADO), no cuenta como fallo |
| Conexión cortada a mitad del cuerpo / colgada | Error temporal, no se procesa nada parcial |
| Credencial revocada (401) | La fuente se detiene y avisa; no insiste |
| Elemento mal formado | Se descarta y la consulta queda INCOMPLETA (no se deducen desapariciones) |
| Disponibilidad que desaparece antes de procesar / después de asignar / en tránsito | Sin alertas engañosas; la asignación se anula y se avisa |
| Dos operadores compiten por la misma asignación | Exactamente uno la obtiene |
| Dos procesos del motor compiten | Sin asignaciones duplicadas |
| Reinicio del motor | Conserva el estado del conector (ETag) y recupera lo cambiado mientras estaba caído |
| Respuesta antigua repetida | Se rechaza (OBSOLETA) |
| Datos que cambian a mitad de la consulta | Reinicia; si se repite, INCOMPLETA |
| Errores temporales de base de datos (al guardar la consulta, al procesar, al reequilibrar) | Nada se pierde ni se duplica; el historial registra lo ocurrido |

Cada prueba termina con la comprobación de integridad del motor (sin asignaciones
incompatibles, historial coherente).

## 5. Escenario operativo (MEDIDO; datos SIMULADOS)

Máquina: Linux, 4 CPU, Python 3.13. Tres procesos en `127.0.0.1`. Intervalo de consulta 1 s,
1.000 solicitudes, 12 operadores, 40 recursos por región, ~290 s por ejecución.
Archivos: `docs/resultados/v1.3/integracion_http_{sqlite,postgresql,sqlite_enfriamiento10}.json`.

### 5.1 Comprobaciones

| Comprobación | SQLite | PostgreSQL | SQLite, enfriamiento 10 s |
|---|---|---|---|
| Convergencia final: el motor ve exactamente lo que publica el proveedor | 0 diferencias | 0 | 0 |
| Integridad: sin asignaciones incompatibles | OK | OK | OK |
| Solicitudes con dos asignaciones vivas | 0 | 0 | 0 |
| Recursos con más unidades retenidas que disponibles | 0 | 0 | 0 |
| Dobles tomas en competencias reales | 0 de 161 | 0 de 220 | 0 de 220 |
| Telegram (emulador): entregas → mensajes en 2 chats | 1.243 → 2.486 | 1.277 → 2.554 | 1.489 → 2.978 |
| Cambios del proveedor no detectados **fuera** de fallos | **0** de 2.538 | **0** de 2.543 | **0** de 2.797 |
| Liberaciones controladas detectadas | 51/51 | 51/51 | 50/51 (1 tapada por otro cambio del mismo recurso antes de la siguiente consulta) |
| Asignaciones EN_PROCESO al terminar (no crítica) | 0 | **1** (ver §7.2) | 0 |

### 5.2 Tiempos

Liberaciones controladas (hora exacta conocida; es la medición más limpia):

| | SQLite | PostgreSQL |
|---|---|---|
| Detección p50 / p95 / p99 | 0,54 / 1,41 / 1,57 s | 0,71 / 1,05 / 1,16 s |
| Hasta la asignación p50 / p95 | 1,00 / 1,94 s | 1,25 / 1,63 s |
| Hasta Telegram (emulador) p50 / p95 | 1,04 / 1,97 s | 1,29 / 1,67 s |

Con consultas cada 1 s, la detección media teórica ronda 0,5 s más el tiempo de la petición,
así que estas cifras son las esperables. **No son tiempos de un proveedor real**: aquí la red es
local.

Todos los cambios detectados (incluye carga, fallos y reinicios), etapas por separado:

| p50 / p95 / p99 | SQLite | PostgreSQL |
|---|---|---|
| Detección | 0,77 / 1,90 / 6,52 s | 0,89 / 2,23 / 5,03 s |
| Procesamiento | 0,48 / 0,75 / 0,93 s | 0,59 / 1,33 / 2,04 s |
| Hasta la asignación | 1,20 / 2,39 / 4,86 s | 1,47 / 2,90 / 5,52 s |
| Hasta Telegram | 1,26 / 2,42 / 4,98 s | 1,68 / 3,20 / 5,77 s |

El máximo de detección (73 s en SQLite, 77 s en PostgreSQL) corresponde a regiones con el
circuito abierto tras un fallo (§7.1).

### 5.3 Consultas, cambios y recursos

| | SQLite | PostgreSQL | Enfriamiento 10 s |
|---|---|---|---|
| Consultas / correctas | 884 / 96,8 % | 824 / 97,6 % | 899 / 96,2 % |
| Por región (consultas) | 158–201 | 135–181 | 174–184 |
| Cambios del proveedor / detectados por el motor | 2.538 / 1.183 | 2.543 / 1.284 | 2.797 / 1.547 |
| Demasiado breves para verse (< 2 consultas) | 647 | 633 | 611 |
| Ya descontados por el motor al completar | 426 | 398 | 494 |
| No detectados durante fallos | 48 | 38 | 9 |
| Ofertas que desaparecieron antes de usarse | 6 | 23 | 12 |
| Asignaciones anuladas por la fuente | 16 | 1 | 42 |
| Reinicio del motor: listo / primera consulta correcta | 1,08 s / 0,52 s | 1,06 s / 0,32 s | 1,05 s / 0,59 s |
| Reinicio del proveedor: primera consulta correcta | 2,08 s | 1,08 s | 1,35 s |
| CPU del motor media / máxima | 29 % / 103 % | 41 % / 112 % | 36 % / 103 % |
| Memoria del motor máxima | 133 MB | 134 MB | 136 MB |
| Proveedor: CPU media / memoria | 3 % / 54 MB | 3 % / 53 MB | 4 % / 53 MB |
| Crecimiento de la base | 3,6 MB (4,1 KB por consulta) | 5,6 MB (6,7 KB por consulta) | 4,4 MB |

"Cambios demasiado breves" no son pérdidas: con consultas periódicas es imposible ver un estado
que dura menos que el intervalo (por ejemplo, una reserva que se cancela 0,4 s después). Si un
negocio necesita verlos, hace falta que el proveedor ofrezca notificaciones (webhooks) o un
historial de cambios; consultar más deprisa solo lo reduce.

## 6. Resultados en GitHub Actions (MEDIDO)

Ejecución **43313525646** (commit `393aca9`): **12/12 trabajos correctos**.

| Trabajo | Resultado |
|---|---|
| Linux · Python 3.11 / 3.12 / 3.13 | 258 pruebas pasan (SQLite + PostgreSQL 16) |
| Windows · Python 3.11 / 3.12 / 3.13 | 166 pasan, 1 omitida (requiere PostgreSQL) |
| Windows · PostgreSQL preinstalado | 258 pasan |
| Integración HTTP · Linux | 40 pruebas pasan; escenario corto 15/15 en SQLite y 15/15 en PostgreSQL |
| Integración HTTP · Windows | 21 pruebas pasan; escenario corto 15/15 en SQLite, con los tres servicios como procesos de Windows |
| Scripts PowerShell 5.1 y 7; instalación y actualización | correctos |

Ejecuciones anteriores de esta fase y lo que encontraron: 43292836522 y 43307083842 (las dos
pruebas sensibles a Windows, §3.3), 43307083842 (bloqueo mutuo del banco con PostgreSQL),
43311136309 y 43312383084 (clasificación de la auditoría). Todo corregido.

Esto es Windows real en la nube (`windows-latest`). **Sigue sin probarse en el MSI.**

## 7. Hallazgos operativos (requieren decisión)

### 7.1 El enfriamiento de circuito de 60 s deja regiones a ciegas

Cuando una fuente falla varias veces seguidas, el circuito se abre y no se vuelve a consultar
hasta pasado `enfriamiento_circuito_s` (60 s por defecto). Con fallos de 12 s:

| | Enfriamiento 60 s (SQLite) | Enfriamiento 10 s |
|---|---|---|
| Tiempo total a ciegas (suma de 5 regiones) | 274 s | 152 s |
| Peor región | 80 s | 44 s |
| Detección máxima | 73 s | 19 s |
| Cambios no detectados durante fallos | 48 | 9 |

Es **una ejecución de cada**, así que las cifras son indicativas, no estadísticas. Pero la
dirección es clara: un fallo de pocos segundos cuesta un minuto de ceguera.
**Recomendación:** fijar el enfriamiento en función del intervalo de consulta (por ejemplo, 5–10
intervalos) y no en 60 s fijos. El coste es más peticiones a un proveedor que está fallando;
contra un proveedor real habría que respetar lo que diga su contrato.

### 7.2 Respuesta perdida al "tomar" una asignación

En la ejecución con PostgreSQL, dos operadores pulsaron "tomar" **13 ms antes** del corte brusco
del motor. El motor guardó la toma, pero ninguno recibió la respuesta. Resultado: la asignación
quedó EN_PROCESO, y nadie la estaba atendiendo. Por diseño (desde la v1.0) el motor **no expira**
las asignaciones EN_PROCESO, porque se supone que hay una persona trabajando en ellas.
**No es una corrupción de datos** (la integridad es correcta), pero en la práctica la solicitud
se queda parada hasta que alguien la mira.
**Recomendación (no implementada, cambia el comportamiento):** que "tomar" devuelva éxito si la
asignación ya la tiene ese mismo operador (así el reintento del operador funciona), y una alerta
de sistema para asignaciones EN_PROCESO más antiguas que un umbral. Coste estimado: pequeño (una
condición y una regla de mantenimiento, con sus pruebas).

## 8. Lo que no está verificado

- **El MSI de Diego**: nada de esta fase se ha ejecutado en ese equipo.
- **Telegram real**: solo el emulador. La guía manual está en `docs/TELEGRAM.md`.
- **Un proveedor externo real**: ninguno. Latencias, cuotas y formatos reales serán distintos.
- **Duración**: cada ejecución dura unos 5 minutos; no hay prueba de horas o días con HTTP real
  (la v1.2 tiene resistencia simulada de 1 y 7 días, sin red).
- **Tamaño**: 5 fuentes y 200 recursos; con cientos de fuentes HTTP habría que medir de nuevo.
- Los percentiles altos (p99, máximo) dependen mucho de los fallos inyectados y de una sola
  máquina de 4 CPU; no hay que leerlos como garantía.

## 9. Diferencia con una integración futura con un proveedor externo

| | Esta fase (servicio de prueba) | Proveedor externo autorizado (futuro) |
|---|---|---|
| Quién controla el servicio | Nosotros | El proveedor |
| Datos | Ficticios | Reales, con sus obligaciones legales |
| Contrato | `CONTRATO.md`, escrito por nosotros | El que publique el proveedor |
| Credenciales | Inventadas, por variable de entorno | Emitidas por el proveedor, con autorización |
| Fallos | Provocados a voluntad (`/control`) | Los que ocurran; no se pueden provocar |
| Ritmo de consulta | Libre | El que permitan sus condiciones de uso |
| Conector | `proveedor_prueba` | **Uno nuevo**, escrito a partir de su contrato |

Lo que sí se reutiliza: el núcleo del motor (no tiene código de este proveedor, lo comprueba una
prueba), las capacidades genéricas del conector (§2.2), las métricas y el método de auditoría.

## 10. Cómo reproducir

```
git checkout fase-1.3
python -m pip install -r requirements.txt            # y requirements-postgres.txt si se usa PostgreSQL
python -m pytest -q                                  # 258 pruebas (PostgreSQL si MONITOREO_TEST_PG está definida)
python -m pytest -q tests/test_integracion_http.py   # 21 pruebas con el proveedor como proceso real
python scripts/integracion_http.py --rapido          # escenario corto (~3 min)
python scripts/integracion_http.py                   # escenario completo (~5 min)
python scripts/integracion_http.py --bd "postgresql+psycopg://usuario@host/base_vacia"
python scripts/integracion_http.py --enfriamiento-circuito 10
```

El escenario crea todo en una carpeta temporal y no toca ninguna base existente (con `--bd` hay
que darle una base **vacía**). Para usar el proveedor a mano, ver `proveedor_prueba/CONTRATO.md`.

Las ejecuciones oficiales de este informe se hicieron con el commit `0c90c85` (SQLite y
PostgreSQL) y `393aca9` (enfriamiento 10 s; solo cambia la clasificación de la auditoría).

## 11. Recomendación para la siguiente fase

1. Decidir los dos hallazgos de §7 (son cambios pequeños).
2. Ejecutar el escenario `--rapido` en el MSI con `instalar.ps1` ya probado, para tener la
   primera evidencia en ese equipo.
3. Antes de hablar con un proveedor real: obtener su contrato y sus condiciones de uso por
   escrito; el conector se escribe después, no antes.
