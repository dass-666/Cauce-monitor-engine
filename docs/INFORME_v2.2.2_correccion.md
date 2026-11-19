# Informe de corrección v2.2.2: Supervisor y prueba de despertar

Fecha: 2026-11-19 · Rama: `claude/project-thread-6fxbwl`, que sale de `claude/estabilizacion-v2.2.2`
(6d724e7). Sin fusionar, sin despliegues, sin datos reales ni servicios externos.

Leyenda: **[HECHO]** comprobado con una prueba o una medición reproducible · **[INFERENCIA]** conclusión
razonada · **[PENDIENTE]** sin comprobar.

## 0. Punto de partida [HECHO]

- `claude/estabilizacion-v2.2.2` está en **6d724e7**, no en 51bbd3b. Los dos commits posteriores
  (1b1644a y 6d724e7) solo cambian `docs/` y el `commit_fijado` de `jarvis/project.yaml`. El código es
  idéntico al de 51bbd3b (`git diff --stat 51bbd3b 6d724e7`).
- La corrección parte de 6d724e7.

## 1. Resumen

| # | Defecto | Estado | Evidencia |
|---|---|---|---|
| 1 | El Supervisor relanza en el mismo ciclo un observador `DETENIDO_POR_FALLOS` | **Corregido** [HECHO] | 32 pruebas nuevas: 32/32 con la corrección, 28/32 fallan sin ella; carrera forzada con navegador real: 3/4 fallos antes, 5/5 aprobadas después |
| 2 | `test_changes_wake_the_cycle` sensible a las tareas de fondo | **Aislada** [HECHO]; causa del 0,51 s **no reproducida** [INFERENCIA] | Solape medido 20/20; umbral 0,4 s intacto; CI con 30 + 30 repeticiones (normal y CPU saturada) por sistema y versión |

## 2. Commits

| Commit | Contenido |
|---|---|
| `cd8dd34` | Corrección del Supervisor, `tests/test_supervisor_v222.py`, el xfail pasa a prueba normal y se corrige la carrera propia de `test_limite_de_reinicios_detiene_con_motivo` |
| `332431c` | Aislamiento de las tareas de fondo en `tests/test_ciclo_despertar.py` |
| `443126d` | CI: trabajo `regresion-v222` **bloqueante** en lugar de los dos trabajos de investigación |
| `96794a3` | Ajuste de mi propia prueba nueva tras la primera ronda de CI (ver 5.1) |
| (este) | Informe, evidencias y script de medición |

## 3. Defecto 1: Supervisor

### 3.1 Causa [HECHO]

`Supervisor.ciclo` (`monitoreo/planificador/observador.py`) leía la configuración una vez al principio.
Al detener un observador por fallos lo marcaba `DETENIDO_POR_FALLOS`, pero el segundo bucle usaba la
lectura vieja (`ACTIVO`) y lo volvía a arrancar en el mismo ciclo.

Al revisarlo apareció una segunda vía del mismo problema: entre esa lectura y el arranque, **otro
proceso** sobre la misma base podía detenerlo o marcarlo como fallido, y este proceso lo arrancaba igual.

### 3.2 Corrección (13 líneas, solo en `Supervisor.ciclo`)

1. Tras marcar un fallo, se actualiza la lectura local de esa clave a `(False, DETENIDO_POR_FALLOS)`.
2. Justo antes de arrancar se relee la fila. Si ya no está `activo` con estado `ACTIVO`, no se arranca y
   se suelta el arrendamiento.

Las dos partes son necesarias [HECHO]: con solo la primera, falla
`test_estado_terminal_escrito_por_otro_proceso_entre_lectura_y_arranque`.

### 3.3 Políticas que NO cambian (cada una con prueba)

- Límite de reinicios del observador (`max_reinicios` en `ventana_reinicios_s`), espera creciente y
  mensaje «se relanzó N veces». Se ejecuta el bucle real con un proceso falso: exactamente 3 lanzamientos
  con `max_reinicios=2`.
- Un fallo al iniciar (sin Node) queda `DETENIDO_POR_FALLOS` y no se reintenta en bucle.
- Un observador sano no se relanza en cada ciclo.
- `desactivar` deja `DETENIDO`. **Solo `configurar` reactiva** una configuración detenida, y pone los
  contadores a cero.
- Tras un apagado ordenado, lo `ACTIVO` se restaura. Un solo proceso ejecuta cada observador.
- Un fallo no arrastra a otros observadores.

### 3.4 Pruebas nuevas: `tests/test_supervisor_v222.py`

No necesitan Node ni navegador, así que corren igual en Linux y Windows. Con PostgreSQL corren también
en su variante `[postgres]`.

| Lo pedido | Pruebas |
|---|---|
| Fallos consecutivos | `test_fallos_consecutivos_no_reactivan_y_el_estado_persiste`, `test_un_fallo_no_arrastra_a_otros_observadores` |
| Límite de reintentos | `test_limite_de_reintentos_real_detiene_y_el_supervisor_no_lo_relanza`, `test_fallo_al_iniciar_sin_node_...` |
| Reinicio del Supervisor | `test_reinicio_del_supervisor_no_reactiva_lo_detenido_por_fallos`, `test_reinicio_ordenado_sigue_restaurando_lo_activo` |
| Recuperación tras reiniciar el programa | `test_recuperacion_tras_reiniciar_el_programa` (conexión nueva a la misma base) |
| Ejecución concurrente | `test_estado_terminal_escrito_por_otro_proceso_...` (determinista), `test_dos_procesos_concurrentes_...`, `test_ciclos_concurrentes_en_el_mismo_supervisor` |
| Persistencia del estado | `estado`, `activo`, `motivo` y `reinicios` comprobados en la base en las anteriores |
| Escenario intermitente | `test_repeticion_del_escenario_intermitente` ×20, con el hilo girando cada 5 ms |

### 3.5 La prueba con navegador real tenía su propia carrera [HECHO]

`test_limite_de_reinicios_detiene_con_motivo` esperaba que **su** llamada a `ciclo` registrara el fallo.
Pero el hilo del supervisor de la app también ejecuta `ciclo`, y a veces llega antes. Con el código
corregido y la prueba vieja, forzando el hilo a 0,05 s, falló 1 de 4 veces con `'fallidos': []`. No hubo
relanzamiento.

Ahora la prueba registra los resultados de **todos** los ciclos, los del hilo y los suyos. Comprueba lo
que importa sin depender de quién llega primero: el fallo se registra una sola vez y solo se creó un
observador. Es más estricta que antes, no menos.

Evidencia: `docs/resultados/v2.2.2/correccion/carrera_forzada_observador.txt`.

## 4. Defecto 2: prueba de despertar

### 4.1 Qué interfería [HECHO]

`scripts/medir_solape_despertar_v222.py` envuelve las tareas de fondo reales sin cambiarlas. Resultado:
el seguimiento de expedientes (`captura`), el envío de alertas y el mantenimiento de historial corrían
en hilos **a la vez que la consulta medida en 20 de 20 rondas**.

Evidencia: `docs/resultados/v2.2.2/correccion/solape_tareas_de_fondo.json`.

### 4.2 Corrección (solo la prueba)

Se sustituyen por funciones vacías las tareas que no son objeto de la prueba:
- recuperación inicial;
- envío;
- seguimiento;
- historial;
- autodiagnóstico;
- oyentes de calendario;
- anuncio del proveedor.

Es lo mismo que la prueba ya hacía con `maintenance`. **No se sustituye** lo que la prueba mide: el bucle
real, la espera, el despertar y el arrendamiento en la base.

- **El umbral sigue en 0,4 s.**
- La prueba comprueba además que el planificador **sigue lanzando** esas tareas: solo se vació su
  contenido.

### 4.3 Lo que sigue sin demostrar [INFERENCIA]

El 0,51 a 0,53 s original de CI Linux 3.13 **no se ha reproducido** nunca: el máximo medido fue 0,31 s en
v2.2.2 y 0,124 s ahora con la CPU saturada en local. El aislamiento elimina la única fuente de
interferencia **medida**, pero no puedo afirmar que fuera la causa de aquel 0,51 s. Si vuelve a fallar
con el aislamiento, quedaría descartada la contención con las tareas de fondo. Lo siguiente a mirar
sería:
- el planificador del sistema operativo del ejecutor (pausas de la máquina virtual);
- la escritura del arrendamiento en SQLite bajo disco lento.

El script `investigar_despertar.py` sigue en CI como medición informativa.

## 5. Validación

### 5.1 CI

**Primera ronda, 44452147825 sobre 443126d.**
- Mi prueba nueva falló 30 de 30 en Linux. Exigía que se lanzara también la tarea de historial, pero su
  primera vuelta depende de que `time.monotonic()` supere `mantenimiento_historial_cada_s`. En un
  ejecutor recién arrancado no ocurre.
- Era un error de la prueba, no del motor. Corregido en 96794a3.
- En esa ronda también falló `Integración HTTP · Windows` con 14/15: un cambio del proveedor no
  detectado fuera de las ventanas de fallo. En la ronda siguiente aprobó. Es un intermitente previo, sin
  relación con estos cambios: esa prueba no usa el Supervisor y la prueba de despertar no forma parte
  de ella [INFERENCIA]. Queda anotado como riesgo.

**Segunda ronda, 44453115552 sobre 96794a3: 28 de 28 en verde.** Detalle en 5.3.

### 5.2 Local, suite completa por el ejecutor de Jarvis

Sobre 96794a3, árbol limpio, SQLite y PostgreSQL 16:
- **867 pruebas: 862 aprobadas, 0 fallidas, 0 errores, 5 omitidas**, en 887 s.
- Integridad: `git_sin_cambios: true`, `data_sin_cambios: true`.

Las omisiones son:
- 4 son las variantes `[sqlite]` de `test_carreras_postgres.py`. Sí corren en `[postgres]`.
- 1 es la prueba que necesita `jsonschema`, que no está instalado en este contenedor. En CI sí corre.

Ya no hay `xfailed`. Evidencia: `docs/resultados/v2.2.2/correccion/suite_completa_resultado.json`.

### 5.3 Trabajo `regresion-v222` (bloqueante: una sola repetición fallida lo pone en rojo)

En Linux y Windows, con Python 3.11 y 3.13:
- `test_supervisor_v222.py` 5 veces;
- despertar 30 veces en condiciones normales;
- despertar 30 veces con la CPU saturada (2 procesos por núcleo);
- despertar 3 veces tras los archivos que la preceden;
- en Windows, además, el observador real 8 veces.

Ejecución **44453115552** sobre 96794a3 (código final; los commits posteriores son solo de documentos):

| Trabajo | Supervisor ×5 | Despertar ×30 | Despertar ×30 con CPU saturada | En contexto ×3 | Observador real ×8 |
|---|---|---|---|---|---|
| Linux · 3.11 | 0 fallos | 0 | 0 | 0 | (solo Windows) |
| Linux · 3.13 | 0 fallos | 0 | 0 | 0 | (solo Windows) |
| Windows · 3.11 | 0 fallos (32/32 cada vez) | 0 | 0 | 0 (174 aprobadas, 4 omitidas) | **0 de 8** |
| Windows · 3.13 | 0 fallos (32/32 cada vez) | 0 | 0 | 0 (174 aprobadas, 4 omitidas) | **0 de 8** (antes 8 de 16) |

Los trabajos Linux se leen por su estado: cada paso falla si una sola repetición falla, y todos
terminaron en verde. Los de Windows se leyeron además línea a línea en el registro.

Medición informativa en Windows 3.13, latencia máxima en segundos: normal 0,102; carga 0,156;
aislado 0,099; carga_aislado 0,122. El umbral es 0,4 s.

**Resto del CI en la misma ejecución:** los 24 trabajos habituales en verde, entre ellos Linux 3.11, 3.12 y 3.13 con PostgreSQL, Windows 3.11, 3.12 y 3.13, Integración HTTP Linux y Windows, Reservaciones, instalación, actualizaciones y Jarvis Linux y Windows. **Resultado: 28 de 28 trabajos en verde** (ejecución completa `success`).

**Lectura honesta [INFERENCIA].** Antes, Windows 3.13 fallaba 8 de 16 veces. Ahora da 0 de 16 (8 por
versión). Con esa frecuencia previa, la probabilidad de 0 de 8 por azar con el código viejo sería de
aproximadamente 0,5⁸ ≈ 0,4 %. Además, la causa está reproducida y corregida de forma determinista.
Para el despertar no hay frecuencia previa medible: el fallo original no se reprodujo nunca. El verde
confirma que no hay regresión, no que la causa del 0,51 s quedara demostrada.

### 5.4 Módulos que no se tocaron [HECHO]

`git diff --name-only 6d724e7 HEAD` incluye solo:
- `.github/workflows/ci.yml`;
- `monitoreo/planificador/observador.py`, únicamente `Supervisor.ciclo`;
- cuatro archivos de prueba;
- un script de medición;
- documentación.

No cambian expedientes, consentimiento, revocaciones, planificación del día (`plan`, `propiedad`,
`coincidencias`, `operacion`), `monitoreo/interop/` ni `jarvis/`.

## 6. Jarvis

- No se tocan los contratos. `project.yaml` sigue fijando la rama `claude/estabilizacion-v2.2.2` en 51bbd3b.
- Sobre esta rama, el informe para Jarvis dará `integracion.relacion = DISTINTO`. Es correcto: esta rama
  todavía no es la de integración.
- **Decisión de Diego:** si acepta la corrección, hay que llevarla a la rama de integración, o cambiar
  la rama declarada, y volver a fijar `commit_fijado` con una revisión de `project.yaml`.
- Recomendación, no aplicada porque toca el manifiesto: añadir `tests/test_supervisor_v222.py` a la suite
  `planificador` de `jarvis/test-manifest.json`. Hoy solo la cubre la suite `completa`.

## 7. Riesgos pendientes

| Riesgo | Gravedad | Cómo comprobarlo |
|---|---|---|
| El 0,51 s original no se reprodujo: la causa es una inferencia | Baja: solo afecta a la prueba | Que `regresion-v222` siga en verde en las próximas ejecuciones; si falla con aislamiento, mirar pausas del ejecutor |
| `Integración HTTP · Windows` falló 1 de 2 rondas (14/15) | Media: es una comprobación de detección de extremo a extremo | Repetir el trabajo varias veces y analizar el ejemplo no detectado (OES-035, versión 92) |
| Windows físico (MSI) sin validar | Media | W1 a W10 de `docs/JARVIS_REGISTRO.md` |
| Supervisores en dos máquinas con relojes distintos | Baja: ya existía; el arrendamiento usa la hora de cada proceso | Fuera del alcance de esta corrección |
