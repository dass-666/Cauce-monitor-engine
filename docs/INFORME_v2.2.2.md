# Informe de la ronda de estabilización v2.2.2

Fecha: 2026-11-15 · Rama: `claude/estabilizacion-v2.2.2` (parte de `claude/project-thread-twugkn`, 4fa04f1)
Sin fusiones, sin despliegues, sin cambios en la rama por omisión de GitHub, sin datos reales ni portales.

Leyenda: **[HECHO]** comprobado con una prueba o una medición reproducible · **[INFERENCIA]** conclusión
razonada a partir de las mediciones · **[PENDIENTE]** sin comprobar.

## 1. Resumen

| # | Tarea | Estado | Evidencia |
|---|---|---|---|
| 1 | `test_restart.py` ya no escribe en `data/` (`MONITOREO_DATA_DIR` opcional) | **Corregido** [HECHO] | 5 pruebas nuevas; la suite completa por el ejecutor da `data_sin_cambios: true` |
| 2 | `test_changes_wake_the_cycle` (Linux 3.13) | **Investigado, sin corregir** | 0 fallos en 480 mediciones y 90 ejecuciones aisladas en CI; causa probable: contención, no carrera |
| 3 | Observador intermitente (Windows 3.11) | **Causa reproducida, sin corregir** | Defecto real del `Supervisor`, prueba xfail estricta; 4 de 8 fallos en Windows 3.13 |
| 4 | Rama de integración y commit fijado en `jarvis/project.yaml` | **Corregido** [HECHO] | bloque `integracion` + comparación con HEAD en el informe; 6 estados probados |
| 5 | Contratos intactos + regresiones | **Cumplido** [HECHO] | campo nuevo opcional; los informes v2.2.1 siguen validando (prueba) |
| 6 | CI completo | **Verde** con las salvedades de la sección 6 | ejecución 43671435061: 29/29 trabajos |

Las tareas 2 y 3 **no se corrigieron** a propósito: la corrección toca lógica del planificador o modifica
una prueba existente, y eso queda fuera de lo autorizado. Ver la sección 8.

## 2. Commits

| Commit | Contenido |
|---|---|
| `b516869` | `MONITOREO_DATA_DIR`, `test_restart` en carpeta temporal, bloque `integracion`, `relacion_con_fijado`, pruebas `test_estabilizacion_v222.py`, script `investigar_despertar.py`, trabajos de investigación en CI |
| `7bef850` | fija `commit_fijado` = b516869 |
| `51bbd3b` | CI: el trabajo de investigación del despertar instala `requirements-postgres.txt` (ver 6.2) |
| (siguiente) | este informe, evidencias en `docs/resultados/v2.2.2/`, guía breve y `commit_fijado` = 51bbd3b (solo documentación y `jarvis/`) |

## 3. Tarea 1: `MONITOREO_DATA_DIR`

**Causa [HECHO].** `test_restart.py` arranca el servidor real con el motor en marcha. La base ya iba a un
temporal, pero `settings.data_dir` era siempre `<proyecto>/data`, así que el servidor creaba
`data/feeds/` y `data/notificaciones.log` dentro del proyecto. En una copia que también atendiera la
ventanilla, la prueba habría escrito junto a los datos reales.

**Corrección.** `monitoreo/config.py` lee `MONITOREO_DATA_DIR`:
- sin definir o vacía: `BASE_DIR/data`, exactamente como antes;
- definida: esa carpeta; la base SQLite por omisión también pasa a ella;
- `MONITOREO_DB_URL` explícita sigue mandando.

`test_restart.py` pasa `MONITOREO_DATA_DIR=<tmp>/datos`. No cambia ninguna lógica del motor.

**Regresiones** (`tests/test_estabilizacion_v222.py`):
- valor por omisión idéntico al anterior;
- con la variable;
- con la variable vacía;
- con la variable y `MONITOREO_DB_URL` a la vez;
- servidor real con el motor en marcha: escribe en la carpeta temporal y la huella de `data/` del
  proyecto no cambia;
- comprobación estática de que `test_restart.py` usa la carpeta temporal.

**Comprobación.**
- Suite completa por el ejecutor: `integridad.data_sin_cambios = true` (antes, en v2.2.1, hacía falta la
  etiqueta de guarda `escribe-data`).
- `data/` no existe tras la suite.
- La etiqueta `escribe-data` se conserva en el manifiesto como defensa adicional.

## 4. Tarea 2: `test_changes_wake_the_cycle` (límite 0,4 s)

**Antecedente.** Falló en CI Linux · 3.13 con 0,51 y 0,53 s.

**Método.** `scripts/investigar_despertar.py` reproduce el escenario de la prueba (mismas sustituciones,
planificador real) y mide por partes:
- la consulta falsa: `sleep(0.05)`;
- la salida de la espera;
- el arrendamiento SQLite;
- la latencia que mide la prueba.

Escenarios:
- `normal`;
- `carga`: procesos al 100 % de CPU;
- `aislado`: sin las tareas de fondo reales (captura, historial, envío, autodiagnóstico);
- `carga_aislado`.

**Mediciones [HECHO].** Latencia en segundos, 40 repeticiones por escenario.

| Entorno | normal máx | carga p95 / máx | aislado máx | carga_aislado máx | Fallos |
|---|---|---|---|---|---|
| Local (3.13) | 0,199 | — / 0,266 | 0,085 | — | 0 |
| CI 3.11 | 0,083 | 0,284 / 0,310 | 0,089 | 0,092 | 0/160 |
| CI 3.12 | 0,083 | 0,099 / 0,161 | 0,086 | 0,089 | 0/160 |
| CI 3.13 | ≤ 0,099 en los 4 escenarios | | | | 0/160 |

Otras mediciones:
- **Prueba real aislada:** 30 de 30 aprobadas en 3.11, 3.12 y 3.13 (90 ejecuciones).
- **Prueba real tras los archivos que la preceden:** la prueba aprobó en las 9 ejecuciones (3 por
  versión). El paso salió "fallido" por otra prueba sin relación (ver 6.2).
- **Componentes:** en las rondas lentas, la espera hasta salir (≈ 0,030 s) y el arrendamiento (≈ 0,001 a
  0,005 s) no cambian. Lo que se infla es el `sleep(0.05)` de la consulta falsa: 0,25 a 0,26 s en 3.11
  con carga. Eso solo ocurre con las tareas de fondo activas: con `carga_aislado` el máximo es 0,092 s.

**Conclusión [INFERENCIA].** No hay evidencia de una carrera en la lógica de despertar: siempre hay
`wakeups ≥ 1` y los eventos se procesan en el ciclo siguiente. El fallo es intermitente y lo explican dos
cosas juntas:
- **contención de CPU y del GIL** en un ejecutor cargado. Las tareas de fondo reales, que la prueba no
  sustituye, compiten con el hilo de la consulta y alargan su `sleep`;
- **una limitación de la prueba**, que mide también esa espera inflada y no solo el despertar.

No es un problema de rendimiento del motor en uso real, donde la consulta es de red y dura mucho más
que 0,4 s. La reproducción del 0,51 a 0,53 s **no se consiguió**: el máximo medido fue 0,31 s. Por eso la
causa es una inferencia y no un hecho.

**Corrección propuesta, no aplicada.** Sustituir en la prueba las tareas de fondo por funciones vacías,
como ya hace con `maintenance`. Así mediría solo el despertar. **No se eleva el umbral.** Hace falta
autorización porque modifica una prueba existente.

## 5. Tarea 3: observador intermitente en Windows

**Prueba afectada.** `test_limite_de_reinicios_detiene_con_motivo` (`tests/test_observador.py`).

**Causa [HECHO, reproducida de forma determinista].** En `monitoreo/planificador/observador.py`,
`Supervisor.ciclo` lee la configuración (`cfgs`) una sola vez al principio. A partir de ahí:
1. El primer bucle ve que el observador superó los reinicios, lo marca `DETENIDO_POR_FALLOS` en la base y
   lo quita de `activos`.
2. El segundo bucle usa la lectura vieja, en la que sigue `ACTIVO`, y **lo vuelve a arrancar en el mismo
   ciclo**: un navegador de más.
3. En el ciclo siguiente se detiene y aparece como `detenidos` en lugar de `fallidos`.

La prueba falla cuando el hilo del supervisor (periodo de 3 s) hace ese ciclo antes que la propia prueba.
Es una condición de tiempo, de ahí la intermitencia.

**Frecuencia [HECHO].**

| Dónde | Intentos | Fallos | Mensaje |
|---|---|---|---|
| CI histórico, ejecución 43599314834 | 1 | 1 (Windows 3.11) | igual |
| CI 43671435061, Windows 3.11 | 8 | 0 | — |
| CI 43671435061, Windows 3.13 | 8 | **4** | `{'arrancados': [], 'detenidos': ['NOR-1\|alta'], 'fallidos': [], ...}` |
| Linux | toda la suite | 0 observados | — |

En resumen, falla en Windows con cualquier versión, con una frecuencia muy variable entre ejecuciones
(0 % a 50 %). No es un problema exclusivo de Python 3.11.

**Impacto [INFERENCIA].** En uso real, un observador que ya superó el límite de reinicios se relanza una
vez más durante un ciclo y luego se detiene. No hay pérdida de datos ni bucle infinito, pero sí un
arranque de navegador no deseado tras declarar el fallo.

**Documentación.** `test_supervisor_no_relanza_en_el_mismo_ciclo_un_observador_detenido_por_fallos` es
un `xfail(strict=True)`. Hoy falla, como se espera. Si alguien corrige el defecto, pytest la marcará XPASS
y la suite fallará, para obligar a quitar el xfail de forma consciente.

**Corrección propuesta, no aplicada.** En el segundo bucle, saltar las claves tratadas en el primero, o
releer la configuración. Son unas 3 líneas, pero tocan el planificador.

## 6. Pruebas ejecutadas y estado del CI

### 6.1 Local, sobre 7bef850 con el árbol limpio, por el ejecutor (`suite completa`, SQLite y PostgreSQL 16)

- 805 en total:
  - 799 aprobadas;
  - 0 fallidas, 0 errores;
  - 6 omitidas, en 700 s.
- Las omisiones son:
  - 4 `skipped`: `tests/test_carreras_postgres.py` en su variante `[sqlite]`. La carrera solo existe en
    PostgreSQL y esas mismas pruebas **sí corren** en la variante `[postgres]`;
  - 2 `xfailed`: la prueba del defecto del Supervisor, en sus variantes `[sqlite]` y `[postgres]`.
- Integridad: `git_sin_cambios: true`, `data_sin_cambios: true`.
- Demostración para Jarvis: 14 de 14 comprobaciones, con la misma huella de contenido en dos rondas.
- Evidencia: `docs/resultados/v2.2.2/suite_completa_resultado.json` y `demo_jarvis.json`.

### 6.2 GitHub Actions, ejecución 43671435061 sobre 7bef850

**29 de 29 trabajos en verde:**
- los 24 habituales: Linux 3.11, 3.12 y 3.13, Windows, PostgreSQL, observador, `jarvis-interop` en Linux
  y Windows, etc.;
- los 5 de investigación (`continue-on-error`).

**Salvedad 1.** El paso "en contexto" del trabajo `investigar-despertar` falló 3 de 3 en cada versión.
- La causa fue `test_autodiagnostico.py::test_database_down_is_reported_without_crashing` con
  `ModuleNotFoundError: psycopg`: ese trabajo instalaba solo `requirements.txt`.
- **No es** la prueba investigada, que aprobó.
- Corregido en `51bbd3b`. El resultado de la nueva ejecución de CI está en la sección 6.3.

**Salvedad 2.** Los trabajos de investigación no bloquean el CI, para no ocultar ni forzar resultados.
Que estén en verde no significa que las pruebas investigadas sean estables.

### 6.3 CI tras este informe

**[PENDIENTE]** al escribir este documento. El resultado se comunica en el mensaje de entrega.

## 7. Guía breve para Jarvis E9.1.2

Está en `docs/JARVIS_REGISTRO.md`, sección 0:
1. Leer `integracion.rama` e `integracion.commit_fijado`, **no** `proyecto.rama_principal` (`master` es
   la rama histórica v1.0).
2. Hacer checkout de esa rama en una copia de desarrollo.
3. Ejecutar `{python} -m monitoreo.interop ciclo --suite humo`.
4. Exigir `estado_global == APROBADO`, `integracion.relacion` igual a `FIJADO` o
   `POSTERIOR_SOLO_DOCUMENTOS`, y `rama_actual_coincide == true`. En cualquier otro caso, avisar a una
   persona.

Volver a fijar la rama o el commit es una decisión humana, mediante una revisión de `project.yaml`.

Los pasos dentro de Jarvis siguen siendo **[INFERENCIA]**: no hay acceso al código de Jarvis.

## 8. Lo que queda y requiere decisión

| Propuesta | Riesgo de hacerla | Riesgo de no hacerla |
|---|---|---|
| (a) Corregir el Supervisor (no relanzar en el mismo ciclo) | Bajo: unas 3 líneas, cubiertas por la prueba xfail y las del observador | Fallo intermitente en Windows; un navegador de más tras declarar el fallo |
| (b) Aislar las tareas de fondo en `test_changes_wake_the_cycle` | Muy bajo: solo la prueba | Fallos intermitentes ocasionales en ejecutores cargados |

Otros pendientes:
- La validación en el equipo Windows físico sigue **[PENDIENTE]** (W1 a W10 de `docs/JARVIS_REGISTRO.md`).
- El ejemplo `docs/resultados/v2.2.2/informe_ejemplo_para_jarvis.json` se generó sobre 7bef850.
