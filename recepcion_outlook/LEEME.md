# Recepción de Outlook.com para Bandeja Cauce S8.1: auditoría y prueba de un buzón

Fecha: 13-nov-2026. Alcance: solo la recepción de correo. No se tocó la Bandeja del puerto 5810
ni el servidor de DigitalOcean (5821), ni Caddy ni Docker. No se usó la lista de direcciones subida al proyecto.

## 1. Lo que se verificó

| Hecho | Evidencia |
|---|---|
| `sync_correo.py` y la configuración de buzones de S8.1 **no están** en ningún repositorio accesible (`Cauce-monitor-engine`, todas sus ramas) ni en las carpetas compartidas del proyecto. | `git grep` en todas las ramas remotas (sin resultados para `sync_correo`, `imaplib`, `imap`, `xoauth`, `msal`, `outlook`, `5821`) y búsqueda de código en GitHub. |
| Outlook.com rechaza usuario y contraseña por IMAP/POP/SMTP (autenticación básica); exige autenticación moderna (OAuth2). | Aviso oficial de Microsoft: https://support.microsoft.com/office/f4202ebf-89c6-4a8a-bec3-3d60cf7deaef |
| Las aplicaciones nuevas deben registrarse dentro de un directorio de Microsoft Entra (Azure). | https://learn.microsoft.com/en-us/entra/identity-platform/reference-breaking-changes |
| La herramienta de esta carpeta llega al servidor real de inicio de sesión de Microsoft y clasifica bien un registro de aplicación inválido (`APP_MAL_CONFIGURADA`, AADSTS700038). | Ejecución real con un Id. de aplicación ficticio. |
| 16 pruebas automatizadas pasan (sin red, con respuestas de Graph simuladas). | `python -m pytest -q tests` → `16 passed` |

## 2. Lo que NO se ha verificado (inferencias)

- **Que S8.1 funcione hoy con Outlook.com.** Sin ver `sync_correo.py` no se puede afirmar. Si usa
  `imaplib.login(usuario, contraseña)`, **no puede funcionar** con Outlook.com (hecho de Microsoft, arriba).
- **Que una cuenta personal de Outlook.com acepte esta aplicación sin aviso de "no verificada".**
  Lo esperable es que el dueño vea un aviso y pueda aceptar; solo la prueba real lo confirma.
- **Lectura real de un buzón por Graph.** Requiere el registro de Azure y el consentimiento del dueño (paso 3).

## 3. Qué tiene que hacer Diego (bloqueos reales)

**A. Enviar `sync_correo.py` y el archivo de configuración de buzones** (o decir la ruta en el servidor).
Sin eso no hay auditoría del código existente, solo del requisito de Microsoft.

**B. Registrar la aplicación en Azure** (gratis, unos 10 min):
1. https://entra.microsoft.com → *Registros de aplicaciones* → *Nuevo registro*.
2. Nombre: `Bandeja Cauce - recepción`. Tipos de cuenta: **Solo cuentas personales de Microsoft**.
   Si las direcciones de la oficina son de Microsoft 365 empresarial (no `@outlook.com`), avise antes:
   el camino es otro (ver sección 5).
3. *Autenticación* → *Configuración avanzada* → **Permitir flujos de cliente público: Sí**
   (sin esto aparece AADSTS7000218).
4. *Permisos de API* → Microsoft Graph → Delegados → **Mail.Read** (nada más: ni Mail.Send ni Mail.ReadWrite).
5. No cree "secreto de cliente": no hace falta y sería una credencial más que proteger.
6. Copie el **Id. de aplicación (cliente)**. No es secreto.

**C. Prueba real con UN buzón cuyo dueño la autorice** (en el MSI o cualquier PC con Python 3.11+):

```
cd recepcion_outlook
python -m pip install -r requirements.txt
set CAUCE_MS_CLIENT_ID=<Id. de aplicación>
python prueba_buzon.py --direccion <buzón-autorizado>@outlook.com
```

El dueño del buzón abre https://microsoft.com/devicelogin, escribe el código, inicia sesión **él mismo**
y acepta "Leer su correo". El programa muestra los últimos 5 asuntos, genera un asunto único
`CAUCE-PRUEBA-XXXXXXXX` y espera a que alguien envíe un correo con ese asunto.

**Criterio de éxito (verificable):** el programa termina con `[OK] recibir_correo_de_prueba` y
`estado_prueba/<buzón>/evidencia.json` registra la demora en segundos y el `internetMessageId`.
Comparta ese archivo (no contiene tokens; sí contiene la dirección y la hora).
**Si falla**, el estado dice por qué: `NECESITA_CONSENTIMIENTO`, `CUENTA_DISTINTA`, `PERMISO_DENEGADO`,
`LIMITADO_POR_MICROSOFT`, `ERROR_RED` o `APP_MAL_CONFIGURADA`.

`estado_prueba/` guarda el token de acceso del buzón: no lo suba a Git (ya está en `.gitignore`) ni lo comparta.

## 4. Qué hace y qué no hace la herramienta

- Permiso mínimo `Mail.Read` delegado: no puede enviar, borrar ni mover correo.
- Nunca pide, recibe ni guarda contraseñas. No usa el Excel.
- Comprueba que la cuenta que inició sesión es la dirección esperada (evita vincular un buzón equivocado a un expediente).
- Sincronización incremental (*delta*) de la Bandeja de entrada: la primera pasada trae la bandeja y un
  marcador; las siguientes, solo lo nuevo. Es lo que debería reemplazar al sondeo IMAP en `sync_correo.py`.
- Ante un 429 de Microsoft espera lo que indica `Retry-After`; ante un fallo no transitorio se detiene.
  No rota cuentas, redes ni aplicaciones.
- Limitación conocida: solo lee la Bandeja de entrada (no "Correo no deseado").

## 5. Riesgos que conviene decidir antes de escalar

1. **Cada buzón personal exige un inicio de sesión de su dueño.** Con cuentas `@outlook.com` no existe
   "consentimiento de administrador" para todas a la vez. Para decenas de buzones es tolerable; para cientos,
   no. Si la oficina controla los buzones, lo sostenible es **Microsoft 365 empresarial** (dominio propio):
   un administrador autoriza una vez con permiso de aplicación limitado a un grupo de buzones.
2. **Caducidad del acceso.** El acceso se mantiene mientras se use (renovación automática), pero se pierde
   si el dueño cambia la contraseña, revoca el permiso o el buzón queda inactivo. Por eso S8.1 debe mostrar
   `NECESITA_CONSENTIMIENTO` por buzón (requisito 6) y no fallar en silencio.
3. **Tokens en el servidor.** Al integrarlo en S8.1, el caché de tokens debe guardarse cifrado
   (no en texto plano como en esta prueba local) y fuera de las copias de seguridad sin cifrar.
4. **Buzones creados en serie.** Microsoft suspende cuentas que parecen creadas automáticamente; si eso
   ocurre, ninguna integración lo evita. Esta herramienta no está pensada ni sirve para cargar listas masivas.

## 6. Siguiente paso tras una prueba exitosa (no iniciado)

Integrar `receptor_graph.py` en `sync_correo.py`: guardar por buzón el `delta_link` y el estado, deduplicar por
`internetMessageId`, y mostrar el estado de cada buzón en la bandeja central. Requiere ver primero el código actual (bloqueo A).
