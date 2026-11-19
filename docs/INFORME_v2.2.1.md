# Informe · v2.2.1 Preparación para Jarvis (rama `claude/project-thread-twugkn`)

Rama creada desde `fase-2.2` (4d6d8c3). **No se fusionó** con `master` ni con otra rama. No se
tocó producción, ni la lógica de consentimiento, revocación, planificación, reservaciones o captura.
Ninguna prueba existente se eliminó ni se modificó. Sin portal externo, sin datos reales.

Etiquetas: **VERIFICADO** (medido en esta ronda, salida en `docs/resultados/v2.2.1/`),
**REPORTADO** (dicho por un resultado anterior), **SIMULADO**, **INFERENCIA**, **PENDIENTE**.

Documentos hermanos:
- [`ESTADO_CONSOLIDADO_PARA_JARVIS.md`](ESTADO_CONSOLIDADO_PARA_JARVIS.md): auditoría e inventario;
- [`JARVIS_REGISTRO.md`](JARVIS_REGISTRO.md): guía de registro y comprobaciones en Windows físico.

## 1. Resultado en una frase

El lado de Cauce está listo para que Jarvis lo registre, ejecute suites permitidas y lea un informe
verificable. **No** está probada la integración con Jarvis (no tuve su código), ni Windows físico.

## 2. Qué se entregó

| Entregable | Dónde |
|---|---|
| Informe consolidado del repositorio | `docs/ESTADO_CONSOLIDADO_PARA_JARVIS.md` |
| Contratos | `jarvis/project.yaml`, `jarvis/test-manifest.json`, `jarvis/report.schema.json`, `jarvis/declaraciones.json` |
| Exportador de solo lectura | `python -m monitoreo.interop informe` (`monitoreo/interop/informe.py`) |
| Ejecutor en lista blanca | `python -m monitoreo.interop ejecutar --suite <id>` (`monitoreo/interop/ejecutor.py`) |
| Inventario | `python -m monitoreo.interop inventario` |
| Pruebas nuevas | `tests/test_interop_jarvis.py` (45 funciones, 86 casos) |
| Demostración | `scripts/demo_jarvis_v221.py` |
| CI | trabajo nuevo `jarvis-interop` (Linux con PostgreSQL 16 y Windows Server) |
| Ejemplo de informe para Jarvis | `docs/resultados/v2.2.1/informe_ejemplo_para_jarvis.json` |
| Guía de registro y pendientes Windows | `docs/JARVIS_REGISTRO.md` |

Dependencias nuevas: **ninguna** en tiempo de ejecución. El validador de esquema y el lector de
YAML son mínimos y propios. `jsonschema` y `PyYAML` solo se instalan en CI para comparar resultados.

### 2.1 Decisiones de diseño (y por qué)

- **El manifiesto es la lista blanca.** Quien llama manda un id; el comando sale del manifiesto. Así
  un modelo no puede inyectar un comando aunque lo intente. Además, el propio manifiesto se revisa
  con reglas fijas (solo `{python} -m pytest`, rutas dentro de `tests/`, opciones conocidas), por si
  alguien lo edita.
- **VERIFICADO tiene reglas mecánicas.** Solo lo produce el ejecutor, con huellas SHA-256 íntegras,
  mismo commit que HEAD, árbol limpio y menos de 24 h. Lo demás se degrada a REPORTADO y dice por qué.
  Un histórico no puede salir como VERIFICADO de ninguna forma.
- **Huella de contenido** para la idempotencia: dos informes con el mismo estado tienen la misma
  huella aunque cambien fechas y duraciones.
- **Formatos simples.** JSON y un subconjunto de YAML que cualquier biblioteca lee.
- **No es un agente.** Cauce no decide, no reintenta, no corrige ni fusiona. Solo expone comandos.

## 3. Resultados (VERIFICADO salvo que se indique)

Contenedor Linux, Python 3.13, PostgreSQL 16 local.

| Qué | Resultado | Evidencia |
|---|---|---|
| Suite completa de `fase-2.2` sin cambios (línea base) | 703 pasan, 4 omitidas, 0 fallan (11 min 12 s) | `linea_base_fase22_4d6d8c3.txt` |
| Suite completa por el ejecutor (commit 59770a9) | 791 casos: 787 pasan, 4 omitidas, 0 fallan (11 min 29 s). **Avisó** que `data/` cambió (sección 4, D2) | `suite_completa_resultado.json` |
| Suite `interop` por el ejecutor (commit 69f3743) | 86 de 86 | `suite_interop_resultado.json` |
| Demostración con SQLite + PostgreSQL (commit 69f3743) | **14 de 14**: inventario, manifiesto, suite `humo` (63 casos) dos veces, informe, validación, consulta simulada, comando no permitido rechazado (código 2) | `demo_jarvis_sqlite_y_postgresql.json` |
| Idempotencia | Las dos rondas dan la misma huella y el mismo conteo | ídem |
| Tiempo agotado real | `completa --tiempo-s 20` → código 3 a los 20,07 s, sin procesos huérfanos, repo y `data/` intactos | `tiempo_agotado_resultado.json` |
| Cancelación real | Al terminar con SIGTERM el ejecutor durante una suite, no quedó ningún pytest vivo | observado (`pgrep`), sin archivo |
| Ejemplo de informe | 2 VERIFICADO, 2 REPORTADO, 3 SIMULADO, 7 PENDIENTE; válido con el validador propio | `informe_ejemplo_para_jarvis.json` |
| GitHub Actions | ver sección 5 | — |

La consulta de Jarvis de la demostración es **SIMULADA**: la escribí en Cauce para mostrar qué debe
comprobar un supervisor. No es código de Jarvis.

## 4. Defectos encontrados

| # | Defecto | Gravedad | Qué hice | Cómo comprobarlo |
|---|---|---|---|---|
| D1 | La API decía `2.0.0-dev` en la v2.2 (`/api/info`) | Baja, pero engaña a un supervisor | `monitoreo/version.py` como única fuente: `2.2.1-dev` | `test_version_unica_en_api_y_exportador` |
| D2 | **`tests/test_restart.py` escribe en el `data/` de la carpeta del proyecto** (`data/feeds/*`, `data/notificaciones.log` con alertas ficticias): arranca el servidor real con `cwd` en el proyecto y `data_dir` no es configurable. En una instalación real mezclaría notificaciones de prueba con las reales | Media-alta si alguien ejecuta la suite completa en la instalación | No toqué la prueba. La suite `completa` queda marcada `escribe-data` y el ejecutor se niega si `data/` tiene una base o `.env` define `MONITOREO_DB_URL` | `test_suite_que_escribe_data_se_niega_en_una_instalacion_real`; reproducir: borrar `data/`, `pytest tests/test_restart.py`, mirar `data/` |
| D3 | Las pruebas con PostgreSQL vacían la base de `MONITOREO_TEST_PG` sin comprobar cuál es | Alta si apunta a la real | El ejecutor solo la pasa si el nombre dice test/pruebas/ci | `test_postgres_solo_con_base_de_pruebas` |
| D4 | Las pruebas heredaban el `.env` de la instalación (BD, tokens, portal) | Alta en una instalación real | `MONITOREO_IGNORAR_DOTENV=1` (solo lo define el ejecutor) y entorno con lista corta | `test_entorno_aislado_no_hereda_secretos`, `test_ignorar_dotenv_solo_con_la_variable` |
| D5 | Defecto mío, corregido antes de entregar: el entorno aislado quitaba `NODE_PATH` y 10 pruebas del observador se **omitían** sin fallar | Media (menos cobertura en silencio) | `NODE_PATH` y `XDG_CACHE_HOME` pasan; `NODE_OPTIONS` no (permite inyectar código) | el informe muestra `omitidas` |
| D6 | El CI del commit final de la v2.2 (4d6d8c3) fue 21/22, no 22/22: `test_limite_de_reinicios_detiene_con_motivo` falló en Windows 3.11 | Media | No lo toqué (planificador). Intermitente por INFERENCIA: ese commit solo cambia documentos | volver a ejecutar ese trabajo varias veces |
| D7 | En el CI de 59770a9 falló `test_changes_wake_the_cycle` (Linux 3.13): esperaba despertar en < 0,4 s y tardó 0,51 s | Baja (umbral de tiempo en una máquina cargada) | No lo toqué; no depende de esta ronda | ídem |

**Recomendación para D2 (siguiente ronda, requiere tu visto bueno):** una variable opcional
`MONITOREO_DATA_DIR` en `config.py` (por omisión, igual que hoy) y que `test_restart.py` la use con
`tmp_path`. Es un cambio pequeño y compatible, pero toca una prueba existente, por eso no lo hice.

## 5. CI (GitHub Actions)

Workflow "CI (Windows y Linux)", 24 trabajos (los 22 de antes + `jarvis-interop` en Linux y Windows).

| Ejecución | Commit | Resultado |
|---|---|---|
| 43665781356 | 04277a8 | cancelada por el push siguiente (la configuración cancela ejecuciones viejas de la misma rama) |
| 43665923376 | 59770a9 | cancelada por el push siguiente; antes de eso había fallado Linux · 3.13 (`test_changes_wake_the_cycle`, 0,51 s contra un límite de 0,4 s) |
| **43667232345** | **69f3743** (código final) | **23 de 24**. Pasan `Jarvis v2.2.1 · ubuntu-latest` (con PostgreSQL 16) y `Jarvis v2.2.1 · windows-latest`, todo Windows (3.11, 3.12, 3.13, PostgreSQL, instalación, actualizaciones desde v1.1 a v1.6, PowerShell 5.1 y 7) y Linux 3.11 y 3.12. Falla **Linux · Python 3.13**, otra vez solo `test_changes_wake_the_cycle` (0,53 s; 768 pasan) |

Sobre ese fallo (D7), separando hechos de inferencias:
- HECHO: falló en las dos ejecuciones de esta rama en Linux 3.13 y pasó en 3.11 y 3.12 de la misma
  ejecución; pasó en el CI de `fase-2.2`; pasó dos veces en este contenedor (Python 3.13.18), en la
  línea base y dentro de la suite completa por el ejecutor.
- HECHO: la prueba sustituye por funciones falsas todo lo que toca el motor y mide un tiempo de
  reloj de 0,4 s. Esta ronda no modificó `engine/`, el planificador ni esa prueba.
- INFERENCIA: es sensibilidad a la carga del ejecutor de GitHub, no una regresión. **No está
  demostrado**: haría falta repetir ese trabajo varias veces o medir en `fase-2.2` en el mismo día.
- No lo "arreglé" subiendo el límite: cambiar el umbral de una prueba de rendimiento para ver verde
  sería ocultar el síntoma. Queda como pendiente con tu decisión.

Windows Server de GitHub **no** es el equipo físico.

## 6. Pendientes de validación en Windows

Lista completa con comandos en `JARVIS_REGISTRO.md`, sección 5 (W1 a W10). Lo más importante:
`ciclo --suite humo` en el equipo físico, cancelación sin procesos huérfanos (W5) y el antivirus
(W10). Todo es **PENDIENTE**: no tuve acceso al equipo.

## 7. Recomendaciones para integrar Autonomous Worker después

1. **Que el Worker no ejecute pytest ni git directamente en Cauce.** Solo `ejecutar --suite <id>`,
   `informe` y `validar`. Si necesita otra suite, se añade al manifiesto en una revisión humana.
2. **Trabajar en una copia de desarrollo**, nunca en la carpeta de la ventanilla (D2).
3. **Decidir con el informe, no con la salida de consola**: solo `VERIFICADO` cuenta, y
   `APROBADO` no autoriza fusionar.
4. **Fusión y despliegue siempre humanos.** El contrato lo declara (`politicas`), pero quien lo hace
   cumplir es Jarvis: conviene que Jarvis lo compruebe también de su lado.
5. **Cancelar con el archivo `CANCELAR`** antes de matar procesos; el ejecutor detiene el árbol.
6. **Firma del resultado (HMAC)** solo si el Worker corre en otra máquina. Con un solo equipo, no
   compensa el costo.
7. **No subir el límite de tiempo** de `completa` sin medir en el equipo físico (hoy 90 min; en
   Linux tarda 11 min).

## 8. Cómo reproducirlo

```
python -m pytest -q tests/test_interop_jarvis.py
python -m monitoreo.interop suites
python -m monitoreo.interop ciclo --suite humo
python scripts/demo_jarvis_v221.py --suite humo [--con-postgres]
```
