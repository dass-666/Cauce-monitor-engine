# Autorrellenar formulario desde Ventanilla con Forms (laboratorio)

Rama `claude/project-thread-ksm2qd`, sobre `claude/lab-puerto-estable` (8328e2c). Solo laboratorio y datos FICTICIOS.

## 1. Qué hace

En una atención de Ventanilla con expediente y consentimiento vigente aparece la tarjeta **«Autorrellenar formulario
(Forms · laboratorio)»**. Al pulsar el botón:

1. Monitor comprueba con **su base y sus reglas** (`monitoreo/autorrelleno/adaptador.py`, `evaluar()`):
   - la atención es del operador que pulsa y está abierta; el usuario está vinculado a un operador;
   - el operador puede trabajar ese expediente (región, sucursal, que no lo tenga otro operador: `ventanilla._disponible`);
   - el expediente está LISTO o tomado por él (ASIGNADO / CAPTURA_EN_PROCESO);
   - hay **consentimiento vigente** (otorgado, no revocado, no vencido, del mismo trámite, sin revocación pendiente en el
     diario) con huella SHA-256 del documento y su **cadena de eventos íntegra** (`consentimiento.verificar`). Se exige
     aunque `MONITOREO_EXIGIR_CONSENTIMIENTO=0`;
   - no hay recuperación pendiente.
   Si algo falla **no se lanza nada** y se responde 403/404/409 con un código (`SIN_CONSENTIMIENTO`,
   `CONSENTIMIENTO_REVOCADO`, `CONSENTIMIENTO_NO_VERIFICABLE`, `PERMISO_DENEGADO`, `NO_ES_TU_ATENCION`, …), que se audita.
2. Arma la solicitud `cauce.conector.v1` con **solo los 8 campos automáticos** del contrato del portal por día
   (operación, trámite, identificador, fecha de nacimiento, teléfono y su confirmación, correo y su confirmación;
   folio anterior solo al reprogramar). No viajan el nombre, la CURP, notas ni los campos manuales (clave de identidad,
   aviso de privacidad). La autorización es `aut_<código del consentimiento real>` y `rev_<usuario que pulsó>`.
3. Lanza Forms (`python -m cauce_form_connector preparar`, una solicitud por proceso) con un **entorno limpio**: ninguna
   variable `MONITOREO_*` (ni clave secreta ni tokens). Forms abre una **ventana visible**, teclea, verifica y **no envía**.
   El operador conserva la ventana: revisa, teclea los campos manuales y la cierra (o pulsa él mismo «Continuar»).
4. Mientras la ventana está abierta, Monitor vigila cada segundo: si el consentimiento deja de estar vigente o la
   atención se cierra, **termina el proceso de Forms y todo su árbol** (Chromium incluido) → `CANCELADA`.
5. Valida la respuesta con las reglas del contrato (estructura, huella de preparación, búsqueda de datos personales).
   Una respuesta inválida queda `INCIERTA` y no se muestra. El historial guarda `AUTORRELLENO_SOLICITADO` /
   `AUTORRELLENO_TERMINADO` con estados y nombres de campo: **nunca valores**.

Estados que ve el operador: `PREPARADO` (ventana cerrada sin envío), `CADUCADA` (rellenado y verificado, la ventana
siguió abierta más del tiempo de revisión, 10 min por omisión), `ENVIADA_POR_PERSONA` (indicio, no cita),
`CON_INCIDENCIAS`, `DETENIDA`, `RECHAZADA`, `ERROR`, `CANCELADA`, `INCIERTA`.

**Importante:** si el operador pulsa «Continuar» en la ventana de Forms y llega a confirmar en el portal de laboratorio,
esa cita NO queda registrada en Monitor (Forms solo lo observa como indicio). La cita se sigue registrando con el flujo
de Ventanilla de siempre.

## 2. Decisiones sobre los PR existentes

- **Forms PR #3** (`claude/integracion-monitor-autorrelleno`, 16d3e87): **no hace falta y no conviene usar su
  `autorrellenar` desde Monitor.** `puente_monitor.construir_solicitud` fija `consentimiento_vigente: True` y usa por
  omisión `aut_demo` / `rev_demo`: es justo la autorización ficticia que la misión prohíbe. Monitor construye la
  solicitud él mismo y usa el modo `preparar` y `aprobar-perfil` que ya están en `main` de Forms (aa1bb02). Se reutiliza la
  idea del PR #3 (perfil configurable y minimización) como `monitoreo/autorrelleno/perfil_portal_laboratorio_dia.json`.
  Recomendación para Forms: si se fusiona el PR #3, que `autorrellenar` exija la autorización del llamador en vez de
  valores por omisión.
- **Monitor PR #4** (`claude/prueba-conjunta-conector`): se trae **solo el lanzador de laboratorio** (Job Object en Windows,
  terminación del árbol) con sus 8 pruebas, sin cambios de lógica, ahora en `monitoreo/autorrelleno/lanzador_lab.py`.
  No se trae `conector/confirmacion.py` (en memoria; lo sustituye la confirmación humana persistente del piloto
  asistido), ni el arnés/CI conjunta fijados en el commit antiguo del conector, ni la especificación de aislamiento.
  El PR #4 puede cerrarse sin fusionar cuando este quede aceptado.
- `conector.aislamiento.puede_lanzar()` **sigue en False**. Este camino es otro, explícito y solo de laboratorio.

## 3. Por qué solo laboratorio (límite de seguridad)

Forms corre con el mismo usuario de Windows que Monitor: técnicamente podría leer `cauce_lab.db`. Con datos ficticios es
aceptable; **con datos reales no**, hasta tener la cuenta aparte + ACL descritas en `docs/CONECTOR_CONTRATO_v1.md` §4.
Por eso el router está en `modulos.SOLO_LABORATORIO` (nunca se monta en producción) y además exige en cada petición
`MONITOREO_AUTORRELLENO_LAB=1`, portal propio en 127.0.0.1 con token de control y un perfil aprobado cuya huella coincide.

## 4. Pruebas (contenedor Linux, bases temporales)

| Prueba | Resultado |
|---|---|
| `tests/test_autorrelleno_forms.py` con conector espía (siempre) | 12/12 (incluye «detener Monitor con la ventana abierta termina Forms») |
| `tests/test_autorrelleno_forms.py` con **Forms real** (`FORMS_REPO=…`) | 3/3: 8 campos y 2 confirmaciones; revocación a mitad termina Forms y Chromium; **cerrar la ventana con la X → `PREPARADO`** (con ventana, `FORMS_HEADLESS=0`) |
| Cierre de la ventana con la X, repetido | **10/10 `PREPARADO`** (2026-11-22, Xvfb, `WM_DELETE_WINDOW`) |
| `tests/test_lanzador_lab.py` | 9/9, incluye «Monitor muere de golpe → el conector no queda huérfano» (falla sin la corrección: control negativo hecho) |
| `scripts/recorrido_autorrelleno.py --con-ventana` | 18/18; la búsqueda de datos cubre ahora **todos** los archivos de texto del laboratorio, de Forms (`logs/`, `state/`) y de la evidencia |
| `scripts/prueba_reinicio_autorrelleno.py --con-ventana` (Forms real) | 11/11: parada ordenada y muerte forzada de Monitor con la ventana abierta → 0 procesos de Forms/Chromium; Monitor rearranca y acepta otro autorrellenado |
| `scripts/validar_autorrelleno_windows.py` (el mismo que usa el `.bat`, ejecutado en Linux) | TODO OK |
| Suite completa de Monitor (SQLite) | 763 aprobadas, 12 omitidas; se excluyó `test_database_down_is_reported_without_crashing` (necesita `psycopg`, no instalado en el contenedor) |

**Corrección de la validación física (punto 7).** Antes, si Monitor se detenía con la ventana abierta, Forms seguía vivo
hasta su propio plazo (10 min) en Linux, y en una parada ordenada en cualquier sistema. Ahora:
- al apagarse la aplicación, `adaptador.apagar()` cancela la corrida (`CANCELADA` / `MONITOR_DETENIDO`) y termina el árbol;
- si Monitor muere de golpe: en Windows el Job Object (no heredable) se cierra con Monitor y mata todo el árbol; en Linux
  el hijo directo recibe SIGTERM del núcleo (`PR_SET_PDEATHSIG`) y Forms cierra su Chromium.
Límite: tras reiniciar, el expediente conserva `AUTORRELLENO_SOLICITADO` sin `AUTORRELLENO_TERMINADO` (la corrida vivía
en memoria). No afecta a la seguridad; se puede añadir un evento de cierre al arrancar si se considera útil.

Las pruebas espía demuestran qué sale de Monitor: los 8 valores salen de la base, sin nombre ni campos manuales;
`aut_<consentimiento>` coincide con el código de la base; ninguna variable `MONITOREO_*` llega a Forms; y sin
consentimiento, revocado, manipulado en la base, de otro operador/región o con perfil alterado, Forms **no se ejecuta**.

## 5. Validación física en la MSI (Windows 11)

No toca `D:\cauce-demo-8328e2c` (puerto 8100), `C:\cauce-msi`, `C:\cauce-monitor-validacion-pr11` ni ninguna base o
respaldo: todo va en una carpeta nueva y el laboratorio usa un puerto libre distinto.

```powershell
mkdir C:\cauce-autorrelleno; cd C:\cauce-autorrelleno
git clone -b claude/project-thread-ksm2qd https://github.com/dass-666/Cauce-monitor-engine monitor
git clone https://github.com/dass-666/CAUCE-UNIVERSAL-CONEXION cauce-forms
cd cauce-forms; git checkout aa1bb02; .\instalar_windows.bat; cd ..
# si la MSI no tiene el lanzador «py»: python -m venv .venv; .venv\Scripts\pip install -r requirements-dev.txt;
#   $env:PLAYWRIGHT_BROWSERS_PATH="$PWD\.pw-browsers"; .venv\Scripts\python -m playwright install chromium
cd monitor; powershell -ExecutionPolicy Bypass -File .\instalar.ps1
C:\cauce-autorrelleno\monitor\validar_autorrelleno_windows.bat
```

**Validación automática (un solo paso, ~10 min).** `validar_autorrelleno_windows.bat` comprueba la instalación y el
commit de Forms, ejecuta las pruebas con Forms real y **ventana visible** (incluye cerrar la ventana con la X mediante
`WM_CLOSE`, revocar a mitad, detener Monitor y matarlo de golpe), el recorrido por la interfaz y el reinicio de Monitor.
Se ven abrirse y cerrarse ventanas de Chromium: no hay que tocarlas. Deja `resultados\validacion_autorrelleno\RESUMEN.txt`
(solo códigos y conteos) y los `.log` de cada paso. Si falta Node.js/Playwright, el recorrido aparece como `OMITE`.

**Comprobación a mano (2 min)**, con `C:\cauce-autorrelleno\monitor\autorrelleno_laboratorio.bat`:


(En la MSI usa la ruta completa del `.bat` o doble clic: `NoDefaultCurrentDirectoryInExePath=1`.)

1. La consola imprime la dirección de Ventanilla y «Autorrellenar con Forms: ACTIVO». Se abre el navegador.
2. Entra como `operador1` / `Operador-Laboratorio-2026` → Ventanilla → «Nueva atención».
3. Registra una persona ficticia (región NORTE, sucursal NOR-1) **con consentimiento**: medio, versión del aviso y
   cualquier archivo como «documento firmado» (la huella se calcula en el navegador).
4. En la tarjeta «Autorrellenar formulario» pulsa el botón. Debe abrirse **otra ventana de Chromium** con el portal de
   laboratorio y verse cómo se escriben los 8 campos. Forms no pulsa «Continuar».
5. Teclea si quieres la clave manual, revisa y **cierra la ventana**. En Ventanilla: `PREPARADO`, 8 ✓ y 2 «coincide».
6. Negativos: despliega el consentimiento y registra la revocación → la atención se cierra y no hay botón. Crea otra
   persona sin consentimiento → Ventanilla pide registrarlo y no hay botón.

Recorrido automático opcional (necesita Node.js y Playwright como los otros recorridos):
`.venv\Scripts\python.exe scripts\recorrido_autorrelleno.py --forms ..\cauce-forms --con-ventana`

## 6. Qué queda sin probar

Estado de los 8 puntos. **Verificado en la MSI (Windows 11, 2026-11-22, 18:02-18:10 hora de México)** con
`validar_autorrelleno_windows.py` en `C:\cauce-autorrelleno` (monitor 57c6333, Forms aa1bb02): **TODO OK** (pytest 25/25,
recorrido 18/18, reinicio 11/11) y comprobación en la interfaz real → `PREPARADO`, 8 ✓, 2 «coincide», «Envío por Forms: no».

| # | Punto | Linux (contenedor) | Windows 11 (MSI) |
|---|---|---|---|
| 1 | Instalación de ambos proyectos | n/a | sí (Forms: `instalar_windows.bat` falla sin el lanzador `py`; se hicieron sus pasos con `python`) |
| 2 | El botón abre Chromium visible | sí | sí (ventana «Citas por día (laboratorio)» capturada en el escritorio) |
| 3 | 8 campos y 2 confirmaciones | sí | sí |
| 4 | Forms no envía | sí | sí |
| 5 | Cerrar la ventana → `PREPARADO` | sí, 10/10 | sí, con `WM_CLOSE` (el mensaje de la X); el clic físico de una persona no se probó |
| 6 | Revocar cierra Forms y sus procesos | sí | sí |
| 7 | Reiniciar Monitor sin huérfanos | sí | sí (parada ordenada y forzada: 0 procesos) |
| 8 | Sin datos sensibles en registros | sí | sí |

Protecciones comprobadas en la MSI: `C:\cauce-msi` sin cambios (6158 archivos, misma fecha más reciente); el servicio del
puerto 8100 es el mismo proceso antes y después; en `D:\cauce-demo-8328e2c` solo escribe su propio servicio (como ya
hacía antes de empezar).

Otros límites:
- Cerrar con `WM_CLOSE`/`WM_DELETE_WINDOW` es lo que hace la X, pero no es una persona: por eso queda la comprobación
  a mano. Con un conector antiguo (PR #4) hubo 2/10 `ERROR` al cerrar; con `aa1bb02` no se repitió en 10 intentos.
  Si aparece en Windows, la causa probable está en Forms (`_esperar_revision` mira la desconexión del navegador antes que
  el cierre de la página) y se corregiría allí, no en Monitor.
- La prueba de cierre espera 15 s (25 s en el `.bat`) a que Forms termine de teclear; si Windows es más lento podría
  cerrar antes de tiempo y fallar sin que sea un error real (se vería `DETENIDA`/`ERROR` en `1_pytest.log`).
- Solo el formulario POR_DIA (`/cita`). POR_HORARIO necesitaría otro perfil aprobado.
- La aprobación del perfil en laboratorio la hace el arranque (`rev_laboratorio`) con la correspondencia revisada en
  este PR; en un portal real tendría que ser un paso humano explícito.
