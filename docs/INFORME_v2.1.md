# Informe técnico · v2.1 — Planificador diario y observador visual de calendarios

Rama `fase-2.1`, creada desde `fase-2.0`. **No se fusionó con `master`** ni con ninguna otra rama.
Todo se ejecutó con **datos ficticios** y con el **portal de laboratorio propio** (127.0.0.1). No se
conectó ningún servicio externo ni se probó contra un portal real.

Etiquetas: **MEDIDO** (ejecutado aquí, con su archivo de resultados), **SIMULADO** (lo hace un
script en lugar de una persona o de un sistema real), **ESTIMADO** (juicio, sin medición),
**PENDIENTE** (no se hizo).

## 1. Resumen

| Pedido | Estado | Evidencia |
|---|---|---|
| Riesgo: dos instancias con distinto portal sobre la misma base | **Corregido en `fase-2.0`** (e14105d) | `tests/test_proveedor.py` (falla con el código anterior); suite 590/4/0 |
| P1 Planificador diario | Hecho (API, sin pantalla) | `tests/test_planificador.py`; recorrido §6 |
| P2 Observador visual genérico (DOM frente a imagen) | Hecho sobre el portal de laboratorio | `tests/test_observador.py` (Chromium real); recorrido §6 |
| P3 Motor de coincidencias | Hecho | pruebas de concurrencia en SQLite y PostgreSQL |
| P4 Recorrido integrado | Hecho con procesos reales; **confirmación humana SIMULADA por el script** | `docs/resultados/v2.1/recorrido_v21_*.json` |
| P5 Resistencia | Hecho lo pedido salvo lo indicado en §8 | pruebas + recorrido (PostgreSQL detenido de verdad) |
| GitHub Actions | 22/22 en verde en la última corrida, tras corregir un defecto real que CI encontró | §9 |
| Pruebas en el MSI | **PENDIENTE** (no se pidieron en esta fase) | — |

La fase **no está terminada**: faltan las decisiones y el trabajo de §10 (sobre todo una pantalla
para el operador y el caso de un día con más de una plaza).

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

## 8. Lo que NO se ha probado (o no se resolvió)

1. **Ningún portal real.** El observador es genérico por perfil, pero solo se probó con la página
   del laboratorio, que diseñamos nosotros.
2. **Confirmación humana real:** en el recorrido automático la hace el script (SIMULADA).
3. **Windows:** solo lo que corra en GitHub Actions (Windows Server). Nada en el MSI.
4. **Un día con varias plazas** genera un evento y se asigna **un** expediente; la segunda plaza no
   produce un evento nuevo (el cambio de «libres» no es una transición). Es una limitación real.
5. El observador vive dentro del proceso del motor y **no se relanza solo tras reiniciar el motor**:
   hay que volver a pedirlo (`POST /api/observador`). Su configuración no se guarda.
6. Durante una caída de la base, las líneas del observador esperan en memoria (máx. 5000); si el
   motor muere a la vez, se pierden (al reabrir, el estado INICIAL recupera lo que siga visible).
7. Si además hay una fuente de calendario por API configurada, el motor puede asignar por su cuenta
   un expediente que está en el plan. No se duplica nada (gana uno, el otro recibe 409), pero
   compiten. En el recorrido no se configuró esa fuente.
8. **No hay pantalla** para el plan ni para las coincidencias (se pidió no hacer otra consola); el
   operador usa la API. Ventanilla no las muestra todavía.

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
| Pantalla del operador | Sin ella el plan no sirve en la práctica | Mostrar «coincidencias» dentro de Ventanilla (no una consola nueva) | ESTIMADO 1–2 días |
| Días con varias plazas | Se aprovecha una sola plaza por evento | Evento por cambio de «libres» y asignación por plaza | ESTIMADO 1 día |
| Relanzar el observador | Tras reiniciar el motor no observa | Guardar su configuración y relanzarlo al arrancar | ESTIMADO 0,5 días |
| Portal real | Estructura desconocida; puede prohibir la automatización | Solo con autorización escrita del proveedor; si ofrece API, preferirla al observador | — |
| Competencia con la cola del motor | Asignaciones cruzadas | Excluir de la asignación automática los expedientes del plan del día | ESTIMADO 0,5 días |
