# Prueba conjunta Monitor ↔ Conector Universal (laboratorio)

Marcas: **[V]** verificado con una ejecución de esta rama · **[I]** inferido · **[P]** pendiente / no verificado.
Monitor: rama base `claude/lab-puerto-estable` `9e7f88d`. Conector Universal (solo lectura): `main` `aa1bb02`.
Alcance: laboratorio, datos ficticios, formulario propio del conector (`lab_forms/01_tradicional.html`) servido en `127.0.0.1`.
Esto es una **herramienta de pruebas** (`scripts/prueba_conjunta_conector.py`, `tests/test_prueba_conjunta.py`); `aislamiento.puede_lanzar()` sigue en `False` y el arnés no lo consulta [V: prueba por AST].

## 1. Verificación inicial
- Commits reales confirmados [V]: `git rev-parse HEAD` del conector = `aa1bb0295b32…`; Monitor = `9e7f88d`.
- Esquemas `solicitud` y `respuesta`: **idénticos** entre ambos repositorios [V]: byte a byte en Linux; en la CI de Windows la copia descargada tiene finales de línea CRLF (artefacto del checkout [I]) y la prueba compara con los finales normalizados y, además, el JSON interpretado. `VERSION`, `SALIDA` y `MAPA_ESTADO` del código del conector son iguales a los de Monitor [V].
- Estados exigidos:

| Situación | Conector (`estado`, salida) | Monitor | Verificado |
|---|---|---|---|
| Perfil alterado (1 byte) | `rechazado_no_autorizado` (3), `PERFIL_NO_COINCIDE`, 0 campos | RECHAZADA | [V] real |
| Solicitud repetida | `rechazado_solicitud_invalida` (6), `SOLICITUD_REPETIDA`; no pide la página | RECHAZADA (y el Registro no permite relanzar) | [V] real |
| Solicitud caducada | `rechazado_solicitud_invalida` (6), `SOLICITUD_CADUCADA` | Monitor no la lanza (`SOLICITUD_CADUCADA`) | [V] real |
| Preparación sin envío | `preparado_revisado_sin_envio` (0), `envio` todo `false`, 0 POST al formulario | PREPARADO | [V] real (Linux) |
| Resultado incierto (sin JSON / proceso terminado) | sin respuesta | INCIERTA; solo una persona concilia | [V] real (Linux) |
| Navegador cerrado sin revisión | ver hallazgos 2 y 3 | PREPARADO o ERROR, nunca éxito de envío | [V] real (Linux) |

## 2. Interoperabilidad (conector REAL como subproceso)
Entorno temporal aislado (directorio propio, `HOME`/`TMPDIR` propios, entorno mínimo sin variables ni `.env` de Monitor, sin acceso a su BD). Pasos ejecutados [V en Linux]: solicitud válida (validada con `validar_solicitud` de Monitor) → autorización simulada → `aprobar-perfil` + `preparar` del conector por stdin → relleno del formulario de laboratorio → respuesta validada con `validar_respuesta` (esquema, PII, huella, coherencia) → Registro de Monitor.
- Campos aprobados escritos en la página (leídos por CDP justo antes de cerrar): nombre, apellidos, correo, teléfono, país y casilla; **fecha de nacimiento y mensaje (no aprobados) quedaron vacíos** [V].
- Ningún POST llegó al servidor del formulario en ningún escenario: no hubo envío programático [V]. (El navegador sí pide `favicon.ico`; se ignora.)
- Ningún valor ficticio en stdout, stderr ni en ningún archivo del entorno temporal (perfiles, estado, auditoría, trabajo, temporales), con un control que demuestra que el detector sí encuentra un valor plantado [V]. **No** se inspeccionó el perfil temporal de Chromium *mientras* el navegador estaba abierto [P].
- Idempotencia: `solicitud_id` de un solo uso en el conector (reenvío rechazado sin abrir el navegador) y `clave_idempotencia` en Monitor (mismo contenido = mismo registro; contenido distinto = `IDEMPOTENCIA_CONFLICTO`) [V].
- Caducidad: solicitud caducada no se lanza y el conector también la rechaza [V]. «Nadie revisa»: `revision_caducada` (7) → CADUCADA [V].
- Resultado registrado solo en el entorno temporal (se borra al terminar).

## 3. Negativas ejecutadas [V]
Solicitud repetida · perfil alterado · estructura cambiada (etiqueta distinta → `detenido_estructura_cambiada`, 0 teclas, 0 POST) · campo no aprobado/ambiguo (`CAMPO_NO_DECLARADO`, nada escrito) · dominio externo (Monitor rechaza `PORTAL_NO_LOCAL`; el conector, si se le salta Monitor, `FORMULARIO_NO_AUTORIZADO`; no se contactó ningún servicio externo) · CAPTCHA (no admite perfil aprobado; `aprobar-perfil` sale con 4) · consentimiento revocado antes de lanzar (Monitor no lanza; el conector sale con 6 sin JSON) · revocación a mitad (Monitor termina el proceso → INCIERTA) · navegador que desaparece (→ `error_interno`/ERROR, no PREPARADO).

## 4. Hallazgos (no se alteró ningún criterio para pasar)
1. **SIGTERM no termina al conector mientras espera la revisión** [V: no salió en 30 s]. Se documenta con una prueba `xfail(strict)` que fallará (avisando) el día que el conector lo corrija. Consecuencia: Monitor debe matar el **árbol** de procesos tras una gracia breve (ya existe `detener_arbol`). En Linux, al matar solo al conector Chromium terminó solo (0 huérfanos en 1 corrida) [V]; en Windows **no verificado** [P].
2. **Cerrar la ventana no es aprobación humana**, pero la respuesta dice `preparado_revisado_sin_envio` («revisado»). Eso sobre-afirma [V por diseño de la respuesta]. Monitor no debe derivar «aprobado» de PREPARADO: debe exigir su propia confirmación explícita («revisé y apruebo») antes de cualquier uso posterior.
3. **Clasificación no determinista del cierre**: cerrar la página dio PREPARADO en 8 de 10 corridas y `error_interno` en 2 (hipótesis [I]: carrera entre el evento de cierre de página y la desconexión del navegador en `_esperar_revision`). Ambas salidas son seguras (nada se envió), pero no deben usarse para distinguir «cerró la persona» de «murió el navegador».
4. En Linux como `root` Chromium exige `--no-sandbox`; el conector no lo añade. El arnés lo añade solo si corre como root; en CI (usuario normal) no se usa.
5. El conector necesita un Chromium instalado por su propia versión de Playwright; en este contenedor la versión de pip no coincidía con el Chromium preinstalado y hubo que indicarlo con `CAUCE_BROWSER_PATH` (problema de entorno, no del contrato).

## 5. Simulación vs. ejecución real
| | Simulador de Monitor | Conector real |
|---|---|---|
| Navegador y formulario | no | sí (Chromium, relleno carácter a carácter) |
| Perfil | no existe | exige `aprobar-perfil` previo; sha256 + firma v2 |
| Un solo uso del id | en memoria de Monitor | además, archivo en `state/` del conector (se pierde si se borra) |
| Duración | instantánea | segundos hasta la revisión; hasta `espera_revision_max_s` |
| Cancelación | trivial | SIGTERM no coopera (hallazgo 1) |
| Cierre de ventana | no modelado | PREPARADO o ERROR (hallazgos 2–3) |
| `detalle` | solo en el escenario de fuga (para probar el filtro) | no lo emite nunca |
| Entorno | Python puro | requiere Playwright/Chromium; en Windows solo hay CI |

## 6. Windows y aislamiento
- La CI de esta rama corre en Linux y Windows. En Windows se omiten las pruebas que necesitan controlar el navegador por CDP (cierre de ventana, navegador muerto, cancelación): **no verificadas allí** [P].
- **El aislamiento de permisos en Windows NO está demostrado** (falta cuenta dedicada, ACL y prueba de `PermissionError` sobre `data/*.db`). Por eso esto es un ensayo fuera del flujo operativo y `puede_lanzar()` sigue en `False`.

## 7. Bloqueos de seguridad vigentes
Aislamiento Windows no demostrado · sin cifrado en reposo ni rol «conector» en Monitor · sin tabla, migración ni lanzador operativo · registro de ids del conector sin purga y sin respaldo · perfil temporal de Chromium sin inspeccionar en ejecución · CI con fallas intermitentes previas sin causa demostrada (ver el hilo anterior).

## 8. Requisitos exactos para integrar el conector al flujo de Monitor (solo si Diego lo autoriza)
1. Aislamiento demostrado en Windows: cuenta local de baja privilegio + ACL solo a su carpeta de trabajo + prueba automatizada de `PermissionError` sobre `data/*.db` y `.env`. Hasta entonces `puede_lanzar()` = `False`.
2. Lanzador de Monitor: un proceso por solicitud, script fijo en lista blanca, `shell=False`, entorno mínimo (como el de este arnés), plazo = `espera_revision_max_s` + 180 s, y terminación del **árbol** (SIGTERM no basta; `detener_arbol`).
3. Corrección del conector (su repositorio): terminar con SIGTERM/`terminate()` de forma ordenada; clasificar el cierre de forma determinista; no escribir «revisado» cuando solo se cerró la ventana (hallazgos 1–3).
4. Confirmación humana explícita dentro de Monitor antes de tratar una preparación como aprobada; PREPARADO ≠ aprobado ≠ enviado. El folio/comprobante sigue siendo la única prueba de envío.
5. Tabla persistente de solicitudes con `UNIQUE(clave_idempotencia)` y `UNIQUE(solicitud_id)`, migración revisada y base de pruebas separada; diario de eventos `CONECTOR_*` sin datos personales.
6. Respaldo/rotación del registro de ids usados del conector, o que Monitor sea la única fuente de unicidad.
7. Validación física en Windows (cierre de ventana, procesos huérfanos, temporales del navegador) con la MSI de pruebas, no la instalada.
8. Solo después: operación con un portal propio de laboratorio; ningún proveedor real sin autorización escrita.
