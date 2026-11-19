# Informe Fase 1.1: estabilidad, persistencia y notificaciones

Fecha: 2026-11-12. Máquina de pruebas: contenedor Linux de 4 núcleos, Python 3.13.18
(y 3.11.18 para compatibilidad), PostgreSQL 16 local. **Nada de lo medido aquí es un
servidor de producción ni un equipo Windows.**

Convención de este informe:
- **Medido**: ejecutado aquí, con el archivo de resultados indicado.
- **Simulado / inyectado**: ocurre dentro de la simulación acelerada (reloj virtual) o es un fallo provocado a propósito.
- **No verificado**: implementado o revisado, pero sin una ejecución que lo demuestre.

## 1. Resumen

La v1.1 añade siete bloques sobre la v1.0 sin romperla. Todas las pruebas de la v1.0
siguen en la batería y pasan.

| Tarea | Estado |
|---|---|
| 1. Migraciones Alembic (SQLite y PostgreSQL), respaldo previo, bases v1.0 existentes | **Terminado y probado** |
| 2. Historial: retención, agregados por hora y día, estadísticas, limpieza por lotes | **Terminado y probado** |
| 3. Recuperación tras reinicio y un solo planificador activo (arrendamiento en BD) | **Terminado y probado** (incluye 2 procesos reales con kill -9) |
| 4. Canal Telegram | **Terminado y probado contra un emulador local.** Sin probar contra Telegram real (no se pidieron credenciales) |
| 5. Observabilidad y página de diagnóstico | **Terminado y probado** (API + navegador real, escritorio y teléfono) |
| 6. Resistencia | **Ejecutada**: 7 días virtuales con 1.672 solicitudes y fallos inyectados, más 3 caídas **reales** de PostgreSQL |
| 7. Revisión de Windows | **Revisión estática + correcciones.** Windows **no validado**: nada se ha ejecutado en Windows |

## 2. Rama y commits

Rama `fase-1.1` creada desde `master` (etiqueta `v1.0`, commit `b7a8c51`).
El código va en el zip con su historial git (`git log v1.0..fase-1.1`).
No hay repositorio remoto: este espacio de trabajo no puede crear repositorios en GitHub.

## 3. Cambios

**Base de datos y migraciones**
- `monitoreo/migrations/`: `0001_v1_0`, que reproduce el esquema v1.0. Se comparó contra una base creada por el código real de la v1.0.
- `0002_v1_1`: solo **añade** cuatro tablas, columnas que admiten nulos y un índice. Incluye la reversión.
- `monitoreo/migrate.py`: al arrancar, `bootstrap` actualiza el esquema.
  - Antes de cambiar una base con datos hace un respaldo. En SQLite es una copia consistente verificada con `integrity_check`; en PostgreSQL usa `pg_dump`, y sin `pg_dump` se detiene.
  - Una base v1.0 sin versionar se reconoce y se marca como `0001` sin tocar los datos.
  - Si la base tiene tablas que no reconoce, no se modifica nada.
  - Dos arranques a la vez no migran en paralelo.
  - Uso por consola: `python -m monitoreo.migrate estado | actualizar | respaldar | revertir <rev>`.

**Historial (`engine/retention.py`)**
- Resúmenes por hora (`poll_stats_hourly`) y por día (`poll_stats_daily`) para cada fuente y región.
- Una marca de agua impide borrar consultas sin resumir.
- El borrado va por lotes de 1.000 con pausas, en una tarea separada del ciclo del motor.
- Valores por defecto conservadores, editables en el panel:

| Qué se conserva | Plazo por defecto |
|---|---|
| Consultas detalladas | 14 días |
| Eventos sin alerta ni asignación | 30 días |
| Resúmenes horarios | 90 días |
| Resúmenes diarios | sin límite |
| Registro de notificaciones | sin límite |

- Nunca se borran auditoría, solicitudes, historial de solicitudes, asignaciones, alertas, entregas ni eventos relevantes.

**Recuperación y continuidad (`engine/lease.py`, `engine/recovery.py`, `engine/scheduler.py`)**
- Arrendamiento del planificador en la BD, con vencimiento, margen de seguridad de 1/3 y `fencing_token`.
- Una instancia en espera toma el relevo cuando el arrendamiento vence.
- Las consultas en curso comprueban el liderazgo antes de guardar.
- Al asumir el liderazgo:
  - se reencolan las entregas que quedaron a medias;
  - se marcan las simulaciones interrumpidas;
  - se guarda un informe de recuperación, visible en Salud.
- La cubeta del límite por minuto se reconstruye tras un reinicio, así que no hay ráfaga.
- El `Retry-After` del proveedor y el retroceso sobreviven a un reinicio.
- Avisos críticos (alerta `SISTEMA`) cuando:
  - se abre el circuito de una fuente;
  - una fuente pasa a ERROR;
  - una fuente responde 401 o 403.
- Filtro por tipo de alerta en cada canal.

**Telegram (`notifiers/telegram.py`, `emuladores/telegram.py`, [TELEGRAM.md](TELEGRAM.md))**
- Configuración por variables de entorno y destinatarios autorizados.
- Seguimiento de la entrega por chat: no se reenvía a quien ya lo recibió.
- Respeta el 429/`retry_after` de Telegram; los errores permanentes se separan de los temporales.
- El mensaje lleva una referencia contra repeticiones.
- No se envían datos personales: el titular de la solicitud nunca sale.
- El token no aparece en errores ni en la API.
- Emulador local con modos 429, 500, lento y chats bloqueados.

**Observabilidad (`api/observability.py`, vista "Salud" del panel)**
- Estado del motor: planificador líder, rol de esta instancia, ciclos, errores seguidos y recuperación.
- Por región y por fuente: última consulta correcta, errores seguidos, latencia de detección (p50/p95 en 24 h), solicitudes en espera, alertas pendientes, CPU y memoria del proceso.
- **Diagnóstico por fuente**: causas ordenadas por gravedad, con la acción sugerida. Las causas que reconoce:
  - sin líder, o esta instancia en espera;
  - suspendida (quién y cuándo);
  - en ERROR;
  - circuito abierto;
  - esperando un `Retry-After`;
  - retroceso por errores;
  - límite local de solicitudes;
  - configuración incoherente;
  - datos viejos;
  - respuestas vacías.
- Botón "Probar" en cada canal de notificación.

**Windows y dependencias**: ver la sección 9.

**Scripts nuevos**
- `scripts/resistencia.py`: prueba de resistencia acelerada.
- `scripts/caida_postgres.py`: caídas reales de PostgreSQL.
- `scripts/migracion_con_datos.py`: actualización de esquema con datos.
- `scripts/e2e_panel.js`: ampliado con Salud y diagnóstico.

## 4. Pruebas

**Medido**:
- **142 pruebas aprobadas, 0 fallidas**, en SQLite + PostgreSQL. Son 91 pruebas distintas: 90 en SQLite + 52 que se repiten en PostgreSQL. Duración: 165,9 s.
- Solo SQLite: 90 aprobadas y 1 omitida (la de migración en PostgreSQL, que requiere `MONITOREO_TEST_PG`).
- **Python 3.11.18**: 90 aprobadas, 1 omitida. Mismo resultado que con 3.13.
- **Panel en navegador real** (Chromium/Playwright, `scripts/e2e_panel.js`): flujo completo de v1.0 más Salud y diagnóstico, sin errores de consola y sin desbordamiento horizontal en teléfono.
  - Capturas: `docs/capturas/09_salud.png`, `10_diagnostico.png`, `11_telefono_salud.png`.

| Archivo | Pruebas | Qué demuestra |
|---|---|---|
| test_migrations.py | 7 | Ver detalle debajo de la tabla |
| test_retention.py | 6 | Agregados = datos originales; nunca borra sin resumir; protegidos intactos; limpieza **mientras** el motor asigna sin romper integridad; lotes |
| test_recovery.py | 9 | Ver detalle debajo de la tabla |
| test_restart.py | 2 | kill -9 del servidor real a mitad de trabajo; **dos procesos reales**: uno líder, el otro en espera, relevo al matar al líder |
| test_telegram.py | 9 | Envío, chats no autorizados, 429/Retry-After, 500 transitorio, chat bloqueado sin afectar a otros, token nunca filtrado, sin datos personales, flujo completo |
| test_observability.py | 6 | Contenido de Salud; diagnósticos (sin líder, suspendida, circuito abierto, Retry-After, sano); prueba de canal solo admin; **regresión del bloqueo de 30 s** |
| Pruebas v1.0 (53) | 53 | Todas siguen pasando |

Detalle de test_migrations.py:
- base nueva igual a los modelos;
- `0001` igual al esquema real de la v1.0;
- base v1.0 actualizada con datos intactos, comprobado con huella fila a fila; el respaldo es igual al original;
- reversión y nueva actualización;
- una base desconocida no se toca;
- arranques simultáneos;
- volcado real de PostgreSQL v1.0.

Detalle de test_recovery.py:
- el arrendamiento es exclusivo, vence y se puede tomar;
- adquisición concurrente;
- dos planificadores con relevo;
- las entregas a medias se reencolan;
- el Retry-After y la cubeta sobreviven al reinicio;
- aviso SISTEMA;
- filtro por tipo de alerta;
- dos despachadores no envían dos veces.

**Detector de sesiones anidadas.** Las pruebas activan `MONITOREO_DETECTAR_ANIDADAS=1`. Con él, cualquier sesión de BD abierta dentro de otra falla al instante en lugar de bloquear SQLite 30 s. Con el detector activo pasa toda la batería.

## 5. Resultados de simulación

### 5.1 Resistencia de 7 días virtuales (SQLite)

Fuente: `docs/resultados/resistencia_7d_sqlite.json` (`python scripts/resistencia.py`).
**Simulado con reloj virtual y fallos inyectados.** Tiempo, CPU y memoria son mediciones reales de este proceso.

| Concepto | Resultado |
|---|---|
| Duración virtual / real | 7 días / 1.333 s (aceleración ×454) |
| Solicitudes | 1.672: 1.000 iniciales + 4 por hora. Al final: 1.670 COMPLETADO, 2 EXPIRADO |
| Eventos detectados | 15.324; 0 sin procesar al final |
| Asignaciones | 1.670 completadas, 183 rechazadas por operadores simulados, 30 canceladas |
| Alertas | 11.757 de disponibilidad, 1.865 de asignación, 4 SISTEMA |
| **Violaciones de integridad** | **0** en 81 comprobaciones |
| **Envíos duplicados** | **0**: ningún (alerta, canal) con dos envíos correctos |
| **Claves de alerta duplicadas** | **0** |
| **Pasos con dos líderes** | **0** |
| Tablas protegidas | Ninguna disminuyó (alertas, asignaciones, auditoría, solicitudes, historial) |
| Errores **no** inyectados | **0** |

Fallos inyectados y respuesta del sistema:

| Fallo provocado | Qué pasó |
|---|---|
| **Caída de la instancia líder**, 1 por día (7 en total). Muere tras reclamar entregas y antes de enviarlas | En cada caída quedaron de 8 a 20 entregas a medias. La instancia en espera tomó el relevo al vencer el arrendamiento (22 pasos de 60 s sin líder en total) y **reencoló todas**. 0 duplicados |
| **Fallos intermitentes de BD**: 5 % durante 6 h el día 2 y 20 % durante 2 h el día 6. Al abrir la transacción o justo antes de confirmarla | 1.649 fallos inyectados, todos capturados y revertidos. Dos veces el líder no pudo renovar el arrendamiento a tiempo y **otra instancia tomó el relevo con recuperación** (comportamiento esperado) |
| **Destinatarios inalcanzables** (webhook y Telegram apuntando a un puerto cerrado) | 13.626 entregas por canal terminaron FALLIDA tras agotar reintentos. El panel y el canal simulado entregaron todas (13.626) |
| **Fuente que deja de responder** 8 h (día 3) | El motor la pasó a ERROR en **8 minutos virtuales** (circuito abierto repetido) y dejó de consultarla. Al reanudarla, la primera consulta fue correcta |
| **Duplicados**: fuentes con 5 % de respuestas repetidas; 750 eventos ya procesados marcados para reprocesar; dos despachadores a la vez en cada ciclo | 0 alertas nuevas por reproceso, 0 envíos duplicados |
| **Limpieza del historial mientras el motor trabaja**: hilo aparte cada 6 h, con retención acortada (2 días de consultas, 3 de eventos, 5 de resúmenes horarios) | 26 pasadas en paralelo, de 0,05 s a 1,2 s cada una. Al final quedan 9.289 consultas detalladas de 32.922. **Los resúmenes diarios cuadran exactamente con todas las consultas registradas (32.922 = 32.922)** |

Limitación del script: el campo `fuente_caida.alertas_sistema` de este archivo quedó en 0 porque el script no lo rellenaba. Ya está corregido. El total real de avisos SISTEMA de la corrida fue 4.

Qué **no** cubre esta simulación:
- Las dos "instancias" son hilos de un mismo proceso compitiendo sobre la misma BD. Con procesos reales lo cubre `test_restart.py`.
- El ciclo del planificador lo reproduce el script, no la clase `Scheduler` (que corre con reloj real).
- Solo hay 5 fuentes simuladas.

### 5.2 Resistencia de 1 día virtual (PostgreSQL)

Ver `docs/resultados/resistencia_1d_postgres.json`.

Es la misma prueba con 1.096 solicitudes, recortada a 1 día. Los fallos de BD (días 2 y 6) y la caída de fuente (día 3) quedan **fuera** de este recorte. Sí incluye la caída de la instancia líder, los destinatarios inalcanzables, el reproceso y los dos despachadores.

| Concepto | Resultado |
|---|---|
| Tiempo real / aceleración | 458 s / ×189. Más lento que SQLite, como ya ocurría en la v1.0: PostgreSQL añade una ida y vuelta por la red local en cada consulta |
| CPU / memoria pico del proceso | 380 s / 101 MB |
| Ciclo | p50 270 ms, p95 685 ms |
| Solicitudes | 1.096, todas COMPLETADO |
| Integridad / duplicados / dos líderes | 0 / 0 / 0 |
| Caída del líder | 20 entregas a medias, reencoladas por la nueva líder |

**Caídas reales de PostgreSQL** (`docs/resultados/caida_postgres_real.json`, `scripts/caida_postgres.py`). **Medido, no simulado**: servidor real en marcha con actividad continua y 3 paradas reales de PostgreSQL de unos 10 s cada una.
- El proceso sobrevivió.
- `/api/health` mostró `SIN_CONEXION` durante cada caída y volvió a OK entre 0,0 y 0,13 s después de arrancar la base.
- El planificador siguió siendo líder, con los ciclos avanzando y 0 errores seguidos al final.
- 95 peticiones del panel fallaron mientras la base estaba parada (esperado); 167 funcionaron.
- Integridad final: sin problemas.

### 5.3 Actualización de esquema con datos existentes

Ver `docs/resultados/migracion_con_datos.json`.

**Medido** sobre una copia de una base de 11,5 MB llena por un día de simulación: 1.096 solicitudes, 9.776 entregas, 29.585 registros de notificación y 4.794 consultas. La base se devolvió al esquema v1.0 y se actualizó a la v1.1:
- **Actualización con respaldo: 0,14 s.**
- Todas las columnas de todas las tablas v1.0 quedaron **idénticas** (huella fila a fila).
- El respaldo es idéntico al original.
- El esquema final coincide con los modelos.

La prueba equivalente con una base **creada por el código real de la v1.0**, en SQLite y en PostgreSQL, está en `tests/test_migrations.py`.

## 6. Consumo de recursos

**Medido** en este contenedor (4 núcleos), no en un servidor de producción ni en Windows.

| Medición | Valor | Fuente |
|---|---|---|
| Simulación de 7 días (SQLite) | 1.234 s de CPU en 1.333 s reales (≈ 93 % de un núcleo); memoria de 78 MB al inicio y 127 MB de pico; base final 54,9 MB | resistencia_7d_sqlite.json |
| Duración de un ciclo simulado | p50 110 ms, p95 294 ms, p99 443 ms, máximo 1,9 s. Incluye dos despachadores, fallos y limpieza en paralelo | ídem |
| Servidor real en marcha (panel + motor, SQLite) | 151 MB y 5 % de CPU vistos en Salud durante el e2e (una lectura, no una media) | captura 09_salud.png |
| Servidor real durante caídas de PostgreSQL | 128,8 MB al final | caida_postgres_real.json |
| Corrección de CPU en envíos HTTP | La misma simulación corta pasó de 83 s a 21 s (×4) | perfiles con cProfile, sección 7 |

## 7. Defectos corregidos

**Encontrados en esta fase, en código nuevo o en la v1.0:**

1. **Bloqueo de 30 s en la página de observabilidad con el motor activo (SQLite).**
   - Causa: una sesión de BD abierta dentro de otra, en dos sitios.
   - Cómo apareció: en la prueba con navegador real. Las pruebas de API no lo vieron porque no arrancaban el motor.
   - Corrección: reordenar las consultas, más el detector de sesiones anidadas para toda la batería, más una prueba de regresión.
2. **CPU excesiva al enviar notificaciones** (afectaba a webhook desde la v1.0 y a Telegram).
   - Causa: cada envío creaba un cliente HTTP nuevo y cargaba los certificados TLS (~20 ms de CPU) sin reutilizar conexiones. Con un destinatario caído y reintentos, dominaba el consumo.
   - Corrección: un cliente compartido, sin seguir redirecciones. Simulación ×4 más rápida.
3. **Ráfaga de consultas tras un reinicio** (v1.0): la cubeta del límite por minuto empezaba llena. Ahora se reconstruye.
4. **Recuperación al arrancar podía interferir con otra instancia viva** (v1.0). Ahora la hace solo quien obtiene el liderazgo y, en un relevo, solo sobre entregas abandonadas.
5. **Retención con días decimales** (código nuevo): `int("0.1")` daba 0 y desactivaba el borrado. Corregido; lo detectó una prueba.
6. **Panel**: la vista Salud desbordaba en teléfono por textos largos sin espacios. Corregido y verificado (0 px de desbordamiento).
7. **Instalador de Windows** (v1.0, revisión estática; detalle en la sección 9):
   - si pip fallaba, decía "Instalación terminada";
   - no comprobaba la versión de Python;
   - la contraseña se veía al escribirla;
   - `psycopg-binary` impedía instalar en Windows ARM64 aunque no se use PostgreSQL;
   - el navegador se abría antes de que el servidor respondiera;
   - `pruebas.bat` no devolvía el resultado.

## 8. Problemas pendientes y riesgos

1. **Windows no validado.**
   - Nada se ha ejecutado en Windows. `instalar.ps1` ni siquiera ha pasado por un intérprete de PowerShell: la descarga de PowerShell para Linux fue bloqueada en este entorno.
   - Lo que sí hay: revisión manual línea a línea, balance de llaves y paréntesis, codificación UTF-8 con BOM y finales de línea CRLF.
   - **Siguiente paso necesario**: ejecutar en tu MSI `instalar.ps1`, `pruebas.bat` e `iniciar.bat` y enviarme la salida.
2. **Telegram real no probado.** Seguir el paso 5 de TELEGRAM.md con un chat de prueba antes de depender de él.
3. **Entrega "al menos una vez".** Si una instancia se congela más de 15 s justo después de enviar y antes de registrarlo, el mensaje puede repetirse. Está mitigado con la referencia `Ref:` en Telegram y la cabecera `X-Id-Alerta` en webhooks. Eliminarlo del todo exigiría colaboración del receptor.
4. **Relojes entre máquinas.** El arrendamiento usa la hora de cada instancia. Si se ejecutan en máquinas distintas, los relojes deben estar sincronizados (NTP) con menos de 5 s de diferencia (1/3 del plazo).
5. **SQLite con varias instancias**: solo en la misma máquina y nunca en carpetas de red o sincronizadas (OneDrive). Para varios servidores, PostgreSQL.
6. **Rutas de prueba siempre activas**: `/api-prueba/*`, incluido el emulador de Telegram. Escuchan solo en 127.0.0.1 por defecto, pero deberían poder desactivarse en producción.
7. **Arranques simultáneos sobre una base con datos**: cada proceso puede crear su propio respaldo antes de esperar al candado (se observaron 3 respaldos con 3 arranques). Es inofensivo, pero ocupa disco.
8. **"Consultar ya" desde el panel** ejecuta la consulta en la instancia que atiende la petición, aunque no sea la líder. Es una acción manual de administrador y no rompe la integridad.
9. **Python 3.15** no soportado con las versiones fijadas (pydantic-core 2.46.5 sin rueda para Windows).
10. Pendientes de seguridad heredados de la v1.0: HTTPS, límite general de la API y almacén de secretos. Ver INFORME.md.

## 9. Instrucciones Windows

Revisión estática de `instalar.ps1`, `iniciar.bat`, `pruebas.bat`, rutas, dependencias y variables de entorno. Los cambios se basan en defectos comprobables:

| Hallazgo | Cómo se comprobó | Cambio |
|---|---|---|
| No comprobaba la versión de Python | Lectura del script | Busca 3.13, 3.12, 3.11 y 3.14 con el lanzador `py`. Descarta el acceso directo de Microsoft Store |
| Python 3.15 no instalaría | `pip download --platform win_amd64 --python-version 3.15`: falta la rueda de pydantic-core | Rango 3.11–3.14 y mensaje claro |
| Fallos de pip/venv no detectados | En PowerShell, `$ErrorActionPreference` no afecta a programas externos | Se comprueba `$LASTEXITCODE` tras cada paso |
| `2>$null` + `Stop` corta el script en PowerShell 5.1 | Comportamiento documentado de PowerShell 5.1 | `Continue` y comprobaciones explícitas |
| Contraseña visible al escribir | Lectura | `Read-Host -AsSecureString`, pedida dos veces, comparación sensible a mayúsculas |
| `psycopg-binary` sin rueda para Windows ARM64 | `pip download --platform win_arm64` | Pasa a `requirements-postgres.txt`; opción `-ConPostgres` |
| Compilaciones inesperadas | — | `pip install --only-binary=:all:` |
| OneDrive y SQLite | Riesgo conocido de bloqueos y corrupción en carpetas sincronizadas | Aviso en el instalador y el README |
| Navegador abierto antes de que el servidor responda; puerto fijo 8000 | Lectura | El servidor abre el navegador cuando `/api/health` responde, con el puerto de `.env`. Probado en Linux |
| `pruebas.bat` siempre devolvía 0 | Lectura | Devuelve el código de pytest y muestra el resultado |

Verificaciones adicionales:
- Todas las dependencias tienen rueda precompilada para Windows x64 con Python 3.11, 3.12, 3.13 y 3.14 (29 paquetes cada una).
- Las rutas usan `pathlib` y `as_posix()` en las URL de SQLite.
- `test_restart.py` usa `p.kill()`, que en Windows es TerminateProcess.

Pasos para validarlo en tu equipo (instalación nueva o encima de la v1.0):
1. Descomprime en `C:\motor-monitoreo`, fuera de OneDrive. Si tienes la v1.0, **copia antes la carpeta `data`** como respaldo propio. El sistema también hace uno automático.
2. `powershell -ExecutionPolicy Bypass -File .\instalar.ps1`
3. `pruebas.bat`: espera "TODAS LAS PRUEBAS PASARON" (90 pruebas, 1 omitida).
4. `iniciar.bat`: en la ventana verás "Migración de esquema: respaldo... actualizada a 0002_v1_1" si vienes de la v1.0. El panel se abre solo. Revisa **Salud**.
5. Envíame la salida de los pasos 2 y 3, y cualquier error. Hasta entonces, Windows queda como **no validado**.

## 10. Recomendaciones para la v1.2

Ordenadas por valor/coste:
1. **Validar en tu Windows** (coste casi nulo, valor alto) y, si quieres, Telegram real con un chat de prueba.
2. **Modo producción**: desactivar `/api-prueba/*` y los datos demo con una sola variable. Coste bajo.
3. **Pruebas de PowerShell automatizadas** (Pester) en una máquina Windows o en CI de GitHub con `windows-latest`, si creas un repositorio. Coste bajo y elimina la principal incógnita.
4. **HTTPS y límite general de la API** antes de abrir el panel fuera de tu equipo.
5. **Correo como segundo canal real**: misma interfaz que Telegram. Solo si vas a tener destinatarios sin Telegram.
6. **No recomiendo todavía Redis ni Prometheus**: la BD aguanta lo medido con margen. Reconsiderarlo con varios servidores o cientos de fuentes.
7. Antes de conectar una fuente real, revisar sus términos de uso y límites, y configurar intervalo y límite por minuto de forma conservadora. El diagnóstico avisa si son incoherentes.
