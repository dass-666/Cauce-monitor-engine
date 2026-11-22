# Iniciar la candidata del piloto en Windows · instalación independiente

**Decisión (22-nov-2026):** el piloto se opera como una **instalación normal y aislada**: su configuración está en
`.env`, su base en `data\monitoreo.db` y se abre con `iniciar.bat`. Así el motor, `respaldar_externo.bat`,
`verificar_respaldo.bat`, `diagnostico.bat` y `migrate restaurar` trabajan sobre **la misma base**.

`piloto_asistido.bat` queda **solo para demostraciones y pruebas ficticias**: usa otra carpeta
(`resultados\demo_asistido`), contraseñas aleatorias que imprime en la consola, y los respaldos no la cubren.

No toca la MSI (`C:\cauce-msi`), ni `C:\cauce-respaldos`, ni ninguna otra carpeta de Cauce.

## Antes de empezar

- Una carpeta **nueva** para el piloto, por ejemplo `D:\cauce-piloto`. No reutilices `D:\cauce-piloto-candidata`
  ni una carpeta que ya tenga `.env` o `data\`.
- Una carpeta en **otra unidad** que la del piloto para la copia del diario y los respaldos. Si el piloto está en
  `D:\cauce-piloto`, la carpeta externa debe estar en otra unidad (una USB cifrada, `E:\CaucePiloto`). Si prefieres
  usar D: como destino provisional de los respaldos (decisión del 21-nov), instala el piloto en `C:\cauce-piloto`
  y usa `D:\CaucePiloto` como carpeta externa. Cauce **rechaza** la misma unidad.
- Solo datos **ficticios** (la configuración lo exige: `MONITOREO_CAPTURA_SOLO_SINTETICOS=1`).

## Pasos exactos

1. **Código.** Descarga el ZIP de la rama de la candidata y descomprímelo en la carpeta nueva
   (ejemplo: `C:\cauce-piloto`). Abre una consola en esa carpeta.
2. **Preparar** (instala el entorno, pide TU contraseña de administrador y escribe `.env`):
   ```
   C:\cauce-piloto\preparar_piloto.bat D:\CaucePiloto
   ```
   - Pide la contraseña dos veces (mínimo 12 caracteres, letras y números; no se muestra). No uses ninguna de
     laboratorio: Cauce las rechaza.
   - Crea `D:\CaucePiloto\diario` y `D:\CaucePiloto\respaldos`.
   - Puerto fijo 8100 (otro: `preparar_piloto.bat D:\CaucePiloto 8150`).
   - Esperado: `RESULTADO: piloto preparado`.
3. **Comprobar la configuración** (no cambia nada):
   ```
   C:\cauce-piloto\preparar_piloto.bat --comprobar
   ```
   Esperado: `RESULTADO: configuración del piloto correcta`, con la base en `C:\cauce-piloto\data\monitoreo.db`.
4. **Abrir Cauce:**
   ```
   C:\cauce-piloto\iniciar.bat
   ```
   Se abre `http://127.0.0.1:8100/`. Entra como `admin` con tu contraseña. No se abre el portal de prueba.
   Si la configuración no es segura, el servidor **no arranca** y dice qué corregir.
5. **Crear el operador:** menú **Usuarios › Nuevo**, rol operador, su región y **su propia contraseña**.
   No hay ningún operador creado de antemano.
6. **Recorrido ficticio:** con el operador, Ventanilla › Nueva atención › expediente ficticio con consentimiento ›
   «Agendar a mano en el portal oficial» › frase › folio dos veces › Registrar › cerrar la atención.
7. **Respaldo al cerrar** (puede ser con el motor en marcha):
   ```
   C:\cauce-piloto\respaldar_externo.bat
   ```
   Esperado: `RESULTADO: respaldo externo hecho y verificado`. Abre el `.json` que queda junto al respaldo en
   `D:\CaucePiloto\respaldos`: debe decir `"revision": "0017_asistido"` y `expedientes` mayor que 0.
   Si la base no existe, está vacía o no tiene esquema, el respaldo **falla** con un mensaje claro (antes lo daba
   por bueno).
8. **Ensayo de restauración** (no toca la base real):
   ```
   C:\cauce-piloto\verificar_respaldo.bat D:\CaucePiloto\respaldos\<archivo>.db
   ```
9. **Autodiagnóstico:** `C:\cauce-piloto\diagnostico.bat`. Sin CRÍTICOS ni avisos de «Recuperación» o «Respaldo externo».

En la MSI, `NoDefaultCurrentDirectoryInExePath=1`: usa la **ruta completa** del `.bat` o doble clic.

## Qué queda en `.env`

| Variable | Valor | Para qué |
|---|---|---|
| `MONITOREO_ENTORNO` | `produccion` | Sin laboratorio, sin datos de demostración, configuración estricta |
| `MONITOREO_PRODUCCION_FLUJO_ADMIN` | `1` | Expedientes, consentimiento, Ventanilla, mesa de control y cita asistida |
| `MONITOREO_EXIGIR_CONSENTIMIENTO` | `1` | Sin consentimiento vigente no se agenda |
| `MONITOREO_DIARIO_COPIA` | `<externa>\diario` | Copia del diario de eventos críticos en otra unidad |
| `MONITOREO_RESPALDO_EXTERNO` | `<externa>\respaldos` | Destino de `respaldar_externo.bat` |
| `MONITOREO_HOST` / `MONITOREO_PUERTO` | `127.0.0.1` / `8100` | Solo este equipo, dirección fija |
| `MONITOREO_ADMIN_PASSWORD` | la tuya | Solo se usa para crear el administrador la primera vez |
| `MONITOREO_SECRET_KEY` | aleatoria | Generada en este equipo |
| `MONITOREO_CAPTURA_SOLO_SINTETICOS` | `1` | Solo datos ficticios hasta que se autoricen datos reales |

No se definen `MONITOREO_DB_URL` ni `MONITOREO_DATA_DIR`: la base es `data\monitoreo.db` de la carpeta del piloto.

## Contraseñas

- La instalación del piloto **no contiene ninguna contraseña de laboratorio**: el administrador usa la que tú
  escribes y el operador la que le pongas al crearlo.
- En producción el servidor **no arranca** si `MONITOREO_ADMIN_PASSWORD` es una de las contraseñas publicadas en el
  código (`Admin-Laboratorio-2026`, `Admin-Piloto-Lab-2026`…). `preparar_piloto.bat` también las rechaza.
- `.env` contiene tu contraseña inicial en claro: no lo copies ni lo envíes. Tras el primer arranque puedes
  cambiar la contraseña desde el panel.

## No verificado todavía

- Estos pasos se probaron en Linux (preparar, comprobar, arrancar, iniciar sesión, respaldo y diagnóstico) y en la
  CI. **Falta ejecutarlos en el Windows físico**: es la validación de hoy.
- BitLocker en la unidad externa y la exportación de la clave del diario siguen pendientes (ver
  `PROTECCION_DATOS_PILOTO.md`). Sin eso, no se usan datos reales.
