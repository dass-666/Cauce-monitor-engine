# Cauce Monitor 1.0 · evaluación objetiva y versión candidata (2026-11-25)

Base: `claude/lab-puerto-estable` c532b42 (PR #12, #14, #15, #16) + rama `claude/v1-candidata` (este documento).
Forms: CAUCE-UNIVERSAL-CONEXION ff117da + rama `claude/persona-termina-en-la-ventana`.
Entorno: `C:\cauce-coincidencia` en la MSI (Windows 11). **No se tocó** `C:\cauce-msi`, sus datos, respaldos ni
`D:\cauce-forms-interfaz`. Nada fusionado ni desplegado.

Marcas: **[W]** verificado hoy en Windows (MSI) · **[V]** verificado en otro entorno (Linux/runner) · **[C]** existe en el
código, sin verificar hoy · **[F]** falta.

## 1. Lo que ya funciona en Windows [W]

| Función | Evidencia de hoy |
|---|---|
| Abrir desde el escritorio (acceso directo, sin comandos); un segundo doble clic no arranca otra copia | prueba con el `.lnk` |
| Expedientes ficticios con consentimiento, sucursales, trámite y fechas aceptables; plan del día | `recorrido_v1` A |
| «Páginas observadas»: probar una página por URL + perfil, guardar y observar; sin duplicar la fuente (409) | `recorrido_v1` B, 2 pasadas con clics |
| Caso sin disponibilidad (todo «en espera») | `recorrido_v1` C, clics |
| Detección de un día nuevo y coincidencia con fecha, sucursal y expediente; el resto sigue en espera | `recorrido_v1` C (≈ segundos), clics |
| Operación preparada desde el plan, ligada a su coincidencia; preparar dos veces no crea otra reservación | `recorrido_v1` D |
| Forms real en ventana visible: rellena y verifica 8 campos (valor real leído del DOM: `value` de listas y fecha) y 2 confirmaciones; nunca envía; un segundo clic no lanza otro Forms | `recorrido_v1` E, pruebas Monitor con Forms real 24/24 |
| **Corregido hoy:** la persona termina en la MISMA ventana de Forms (campos manuales, día, folio) sin volver a teclear los 8 datos | `recorrido_v1` F; prueba nueva de Forms |
| Folio: falso rechazado, correcto verificado con el portal; repetirlo no duplica; reservación, expediente y plan actualizados | `recorrido_v1` F, clics |
| Historial del expediente (coincidencia → Forms → lista → confirmada) | `recorrido_v1` G, pantalla «Historial» |
| Reinicio del motor sin pérdida (reservación, expediente, plan y página observada siguen igual) | `recorrido_v1` H |
| Recorrido previo observador web + Ventanilla con navegador automatizado | 46/46 |

Evidencia: `resultados/recorrido_v1/recorrido_v1.json`, `resultados/demo_base_web/`.
Control negativo: el mismo recorrido con el Forms anterior da 18/23 (la ventana se cierra al primer «Continuar» y no
hay folio): `resultados/recorrido_v1_control_sin_correccion/`.

Suites en la MSI: Monitor 794 pasan / 34 omitidas / 2 fallaban y se corrigieron (un `.bat` nuevo con un carácter
no ASCII y una prueba del observador que dependía de la zona horaria del equipo: 21600 s = UTC-6; defecto de la
prueba, no del producto); excluida `test_autodiagnostico` (falta psycopg). Forms 215 pasan / 7 omitidas.

## 2. Implementado, pero falta validar

| Función | Estado |
|---|---|
| Forms como cuenta restringida de Windows (aislamiento) | [V] F2 en `C:\cauce-aislamiento-lab` (28/0/3); **no activo** en la candidata: crear la cuenta exige administrador y tu aprobación |
| Recuperación si Monitor muere con Forms abierto (operación INTERRUMPIDA, reintento con motivo) | [V] Linux y runner Windows; no repetido hoy en la MSI |
| Observar sitios externos (https + host permitido por quien instala + autorización escrita vigente; CAPTCHA o inicio de sesión = se detiene) | [C] nunca probado contra un sitio real |
| Perfil de formulario EXTERNO con tablas de equivalencias | [C] solo simulado en 127.0.0.x |
| Importar lista, captura, recuperación segura, notificaciones (Telegram), PostgreSQL, respaldo externo | [C]/[V] en rondas anteriores; fuera del alcance de hoy |

## 3. Solo funciona con datos ficticios o el portal propio de laboratorio

- **Autorrellenar con Forms**: `configuracion()` exige `MONITOREO_AUTORRELLENO_LAB=1`, portal en 127.0.0.1 con token de
  control y no producción. `conector.aislamiento.puede_lanzar()` sigue en `False` (decisión de seguridad, no un defecto).
- **Verificación del folio** (`PORTAL_API_FOLIO`): solo el portal de laboratorio tiene API; con otro portal el folio
  se rechaza con `SIN_VERIFICACION_AUTORIZADA`.
- **Calendario lab**: simula al proveedor (abrir/agotar días). Es una herramienta de prueba, no una función del producto.

## 4. Lo que verdaderamente falta para operar (1.0)

1. **[F] Cerrar la operación con un portal real sin API de folios.** Hoy no hay forma de registrar el resultado en
   el flujo de coincidencias. Lo existente en el piloto asistido (folio DECLARADO por la persona) no está conectado a
   este flujo. Requiere tu decisión: ¿se acepta un folio DECLARADO (marcado como tal) para cerrar la operación?
2. **[F] Forms fuera del laboratorio.** Configurar la cuenta restringida y la regla de red en la instalación
   operativa (F4) y, con la prueba negativa en verde, decidir si `puede_lanzar()` se habilita. Requiere administrador.
3. **[F] Perfil aprobado del formulario real y su fuente de observación**: requiere autorización escrita del
   portal, su host y una revisión del formulario (analizador de Forms → aprobación).
4. **[F] Consolidar el código**: `master` sigue en b7a8c51 (v1.0 original); la candidata está 205 commits por
   delante en ramas. Hacen falta PR y tu autorización para fusionar.
5. **[F] Actualizar `C:\cauce-msi`** (fda7600, 70 commits detrás de c532b42) a la versión aprobada, con respaldo
   previo. Requiere tu autorización.

## 5. Bloqueos externos

- Autorización escrita del portal real (observación y formulario) y su lista de hosts.
- Credenciales o vía oficial para comprobar folios, si existe.
- Acciones de administrador en Windows (cuenta del conector, cortafuegos), con UAC: solo tú.

## 6. Qué cambió hoy (código)

- Forms `integracion._esperar_revision`: un envío de la persona ya no cierra la ventana; la revisión termina cuando la
  persona la cierra o vence el plazo, y el resultado sigue siendo `enviado_por_persona_observado` (solo un indicio).
  Prueba nueva: `test_tras_el_envio_humano_la_ventana_sigue_abierta_para_la_persona`.
- Monitor: textos de Ventanilla para el nuevo flujo; lanzador `cauce_demo.bat` + `scripts/demo_integral.py`
  (expedientes ficticios, plantilla de página, guía) y recorrido `scripts/recorrido_v1.py` con
  `tests/herramientas/forms_persona.py` (persona simulada DENTRO de la ventana de Forms real).
