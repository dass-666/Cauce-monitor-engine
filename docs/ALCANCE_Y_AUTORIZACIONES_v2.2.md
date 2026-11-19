# Alcance y autorizaciones · v2.2

Qué hace Cauce **por sí mismo**, qué está **probado solo en laboratorio** y qué **requiere la
autorización de un tercero** (el titular del portal externo, hoy FONACOT) antes de usarse con
personas reales. Escrito para decidir, no para vender: cuando algo no está comprobado, lo digo.

Etiquetas: **MEDIDO** (lo ejecuté y hay evidencia en el repositorio), **SIMULADO** (lo ejecuté contra
el portal de laboratorio o con fallos provocados), **INFERENCIA** (razonamiento, sin prueba),
**PENDIENTE** (falta verificarlo o decidirlo).

## 1. Capacidades internas (no dependen de ningún portal externo)

Funcionan igual con o sin portal externo. Son la base para atender a ~6 trabajadores al día con
orden y con evidencia.

| Capacidad | Estado | Evidencia |
|---|---|---|
| Registro de consentimiento por expediente: finalidad, medio, fecha, responsable, versión del aviso de privacidad, huella SHA-256 del documento firmado y dónde está el original | MEDIDO | `tests/test_consentimiento_v22.py`, `docs/resultados/v2.2/` |
| Sin consentimiento vigente el expediente no llega a LISTO: no entra a la cola, al plan ni a Ventanilla, y no se prepara ni se confirma nada | MEDIDO | idem, puertas en validación, plan, Ventanilla, preparar, enviar datos, calendario, elegir día y confirmar |
| Revocación definitiva e idempotente, con sus efectos según la etapa (plan, asignado, día elegido, confirmación en curso, cita confirmada) | MEDIDO | idem; carrera revocar/confirmar en PostgreSQL |
| Cadena de sellos por consentimiento (detecta cambios, borrados e inserciones fuera del motor) y su revisión en el autodiagnóstico | MEDIDO | `test_cadena_detecta_cambios_borrados_e_intercalados` |
| Posibles duplicados no exactos (acentos, letras cambiadas, mismo teléfono o correo) a revisión; solo un administrador los descarta, con motivo | MEDIDO | `test_posibles_duplicados_*` |
| Trazabilidad de un expediente de punta a punta, sin datos personales | MEDIDO | `GET /api/expedientes/{id}/trazabilidad`; ejemplo en `docs/resultados/v2.2/trazabilidad_ejemplo.json` |
| Recuperación: proceso que muere tras revocar, fallo a mitad de transacción, sesión vencida, reinicio del motor, PostgreSQL detenido | MEDIDO (Linux) | pruebas de recuperación y `scripts/demo_v22.py` |
| Retención: un expediente con constancia de consentimiento se anonimiza, no se borra | MEDIDO | `test_retencion_borrar_conserva_la_constancia_de_consentimiento` |

**Límites de lo interno (INFERENCIA, revisar):**

- El sistema **registra** que la persona consintió; **no prueba** que la firma sea suya. La huella
  SHA-256 solo demuestra que el documento archivado no cambió desde que se registró. La validez
  del consentimiento depende del formato firmado y del aviso de privacidad, que son documentos
  legales fuera del software.
- Nombre, fecha de nacimiento, teléfono y correo se guardan **sin cifrar** en la base de datos.
  Con datos reales, como mínimo: disco cifrado (BitLocker en el equipo Windows), respaldos cifrados
  y acceso al equipo solo para el personal autorizado. El cifrado por columna está **PENDIENTE**: es
  un cambio mayor que no conviene hacer hasta decidir el piloto.
- La revocación detiene las operaciones y conserva la constancia. **No borra en el acto** los datos
  personales: eso lo hace la retención en su plazo. Una solicitud de cancelación o supresión de
  datos (derechos ARCO) exige un procedimiento escrito con plazos legales: **PENDIENTE**.

## 2. Capacidades de laboratorio (probadas solo contra el portal propio)

Todo esto funciona contra `portal_reservas`, un portal **inventado** que corre en 127.0.0.1. Que
funcione ahí **no demuestra** que funcione, ni que esté permitido, contra un portal externo.

| Capacidad | Estado |
|---|---|
| Observador del calendario (Chromium sin ventana) que detecta días que aparecen y desaparecen | SIMULADO |
| Plan del día: asignar una fecha detectada al expediente que dicta la regla (prioridad, orden) | SIMULADO |
| Preparar el formulario, enviar los datos, elegir el día y confirmar, con folio comprobado en el portal | SIMULADO; la confirmación «humana» la hizo un script |
| Confirmaciones inciertas, respuesta perdida, folio duplicado, sesión del portal caducada | SIMULADO |
| Rendimiento y concurrencia (se conservan como controles de calidad, sin metas nuevas) | MEDIDO en laboratorio |

## 3. Lo que requiere autorización del titular del portal externo (FONACOT)

El consentimiento del trabajador autoriza a Cauce a usar **sus datos** para gestionar **su** cita.
**No** autoriza a Cauce a operar sobre el sistema de un tercero. Son dos permisos distintos y hoy
solo se tiene (o se puede tener) el primero.

| Acción | Por qué necesita autorización | Estado |
|---|---|---|
| Observar el calendario del portal real de forma automática | Es acceso automatizado al sistema de un tercero; sus condiciones de uso pueden prohibirlo y puede afectar su servicio | **No implementado contra el portal real. PENDIENTE de autorización escrita.** |
| Llenar o enviar formularios del portal real con un programa | Igual que lo anterior, y además actúa a nombre de la persona | **No implementado. PENDIENTE.** |
| Reservar o confirmar citas en el portal real | Compromete un recurso público escaso; cualquier ventaja automatizada frente a otros trabajadores es un riesgo legal y reputacional | **No implementado. PENDIENTE.** |
| Integrarse por una interfaz oficial (API) si el proveedor la ofreciera | Es la vía correcta si existe: con contrato, credenciales propias y límites acordados | **No existe ninguna integración. PENDIENTE de saber si el proveedor la ofrece.** |

Explícitamente **fuera de alcance**, con o sin autorización de los trabajadores: rotar direcciones
IP, resolver o evitar CAPTCHA, suplantar usuarios, saltarse límites de citas o cualquier control
antifraude. Nada de eso existe en el código y no se va a construir.

### Riesgo comercial que conviene revisar antes de un piloto (INFERENCIA, verificar)

Muchas instituciones públicas advierten que sus trámites son gratuitos y personales, y desalientan
a los intermediarios («gestores»). Si FONACOT tiene una política así, cobrar a un trabajador por
conseguirle una cita podría verse como intermediación indebida, aunque él consienta. **No lo he
verificado.** Antes de cobrar o de anunciar el servicio, conviene revisar los términos del portal y
consultarlo con un abogado o con el propio FONACOT. Un modelo menos expuesto: Cauce organiza,
recuerda y acompaña (expediente completo, documentos listos, la persona reserva en su propia
sesión), sin automatizar nada en el portal.

### Marco de datos personales en México (INFERENCIA, verificar con asesoría legal)

Los datos de trabajadores reales son datos personales en posesión de un particular. Al menos hace
falta: aviso de privacidad (la versión que se le mostró queda registrada en cada consentimiento),
finalidad limitada (hoy solo existe `GESTION_CITA`), medidas de seguridad, plazo de conservación y
un procedimiento para los derechos de acceso, rectificación, cancelación y oposición (ARCO). La ley
federal de la materia se renovó en 2025; confirma con un asesor qué obligaciones y qué autoridad
aplican hoy. El software ayuda a cumplir, **no** sustituye ese análisis.

## 4. Qué haría falta para pasar de laboratorio a personas reales

**Hecho comprobado en el código (MEDIDO):** hoy Cauce **no acepta datos reales por diseño**. La
captura de expedientes solo existe fuera del modo producción, y la validación exige teléfonos
ficticios (`555…`) y correos de prueba (`.test`) mientras `MONITOREO_CAPTURA_SOLO_SINTETICOS` no se
ponga en 0. La v2.2 **no cambia eso**. Quitar esas protecciones es una decisión explícita que
recomiendo no tomar hasta cumplir lo siguiente:

1. Aviso de privacidad y formato de consentimiento revisados por un abogado (PENDIENTE).
2. Decisión sobre el modelo de servicio a la luz de los términos de FONACOT (PENDIENTE).
3. Equipo con disco cifrado y respaldos cifrados; usuarios con contraseña propia (PENDIENTE).
4. Comprobaciones físicas en el equipo Windows (`docs/COMPROBACIONES_WINDOWS_v2.1.md`) (PENDIENTE).
5. Sin autorización escrita del proveedor, el portal externo **no** se observa ni se opera de forma
   automática. Cauce puede usarse en ese escenario como registro, plan y seguimiento internos.
