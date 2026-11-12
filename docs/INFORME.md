# Informe final · Versión 1.0 del Motor de Monitoreo de Disponibilidad

Fecha: 2026-11-12. Todas las cifras de este informe se obtuvieron ejecutando el código en un
contenedor Linux de 4 CPU (Python 3.13.18, PostgreSQL 16, SQLite de la biblioteca estándar).
Los datos crudos están en `docs/resultados/`. **No se ha probado todavía en Windows** (ver §6).

## 1. Resumen

Se construyó desde cero (no existía un repositorio reutilizable entre los de la cuenta) un
núcleo de monitoreo que: consulta fuentes con ritmo controlado y tolerancia a fallos; detecta
solo cambios relevantes con identificadores estables; mantiene una cola de solicitudes con
prioridad y antigüedad; asigna recursos a operadores sin posibilidad de doble asignación;
emite alertas deduplicadas por varios canales con reintentos y confirmación; y ofrece un panel
web responsivo y un laboratorio que reproduce todo con datos ficticios, incluido tiempo acelerado.

## 2. Arquitectura implementada

Proceso único Python: FastAPI (API + panel) y un planificador asyncio en segundo plano. La base
de datos (SQLite por defecto, PostgreSQL opcional) es también la cola durable mediante el
patrón *outbox*. No se usó Redis ni Celery: no aportaban garantías adicionales en esta fase y
añadían piezas que instalar y asegurar. Detalle y justificación: `docs/ARQUITECTURA.md`.

## 3. Archivos creados

| Ruta | Contenido |
|---|---|
| `monitoreo/config.py`, `db.py`, `models.py` | Configuración (.env), conexión SQLite/PostgreSQL, 19 tablas |
| `monitoreo/contracts.py` | Contrato interno común `Observation` / `Snapshot` |
| `monitoreo/connectors/` | Interfaz y conectores simulación, JSON, CSV, HTTP + registro |
| `monitoreo/engine/` | `scheduler`, `pipeline`, `detector`, `assignment`, `alerts`, `resilience`, `clock`, `params`, `integrity` |
| `monitoreo/simulator/` | Mundo ficticio, datos de demostración (5 regiones), simulación acelerada |
| `monitoreo/api/` | Rutas REST, esquemas de validación, serializadores, API de prueba local |
| `monitoreo/security.py` | Contraseñas scrypt, tokens, auditoría, bloqueo por intentos |
| `monitoreo/web/` | Panel: `index.html`, `app.js`, `app.css` |
| `tests/` (6 archivos de prueba + utilidades) | 53 pruebas (88 ejecuciones con PostgreSQL) |
| `scripts/` | `benchmark.py`, `medir_servidor.py`, `e2e_panel.js`, `crear_env.py` |
| `instalar.ps1`, `iniciar.bat`, `pruebas.bat`, `.env.ejemplo`, `requirements.txt` | Instalación y ejecución en Windows |
| `docs/` | Arquitectura, conectores, este informe, capturas y resultados medidos |

Tamaño: ~3.800 líneas de Python en `monitoreo/`, ~1.850 entre panel y pruebas.

## 4. Funcionalidades terminadas (con evidencia)

| Requisito | Estado | Evidencia |
|---|---|---|
| Fuentes con id, región, tipo, estado, configuración, último resultado, historial y métricas | Hecho | tablas `sources`, `poll_log`; vista Monitor |
| 5 regiones simultáneas | Hecho | demo y simulación de 24 h con 5 regiones |
| Intervalos, variación, límite de solicitudes, reintentos exponenciales, circuit breaker, concurrencia, suspensión manual, respeto de 429/Retry-After | Hecho | `tests/test_pipeline.py` (8 pruebas de resiliencia) |
| Detección: nueva, agotada, horario, aumento, reducción, cancelación, reaparición, duplicados, errores temporales | Hecho | `tests/test_detector.py`; un error nunca se interpreta como "agotado" (`test_errors_never_look_like_sold_out`) |
| Ids estables e idempotencia | Hecho | `event_key`/`dedup_key` UNIQUE; `test_reprocessing_is_idempotent` |
| Cola con prioridad, antigüedad, elegibilidad, historial, estados PENDIENTE→…→COMPLETADO + EXPIRADO/CANCELADO/ERROR/REINTENTO | Hecho | `tests/test_assignment.py` (13 pruebas) |
| Asignación transaccional sin duplicados | Hecho | prueba de 32 hilos (§5) |
| Reevaluación tras expiración o rechazo | Hecho | `test_expiry_returns_to_queue…`, `test_rejection_excludes_same_slot…` |
| Alertas: panel, webhook firmado (HMAC), registro, canal simulado; dedup, reintentos, confirmación | Hecho | `test_pipeline.py`, `test_connectors.py::test_webhook…` |
| Interfaces para Telegram y correo | Solo interfaz | `NotConfiguredSender` (devuelve fallo permanente "no implementado") |
| Laboratorio: eventos, liberaciones, agotamiento, cancelaciones, cambios simultáneos, competencia, aceleración | Hecho | vista Laboratorio, `simulator/accelerated.py` |
| Panel responsivo (inicio, monitor, solicitudes, alertas, laboratorio, configuración) | Hecho | `docs/capturas/`, prueba con navegador real |
| Autenticación, roles, control de acceso, auditoría, secretos, validación | Hecho | `tests/test_api.py` (9 pruebas) |
| Conectores simulación, JSON, CSV, API local | Hecho | `tests/test_connectors.py` (8 pruebas, servidor HTTP real) |
| Reinicio sin pérdida de datos | Hecho | `tests/test_restart.py` mata el servidor con SIGKILL y verifica datos e integridad |

## 5. Pruebas ejecutadas y resultados reales

### 5.1 Pruebas automatizadas
- `pytest` con SQLite: **53 aprobadas** en 20,5 s (`docs/resultados/pytest_sqlite.txt`).
- `pytest` con SQLite **y** PostgreSQL: **88 aprobadas** en 31 s (`pytest_sqlite_y_postgresql.txt`).

### 5.2 Panel con navegador real (Playwright/Chromium) — `scripts/e2e_panel.js`
Inicio de sesión → 5 regiones visibles → registrar solicitud → liberar 1 unidad en el
laboratorio → el motor detecta, asigna y alerta **sin intervención**. Tiempo medido desde la
liberación hasta la alerta de asignación: **1,2 s; 5,3 s; 5,3 s** en tres ejecuciones
(las fuentes de demostración consultan cada 5 s ±15 %). En teléfono (390 px): **0 px de
desbordamiento horizontal**, **0 errores de consola**.

### 5.3 Simulación acelerada de 24 horas (`scripts/benchmark.py`)
5 regiones, 500 solicitudes, 6 operadores, fuentes con 3 % de errores, 1 % de limitación (429),
5 % de filas duplicadas, canal de notificación con 10 % de fallos, picos de cambios cada hora.

| Medida | SQLite | PostgreSQL |
|---|---|---|
| Tiempo real para 24 h virtuales | 113,8 s (×759) | 261,2 s (×331) |
| Cambios simulados | 8.610 | 8.436 |
| Consultas (OK / limitadas / error) | 9.486 / 105 / 0 | 9.469 / 92 / 1 |
| Duplicados descartados | 499 | 451 |
| Eventos detectados | 5.740 | 5.526 |
| Solicitudes completadas | 500 de 500 | 500 de 500 |
| Asignaciones rechazadas / anuladas por agotamiento | 54 / 10 | 44 / 2 |
| Alertas creadas → enviadas | 3.990 → 3.990 | 3.847 → 3.847 |
| Entregas por canal (con 10 % de fallos inyectados) | 7.980 enviadas, 0 fallidas | 7.694 enviadas, 0 fallidas |
| Duración de una consulta p50 / p95 | 0,57 / 0,86 ms | 1,12 / 1,66 ms |
| Ciclo completo del motor p50 / p95 | 32 / 94 ms | 74 / 212 ms |
| CPU consumida | 110 s | 173 s (solo el proceso Python) |
| Memoria pico del proceso | 76 MB | 86 MB |
| Verificaciones de integridad / violaciones | 59 / **0** | 59 / **0** |

Que las cifras difieran entre motores con la misma semilla es esperado: el orden de filas que
devuelve cada base cambia el consumo del generador aleatorio. Que PostgreSQL sea más lento aquí
también: es un solo proceso con la BD en local; su ventaja es la concurrencia real entre procesos,
que esta prueba no ejercita.

### 5.4 Dos (o 32) operadores por el mismo recurso
32 hilos lanzan 1.920 intentos simultáneos sobre 10 recursos de 3 unidades (30 en total):

| | SQLite | PostgreSQL |
|---|---|---|
| Asignaciones logradas | 30 | 30 |
| Rechazadas por conflicto | 1.890 | 1.890 |
| Unidades retenidas en BD | 30 | 30 |
| Violaciones de integridad | 0 | 0 |
| Intentos por segundo | 724 | 362 |

Además: `test_concurrent_take_same_assignment` (8 hilos toman la misma asignación → solo 1 lo
logra) y `test_same_request_two_slots_concurrently`.

### 5.5 Servidor en vivo (60 s, 8 fuentes cada 5–10 s, panel consultando cada segundo)
CPU media **5,0 %** (máx. 7,3 %) de un núcleo; memoria **129 MB**; latencia de la API p50/p95:
`/api/dashboard` 34/45 ms, `/api/requests` 22/30 ms, `/api/alerts` 20/42 ms
(`docs/resultados/servidor_en_vivo.json`).

### 5.6 Reinicio inesperado
Manual: con el servidor en marcha se hizo `kill -9`; antes y después: 44 solicitudes, 6 alertas,
3 asignaciones, 70 eventos (idénticos). Automática: `tests/test_restart.py`.

## 6. Problemas descubiertos y limitaciones (honestas)

**Corregidos durante el desarrollo (encontrados por las pruebas):**
1. Reprocesar un evento tras un reinicio generaba una segunda alerta. Corregido (comprobación por evento).
2. Carrera entre la reevaluación periódica y el procesamiento de eventos producía una alerta
   engañosa "sin solicitud compatible" cuando la unidad ya se había asignado. Corregido.
3. Panel: un repintado automático podía ignorar un filtro recién elegido. Corregido.

**Abiertos / riesgos:**
1. **No verificado en Windows.** El código evita dependencias específicas de Linux y las rutas
   usan `pathlib`, pero `instalar.ps1` e `iniciar.bat` no se han ejecutado en un Windows real.
   Es lo primero a comprobar.
2. **Crecimiento del historial.** Con 8 fuentes cada 5–10 s, `poll_log` crece ~100.000 filas/día.
   Falta una tarea de retención (p. ej. conservar 7 días o resumir). Impacto: disco y lentitud
   progresiva del panel en semanas.
3. **Volumen de alertas.** En la simulación de 24 h hubo ~4.000 alertas (de ~9.500 consultas):
   cada consulta no alerta, pero el simulador cambia mucho. En uso real conviene ajustar
   "Cambios que generan alerta" y el umbral de aumento para evitar fatiga de alertas.
4. **Sin migraciones de esquema** (Alembic). Cambiar tablas en una próxima versión exigirá migrar
   datos a mano. Recomendado antes de tener datos que importen.
5. **Un solo proceso.** Dos instancias contra la misma BD no duplicarían asignaciones (las
   garantías están en la BD), pero sí consultarían dos veces cada fuente. El límite de
   solicitudes vive en memoria y se reinicia al arrancar.
6. **SQLite serializa todas las transacciones** (`BEGIN IMMEDIATE`). Suficiente para esta
   escala; para varios usuarios concurrentes y más fuentes, PostgreSQL.
7. La latencia de detección "virtual" de la simulación acelerada (p50 = 0 s) es un artefacto
   del paso discreto; la cifra realista es la del navegador (1–5 s con intervalo de 5 s).
8. Seguridad pendiente para exponerlo fuera del equipo: HTTPS, limitación de tasa global de la
   API, rotación de claves. Telegram y correo: solo interfaz.

## 7. Instrucciones de ejecución
Ver `README.md` (Windows paso a paso, PostgreSQL opcional, recorrido de aceptación de 5 minutos).

## 8. Próxima fase recomendada (por orden de valor)
1. **Validar en tu Windows** (instalación, arranque, `pruebas.bat`) — 30 min, riesgo alto si se omite.
2. **Repositorio Git privado** para versionar el código (lo creas tú en GitHub y yo lo subo).
3. **Retención de historial + Alembic** — evita problemas de crecimiento y facilita evolucionar.
4. **Telegram** como primer canal real (barato, útil para operadores en el teléfono).
5. Solo entonces, **el primer conector real** a una API *autorizada*, siguiendo `docs/CONECTORES.md`.
   No recomiendo empezar por varios proveedores a la vez: primero medir con uno.
