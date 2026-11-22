# Piloto asistido de Cauce · manual breve

**Qué es.** Una persona agenda cada cita en el portal oficial, con el trabajador presente. Cauce gestiona el
expediente, el consentimiento, la atención de Ventanilla y **guarda la confirmación humana**: quién revisó los
datos, cuándo, con qué huella, y el folio que dio el portal. Cauce **no se conecta al portal oficial** y **no
comprueba** el folio: la cita queda como «declarada por una persona».

**Capacidad prevista:** unas 6 citas al día, una persona operando (se puede añadir otra con su propio usuario).

**Estado de esta versión (sin adornos).**
- Demostrado en pruebas automáticas (SQLite y PostgreSQL) y en un recorrido con navegador en modo piloto: ver
  `tests/test_piloto_asistido.py` y `scripts/piloto_asistido.py --recorrido` (demostración ficticia).
- **No** validado todavía en el Windows físico: eso es la validación del domingo (`docs/VALIDAR_PILOTO_WINDOWS.md`).
- **No** autorizado para datos reales: falta la confirmación de base legal, aviso de privacidad y controles
  (decisión de Diego). Hasta entonces, solo datos ficticios.
- **No** encendido en producción: `MONITOREO_PRODUCCION_FLUJO_ADMIN` sigue apagado hasta la autorización.

**Cómo se arranca el piloto.** Como una instalación normal e independiente: `preparar_piloto.bat <carpeta externa>`
una vez, y después `iniciar.bat`. Pasos exactos en [INICIAR_PILOTO_WINDOWS.md](INICIAR_PILOTO_WINDOWS.md).
`piloto_asistido.bat` es **solo una demostración** con datos ficticios: no se usa para el piloto y los respaldos
no cubren su carpeta.

## 1. Recorrido de una cita (operador)

1. **Ventanilla › Nueva atención.** Busca o crea el expediente de la persona y registra su consentimiento si no
   lo tiene. Sin consentimiento vigente no se puede seguir.
2. **«Agendar a mano en el portal oficial».** Cauce muestra los datos completos a teclear y abre la revisión
   a tu nombre (queda la hora).
3. **Revisa con la persona** cada dato. Si algo está mal, corrígelo ANTES (o revoca la revisión con motivo):
   si los datos cambian después, la revisión deja de valer.
4. Escribe exactamente **REVISÉ Y APRUEBO** y pulsa «Confirmar revisión» (mínimo 5 segundos con la revisión
   abierta).
5. **Agenda en el portal oficial** con la persona presente.
6. Si el portal te dio un folio: teclea el folio **dos veces** (la segunda no admite pegar), la fecha, la hora y
   la sucursal; elige el comprobante (PDF o captura: **no se sube**, solo se guarda su huella SHA-256) y escribe
   dónde lo guardas. Marca «El portal oficial me mostró este folio» y **Registrar la cita**.
7. **Guardar y cerrar la atención.** La atención queda CONFIRMADA con el folio.

Si el portal **no** dio folio: no registres nada. Revoca la revisión con un motivo («sin cupo», «la persona se
retiró») y termina la atención sin cita.

**Lo que nunca cuenta como aprobación:** cerrar el navegador, perder la red, que pase el tiempo o recargar la
página. Al volver, «Retomar» te deja en el mismo paso.

## 2. Checklist diario

**Antes de abrir (administrador, 5 min)**
- [ ] Cauce responde y la cabecera dice el entorno correcto.
- [ ] **Recuperación: Operable** (sin la señal «Bloqueada» en el menú). Si está bloqueada, no se atiende: ver §3.
- [ ] La copia externa del diario está conectada (USB/otra unidad) y el autodiagnóstico no la marca en rojo.
  *Provisional (decisión de Diego, 21-nov):* en la MSI se usa el disco D (SSD interno distinto de C, simulacro 52/52)
  hasta conectar la USB. Protege ante una falla de C, no ante robo, incendio o ransomware: la USB sigue pendiente.
- [ ] Respaldo de ayer hecho y verificado (lo cubre el respaldo externo del hilo de protección de datos).
- [ ] Carpeta de comprobantes disponible (la que se escribe en «Dónde se guarda el comprobante»).

**Durante el día (operador)**
- [ ] Cada cita: revisión confirmada → portal oficial → folio tecleado dos veces → atención cerrada.
- [ ] Ninguna cita se apunta «para después»: si Cauce no deja registrar, anota el folio en papel y avisa.

**Al cerrar (administrador, 10 min)**
- [ ] Mesa de control: citas del día = folios en la carpeta de comprobantes.
- [ ] No quedan atenciones abiertas ni revisiones abiertas sin explicar.
- [ ] Respaldo externo del día y retirada de la USB a un lugar seguro.

## 3. Qué hacer si algo falla

| Síntoma | Qué hacer | Qué NO hacer |
|---|---|---|
| «No se pudo escribir el diario de eventos críticos» (503) al registrar | Anota el folio en papel, avisa al administrador; revisa el autodiagnóstico (disco, copia externa). Al resolverse, registra el folio (la revisión sigue REVISADA). | No vuelvas a agendar en el portal. |
| «Ese folio ya está registrado en otra cita» | Compruébalo en el portal: puede ser un error de tecleo o un duplicado real. Consulta al administrador. | No inventes otro folio. |
| «Los datos del expediente cambiaron» | Revoca la revisión con motivo y ábrela de nuevo con los datos correctos. Si la cita ya se agendó con los datos viejos, avisa al administrador. | No registres un folio que no corresponde a los datos. |
| Tras restaurar un respaldo, falta una cita asistida registrada después | Depende de lo que tuviera el respaldo. **Caso habitual (respaldo del cierre anterior, la revisión se abrió después):** en «Recuperación» aparece «confirmación sin reservación», **sin nombre de expediente** (Cauce sabe cuántas citas faltan, no de quién) y con una sola acción: **Reconocer**. El administrador las reconoce, levanta el bloqueo y cada cita **se vuelve a registrar por el flujo normal** (abrir revisión → frase → folio del comprobante). La carpeta de comprobantes y la hoja del día dicen de quién era cada folio. **Caso raro (el respaldo se hizo con la revisión ya abierta):** aparece «confirmación ausente» y el administrador escribe el folio del comprobante; solo se acepta el que anotó el diario. | No agendes otra vez en el portal: la cita ya existe allí; solo se vuelve a anotar en Cauce. La cita recuperada por conciliación queda sin fecha ni sucursal: complétalas en tu hoja con el comprobante. |
| Se fue la luz o se cerró Cauce justo al pulsar «Registrar la cita» | Al volver, «Recuperación» muestra una confirmación incierta y Cauce queda bloqueado. El operador registra el folio de su comprobante (la revisión sigue REVISADA); después el administrador pulsa «Levantar bloqueo». Si lo concilia el administrador desde «Recuperación», el folio se guarda igual que al registrar (sin espacios, en mayúsculas) y se rechaza si no es válido o ya está en otra cita. | No agendes otra vez en el portal ni pongas un folio distinto. |
| «Operaciones bloqueadas: recuperación pendiente» al abrir | Se restauró un respaldo: el administrador concilia en «Recuperación» antes de agendar nuevas citas. Una cita YA hecha sí se puede registrar. | No agendes a nadie mientras tanto (riesgo de duplicar). |
| La persona cancela una cita ya registrada | El administrador cancela la cita en el portal oficial y, en la ficha del expediente, abre «La persona canceló esta cita»: teclea el folio, el motivo y marca la declaración. La cita queda ANULADA y el expediente CERRADO (CANCELADO). | No reutilices el folio (Cauce lo rechaza). No la anules en Cauce sin cancelarla antes en el portal. |
| Cauce no responde | Atiende en papel (folio, fecha, sucursal, quién) y registra todo al volver. | No reinicies el equipo sin avisar. |

## 4. Criterios para DETENER el piloto

Se detiene (y se vuelve a papel) si ocurre cualquiera de estos:
1. Una cita del portal oficial sin registro en Cauce o un registro en Cauce sin cita real (descuadre al cierre
   que no se explica en el día).
2. Datos personales de un trabajador visibles para quien no corresponde, o una pérdida de datos.
3. Un bloqueo de recuperación que no se puede conciliar en el mismo día.
4. El diario de eventos críticos falla dos veces en un día.
5. Una indicación del proveedor del portal oficial o de un trabajador que retire su consentimiento de forma
   masiva.
6. Diego (responsable) lo decide.

## 5. Límites conocidos de esta versión

- La cita asistida no se comprueba con el proveedor: es una declaración de una persona, firmada por su usuario,
  hora, huella de los datos revisados y, si lo hay, huella del comprobante.
- La anulación de una cita (solo administrador) tampoco se comprueba con el proveedor: es una declaración a su nombre.
- El expediente debe estar tomado por un operador (Ventanilla); un administrador sin operador vinculado puede
  revocar pero no agendar.
- El cálculo de la huella del archivo en el navegador requiere abrir Cauce en `http://127.0.0.1` o `localhost`
  (contexto seguro). Desde otra máquina de la red, pega la huella a mano o deja solo la referencia.
