# Informe de la versión 1.2: validación en Windows, producción y autonomía operativa

Fecha: 12 de noviembre de 2026 · Rama `fase-1.2` (parte de `fase-1.1`; sin fusionar en `master`)
Repositorio: `dass-666/Cauce-monitor-engine` (privado)

Leyenda usada en todo el informe:

- **MEDIDO**: cifra obtenida en una ejecución real, con su archivo de resultados.
- **SIMULADO**: el entorno es ficticio a propósito (fuentes, cambios de disponibilidad, usuarios).
- **ESTIMADO**: extrapolación a partir de lo medido. No es una medición.
- **PENDIENTE**: no se pudo verificar desde aquí.

## 1. Resumen

| Objetivo | Estado | Evidencia |
|---|---|---|
| Validación en Windows | **Probado en Windows real** (GitHub Actions, `windows-latest`), no en el MSI de Diego | §5 |
| Modo producción | Terminado y probado | 19 pruebas, arranque real en modo producción |
| Instalación y actualización para Windows | Terminado; probado en Windows real con PowerShell 5.1 y 7 | §5.3 |
| Integridad de notificaciones | Terminado y probado (8 escenarios de fallo) | 11 pruebas + `NOTIFICACIONES.md` |
| Autodiagnóstico | Terminado y probado (consola, panel y planificador) | 12 pruebas + navegador real |
| Capacidad | Medido en 10 ejecuciones (5 escenarios × SQLite y PostgreSQL) | §6 |
| Compatibilidad | Las 218 pruebas pasan en SQLite y PostgreSQL (las 108 de la v1.1 incluidas) | §4 |

**Defectos encontrados y corregidos: 11**, cada uno con su prueba de regresión (§3). Nueve ya
estaban en la v1.1 o antes; dos los destapó la primera ejecución real en Windows. Aparte hay
cuatro errores míos en código nuevo de esta fase, corregidos antes de ejecutarlo, que no
cuento como hallazgos.

## 2. Qué se ha hecho

### 2.1 Modo producción (`MONITOREO_ENTORNO`)

Hay tres entornos: `desarrollo` (por defecto, compatible con v1.1), `pruebas` y `produccion`.

En **producción**:

- No se registran las rutas `/api/lab/*`, `/api-prueba/*` (incluido el emulador de Telegram)
  ni `/api/docs`. No es que estén protegidas: no existen. La prueba recorre 15 rutas
  peligrosas y además comprueba que ninguna está registrada.
- Nunca se crean datos de demostración, aunque se pidan.
- El servidor **no arranca** si la configuración es insegura, y explica qué cambiar:
  - clave secreta corta o de ejemplo;
  - contraseña de administrador débil (menos de 12 caracteres);
  - datos de demostración activados;
  - emulador de Telegram;
  - escucha en red sin un proxy confiable declarado;
  - entorno mal escrito.
- Los errores internos llegan al cliente solo como "Error interno". El registro va a
  `data/logs/monitoreo.log`, con rotación (10 MB × 5).
- Hay un límite de peticiones a la API por IP: 600 por minuto en total, de ellas 120 de
  escritura, que es lo que frena los intentos de adivinar contraseñas. Es independiente del
  límite de consulta a cada fuente; la prueba demuestra que, con la API saturada, el motor
  sigue consultando con normalidad.
- La publicación con HTTPS mediante proxy inverso (Caddy o nginx, cabecera HSTS,
  `MONITOREO_PROXY_CONFIABLE`) está documentada en `docs/PRODUCCION.md`, con una lista de
  comprobación previa.

### 2.2 Instalación y actualización para Windows

- **`instalar.ps1`** hace siete pasos con mensajes claros:
  1. busca Python;
  2. comprueba el equipo con `preparar.py requisitos`: versión de Python, 64 bits, disco,
     permisos, OneDrive, ruta larga, puerto libre y que el motor esté parado;
  3. **respalda** `data\` y `.env` si existen;
  4. crea `.venv`;
  5. instala solo paquetes precompilados;
  6. crea `.env`;
  7. migra la base y termina con un autodiagnóstico.

  Todo queda en `data\logs\instalacion-<fecha>.log`. Opciones: `-Produccion` (genera un
  `.env` que supera la revisión de producción) y `-NoInteractivo`.
- **`actualizar.ps1 -Anterior <carpeta>`** actualiza "lado a lado":
  - copia (no mueve) `data\` y `.env`;
  - verifica cada base SQLite: integridad y mismo número de filas por tabla;
  - se niega si el destino ya tiene datos, si el motor está en marcha o si la base está en uso;
  - instala, migra con respaldo y diagnostica.

  La carpeta anterior no se modifica, así que volver atrás es usar su `iniciar.bat`.
- **`diagnostico.bat`**: revisión completa con los códigos de salida 0 (correcto),
  1 (avisos) y 2 (críticos).
- No se cambia ninguna política de Windows: `-ExecutionPolicy Bypass` vale solo para esa
  ejecución y la codificación UTF-8 solo para esa ventana. No hay ejecutables de terceros.
- La lógica delicada está en `scripts/preparar.py`, que solo usa la biblioteca estándar, para
  poder probarla en cualquier sistema (11 pruebas).

### 2.3 Integridad de las notificaciones

Defectos de diseño corregidos tras la auditoría:

1. **Resultado tardío.** Un despachador colgado que terminaba después de que otro ya hubiera
   entregado podía sobrescribir `ENVIADA` con un error. Ahora cada reclamo lleva un
   `claim_token` y la escritura final es condicional.
2. **Reclamo colgado sin cambio de líder.** Una entrega se quedaba en `ENVIANDO` hasta que
   cambiara el líder. Ahora se reencola pasados 300 s.
3. **Telegram repetía a destinatarios ya servidos** si el proceso caía a mitad de una entrega
   con varios chats. Ahora se guarda el avance tras cada chat.
4. **Webhooks:** no tenían clave de idempotencia ni respetaban `Retry-After`. Ahora llevan
   `Idempotency-Key` estable (`dedup_key@canal`) y respetan `Retry-After` en 429 y 503.

Escenarios probados, en SQLite y PostgreSQL:

- caída antes del envío;
- caída después del envío y antes de confirmar;
- reintentos con espera creciente y errores permanentes;
- 429 y 503 con espera indicada;
- destinatario bloqueado;
- entrega parcial con 429;
- doble despachador (3 hilos, 60 entregas, cada una enviada exactamente una vez);
- resultado tardío;
- reinicio durante una notificación de Telegram.

**Qué no se puede garantizar** (detalle en `docs/NOTIFICACIONES.md`): entre "el receptor lo
recibió" y "el motor lo guardó" hay una ventana de milisegundos. Si el proceso cae justo ahí,
el aviso se reenvía. Es la garantía "al menos una vez".

- En **webhooks**, el receptor debe descartar las `Idempotency-Key` repetidas. Hay una prueba
  que lo demuestra.
- **Telegram** no admite claves de idempotencia. El duplicado posible se limita al
  destinatario que estaba en curso, y el mensaje lleva `Ref:` para reconocerlo.

### 2.4 Autodiagnóstico

Diez áreas: configuración, base de datos (latencia, `quick_check` y disco), esquema y
respaldos, planificador y liderazgo, conectores (configuración, archivos que faltan, errores,
circuito abierto y datos viejos agrupados), colas, notificaciones, mantenimiento, recursos e
integridad referencial.

Cada problema trae su "Qué hacer". Está disponible en tres sitios: la consola (`--json`,
`--motor-detenido`), el panel (Salud → Autodiagnóstico, solo administradores) y el
planificador (cada 10 minutos crea un aviso SISTEMA por problema crítico y día).

### 2.5 Integración continua (`.github/workflows/ci.yml`)

| Trabajo | Qué ejecuta |
|---|---|
| Linux × Python 3.11 / 3.12 / 3.13 | Suite completa en SQLite y PostgreSQL 16, con `pg_dump` real; arranque real |
| Windows × Python 3.11 / 3.12 / 3.13 | Suite completa en SQLite; arranque real; en 3.13 también el panel con Chromium |
| Windows + PostgreSQL | Suite completa con el PostgreSQL de la imagen (experimental) |
| PowerShell 5.1 y 7 | Sintaxis de todos los `.ps1`, codificación (BOM y `.bat` en ASCII), PSScriptAnalyzer |
| Instalación × PowerShell 5.1 / 7 | ZIP creado con `git archive` → instalación limpia → reinstalación encima → `diagnostico.bat` → arranque desde la carpeta instalada → actualización lado a lado desde una instalación con datos v1.0 → segundo intento (debe negarse) |

Si algo falla, se adjuntan JUnit, los registros de los servidores, el registro de instalación
y las capturas.

`scripts/verificar_arranque.py` arranca `python -m monitoreo` de verdad:

- espera a que responda e inicia sesión;
- prueba 7 rutas, el laboratorio (disponible o ausente según el entorno) y el alta de una
  solicitud;
- espera a que el planificador tome el liderazgo;
- ejecuta el autodiagnóstico y lo detiene de forma ordenada;
- lo reinicia y comprueba que los datos siguen ahí;
- comprueba que una configuración insegura en producción impide arrancar.

## 3. Defectos encontrados y corregidos (todos con prueba de regresión)

| # | Defecto | Gravedad | Cómo se encontró | Prueba |
|---|---|---|---|---|
| 1 | Resultado tardío de un despachador colgado sobrescribía una entrega ya hecha | Alta | auditoría | `test_late_result_does_not_overwrite_newer_state` |
| 2 | Entrega en `ENVIANDO` atascada hasta un cambio de líder | Media | auditoría | `test_stuck_claim_is_requeued_without_leadership_change` |
| 3 | Telegram podía repetir el aviso a destinatarios ya servidos tras una caída | Media | auditoría | `test_telegram_crash_mid_delivery_does_not_repeat_served_chats` |
| 4 | Webhook sin clave de idempotencia y sin respetar `Retry-After` | Media | auditoría | `test_webhook_retry_after_is_respected`, `test_idempotency_key_is_stable_across_retries` |
| 5 | Importar `notifiers.telegram` antes que `engine.alerts` daba `ImportError` (latente desde v1.1) | Media | CLI de diagnóstico | `test_telegram_module_can_be_imported_first` |
| 6 | **El token del bot de Telegram quedaba en los registros**: httpx registra la URL completa | **Alta (seguridad)** | revisión antes de documentar | `test_logs_never_contain_telegram_token` |
| 7 | `.env` guardado con BOM (Bloc de notas de Windows 10) perdía la primera variable, normalmente la contraseña | Media | revisión de la CI | `test_env_file_saved_with_bom_by_notepad_is_read` |
| 8 | Dos instancias arrancando a la vez sobre una base PostgreSQL vacía: la segunda se caía y podían duplicarse canales (avisos dobles) | Media | prueba de capacidad | `test_two_instances_bootstrapping_an_empty_database_at_once` |
| 9 | El verificador de integridad en PostgreSQL informaba incoherencias inexistentes con el motor en marcha (cada consulta veía un instante distinto) | Media (falsas alarmas del autodiagnóstico) | prueba de capacidad | `test_integrity_check_reads_a_consistent_snapshot` |
| 10 | Acentos dañados ("CR�TICO") en la consola de Windows al pasar la salida de Python por PowerShell | Baja (afecta a la comprensión) | **primera ejecución real en Windows** | CI: el registro de instalación no debe contener caracteres dañados |
| 11 | `actualizar.ps1` decía "NO terminó bien" cuando la actualización se completó y solo el diagnóstico veía problemas | Baja | **primera ejecución real en Windows** | CI |

Correcciones propias antes de la primera ejecución (código nuevo de esta fase, no publicado):
`actualizar.ps1` pasaba los modificadores a `instalar.ps1` como texto; `Find-Python` podía
convertir una lista de un elemento en texto suelto; un "→" podía abortar el diagnóstico con la
salida redirigida en Windows; y una prueba nueva habría alterado el entorno de las siguientes.

## 4. Resultados de las pruebas en Linux (MEDIDO)

| Dónde | Resultado |
|---|---|
| Contenedor de desarrollo, Python 3.13, SQLite + PostgreSQL 16 | **218 pruebas pasan** (2 min 44 s) |
| GitHub Actions `ubuntu-latest`, Python 3.11 | 218 pasan; arranque real 35/35 |
| GitHub Actions `ubuntu-latest`, Python 3.12 | 218 pasan; arranque real 35/35 |
| GitHub Actions `ubuntu-latest`, Python 3.13 | 218 pasan; arranque real y panel con Chromium 37/37 |
| Contenedor, Python 3.11 + ZIP limpio | actualización v1.1 → v1.2 completa: copia verificada, migración 0002→0003 con respaldo, diagnóstico OK |

La v1.1 tenía 108 pruebas en SQLite y la v1.2 tiene 145; el resto, hasta 218, son las mismas
pruebas repetidas en PostgreSQL. Todas las de la v1.1 siguen pasando.

## 5. Resultados de las pruebas en Windows (MEDIDO en GitHub Actions)

Son ejecuciones reales en `windows-latest` (Windows Server de GitHub), no en el MSI.

### 5.1 Pruebas y arranque

| Python | Pruebas (SQLite) | Arranque real | Panel con Chromium |
|---|---|---|---|
| 3.11 | 145 pasan, 1 omitida (necesita PostgreSQL) | 35/35 | — |
| 3.12 | 145 pasan, 1 omitida | 35/35 | — |
| 3.13 | 145 pasan, 1 omitida | 37/37 | correcto |
| 3.13 + PostgreSQL de la imagen | **218 pasan** | — | — |

En Windows la parada con Ctrl+Break es ordenada en los dos entornos.

### 5.2 PowerShell

- Sintaxis correcta de los 3 scripts en PowerShell 5.1 y en 7.
- PSScriptAnalyzer: 0 errores y 4 avisos de estilo (dos `catch` vacíos a propósito y dos
  funciones sin `ShouldProcess`).

### 5.3 Instalación y actualización

Primera ejecución, con PowerShell 5.1 y con 7:

- pasaron la instalación limpia, la reinstalación con respaldo, `diagnostico.bat` y el
  arranque desde la carpeta instalada;
- la actualización lado a lado sí copió los datos y migró la base desde la v1.0, pero terminó
  con error por los defectos 10 y 11, y porque a los datos de prueba les faltaba un archivo
  CSV de una fuente.

Segunda ejecución, con las correcciones: ver §5.4.

### 5.4 Segunda ejecución

_Se completa más abajo cuando termina._

## 6. Capacidad (MEDIDO con el servidor real; fuentes y usuarios SIMULADOS)

Equipo: contenedor Linux con 4 CPU y 15,7 GB de RAM. El generador de carga, los clientes y
(en su caso) PostgreSQL comparten esas 4 CPU con el motor. Cada escenario dura 60 s.

Las fuentes usan el conector de simulación (consulta la base, no la red) con intervalo de 10 s.
Los cambios de disponibilidad son simulados: un paso por segundo y un pico cada 15 s, mucho más
agitado que la realidad. Los "operadores" son clientes HTTP concurrentes que alternan inicio,
solicitudes y alertas, con pausas de 0,2 a 0,6 s, y registran una solicitud de cada diez acciones.

Archivos: `docs/resultados/capacidad_sqlite.json` y `capacidad_postgresql.json`.

| Escenario | Base | Memoria máx. | CPU media | API p50 / p95 (inicio) | Consultas/min logradas / previstas | Integridad |
|---|---|---|---|---|---|---|
| e0 reposo: 25 fuentes, sin usuarios | SQLite | 108 MB | 4 % | — | 134 / 150 | correcta |
| e0 | PostgreSQL | 113 MB | 7 % | — | 155 / 150 | correcta |
| e1: 5 regiones, 25 fuentes, 500 solicitudes, 5 operadores | SQLite | 121 MB | 32 % | 21 / 79 ms | 132 / 150 | correcta |
| e1 | PostgreSQL | 121 MB | 44 % | 44 / 109 ms | 146 / 150 | correcta |
| e2: 10 regiones, 100 fuentes, 2.000 solicitudes, 10 operadores | SQLite | 155 MB | 70 % | 41 / 475 ms | 579 / 600 | correcta |
| e2 | PostgreSQL | 132 MB | 95 % | 200 / 512 ms | **484 / 600** | correcta |
| e3: como e2 con 10.000 solicitudes almacenadas | SQLite | 167 MB | 73 % | 43 / 371 ms | 580 / 600 | correcta |
| e3 | PostgreSQL | 128 MB | 95 % | 203 / 546 ms | **489 / 600** | correcta |
| e4: 2 instancias, 100 fuentes, 6 operadores, caída brusca del líder | SQLite | 120 + 132 MB | 30 + 39 % | 33 / 215 ms | 511 / 600 | correcta |
| e4 | PostgreSQL | 133 + 106 MB | 67 + 42 % | 72 / 339 ms | 519 / 600 | correcta |

Otros datos MEDIDOS:

- **0 errores de API** en todos los escenarios con una sola instancia, con hasta 20
  peticiones por segundo.
- **Dos instancias:** nunca hubo dos líderes a la vez. Tras matar al líder sin aviso, la otra
  instancia tomó el relevo en **4,7 s con SQLite y 2,3 s con PostgreSQL** (plazo del
  arrendamiento: 5 s). Los ~130 errores de API de e4 son todos posteriores a la caída: los
  clientes seguían llamando a la instancia muerta porque no hay un balanceador delante. Antes
  de la caída hubo 0 errores.
- **Detección:** el tiempo desde el cambio hasta su detección tuvo una mediana de 1,2 a 5 s, y
  un p95 de unos 10 s, que es el intervalo de consulta. Es lo esperado.
- **10.000 solicitudes almacenadas** frente a 2.000 no cambian las latencias de forma apreciable.

Interpretación (INFERENCIA):

- **SQLite basta para el tamaño de despliegue previsto.** Con 100 fuentes cada 10 s y 10
  operadores, el motor cumple el 97 % de las consultas previstas con latencias de panel
  aceptables (p95 < 0,5 s).
- **Con 100 fuentes cada 10 s en PostgreSQL, el motor se satura en este equipo:** una CPU al
  95 % y el 81 % de las consultas previstas. El motor usa una CPU (Python) y aquí compartía
  las 4 con PostgreSQL y con el generador de carga. Además, las fuentes simuladas consumen
  CPU leyendo la base, mientras que una API real espera en la red. No se ha optimizado nada,
  como pedía la misión, porque hay soluciones de configuración: intervalos de 30–60 s (lo
  habitual con proveedores reales) dividen la carga por 3–6. Si hiciera falta más, el primer
  paso sería medir con un perfilador, no añadir piezas.
- **No hay indicios de que haga falta Redis, Kubernetes ni otra dependencia.**

### 6.1 Crecimiento de la base (ESTIMADO)

En reposo (e0, solo consultas sin cambios) se midió ~1,4 KB por consulta en SQLite y ~5 KB en
PostgreSQL; el tamaño de PostgreSQL incluye filas antiguas que aún no ha limpiado su
mantenimiento automático. Extrapolando a 25 fuentes cada 10 s (unas 200.000 consultas al día):

- SQLite: ~250 MB/día; con la retención por defecto de 14 días, un techo de ~3,5 GB.
- Con intervalos de 60 s: ~42 MB/día y un techo de ~0,6 GB.

Con la agitación simulada (e1–e4) las cifras son 3 a 5 veces mayores, porque cada cambio genera
eventos, asignaciones, alertas y entregas. Esas filas no se borran: son historial.

**Recomendación:** antes de producción, fijar intervalos realistas por fuente y medir el
crecimiento real durante una semana con el autodiagnóstico y la vista Salud. Si el disco es
justo, bajar `retencion_consultas_dias` (los agregados horarios y diarios se conservan).

## 7. Riesgos pendientes

| Riesgo | Gravedad | Qué falta |
|---|---|---|
| No se ha ejecutado en el **MSI de Diego**. Windows Server de GitHub no tiene antivirus de consumo, OneDrive, ni Python instalado desde la Microsoft Store | Media | Instalar en el MSI con la guía del §8 y enviar el registro de instalación |
| **Telegram real** nunca probado (solo el emulador local) | Media | Una prueba con un bot propio cuando Diego quiera |
| El crecimiento de la base es una estimación con datos simulados | Media | Medir una semana con fuentes e intervalos reales |
| La firma de los webhooks no incluye marca de tiempo: una petición capturada se puede reenviar | Baja | El receptor debe descartar `Idempotency-Key` repetidas (documentado); se puede añadir la marca de tiempo en la v1.3 |
| El límite de la API es por proceso. Detrás de un proxy sin `MONITOREO_PROXY_CONFIABLE`, todos los usuarios comparten una sola cuenta | Baja | Seguir `PRODUCCION.md` |
| Si se expone a la red en modo `desarrollo`, solo hay avisos, no bloqueo | Baja | Usar `-Produccion` en cualquier instalación accesible desde otra máquina |
| Respaldos solo locales | Media | Copiar `data\respaldos` fuera del equipo (tarea del sistema operativo) |
| La CI usa acciones con Node 20 (aviso de GitHub de obsolescencia) | Baja | Subir a `checkout@v5` y equivalentes cuando estén disponibles |
| El trabajo "Windows + PostgreSQL" depende de la imagen de GitHub | Baja | Ya marcado como experimental |

## 8. Cómo instalar en el MSI

1. Instala **Python 3.13** desde https://www.python.org/downloads/ y marca
   *Add python.exe to PATH*.
2. Descarga el ZIP de la rama `fase-1.2`: en GitHub, *Code → Download ZIP* o el ZIP adjunto.
   Descomprímelo en `C:\monitoreo`. Evita el Escritorio y Documentos si OneDrive los sincroniza.
3. En esa carpeta, Shift + clic derecho → *Abrir ventana de PowerShell aquí*, y ejecuta:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\instalar.ps1
   ```
   Escribe dos veces la contraseña de `admin`. Al final debe decir
   **"Instalación terminada y comprobada."**
4. Doble clic en `iniciar.bat`. Se abre el panel; entra con `admin` y ve a **Salud** para
   revisar el autodiagnóstico.
5. Si algo falla, envíame el archivo `data\logs\instalacion-<fecha>.log`, o el resultado de
   `diagnostico.bat`.

Si ya tenías la v1.1 instalada en otra carpeta, descomprime la v1.2 en una carpeta nueva y usa
`actualizar.ps1 -Anterior C:\carpeta-anterior` (ver README → *Actualizar*).

## 9. Recomendación para la siguiente fase

En orden de valor y riesgo:

1. **Validación en el MSI y una semana de funcionamiento real con datos ficticios**, con
   intervalos realistas. Medir el crecimiento de la base y el consumo. Coste bajo; despeja
   los tres riesgos medios del §7.
2. **Primera fuente real de bajo riesgo**, una API pública con permiso expreso y sus límites
   documentados, y **Telegram real** con un bot propio. Solo cuando el punto 1 esté
   verificado.
3. **Copias de seguridad fuera del equipo y restauración ensayada.**
4. Ajustes menores: marca de tiempo en la firma de los webhooks y actualización de las
   acciones de la CI.

No se recomienda todavía:

- **Multiservidor, Redis o contenedores:** las mediciones no muestran esa necesidad.
- **Optimizaciones de rendimiento:** antes hay que ajustar intervalos y medir con datos reales.
