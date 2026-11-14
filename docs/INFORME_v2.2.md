# Informe · v2.2 Consentimiento verificable y trazabilidad (rama `fase-2.2`)

Rama `fase-2.2`, creada desde `fase-2.1.1`. **No se fusionó** con `master` ni con ninguna otra rama.

En esta ronda:
- no se tocaron las reglas de asignación, coincidencias, unidades ni propiedad (solo se añadieron
  puertas de consentimiento delante de ellas);
- no se tocó ningún servicio de producción, Mayra ni otro proyecto del servidor;
- no se conectó ningún portal externo ni se automatizó nada de FONACOT;
- no se hicieron optimizaciones de velocidad ni el banco de pruebas 6→50;
- todo se ejecutó con datos ficticios y el portal de laboratorio propio (127.0.0.1).

Etiquetas: **MEDIDO** (ejecutado, con su salida en el repositorio), **SIMULADO** (contra el portal de
laboratorio o con fallos provocados), **INFERENCIA**, **PENDIENTE**.

Documento hermano: [`ALCANCE_Y_AUTORIZACIONES_v2.2.md`](ALCANCE_Y_AUTORIZACIONES_v2.2.md) (qué es
interno, qué es de laboratorio y qué requiere autorización del proveedor).

## 1. Resultado en una frase

Cada expediente tiene ahora un consentimiento registrado y sellado; sin él no se opera nada, y una
revocación detiene todo lo pendiente sin borrar la evidencia. Está probado en SQLite y PostgreSQL,
con navegador real en Ventanilla y con caídas provocadas. **No** está probado en el equipo Windows
físico ni con personas reales, y su uso con personas reales depende de decisiones legales que no
son técnicas.

## 2. Qué se construyó

### 2.1 Registro de consentimiento por expediente (punto 2 de la misión)

Cada consentimiento guarda:
- **finalidad**: hoy solo `GESTION_CITA`;
- **medio**: firma en papel, firma digital o firmado en ventanilla;
- **fecha** de otorgamiento y, opcional, de vencimiento;
- **responsable**: el usuario que lo registró;
- **versión del aviso de privacidad** que se mostró;
- **huella SHA-256** del documento firmado y **dónde está el original**.

El documento **no** se sube al motor. En Ventanilla, la huella se calcula en el navegador del
operador y solo viaja la huella.

Reglas:
- un solo consentimiento vigente por expediente y trámite;
- no se edita: se revoca y se registra otro;
- se puede registrar en la misma transacción que el alta (si es inválido, el alta entera se deshace)
  o después, desde la ficha o desde la atención de Ventanilla;
- la referencia al original rechaza secuencias largas de dígitos y arrobas, para que no se cuelen
  teléfonos o correos.

API: `/api/expedientes/{id}/consentimientos`, `/api/consentimientos/{id}/revocar`,
`/api/consentimientos/verificar` (administrador) y `/api/ventanilla/atenciones/{id}/consentimiento`.

**Cadena de sellos.** Cada consentimiento tiene su cadena de eventos (OTORGADO, REVOCADO,
AVISO_CITA_CONFIRMADA). Cada evento sella el anterior con SHA-256, y la base de datos impide dos
eventos con el mismo antecesor. `verificar` recalcula la cadena y compara los datos del
consentimiento con los sellados al otorgarlo. El autodiagnóstico marca CRÍTICO si algo no cuadra.

**Límite.** La cadena detecta cambios hechos **fuera** del motor. No impide que alguien con acceso
total a la base de datos reescriba la cadena entera. Para eso haría falta guardar los sellos fuera
del equipo: **PENDIENTE**, no lo recomiendo para 6 personas al día.

### 2.2 La revocación impide nuevas operaciones (punto 3)

Sin consentimiento vigente, ninguna de estas puertas deja pasar:
- validación: el expediente queda INCOMPLETO y nunca crea solicitud;
- plan del día;
- tomar en Ventanilla;
- preparar;
- enviar datos, calendario y elegir día;
- confirmar: con la fila del consentimiento bloqueada.

Efectos de revocar, según la etapa:

| Etapa | Efecto | Evidencia |
|---|---|---|
| En el plan, en espera | Se retira del plan; solicitud cancelada; expediente CERRADO (`CONSENTIMIENTO_REVOCADO`) | prueba y demostración |
| Fecha asignada, sin preparar | Igual; ya no se puede preparar (409) | prueba y demostración |
| Día elegido, sin confirmar | El intento se descarta; confirmar da 409; el portal no recibe nada | prueba y demostración |
| Confirmación en curso o en verificación | No se decide a ciegas: se espera el resultado; si sale CONFIRMADA, se avisa | prueba |
| Cita confirmada | **No** se cancela sola: aviso a una persona para acordarlo con el trabajador; el plan la sigue contando | prueba y demostración |
| Revocar y confirmar a la vez | Gana uno; nunca queda una cita confirmada sin aviso ni un expediente abierto | prueba de carrera |

La revocación es definitiva e idempotente: revocar dos veces devuelve la misma revocación.

En la **carrera revocar contra confirmar** (MEDIDO, PostgreSQL, 4 rondas de 3), los dos desenlaces
aparecieron: 4 veces ganó la confirmación (cita más aviso) y 8 veces la revocación (nada confirmado).
Los 12 desenlaces fueron coherentes.

### 2.3 Validaciones, trazabilidad, duplicados y auditoría (punto 4)

- **Posibles duplicados no exactos.** Mismo trámite y expediente activo, más una de estas tres
  condiciones:
  - misma fecha de nacimiento y nombre parecido (≥ 0,85, o un nombre contenido en el otro);
  - mismo teléfono;
  - mismo correo.

  El expediente va a REQUIERE_REVISION. Solo un administrador lo descarta, con motivo; queda
  registrado en los dos expedientes y en la auditoría.
- **Trazabilidad.** `GET /api/expedientes/{id}/trazabilidad` muestra en una sola línea de tiempo:
  - el historial del expediente;
  - los eventos del consentimiento;
  - los cambios de la solicitud;
  - los planes;
  - las atenciones;
  - las reservaciones.

  También verifica la cadena. Sin nombre, teléfono, correo ni fecha de nacimiento (comprobado en
  prueba y en la demostración).
- **Auditoría.** Otorgar, revocar y descartar un duplicado quedan en `audit_log`, sin datos
  personales.
- **Autodiagnóstico.** Revisa la integridad de las cadenas y cuenta los expedientes en operación
  sin consentimiento vigente (los de antes de activar la exigencia). Avisa si la exigencia está
  apagada.

### 2.4 Ventanilla

- El alta de una persona tiene una sección «Consentimiento».
- Si se deja para después, la atención se detiene en el paso «Consentimiento» y no muestra el
  formulario del portal hasta registrarlo.
- La revocación se registra desde la atención. Pide marcar una casilla de confirmación y explica que
  una cita confirmada no se cancela sola.
- Capturas en `docs/resultados/v2.2/capturas/`.

### 2.5 Recuperación (punto 5)

| Caso | Resultado | Cómo |
|---|---|---|
| El proceso muere justo después de guardar la revocación | La revocación ya es definitiva; mientras tanto la puerta bloquea confirmar; el mantenimiento termina los efectos | prueba |
| La base falla entre el cambio de estado y el evento sellado | Se deshace todo: sigue VIGENTE, sin eventos huérfanos ni auditoría a medias | prueba |
| Sesión vencida | Registrar o revocar da 401 y nada cambia; al volver a entrar, «Retomar» sigue en el mismo paso | prueba |
| Motor reiniciado | Consentimientos, revocaciones y cadenas intactos; nada revocado se reabre | prueba y demostración (proceso matado con `kill`) |
| PostgreSQL detenido de verdad | El motor responde 503 y no inventa nada; al volver, se revoca con normalidad y la cadena cuadra | demostración |

### 2.6 Controles de calidad conservados (punto 6)

No se tocaron las pruebas de concurrencia, rendimiento y recuperación anteriores: siguen en la suite
y en CI como controles. No se añadieron metas de velocidad.

## 3. Resultados (MEDIDO)

| Qué | Resultado |
|---|---|
| Pruebas nuevas `tests/test_consentimiento_v22.py` | 22 casos; 44 con PostgreSQL; todos pasan |
| Suite completa, SQLite y PostgreSQL (Linux, este contenedor) | 701 pasan, 4 omitidas, 0 fallan (13 min; `docs/resultados/v2.2/pytest_sqlite_y_postgresql.txt`) |
| Demostración `scripts/demo_v22.py`, SQLite | 36 de 36 comprobaciones (`docs/resultados/v2.2/demo_v22_sqlite.json`) |
| Demostración con PostgreSQL detenido a mitad | 38 de 38 comprobaciones, con 503 durante la caída y recuperación al volver (`docs/resultados/v2.2/demo_v22_postgresql.json`) |
| GitHub Actions (Linux y Windows) | CI_PENDIENTE |

La confirmación «humana» de la demostración la hace el script: es **SIMULADA**.

## 4. Defectos encontrados y corregidos en esta ronda

| # | Defecto | Gravedad | Corrección y comprobación |
|---|---|---|---|
| D1 | El aviso «consentimiento revocado con cita confirmada» no creaba la alerta (faltaba en el catálogo de avisos): se registraba el evento, pero nadie se enteraba | Alta | Añadido al catálogo. La prueba exige exactamente 1 alerta y 1 evento tras dos ciclos de mantenimiento |
| D2 | Si se revocaba con una confirmación en verificación y luego resultaba CONFIRMADA, el aviso no se emitía nunca: el expediente dejaba de estar «activo» y el mantenimiento ya no lo miraba | Alta | El mantenimiento también revisa los confirmados revocados sin aviso, y aplica las revocaciones después de la recuperación. Prueba `test_revocar_durante_la_verificacion_espera_y_despues_avisa` |
| D3 | Revocar una cita recién confirmada podía retirar su elemento del plan, y el plan dejaba de contar esa unidad confirmada | Media | No se retira si su reservación está CONFIRMADA. La demostración comprueba que el plan la muestra CONFIRMADO |
| D4 | **Venía de la v2.0.** Con `retencion_accion=BORRAR`, un expediente CERRADO con atenciones de Ventanilla, elementos de plan o capturas manuales hacía fallar el borrado (clave foránea), y la excepción detenía la retención de **todos** los expedientes | Alta | Esos expedientes se anonimizan en lugar de borrarse (también los que tienen consentimiento: su constancia se conserva). Reproducido sin la corrección (`IntegrityError: FOREIGN KEY constraint failed`) y verde con ella, en SQLite y PostgreSQL |
| D5 | En Chromium, el patrón del campo «aviso de privacidad» era una expresión regular inválida (modo `v`): error en consola y validación del navegador perdida | Baja | Corregido. La demostración exige 0 errores de JavaScript |
| D6 | Tras revocar en Ventanilla, la pantalla final decía «el motor seguirá buscando por ella» | Media (engañoso) | Mensaje propio: el expediente se cerró y no habrá más operaciones |

## 5. Riesgos pendientes

| Riesgo | Gravedad | Qué hacer | Cómo comprobarlo |
|---|---|---|---|
| Los datos personales se guardan **sin cifrar** en la base | Alta con datos reales | Disco cifrado y respaldos cifrados antes de cualquier dato real; cifrado por columna solo si un análisis legal lo exige | BitLocker activo en el equipo; respaldo abierto sin clave = falla |
| Validez legal del consentimiento, aviso de privacidad, derechos ARCO | Alta | Revisión por un abogado; procedimiento ARCO por escrito | Documento aprobado |
| Términos de uso de FONACOT e intermediación («gestores») | Alta (comercial y legal) | Verificar antes de cobrar o anunciar; ver el documento de alcance | Respuesta escrita del proveedor o dictamen legal |
| Windows físico (MSI) sin comprobar | Media | `docs/COMPROBACIONES_WINDOWS_v2.1.md` más `python scripts\\demo_v22.py` | JSON con `ok: true` en el MSI |
| Falsos positivos de duplicados (familiares que comparten teléfono) | Baja | El administrador los descarta con motivo; vigilar cuántos hay | Contar REQUIERE_REVISION por `POSIBLE_DUPLICADO` en un mes |
| La exigencia de consentimiento se puede apagar con una variable | Media | Dejarla activa; el autodiagnóstico avisa si se apaga | Autodiagnóstico sin el aviso `cons:desactivado` |
| La revocación no borra los datos en el acto (los anonimiza la retención en su plazo) | Media | Si la persona pide supresión, aplicar el procedimiento ARCO y acortar el plazo | Expediente anonimizado en el plazo prometido |
| La cadena no resiste a quien reescribe toda la base | Baja para 6 personas al día | Respaldos fuera del equipo | — |

## 6. Cómo reproducirlo

```
python -m pytest -q tests/test_consentimiento_v22.py
python scripts/demo_v22.py --salida resultados/demo_v22
python scripts/demo_v22.py --salida resultados/demo_v22 --bd "postgresql+psycopg://…" --pg-parar "…" --pg-arrancar "…"
```

Archivos nuevos:
- `monitoreo/captura/consentimiento.py`
- `monitoreo/captura/trazabilidad.py`
- `monitoreo/api/consentimiento.py`
- migración `0015_v2_2`
- `tests/test_consentimiento_v22.py`
- `scripts/demo_v22.py`
- `scripts/demo_v22_ventanilla.js`
- `docs/ALCANCE_Y_AUTORIZACIONES_v2.2.md`
- este informe

Modificados: expedientes, ventanilla, reservaciones, seguimiento, retención, plan (propiedad),
autodiagnóstico, `ventanilla.js` y CI.
