# Contrato Monitor ↔ Conector Universal: `cauce.conector.v1` (unificado)

Estado: **PROPUESTO y probado solo con simulador**. Implementado en esta rama: esquemas, validadores, simulador, registro en memoria y 59 pruebas (`monitoreo/conector/`, `tests/test_conector_contrato.py`). **No implementado:** el subcomando del conector real, tabla/migración de Monitor, lanzamiento de procesos con datos de expedientes, aislamiento del sistema operativo. `puede_lanzar()` sigue en `False`.
Fuente del lado del conector: *AUDITORIA_INTEGRACION_MONITOR.md* (Cauce Universal Form Connector v0.1.0, commit `2e00c5c`), leída aquí; **no se verificó contra su código** (no se tocó ese repositorio). Marcas: [V] comprobado aquí con pruebas, [A] afirmado por la auditoría del conector, [P] pendiente.

## 1. Diferencias encontradas y cómo se resolvieron
| Tema | Monitor (`cauce.conector/1`) | Conector (`cauce.conector.v1`, §5) | Decisión unificada |
|---|---|---|---|
| Versión | `cauce.conector/1` | `cauce.conector.v1` | **`cauce.conector.v1`** (la del ejecutor). La anterior se rechaza. |
| Tipo de operación | `accion` PREPARAR / VERIFICAR | `tipo: preparar_formulario` | `tipo` único `preparar_formulario`. VERIFICAR se retira (el conector no la define). |
| Id de solicitud | `id_solicitud` UUID v4 | `solicitud_id` `sol_<hex>` opaco | **`solicitud_id`** `^sol_[0-9a-f]{16,32}$`. Un solo uso en el conector. |
| Idempotencia | `clave_idempotencia` en el mensaje | Solo un solo uso de `solicitud_id` | Dos niveles: `solicitud_id` (viaja, un solo uso) y `clave_idempotencia` (**solo en Monitor, no viaja**: liga expediente+reservación+acción). Misma clave y mismo contenido ⇒ mismo registro; contenido distinto ⇒ conflicto. |
| Fechas | UTC `Z`, ventana ≤120 s | ISO con zona (`-06:00`), ventana de 15 min | ISO 8601 **con zona obligatoria** (se normaliza a UTC). `caduca_en` = último instante para **empezar**; `caduca_en − emitida_en` ≤ **900 s**. |
| Duración del proceso | `tiempo_max_s` ≤120 | `espera_revision_max_s` ≤900 | `opciones.espera_revision_max_s` ≤900; Monitor mata el proceso a los `espera + 180 s` y deja la solicitud `INCIERTA`. |
| Autorización | `revision_humana`, `aprobada_por usr-N`, `consentimiento_vigente` | `ref`, `formulario_id`, `otorgada_por_ref`, `vigente_hasta` | Se usa la del conector **más** `consentimiento_vigente: true` (Monitor lo verifica antes de lanzar; el conector no puede). La revisión humana es física (ventana visible), ya no un booleano. |
| Destino | `portal.base` + `proveedor_id` | `formulario.url` + `perfil` | `formulario.url` (sin query ni fragmento). El contrato admite https; **la política de Monitor en laboratorio exige `127.0.0.1`** (`solo_local`). |
| Integridad de entrada | `huella_campos` (valores) | `perfil_sha256` + `firma_estructura_esperada` `v2:` | Se adoptan **`perfil_sha256` y `firma_estructura_esperada`**; `huella_campos` se retira de la solicitud. |
| Integridad de salida | eco de `huella_campos` | `huella_preparacion` truncada con sal | **`huella_preparacion`** = sha256(`solicitud_id`\|firma\|valores canónicos), salada con el id. Monitor la **recalcula** con lo que envió y exige que coincida (prefijo ≥8 hex). Los valores no viajan. Aviso: con valores de baja entropía el prefijo corto no basta; Monitor no debe publicarla. |
| Firma de estructura | no existía | `v2:<16hex>` (etiqueta, `required`, `pattern`, `maxlength`, `autocomplete`, opciones) | Solo se acepta `v2:`; la v1 (`clave:tipo`) se rechaza (H4). |
| Estados de respuesta | PREPARADO / RECHAZADO / ERROR / CADUCADO / INCIERTO | 9 estados en minúsculas | **Los 9 del conector** en el cable. `INCIERTA` **no** es un estado del conector: lo deriva Monitor. |
| Códigos | enum de 10 códigos | `errores[].codigo` + salida 0–7 | Salida de proceso = tabla del conector (0 ok, 1 error, 2 incidencias, 3 no autorizado, 4 intervención, 5 estructura, 6 solicitud inválida, 7 revisión caducada); Monitor **exige** que coincida con el estado. `errores[].codigo` es un enum cerrado (14 valores). |
| Texto libre | prohibido | `errores[].detalle` «fijo» | `detalle` opcional ≤160 caracteres, **filtrado** (datos personales y valores enviados); Monitor guarda solo el `codigo`. Residuo: no hay catálogo cerrado de textos hasta que el conector lo publique [P]. |
| Envío | `envio_permitido: false` | `envio.realizado_por_conector: false` | Se usa el del conector (const `false`); sin acción de envío en el contrato. |

## 2. Estados, errores y traducción a Monitor
| Estado del conector (salida) | Estado de Monitor | Qué debe cumplir la respuesta (lo comprueba `validar_respuesta`) |
|---|---|---|
| `preparado_revisado_sin_envio` (0) | `PREPARADO` | todos los campos pedidos `ok/ok_normalizado`, firma = esperada, huella recalculada, revisión `cerrada_sin_envio`, 0 errores |
| `enviado_por_persona_observado` (0) | `ENVIADA_POR_PERSONA` | igual, con `envio_humano_observado=true`. Es **indicio**, no prueba de envío (H1): Monitor sigue exigiendo su propia confirmación (folio) |
| `preparado_con_incidencias` (2) | `CON_INCIDENCIAS` | al menos un campo `diferente/invalido`, confirmación que no coincide o error |
| `rechazado_no_autorizado` (3), `rechazado_solicitud_invalida` (6) | `RECHAZADA` | **0 campos y sin huella** (0 teclas) |
| `detenido_intervencion_humana` (4), `detenido_estructura_cambiada` (5) | `DETENIDA` | 0 campos y sin huella; en estructura, `estructura_coincide=false` |
| `revision_caducada` (7) | `CADUCADA` | se preparó y verificó pero nadie revisó a tiempo |
| `error_interno` (1) | `ERROR` | puede traer `NAVEGADOR_NO_DISPONIBLE` o `PAGINA_NO_DISPONIBLE` |
| (sin respuesta válida, proceso muerto, fuera de plazo, respuesta inválida o con datos personales) | **`INCIERTA`** | solo una persona la concilia; nunca éxito supuesto ni reintento automático |

Idempotencia y caducidad (probadas en el registro en memoria): un `solicitud_id` no se acepta dos veces; solicitud caducada no se lanza; respuesta fuera del plazo del proceso ⇒ `INCIERTA`; los estados terminales no se reabren.

## 3. Los cuatro fallos del conector y cómo los cubre el contrato
La auditoría del conector [A] encontró H1–H4 (y H5–H12 menores). El contrato los **exige**, pero **el conector v0.1.0 no los resuelve todavía**: esa es su parte del trabajo (6–7 días estimados por ellos [A]).
- **H1** envío sin ligar a la revisión, repetible: el contrato no tiene envío; el conector sólo **observa** el envío humano. Monitor rechaza `realizado_por_conector=true` y `enviado_*` sin envío observado [V].
- **H2** el CLI aplica correspondencias «revisar» sin revisión: el contrato exige `perfil` + `perfil_sha256`; Monitor no envía campos fuera del perfil, y rechaza respuestas con campos no pedidos [V]. **El conector debe usar solo el perfil aprobado** [P, de ellos].
- **H3** el perfil no se detiene si cambió la estructura o el sitio: `firma_estructura_esperada` y `perfil_sha256` obligatorios; `detenido_estructura_cambiada` con 0 teclas; un estado exitoso con firma distinta se rechaza [V]. **El conector debe comparar la huella del perfil** [P].
- **H4** firma que no ve cambios de etiqueta: solo se acepta `v2:` [V]. **El conector debe implementar la firma v2** [P].
- Además: H7 (salidas con valores) ⇒ filtro de datos personales y huella recalculable; H8 (campos tardíos se rellenan) ⇒ campos de la respuesta ⊆ campos solicitados [V]; H9 ⇒ `NAVEGADOR_NO_DISPONIBLE` ≠ `PAGINA_NO_DISPONIBLE`.
Mutaciones comprobadas: quitar el `const false` del envío, el filtro de datos personales, el chequeo del código de salida o el plazo del proceso hace fallar al menos una prueba.

## 4. Seguridad (sin cambios)
`aislamiento.py::puede_lanzar()` sigue devolviendo siempre `False` y hay una prueba. Un Job Object limita recursos y mata el árbol, **pero no impide leer archivos**: un proceso del mismo usuario abre `data\*.db` y `.env`. Desbloqueo solo con: cuenta local dedicada sin privilegios, ACL NTFS que niegue a esa cuenta la base, `.env`, respaldos y clave del diario, Job Object **además**, y una prueba automática en Windows donde el hijo obtiene `PermissionError` al abrir la base y puede leer lo que sí necesita. Monitor solo pasa por stdin los campos del perfil, por stdin y no por archivo; el simulador no abre archivos, red ni bases (una prueba lo vigila).

## 5. Lo que existe y lo que falta
Existe (Monitor, probado con simulador): esquemas, validadores, huella, caducidad, estados, registro en memoria. **Falta en el conector** [A]: subcomando `preparar`, firma v2, perfil fijado por SHA-256, registro de formularios autorizados y de `solicitud_id` usados, ventana visible con observación del envío humano, tiempo límite, salida sin valores. **Falta en Monitor:** tabla `solicitudes_conector` + migración (sobre base de pruebas), eventos `CONECTOR_*` en el diario, lanzador, aislamiento, rol «conector»; y decidir quién aprueba perfiles y dónde se guarda su huella (propuesto: Monitor).

## 6. Resultado de CI del PR #3 (cabeza `5b3c6f6`, antes de este cambio)
- **Linux 3.13** falló en la ejecución *push* y en la del *pull_request*, mismo paso («Arranque real del servidor», `e2e_panel.js`: el `#drawer` interceptaba el puntero), y **pasó en la repetición** (ejecución 111, intento 2, éxito). Mi diff no toca el panel; en local pasó 4 de 4. Intermitente, causa **no demostrada**; no se tocó código ni tiempos.
- **Windows 3.13 Reservaciones** falló solo en la ejecución *push* (en la del *pull_request* pasó). La comprobación «confirmaciones inciertas» exige `cortadas > 0`; el laboratorio inyecta el corte `cortar_tras_confirmar` en una ventana de `max(5, 25·noche_s/300)` segundos (`scripts/laboratorio_dias.py:421-424`) y esperaba 5 cortes; el portal registró **0**: ninguna confirmación llegó a la ventana de inyección. La comprobación se mantiene como está (que no se produzca ningún corte **debe** seguir siendo un fallo; no es una prueba vacía). Por qué ninguna confirmación llegó a tiempo en ese corredor no está demostrado [P]; hace falta el artefacto de ese trabajo (`reservaciones-windows-py3.13`, ID 14636640599; el proxy del entorno bloquea el host de descarga) para ver cuántas confirmaciones hubo antes de t=20.
- **No hay acción de CI en este cambio**; el diff nuevo (`monitoreo/conector/`, un test y este documento) sigue sin tocar esos componentes.
