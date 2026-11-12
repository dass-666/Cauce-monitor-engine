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

## Instalación en Windows

Requisitos: **Python 3.11 o superior** (https://www.python.org/downloads/, marca *Add python.exe to PATH*).

1. Descomprime la carpeta `motor-monitoreo` donde quieras (por ejemplo `C:\monitoreo`).
2. Abre PowerShell **en esa carpeta** (Shift + clic derecho → *Abrir ventana de PowerShell aquí*) y ejecuta:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\instalar.ps1
   ```
   Crea un entorno virtual `.venv`, instala dependencias y te pide la contraseña del
   usuario `admin` (mínimo 10 caracteres con letras y números). Se guarda en `.env`.
3. Inicia el sistema con doble clic en **`iniciar.bat`** (o `.\.venv\Scripts\python -m monitoreo`).
   Se abre http://127.0.0.1:8000. Entra con `admin` y tu contraseña.
4. Para detenerlo: `Ctrl + C` en la ventana negra. Los datos quedan en `data\monitoreo.db`
   y se conservan al reiniciar.
5. Pruebas automatizadas: doble clic en **`pruebas.bat`** (o `.\.venv\Scripts\python -m pytest -q`).

> Si no pusiste contraseña, se genera una al primer arranque, se muestra en la ventana y
> se guarda en `data\CREDENCIAL_INICIAL.txt` (bórralo después de entrar).

### Instalación manual (Windows, macOS o Linux)

```bash
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
copy .env.ejemplo .env            # y edita MONITOREO_ADMIN_PASSWORD
python -m monitoreo
```

### Usar PostgreSQL (opcional, recomendado en producción)

```
MONITOREO_DB_URL=postgresql+psycopg://usuario:clave@localhost:5432/monitoreo
```
Las tablas se crean solas al arrancar. Para ejecutar también las pruebas sobre PostgreSQL:
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

En el laboratorio también puedes agotar inventario, cancelar eventos, cambiar horarios, lanzar
**picos de cambios simultáneos**, poner **varias solicitudes compitiendo** por un mismo recurso y
ejecutar una **simulación acelerada** (por ejemplo 12 horas virtuales en ~1 minuto).

## Estructura

```
monitoreo/
  app.py, __main__.py      Aplicación FastAPI y arranque
  config.py, db.py, models.py, contracts.py, security.py
  connectors/              Interfaz + simulación, JSON, CSV, HTTP
  engine/                  planificador, pipeline, detector, asignación, alertas, resiliencia, integridad
  simulator/               mundo ficticio, datos de demostración, simulación acelerada
  api/                     rutas REST, esquemas de validación, API de prueba local
  web/                     panel (HTML/CSS/JS sin compilación)
tests/                     pruebas automatizadas (pytest)
scripts/                   benchmark.py, medir_servidor.py, e2e_panel.js
docs/                      arquitectura, conectores, informe y resultados medidos
```

La documentación interactiva de la API está en http://127.0.0.1:8000/api/docs.
