# Guía de instalación y ejecución del laboratorio (Linux y Windows) · v2.1

Esta guía deja un equipo listo para ejecutar el motor con el **portal de laboratorio propio**
(127.0.0.1), el **observador de calendario** (Node + Playwright + Chromium) y los recorridos de
comprobación de la 2.1. Usa solo datos ficticios y no se conecta a ningún portal externo.

> **Estado de esta guía.**
> - En Linux se probó en el contenedor de desarrollo y en GitHub Actions (Ubuntu). La primera
>   validación integrada de Diego en su servidor Linux también pasó.
> - En Windows se probó **solo en GitHub Actions** (Windows Server). **No se ha probado en el
>   MSI.** La lista de comprobaciones para hacerlo a mano está en
>   [`COMPROBACIONES_WINDOWS_v2.1.md`](COMPROBACIONES_WINDOWS_v2.1.md).

## 1. Requisitos

| Qué | Versión | Para qué |
|---|---|---|
| Python | 3.11, 3.12, 3.13 o 3.14 (recomendado 3.13) | El motor y el portal de laboratorio |
| Node.js LTS (con npm) | 20 o 22 | El observador y los recorridos con navegador |
| Internet, la primera vez | — | Descargar paquetes de Python, `playwright` (npm) y Chromium (unos 150 MB) |
| PostgreSQL 16 (opcional) | — | Si no se usa, el motor trabaja con SQLite |

En Linux, Chromium necesita además algunas bibliotecas del sistema. El paso 4 las instala si se
ejecuta como root con `--dependencias-linux`.

**Carpeta.** Instala en una carpeta propia del motor, por ejemplo `/opt/cauce-laboratorio` o
`C:\cauce-laboratorio`.
- No la compartas con otros proyectos del servidor: `node_modules/` y `.browsers/` quedan dentro de
  ella y no se tocan otras carpetas.
- En Windows, evita OneDrive (el Escritorio y Documentos suelen estar sincronizados).

## 2. Obtener el código

```bash
git clone --branch fase-2.1.1 <url-del-repositorio> /opt/cauce-laboratorio
cd /opt/cauce-laboratorio
```

También vale descomprimir un ZIP de esa rama.

## 3. Python y configuración

**Linux:**

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt            # con PostgreSQL: pip install -r requirements-postgres.txt
cp .env.ejemplo .env
```

Después edita `.env` y pon `MONITOREO_ADMIN_PASSWORD` (10 o más caracteres, con letras y números).

**Windows:** usa el instalador, que crea `.venv` y `.env` y te pide la contraseña.

```powershell
powershell -ExecutionPolicy Bypass -File .\instalar.ps1
```

## 4. Navegador del observador (una sola vez)

```bash
python scripts/preparar_navegador.py                      # Windows: .venv\Scripts\python.exe scripts\preparar_navegador.py
sudo .venv/bin/python scripts/preparar_navegador.py --dependencias-linux   # Linux, si faltan bibliotecas
```

Qué hace el script:
1. Instala `playwright@1.63.0` en `node_modules/` de la carpeta.
2. Instala Chromium y su versión sin ventana en **`.browsers/`** de la carpeta.
3. Arranca Chromium sobre una página en blanco para demostrar que funciona.

Termina con `LISTO` (código 0) o explica qué falta (código 1).

**Por qué en `.browsers/`.** Es el defecto que encontró la validación en el servidor:
- Playwright busca por omisión en `~/.cache/ms-playwright`, pero el navegador estaba en
  `<instalación>/.browsers`.
- Ahora el motor y los recorridos usan **solos** la carpeta `.browsers` de la instalación cuando
  `PLAYWRIGHT_BROWSERS_PATH` no está definida.
- Ya no hay que exportar nada en cada arranque.

**Si Chromium ya está instalado en otra carpeta,** no lo reinstales. Añade a `.env`:

```
PLAYWRIGHT_BROWSERS_PATH=/ruta/donde/ya/está
```

`.env` se carga al arrancar el motor, así que la ruta queda fija en cada arranque. Una variable
exportada solo en la consola se pierde al cerrarla. El script avisa si detecta ese caso.

**Orden de búsqueda** (`monitoreo/navegador.py`):
1. `PLAYWRIGHT_BROWSERS_PATH`, del entorno o de `.env`.
2. La carpeta `.browsers` de la instalación.
3. La carpeta por omisión de Playwright.

**Comprobarlo cuando quieras.** Ninguno de estos dos comandos se conecta a nada:

```bash
python -m monitoreo.navegador            # rápido: Node, playwright, ruta y ejecutable
python -m monitoreo.navegador --probar   # además arranca Chromium sin ventana y lo cierra
```

El autodiagnóstico (`python -m monitoreo.diagnostico`, o `diagnostico.bat`) muestra el área
**Observador** cuando hay observadores configurados. Avisa si no encontrarán Chromium o si alguno
se detuvo por fallos, con el motivo.

## 5. PostgreSQL (opcional)

En `.env`:

```
MONITOREO_DB_URL=postgresql+psycopg://usuario:clave@localhost:5432/cauce_lab
```

Usa una base **propia del laboratorio**. No apuntes a bases de producción ni de otros proyectos.
El motor crea o actualiza sus tablas al arrancar, con un respaldo previo (`pg_dump`).

## 6. Ejecutar

| Qué | Linux | Windows |
|---|---|---|
| Portal de laboratorio (127.0.0.1:8200) | `python scripts/iniciar_portal.py` | `iniciar_portal.bat` |
| Motor (panel en 127.0.0.1:8000) | `python -m monitoreo` | `iniciar.bat` |
| Ventanilla (motor, portal y navegador) | (portal y motor, luego abre `http://127.0.0.1:8000/#ventanilla`) | `ventanilla.bat` |
| Autodiagnóstico | `python -m monitoreo.diagnostico` | `diagnostico.bat` |

## 7. Comprobaciones reproducibles

Ejecuta todo desde la carpeta de la instalación, con `.venv` activado. Cada recorrido:
- arranca su propio motor y su propio portal en carpetas temporales;
- **no toca `data/`**;
- deja un JSON con cada comprobación (OK o FALLA) y su detalle.

```bash
# Pruebas automáticas (SQLite). Las de navegador se OMITEN si falta Node o el módulo playwright,
# pero FALLAN si está el módulo y falta Chromium: antes, `python -m monitoreo.navegador --probar`
python -m pytest -q
# Las mismas sobre PostgreSQL (base de pruebas que se BORRA y recrea; nunca la del motor)
MONITOREO_TEST_PG=postgresql+psycopg://usuario:clave@localhost/cauce_pruebas python -m pytest -q

# Regresiones del cierre v2.1 (ruta del navegador, errores de Node, respuestas HTTP)
python -m pytest -q tests/test_cierre_v21.py

# Recorrido integrado (plan, calendario abierto, eventos, coincidencias y folio)
python scripts/recorrido_v21.py --salida resultados/recorrido_v21
python scripts/recorrido_v21.py --salida resultados/recorrido_v21 --bd "postgresql+psycopg://usuario:clave@localhost/cauce_rec"

# Aceptación de la 2.1 (Ventanilla «Plan del día», varias unidades, propiedad única, reinicios)
python scripts/aceptacion_v21.py --salida resultados/aceptacion_v21
```

En Windows, `pruebas.bat` ejecuta las pruebas.

**Cómo leer el resultado:**
- código de salida `0`: todas las comprobaciones pasaron;
- `1`: alguna falló (está en el JSON);
- `3`: falta Node.js.

Si una respuesta del motor no trae lo esperado, el recorrido ya no cae con un `KeyError`. Escribe
una FALLA como esta y guarda el JSON igualmente:

> `GET /api/observaciones respondió HTTP 503; se esperaba la clave «eventos»; claves recibidas: detail; detalle: …`

El diagnóstico va saneado: sin cadenas de consulta, secretos, correos ni teléfonos.

## 8. Si el observador no arranca

1. `python -m monitoreo.navegador --probar`: dice qué falta y cómo arreglarlo.
2. En el registro del motor, busca `ERROR del navegador`. Ahí está el mensaje de Node o Playwright,
   saneado. Por ejemplo: `browserType.launch: Executable doesn't exist at …`.
3. `GET /api/observador` (administrador) muestra `ultimo_error`, `errores` y `stderr_node` de cada
   observador.
4. Si el navegador se relanzó más de 8 veces en 10 minutos, el observador queda
   `DETENIDO_POR_FALLOS`. Su motivo incluye el último error, recortado a 200 caracteres. Corrige la
   causa y vuelve a iniciarlo.

## 9. Lo que esta guía no cubre

- Portales reales: no hay integración ni autorización.
- El modo producción: ver [`PRODUCCION.md`](PRODUCCION.md). El observador y el portal de
  laboratorio no se montan en producción.
- El MSI: ver la lista de comprobaciones físicas.
