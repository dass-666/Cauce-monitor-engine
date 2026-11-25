# Preparación para uso real · candidata 1.0 (2026-11-25)

Nada de esto se ha aplicado: son los pasos y bloqueos para decidir. No se tocó `C:\cauce-msi`.

## 1. Versiones compatibles (dependencia Monitor ↔ Forms)

| Monitor (Cauce-monitor-engine) | Forms (CAUCE-UNIVERSAL-CONEXION) | Notas |
|---|---|---|
| `claude/v1-candidata` (sobre c532b42) | `claude/persona-termina-en-la-ventana` (d3b230a, sobre ff117da) | Combinación probada hoy en la MSI: `recorrido_v1` 23/23, `recorrido_estabilidad` 19/19 |
| `claude/v1-candidata` | ff117da (sin el PR de Forms) | Funciona, pero la persona NO puede terminar en la ventana de Forms (se cierra al primer «Continuar»): control negativo 18/23 |
| c532b42 | `claude/persona-termina-en-la-ventana` | Compatible: el contrato `cauce.conector.v1` no cambia (mismos estados y salida) |

- Contrato: `cauce.conector.v1`, sin cambios. Monitor ya trataba `ENVIADA_POR_PERSONA` como «lista para el folio».
- Forms se instala aparte con su `.venv` y su `.pw-browsers` (Chromium 1255); Monitor lo encuentra por
  `MONITOREO_FORMS_DIR`. El perfil del formulario se aprueba en Forms y Monitor fija su huella SHA-256.
- Orden de integración recomendado: primero el PR de Forms, después el de Monitor (el recorrido de Monitor
  `scripts/recorrido_v1.py` exige la versión nueva de Forms).

## 2. Habilitar Forms bajo una cuenta restringida (pasos; requieren administrador y tu aprobación)

Base: `docs/PLAN_AISLAMIENTO_FORMS_WINDOWS.md` (F4) y `docs/AISLAMIENTO_VENTANILLA_LAB.md`. Ya validado en F2 en una
carpeta de laboratorio (`C:\cauce-aislamiento-lab`, 28 pasan / 0 fallan / 3 omitidas).

1. **Python para todos los usuarios** o portable dentro de la carpeta del conector (H2: el de Microsoft Store no lo
   ejecuta otra cuenta). Reinstalar el `.venv` de Forms con ese Python y dejar `.pw-browsers` dentro de Forms.
2. **Carpeta del conector** (código de Forms + carpeta de trabajo) **sin herencia de `C:\`** (H1): lectura/ejecución para
   la cuenta en el código, escritura solo en `trabajo\`.
3. **Cuenta local `cauce_conector` permanente**: no administradora, solo grupo Usuarios, oculta en la pantalla de inicio,
   sin acceso por red ni Escritorio remoto (H3: no denegar el inicio de sesión local). Clave aleatoria larga, guardada
   cifrada con DPAPI de la cuenta de Monitor (`scripts/guardar_clave_conector.py` → `MONITOREO_FORMS_CLAVE_DPAPI`),
   nunca en `.env`.
4. **ACL de Monitor**: la base, `data\`, respaldos, claves, diario y `.env` de la instalación que use Forms, y los
   respaldos externos, deben negar la lectura a esa cuenta (lo comprueba la sonda antes de CADA lanzamiento).
5. **Red**: regla de Windows Firewall por cuenta (`-LocalUser`) que bloquee todo salvo los destinos permitidos.
   Hoy solo 127.0.0.1 (laboratorio); un portal real exige su lista de IP autorizadas (H12, depende del portal).
6. **Variables de Monitor**: `MONITOREO_AUTORRELLENO_AISLAMIENTO=cuenta_restringida`, `MONITOREO_FORMS_CUENTA`,
   `MONITOREO_FORMS_TRABAJO`, `MONITOREO_FORMS_CLAVE_DPAPI`, `MONITOREO_AISLAMIENTO_RED_DESTINO` (host:puerto que Monitor
   alcanza y la cuenta no), `MONITOREO_AISLAMIENTO_API` y, si hay más carpetas con datos, `MONITOREO_AISLAMIENTO_RUTAS_EXTRA`.
7. **Prueba negativa en la instalación**: `scripts/validar_aislamiento_msi.ps1` adaptado a la carpeta real; con permisos
   abiertos debe negar el lanzamiento (409 `AISLAMIENTO_NO_VERIFICADO`), y con ellos cerrados permitirlo.
8. **Cambio de código pendiente** (ver §3, nuestro código): hoy el autorrellenado solo existe con
   `MONITOREO_AUTORRELLENO_LAB=1` y el portal de laboratorio, y `conector.aislamiento.puede_lanzar()` devuelve `False`.

## 3. Bloqueos, separados por origen

### Dependen del portal externo (no son nuestros)
- Autorización escrita del portal para observar su calendario y para preparar su formulario (referencia, huella del
  documento, vigencia) y su lista de hosts/IP.
- Vía oficial para comprobar folios. Sin ella, la candidata registra el folio como **DECLARADO, PENDIENTE DE
  VERIFICACIÓN** (nunca confirmado).
- CAPTCHA, inicio de sesión o límites de peticiones del portal: Cauce se detiene; no se rodean.
- Compatibilidad real del formulario y del calendario del portal: sin probar (no hay autorización).

### Dependen de nuestro código o de nuestra instalación
- **Autorrellenado fuera del laboratorio**: `adaptador.configuracion()` exige laboratorio y portal 127.0.0.1;
  `puede_lanzar()` en `False`. Falta el camino operativo condicionado al aislamiento verificado (diseño + aprobación).
- **Adaptador de verificación del portal real** (`ADAPTADOR_POR_CONTRATO`): solo existe el del laboratorio; uno real
  depende de la vía oficial anterior.
- **Perfil aprobado del formulario real y fuente de observación**: se generan con el analizador de Forms y
  «Páginas observadas» cuando haya autorización.
- **Cuenta restringida en la instalación operativa** (§2): pasos de administrador, con UAC.
- **Integración del código**: `master` sigue en b7a8c51; la candidata va por ramas y PR. Actualizar `C:\cauce-msi`
  (fda7600) solo con tu autorización y respaldo previo.
