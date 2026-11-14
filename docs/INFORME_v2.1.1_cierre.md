# Informe · Cierre de la v2.1 (rama `fase-2.1.1`)

Rama `fase-2.1.1`, creada desde `fase-2.1` (56d90a2). **No se fusionó** con ninguna rama.

En esta ronda:
- no se tocaron las reglas de asignación, coincidencias, unidades ni propiedad;
- no se tocó ningún servicio de producción, Mayra ni otro proyecto del servidor;
- no se conectó ningún portal externo;
- todo se ejecutó en el contenedor de desarrollo (Linux) y en GitHub Actions, con datos ficticios.

Etiquetas: **MEDIDO** (ejecutado aquí, con su salida), **REPORTADO** (lo midió Diego en su servidor;
no lo he reproducido), **INFERENCIA** (razonable, sin prueba directa), **PENDIENTE**.

## 1. Punto de partida (REPORTADO por Diego)

La primera validación integrada en su servidor Linux pasó, con estos resultados:
- 5 fechas detectadas y asignadas;
- recuperación tras reinicio en 15,4 s;
- detección DOM con mediana de 300 ms;
- asignación con mediana de 311 ms.

Pasó solo después de exportar a mano `PLAYWRIGHT_BROWSERS_PATH=/root/Cauce-laboratorio/.browsers`.
Playwright buscaba en `/root/.cache/ms-playwright`.

## 2. Qué se cambió, por punto de la misión

### 2.1 Playwright encuentra Chromium de forma persistente

**Causa (MEDIDO, reproducida).**
- El motor lanzaba Node sin decirle dónde está el navegador. Playwright usa
  `~/.cache/ms-playwright` salvo que `PLAYWRIGHT_BROWSERS_PATH` esté definida en **ese** proceso.
- Una variable exportada en una consola no llega a un motor arrancado desde otra, ni a uno
  arrancado tras reiniciar.

En una copia de la instalación con Chromium solo en `.browsers/` y la variable sin definir, el
diagnóstico da exactamente el mensaje del servidor:

> `Playwright busca Chromium en /root/.cache/ms-playwright/chromium-1243/… y no está`

**Corrección.**
- `monitoreo/navegador.py` fija un orden de búsqueda:
  1. `PLAYWRIGHT_BROWSERS_PATH`, del entorno **o de `.env`** (el motor ya cargaba `.env` al
     arrancar);
  2. `.browsers` de la instalación;
  3. la carpeta por omisión.
- `monitoreo/config.py` (el motor) y `scripts/verificar_arranque.py` (los recorridos) fijan
  `.browsers` si existe y la variable no está definida. Nunca pisan un valor definido.
- `scripts/preparar_navegador.py` instala `playwright@1.63.0` y Chromium (con y sin ventana)
  **dentro** de la instalación. Termina arrancando Chromium de verdad. Avisa si la variable solo
  existe en la consola y no en `.env`.
- `python -m monitoreo.navegador [--probar] [--json]` es el diagnóstico.
  - Comprueba Node, el módulo, el ejecutable y la versión sin ventana (`chromium_headless_shell`),
    que es la que lanza el observador.
  - Con `--probar`, arranca Chromium sobre `about:blank` y lo cierra.
- Autodiagnóstico: nueva área **Observador**. Avisa si hay observadores activos y no se encontrará
  Chromium, o si alguno quedó `DETENIDO_POR_FALLOS`, con su motivo. Sin observadores
  configurados no dice nada, para no añadir ruido.
- `demo_ventanilla.bat` ahora comprueba que Chromium **arranca**, no solo que exista
  `node_modules`, y si no, ejecuta el script.

**Comprobación (MEDIDO, en una copia de la instalación, sin exportar la variable):**

| Caso | Resultado |
|---|---|
| Chromium en `.browsers` | `navegador --probar` → LISTO, origen «carpeta .browsers de la instalación», arranque real OK. El proceso del motor ve la ruta. `test_observador_persistente.py` + `test_cierre_v21.py`: **20 pasan** |
| Control: `.browsers` borrada | el diagnóstico da el mensaje del servidor. `test_observador_persistente` **falla**, como se esperaba: es la condición original |

**INFERENCIA:** que `/root/Cauce-laboratorio` sea la carpeta de la instalación. Si lo es, el motor
encontrará `.browsers` solo. Si no lo es, basta con poner `PLAYWRIGHT_BROWSERS_PATH` en `.env`.
Se comprueba allí con `python -m monitoreo.navegador --probar`.

### 2.2 Diagnóstico de fallos del observador (ERROR de Node)

**Antes:**
- la salida de error de Node iba a `/dev/null`;
- las líneas `OBS {"tipo":"ERROR"}` del script se guardaban en memoria, pero no se registraban;
- el observador se relanzaba en silencio hasta el límite, con el motivo «se relanzó 9 veces».

**Ahora** (`monitoreo/planificador/observador.py`, `scripts/observador_calendario.js`):
- Cada línea ERROR se registra como `observador <nombre>: ERROR del navegador (<origen>): <mensaje>`,
  **saneada**.
- La salida de error de Node se lee en un hilo (también evita que el proceso se bloquee si la
  llena) y se guardan sus últimas 20 líneas, saneadas.
- Si Node termina con error sin emitir una línea ERROR, se registra el final de su salida de error.
- Si falta el módulo `playwright`, el script ahora lo dice con una línea ERROR (código 4). Antes
  solo salía por la salida de error, que se descartaba.
- El motivo de `DETENIDO_POR_FALLOS` incluye el último error, recortado para caber en los 200
  caracteres de la columna. No hizo falta migración.
- `GET /api/observador` muestra por observador `ultimo_error`, `errores` (últimos 20),
  `stderr_node` (últimas 5 líneas) y `errores_navegador`.

**Saneado** (`monitoreo/texto_seguro.py::sanitizar`):
- una sola línea, sin caracteres de control ni el marco de texto de Playwright;
- URL sin cadena de consulta;
- fuera: `token=`, `password`, `clave`, `Authorization`, `Bearer`, `cookie`, correos, teléfonos,
  CURP, RFC y secuencias de 8 o más dígitos;
- máximo 300 caracteres.

Conserva rutas, fechas, folios y puertos, que son lo útil para diagnosticar. Es una red de
seguridad, no un filtro perfecto.

### 2.3 `recorrido_v21.py` ante una respuesta sin «eventos»

**Antes:** `c.get(...).json()["eventos"]` caía con `KeyError: 'eventos'` si el motor respondía un
error (401, 503…). Se perdían el código HTTP y el JSON de resultados.

**Ahora:**
- `cuerpo_esperado(r, clave)` comprueba el código y la clave. Si algo falla, lanza
  `RespuestaInesperada` con un diagnóstico seguro. Ejemplo **MEDIDO** con el motor real sin sesión:

  > `GET /api/observaciones respondió HTTP 401; se esperaba la clave «eventos»; claves recibidas: detail; detalle: …`

- Nunca copia el cuerpo completo, que podría traer datos de expedientes. Si el cuerpo no es JSON,
  da solo su tipo y su tamaño.
- Se cambiaron los 18 accesos directos de `recorrido_v21.py` y los 11 de `aceptacion_v21.py`,
  además de los GET sin clave.
- Las esperas reintentan una respuesta inesperada y guardan el último diagnóstico.
- Si el recorrido se interrumpe, queda una FALLA con el diagnóstico (o con el tipo de error y su
  línea) y **el JSON se escribe igual**. El código de salida es 1.

**No verificado:** no tengo el registro de tu servidor, así que no sé qué respuesta exacta produjo
el `KeyError` allí. La corrección cubre cualquier código o cuerpo.

### 2.4 Pruebas de regresión: `tests/test_cierre_v21.py` (13 funciones: 15 casos en SQLite, 17 con PostgreSQL)

- **Saneado:** secretos, datos personales, controles y longitud, conservando lo útil.
- **`cuerpo_esperado`:**
  - casos 200, 503 con `detail`, clave ausente, no JSON y lista;
  - **401 real del motor** (`TestClient`), en SQLite y PostgreSQL.
- **Guardia:** ningún `.json()[` directo en los dos recorridos.
- **Ruta del navegador:**
  - orden de búsqueda;
  - `.env` fija la ruta;
  - falta la versión sin ventana;
  - `config` y `verificar_arranque` usan `.browsers` y nunca pisan la variable.
- **Diagnóstico real:** carpeta vacía frente a la buena, con arranque real.
- **Observador real con Chromium ausente:** registra el ERROR saneado y se detiene con «se relanzó
  2 veces … último error: browserType.launch: Executable doesn't exist …» (como máximo 200
  caracteres).
- **Script sin módulo `playwright`:** línea ERROR y código 4.
- **Node que falla sin línea ERROR:** se conserva su salida de error, saneada. En el registro no
  aparece `token=s3cr3t` ni el correo.
- **Autodiagnóstico:** silencio sin observadores; aviso sin Chromium; aviso de detenido por fallos
  con su motivo.

### 2.5 Guía reproducible

[`docs/GUIA_INSTALACION_LABORATORIO.md`](GUIA_INSTALACION_LABORATORIO.md), para Linux y Windows:
requisitos, Python, `.env`, navegador, PostgreSQL de laboratorio, ejecución, comprobaciones con
sus comandos y qué hacer si el observador no arranca.

### 2.6 Auditoría de Ventanilla y comprobaciones físicas

[`docs/COMPROBACIONES_WINDOWS_v2.1.md`](COMPROBACIONES_WINDOWS_v2.1.md): 8 hallazgos de la lectura
del código y 14 comprobaciones para el MSI, cada una con qué hacer, qué debe pasar y qué anotar.

**El hallazgo más relevante, A1:** Ventanilla no se repinta sola, para no borrar lo que el
operador teclea. Por eso una coincidencia nueva en «Plan del día» **no aparece hasta que el
operador hace algo**. No lo corregí: es un cambio de pantalla y esta ronda era de cierre.
Propuesta: un botón «Actualizar plan» (ESTIMADO, 1–2 h con su prueba). Lo decides tú.

## 3. Archivos

| Archivo | Cambio |
|---|---|
| `monitoreo/navegador.py` | **nuevo**: búsqueda y diagnóstico de Chromium |
| `monitoreo/texto_seguro.py` | **nuevo**: `sanitizar`, `cuerpo_esperado`, `RespuestaInesperada` |
| `scripts/preparar_navegador.py` | **nuevo**: instalación del navegador dentro de la carpeta |
| `tests/test_cierre_v21.py` | **nuevo**: 13 funciones de prueba (15 casos; 17 con PostgreSQL) |
| `docs/GUIA_INSTALACION_LABORATORIO.md`, `docs/COMPROBACIONES_WINDOWS_v2.1.md`, este informe | **nuevos** |
| `docs/resultados/v2.1.1/*.json` | **nuevos**: recorrido y aceptación de esta ronda (SQLite y PostgreSQL) |
| `monitoreo/config.py` | usa `.browsers` de la instalación si la variable no está definida |
| `monitoreo/planificador/observador.py` | stderr capturado, ERROR registrados y saneados, último error en el motivo y en el estado; Node con la ruta del navegador |
| `monitoreo/diagnostico.py` | área Observador |
| `scripts/observador_calendario.js` | línea ERROR si falta el módulo `playwright` |
| `scripts/recorrido_v21.py`, `scripts/aceptacion_v21.py` | respuestas comprobadas; FALLA con diagnóstico y JSON escrito igual |
| `scripts/verificar_arranque.py`, `scripts/investigar_bloqueo_observador.py` | misma ruta del navegador que el motor |
| `demo_ventanilla.bat` | comprueba que Chromium arranca; si no, ejecuta `preparar_navegador.py` |
| `.github/workflows/ci.yml` | los trabajos de laboratorio preparan el navegador con el script nuevo (en `.browsers`, sin la variable), lo diagnostican y ejecutan `test_cierre_v21.py` |
| `.gitignore` | `node_modules/` y `/.browsers/` |

No cambió nada en `coincidencias.py`, `propiedad.py`, `plan.py`, `assignment.py` ni en las
migraciones.

## 4. Pruebas ejecutadas (MEDIDO, Linux, contenedor de desarrollo)

| Qué | SQLite | PostgreSQL 16 |
|---|---|---|
| Suite completa (`pytest`) | 657 pasan, 4 omitidas, 0 fallan (las dos bases en la misma corrida, 11 min). Los 2 casos de `.browsers` al arrancar se añadieron después y se ejecutaron aparte | (misma corrida) |
| `tests/test_cierre_v21.py` (versión final) | 15 pasan | 17 pasan (2 usan la base y corren en las dos) |
| `recorrido_v21.py` | **34/34**; mediana hasta asignar 384 ms | **34/34**; mediana 276 ms |
| `aceptacion_v21.py` | **42/42**; asignación 226–554 ms; restauración del observador 16,9 s | **42/42**; 237–597 ms; 14,0 s |

Archivos en `docs/resultados/v2.1.1/`.

**GitHub Actions** (corrida 43589183718, commit 847d306): ver §6.

## 5. Pendientes reales

1. **MSI físico:** nada probado. Lista en `COMPROBACIONES_WINDOWS_v2.1.md`. Windows está verificado
   solo en GitHub Actions.
2. **Tu servidor:** comprobar allí `python -m monitoreo.navegador --probar` **sin** exportar la
   variable. Si `Cauce-laboratorio` no es la carpeta de la instalación, poner la ruta en `.env`.
3. **Hallazgo A1** (el plan no se actualiza solo): pendiente de tu decisión.
4. **Bloqueo de Playwright:** mitigado (0 de 750 ciclos en Linux), causa interna no demostrada.
   En Windows no se midió.
5. Lo que ya estaba abierto en la 2.1 sigue igual: portal real, confirmación humana real (en los
   recorridos es SIMULADA) y Telegram real (ver §8 de `INFORME_v2.1.md`).

## 6. GitHub Actions

PENDIENTE de completar con el resultado de la corrida 43589183718.
