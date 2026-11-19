# Informe v2.2.2 · Integración y diagnóstico (2026-11-19)

Convención: **HECHO** (comprobado, con evidencia), **INFERENCIA** (razonable, no demostrado),
**PENDIENTE** (nadie lo ha comprobado). No se abrió una fase funcional nueva, no se fusionó con
`master`, no se desplegó, no se usaron datos reales ni servicios externos.

## 1. Ramas y commits

| Rama | Commit | Papel |
|---|---|---|
| `claude/project-thread-6fxbwl` | `eb74cfe` | Correcciones del Supervisor y de la prueba de despertar. **Referencia, no se toca.** |
| `claude/integracion-v2.2.2` | ver abajo | Rama de integración: v2.2.2 + correcciones + esta fase. |
| `claude/estabilizacion-v2.2.2` | `6d724e7` | v2.2.2; es antecesora de las dos anteriores. |
| `master` | `b7a8c51` | v1.0 histórica. Sin cambios. |

HECHO: `claude/integracion-v2.2.2` parte de `eb74cfe`, que contiene `6d724e7`. No hizo falta ninguna
fusión: todo está en una línea.

Commits de esta fase:

1. `7dc2f84`: el fijado de Jarvis no puede ocultar cambios del contrato; la suite `planificador` incluye el Supervisor.
2. `0993a47`: los recorridos no escriben en el `data/` del proyecto; diagnóstico de cambios no detectados.
3. `a57b7b0`: CI con la suite `planificador` por el ejecutor e investigación HTTP en Windows.
4. `1640dad` (solo documentación): fijaba `a57b7b0`, más este informe y sus evidencias.
5. `9f79a3c`: la guarda del fijado funciona en CI (ver 2.4). **Último commit de código; es el fijado vigente.**
6. `57bac72` y siguientes (solo documentación): vuelven a fijar en `9f79a3c` y actualizan este informe.

Desde `6d724e7` (la v2.2.2), en `monitoreo/captura`, `monitoreo/api` y `monitoreo/planificador` solo
cambió `monitoreo/planificador/observador.py` (+12 −1, la corrección del Supervisor). Expedientes,
consentimiento, revocaciones, Ventanilla y planificación siguen intactos. HECHO: lo dice `git diff --stat`.

## 2. Defectos corregidos en esta fase

### 2.1 El fijado de Jarvis podía ocultar cambios de código (gravedad: media)

- **Problema (HECHO).** `rutas_solo_documentacion` incluía todo `jarvis/`. Después del fijado se podía
  cambiar el manifiesto (qué comandos ejecuta Jarvis), el esquema o las políticas y argv de
  `project.yaml`, y el informe seguía diciendo `POSTERIOR_SOLO_DOCUMENTOS`. Además, ampliar las rutas
  en un commit posterior bastaba para esconder código (por ejemplo, añadiendo `monitoreo/`).
- **Impacto.** Jarvis podía dar por verificado un contrato distinto del verificado.
- **Corrección** (`monitoreo/interop/comun.py`):
  - El manifiesto y el esquema **siempre** cuentan como código.
  - De `project.yaml` solo el bloque `integracion` puede cambiar sin volver a fijar. Se comparan las
    dos versiones sin ese bloque.
  - Las rutas de documentación que valen son las del commit **fijado**, no las de HEAD.
- **Referencia autocontradictoria.** Un commit no puede contener su propio hash. La regla es que el
  commit fijado sea el último de código, y que el commit que lo escribe sea posterior y solo de
  documentación. El estado correcto es `POSTERIOR_SOLO_DOCUMENTOS`, y deja de ser contradictorio.
- **Cómo se comprueba.** `tests/test_integracion_v222.py` tiene 9 pruebas en repositorios git
  temporales: fijar en un commit posterior, cambiar el manifiesto, cambiar argv o políticas, cambiar
  solo `integracion`, ampliar las rutas. Además incluye una guarda sobre el repositorio real: en la rama
  declarada, HEAD debe ser `FIJADO` o `POSTERIOR_SOLO_DOCUMENTOS`.

### 2.2 Los recorridos dejaban archivos en `data/` del proyecto (gravedad: baja)

- **HECHO.** `scripts/integracion_http.py` y los demás que usan `base_env` arrancaban el motor sin
  `MONITOREO_DATA_DIR` y escribían `data/notificaciones.log` (~550 KB) en la carpeta del proyecto. Lo
  detectó la prueba `test_servidor_real_con_data_dir_no_toca_el_data_del_proyecto`.
- **Corrección.** `base_env` fija `MONITOREO_DATA_DIR` dentro de la carpeta temporal. Hay una prueba de
  regresión en `test_integracion_v222.py`. Después de la corrección, la suite completa terminó con
  `data_sin_cambios: true`.

### 2.3 Suite `planificador` sin las pruebas del Supervisor

- HECHO: `jarvis/test-manifest.json` incluye ahora `tests/test_supervisor_v222.py` en `planificador`.
  La prueba de regresión exige los cuatro archivos y el orden exacto de las suites.
- CI ejecuta `python -m monitoreo.interop ejecutar --suite planificador` en el trabajo `regresion-v222`
  (Linux y Windows, Python 3.11 y 3.13), por el mismo ejecutor que usa Jarvis.

### 2.4 Mi guarda del fijado fallaba en CI (gravedad: baja, defecto de la prueba)

- **HECHO.** En la CI de `1640dad`, las 5 suites principales (Linux 3.11 y 3.13, Windows 3.11, 3.12 y
  3.13) fallaron en una sola prueba: `test_el_repositorio_real_esta_fijado_cuando_se_esta_en_la_rama_declarada`.
  Daba `relacion: DESCONOCIDO` porque `actions/checkout` clona con profundidad 1 y el commit fijado no
  estaba en el clon. En `a57b7b0` no había fallado porque aún estaba declarada la rama anterior y la
  prueba se omitía. En local pasaba porque el clon es completo.
- **Corrección (`9f79a3c`).**
  - Las tres suites principales clonan la historia completa (`fetch-depth: 0`), así que la guarda se
    ejecuta de verdad.
  - En un clon superficial la prueba se omite y dice por qué. En un clon completo, `DESCONOCIDO` sigue
    siendo un fallo.
- **Cómo se comprobó.**
  - En un clon completo: 9/9 aprobadas.
  - En un clon superficial local (`git clone --depth 1`): la prueba se omite con el motivo «clon
    superficial».
- Para Jarvis: en una copia superficial el informe también diría `DESCONOCIDO`. La guía ya indica
  avisar a una persona en ese caso. La copia de desarrollo debe ser un clon completo.

## 3. Investigación: «Integración HTTP · Windows», caso OES-035 versión 92

### 3.1 El fallo original (HECHO, trabajo 135318440317)

- 14 de 15 comprobaciones correctas. Falló «cambios duraderos no detectados FUERA de ventanas de
  fallo»: 1 de 1206 cambios.
- El cambio: `OES-035`, región oeste, 0→1, motivo «liberación», versión 92, a las 03:37:24.576. Fue
  en la fase de carga, unos 5 s antes de que empezara la fase de fallos (03:37:29.535).
- En la misma ejecución, la **convergencia** pasó: `distintos: 0`. Al final, el motor veía exactamente
  lo que publicaba el proveedor. No hubo pérdida duradera de datos. También pasaron la integridad,
  Telegram (625/625) y las 21 liberaciones controladas.
- El ejemplo no llevaba duración del estado ni consultas cercanas, así que no se puede saber si se
  trató de un hueco de consultas, de un fallo del motor o de un error de la auditoría.

### 3.2 Reproducción

| Dónde | Ejecuciones | Fallos «fuera de ventanas» |
|---|---|---|
| Local (Linux), 4 de ellas con 6 procesos quemando CPU | 8 | 0 |
| CI Windows, trabajo bloqueante (a57b7b0) | 1 | 0 |
| CI Windows, investigación: 2 tandas × 3 | 6 | 0 |

HECHO: **no se reprodujo** en 15 ejecuciones. Todas tienen entre 1 y 10 «no detectados en ventanas de
fallo», que son esperables. Con 1 fallo conocido entre varias decenas de ejecuciones históricas, 15
ejecuciones no bastan para descartarlo.

### 3.3 Lo que encontré al leer el código

- **HECHO, defecto del proveedor de prueba, no del motor.** Cuando una ventana vence,
  `vista()` deja de contar sus unidades en cuanto pasa la hora, pero la versión de la región (y por
  tanto el ETag) solo sube cuando el bucle del proveedor procesa el vencimiento. En ese intervalo el
  proveedor puede contestar 304 a datos que ya cambiaron. El efecto es un **retraso** de como mucho un
  ciclo del bucle, no una pérdida, porque la versión sube después. No explica el caso OES-035, que fue
  una liberación sin ventana.
- **HECHO, limitación de la auditoría.** Un cambio cuenta como detectado solo si el motor tiene un
  evento con `observed_at` igual a la hora exacta del cambio. Hay una alternativa, «el motor tenía el
  valor», pero se comprueba solo al **final** del estado (`engine_value(rk, nxt)`) y resta los
  descuentos locales posteriores. Supongamos que el motor vio el valor con otra hora (por ejemplo, la
  hora de vencimiento de una ventana, que el conector usa como `changed_at`) y que después un operador
  completó una asignación de ese recurso. En ese caso la auditoría lo cuenta como perdido.
- **HECHO, limitación de la auditoría.** «En ventanas de fallo» solo mira si el inicio o el fin del
  estado caen dentro de una ventana. **No** lo amplié: el estado de OES-035 vivió unos 5 s con
  consultas sanas antes de la fase de fallos, y el motor debía verlo. Reclasificarlo escondería un
  defecto real.

### 3.4 Conclusión

- **No hay evidencia de un defecto funcional del motor.** El estado final convergió y no se reprodujo
  en 15 ejecuciones.
- INFERENCIA: lo más probable es un problema de **sincronización o de emparejamiento de la
  auditoría** (2.ª limitación de 3.3), no una pérdida del motor. No está demostrado.
- **No cambié umbrales ni la clasificación** para obtener verde.
- Lo que deja preparado para la próxima vez:
  - Cada «no detectado» lleva ahora un `diagnostico`: duración del estado, consultas cercanas con su
    resultado y duración, consultas correctas durante el estado, hueco máximo entre consultas
    correctas, eventos del motor de ese recurso (±30 s) y cambios vecinos.
  - Con eso, la próxima vez que ocurra se podrá decidir entre las tres hipótesis.
  - El trabajo `investigar-http-windows` (no bloqueante) repite el recorrido 6 veces en cada CI.
- **PENDIENTE (propuesta):**
  1. Corregir el proveedor de prueba para que el vencimiento de una ventana suba la versión al leer.
  2. Hacer que la auditoría compruebe el valor del motor **durante** el estado, no solo al final.
  3. Clasificar por tiempo de consultas sanas fuera de ventanas.

  La regla 3 es más estricta y podría hacer aparecer fallos nuevos. Las tres tocan cómo se juzga la
  prueba, así que conviene decidirlas aparte y con datos de diagnóstico.

## 4. Resultados de pruebas

| Prueba | Resultado | Evidencia |
|---|---|---|
| Suite completa por el ejecutor de Jarvis (`--suite completa --con-postgres`, árbol limpio en `a57b7b0`) | **876 pruebas: 870 aprobadas, 0 fallidas, 0 errores, 6 omitidas**, 893 s, `git_sin_cambios` y `data_sin_cambios` en `true` | `docs/resultados/v2.2.2/integracion/suite_completa_sqlite_postgres.json` |
| Omitidas | 4 carreras solo con PostgreSQL en su variante SQLite (las de PostgreSQL sí corrieron); la guarda de rama (en ese momento estaba declarada la rama anterior); 1 que requiere `jsonschema` (no instalado en el entorno local) | `salida.txt` del ejecutor |
| CI `a57b7b0` (run 44463222356) | ver 4.1 | GitHub Actions |

PostgreSQL local: 16, en 127.0.0.1, base `monitoreo_test`. No hay datos reales.

### 4.1 CI

Lo resume la respuesta que acompaña a este informe. HECHO, a la hora de escribir esto: todos los trabajos
terminados en Linux y Windows estaban en verde, entre ellos los tres de Integración HTTP. Los trabajos
largos seguían en curso; su resultado final y el del commit de documentación se comprueban en GitHub
Actions, no aquí.

## 5. Inventario: qué falta para el recorrido operativo de principio a fin en el laboratorio

Lo que ya existe (HECHO, todo corre en CI con datos ficticios):

- `demo_v22`: consentimiento → plan → observador → operación → folio confirmado por una persona,
  revocaciones y reinicio.
- `aceptacion_v21`, `demo_reservacion` y `demo_dia`.
- `integracion_http`: motor, proveedor y emulador de Telegram como procesos separados.
- `laboratorio_dias`, el portal de reservas de prueba, la Ventanilla, los instaladores PowerShell y la
  actualización desde v1.1–v1.6.
- El ejecutor de Jarvis con suites en lista blanca.

Huecos. Cada uno dice dónde encaja, para **no duplicar módulos**:

| # | Hueco | Dónde encaja | Tipo |
|---|---|---|---|
| 1 | Ninguna ejecución encadena alta del expediente → consentimiento → plan → detección → reservación → confirmación → cierre → retención y borrado | Un recorrido nuevo que **llame** a `demo_v22` y a `captura/retencion.py`, sin copiarlos | Laboratorio |
| 2 | Después de `RESERVACION_CONFIRMADA` solo existe `CERRADO`: no se registra si la persona asistió a la cita | `captura/expedientes.py` (estados y transiciones) | Funcional, **decisión de negocio** |
| 3 | No hay aviso a la persona trabajadora. Telegram solo avisa a operadores | `notifiers/` + consentimiento (canal y finalidad) | Funcional + legal |
| 4 | El respaldo y la restauración existen en instalar y actualizar, pero ningún recorrido los ejercita a mitad de operación | Paso de restauración dentro del recorrido 1 | Laboratorio |
| 5 | Equipo Windows físico (instalador, Ventanilla, observador): nunca probado | Guías existentes | **PENDIENTE**, necesita a una persona |
| 6 | Telegram real: nunca probado (solo el emulador) | `docs/TELEGRAM.md` | Necesita autorización |
| 7 | Aviso de privacidad, derechos ARCO, términos de FONACOT, cifrado de disco | `docs/ALCANCE_Y_AUTORIZACIONES_v2.2.md` | Legal, fuera del código |
| 8 | El proveedor real no está autorizado: todo usa el proveedor de prueba | `adaptadores/sin_integracion.py` ya bloquea | Necesita autorización |
| 9 | La guarda de fijado y la suite `interop` se apoyan en `jsonschema`, que es opcional (1 prueba omitida en local) | `requirements*.txt` del entorno de Jarvis | Menor |

INFERENCIA sobre los huecos 2 y 3: los detecté leyendo el código (las transiciones de
`expedientes.py` y quién recibe mensajes); no hay un requisito escrito que diga que hagan falta.

## 6. Propuesta para la siguiente fase operativa

**Recomendación: una fase de laboratorio, sin funciones nuevas**, con este orden:

1. **Recorrido único de principio a fin** (hueco 1 + hueco 4): un script que orqueste los módulos
   existentes con datos ficticios y una restauración de respaldo a mitad de camino. Se ejecutaría en CI
   en Linux y Windows. Coste bajo, porque reutiliza lo que ya existe. Es el mejor indicador de «listo
   para el laboratorio».
2. **Prueba en el equipo Windows físico** (hueco 5), con la guía existente y una lista de comprobación.
   Necesita a una persona, de 1 a 2 horas.
3. **Las 3 mejoras del juicio de `integracion_http`** (sección 3.4). Solo si la próxima vez que falle
   el diagnóstico apunta a la auditoría o al proveedor de prueba.

**Después, y solo con decisión humana:** los estados de asistencia (hueco 2) y el aviso a la persona
trabajadora (hueco 3). Cambian el modelo de expedientes y el consentimiento, y el aviso además tiene
implicaciones legales (finalidad del dato, canal). No conviene programarlos antes de definir el
requisito.

**No recomiendo todavía** conectar Telegram real ni el proveedor real (huecos 6 y 8). Faltan la
autorización y lo legal (hueco 7).

## 7. Riesgos que siguen abiertos

- El fallo intermitente HTTP Windows no está explicado. Su frecuencia es baja y el trabajo
  bloqueante puede volver a fallar alguna vez.
- `fijado_en` y `commit_fijado` se mantienen a mano. La guarda de CI detecta si se olvida volver a fijar
  en la rama declarada, pero no lo corrige.
- `jarvis/declaraciones.json` no se actualizó en esta fase, porque cambiarlo cuenta como código y
  obligaría a volver a fijar. Sus pendientes son los de la v2.2.2.
- Nada se ha probado en el Windows físico.
