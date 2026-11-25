# Laboratorio aislado: Ventanilla → Forms como OTRA cuenta (PR #13 conectado al recorrido actual)

Estado: **solo laboratorio, datos ficticios.** `conector.aislamiento.puede_lanzar()` sigue en `False`. No hay despliegue
operativo, no se tocó ninguna instalación (MSI incluida) y no se usa con datos reales.
Marcas: **[V]** verificado con evidencia · **[I]** inferido · **[P]** pendiente.

## 1. Qué faltaba en el PR #13 y qué se hizo
El PR #13 traía el lanzador como otra cuenta de Windows (`lanzador_restringido.py`) y la prueba en runner efímero
(A1–A11), pero **no estaba conectado a Ventanilla** y no tenía la «comprobación activa» de la fase F3. Sobre la base
estable `claude/lab-puerto-estable` (4131367, con #12, #14 y #15) se fusionó sin conflictos (solo añade archivos) y se
completó:

| Pieza | Dónde |
|---|---|
| Activación solo en laboratorio: `MONITOREO_AUTORRELLENO_AISLAMIENTO=cuenta_restringida` (además de `MONITOREO_AUTORRELLENO_LAB=1`; en producción sigue negado) | `monitoreo/autorrelleno/aislamiento_lab.py` |
| Cuenta y carpeta de trabajo: `MONITOREO_FORMS_CUENTA`, `MONITOREO_FORMS_TRABAJO`. Windows: clave cifrada con DPAPI de la cuenta de Monitor (`MONITOREO_FORMS_CLAVE_DPAPI`, se crea con `scripts/guardar_clave_conector.py`); nunca en `.env` | idem |
| **Comprobación activa antes de CADA lanzamiento**: una sonda corre como la cuenta (mismo lanzador, mismo Python que Forms) e intenta abrir la base (y `-wal/-shm`), listar la carpeta de datos, `respaldos/`, `claves/`, el diario y su clave, el `.env`, la copia del diario, el respaldo externo y las rutas de `MONITOREO_AISLAMIENTO_RUTAS_EXTRA`; escribir en el código de Monitor y de Forms; y escribir en su carpeta de trabajo. Solo «permiso denegado» cuenta como protegido; «no existe», un error raro o no responder = **no se lanza** (`AISLAMIENTO_NO_VERIFICADO`, 409) | `aislamiento_lab.verificar()` |
| Ventanilla usa el lanzador de la cuenta: Windows `CreateProcessWithLogonW` + Job Object (PR #13); Linux `LanzadorCuentaPosix` (solo pruebas, Monitor como root) | `adaptador._correr()` |
| Entorno de Forms sin el perfil de Monitor: `HOME/USERPROFILE/APPDATA/LOCALAPPDATA/TEMP/TMP` → carpeta de trabajo; sin `XAUTHORITY`, `XDG_RUNTIME_DIR`, D-Bus; sin `MONITOREO_*` (ya era así) | `aislamiento_lab.entorno()` |
| Procesos de la cuenta vivos antes de lanzar (restos): se terminan; si no se puede, `CUENTA_OCUPADA`. También al arrancar Monitor | `limpiar_huerfanos()`, `adaptador.al_arrancar()`, `app.py` |
| La corrida y el historial guardan solo `{"modo", "verificado", "rutas_denegadas"}`; la auditoría de un rechazo guarda los códigos (`base:PERMITIDO`…), nunca contenido | `adaptador` |
| `GET /api/autorrelleno/atenciones/{id}` dice `aislamiento: cuenta_restringida | ninguno` | `api/autorrelleno.py` |
| Recorrido completo con `--aislado CUENTA` | `scripts/recorrido_coincidencia_forms.py` |

Sin la variable, el laboratorio funciona exactamente como antes (comprobado: ver §3).

## 2. Cómo se activa (solo laboratorio)
Linux (contenedor/CI de pruebas, como root): `useradd -M -s /usr/sbin/nologin cauce_conector_t`, carpeta de trabajo
propiedad de la cuenta (700), perfil de Forms legible y su estado escribible por la cuenta, datos de Monitor 700.
`scripts/recorrido_coincidencia_forms.py --forms <Forms> --aislado cauce_conector_t` lo prepara solo.

Windows: **no se ha hecho en la MSI** (requiere F2/F4 del plan y tu aprobación). En el runner efímero lo hace el job
«Ventanilla aislada (Windows)» de `.github/workflows/aislamiento.yml`: cuenta local de un solo uso, ACL NTFS sobre
carpetas temporales, clave por DPAPI, y la borra al final.

## 3. Evidencia
### Linux (contenedor de este hilo, Python 3.13, Forms real ff117da, Chromium 1243) — [V]
| Prueba | Resultado |
|---|---|
| `tests/test_aislamiento_ventanilla.py` (espía, «fisgón», control negativo, revocar, cerrar atención, detener Monitor, matar Monitor, árbol/desertor/plazo, resto previo, Python inaccesible, respuesta basura, coincidencia→folio, Forms real ×2) | **19/19** |
| El «fisgón» (corre como la cuenta) intenta leer base, datos, respaldos, claves, diario, `.env`: | todo `DENEGADO`; escribir en código: `DENEGADO`; en trabajo: permitido |
| Control negativo (permisos abiertos): | 409 `AISLAMIENTO_NO_VERIFICADO`, Forms **no** se lanza; al volver a cerrarlos vuelve a funcionar |
| Recorrido calendario `--aislado` (Forms real, observador real) | **28/28** (25 del recorrido + 3 del aislamiento) |
| Recorrido observador web `--aislado` | **30/30** |
| Recorrido observador web + pantalla de Ventanilla `--interfaz --aislado` | **48/48** |
| Recorrido calendario SIN aislamiento (regresión) | **25/25** |
| Chromium de Forms corre como la cuenta | sí (visto con psutil durante la corrida) |
| Procesos de la cuenta al terminar | 0 en todos los casos |

Suite completa: ver el PR (base 4131367 en el mismo contenedor: 800 pasan, 10 omitidas, 1 falla
`test_autodiagnostico::test_database_down_is_reported_without_crashing` porque falta `psycopg` en este entorno; no
tiene relación con este cambio).

### Windows — runner efímero de GitHub — ver el PR
El job «Ventanilla aislada (Windows)» ejecuta los mismos tests con `CreateProcessWithLogonW`, Job Object, DPAPI y ACL
NTFS reales. **Un runner efímero no es la MSI.**

## 4. Hallazgos y límites (no afirmar lo contrario)
- **H8 [V] Linux: matar Monitor de golpe deja nietos vivos de la cuenta** (se midió: 3 procesos antes, 2 tras el
  `kill -9`). El hijo directo muere (PDEATHSIG), los nietos no (no hay Job Object). Los termina el siguiente arranque
  de Monitor o el siguiente lanzamiento (`limpiar_huerfanos`). En Windows lo cubre el Job Object [V en runner].
- **H9 [V] Bajo carga simultánea** (dos recorridos con Chromium a la vez) fallaron una vez una prueba existente con
  Forms real (`test_forms_real_revocacion_a_mitad_termina_chromium`) y una nueva; repetidas solas pasaron. Intermitente
  por carga, no por el aislamiento [I].
- Entre la comprobación y el lanzamiento hay milisegundos: un cambio de permisos justo ahí no se ve hasta el siguiente.
- La comprobación cubre las rutas que Monitor conoce; otra carpeta con datos se añade con `MONITOREO_AISLAMIENTO_RUTAS_EXTRA`.
- Con PostgreSQL la base no es un archivo: la protege su credencial (que está en el `.env`, que sí se comprueba).
- Un administrador/SYSTEM/root lo lee todo; la cuenta del conector nunca debe serlo (en Linux se rechaza root).
- `LanzadorCuentaPosix` exige Monitor como root: **solo** para pruebas, no es una forma de operar.

## 5. Bloqueos para fusionar y para pasar de laboratorio
1. Que la base estable (4131367) quede validada (su CI estaba pendiente al empezar).
2. CI de esta rama en verde, incluido «Ventanilla aislada (Windows)».
3. **F2 en la MSI** (cuenta de un solo uso, carpeta nueva, sin tocar instalaciones): requiere tu aprobación. Responde H1
   (herencia de `C:\`) y H2 (Python «solo para mí» en la MSI: Forms no arrancaría con otra cuenta).
4. F4 (cuenta permanente + ACL en la instalación que use Forms): otra aprobación, después de F2.
5. Datos reales: siguen prohibidos (base legal y consentimiento) y `puede_lanzar()` en False.
