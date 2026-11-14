# Comprobaciones físicas en Windows (MSI) · v2.1

Lo que **no** puede demostrar GitHub Actions y hay que hacer a mano en el equipo real. GitHub
Actions usa Windows Server sin pantalla, con Chromium automatizado, sin suspensión, sin antivirus
de escritorio y sin red Wi-Fi. Cada punto dice qué hacer, qué debe pasar y qué anotar. Usa
**solo datos ficticios** y el portal de laboratorio (127.0.0.1).

Anota en cada punto: **OK / FALLA / NO APLICA**, la fecha y, si falla, una captura y el
mensaje exacto. No pegues datos de personas reales.

## Auditoría del flujo de Ventanilla (lectura del código, cierre v2.1)

Lo revisado en `monitoreo/web/ventanilla.js`, `monitoreo/web/captura.js`, `monitoreo/web/app.js`
y `monitoreo/captura/ventanilla.py`. Estos hallazgos salen de leer el código: no son mediciones.

| # | Hallazgo | Gravedad | Recomendación |
|---|---|---|---|
| A1 | **Ventanilla no se repinta sola** (`noAuto`), para no borrar lo que el operador teclea. Consecuencia: en «Plan del día», una coincidencia nueva **no aparece hasta que el operador hace algo** (vuelve a la lista, cambia de sección o recarga). No hay un botón «Actualizar». | Media: el operador puede tardar en ver una fecha que acaba de aparecer | Añadir un botón «Actualizar plan», o un aviso que no repinte el formulario. Es un cambio de pantalla, no de reglas. **No se hizo en esta ronda: pendiente de tu decisión.** |
| A2 | La sesión dura `MONITOREO_TOKEN_TTL_HORAS` (12 h por omisión). Al vencer, la siguiente acción lleva a la pantalla de entrada («Tu sesión expiró»). La atención queda en el servidor y se retoma con «Retomar». | Baja | Comprobarlo físicamente (C7). Los campos manuales tecleados y no enviados se pierden: es intencional, nunca se guardan. |
| A3 | Los campos manuales (p. ej. la clave de identidad) **nunca se guardan**. Si se recarga la página o se cae la red antes de «Enviar», hay que teclearlos otra vez. | Diseño (privacidad) | Explicárselo a los operadores. |
| A4 | Contra el doble clic: «Enviar datos» y «Confirmar reservación» se desactivan mientras esperan. «Nueva atención» reutiliza la misma clave si la red falla, así que el servidor devuelve la misma atención. | Correcto | Verificar con ratón y teclado reales (C5). |
| A5 | En la lista solo se puede **preparar** desde «Plan del día» si el usuario tiene un operador vinculado. Un administrador sin operador ve el plan, pero no prepara. | Diseño | Usar un usuario operador en las pruebas. |
| A6 | `ventanilla.bat` abre el **navegador predeterminado** del equipo (en Windows suele ser Edge). Las pruebas automáticas usan **Chromium**. Edge comparte motor con Chromium, pero no se ha probado. | Media hasta comprobarlo | C3 y C4. |
| A7 | La «jornada» del plan se calcula con `MONITOREO_ZONA_HORARIA` (America/Mexico_City), no con el reloj de Windows. Si el equipo tiene otra zona, la fecha del plan puede no coincidir con la del reloj cerca de la medianoche. | Baja | C12. |
| A8 | El observador usa Chromium **sin ventana** en `.browsers` de la instalación. En Windows, rutas largas, carpetas con espacios o acentos, o un antivirus, pueden impedir que arranque. Ninguno de estos casos está probado. | Media hasta comprobarlo | C2 y C9. |

## Comprobaciones

### Instalación

- **C1. Instalación limpia.**
  - Hazlo en una carpeta corta y fuera de OneDrive (`C:\cauce-lab`):
    `powershell -ExecutionPolicy Bypass -File .\instalar.ps1`.
  - Debe pasar: termina con el autodiagnóstico sin CRÍTICOS.
  - Anota la versión de Windows y la de Python.
- **C2. Navegador dentro de la instalación.**
  - Ejecuta `.venv\Scripts\python.exe scripts\preparar_navegador.py`.
  - Debe pasar: termina en `LISTO` y existe `C:\cauce-lab\.browsers\chromium_headless_shell-*`.
  - Repite en una carpeta **con espacio y acento** (`C:\Pruebas Cauce\instalación`) y anota si
    también funciona.
  - Repite `python -m monitoreo.navegador --probar` **después de reiniciar Windows**, sin
    exportar nada. Debe decir `LISTO` y el origen «carpeta .browsers de la instalación».

### Ventanilla con ratón y teclado reales

- **C3. Navegador predeterminado.**
  - Abre `ventanilla.bat` y anota qué navegador se abre (Edge o Chrome) y su versión.
  - Debe pasar: se abre directamente en Ventanilla.
- **C4. Pantalla y escala.**
  - Con la escala de Windows al 100 %, 125 % y 150 %, revisa «Plan del día», la atención y el
    calendario.
  - Debe pasar: nada queda cortado ni hay desplazamiento horizontal. El calendario del día
    asignado es visible.
  - El selector de fecha de nacimiento acepta el formato dd/mm/aaaa.
- **C5. Doble clic y teclado.**
  - Haz doble clic rápido en «Nueva atención», «Enviar datos al portal» y «Confirmar reservación».
  - Debe pasar: una sola atención, un solo envío y una sola confirmación (un solo folio).
  - Prueba también Tab y Enter en los formularios.
- **C6. Flujo completo a mano.**
  - Atiende 3 personas ficticias: 2 hasta el folio y 1 sin cita.
  - Debe pasar: «Cita confirmada» aparece solo con folio. La atención sin cita vuelve al plan si el
    expediente estaba en él, o a la cola si no.
  - Anota cuánto tarda cada atención.
- **C7. Sesión vencida.**
  - Pon `MONITOREO_TOKEN_TTL_HORAS=1` en una copia de prueba, espera 1 h a mitad de una atención y
    pulsa algo.
  - Debe pasar: vuelve a la entrada; al entrar, «Retomar» continúa en el mismo paso.
- **C8. Plan del día (hallazgo A1).**
  - Con el plan creado y el observador activo, habilita un día en el portal de laboratorio
    (`demo_ventanilla.bat` o los controles del laboratorio). Anota:
    - si la coincidencia aparece **sin** tocar nada (según el código, no debería);
    - qué acción hace falta para verla;
    - cuánto tiempo pasa.

### Observador y energía

- **C9. Antivirus y SmartScreen.**
  - Con el antivirus del MSI activo, inicia el observador (paso 2 de `aceptacion_v21.py`, o
    `POST /api/observador`).
  - Debe pasar: el estado llega a ACTIVA.
  - Si no, el registro debe tener `ERROR del navegador (...)` y `GET /api/observador` el
    `ultimo_error`. Anota el mensaje.
- **C10. Suspender y cerrar la tapa.**
  - Con el observador activo y una atención a medias, suspende 5 minutos y vuelve.
  - Debe pasar:
    - el observador vuelve a ACTIVA solo (lo relanza el vigilante o el supervisor);
    - **no** duplica eventos;
    - la atención se retoma.
  - Anota cuánto tarda en volver.
- **C11. CPU ocupada.**
  - Ejecuta `python scripts\investigar_bloqueo_observador.py scripts\observador_calendario.js 100 resultados\bloqueo` con otras aplicaciones abiertas.
  - Anota los ciclos y los bloqueos. El bloqueo de Playwright solo se midió en Linux (0 de 750).
    En Windows está **sin medir**.
- **C12. Zona horaria.**
  - Cambia la zona de Windows a otra distinta de México y crea el plan.
  - Debe pasar: la fecha de la jornada es la de `MONITOREO_ZONA_HORARIA`. Anota si confunde al
    operador.

### Recorridos automáticos en el MSI

- **C13.** En el MSI: `pruebas.bat`, `python scripts\recorrido_v21.py --salida resultados\recorrido_v21`
  y `python scripts\aceptacion_v21.py --salida resultados\aceptacion_v21`.
  - Debe pasar: código 0.
  - Guarda los JSON: son la evidencia de que Windows quedó **verificado en el equipo real**. Hasta
    entonces, solo está verificado en GitHub Actions.
- **C14. Red.**
  - Wi-Fi y cable, más el cambio de uno a otro a mitad de una atención (punto G.3 de
    `GUIA_VENTANILLA_WINDOWS.md`).
  - Solo aplica si el portal de laboratorio está en otro equipo de tu red.

## Qué cuenta como «Windows verificado»

C1, C2, C6 y C13 en OK en el MSI, con sus JSON guardados. El resto son riesgos que conviene
conocer antes de un piloto, pero no bloquean el laboratorio.
