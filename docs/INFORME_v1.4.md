# Informe de la versión 1.4: estabilización, auditoría y preparación para el MSI

Fecha: 13 de noviembre de 2026 · Rama `fase-1.4` (sale de `fase-1.3`; **sin fusionar en `master`**)
Repositorio: `dass-666/Cauce-monitor-engine` (privado)

Leyenda: **MEDIDO** (ejecución real con archivo de resultados) · **SIMULADO** (datos ficticios a
propósito) · **ESTIMADO** (deducción) · **PENDIENTE** (no verificable desde aquí).

## ¿ESTÁ LISTO PARA LA PRUEBA EN EL MSI?

**SÍ, para una prueba de instalación y uso con datos ficticios.** No para producción real.

Evidencia: GitHub Actions **15/15 trabajos en verde** (ejecución 43337690936, commit `a7db5ce`; el commit final solo añade este informe y los resultados, ver §8). Entre esos trabajos está la instalación desde cero y la
actualización real desde v1.0, v1.1, v1.2 y v1.3 en Windows (con Windows PowerShell 5.1, el que
trae Windows de serie, y PowerShell 7), y el arranque del servidor con los datos actualizados.
Localmente: **324 pruebas pasadas** (SQLite y PostgreSQL; 4 omitidas a propósito: las carreras que solo existen en PostgreSQL, que sí se ejecutan en PostgreSQL) y el panel con navegador real (escritorio y teléfono) 37/37.

Lo que **no** está verificado y por eso es la prueba que falta: **el MSI en sí** (GitHub Actions
usa máquinas Windows limpias, no tu equipo: antivirus, OneDrive, permisos o una versión de Python
distinta pueden comportarse de otro modo) y **Telegram real** (solo con el emulador).

## 1. Respuestas a lo pedido

| Pedido | Estado | Evidencia |
|---|---|---|
| P1-A Enfriamiento adaptable del circuito | Hecho y probado | §2.1, 10 pruebas, 2 rondas comparativas |
| P1-B Asignaciones abandonadas | Hecho y probado | §2.2, 11 pruebas + navegador |
| P2 Auditoría v1.0–v1.3 | 11 defectos reales corregidos, cada uno con prueba | §3 |
| P3 Optimización medida | 2 cuellos de botella reales (perfil del servidor) corregidos | §4 |
| P4 Resistencia | Escala 5/10/25 regiones, 25/100 fuentes, 10.000 solicitudes; 2 días virtuales con fallos; HTTP real en SQLite y PostgreSQL: sin violaciones | §5 |
| P5 Panel | 2 indicadores, botón Recuperar…, reintento seguro; escritorio y teléfono | §6 |
| P6 Paquete MSI | ZIP, guía corta, actualización desde cada versión probada en Windows | §7 |
| P7 Validación final | 324 pruebas locales y 15/15 trabajos de CI en verde; nada se marcó como aprobado sin ejecutarse | §8 |

## 2. Las dos correcciones prioritarias

### 2.1 Enfriamiento adaptable del circuito (hallazgo 7.1 de la v1.3)

Antes: tras 3 fallos seguidos la fuente quedaba en pausa **60 s fijos**, aunque el fallo
durase 12 s. Ahora (modo `adaptable`, por defecto):

- pausa = `max(enfriamiento_min_s, intervalo × enfriamiento_intervalos)` → con consulta cada 1 s y
  los valores por defecto (5 intervalos, mínimo 5 s), **5 s**;
- si la consulta de prueba vuelve a fallar, la pausa se **duplica** (10, 20, 40… hasta
  `enfriamiento_max_s` = 300 s);
- **una sola consulta de prueba**: el estado `SEMIABIERTO` se escribe en la base *antes* de
  consultar, así que ni otro hilo ni un reinicio lanzan otra a la vez;
- recuperación **gradual**: cada éxito resta una apertura (no vuelve a cero de golpe);
- el estado vive en la base: **sobrevive a reinicios**;
- un 429 / `Retry-After` durante la prueba se **respeta** y no cuenta como fallo; no hay cambio
  de identidad, proxies ni nada para eludir límites;
- la fuente solo pasa a `ERROR` (revisión manual) si además lleva 15 min sin ningún éxito;
- diagnóstico visible: columna *Circuito* del Monitor («EN RECUPERACIÓN · próxima prueba en…»),
  indicador «Fuentes en recuperación» en Inicio y causa en el diagnóstico de la fuente;
- el modo `fijo` conserva el comportamiento de la v1.3 (compatibilidad).

**Comparación MEDIDA** (escenario HTTP completo, misma semilla 13, SQLite, 2 rondas; fallos
de 12 s; `docs/resultados/v1.4/circuito/`):

| | Fijo 60 s (v1.3) | Fijo 10 s | **Adaptable (v1.4)** |
|---|---|---|---|
| Tiempo a ciegas, suma de 5 regiones (ronda 1 / 2) | 233 / 172 s | 141 / 142 s | **137 / 138 s** |
| Peor región (ronda 1 / 2) | 77 / 80 s | 40 / 47 s | **43 / 36 s** |
| Cambios no detectados durante fallos | 9 / 19 | 6 / 11 | 15 / 12 |
| Comprobaciones del escenario | 15/15 y 15/15 | 15/15 y 15/15 | 15/15 y 15/15 |

Lectura honesta: el adaptable **reduce a la mitad el peor hueco** respecto a la v1.3 y queda a la
par del fijo de 10 s, pero sin fijar un número para todas las fuentes (una fuente lenta tiene
pausas largas, una rápida cortas, y un fallo persistente no se martillea). El resto del tiempo a
ciegas ya no es el circuito sino los propios tiempos de espera de la red durante el fallo. Los
«no detectados durante fallos» varían mucho entre rondas (6–19) en los tres modos: con 2 rondas
no se puede afirmar que uno sea mejor en eso.

### 2.2 Asignaciones abandonadas (hallazgo 7.2 de la v1.3)

Caso real de la v1.3: el motor guardó una toma 13 ms antes de un corte brusco; nadie recibió la
respuesta y la asignación quedó EN_PROCESO sin que nadie la atendiera.

- **Toma idempotente**: si el mismo operador vuelve a pulsar «Tomar», recibe 200 (`ya_tomada`).
- **Identificador de operación persistente** (`Idempotency-Key`): el mismo intento repetido devuelve
  el resultado guardado (`repetida: true`) sin hacerlo dos veces; también el de un fallo. Tabla
  `operations` (migración 0005, solo añade), purgada a los 7 días.
- **Consulta tras perder la conexión**: `GET /api/operaciones/{clave}`. El panel reintenta solo
  con la misma clave si la red falla.
- **Detección** de EN_PROCESO sin actividad durante `inactividad_en_proceso_min` (30 min):
  alerta de sistema al operador y al administrador, una sola vez, y registro de auditoría.
- **Nunca se libera sola.** El operador pulsa «Sigo con ella» (anota actividad) o «Completar».
  El administrador tiene **Recuperar…**: elige «terminada» o «devolver a la cola», escribe el
  motivo y confirma; queda auditado y las alertas se dan por atendidas.
- Probado en los momentos críticos: respuesta perdida, caída antes de confirmar, dos envíos
  simultáneos con la misma clave, competencia con el administrador (`tests/test_asignaciones_abandonadas.py`)
  y en el escenario HTTP real (operadores que reintentan con la misma clave tras un corte).

## 3. Auditoría adversarial (P2): defectos encontrados

Solo cuento lo que reproduje. Cada corrección tiene prueba de regresión que fallaba antes.

| # | Gravedad | Defecto (desde) | Impacto | Corrección · prueba |
|---|---|---|---|---|
| 1 | **Alta** (solo PostgreSQL) | Cancelar, anular por falta de unidades y vencer solicitudes **sobrescribían** un cambio hecho a la vez por otro proceso (v1.0) | Una toma que había respondido 200 se perdía en silencio; una solicitud completada quedaba cancelada (la restricción de la base evitaba la doble liberación, pero respondía error 500) | Transición condicionada al estado leído (`ConcurrentChange` → 409/reintento) · `tests/test_carreras_postgres.py` (4 carreras forzadas; las 4 fallaban antes) |
| 2 | Media | Un destino de alertas colgado **paraba el planificador** (v1.0) | Con un webhook o Telegram que no responde, cada envío esperaba su tiempo (5–10 s) dentro del ciclo: sin consultas, sin asignaciones, sin renovar el liderazgo durante minutos; los demás canales esperaban detrás | Envío en tarea aparte, presupuesto por pasada (`despacho_presupuesto_s`), canal caído se aplaza sin gastar intentos · `tests/test_alertas_destino_caido.py` (3, fallaban antes) |
| 3 | Media | **Restaurar** copiando el archivo (método del README) mezclaba un `monitoreo.db-wal` de un cierre brusco con el respaldo (v1.0) | La base «restaurada» contenía datos posteriores al respaldo o quedaba dañada | `python -m monitoreo.migrate restaurar <archivo>`: verifica, se niega con el motor en marcha, guarda la actual · `tests/test_restaurar.py` (incluye una prueba que reproduce el defecto) |
| 4 | Media | Webhooks **reutilizables**: la firma no llevaba marca de tiempo (v1.1) | Quien capturase una petición podía reenviarla | `X-Firma-V2` = HMAC(marca.cuerpo) + `X-Marca-Tiempo`; la antigua se mantiene · `tests/test_webhook_firma.py` |
| 5 | Media | Un operador podía **cancelar cualquier solicitud**, también la que otro atendía (v1.0), en contra de lo documentado | Trabajo de otro cancelado | Solo lo que registró o tiene asignado (admin, todo) · `test_operator_cannot_cancel_…` |
| 6 | Media | Cambiar la contraseña **no cerraba las otras sesiones** (v1.0) | Una sesión robada seguía valiendo | Se cierran todas menos la actual · `test_password_change_closes_other_sessions…` |
| 7 | Baja | Parámetros del motor **sin límites** (v1.1) | `tick_segundos: 0` = bucle a máxima CPU | Rangos por parámetro · `test_engine_params_out_of_bounds_are_rejected` |
| 8 | Baja | Dos arranques simultáneos sobre una base SQLite vacía: el segundo caía (`UNIQUE sources.code`) (v1.2) | Visto 2 veces en CI; en el MSI, dos `iniciar.bat` a la vez | Comprobar y sembrar en la misma transacción · `test_two_instances_bootstrapping…` (intermitente; no lo reproduje localmente en 25 intentos, lo corregí por lectura del código) |
| 9 | Alta (rendimiento) | **Introducido por mí** en la corrección #1 y detectado por el perfil: cada transición recorría toda la sesión | Mantenimiento de 2.000 solicitudes: 11,9 s y creciendo con el cuadrado | §4 · `test_maintenance_is_linear…` |
| 10 | Baja (prueba) | Prueba del autodiagnóstico dependiente de la hora: fallaba cerca de medianoche UTC | Falso rojo | Hora fija en la prueba |
| 11 | Baja (banco de pruebas) | La comprobación de Telegram del escenario HTTP no admitía el repetido documentado tras un corte brusco del motor | Falso rojo en Windows (1 mensaje repetido en 1.099) | Se cuentan mensajes distintos y se admite como mucho 1 repetido por corte; una pérdida sigue fallando |

Revisado **sin encontrar defecto**: token de Telegram en registros (ya se ocultaba; añadí prueba),
acceso a archivos fuera de `data/feeds` (ya bloqueado), validación de entradas, rutas de
administrador, recuperación de entregas `ENVIANDO`, integridad referencial tras cortes.
Riesgos que no son defectos reproducibles se listan en §9.

## 4. Optimización medida (P3)

Método: perfil del **servidor real** con `py-spy` (muestreo, herramienta solo de desarrollo, no
es dependencia) durante el escenario de capacidad con 100 fuentes, y comparación con la v1.3 en
la misma máquina y los mismos escenarios (`scripts/capacidad.py`, 40 s cada uno, SQLite).

Hallazgos del perfil:
1. **57 % de la CPU** en una sola línea: la sincronización de la sesión en cada transición de
   estado durante el mantenimiento (defecto #9, introducido en esta fase y corregido).
2. El panel principal hacía **4 consultas por región** en cada refresco (cada 4 s por usuario):
   ahora 3 consultas agrupadas en total.
3. Los cambios detectados esperaban al siguiente tick (0,5 s) para asignarse: ahora una consulta
   con cambios despierta el ciclo (con 30 ms para agrupar).

MEDIDO, mantenimiento con N solicitudes en una sesión (`bench`, misma máquina):

| N | v1.3 | v1.4 antes de optimizar | v1.4 final |
|---|---|---|---|
| 1.000 | 0,08 s | 3,39 s | 0,22 s |
| 2.000 | 0,18 s | 11,92 s | 0,49 s |

La v1.4 final es ~2,7 veces más lenta que la v1.3 en este caso concreto: es el precio de la
protección contra carreras (una sentencia condicionada por transición, ~0,25 ms). Lo acepto
porque evita pérdidas de datos en PostgreSQL y sigue siendo lineal.

MEDIDO, servidor real (`docs/resultados/v1.4/capacidad_*.json`):

| Escenario | Versión | Consultas/min (esperadas) | Ciclos en 40 s | CPU media | Panel p50 / p95 | Asignaciones | Integridad |
|---|---|---|---|---|---|---|---|
| 10 reg · 100 fuentes · 10.000 solicitudes | v1.3 | 493 (600) | 52 | 61 % | 31 / 188 ms | 123 | correcta |
| | **v1.4** | **544** (600) | **124** | 59 % | **17** / 286 ms | 131 | correcta |
| 25 reg · 100 fuentes · 2.000 solicitudes | v1.3 | 530 (600) | 38 | 77 % | 65 / 475 ms | 241 | correcta |
| | **v1.4** | **613** (600) | **116** | **61 %** | **19 / 202 ms** | 290 | correcta |
| 10 reg · 100 fuentes · 2.000 solicitudes (aislada) | v1.3 | 594 (600) | 41 | 68 % | — / 213 ms | 120 | correcta |
| | v1.4 | 594 (600) | 104 | 61 % | 17 / 273 ms | 127 | correcta |

Lectura: con 25 regiones la v1.4 usa menos CPU, cumple el ritmo de consultas previsto y el panel
responde 3 veces más rápido en la mediana. El p95 del panel no mejora siempre (sube en 10.000
solicitudes): con más ciclos hay más competencia por la escritura en SQLite. El tiempo de
detección (p50 ≈ 1,3–2 s) no cambia: lo marca el intervalo de consulta de las fuentes (10 s en
este escenario), no el motor. Una ejecución de cada: indicativo, no estadístico.

## 5. Resistencia y recuperación (P4)

Tres tipos de prueba, con su naturaleza indicada:

| Prueba | Tiempo | Resultado |
|---|---|---|
| Capacidad, servidor real: 5 reg/25 fuentes/500 sol/5 operadores; 10 reg/100 fuentes/2.000 sol/10 operadores; 10 reg/100 fuentes/**10.000 solicitudes**; **25 regiones**/100 fuentes/2.000 sol (§4) | REAL, 40 s cada uno | 0 errores de API, integridad correcta en marcha y tras parar |
| Resistencia acelerada (`scripts/resistencia.py --dias 2`): 332 + 348 fallos de base de datos inyectados, 2 caídas de instancia, fuente caída 8 h, 2 destinatarios inalcanzables, 3 instancias compitiendo | **VIRTUAL**: 2 días en 199 s reales (×868) | 22 verificaciones de integridad, **0 violaciones**; nunca dos líderes; 0 envíos duplicados; 0 eventos sin procesar; 1.192/1.192 solicitudes completadas; 0 errores no inyectados |
| Escenario HTTP completo (motor, proveedor y emulador de Telegram como procesos; 1.000 solicitudes, 12 operadores; 429, cortes de conexión, proveedor que no responde, respuestas antiguas, cambios entre páginas, corte brusco del motor y del proveedor) en **PostgreSQL**, y 7 veces en SQLite | REAL, ~6 min cada uno | 15/15 comprobaciones en todas; 0 dobles tomas; 0 solicitudes con dos asignaciones vivas; convergencia exacta con el proveedor; cada alerta enviada llegó a los 2 chats sin repetidos |

Tiempos MEDIDOS de punta a punta (liberaciones controladas, p50):

| | Detección | Hasta la asignación | Diferencia (lo que tarda el motor) |
|---|---|---|---|
| v1.3, PostgreSQL | 713 ms | 1.246 ms | 533 ms |
| **v1.4, PostgreSQL** | 601 ms | **669 ms** | **68 ms** |
| v1.3, SQLite | 535 ms | 1.000 ms | 465 ms |
| **v1.4, SQLite** (escenario corto) | 614 ms | **653 ms** | **39 ms** |

La asignación llega ahora ~0,5 s antes porque la consulta con cambios despierta el ciclo (§4).
Durante el escenario hubo 12 reintentos de operadores con la misma clave tras una respuesta
perdida (`reintentos_con_clave`), sin ninguna doble toma.

## 6. Panel (P5)

Sin rediseño. Añadido (capturas en `docs/capturas/13–15`):
- Inicio: indicadores **«Asignaciones sin actividad»** y **«Fuentes en recuperación»**; si hay
  alguna, se resaltan y llevan con un clic a donde se resuelve.
- Solicitudes: tarjeta «⚠ Asignaciones sin actividad» con instrucciones en lenguaje claro;
  botones «Sigo con ella» (operador) y «Recuperar…» (administrador, con motivo y confirmación).
- Monitor: columna Circuito en palabras: NORMAL / EN RECUPERACIÓN (próxima prueba en…) / PROBANDO.
- Tomar, completar y rechazar reintentan solos con la misma clave si se corta la red.
Verificado con navegador real (`scripts/e2e_panel.js`): toma repetida con la misma clave, aviso
de inactividad, recuperación auditada, sin desbordamiento horizontal en teléfono (390 px) en
Inicio, Solicitudes y Salud, sin errores de consola.

## 7. Paquete para el MSI (P6)

- ZIP: `motor-monitoreo-v1.4.zip` (mismo contenido que la rama, sin datos).
- Guía de una página: `docs/GUIA_RAPIDA_WINDOWS.md`.
- Actualización probada en Windows con **los instaladores originales** de v1.2 y v1.3 y con datos
  creados por **su propio código** (`scripts/sembrar_version_anterior.py`); v1.0 y v1.1 con bases
  creadas por su código (`tests/fixtures`). Se comprueba: la carpeta anterior no cambia (huella),
  respaldo antes de migrar, mismas solicitudes y asignaciones EN_PROCESO, integridad, esquema
  `0005_v1_4`, autodiagnóstico sin críticos y arranque real del servidor con esos datos.
- Restauración segura de respaldos (defecto #3) documentada en README y en la guía.

## 8. Validación final (P7)

- **Pruebas locales: 324 pasadas** (SQLite y PostgreSQL 16), 4 omitidas a propósito (las carreras
  forzadas solo existen en PostgreSQL y allí se ejecutan). En la v1.3 eran 258.
- **GitHub Actions: 15/15 en verde** (ejecución 43337690936; detalle por trabajo en `docs/resultados/ci_github_actions.md`; en Windows 213 pruebas porque las de PostgreSQL se omiten salvo en el trabajo con PostgreSQL): pruebas en Linux (3.11/3.12/3.13, con
  PostgreSQL) y Windows (3.11/3.12/3.13), Windows con PostgreSQL, sintaxis y codificación de los
  scripts en PowerShell 5.1 y 7, instalación limpia y actualización desde v1.0 (PowerShell 5.1 y 7),
  **actualización desde v1.1, v1.2 y v1.3** (nuevo), escenario HTTP en Linux y Windows, panel con
  navegador real en Linux y Windows.
- Ejecuciones intermedias en rojo, todas investigadas y corregidas: 1 repetido de Telegram tras el
  corte brusco (comprobación demasiado estricta, defecto #11), la preparación del nuevo trabajo de
  actualización (comillas en PowerShell, archivos de demostración que faltaban, aviso tratado como
  error) y la siembra concurrente (defecto #8). No se quitó ni se relajó ninguna prueba del motor.
- Navegador real: 37/37 (desarrollo y producción, escritorio y teléfono).

## 9. Riesgos pendientes

1. **El MSI no está probado** (ver arriba). Es la siguiente prueba.
2. **Telegram real** nunca probado (solo emulador).
3. Un operador **ve todas las solicitudes** (no solo las suyas). Con datos ficticios da igual;
   con datos personales reales habría que limitarlo por región. No lo cambié porque altera el uso.
4. Los webhooks usan `MONITOREO_SECRET_KEY` si el canal no tiene su propio `secret`:
   recomendable un secreto por canal.
5. SQLite con 100 fuentes: el p95 del panel ronda 200–300 ms y sube con la carga de escritura.
   Para más de una máquina o más carga, PostgreSQL.
6. Entrega de alertas «al menos una vez»: tras un corte brusco puede llegar un mensaje repetido
   (documentado en `docs/NOTIFICACIONES.md`).
7. Las cifras de rendimiento son de una ejecución por escenario en un contenedor Linux de 4 CPU,
   no del MSI.

## 10. Qué probar en casa (MSI), en este orden

1. Instalar en `C:\monitoreo-1.4` siguiendo `docs/GUIA_RAPIDA_WINDOWS.md` (parte B si quieres
   conservar los datos de tu versión anterior). Anota si el autodiagnóstico dice OK/AVISO/CRÍTICO.
2. `iniciar.bat` → entra con admin → Inicio: deben verse 5 regiones y los indicadores nuevos en 0.
3. Laboratorio → libera una unidad → en unos segundos la solicitud pasa a ASIGNADO.
4. En Configuración pon `inactividad_en_proceso_min` en `1`; toma una asignación y espera ~1 min:
   debe aparecer «Asignaciones sin actividad»; prueba «Recuperar…». Vuelve a ponerlo en 30.
5. Estrecha la ventana del navegador del MSI (o F12 → icono de móvil) y comprueba que el panel
   se lee sin desplazar de lado. (El motor solo escucha en el propio MSI: un teléfono no llega.)
6. Cierra con Ctrl+C, vuelve a abrir: todo sigue ahí. Ejecuta `diagnostico.bat`.
7. Si algo falla, envíame `data\logs\instalacion-*.log` y la salida de `diagnostico.bat`
   (no contienen contraseñas).

## 11. Rama, commits y archivos

Rama `fase-1.4` (desde `fase-1.3`, sin fusionar en `master`). Commits:

- `a7db5ce` v1.4: siembra de demostración atómica (dos arranques simultáneos en SQLite chocaban en sources.code, visto en CI)
- `88efd37` v1.4: versión 1.4.0, capturas del panel, CI de actualización con las fuentes de demostración y avisos aceptados
- `1890642` v1.4 CI: actualización desde v1.1–v1.3 con script de comprobación; Telegram admite solo el repetido de un corte brusco
- `3b0ea0b` v1.4 rendimiento: transición sin recorrer la sesión (crecía con el cuadrado), panel principal con consultas agrupadas
- `5963d42` v1.4: restauración segura, destino de alertas colgado, permiso de cancelar, ciclo que despierta con cambios, panel de atención, actualización desde v1.1–v1.3
- `17f6d3a` v1.4 auditoría: carreras en PostgreSQL, firma de webhook con marca de tiempo, cierre de sesiones al cambiar la contraseña, límites de parámetros
- `5095546` v1.4: enfriamiento adaptable del circuito y asignaciones abandonadas
- (último) informe v1.4 y resultados

Archivos nuevos principales: `monitoreo/webhook_firma.py`, migración `0005_v1_4_asignaciones_operaciones.py`,
`scripts/sembrar_version_anterior.py`, `scripts/comprobar_actualizacion.py`, `docs/GUIA_RAPIDA_WINDOWS.md`,
`docs/INFORME_v1.4.md`, `docs/resultados/v1.4/`, bases `tests/fixtures/bd_v1_{1,2,3}.sqlite` y 10 archivos de
prueba nuevos (`test_circuito_adaptable`, `test_asignaciones_abandonadas`, `test_carreras_postgres`,
`test_webhook_firma`, `test_auditoria_seguridad`, `test_restaurar`, `test_alertas_destino_caido`,
`test_ciclo_despertar`, `test_actualizar_versiones`, `test_optimizacion`).
Modificados principales: `engine/resilience.py`, `engine/pipeline.py`, `engine/assignment.py`,
`engine/alerts.py`, `engine/scheduler.py`, `engine/params.py`, `api/routes.py`, `models.py`,
`migrate.py`, `security.py`, `simulator/seed.py`, `web/app.js`, `web/app.css`, `.github/workflows/ci.yml`,
`scripts/integracion_http.py`, `scripts/capacidad.py`, `scripts/e2e_panel.js`, README y documentación.

Reproducir: `python -m pytest -q` (con `MONITOREO_TEST_PG=<url>` también PostgreSQL) ·
`python scripts/integracion_http.py` · `python scripts/capacidad.py --solo e3,e5` ·
`python scripts/resistencia.py --dias 2` · `python scripts/verificar_arranque.py --e2e`.

