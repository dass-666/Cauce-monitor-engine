# Plan: aislar Forms en Windows (cuenta aparte + ACL) antes de cualquier dato real

Estado: **PROPUESTA. Nada de este plan se ha ejecutado en la MSI.** `aislamiento.puede_lanzar()` sigue en `False`.
Marcas: **[V]** verificado (con evidencia) · **[I]** inferido (razonable, sin medir) · **[P]** pendiente.
Base: especificación `docs/AISLAMIENTO_WINDOWS_ESPECIFICACION.md` (traída del PR #4) y contrato `docs/CONECTOR_CONTRATO_v1.md` §4.

## 1. Objetivo en una frase
Que el proceso de Forms (y su Chromium) corra con **otra cuenta de Windows** que **no pueda leer** la base, el `.env`,
los respaldos ni la clave del diario de Monitor, y tener una **prueba automática** que lo demuestre (`PermissionError`)
en la máquina real. Solo entonces tiene sentido que `puede_lanzar()` devuelva `True`.

## 2. Hallazgos que cambian el plan (antes de tocar nada)

| # | Hallazgo | Gravedad | Estado |
|---|---|---|---|
| H1 | **Crear la cuenta sin poner ACL empeora la situación.** Las carpetas creadas en la raíz de `C:\` heredan «Usuarios autenticados: Modificar», y una unidad de datos como `D:\` suele dar lectura a «Usuarios». Una cuenta nueva podría **leer y modificar** `C:\cauce-msi\data` y los respaldos de `D:\`. Hoy no pasa porque solo existe la cuenta de Diego. | Alta | [I] (ACL por defecto de Windows); se mide en la fase 0 con `icacls` |
| H2 | **Python instalado «solo para mí» no sirve.** El `.venv` de Forms apunta al Python base; si está en `C:\Users\MSIGF\AppData\...`, la cuenta restringida no puede ejecutarlo y Forms no arranca. Hace falta Python «para todos los usuarios» (`C:\Program Files\Python3xx`) o un Python dentro de la carpeta del conector. Lo mismo con el Chromium de Forms (`.pw-browsers` debe estar dentro de esa carpeta). | Alta (bloquea) | [I]; se mide en la fase 0 (`pyvenv.cfg`) |
| H3 | **Contradicción en la especificación (control C1).** «Denegar inicio de sesión local» rompe `CreateProcessWithLogonW`, que usa un inicio de sesión interactivo. En su lugar: ocultar la cuenta de la pantalla de inicio, clave aleatoria larga guardada con DPAPI, y denegar solo el acceso por red y por Escritorio remoto. | Media | [I]; se verifica en la fase 2 |
| H4 | **El lanzador de laboratorio actual no sirve para otra cuenta.** Encuentra los procesos con psutil y con una marca en el entorno, pero los procesos de otro usuario no muestran su entorno y su padre es el servicio de Windows, no Monitor. El nuevo lanzador se apoya **solo** en el Job Object (que sí los cubre). | Media | [V] por diseño; probado en CI (§4) |
| H5 | **El temporal y el perfil de Chromium** deben ir a la carpeta de trabajo del conector, no al perfil de Diego. | Baja | [V] en la prueba de CI (TEMP/TMP redirigidos) |
| H6 | **El Firewall «solo 127.0.0.1» vale para el laboratorio**, no para el portal externo: ese necesitará una lista de destinos permitidos, que depende de la autorización de FONACOT. | Informativa | — |
| H7 | **Un administrador o SYSTEM siempre puede leerlo todo.** La cuenta del conector nunca debe ser administradora, y Monitor no debe correr como SYSTEM. | Informativa | — |

## 3. Lo que ya existe en esta rama (`claude/project-thread-4lnq8h`)
- `monitoreo/autorrelleno/lanzador_restringido.py`: lanza un proceso **como otra cuenta** (`CreateProcessWithLogonW`),
  nace **suspendido**, se mete en un Job Object con «matar al cerrar» y **solo entonces** se reanuda; si no se puede
  meter en el Job, se mata sin haber ejecutado nada. stdin/stdout por tuberías, entorno explícito (sin `MONITOREO_*`).
  Solo laboratorio (`modo_laboratorio=True`) y solo Windows. Desde la rama `claude/project-thread-vaxyhp` está
  conectado a Ventanilla en modo laboratorio (ver F3 y `docs/AISLAMIENTO_VENTANILLA_LAB.md`).
- `scripts/aislamiento_sonda.py`: lo que corre como la cuenta: intenta leer la base, `.env`, respaldos, clave del
  diario; escribir en el código; conectarse a una IP pública; y recorre **toda** la raíz buscando cualquier archivo legible.
- `scripts/aislamiento_windows_lanzador.py`: prueba de extremo a extremo (A1–A11) que crea una cuenta de un solo uso,
  un árbol con datos **ficticios**, las ACL, la regla de Firewall, lanza la sonda y el Forms real, comprueba que no
  quedan procesos (cancelación, plazo, hijo abandonado, Monitor muerto de golpe) y **lo borra todo**. Se niega a correr
  sin `CAUCE_AISLAMIENTO_EFIMERO=1` (para no lanzarlo por accidente en la MSI).
- `.github/workflows/aislamiento.yml`: la ejecuta en runners Windows efímeros de GitHub (Python 3.12 y 3.13).
- `scripts/aislamiento_diagnostico_windows.ps1`: diagnóstico de **solo lectura** para la fase 0.
- `tests/test_lanzador_restringido.py` (Linux): incluye un **control negativo**: sin ACL, la sonda **sí** detecta que la
  base y el `.env` son legibles (así un «ok» en Windows no puede deberse a que la sonda no mire).

## 4. Fases

| Fase | Dónde | Qué cambia en el equipo | Aprobación |
|---|---|---|---|
| **F1** Técnica en runner efímero | GitHub Actions | Nada en la MSI | No (hecho en esta rama) |
| **F0** Diagnóstico | MSI | **Nada**: solo lee permisos y rutas, escribe un JSON | Conectar el equipo |
| **F2** Prueba en la MSI con copia de laboratorio | MSI, carpeta nueva `C:\cauce-aislamiento-lab` | Cuenta de **un solo uso** creada y **borrada** en la misma ejecución; ACL solo en la carpeta nueva; regla de Firewall temporal | **Sí, de Diego** |
| **F3** Integrar en Monitor | Código (rama/PR) | Nada en la MSI | PR; fusión con su aprobación |
| **F4** Cuenta permanente + ACL en la instalación que use Forms | MSI | Cuenta `cauce_conector` permanente; ACL sobre la carpeta de esa instalación y de los respaldos | **Sí, aparte**, después de F2 |

### F0 — diagnóstico (solo lectura)
`powershell -ExecutionPolicy Bypass -File scripts\aislamiento_diagnostico_windows.ps1 -Rutas C:\cauce-msi,C:\cauce-autorrelleno,D:\ -FormsDir C:\cauce-autorrelleno\forms`
Responde: ¿las carpetas dan acceso a «Usuarios autenticados»? (H1) · ¿el Python de Forms está en el perfil de Diego? (H2)
· ¿está activo el servicio «Inicio de sesión secundario»? · ¿Firewall activo? · ¿Diego es administrador?

### F2 — prueba real en la MSI (requiere aprobación)
1. Crear `C:\cauce-aislamiento-lab` (carpeta **nueva**) y copiar ahí Forms (con Python para todos los usuarios si F0
   confirma H2).
2. Ejecutar `aislamiento_windows_lanzador.py` como administrador: crea `cauce_conector_t`, ACL **solo en esa carpeta**,
   regla de Firewall, ejecuta A1–A11 y **borra** cuenta, perfil, regla y carpeta al terminar.
3. **Medición de H1 sin tocar nada:** mientras la cuenta existe, la sonda intenta abrir (sin leer contenido, solo
   «se puede abrir: sí/no») los archivos de `C:\cauce-msi` y `D:\`. El resultado dice cuánta exposición habría si la
   cuenta fuera permanente sin ACL. Es una lectura; no modifica esas carpetas. *(Opcional; Diego decide.)*
4. Reversión manual si algo se corta a mitad: `net user cauce_conector_t /delete`,
   `Remove-NetFirewallRule -DisplayName cauce-aislamiento-*`, borrar `C:\cauce-aislamiento-lab`.
   No toca `C:\cauce-msi`, `D:\cauce-demo-8328e2c`, `C:\cauce-autorrelleno` ni otras instalaciones.

### F3 — integración en Monitor (código) — HECHA EN CÓDIGO (rama vaxyhp), solo laboratorio
Detalle, evidencia y lo que falta: `docs/AISLAMIENTO_VENTANILLA_LAB.md`. Lo de abajo era la propuesta; se implementó así,
salvo que la regresión «falla si alguien amplía permisos» es la propia comprobación activa (se repite en cada lanzamiento).
- Clave de la cuenta guardada con **DPAPI** de la cuenta de Monitor (nunca en `.env`).
- **Comprobación activa antes de cada lanzamiento**: Monitor ejecuta la sonda como la cuenta contra la instalación real
  (~1 s) y solo lanza Forms si la base, el `.env`, los respaldos y la clave del diario dan `PermissionError`. Así
  `puede_lanzar()` no depende de una bandera, sino de una prueba vigente (criterio §5 de la especificación).
- Prueba de regresión que falla si alguien amplía los permisos de la cuenta.

### F4 — permanente (requiere otra aprobación)
Cuenta `cauce_conector` permanente (no administradora, oculta, sin red/RDP), ACL sin herencia sobre la instalación que
use Forms y sobre la carpeta de respaldos, y documento de resultados firmado por Diego.

## 5. Qué NO resuelve este plan
Cifrado en reposo de la base; un exploit de elevación en Windows o Chromium; la autorización de FONACOT para el
perfil externo. Datos reales siguen prohibidos hasta tener base legal y consentimiento verificados.

## 6. Resultados de F1 (runner efímero)
Ver la sección «Resultados» del PR y el artefacto `aislamiento-lanzador-py*` del workflow «Aislamiento Forms Windows».
Un veredicto `AISLAMIENTO_DEMOSTRADO_EN_ESTE_ENTORNO` solo dice que la técnica funciona en ese runner.
