# Motor de Monitoreo de Disponibilidad

Núcleo reutilizable para **detectar cambios de disponibilidad** (eventos, reservaciones,
inventarios, calendarios), **asignar automáticamente** el recurso a la solicitud más
prioritaria con un operador disponible y **emitir alertas** por varios canales, con
historial completo y un panel web de operaciones.

Esta versión funciona **solo con datos ficticios**: un laboratorio de simulación con cinco
regiones, archivos JSON/CSV de ejemplo y una API de prueba local. No se conecta a ninguna
plataforma externa.

- Arquitectura y decisiones: [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md)
- Integrar nuevas fuentes: [docs/CONECTORES.md](docs/CONECTORES.md)
- Informe de la primera versión (pruebas y cifras medidas): [docs/INFORME.md](docs/INFORME.md)
- **Novedades v1.1** (migraciones, historial, recuperación, Telegram, Salud, resistencia):
  [docs/INFORME_v1.1.md](docs/INFORME_v1.1.md) · Telegram: [docs/TELEGRAM.md](docs/TELEGRAM.md)
- **Novedades v1.2** (modo producción, instalación y actualización seguras, autodiagnóstico,
  integridad de notificaciones, capacidad, CI en Windows): [docs/INFORME_v1.2.md](docs/INFORME_v1.2.md)
  · Producción y HTTPS: [docs/PRODUCCION.md](docs/PRODUCCION.md)
  · Garantías de las notificaciones: [docs/NOTIFICACIONES.md](docs/NOTIFICACIONES.md)

## Instalación en Windows

Requisitos: **Python 3.11, 3.12, 3.13 o 3.14** (recomendado 3.13; https://www.python.org/downloads/,
marca *Add python.exe to PATH*). Con Python 3.15 una dependencia todavía no tiene paquete para Windows;
si tienes varias versiones, el instalador elige sola una compatible.

1. Descomprime la carpeta `motor-monitoreo` en una ruta local, por ejemplo `C:\monitoreo`.
   **Evita carpetas sincronizadas por OneDrive** (Escritorio y Documentos suelen estarlo):
   la sincronización puede bloquear o dañar la base SQLite mientras el motor la usa.
2. Abre PowerShell **en esa carpeta** (Shift + clic derecho → *Abrir ventana de PowerShell aquí*) y ejecuta:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\instalar.ps1
   ```
   El instalador, paso a paso y con un mensaje claro si algo falla:
   comprueba el equipo (Python, espacio en disco, permisos, OneDrive, puerto libre, motor parado);
   **respalda** `data\` y `.env` si ya existían (en `respaldos-instalacion\<fecha>\`); crea un
   entorno virtual `.venv`; instala dependencias; te pide dos veces la contraseña del usuario `admin`
   (no se ve al escribir; mínimo 10 caracteres con letras y números) y la guarda en `.env`;
   prepara la base de datos y termina con un **autodiagnóstico**. Todo queda registrado en
   `data\logs\instalacion-<fecha>.log`.
   Opciones: `-ConPostgres` (usarás PostgreSQL) · `-Produccion` (uso real, ver
   [docs/PRODUCCION.md](docs/PRODUCCION.md)) · `-NoInteractivo` (sin preguntas; contraseña en
   la variable `MONITOREO_ADMIN_PASSWORD`). Se puede repetir sin perder nada.
3. Inicia el sistema con doble clic en **`iniciar.bat`** (o `.\.venv\Scripts\python -m monitoreo`).
   El navegador se abre solo en http://127.0.0.1:8000 cuando el servidor está listo.
   Entra con `admin` y tu contraseña. Al arrancar, el esquema de la base se actualiza solo
   (con respaldo previo en `data\respaldos`) si vienes de la versión 1.0.
4. Para detenerlo: `Ctrl + C` en la ventana negra. Los datos quedan en `data\monitoreo.db`
   y se conservan al reiniciar.
5. Pruebas automatizadas: doble clic en **`pruebas.bat`** (o `.\.venv\Scripts\python -m pytest -q`).
6. ¿Algo no va bien? Doble clic en **`diagnostico.bat`**: revisa base de datos, esquema,
   planificador, conectores, notificaciones, recursos e integridad, y dice qué hacer con cada
   problema. No modifica nada. Lo mismo se ve en el panel → **Salud → Autodiagnóstico**.

### Actualizar a una versión nueva

Recomendado ("lado a lado", la instalación anterior no se toca):

1. Cierra el motor (`Ctrl + C` en su ventana).
2. Descomprime la versión nueva en una carpeta **nueva**, por ejemplo `C:\monitoreo-1.2`.
3. En esa carpeta nueva:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\actualizar.ps1 -Anterior C:\monitoreo
   ```
   Copia (no mueve) `data\` y `.env` verificando cada base, instala, migra con respaldo previo y
   hace el autodiagnóstico. Para volver atrás basta con usar el `iniciar.bat` de la carpeta anterior.

Si preferiste descomprimir encima de la carpeta anterior, ejecuta `instalar.ps1`: respalda
`data\` y `.env` antes de cambiar nada.

### Respaldos y restauración

| Qué | Dónde | Cuándo |
|---|---|---|
| Base antes de cada cambio de esquema | `data\respaldos\monitoreo-<fecha>-antes-de-<versión>.db` (o `.dump` en PostgreSQL) | automático al migrar |
| `data\` y `.env` antes de reinstalar | `respaldos-instalacion\<fecha>\` | automático en `instalar.ps1` |
| Instalación completa anterior | la carpeta anterior | al actualizar lado a lado |
| Respaldo manual | `.\.venv\Scripts\python -m monitoreo.migrate respaldar` | cuando quieras |

Restaurar SQLite: cierra el motor y copia el respaldo como `data\monitoreo.db` (guarda antes
la base actual con otro nombre). Restaurar PostgreSQL:
`pg_restore --clean -d monitoreo data\respaldos\<archivo>.dump`. Estos respaldos son locales:
cópialos también fuera del equipo.

> Si no pusiste contraseña, se genera una al primer arranque, se muestra en la ventana y
> se guarda en `data\CREDENCIAL_INICIAL.txt` (bórralo después de entrar).

### Instalación manual (Windows, macOS o Linux)

```bash
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt   # con PostgreSQL: pip install -r requirements-postgres.txt
copy .env.ejemplo .env            # y edita MONITOREO_ADMIN_PASSWORD
python -m monitoreo
```

### Usar PostgreSQL (opcional, recomendado en producción)

```
MONITOREO_DB_URL=postgresql+psycopg://usuario:clave@localhost:5432/monitoreo
```
Instala el controlador: `pip install -r requirements-postgres.txt` (o `instalar.ps1 -ConPostgres`).
Las tablas se crean o actualizan solas al arrancar (migraciones Alembic). Antes de actualizar un
esquema con datos se ejecuta `pg_dump`; si no está instalado, la actualización se detiene
(ver `MONITOREO_RESPALDO_PG` en `.env.ejemplo`). Estado y respaldo manual:
`python -m monitoreo.migrate estado` · `python -m monitoreo.migrate respaldar`.
Para ejecutar también las pruebas sobre PostgreSQL:
`set MONITOREO_TEST_PG=postgresql+psycopg://usuario:clave@localhost/monitoreo_test` y `pytest`.

## Recorrido de los criterios de aceptación (5 minutos)

1. **Abrir el panel** → *Inicio*: estado del motor, 8 fuentes, **5 regiones ficticias**, cola, operadores.
2. **Registrar solicitudes** → *Solicitudes* → formulario (por ejemplo región Este, tipo `unidad`, prioridad 1).
3. **Ejecutar el monitor**: ya corre solo; en *Monitor* ves cada consulta, su resultado y latencia.
4. **Simular una liberación** → *Laboratorio* → filtra *Región Este* → **Liberar +1**.
5. **Comprobar la detección** → *Monitor* → *Eventos detectados*: `NUEVA DISPONIBILIDAD` y su tiempo de detección.
6. **Ver la asignación automática** → *Solicitudes*: la solicitud pasa a `ASIGNADO` con un operador.
7. **Recibir una alerta** → *Inicio* / *Alertas*: alerta `ASIGNACION`; **Confirmar** marca la recepción.
8. **Consultar el historial** → clic en la solicitud: línea de tiempo de estados y asignaciones.
9. **Reiniciar sin perder datos**: cierra la ventana, vuelve a abrir `iniciar.bat`; todo sigue ahí.
10. **Pruebas automatizadas**: `pruebas.bat`.
11. **(v1.1) Salud** → estado del planificador, de cada región y fuente; pulsa una fuente para ver
    en lenguaje claro **por qué no se actualiza** y qué hacer.

En el laboratorio también puedes agotar inventario, cancelar eventos, cambiar horarios, lanzar
**picos de cambios simultáneos**, poner **varias solicitudes compitiendo** por un mismo recurso y
ejecutar una **simulación acelerada** (por ejemplo 12 horas virtuales en ~1 minuto).

## Estructura

```
monitoreo/
  app.py, __main__.py      Aplicación FastAPI y arranque
  config.py, db.py, models.py, contracts.py, security.py
  connectors/              Interfaz + simulación, JSON, CSV, HTTP, proveedor de prueba (v1.3)
  engine/                  planificador, pipeline, detector, asignación, alertas, resiliencia, integridad,
                           arrendamiento (lease), recuperación, retención del historial
  migrations/, migrate.py  migraciones Alembic con respaldo previo
  notifiers/, emuladores/  canal Telegram y su emulador local
  simulator/               mundo ficticio, datos de demostración, simulación acelerada
  api/                     rutas REST, esquemas de validación, API de prueba local
  web/                     panel (HTML/CSS/JS sin compilación)
proveedor_prueba/          v1.3: servicio HTTP de PRUEBA independiente (datos ficticios, CONTRATO.md)
tests/                     pruebas automatizadas (pytest)
scripts/                   preparar.py (instalación), verificar_arranque.py, capacidad.py, benchmark.py,
                           resistencia.py, integracion_http.py (v1.3), e2e_panel.js, e2e_produccion.js
.github/workflows/ci.yml   integración continua en Windows y Linux (ver docs/INFORME_v1.2.md)
instalar.ps1, actualizar.ps1, iniciar.bat, diagnostico.bat, pruebas.bat
docs/                      arquitectura, conectores, informe y resultados medidos
```

La documentación interactiva de la API está en http://127.0.0.1:8000/api/docs (solo fuera del
modo producción).
