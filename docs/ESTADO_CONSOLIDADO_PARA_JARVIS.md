# Estado consolidado de Cauce Monitor Engine para Jarvis (v2.2.1)

Fecha de la auditoría: 15 de noviembre de 2026 (UTC). Repositorio `cauce-monitor-engine`, revisado
desde un clon nuevo con todas las ramas remotas. Rama de este trabajo:
`claude/project-thread-twugkn`, creada desde `fase-2.2` (4d6d8c3). **No se fusionó nada.**

Etiquetas: **VERIFICADO** (lo medí en esta ronda, con la salida guardada), **REPORTADO** (lo dice un
informe o un resultado anterior; no lo volví a ejecutar), **INFERENCIA** (deducción razonable sin
medición), **PENDIENTE** (nadie lo ha comprobado).

El inventario de máquina se obtiene con `python -m monitoreo.interop inventario` (solo lectura).

## 1. Resumen

- La última entrega es **`fase-2.2`**, pero su último commit es **4d6d8c3**, no b5fb73f: después
  de b5fb73f hay dos commits que solo cambian documentación y resultados (VERIFICADO con `git diff`).
- El "CI 22/22" de la v2.2 corresponde a **b5fb73f** (ejecución 43596450341). El CI del commit
  final 4d6d8c3 (ejecución 43599314834) terminó **21/22**: falló una prueba del observador en
  Windows con Python 3.11 (detalle en la sección 8, R1). VERIFICADO en la API de GitHub Actions.
- `master` sigue en la **v1.0** (b7a8c51), 91 commits por detrás de `fase-2.2`. Si Jarvis supervisa
  `master`, supervisa una versión de hace tres días y sin consentimiento ni planificador.
- No existe ninguna entrega posterior a la 2.2 en el repositorio. La rama
  `claude/project-thread-vh931y` (recepción de Outlook) sale de la v1.0 y es independiente.
- Suite completa medida en este contenedor sobre 4d6d8c3: **703 pasan, 4 omitidas, 0 fallan**
  (SQLite + PostgreSQL 16, 11 min 12 s). VERIFICADO. El informe v2.2 decía 701: la diferencia de 2
  no la investigué; no hay fallos.
- No había ningún contrato para Jarvis en Cauce, y no encontré un repositorio de Jarvis en GitHub
  bajo esta cuenta (búsqueda de repositorios y de código por "jarvis": 0 resultados). El contrato
  de esta ronda está diseñado de forma independiente.

## 2. Ramas y último commit (VERIFICADO, `git for-each-ref` tras `git fetch`)

| Rama | Último commit | Fecha (UTC) | Commits por delante de master | Qué contiene |
|---|---|---|---|---|
| `fase-2.2` | 4d6d8c3 | 2026-11-14 13:20 | 91 | v2.2: consentimiento, revocación, trazabilidad. **Entrega vigente** |
| `fase-2.1.1` | 6d9098e | 2026-11-14 12:18 | 85 | cierre v2.1 (Chromium persistente, errores de Node) |
| `fase-2.1` | 56d90a2 | 2026-11-14 04:59 | 82 | planificador diario, observador de calendario |
| `fase-2.0` | 8ae2525 | 2026-11-14 01:04 | 72 | Ventanilla, gestor de conexiones, adaptadores |
| `fase-1.7` … `fase-1.1` | 68daadf … 06f9076 | 12 y 13 nov | 55 … 7 | versiones anteriores (cada una sale de la previa) |
| `claude/project-thread-vh931y` | 6532f03 | 2026-11-13 23:11 | 1 | prueba de recepción Outlook (Graph, solo lectura); sale de v1.0 |
| `master` | b7a8c51 | 2026-11-12 14:38 | 0 | v1.0 |
| `claude/project-thread-twugkn` | (esta ronda) | 2026-11-15 | — | v2.2.1 = fase-2.2 + interoperabilidad con Jarvis |

Ninguna rama `fase-*` tiene commits que no estén en la siguiente: la historia es lineal
(`fase-2.2` contiene todo lo anterior). VERIFICADO con `git rev-list --left-right --count`.

### Diferencias entre `fase-2.2` y `master`

91 commits; 112 archivos de `monitoreo/` cambiados (+17 963 / −322 líneas); 14 migraciones nuevas
(0002 a 0015). Todo lo de las secciones 3 y 4 posterior a la v1.0 está **solo** en las ramas `fase-*`.

## 3. Funcionalidades y pruebas asociadas

Conteo estático de funciones `test_*` (pytest ejecuta más casos porque muchas se repiten con
SQLite y PostgreSQL). VERIFICADO con el inventario; que cada función cubre lo que dice su nombre es
INFERENCIA por lectura.

| Área | Módulos | Pruebas principales (funciones) |
|---|---|---|
| Detección de cambios y cola | `engine/`, `connectors/` | detector (6), pipeline (16), assignment (17), connectors (8), circuito_adaptable (10) |
| Alertas y notificaciones | `notifiers/`, `emuladores/` | notificaciones (10), telegram (9, solo emulador), alertas_destino_caido (3), webhook_firma (2) |
| Recuperación y reinicios | `engine/`, `db.py` | recovery (9), restart (2), arranque_concurrente (2), asignaciones_abandonadas (11), carreras_postgres (4) |
| Producción, instalación y seguridad | `entorno.py`, `security.py`, `api/` | produccion (11), instalacion (13), actualizar_versiones (3), seguridad_v17 (5), auditoria_seguridad (4), api (11) |
| Autodiagnóstico | `diagnostico.py` | autodiagnostico (12), diagnostico_v17 (4) |
| Expedientes y captura (laboratorio) | `captura/` | captura_datos (17), captura_flujo (21), captura_dia (22), portal_reservas (8), portal_dias (10) |
| Ventanilla | `api/`, `web/` | ventanilla (18) |
| Conexiones y proveedor | `conexiones/`, `proveedor.py`, `adaptadores/` | conexiones (13), proveedor (3), adaptadores (8) |
| Planificador y observador | `planificador/` | planificador (9), plan_unidades_propiedad (9), observador (3), observador_persistente (5), calendario_motor (6), cierre_v21 (13) |
| Consentimiento y revocación (v2.2) | `captura/consentimiento.py`, `captura/trazabilidad.py` | consentimiento_v22 (22) |
| Retención | `engine/` | retencion (3), retention (6), restaurar (4) |
| Migraciones | `migrations/` | migrations (7) |
| **Interoperabilidad Jarvis (v2.2.1)** | `interop/` | interop_jarvis (45 funciones, 86 casos) |

## 4. Migraciones

15 revisiones, cadena **lineal** de `0001` a `0015_v2_2` (una sola cabeza). VERIFICADO
estáticamente. Esta ronda **no añade migraciones**.

"Migraciones pendientes" depende de cada base: el inventario no abre ninguna base de datos a
propósito. En una instalación concreta lo dice `python -m monitoreo.diagnostico` (sección de
esquema). La instalación del equipo Windows físico: PENDIENTE (no tuve acceso).

## 5. Scripts de Windows

10 archivos (`*.bat`, `*.ps1`, `scripts/comun.ps1`). Todos cumplen las reglas de codificación de CI
(`.bat` solo ASCII y con CRLF; `.ps1` con acentos con BOM). VERIFICADO por el inventario. Su
funcionamiento está REPORTADO por CI en Windows Server (PowerShell 5.1 y 7, instalación limpia,
reinstalación y actualización desde v1.1 a v1.6). En el equipo físico: PENDIENTE.

Esta ronda no añade `.bat` ni `.ps1`: Jarvis llama al Python del entorno virtual directamente.

## 6. Informes técnicos vigentes

| Documento | Estado |
|---|---|
| `docs/INFORME_v2.2.md` | Vigente para la v2.2. Correcto salvo el matiz del CI (22/22 es de b5fb73f, no del commit final) |
| `docs/ALCANCE_Y_AUTORIZACIONES_v2.2.md` | Vigente: límites de uso con datos reales y con el portal externo |
| `docs/INFORME_v2.2.1.md` | Esta ronda |
| `docs/JARVIS_REGISTRO.md` | Esta ronda: guía de registro y comprobaciones Windows |
| `docs/CONTRATO_ADAPTADORES.md`, `GUIA_*`, `COMPROBACIONES_WINDOWS_v2.1.md` | Vigentes como referencia |
| `docs/INFORME_v1.x.md`, `v2.0`, `v2.1` | Históricos |

## 7. Qué cambió en esta ronda respecto a `fase-2.2`

Sin cambios en la lógica de consentimiento, revocación, planificación, reservaciones ni captura.
Ninguna prueba existente se eliminó ni se modificó. Cambios en código existente (dos, mínimos):

1. `monitoreo/app.py`: la versión viene de `monitoreo/version.py`. **Defecto previo**: la API
   (`/api/info`) informaba `2.0.0-dev` en la v2.2. Ahora `2.2.1-dev`.
2. `monitoreo/config.py`: si `MONITOREO_IGNORAR_DOTENV=1`, no se lee `.env`. Solo lo define el
   ejecutor para Jarvis. Sin esa variable, el comportamiento es idéntico al anterior.

## 8. Riesgos y bloqueos

| # | Riesgo o bloqueo | Gravedad | Recomendación | Cómo comprobarlo |
|---|---|---|---|---|
| R1 | `test_limite_de_reinicios_detiene_con_motivo` falló en Windows · Python 3.11 en el CI de 4d6d8c3 (el observador aparece como `detenidos` y no como `fallidos`). El commit solo cambia documentos, así que es **intermitente** (INFERENCIA: carrera de tiempos en Windows), no una regresión | Media: un supervisor vería CI rojo sin causa real | No lo toqué (es lógica del planificador). Investigarlo en una ronda propia; mientras tanto, Jarvis no debe tratar un rojo aislado de este trabajo como regresión | Volver a ejecutar ese trabajo 5 veces; anotar cuántas falla |
| R2 | `master` está en v1.0 | Alta para Jarvis | Decidir qué rama supervisa Jarvis (`fase-2.2` o esta) o fusionar con autorización | `git log origin/master -1` |
| R3 | Las pruebas con PostgreSQL **vacían** la base de `MONITOREO_TEST_PG` | Alta si se apunta a la base real | El ejecutor solo la usa si su nombre dice test/pruebas/ci | `test_postgres_solo_con_base_de_pruebas` |
| R4 | Ejecutar pruebas dentro de una instalación real hereda su `.env` (BD, tokens, portal) | Alta | El ejecutor no hereda nada (variable nueva + lista corta de variables); aun así, usar una copia de desarrollo | `test_entorno_aislado_no_hereda_secretos` |
| R5 | El aislamiento de red no lo impone el sistema operativo | Media | Firewall de Windows para la copia de desarrollo | Comprobación W-10 de `JARVIS_REGISTRO.md` |
| R6 | Un `resultado.json` se puede fabricar con acceso a la carpeta (no hay firma) | Baja en un solo equipo | Si Jarvis corre en otro equipo, añadir firma HMAC más adelante | — |
| R7 | Con el entorno aislado, algunas pruebas del observador se **omiten** si Node/Playwright solo está accesible por variables que no pasan (lo vi en este contenedor con `NODE_PATH`; ya se pasa) | Media: menos cobertura sin fallo visible | El informe muestra `omitidas`; Jarvis debe vigilar ese número | Comparar `omitidas` entre ejecuciones |
| R8 | `tests/test_restart.py` escribe en el `data/` de la carpeta del proyecto (medido: `data/feeds/*` y `data/notificaciones.log`) | Media-alta en una instalación real | El ejecutor rechaza la suite `completa` donde `data/` tiene una base; corregir la prueba en otra ronda (ver INFORME_v2.2.1, D2) | borrar `data/`, ejecutar esa prueba y mirar `data/` |
| B1 | Operar con el portal externo requiere autorización del proveedor | Bloqueo | Fuera de alcance | `ALCANCE_Y_AUTORIZACIONES_v2.2.md` |
| B2 | Equipo Windows físico sin validar | Bloqueo para declarar Windows listo | `JARVIS_REGISTRO.md`, sección 5 | Informe JSON generado en el equipo |
| B3 | Jarvis real no ha consumido los contratos | Bloqueo para declarar la integración | Registrar y probar desde Jarvis E9.1.2 | `ciclo --suite humo` lanzado por Jarvis |
