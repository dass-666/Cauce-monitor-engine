# Informe de la versión 1.5: laboratorio de captura y reservaciones

Fecha: 13 de noviembre de 2026 · Rama `fase-1.5` (sale de `fase-1.4`; **sin fusionar en `master`**)
Repositorio: `dass-666/Cauce-monitor-engine` (privado)

Leyenda: **MEDIDO** (ejecución real con archivo de resultados) · **SIMULADO** (datos ficticios a
propósito) · **ESTIMADO** (deducción, no medido) · **PENDIENTE** (no verificable desde aquí).

## Resumen en cinco líneas

1. Un expediente ficticio llega solo a un folio ficticio con un navegador real: el motor lo asigna,
   el operador revisa los campos, elige un horario y confirma, y el folio queda guardado con
   evidencia del portal. Demo: **23/23 comprobaciones en SQLite y en PostgreSQL** (MEDIDO).
2. Carga: **200 expedientes, 10 operadores compitiendo por los mismos horarios → 200 folios, 0
   horarios reservados dos veces, 0 errores** en SQLite y PostgreSQL (MEDIDO, §6).
3. **410 pruebas automáticas** pasan en un entorno limpio con solo las dependencias declaradas
   (SQLite y PostgreSQL; 4 omitidas a propósito) (MEDIDO).
4. Todo funciona **solo contra nuestro portal de prueba en 127.0.0.1**, con datos sintéticos y con
   confirmación humana obligatoria. Las rutas de captura **no existen en modo producción**.
5. **No validado en el MSI físico** (PENDIENTE). GitHub Actions usa máquinas Windows limpias.

## Advertencia de uso (doble uso)

Un sistema que llena formularios y reserva horarios puede usarse para acaparar citas en portales
públicos. Esta versión se diseñó para que eso no sea su uso:

- solo navega hosts permitidos (`MONITOREO_WEBHOOK_HOSTS`, por defecto 127.0.0.1);
- cada envío exige que una persona marque "He revisado" y cada reservación que marque "Confirmo";
- no hay evasión de CAPTCHA, de límites de velocidad ni de detección de bots, ni la habrá;
- el navegador es HTTP simple pensado para nuestro portal; no "se disfraza" de navegador humano.

Conectarlo a un portal real exigiría autorización escrita del dueño del portal, cumplir sus
términos de uso, preferir su API oficial y un marco legal de protección de datos. **Mi
recomendación es no hacerlo sin esas tres cosas**, aunque técnicamente sea posible.

## 1. Rama y commits

| Commit | Contenido |
|---|---|
| `918cba4` | Portal de prueba, expedientes, validación, motor de formularios, reservaciones con evidencia, panel, migración 0006, pruebas |
| `209bf53` | Recorrido Playwright, prueba de carga, capturas del panel, trabajos de CI de reservaciones |
| `067292d` | Corrección de un defecto encontrado por CI (dependencia no declarada, §8) |
| (último) | Documentación, guía de Windows, este informe y resultados |

82 archivos cambiados frente a `fase-1.4`. Cambios en el núcleo del motor: **34 líneas** (una tarea
del planificador, dos parámetros y el bloqueo de "completar a mano" en asignaciones de expedientes).

## 2. Arquitectura

Detalle y diagrama en [ARQUITECTURA.md](ARQUITECTURA.md#decisiones-de-la-versión-15-captura-y-reservaciones-de-laboratorio).
La idea central: **cada expediente es una solicitud normal del motor**. Así se reutilizan sin
duplicar la cola, la asignación automática, los operadores, las alertas, la autenticación, la
auditoría y la recuperación tras reinicio. Lo nuevo es lo que no existía: el expediente con sus
datos personales, el contrato de campos, el navegador y la máquina de estados de la reservación.

## 3. Módulos nuevos

| Módulo | Qué hace |
|---|---|
| `monitoreo/captura/validacion.py` | Valida nombre, fecha, teléfono y correo con su confirmación, región, sucursal, trámite. Las confirmaciones se comparan y se descartan (no se guardan). Filtro de datos sintéticos. |
| `monitoreo/captura/expedientes.py` | Alta, edición con versión (dos personas editando no se pisan), cierre, historial, permisos, huella anti-duplicado (mismo nombre + nacimiento + trámite activo). |
| `monitoreo/captura/formularios/` | Contrato JSON de campos (`contratos/portal_laboratorio.json`) separado del navegador (`navegador.py`). |
| `monitoreo/captura/reservaciones.py` | Máquina de estados con evidencia, confirmación idempotente, cancelación, recuperación. |
| `monitoreo/captura/seguimiento.py` | Refleja la asignación del motor en el expediente y hace el mantenimiento cada 2 s. |
| `monitoreo/captura/calendario.py` | Lectura (solo lectura) de la disponibilidad por sucursal y fecha. |
| `monitoreo/connectors/portal_reservas.py` | Conector: un recurso por sucursal con sus horarios libres. |
| `monitoreo/api/captura.py` | API de expedientes y reservaciones (no se monta en producción). |
| `monitoreo/web/captura.js` | Panel: Expedientes, Preparación de formularios, Calendario de laboratorio, Reservaciones, Confirmaciones, Historial. |
| Migración `0006_v1_5` | Solo crea tablas nuevas (`expedientes`, `expediente_eventos`, `reservaciones`); no toca datos. |

Eventos normalizados en el historial de cada expediente, enlazados a su asignación:
`DISPONIBILIDAD_DETECTADA`, `FORMULARIO_PREPARADO`, `DATOS_ENVIADOS`, `RECURSO_SELECCIONADO`,
`RESERVACION_PENDIENTE`, `RESERVACION_CONFIRMADA`, `RESERVACION_RECHAZADA`, `RESERVACION_EXPIRADA`.

## 4. Portal de prueba (`portal_reservas/`)

Proceso independiente (`iniciar_portal.bat`, puerto 8200, solo 127.0.0.1), con datos SIMULADOS:
10 sucursales (2 por región), 540 horarios a 6 días hábiles, 300 registros previos sintéticos.

- 4 pantallas HTML: **Datos → Disponibilidad → Confirmación → Resultado** (con folio).
- API de consulta con token: catálogo, disponibilidad, reservación por folio y por clave, cancelar.
- Rutas de control (otro token) para las pruebas: fallos, agotar/liberar horarios, caducar sesiones.
- Errores simulables: campo incorrecto, identificador inexistente, reservación duplicada, horario
  agotado, sesión caducada, conexión cortada (antes o **después** de confirmar), error interno,
  respuesta lenta, campo renombrado, y reintento de confirmación (idempotente por clave).

## 5. Flujo de reservación

1. El conector ve horarios libres en una sucursal → el motor asigna el expediente a un operador
   de su región (DISPONIBILIDAD_DETECTADA).
2. El operador abre la ficha → **Preparar formulario**: el contrato produce los campos (con datos
   enmascarados) y avisa si falta alguno.
3. Marca **He revisado** → **Enviar datos**: el portal valida; si rechaza un campo, el expediente
   vuelve a revisión con el error en el campo.
4. Elige un horario del calendario → el portal lo **aparta** por unos minutos (RETENIDA).
5. Marca **Confirmo** → **Confirmar**. Antes de llamar al portal se guarda CONFIRMANDO; solo un
   operador puede ganar ese paso. Con respuesta clara: CONFIRMADA con folio **y** verificación por
   la API del portal. Si la respuesta se pierde: HTTP 202 "verificando", y el sistema pregunta al
   portal por la clave hasta saberlo; nunca se marca CONFIRMADA sin esa evidencia.
6. El expediente queda CONFIRMADO y la asignación del motor se completa.

Demo verificable (MEDIDO, `python scripts/demo_reservacion.py`; resultados en
`docs/resultados/v1.5/demo_sqlite` y `demo_pg`):

| Paso | Resultado |
|---|---|
| Expediente ficticio | `EXP-000001`, alta con datos enmascarados en la respuesta |
| Asignación automática por el motor | 2,05 s |
| Recorrido en el panel con Chromium real | expediente → formulario → calendario → horario → confirmación → folio `LAB-20261116-00001E` |
| Evidencia independiente | el portal tiene ese folio CONFIRMADO; la clave coincide |
| Mismo contrato, otra navegación | Playwright llena el portal directamente desde el JSON → folio `LAB-20261116-00002H` |
| Matar motor y portal (kill) y reiniciar | el folio sigue CONFIRMADO en ambos; un segundo expediente llega a `LAB-20261116-00003U` |
| Errores de JavaScript / desborde en teléfono | 0 / no |

## 6. Pruebas automáticas

**410 pasadas, 4 omitidas, 0 fallidas** en 314 s, en un entorno virtual nuevo con solo
`requirements-postgres.txt`, contra SQLite y PostgreSQL 16 (MEDIDO). Nuevas en esta versión:

- Portal (8): pantallas, errores de campos, sesión caducada, agotado, idempotencia, cortes.
- Datos (33 casos): campos obligatorios e inválidos, confirmaciones que no coinciden, datos no
  sintéticos, duplicados, edición concurrente, permisos, enmascarado, migración desde una base v1.4.
- Flujo (21 × SQLite y PostgreSQL): recorrido hasta folio; revisión humana obligatoria; campo
  incorrecto; identificador inexistente; dato faltante; duplicada; **dos reservaciones para el
  mismo horario**; sesión caducada al enviar y al confirmar; **confirmación registrada antes de
  perder la respuesta**; error interno y reintento; nunca CONFIRMADA sin evidencia; **reinicio del
  motor a mitad de confirmar**; **dos operadores** (no ven lo ajeno, solo uno confirma);
  disponibilidad que desaparece; cancelación; retención vencida; datos personales fuera de
  listados, auditoría y alertas.
- Se conservan todas las pruebas anteriores (incluida la actualización desde v1.0–v1.4).

Prueba de carga (MEDIDO, `scripts/carga_captura.py`, procesos reales, 200 expedientes, 10
operadores eligiendo a propósito entre los 3 primeros horarios libres):

| Base | Duración | Folios | Choques por horario ya tomado | Dobles reservas | Latencia confirmar p50 / p95 / máx |
|---|---|---|---|---|---|
| SQLite | 30,4 s | 200/200 | 9 | 0 | 150 / 981 / 2082 ms |
| PostgreSQL | 72,6 s | 200/200 | 8 | 0 | 418 / 538 / 656 ms |

Lectura honesta: es una sola máquina con motor, portal y 10 operadores a la vez. SQLite es más
rápido en promedio pero con colas más largas (un solo escritor); PostgreSQL es más parejo pero
unas 2,4 veces más lento en total. No investigué la causa en PostgreSQL (ESTIMADO: costo de
conexión y de cada commit); para el volumen previsto de un laboratorio no es un problema.

## 7. GitHub Actions

PENDIENTE_CI

## 8. Defectos corregidos

| # | Defecto | Gravedad | Cómo se encontró / prueba |
|---|---|---|---|
| 1 | Si la conexión se cortaba **después** de que el portal confirmara, el navegador lo leía como rechazo: habría marcado RECHAZADA una reservación que sí existía (y el horario quedaría ocupado sin dueño). Ahora cualquier respuesta no reconocida es "incierta" y se verifica por clave. | Alta | Prueba `test_confirmacion_registrada_antes_de_perder_la_respuesta` |
| 2 | El portal usaba la lectura de formularios de Starlette, que necesita `python-multipart`: instalado en mi equipo pero **no declarado**. En una instalación limpia (como el MSI) el portal habría fallado con error 500. Ahora lee el formulario con la biblioteca estándar. | Alta para la instalación | CI en máquinas limpias (39 pruebas fallaron); repetido en un entorno virtual nuevo: 410/410 |
| 3 | Asignaciones creadas por rebalanceo no tenían evento de cambio: se perdía la trazabilidad de DISPONIBILIDAD_DETECTADA. Ahora se toma el último evento del recurso. | Media | Pruebas de flujo |
| 4 | La prueba de aceptación v1.0 contaba 8 fuentes fijas; con la fuente del portal son 9. Ahora compara antes y después del reinicio. | Baja (prueba) | CI |

No cuento como defectos corregidos dos protecciones que diseñé desde el principio, aunque las
pruebas las cubren: el portal rechaza una confirmación con una clave distinta de la de su sesión
(CLAVE_INVALIDA), y "Completar" a mano desde Solicitudes está bloqueado (409) para asignaciones de
expedientes, porque cerraría un expediente sin folio.

## 9. Capturas del panel

En `docs/capturas/v1.5/` (generadas por el recorrido automático, no retocadas):
`01`–`06` recorrido del operador hasta el folio; `10`–`15` las seis secciones del administrador;
`20` vista de teléfono; `30`–`33` el portal de prueba llenado desde el contrato.

## 10. Instalación en Windows

Ver [GUIA_RAPIDA_WINDOWS.md](GUIA_RAPIDA_WINDOWS.md): instalación nueva, actualización desde
v1.0–v1.4 (`actualizar.ps1 -Anterior C:\motor-monitoreo-v1.4`) y una prueba guiada de 5 minutos
de la captura. `iniciar.bat` abre además el portal de prueba minimizado. Un `.env` anterior sin
`MONITOREO_PORTAL_TOKEN` funciona: motor y portal usan el mismo token local de laboratorio.

## 11. Riesgos pendientes

| Riesgo | Estado | Qué haría |
|---|---|---|
| No probado en el MSI físico (antivirus, OneDrive, segunda ventana del portal, Chromium no se usa en la instalación normal) | PENDIENTE | Seguir la guía §A y §C en el MSI y enviarme `diagnostico.bat` |
| Doble uso contra portales reales | Mitigado por diseño, no eliminado | Mantener lista de hosts y confirmación humana; no añadir evasión |
| Sin política de retención de expedientes (datos personales) | Abierto | Antes de cualquier dato real: plazo de borrado y purga automática |
| El filtro "solo sintéticos" es heurístico (dominios y prefijo 555) | Aceptable para laboratorio | No es una garantía legal |
| En modo demostración, la fuente `portal-lab` aparecerá con errores en Salud si el portal no está abierto | ESTIMADO | Abrir el portal o desactivar la fuente |
| Token local fijo de laboratorio cuando `.env` no trae uno | Aceptable solo en 127.0.0.1; en producción la captura no existe | `crear_env.py` ya genera uno aleatorio en instalaciones nuevas |
| Contrato y navegador probados solo con nuestro portal | Por diseño | Otro portal exigiría su propio contrato y, sobre todo, autorización |
| Rendimiento medido en una sola máquina Linux | MEDIDO solo ahí | Repetir `carga_captura.py` en el MSI |

## 12. Siguiente fase recomendada

Recomiendo **no añadir funciones nuevas todavía**. El orden que propongo:

1. **Prueba física en el MSI** de la v1.5 (guía §A–§C). Es lo único que falta para decir "funciona
   en tu equipo" y no depende de más código.
2. **Retención y borrado de datos personales** de expedientes cerrados (plazo configurable, purga
   auditada). Es barato (1–2 días) y obligatorio antes de tocar cualquier dato real.
3. Solo si hay un caso real **con autorización del dueño del portal**: integrar por su API
   oficial, no por formularios. Si no hay API ni autorización, mi recomendación es no seguir por
   ese camino.

## Cómo reproducir

```bash
python scripts/demo_reservacion.py --salida resultados/demo --capturas resultados/capturas   # requiere node + playwright
python scripts/carga_captura.py --expedientes 200 --operadores 10 --salida resultados/carga
python -m pytest -q
```
