# Coincidencia → Forms → confirmación supervisada → folio verificado (laboratorio)

Rama `claude/project-thread-c40806`, apilada sobre el PR #14 (`claude/project-thread-ctpg63`, que incluye el PR #12).
Solo laboratorio y datos FICTICIOS. No crea otro motor: une piezas que ya existían.

## 1. Qué faltaba

| Paso del recorrido | Ya existía | Faltaba |
|---|---|---|
| Expediente con fechas aceptables → plan del día | sí (Ventanilla, plan, importador) | — |
| Calendario del portal → observación → coincidencia | sí (observador Node/Chromium, `coincidencias.py`) | — |
| Operación preparada (reservación PREPARADA con fecha y sucursal) | sí (`operacion.preparar`) | — |
| Rellenar y verificar con Forms sin enviar | sí (PR #12/#14, botón de Ventanilla) | **Forms no recibía la coincidencia y su resultado no volvía a la reservación** |
| Cita confirmada solo con evidencia | sí, por el navegador HTTP propio de Monitor o por la confirmación asistida | **con Forms, la cita confirmada en la ventana nunca llegaba a Monitor** |

## 2. Qué hace ahora (`monitoreo/autorrelleno/coincidencia.py`)

1. Si el expediente de la atención tiene una **operación preparada por ese operador** (elemento del plan
   `OPERACION_PREPARADA` con reservación `PREPARADA`), el autorrellenado la adjunta. La fecha y la sucursal viajan a
   Forms como `fecha_cita` / `sucursal_cita` **solo si el perfil aprobado del formulario las declara** (formularios de
   una pantalla). En el portal de laboratorio por día no viajan: van en su 2.ª pantalla (calendario), que elige la persona.
   Historial: `COINCIDENCIA_ENTREGADA_A_FORMS`.
2. Al terminar Forms, su resultado (sin valores) queda en `reservaciones.evidencia.forms`. Si rellenó y verificó todos
   los campos sin enviar → `OPERACION_LISTA_CONFIRMACION`. **No es una cita**: la reservación sigue `PREPARADA` y el
   plan sigue `OPERACION_PREPARADA`. Si no, `OPERACION_FORMS_SIN_PREPARAR`.
3. La persona confirma en el portal (en la ventana de Forms: campos manuales, día de la coincidencia, «Continuar») y el
   operador teclea el folio dos veces en la tarjeta. `POST /api/autorrelleno/reservaciones/{id}/folio`:
   - diario de eventos críticos: intento ANTES de consultar, resultado DESPUÉS;
   - el adaptador del portal (laboratorio: API por folio) debe decir `CONFIRMADA` y coincidir **titular, trámite,
     fecha y sucursal de la coincidencia**; si algo no coincide no cambia nada (`FOLIO_RECHAZADO`, 409);
   - solo entonces: reservación `CONFIRMADA` (evidencia `PORTAL_API_FOLIO`, `verificada_con_proveedor: true`),
     expediente `RESERVACION_CONFIRMADA`, plan `CONFIRMADO` (`operacion.sincronizar`);
   - folio ya usado por otra reservación → `EN_VERIFICACION` para un administrador (regla existente);
   - un portal sin vía autorizada de verificación → `SIN_VERIFICACION_AUTORIZADA`: se usa la confirmación asistida
     (queda «declarada por una persona»).
4. `AdaptadorLaboratorio.verificar` acepta `identificador` y `tramite` opcionales y devuelve qué se comprobó (booleanos).

## 3. Evidencia (contenedor Linux, 2026-11-24)

- `scripts/recorrido_coincidencia_forms.py` con **Forms real ff117da** y el **observador real** (Node + Chromium):
  **24/24** (`docs/evidencia_coincidencia/recorrido_coincidencia_forms.json`). Del día habilitado a la coincidencia:
  0,45 s; Forms: 37,6 s (sin ventana). La persona del paso 5 está **simulada** por el script.
- Hallazgo de una corrida anterior (útil): el observador asignó un día ya publicado dentro del rango aceptable
  (no el que el script esperaba), la «persona» confirmó otro día y Monitor **rechazó** el folio por fecha.
- `tests/test_autorrelleno_coincidencia.py`: 6/6 (con espía). Controles negativos: sin comprobar titular o sin la
  integración del adaptador, las pruebas fallan.
- Regresión: `test_adaptadores`, `test_autorrelleno_forms`, `test_autorrelleno_externo`, `test_planificador`,
  `test_piloto_asistido`, `test_portal_dias`: 74 aprobadas, 4 omitidas. Con Forms real (`FORMS_REPO`): 31 + 1 omitida.

- **Pantalla de Ventanilla probada en navegador** (`--interfaz`, Chromium automatizado, `scripts/recorrido_coincidencia_ui.js`):
  el operador retoma la atención, ve la coincidencia («todavía NO es una cita»), pulsa «Autorrellenar», la tarjeta
  pasa sola a «Lista para confirmación supervisada», teclea un folio inexistente (error visible, nada cambia) y el
  correcto (la atención muestra «Reservación confirmada» con evidencia `PORTAL_API_FOLIO`). Sin errores de JavaScript.
  **37/37** con el observador universal (con los ajustes de pantalla) (`docs/evidencia_coincidencia/recorrido_interfaz_ventanilla.json`, capturas en
  `docs/evidencia_coincidencia/interfaz/`) y **35/35** con el observador del calendario.
- Ajustes de pantalla aprobados por Diego (2026-11-24) y verificados en el recorrido con navegador:
  1. En cuanto la operación del plan va por Forms (se lanzó el autorrellenado con ella), el flujo anterior
     («Revisión de campos» y «Enviar datos al portal») se oculta y el «Siguiente» de la cabecera habla del folio.
     Solo es visual: la API del flujo anterior no cambió.
  2. El estado `CADUCADA` del resultado de Forms se muestra como «Tiempo de revisión agotado» (el estado real sigue
     en `data-estado`); la operación sigue diciendo «todavía NO es una cita».
  3. Con la operación LISTA, «Autorrellenar» queda desactivado («Autorrellenado completo») y se presenta
     «Registrar folio». Volver a abrir el formulario exige un motivo (`VENTANA_CERRADA`, `TIEMPO_AGOTADO`,
     `DATO_CORREGIDO`), máximo 2 veces, sobre la MISMA reservación (evento `AUTORRELLENO_REINTENTO`); si el reintento
     sale peor, la operación sigue LISTA. Lo aplica el servidor (`OPERACION_YA_LISTA`, `MOTIVO_NO_VALIDO`,
     `REINTENTOS_AGOTADOS`), no solo la pantalla. Prueba: `test_operacion_lista_no_se_vuelve_a_autorrellenar_…`.
  Recorrido con navegador: **37/37** (observador universal) y **35/35** (calendario). El reintento se comprobó por
  API (prueba automática); en pantalla solo se comprobó que aparece y está desactivado sin motivo.

## 4. Límites

- Solo laboratorio (router en `SOLO_LABORATORIO`, `MONITOREO_AUTORRELLENO_LAB=1`). Forms sigue con el mismo usuario
  de Windows que Monitor: sin datos reales hasta el aislamiento del PR #13.
- No hay portal real autorizado. El observador y la verificación por folio solo hablan con el portal de laboratorio.
- Monitor nunca confirma ni envía solo; la selección del día y «Continuar» los hace una persona.

## 5. Prueba física en la MSI

```powershell
mkdir C:\cauce-coincidencia; cd C:\cauce-coincidencia
git clone -b claude/project-thread-c40806 https://github.com/dass-666/Cauce-monitor-engine monitor
git clone https://github.com/dass-666/CAUCE-UNIVERSAL-CONEXION cauce-forms
cd cauce-forms; git checkout ff117da
python -m venv .venv; .venv\Scripts\pip install -r requirements-dev.txt
$env:PLAYWRIGHT_BROWSERS_PATH="$PWD\.pw-browsers"; .venv\Scripts\python -m playwright install chromium; cd ..
cd monitor; powershell -ExecutionPolicy Bypass -File .\instalar.ps1
.venv\Scripts\python.exe scripts\preparar_navegador.py   # Playwright de Node + Chromium del observador, dentro de la carpeta
C:\cauce-coincidencia\monitor\recorrido_coincidencia_forms.bat C:\cauce-coincidencia\cauce-forms --con-ventana
```

Resultado esperado: `35/35 comprobaciones` (el lanzador usa `--interfaz`: los pasos del operador se hacen en la pantalla) y `resultados\recorrido_coincidencia_forms\recorrido_coincidencia_forms.json`.
Después, a mano con `autorrelleno_laboratorio.bat` (Ventanilla): dejar a una persona ficticia en el plan, habilitar
un día en «Calendario del laboratorio», preparar la coincidencia, autorrellenar, completar en la ventana de Forms
(clave AAA000000, aviso, día de la coincidencia, «Continuar»), y teclear el folio en la tarjeta: debe quedar
«Cita CONFIRMADA (folio comprobado con el portal)».

## Mismo sitio para el día y el formulario (2026-11-24)

- El día de una coincidencia solo se lleva al formulario **del mismo sitio** que lo publicó. El origen es el
  observador cuyo evento asignó el elemento del plan (`coincidencia.origen`): fuente web → host de su URL;
  observador del calendario → portal configurado. Con un perfil externo, origen distinto o desconocido →
  `SITIO_DISTINTO` / `ORIGEN_DESCONOCIDO` y a Forms no llega nada. En el laboratorio, 127.0.0.1 y localhost cuentan
  como el mismo sitio.
- Con un formulario externo, la reservación anota `forms.externo` y `forms.sitio`; el folio **no** se comprueba con
  el adaptador del laboratorio (`SIN_VERIFICACION_AUTORIZADA`) y nada queda confirmado. Para confirmar citas de un
  sitio real hace falta un adaptador de verificación autorizado por ese sitio.
- Recorrido en navegador (`--interfaz`, observador web, Forms real ff117da): **45/45**, con reintento desde el botón
  (motivo obligatorio, misma reservación, un reintento menos) y comprobación de que sin folio validado nada aparece
  como cita confirmada. Pruebas: `test_autorrelleno_coincidencia.py` + externo + forms, 61 (SQLite y PostgreSQL).
