# Informe técnico · v2.1 — Planificador diario, observador de calendarios y «Plan del día» en Ventanilla

Rama `fase-2.1`, creada desde `fase-2.0`. **No se fusionó con `master`** ni con ninguna otra rama.
Todo se ejecutó con **datos ficticios** y con el **portal de laboratorio propio** (127.0.0.1). No se
conectó ningún servicio externo ni se probó contra un portal real.

Etiquetas: **MEDIDO** (ejecutado aquí, con su archivo de resultados), **SIMULADO** (lo hace un
script en lugar de una persona o de un sistema real), **ESTIMADO** (juicio, sin medición),
**PENDIENTE** (no se hizo).

## 1. Resumen

Este informe tiene dos partes. Las §2–§7 describen la **primera entrega** de la 2.1. Las §11–§16
describen la **segunda iteración**, que resolvió los cuatro pendientes que Diego autorizó tras
revisarla. Las §8–§10 están actualizadas a la segunda iteración.

| Pedido | Estado | Evidencia |
|---|---|---|
| Riesgo: dos instancias con distinto portal sobre la misma base | **Corregido en `fase-2.0`** (e14105d) | `tests/test_proveedor.py`; suite 590/4/0 |
| P1 (1.ª) Planificador diario | Hecho | `tests/test_planificador.py`; recorrido §6 |
| P2 (1.ª) Observador visual genérico (DOM frente a imagen) | Hecho sobre el portal de laboratorio | `tests/test_observador.py` (Chromium real) |
| P3 (1.ª) Motor de coincidencias | Hecho | pruebas de concurrencia en SQLite y PostgreSQL |
| **Pendiente 1 · Plan del día visible en Ventanilla** | **Hecho** (§11) | recorrido de aceptación con Chromium; 13 capturas |
| **Pendiente 2 · Varias unidades por fecha** | **Hecho** (§12) | 5 pruebas nuevas × 2 bases; aceptación (3, 2, 1, 2→0→1, 1 unidades) |
| **Pendiente 3 · Observador persistente + bloqueo de Playwright** | **Hecho**; el bloqueo se **reprodujo** y la corrección se midió (§13) | 5 pruebas con Chromium × 2 bases; 7/60 bloqueos sin candado, 0/750 con candado |
| **Pendiente 4 · Una sola autoridad de asignación** | **Hecho** (§14) | 5 pruebas nuevas × 2 bases (carrera plan contra cola, 8 rondas) |
| **Recorrido de aceptación** | **42/42 en SQLite y 42/42 en PostgreSQL** (local); confirmación humana **SIMULADA** por el script | `docs/resultados/v2.1/aceptacion_v21_*.json` |
| Suite completa | 626 pasan, 4 omitidas, 0 fallan (local, SQLite y PostgreSQL) + 20 de observador | §15 |
| GitHub Actions (23a2bd8) | **22/22 en verde**, con la aceptación 42/42 en Linux (SQLite y PostgreSQL) y en Windows 3.11, 3.12 y 3.13 | corrida 43556863228, §9 |
| Pruebas en el MSI | **PENDIENTE** (no se pidieron) | — |

Los cuatro pendientes están resueltos y demostrados en el laboratorio. **No** significa que la 2.1
esté lista para producción: siguen abiertas las limitaciones de §8, sobre todo que nada se ha
probado contra un portal real ni en el MSI.

## 2. Corrección previa en v2.0: identidad del proveedor

- Cada instancia calcula una huella del portal configurado (esquema, host normalizado —`localhost`
  = `127.0.0.1`—, puerto y contratos). La base registra la de la primera instancia
  (`settings.proveedor.identidad`); cada reservación guarda la suya (`reservaciones.proveedor`,
  migración 0012; las antiguas, nulas, cuentan como del proveedor registrado).
- Una instancia con otro portal: `recuperar()` deja intactas las reservaciones ajenas
  (`bloqueadas_otro_proveedor`) y lo registra; confirmar, enviar datos, elegir día y cancelar
  devuelven 409 `OTRO_PROVEEDOR`; preparar reservaciones nuevas se niega.
- Cada instancia anuncia cada 10 s a qué portal apunta (`instancias_motor`); el autodiagnóstico
  marca **CRÍTICO** una instancia incompatible o una instancia activa discrepante.
- Cambiar de portal a propósito: `POST /api/proveedor/adoptar` (administrador, auditado), que se
  niega mientras haya reservaciones a medias del proveedor anterior.
- **Límite que queda:** la recuperación la hace el líder. Si el líder es la instancia mal
  configurada, lo pendiente espera (sin dañarse) hasta corregir la configuración.

Además, en la corrida 43525793910 de `fase-2.0` falló una prueba en Windows/3.11: el
autodiagnóstico profundo tardó 6,2 s (límite 5 s). **El informe de v2.0 citaba la corrida verde
anterior sin advertir que la del propio commit del informe había fallado.** Causa probable
(inferencia, no medida aparte en Windows): se forzaba el inventario de interfaces, que en Windows
llama a PowerShell (hasta 6 s), aunque el portal esté en el mismo equipo. Corregido en 5c8ef8a.

## 3. P1 · Planificador diario (`monitoreo/planificador/plan.py`)

- Un plan por **fecha de jornada** (UNIQUE). Cada elemento: expediente, **prioridad** (1–5),
  **trámite**, **sucursales**, **fechas aceptables** y orden de alta.
- **Selección inicial automática de 6** (`capacidad` 6 por defecto): expedientes LISTO por prioridad,
  antigüedad e id. Se puede **ampliar** hasta 50.
- Rechazos con motivo, elemento por elemento: duplicado en el plan; vivo en otro plan
  (`activo_expediente_id` UNIQUE); **incompleto** o sin validar; en revisión, cerrado, confirmado o
  en manos de un operador; trámite distinto; sucursal que no es la del expediente ni una
  alternativa; fechas anteriores a la jornada o a más de 120 días; plan lleno.
- **Persistente**: todo está en la base (migración 0013). La prueba abre otra aplicación sobre la
  misma base y obtiene el mismo plan; el recorrido mata el motor (`kill`) y lo comprueba.

## 4. P2 · Observador de calendario (`scripts/observador_calendario.js` + `monitoreo/planificador/`)

**Qué se observa.** Una vista **pública y en vivo** que se añadió al portal de laboratorio
(`/observar/calendario`): la página se abre una vez y su propio JavaScript pide las celdas cada
`refresco_ms` (500 ms) y cambia solo las que variaron. Simula un calendario dinámico; **no describe
a ningún portal real**.

**Dos estrategias, en el mismo navegador y sobre la misma página (nunca se recarga):**

| | DOM | Imagen (visual) |
|---|---|---|
| Cómo detecta | `MutationObserver` sobre el calendario | Captura del calendario cada 250 ms; color de cada celda comparado con la **leyenda** de la propia página |
| Calibración | ninguna | posición de las celdas, una vez al abrir (se toma del DOM) |
| Evidencia | fragmento HTML de la celda + sha256 | captura PNG + sha256 + caja de la celda |
| Latencia medida (mediana, n=5) | 216–226 ms | 413–463 ms |
| Ve parpadeos de 1,5 s | sí | sí |
| Ve parpadeos de 150 ms | **no** | **no** |

Ninguna estrategia puede ver un cambio más corto que el refresco de la propia página: nunca llega al
DOM. La de imagen tarda más (intervalo de captura + análisis) y depende de que los estados se
distingan por color; la de DOM depende de que la estructura de la página sea estable. El
observador es **genérico por perfil** (selectores y atributos configurables), pero **solo se ha
probado con la página del laboratorio**.

**Eventos normalizados** (`observacion_eventos`): `DIA_DISPONIBLE`, `DIA_NO_DISPONIBLE` y `VISTA`
(ACTIVA, SESION_CADUCADA, DESCONECTADO, CONGELADO), con sucursal, trámite, fecha, instante de
detección (el de la página), estrategia, observador y evidencia. **Deduplicación:** solo una
transición crea evento (UPDATE condicional sobre `observacion_dias` y huella UNIQUE); la segunda
estrategia u otro observador que ve el mismo cambio se anota en `detecciones` del mismo evento.

**Estados que no se confunden:**
- *Sesión caducada*: la página recibe 401; el supervisor la reabre con espera creciente (2 s, 4 s…).
- *Desconexión*: la petición falla; nunca se interpreta como «no hay días».
- *Congelado*: la petición queda colgada y el sello «Actualizado» deja de cambiar (3–5 s).
- *Sin disponibilidad*: vista ACTIVA y ningún día disponible.

**Recuperación del navegador.** Si el proceso del navegador muere, el supervisor lo relanza con
espera creciente (1, 2, 4… 30 s). Un **vigilante** lo reinicia también si deja de latir 20 s o si
una reapertura no se confirma en 20 s. Las capturas de imagen y la navegación van **en serie**
(candado de página) para que no se bloqueen entre sí (ver §9).

## 5. P3 · Motor de coincidencias (`monitoreo/planificador/coincidencias.py`)

- Compatibles: elemento EN_ESPERA de un plan ABIERTO de la jornada, mismo trámite, sucursal y fecha
  aceptadas, expediente aún LISTO.
- Orden **determinista**: prioridad → orden de alta → id. Se asigna **uno** con UPDATE condicional;
  si otro evento lo tomó en ese instante, se intenta el siguiente. Los demás siguen EN_ESPERA.
- **Explicación** guardada en el evento y en el elemento: regla, elegido, compatibles que esperan,
  descartados con su motivo.
- Un evento se atiende una sola vez (`NUEVO → EN_PROCESO` condicional). Un expediente no se consume
  dos veces (UPDATE condicional + `activo_expediente_id` UNIQUE). Todo en una transacción.
- Si el día ya no está disponible al atender: `DESAPARECIDO`. Si desaparece tras asignarse y antes
  de preparar la operación: el elemento vuelve a EN_ESPERA con el motivo.
- **Asignar no reserva nada ni habla con el portal.**

De la coincidencia a la cita (`operacion.py`): el operador toma la coincidencia
(`POST /api/planificador/elementos/{id}/preparar`, UPDATE condicional: si dos operadores lo
intentan, uno recibe 409), el expediente pasa a él por la misma vía que ventanilla y sigue el flujo
normal: campos manuales tecleados, día, confirmación expresa y folio comprobado por la API del
portal. El plan refleja el resultado (CONFIRMADO, o EN_ESPERA si el intento terminó sin cita).

## 6. P4 · Recorrido integrado — MEDIDO

`scripts/recorrido_v21.py`: portal y motor como procesos; el motor lanza el observador (Chromium).
Resultados: `docs/resultados/v2.1/recorrido_v21_sqlite.json` (33/33) y
`recorrido_v21_postgresql.json` (35/35, con PostgreSQL detenido de verdad).

1. 8 expedientes ficticios LISTOS + 1 incompleto. Plan de 6 por prioridad; incompleto y duplicado
   rechazados; 7.º rechazado por plan lleno; ampliado a 8.
2. Calendario abierto sin días: 4 s, ningún evento de día, los 8 EN_ESPERA, vista ACTIVA.
3. 5 fechas nuevas habilitadas: las 5 detectadas por las dos estrategias; las 5 asignadas al
   elemento que dicta la regla (comprobado contra el cálculo independiente del script).
4. Operación preparada → datos con campos manuales → día → **confirmación (SIMULADA: la «persona» es
   el script)** → CONFIRMADA con folio → el folio existe CONFIRMADO en el portal (consulta aparte)
   → el plan queda CONFIRMADO. Antes de confirmar, el plan seguía en OPERACION_PREPARADA.
5. Día que desaparece tras asignarse: el expediente vuelve a EN_ESPERA; preparar da 409.
6. Parpadeos: 150 ms no visto por ninguna; 1,5 s visto por ambas y, al desaparecer, nada queda
   asignado a ese día.
7. Motor matado (`kill`) y reiniciado en ~1,1 s: plan idéntico; al reabrir el calendario no se
   duplican eventos; se sigue detectando y asignando.
8. PostgreSQL detenido mientras se habilita un día: salud 503; al volver, el cambio se registra
   una vez (5,2 s desde que se habilitó, por la espera de la caída).

| Medición (ms desde que el laboratorio habilita el día) | SQLite | PostgreSQL |
|---|---|---|
| Detección DOM (mediana / máx.) | 226 / 239 | 216 / 274 |
| Detección por imagen (mediana / máx.) | 463 / 469 | 413 / 465 |
| Hasta la asignación (mediana / máx.) | 231 / 244 | 224 / 280 |
| Operación preparada → folio (API, 1 caso) | 125 | 167 |

n=5 por base: **muestra pequeña**; sirve para comparar estrategias, no como cifra de servicio. La
latencia está dominada por el refresco de la propia página (500 ms): con un portal real ese
intervalo lo decide el portal.

## 7. P5 · Resistencia

| Escenario | Dónde | Resultado |
|---|---|---|
| Eventos simultáneos (6 eventos × 2 hilos, 1 expediente) | `test_planificador.py`, SQLite y PG | 1 asignación, 5 SIN_CANDIDATOS |
| Cambios que desaparecen (antes de atender, tras asignar, y reaparición) | pruebas + recorrido | sin asignación fantasma; vuelve a EN_ESPERA; reaparición = evento nuevo |
| Calendario congelado | `test_observador.py` | CONGELADO por ambas estrategias y vuelta a ACTIVA |
| Caída de PostgreSQL | recorrido (PG detenido de verdad) | 503; registro diferido sin duplicar |
| Reinicio del navegador (kill) | `test_observador.py` | relanzado; lo cambiado mientras estaba caído se detecta; lo ya visto no se repite |
| Reinicio del motor (kill) | recorrido | plan intacto; sin duplicados |
| Confirmación incierta | `test_planificador.py` | sigue OPERACION_PREPARADA hasta verificar; luego CONFIRMADO con folio |
| Dos operadores por la misma coincidencia | `test_planificador.py` | 200 y 409; el perdedor ya no la ve |
| Sesión caducada / desconexión del portal | `test_observador.py` | estados distintos; ninguno se toma como «agotado» |

## 8. Lo que NO se ha probado (o sigue abierto) — actualizado

Resueltos en la segunda iteración (antes eran los puntos 4, 5, 7 y 8 de esta lista): varias
unidades por día, observador que no se restauraba, competencia con la cola del motor y la falta de
pantalla. Lo que sigue abierto:

1. **Ningún portal real.** El observador es genérico por perfil, pero solo se probó con la página
   del laboratorio, que diseñamos nosotros.
2. **Confirmación humana real:** en los recorridos automáticos la hace el script (SIMULADA).
3. **Windows:** solo lo que corre en GitHub Actions (Windows Server, SQLite). Nada en el MSI.
4. **La estrategia de imagen no lee cantidades.** Si solo funciona la de imagen, cada día disponible
   cuenta como **1** unidad (prudente: puede asignar de menos, nunca de más). Las cantidades
   las aporta la estrategia DOM.
5. **Una operación ya preparada no se retira sola** si las unidades bajan. Si sobran operaciones
   preparadas para las unidades que quedan, se registra como «sobrecompromiso» y el portal decide
   al confirmar (la que llegue tarde recibe «día agotado» y vuelve a la espera). No hay doble cita
   porque el portal no la permite, pero el operador pierde ese intento.
6. **Restaurar tras una caída tarda unos 14–17 s** (MEDIDO): es lo que tarda en vencer el
   arrendamiento del proceso muerto (15 s). Tras un apagado ordenado, 0,7 s. Durante ese hueco no
   se observa el calendario; lo que cambie se detecta al reabrir (estado inicial frente a lo guardado).
7. **Bloqueo de Playwright:** se reprodujo y la corrección lo evita en 750 ciclos, pero la causa
   interna (dentro de Playwright/Chromium) no se investigó a nivel de protocolo. No se afirma que
   haya desaparecido para siempre; el vigilante sigue como respaldo (§13).
8. Durante una caída de la base, las líneas del observador esperan en memoria (máx. 5000); si el
   motor muere a la vez, se pierden (al reabrir, el estado INICIAL recupera lo que siga visible).
9. **Pantalla:** desde Ventanilla no se editan las fechas ni las sucursales de un elemento, ni se
   retira uno del plan (existe en la API). Un administrador sin operador vinculado ve el plan pero no
   prepara operaciones.
10. **Fin de jornada:** los expedientes no atendidos vuelven a la cola general al día siguiente;
    las operaciones ya preparadas siguen su curso y el plan no se cierra hasta que terminan.
11. Las pruebas con varios procesos del motor sobre la misma base usan dos supervisores en el mismo
    proceso de prueba (y el recorrido mata y relanza el motor real). No se probó con dos máquinas.

## 9. Commits y GitHub Actions

**`fase-2.0`** (sin fusionar): e14105d (identidad del proveedor), 5c8ef8a (autodiagnóstico en
Windows), 8ae2525 (defecto del script de resistencia, escenario C). CI 43538347390: **22/22 en verde**.

**`fase-2.1`** (sin fusionar):

| Commit | Qué | CI | Resultado |
|---|---|---|---|
| 92928b9 · e22ef09 | P1–P5, recorrido, pruebas, pasos nuevos de CI | 43540639238 | 20/22: falla la reapertura del observador y `demo_v20` en Windows 3.11 |
| 0d16fef | una sola reapertura por sesión caducada; `demo_v20` más robusto | 43542491882 | 19/22: el observador vuelve a fallar; `e2e_panel` falla una vez (pasa en la siguiente) |
| bd42201 | prueba con detalle completo | 43544642083 | 21/22: el detalle muestra que **el navegador no respondía tras la orden de reabrir** |
| 0d0d755 | vigilante: sin latido o reapertura sin confirmar en 20 s → reinicia el navegador | 43546663108 | Windows 3.11 y 3.12 fallan: el vigilante **sí** recupera (`reinicios_por_vigilancia: 1`), pero tarde para el plazo de la prueba |
| c3d949a | candado de página: captura de imagen y navegación nunca se solapan | 43547795901 | **22/22 en verde**, y otra vez **22/22** al repetirla (intento 2) |
| 00aa543 | informe de la 1.ª entrega | 43552616551 | 22/22 en verde |
| 531d91a | varias unidades, propiedad única, observador persistente | — | sin corrida propia: se subió junto con 23a2bd8 |
| 23a2bd8 | «Plan del día» en Ventanilla, aceptación, investigación del bloqueo | 43556863228 | **22/22 en verde**. Aceptación v2.1: **42/42 en Linux con SQLite, 42/42 en Linux con PostgreSQL y 42/42 en cada Windows (3.11, 3.12 y 3.13, SQLite)**. Observador con Chromium: 52 pruebas en Linux (dos bases) y 26 en cada Windows |

En la aceptación de Windows 3.11 se midió (MEDIDO, Windows Server de GitHub, no el MSI): asignación
en 238–576 ms, reinicio del motor en 2,5 s y restauración del observador en 17,7 s.

Lo que CI encontró era un **defecto real**, no una prueba frágil: tras caducar la sesión, la orden de
reabrir podía coincidir con una captura de imagen en curso y la página quedaba sin responder. La causa
exacta dentro de Playwright **no está demostrada** (inferencia: navegación y captura simultáneas
sobre la misma página); la corrección elimina esa simultaneidad y el vigilante queda como respaldo.
Evidencia local: 8/8 repeticiones por base, 4/4 con la CPU saturada. Evidencia en CI: dos
intentos verdes de la misma corrida. Con tan pocas repeticiones **no se puede descartar** que
vuelva a aparecer con baja frecuencia.

Pasos nuevos en CI: en «Reservaciones de laboratorio · Linux» y «· Windows» se añadieron las pruebas
del observador y del planificador (`pytest -vv`) y el recorrido v2.1 (en Linux, también con
PostgreSQL detenido y arrancado de verdad con `docker stop/start`).

## 10. Riesgos y siguientes pasos (recomendación)

| Tema | Riesgo | Recomendación | Costo |
|---|---|---|---|
| Portal real | Estructura desconocida; puede prohibir la automatización | Solo con autorización escrita del proveedor; si ofrece API, **preferirla** al observador | — |
| Pruebas en el MSI | Diferencias de Windows de escritorio frente a Windows Server | Probar el instalador con la 2.1 antes de cualquier piloto | ESTIMADO 0,5–1 día |
| Sobrecompromiso de operaciones preparadas | El operador pierde un intento si las unidades bajan | Avisar en Ventanilla cuando su día pierde unidades (no retirarle la operación) | ESTIMADO 0,5 días |
| Hueco de 15 s tras una caída | No se observa el calendario mientras vence el arrendamiento | Bajar el TTL a 5–8 s si el hueco importa (más escrituras en la base) | ESTIMADO 1 h + medir |
| Editar elementos desde Ventanilla | Hoy solo por API | Añadirlo **solo si** los supervisores lo piden en uso real | ESTIMADO 0,5 días |

Recomendación: **no** agregar más funciones antes de probar el MSI y de tener la autorización (o
la API) de un proveedor real. Lo construido ya cubre el flujo; lo que falta es evidencia fuera del
laboratorio.


---

# Segunda iteración · los cuatro pendientes

## 11. Pendiente 1 · «Plan del día» dentro de Ventanilla

**Qué se hizo.** Una sección nueva al principio de la lista de Ventanilla (`monitoreo/web/ventanilla.js`,
`planDelDia`). No es una consola nueva: usa la misma tabla, los mismos indicadores y la misma pantalla
de atención que ya usaban los operadores.

- **Administrador:** «Crear el plan de hoy» (entran los 6 de mayor prioridad), ampliar la
  capacidad y «Completar hasta la capacidad».
- **Todos:** el resumen «vivos / coincidencias / pendientes» y una tabla con expediente, prioridad,
  estado, día asignado y operador.
- **El detalle de un elemento:** sucursales y fechas aceptables, marcando las que ya están disponibles.
- **Operador:** «Preparar» en una coincidencia abre su expediente en la pantalla de atención de
  siempre, con el día asignado ya indicado. Si ya hay un intento PREPARADO, se abre directamente su
  revisión.
- **Permisos (los de la API, sin cambios):** crear, ampliar y llenar exigen rol
  administrador (403 para el resto; no existe un rol «supervisor»). Preparar exige un operador vinculado.
- API nueva de solo lectura: `GET /api/planificador/jornada` y `GET /api/planificador/elementos/{id}`.

**Capturas (MEDIDO, Chromium real, recorrido de aceptación)** en `docs/resultados/v2.1/capturas/`:

| Captura | Qué muestra |
|---|---|
| 70 | Ventanilla sin plan |
| 71 | El plan con 6 elementos |
| 72 | El plan ampliado a 8 |
| 73 | Detalle de un elemento con sus sucursales y fechas |
| 74 | Coincidencias listas para preparar |
| 76 | El calendario del portal con el día asignado |
| 77 | El folio |
| 78 | El plan tras las operaciones |
| 79–81 | Una atención cerrada sin cita, que vuelve al plan |
| 82 | El plan en un teléfono de 390 px, sin desborde horizontal |
| 83 | El estado final |

**Defecto encontrado y corregido.** Al reanudar una atención con un intento ya PREPARADO,
Ventanilla mostraba otra vez el botón «Preparar formulario». Ahora abre la revisión de ese
intento. Es un cambio de comportamiento visible para los operadores.

## 12. Pendiente 2 · Varias unidades por fecha

- **Qué se guarda.** El observador ahora guarda cuántas unidades libres ve en cada día
  (`observaciones_dia.libres`). La estrategia DOM las lee. La de imagen no lee cantidades, así que
  cada día disponible cuenta como 1.
- **Eventos.** Si cambia la cantidad, se genera un evento con las unidades anteriores y las nuevas.
  Un mismo cambio visto por las dos estrategias produce **un solo** evento. Esto se garantiza con una
  actualización condicionada a la versión de la fila.
- **Cupo.** El cupo de un día es el número de unidades libres menos las comprometidas. Se calcula
  con la fila del día bloqueada, así que dos procesos no pueden repartirse la misma unidad.
  Comprometidas son:
  - las ASIGNADAS;
  - las PREPARADAS con reservación viva o incierta;
  - las confirmadas después de la última lectura del portal.

  Con N unidades se asignan como máximo N coincidencias, a los de mayor prioridad. El resto queda en
  espera con su motivo (`SIN_CUPO`).
- **Baja inesperada (`UNIDADES_REDUCIDAS`).** Se devuelven a la espera primero las asignaciones de
  menor prioridad. Las operaciones ya PREPARADAS no se retiran: el exceso se registra como
  «sobrecompromiso» (limitación 5 de §8).
- **Recuperación.** Si una atención termina sin cita y su unidad sigue libre, el motor vuelve a
  ofrecerla (`REEVALUACION`).

**Pruebas (MEDIDO):** `tests/test_plan_unidades_propiedad.py` (5 de unidades), en SQLite y en
PostgreSQL:
- 3 unidades con 5 compatibles: se asignan exactamente 3;
- 8 hilos a la vez sobre el mismo día: nunca más asignaciones que unidades;
- una baja de 3 a 1 devuelve a la espera a los 2 de menor prioridad;
- un evento repetido no asigna dos veces;
- recuperación tras una baja.

`tests/test_observador_persistente.py::test_varias_unidades_vistas_por_dom` usa Chromium real.

**Defecto de la 1.ª entrega corregido:** con un solo cupo implícito, un día podía asignarse dos
veces si llegaban dos eventos seguidos. La prueba de determinismo de `test_planificador.py` lo
aceptaba. Se cambió: ahora el segundo evento da `SIN_CUPO`.

## 13. Pendiente 3 · Observador persistente e investigación del bloqueo

- **Configuración persistente.** Se guarda en la tabla `observadores_config` (migración 0014). Al
  arrancar, cada proceso del motor que no es de producción levanta un **supervisor**. El supervisor
  arranca lo configurado y detiene lo desactivado.
- **Sin duplicados.** Cada observador lleva un arrendamiento `obs:<clave>` de 15 s en la base. Un
  segundo proceso sobre la misma base no abre otro navegador: lo informa como «de otro proceso».
  Si el titular muere, otro proceso lo toma cuando vence el arrendamiento.
- **Límites:**
  - más de 8 relanzamientos del navegador en 10 min, o más de 10 reaperturas seguidas por sesión
    caducada sin volver a ACTIVA, detienen el observador;
  - queda en `DETENIDO_POR_FALLOS` con su motivo y no se reintenta solo.
- **MEDIDO:**
  - restauración tras una caída: 14,0–16,9 s (lo que tarda en vencer el arrendamiento);
  - tras un apagado ordenado: 0,7 s;
  - el reinicio del motor en el recorrido: 1,2–1,3 s.

**Bloqueo de Playwright** (`scripts/investigar_bloqueo_observador.py`, resultados en
`docs/resultados/v2.1/bloqueo_observador.json`). Se usó la CPU saturada (6 bucles ocupados en
4 núcleos) y reaperturas repetidas:

| Versión | Ciclos | Bloqueos |
|---|---|---|
| Sin candado (0d0d755) | 8 | 1 |
| Sin candado (0d0d755) | 60 | 7 (≈12 %) |
| Con candado (c3d949a) | 150 | 0 |
| Con candado, versión de esta fase | 600 | 0 |

- En el registro de Playwright del primer bloqueo, una captura de imagen empezó 12 ms antes de la
  navegación de reapertura y nunca terminó. La captura siguiente esperó 30 s.
- Esto **respalda** la hipótesis de que captura y navegación ocurrían a la vez. **No** se investigó
  dentro de Playwright a nivel de protocolo.
- Con 0 de 750 se puede decir que la frecuencia bajó mucho, pero no que sea cero. El vigilante
  sigue como respaldo.

## 14. Pendiente 4 · Una sola autoridad de asignación

**La regla** (`monitoreo/planificador/propiedad.py`). Un expediente tiene un único dueño en cada
momento: la cola general o el plan del día.

- **Entrar al plan** es un cambio condicionado de la solicitud, `ELEGIBLE|REINTENTO → EN_PLAN`. Se
  hace en la **misma transacción** que el alta del elemento. Si la cola ya lo asignó, o si está en
  otro plan, la operación se niega con 409 y el motivo.
- **`EN_PLAN` no es asignable por la cola** (`try_assign` lo excluye), y Ventanilla tampoco deja
  tomarlo fuera del plan (409 `EN_PLAN`).
- **Las transiciones:**
  - preparar desde el plan: `EN_PLAN → EN_VENTANILLA`;
  - atención sin cita: `EN_VENTANILLA → EN_PLAN` (vuelve al plan, no a la cola);
  - retirar del plan, fin de jornada o expediente que deja de ser elegible: `EN_PLAN → ELEGIBLE`.
- **Reconciliación.** El mantenimiento repara los estados que no cuadran: conflictos, huérfanas y
  planes de días pasados.

**Pruebas (MEDIDO):** 5 en `tests/test_plan_unidades_propiedad.py`, en las dos bases:
- una carrera entre el plan y la cola en 8 rondas, con un único ganador en cada ronda;
- la cola no toca lo que está EN_PLAN;
- la vuelta al plan tras una atención sin cita;
- retirar y fin de jornada devuelven a la cola;
- la reconciliación.

En el recorrido de aceptación, **ninguna** de las 8 solicitudes del plan pasó por la cola general.

## 15. Recorrido de aceptación, pruebas y métricas

**Recorrido de aceptación** (`scripts/aceptacion_v21.py` y `aceptacion_v21.js`). Motor y portal de
laboratorio corren como procesos separados, con Chromium real y 8 expedientes ficticios. Resultado
**MEDIDO: 42/42 en SQLite y 42/42 en PostgreSQL**. Pasos:

1. Desde Ventanilla, el administrador crea el plan (6), lo amplía a 8 y lo completa. El detalle
   muestra las sucursales y las fechas aceptables.
2. Se configura el observador persistente y el calendario se abre.
3. Aparecen días con 3, 2 y 1 unidades. Se asignan 3, 2 y 1 coincidencias, a los de mayor
   prioridad, con un solo evento por día.
4. Un día con 2 unidades cubre las 2 pendientes. El día se agota (las 2 vuelven a la espera) y
   reaparece con 1 unidad, que va a la de mayor prioridad.
5. Un operador lleva operaciones desde el «Plan del día» hasta el folio. La confirmación humana la
   hace el script: **SIMULADA**.
6. Una atención termina sin cita. El expediente vuelve **al plan** y su unidad se le vuelve a
   ofrecer.
7. Se deja una operación a medias, se **mata el motor** y se reinicia. El plan sigue intacto y el
   observador se restaura solo, sin duplicarse. La operación se termina con folio.
8. Se mata el navegador del observador. El supervisor lo relanza y la detección sigue.
9. **Resultado final:**
   - 8 folios distintos, cada uno comprobado CONFIRMADO en el portal de forma independiente;
   - ningún día con más citas que unidades;
   - todas las solicitudes COMPLETADO;
   - sin datos personales en los registros.

**Métricas (MEDIDO):**
- latencia desde que aparece un día hasta la asignación: 326–545 ms;
- reinicio del motor: 1,2–1,3 s;
- restauración del observador: 14,0–16,9 s.

**Recorrido v2.1** (`scripts/recorrido_v21.py`): **34/34 en SQLite** y **36 OK, 0 fallos en
PostgreSQL**. En PostgreSQL incluye detener y arrancar el servidor de verdad. Archivos
`recorrido_v21_*.json`.

**Suite local (MEDIDO, Linux):** 626 pasan, 4 se omiten y 0 fallan, en SQLite y en PostgreSQL. A
eso se suman las pruebas del observador con Chromium (incluidas las 5 nuevas de persistencia) en
las dos bases.

**Windows:** solo GitHub Actions (Windows Server con SQLite), ver §9. **Nada en el MSI.**

## 16. Defectos encontrados en esta iteración

| Defecto | Gravedad | Estado | Cómo se comprueba |
|---|---|---|---|
| Un día con una unidad podía asignarse dos veces si llegaban dos eventos (1.ª entrega) | Alta: dos operadores irían al mismo cupo | Corregido (cupo bajo bloqueo de la fila) | pruebas de unidades con 8 hilos en SQLite y PostgreSQL |
| La cola general y el plan podían tomar el mismo expediente | Alta | Corregido (estado EN_PLAN transaccional) | carrera de 8 rondas |
| Bloqueo de la página al reabrir con una captura en curso | Media: el observador se quedaba ciego | Mitigado (candado), con el vigilante como respaldo; causa interna no demostrada | 0/750 frente a 7/60 |
| Ventanilla ofrecía «Preparar formulario» para un intento ya PREPARADO | Baja | Corregido | recorrido de aceptación |
| El observador no se restauraba tras reiniciar el motor | Media | Corregido (configuración persistente y supervisor) | pruebas de persistencia; recorrido |
