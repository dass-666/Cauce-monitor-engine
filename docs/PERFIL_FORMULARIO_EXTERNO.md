# Perfil de formulario EXTERNO autorizado (laboratorio)

El botón «Autorrellenar formulario» de Ventanilla (docs/AUTORRELLENO_FORMS_LAB.md) puede preparar, además del
`/cita` del portal propio, el formulario de **un sitio externo sobre el que hay autorización escrita**. Es el
mismo flujo, con las mismas puertas de Monitor (consentimiento vigente con huella, operador y expediente
correctos, recuperación operable). Forms rellena y verifica, **no envía**: la persona revisa y decide.

## Límites (no cambian con este perfil)

- Solo laboratorio: `MONITOREO_AUTORRELLENO_LAB=1` y nunca en producción. El router sigue en `SOLO_LABORATORIO`.
- **Solo datos ficticios.** Forms corre con el mismo usuario de Windows que Monitor y podría leer su base: con
  datos reales falta el aislamiento (PR #13, `docs/PLAN_AISLAMIENTO_FORMS_WINDOWS.md`). `puede_lanzar()` sigue en False.
- No sirve para FONACOT ni para ningún sitio sin autorización del propietario: el consentimiento de la persona no
  sustituye la autorización del sitio. No se eluden CAPTCHA, límites ni otras restricciones; si el sitio pide
  intervención humana, Forms se detiene.
- El contrato `cauce.conector.v1` no cambia: la URL debe ser `https://dominio/ruta` **sin `?` ni `#`**
  (ruta de hasta 100 caracteres `A-Za-z0-9._~/-`). Una URL con consulta no se puede usar sin cambiar el contrato.

## Dos llaves

1. **El perfil** (archivo JSON revisado por una persona, plantilla en
   `monitoreo/autorrelleno/perfiles_externos/ejemplo_sitio_externo.json`): `externo: true`, la URL exacta,
   la autorización escrita (`ref` `autext_…`, quién la otorgó, SHA-256 del documento firmado y `vigente_hasta`),
   la correspondencia `campo de Monitor -> clave de Forms` y las tablas de equivalencias.
2. **El host escrito por quien abre el laboratorio** (`--host-autorizado`, variable
   `MONITOREO_AUTORRELLENO_HOSTS_EXTERNOS`). Nunca se toma del perfil. Si no coincide: `HOST_EXTERNO_NO_AUTORIZADO`.

Además: autorización vencida → `AUTORIZACION_EXTERNA_CADUCADA`; la solicitud nunca dura más allá de esa fecha.
Forms recibe un `allowed_hosts.json` propio de la carpeta del laboratorio con **solo** ese host (no se toca la
configuración de Forms) y la aprobación se hace con `--authorized`.

## Campos y equivalencias

- Solo campos AUTOMÁTICOS del contrato de Monitor del modo del perfil (POR_DIA: `operacion`, `tramite`,
  `identificador`, `fecha_nacimiento`, `telefono`, `telefono_confirmacion`, `correo`, `correo_confirmacion`,
  `folio_anterior`). El nombre, la CURP, la clave de identidad y el aviso de privacidad nunca viajan.
  Declara solo lo que el sitio pide; lo no declarado no sale de Monitor.
- `equivalencias`: `{campo: {código de Monitor: valor de opción del sitio}}`. Monitor traduce antes de enviar la
  solicitud. **Un código sin fila no viaja**: queda para la persona y Ventanilla lo indica («el código no está en
  la tabla aprobada; elígelo tú»). Tablas 1 código → 1 opción, sin reglas condicionales.

## Pasos para un primer intento (datos ficticios)

1. Analiza el formulario en modo de solo lectura con Forms (en su carpeta, con el host en un
   `allowed_hosts.json` temporal): `python -m cauce_form_connector analizar-formulario --url URL --authorized`.
   Da `perfil_borrador.json` y las claves de campo (`formulario::campo`) y opciones reales.
2. Copia la plantilla, rellena URL, campos, equivalencias y autorización. Revísalo con quien autorizó.
3. Abre el laboratorio (crea su propia carpeta `resultados\autorrelleno_externo_lab`, no toca otras):
   `autorrelleno_externo_laboratorio.bat RUTA\perfil.json HOST [CARPETA_FORMS]`.
   Monitor valida el perfil, Forms lo aprueba (abre la URL una vez, sin teclear) y fija huella y firma.
4. En Ventanilla, con una persona FICTICIA con consentimiento: «Autorrellenar formulario». Revisa la ventana,
   no envíes, ciérrala. Ventanilla debe decir PREPARADO (o CADUCADA si nadie la cierra).

## Pruebas

`tests/test_autorrelleno_externo.py`: el «sitio externo» es un servidor de la prueba en `127.0.0.2` (para
Monitor y Forms no es 127.0.0.1). Con conector espía: traducción, código sin fila, sin host, autorización
vencida, producción, perfiles inválidos (http, 127.0.0.1, credenciales en la URL, `?`, autorización incompleta,
equivalencias sobre campos no declarados) y plantilla válida. Con `FORMS_REPO`: Forms real aprueba el perfil
externo y rellena 5 campos + confirmación, sin envío.

No probado: un sitio externo real (no hay URL ni autorización todavía), redirecciones de inicio de sesión,
páginas de varios pasos.
