# Informe de la versión 1.7: centro de operaciones, supervisión y notificaciones

Fecha: 13 de noviembre de 2026 · Rama `fase-1.7` (sale de `fase-1.6` `dedc062`; **sin fusionar en `master`**)
Repositorio: `dass-666/Cauce-monitor-engine` (privado)

Leyenda: **MEDIDO** (ejecución real con archivo de resultados) · **SIMULADO** (datos o actores
ficticios a propósito) · **ESTIMADO** (deducción, no medido) · **PENDIENTE** (no verificable desde aquí).

## 1. Rama y commits

| Commit | Contenido |
|---|---|
| `9162e61` | Centro de operaciones (`/api/centro`), supervisión y redistribución auditada, reparto configurable y explicado, notificaciones (10 categorías, silencio, agrupación), privacidad entre operadores, migración 0008 |
| `308ddae` | Consola *Mi trabajo* (12 pasos, retomar), «Siguiente expediente», calendario que salta al mes con días, medición de interacciones |
| `b0772e1` | Recorrido con navegador real (vistas, móvil, reinicio, conexión perdida) y sus correcciones |
| `91597cf` | Reparaciones seguras auditadas, diagnóstico ampliado, pruebas de auditoría adversarial, laboratorio `--v17`, CI y guías |
| `cc0b4b7` | Presupuesto de tiempo por canal de notificaciones (defecto real) y laboratorio con confirmaciones inciertas |
| (este cierre) | Supervisión con consultas agrupadas, prueba de navegador del CI actualizada, rendimiento, informe y versión 1.7.0 |

## 2. Resumen ejecutivo

1. El panel tiene ahora un **centro de operaciones** (cifras de la base, filtrables por región,
   sucursal, operador, estado y periodo), una consola **Mi trabajo** para el operador (12 pasos,
   retomar lo que quedó a medias, «Siguiente expediente»), **supervisión** con redistribución
   auditada y un **centro de notificaciones** con 10 categorías.
2. Laboratorio integrado: **600 expedientes, 10 operadores**, noche simulada con operadores que dejan
   de responder, errores de captura, confirmaciones cortadas por el portal y un canal de avisos lento.
   **26/26 comprobaciones**, 478 folios confirmados, 0 sobreventas, 0 confirmaciones sin evidencia
   (MEDIDO con operadores SIMULADOS, §10).
3. Rendimiento a 3 escalas (hasta 25 regiones, 10 000 expedientes, 20 operadores) en SQLite y
   PostgreSQL: **0 errores HTTP, integridad sin fallos**, pero el servidor de un solo proceso
   **satura la CPU a unas 20-40 peticiones/s** y con SQLite las colas de latencia son largas (§11).
   La pantalla de supervisión era el peor caso (2,6 s en PostgreSQL); se reescribió y bajó a 0,64 s.
4. Defectos reales encontrados y corregidos: **6** (§5), entre ellos que los operadores veían
   alertas y solicitudes de otros, y que un canal de avisos lento retrasaba a los demás.
5. Todo sigue siendo **solo contra nuestro portal de prueba en 127.0.0.1** con datos sintéticos.
   **No validado en el MSI físico** ni con Telegram real (PENDIENTE).

## 3. Módulos nuevos o modificados

Nuevos: `monitoreo/api/centro.py` (centro y supervisión), `monitoreo/reparaciones.py` (reparaciones
seguras), migración `0008_v1_7_centro_operaciones.py`, `scripts/demo_v17.py`,
`scripts/recorrido_v17.js`, `scripts/rendimiento_v17.py`, `laboratorio.bat`.
Modificados: `engine/alerts.py` (categorías, niveles, agrupación, presupuesto por canal),
`engine/assignment.py` (reparto `menor_carga` / `equitativo_dia`, explicación guardada, operadores
ausentes), `captura/*` (Mi trabajo, siguiente paso, calendario), `diagnostico.py`, `api/routes.py`
(privacidad por operador), panel (`app.js`, `captura.js`, `app.css`), `scripts/laboratorio_dias.py`
(`--v17`). En total 30 archivos de código y pruebas, +2 298 líneas.

## 4. Funcionalidades

| Función | Qué hace |
|---|---|
| Centro de operaciones | Cifras de solicitudes, asignaciones, expedientes y confirmaciones salidas de la base; filtros por región, sucursal, operador, estado y periodo (hoy en la zona horaria configurada) |
| Mi trabajo (operador) | Lista sus expedientes con el siguiente paso (de 12), urgencia y lo que quedó a medias; «Continuar», «Retomar», «Siguiente expediente» |
| Colas explicadas | Cada asignación guarda por qué se eligió ese operador (región, sucursal, carga, reparto) |
| Supervisión | Presencia por operador (CONECTADO, CONEXION_PERDIDA, INACTIVO, DESCONECTADO), carga, completadas, rechazos, errores, reasignaciones |
| Redistribución | Devuelve a la cola el trabajo de un operador; nunca lo que tiene señal reciente ni una confirmación en curso o en verificación; todo auditado |
| Notificaciones | 10 categorías, niveles, silencio de lo no crítico, agrupación, confirmación en bloque |
| Reparaciones seguras | Corrigen descuadres (cargas, cupos retenidos) con confirmación, permisos y auditoría |
| Flujo con menos fricción | Dos reservaciones seguidas: **21 → 17 interacciones** (−19 %), conservando «He revisado», los campos manuales tecleados y «Confirmo» (MEDIDO, `interacciones_operador.json`) |

## 5. Defectos reales encontrados

| # | Defecto | Gravedad | Cómo apareció |
|---|---|---|---|
| 1 | Un operador veía alertas y solicitudes de **otros** operadores | Alta (privacidad) | Revisión del código para la supervisión |
| 2 | Un canal de avisos lento pero que respondía **retrasaba a todos los demás** (6 de 20 entregados en el tiempo de una pasada) | Media | Laboratorio integrado |
| 3 | El centro daba **error 500** en cuanto existía una fuente de calendario por día (campo mal nombrado) | Media | Recorrido con navegador real |
| 4 | Sin conexión, el panel **borraba** lo que había en pantalla | Media (operación) | Recorrido con navegador real |
| 5 | El calendario por día se quedaba en un mes sin días libres (defecto de la v1.6) | Media | Consola Mi trabajo |
| 6 | **Supervisión lenta**: 6-8 consultas por operador; 2,6 s con 20 operadores y 10 000 expedientes en PostgreSQL | Media (rendimiento) | Medición de rendimiento (§11) |

También hubo errores **en mis propias herramientas de prueba**, no en el producto, y los declaro:
el laboratorio cerraba la sesión equivocada al probar el cierre de sesión, contaba mal las
confirmaciones inciertas (buscaba un evento que el motor ya no necesita porque resuelve por clave al
instante) y la siembra de rendimiento dejaba cupos retenidos en 0, lo que producía falsos fallos de
integridad. Los tres se corrigieron en los scripts y las mediciones se repitieron.

## 6. Correcciones

| # | Corrección | Prueba que lo demuestra |
|---|---|---|
| 1 | Filtro por operador en alertas y solicitudes; alertas sin nombre del titular | `test_seguridad_v17.py` (3 pruebas) |
| 2 | Presupuesto de tiempo **por canal** (`despacho_canal_presupuesto_s`, 3 s) | `test_canal_lento_v17.py` (falla con el código anterior) |
| 3 | Nombre de campo corregido | `test_captura_dia.py::test_v17_centro_con_calendario_por_dia_cifras_de_la_base` |
| 4 | Se conserva la pantalla bajo el aviso «Sin conexión» | Recorrido `demo_v17.py` (19/19) |
| 5 | Si el mes no tiene días, se muestra el siguiente con aviso | `test_captura_dia.py` y recorrido |
| 6 | Supervisión con **una consulta agrupada por cifra** | `test_v17_supervision_agrupada_da_las_mismas_cifras_que_por_operador` (paridad con el cálculo anterior) y nueva medición (§11) |

## 7. Pruebas añadidas

| Archivo | Qué cubre |
|---|---|
| `test_centro.py` | Centro y supervisión: permisos, filtros, presencia, redistribución |
| `test_seguridad_v17.py` | Privacidad entre operadores (alertas y solicitudes ajenas, alertas sin nombre del titular); 10 categorías, silencio, agrupación y API del centro de notificaciones |
| `test_auditoria_v17.py` | Auditoría adversarial: redistribución contra una toma simultánea, presencia como mucho cada 15 s, «hoy» en la zona horaria configurada, redistribución auditada aunque no libere nada |
| `test_diagnostico_v17.py` | Reparaciones seguras: se reparan solas, quedan auditadas, permisos y confirmación, en SQLite y PostgreSQL |
| `test_canal_lento_v17.py` | Canal lento no retrasa a los demás; presupuesto configurable y validado |
| `test_captura_dia.py` (+) | Mi trabajo, calendario del mes siguiente, redistribución frente a una confirmación en verificación, paridad de la supervisión agrupada |

## 8. Resultado de la suite

**513 pasan, 4 omitidas a propósito, 0 fallan** en SQLite y PostgreSQL 16 juntos (8 min 17 s).
Archivo: `docs/resultados/v1.7/pytest_sqlite_y_postgresql.txt` (MEDIDO). Las 4 omitidas son carreras
que solo existen en PostgreSQL. La v1.6 terminó con 470.

## 9. Resultado del CI

Corrida 43389538322 (commit `cc0b4b7`): **20 de 22**. Fallaron *Linux · Python 3.13* y
*Windows · Python 3.13* en la prueba del panel `e2e_panel.js`: seguía esperando el indicador
«Asignaciones sin actividad» del Inicio de la v1.6, que en la v1.7 es el centro de operaciones y lo
llama «Operaciones abandonadas». Era la prueba, no el producto; se actualizó (pasa en local 37/37).

**Corrida final 43399158677 (commit `1470d2e`): 22 de 22 trabajos en verde** (MEDIDO): Windows
3.11, 3.12 y 3.13 (incluido `demo_v17.py` y el laboratorio reducido); Linux 3.11, 3.12 y 3.13;
PostgreSQL en Windows; reservaciones de laboratorio en Linux y Windows ×3; integración HTTP;
instalación; PowerShell 5.1 y 7; actualización desde v1.1, v1.2, v1.3, v1.4, v1.5 y **v1.6**.

## 10. Laboratorio integrado (`docs/resultados/v1.7/laboratorio/A/laboratorio.json`)

MEDIDO con actores SIMULADOS: 600 expedientes (58 incompletos a propósito), 10 operadores, mecanismo
A (eventos), noche simulada de 300 s reales (reloj del portal ×114) con 302 liberaciones.

| Cifra | Resultado |
|---|---|
| Comprobaciones | **26/26** |
| Folios confirmados | 478 (478 expedientes, 478 folios distintos) |
| Detección de liberaciones | p50 0,062 s · p95 0,099 s (n = 251); durante fallos p50 2,2 s · p95 23,9 s |
| Liberaciones no detectadas | 0 |
| Operadores que dejan de responder | op09 y op10 vistos como CONEXION_PERDIDA, un cierre de sesión como DESCONECTADO |
| Redistribución | 3 asignaciones de cada operador caído devueltas a la cola y auditadas |
| Confirmaciones cortadas por el portal | 5; todas resueltas por la clave (ninguna duplicada, ninguna sin evidencia) |
| Canal lento | 118 avisos recibidos, 59 respuestas 503; 59 enviados y **1 555 pendientes** al terminar |
| Reinicio del motor | arranque 1,1 s; calendario verificado 0,19 s después |

Limitación que no se resolvió: con un canal de avisos que tarda, los demás ya no esperan, pero **los
avisos de ese canal se acumulan** (1 555 pendientes). Hace falta decidir qué hacer con un canal
crónicamente lento (desactivarlo, avisar al administrador o descartar lo viejo). Queda en §14.

## 11. Rendimiento y recursos (`docs/resultados/v1.7/rendimiento_*.json`)

MEDIDO en el contenedor de desarrollo (Linux, un solo servidor `uvicorn` de un proceso, generador
de carga en la misma máquina, 40 s por escenario). Escenarios: s1 = 5 regiones, 1 000 expedientes,
5 operadores · s2 = 10 regiones, 5 000, 10 · s3 = 25 regiones, 10 000, 20. Cada operador simulado
refresca Inicio y Mi trabajo como el panel; el administrador abre centro, supervisión y expedientes.

| Escenario | Base | Pet./s | CPU servidor | Memoria máx. | Centro p50 / p95 | Mi trabajo p50 / p95 | Errores HTTP | Integridad |
|---|---|---|---|---|---|---|---|---|
| s1 | SQLite | 18,1 | 86 % | 192 MB | 63 / 758 ms | 62 / 1 213 ms | 0 | sin fallos |
| s2 | SQLite | 16,7 | 97 % | 226 MB | 91 / 2 161 ms | 207 / 4 257 ms | 0 | sin fallos |
| s3 | SQLite | 40,5 | 97 % | 275 MB | 122 / 1 491 ms | 111 / 1 757 ms | 0 | sin fallos |
| s1 | PostgreSQL | 20,5 | 95 % | 151 MB | 129 / 374 ms | 45 / 184 ms | 0 | sin fallos |
| s2 | PostgreSQL | 31,2 | 99 % | 164 MB | 230 / 543 ms | 77 / 236 ms | 0 | sin fallos |
| s3 | PostgreSQL | 39,2 | 102 % | 181 MB | 661 / 1 103 ms | 245 / 423 ms | 0 | sin fallos |

Asignación del motor: p95 por debajo de 8 ms en todos los escenarios; 0 asignaciones dobles;
0 «database is locked» en el registro.

Supervisión en s3 antes y después de la corrección n.º 6 (PostgreSQL): **p50 2 593 → 645 ms,
p95 3 010 → 900 ms** (MEDIDO, `rendimiento_tras_supervision/`; pocas muestras: 7 y 10). En SQLite no
hubo diferencia apreciable (130 → 119 ms p50; con 11-13 muestras el p95 no es fiable).

Lectura honesta:
- El cuello de botella es la **CPU de un único proceso de Python**: en todos los escenarios está al
  86-102 %. Con 20 operadores refrescando, el servidor atiende ~40 peticiones/s y las latencias
  crecen. Para más operadores: varios procesos (`uvicorn --workers`, solo con PostgreSQL) o refrescos
  menos frecuentes. ESTIMADO: no lo he medido con varios procesos.
- **SQLite tiene colas de latencia largas** (p95 de 1-4 s, máximo 13 s en `/api/operators` en s2)
  porque las escrituras del motor y las lecturas del panel se turnan. Para más de unos 10 operadores
  recomiendo PostgreSQL.
- El generador de carga comparte la CPU con el servidor, así que las cifras son conservadoras.
  Una primera tanda se descartó porque coincidió con la suite de pruebas; las cifras de arriba son
  de una tanda limpia.

## 12. Seguridad y privacidad

- Los operadores **ya no ven** alertas, solicitudes ni asignaciones de otros (defecto n.º 1).
- Las alertas no llevan el nombre del titular; *Mi trabajo* no muestra datos personales.
- Supervisión, redistribución, reparaciones y configuración solo para administradores; cada acción
  queda en la auditoría con motivo.
- La redistribución usa UPDATE condicional: no puede quitarle a un operador algo que acaba de tomar
  (`test_redistribuir_no_gana_a_una_toma_simultanea`).
- La presencia se escribe como mucho cada 15 s (evita escrituras por cada petición).
- Sin cambios en las barreras de la v1.5/1.6: hosts permitidos, confirmación humana obligatoria,
  captura inexistente en modo producción, ningún mecanismo para sortear CAPTCHA ni límites.

## 13. Capturas del panel (`docs/capturas/v1.7/`, Chromium real)

`01`-`08` centro, filtros, supervisión, redistribución, notificaciones, explicación de la asignación;
`10`-`11` pantalla pequeña; `20`-`23` teléfono (centro, notificaciones, supervisión, Mi trabajo);
`30`-`32` operación a medias, Mi trabajo tras reiniciar y folio tras retomar; `40`-`41` conexión
perdida y recuperada; `v1.7_mi_trabajo.png`, `v1.7_siguiente.png`. Recorrido: `demo_v17.json` (19/19).

## 14. Riesgos pendientes

| Riesgo | Gravedad | Recomendación | Cómo comprobarlo |
|---|---|---|---|
| **No probado en el MSI físico** | Alta hasta probarlo | Instalar y ejecutar `scripts\demo_v17.py` y `laboratorio.bat` | 19/19 y TODO OK en el equipo |
| CPU de un solo proceso a ~20-40 pet./s | Media | PostgreSQL + varios procesos para más de ~10 operadores | Repetir `rendimiento_v17.py` con `--workers` |
| SQLite con colas de latencia de segundos con 10-20 operadores | Media | PostgreSQL en oficinas con varios operadores | p95 de `rendimiento_sqlite.json` |
| Canal de avisos crónicamente lento acumula pendientes | Media | Decidir política (desactivar, avisar, caducar) | Laboratorio `--v17`, cifra de pendientes |
| Telegram real nunca probado | Media | Guía manual `docs/TELEGRAM.md` | Mensaje recibido en un chat de prueba |
| Mecanismo A (eventos) como predeterminado y B como respaldo | Decisión pendiente | Mantener A; revisar cuando exista un portal real | — |
| Los operadores del laboratorio son simulados: sus tiempos no representan personas | Media (interpretación) | No planear personal con esas cifras | Piloto con personas |

## 15. Instalación y actualización

Ver [GUIA_RAPIDA_WINDOWS.md](GUIA_RAPIDA_WINDOWS.md). Desde la v1.6: `actualizar.ps1 -Anterior
C:\motor-monitoreo-v1.6` en la carpeta nueva; hace respaldo, aplica la migración 0008 (solo añade
columnas y clasifica las alertas existentes; no borra nada) y comprueba la integridad. El CI prueba
la actualización desde v1.1, v1.2, v1.3, v1.4, v1.5 y v1.6 en Windows PowerShell 5.1.

## 16. ZIP

`/mnt/project-files/motor-monitoreo/motor-monitoreo-v1.7.0.zip` (código, guías, scripts, capturas y
resultados; sin bases de datos, entornos virtuales ni secretos).

## 17. Recomendaciones para la siguiente fase

1. La **fase 2.0 (modo ventanilla y gestor de conexiones) ya está en marcha** en la rama `fase-2.0`,
   que sale de esta 1.7 (`cc0b4b7`). Lo que la 1.7 ya cubre de la 2.0: supervisión de operadores,
   recuperación de operaciones a medias, centro de operaciones e indicadores, notificaciones y la
   base de la cola con reparto explicado. La 2.0 añade la atención presencial, la meta diaria de
   citas y el gestor de conexiones.
2. Antes de más funciones: **probar en el MSI** (instalación, `demo_v17.py`, laboratorio).
3. Para más de ~10 operadores: PostgreSQL y varios procesos; medirlo antes de prometerlo.
4. Decidir la política del canal de avisos lento.

---

## ESTADO DEL PRODUCTO AL CIERRE DE LA FASE 1.7

| Parte | Implementado | Probado en Linux | Probado en Windows CI | Pendiente de probar en el MSI | Pendiente de validar con una integración externa |
|---|---|---|---|---|---|
| Centro de operaciones y filtros | Sí | Sí (pruebas + navegador) | Sí (pruebas) (CI 22/22) | Sí | — |
| Mi trabajo (12 pasos, retomar) | Sí | Sí (pruebas + navegador + reinicio) | Sí (pruebas + `demo_v17.py`) (CI 22/22) | Sí | — |
| Supervisión y redistribución | Sí | Sí (pruebas, laboratorio, paridad) | Sí (pruebas + laboratorio reducido) (CI 22/22) | Sí | — |
| Notificaciones (10 categorías) | Sí | Sí | Sí (CI 22/22) | Sí | **Telegram real** |
| Presupuesto por canal | Sí | Sí (prueba que fallaba antes) | Sí (CI 22/22) | Sí | Canales reales |
| Reparaciones seguras | Sí | Sí (SQLite y PostgreSQL) | Sí (SQLite) (CI 22/22) | Sí | — |
| Rendimiento a 3 escalas | Medido | Sí (este informe) | No | Sí | — |
| Actualización desde v1.6 | Sí | — | Sí (PowerShell 5.1) (CI 22/22) | Sí | — |
| Portal de reservaciones | Solo el de prueba | Sí | Sí | Sí | **Cualquier portal real** (requiere autorización) |
