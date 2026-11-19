# Matriz de correspondencia · 8 campos del formulario · RC1

Fecha: 2026-11-19. Rama `claude/project-thread-y5dh74`. Revisión del contrato de datos frente al código actual.
**Única fuente de requisitos del formulario:** el contrato del portal de **laboratorio**
(`monitoreo/captura/formularios/contratos/portal_laboratorio_dia.json`). No es el formulario de ningún
servicio externo; no se añadió ningún requisito que no esté ahí.

## 1. Matriz

| # | Campo (tu nombre) | Formulario de laboratorio (nombre · obligatorio · regla) | Modelo / BD (`expedientes`) | Validación (`validacion.py`) | Pantallas | Importación (encabezados reconocidos y conversión) |
|---|---|---|---|---|---|---|
| 1 | Operación a realizar | `operacion` · sí · opción | `operacion` VARCHAR(16), por omisión `nueva` | Catálogo `nueva` / `reprogramar`. Reprogramar exige identificador y folio de la cita anterior (si faltan: INCOMPLETO) | Alta (Ventanilla), **Corregir datos** (Mesa de control), ficha, revisión de la operación | «Operación», «Operación a realizar», «Tipo de operación», «Tipo de cita». Valor: clave o etiqueta («Nueva cita», «Reprogramar»), sin acentos ni mayúsculas. Vacío → `nueva` |
| 2 | Tipo de trámite | `tramite` · sí · opción | `tipo_tramite` VARCHAR(32) NOT NULL | Catálogo `alta` / `renovacion` / `constancia` (**ficticios**). Mínimo para crear el expediente | Ídem | «Trámite», «Tipo de trámite», «Servicio». Valor: clave o etiqueta («Renovación (ficticia)», «Alta de registro») |
| 3 | Identificador de cliente o folio de registro previo | `identificador` («Identificador de registro») · sí · `[A-Z0-9-]{3,32}` | `id_registro_previo` VARCHAR(32), **texto** | 3–32 letras, números o guiones; se pasa a mayúsculas. Si falta: INCOMPLETO | Ídem | «Registro previo», «Identificador de registro», «Folio de registro previo», «Identificador de cliente», «Número de registro»… Se lee como texto: **conserva los ceros** (CSV siempre; Excel si la celda es texto o tiene formato `00000000`) |
| 4 | Fecha de nacimiento | `fecha_nacimiento` · sí · no futura | `fecha_nacimiento` DATE | No futura, ≤120 años. Si falta: INCOMPLETO | Ídem | «Fecha de nacimiento», «Nacimiento», «F. nac.»… DD/MM/AAAA (o MM/DD si se elige), AAAA-MM-DD o fecha de Excel |
| 5 | Teléfono de contacto | `telefono` · sí · `[0-9]{10}` | `telefono` VARCHAR(16) | 10 dígitos; en laboratorio, prefijo 555 (solo ficticios). Si falta: INCOMPLETO | Ídem (enmascarado en listados) | «Teléfono», «Teléfono de contacto», «Celular»… Acepta espacios y guiones; se guardan 10 dígitos |
| 6 | Confirmación del teléfono | `telefono_confirmacion` · sí · `confirmacion_de: telefono` | **No existe columna** | Debe coincidir con el teléfono; se exige al crear y al cambiar el teléfono | Alta y Corregir datos (solo si se cambia el teléfono) | «Confirmación del teléfono», «Confirmar teléfono»… Opcional: si la columna existe se **compara** (si no coincide, la fila se rechaza); si no existe, el archivo es la única fuente |
| 7 | Correo electrónico | `correo` · sí · (sin patrón en el contrato) | `correo` VARCHAR(128) | Formato de correo, en minúsculas; en laboratorio solo dominios de prueba. Si falta: INCOMPLETO | Ídem (enmascarado en listados) | «Correo», «Correo electrónico», «Email», «Correo de contacto» |
| 8 | Confirmación del correo | `correo_confirmacion` · sí · `confirmacion_de: correo` | **No existe columna** | Debe coincidir (sin distinguir mayúsculas) | Alta y Corregir datos (solo si se cambia el correo) | «Confirmación del correo», «Confirmar correo»… Igual que el 6 |

Conceptos relacionados que **no** deben confundirse con el campo 3: `id_externo` (identificador interno de tu
lista, opcional; clave para no duplicar al reimportar) y `folio_anterior` (folio de la cita que se reprograma).

## 2. Lo que pedías comprobar

| Requisito | Resultado | Evidencia |
|---|---|---|
| Los ocho campos representados | Sí: seis como columnas y dos (confirmaciones) solo como validación | Matriz; `test_ocho_campos_…` |
| Identificador con ceros iniciales | Sí: columna de texto, «00450007» y «000123» se guardan tal cual | `test_ocho_campos_…`, `test_corregir_…`, captura 05 |
| Operación y trámite independientes | Sí: columnas, catálogos y validaciones separados («Reprogramar» + «Renovación» se guardan cada uno en su campo) | `test_ocho_campos_…` |
| El Excel prepara bien un expediente | Sí, tras las correcciones D1–D4 y D7 | pruebas y plantilla |
| Las confirmaciones no duplican columnas | Sí: no hay columnas `*_confirmacion` en `expedientes` | aserción sobre `Expediente.__table__` |
| Validaciones y obligatorios documentados | Sí: esta matriz e [INVENTARIO_CAMPOS_RC1.md](INVENTARIO_CAMPOS_RC1.md) (actualizado) | — |
| Revisar y corregir lo importado desde la Mesa de control | **No se podía** (D6); ahora sí | `test_corregir_…`, recorrido paso 4b, captura 05 |

## 3. Defectos encontrados y corregidos

| | Defecto (comprobado) | Gravedad | Corrección | Prueba |
|---|---|---|---|---|
| D1 | «Confirmación del teléfono/correo» se clasificaba como columna de **consentimiento**: «con**firma**ción» contiene «firma» y la búsqueda era por subcadena. La vista previa decía «consentimiento ignorado». | Media | Búsqueda por palabras completas y campos de confirmación reconocidos | `test_ocho_campos_…` |
| D2 | Las confirmaciones del archivo nunca se comparaban: un teléfono mal capturado en una de las dos columnas pasaba sin aviso | Media | Si hay columna de confirmación se compara; si no coincide, la fila se rechaza con el motivo | `test_ocho_campos_…` (filas 4 y 5 rechazadas) |
| D3 | No se reconocían las etiquetas del propio formulario («Identificador de registro», «Operación a realizar», «Teléfono de contacto») ni las del importador («Folio de la cita anterior»): la columna quedaba «sin uso» y el identificador podía perderse si no se asignaba a mano | Media | Sinónimos añadidos y cada campo reconoce su propia etiqueta | `test_ocho_campos_…`, `test_plantilla_…` |
| D4 | El valor «Nueva cita» (la etiqueta que muestran las pantallas) se rechazaba como «Operación no válida»; «Renovación (ficticia)» tampoco se aceptaba | Media | Se acepta la clave o la etiqueta del catálogo | `test_ocho_campos_…` |
| D5 | «ID cliente» se asignaba solo a `id_externo`, en conflicto con tu término «identificador de cliente» (= registro previo) | Media (dato en el campo equivocado) | «Identificador de cliente» → registro previo; «ID cliente» ya no se asigna solo: el administrador elige en la vista previa (**decisión confirmada por el propietario**, 2026-11-19) | `test_plantilla_…` |
| D6 | La Mesa de control no permitía corregir datos: el botón «Ver / corregir» solo mostraba la ficha | Alta para el flujo administrativo | Botón **Corregir datos** con los 8 campos + folio anterior; usa el PATCH existente (mismas validaciones, control de versión, revalidación, historial sin valores) | `test_corregir_…`, recorrido 4b |
| D7 | La plantilla no traía «Operación» ni «Folio de la cita anterior», y sus columnas no eran Texto: Excel convertiría «00450001» tecleado en 450001 | Baja-media | Columnas añadidas; identificador, teléfono y folio con formato Texto | `test_plantilla_…` |

No se tocó: modelo, migraciones, reglas de validación, contrato del formulario, planificador, observador,
operación ni Ventanilla.

## 4. Límites honestos

- **Ceros ya perdidos no se recuperan.** Si un Excel ajeno guardó el identificador como número sin formato,
  los ceros ya no existen en el archivo; el importador avisa («formatea la columna como Texto») pero no los adivina.
- **Corregir datos** aparece en «Faltan datos», «Falta consentimiento», «Listo» y «En revisión». Con el expediente
  en el plan o en operación no se ofrece (para no cambiar datos de una operación en marcha). Tampoco permite
  **vaciar** un campo (el PATCH ignora valores vacíos).
- Catálogos (trámites, sucursales) y formato real del identificador **siguen siendo del laboratorio**: falta
  documentación del servicio real.
- Probado en Linux con SQLite y en la CI de GitHub (corrida 98, commit `497f63c`: **30/30 trabajos**, incluido el
  recorrido RC1 en Linux con SQLite/PostgreSQL y Windows con Python 3.11, 3.12 y 3.13:
  https://github.com/dass-666/Cauce-monitor-engine/actions/runs/44574124275). No probado en el MSI.

## 5. Mejora pendiente (documentada, no implementada)

**Vaciar un campo incorrecto.** Hoy «Corregir datos» solo puede cambiar valores; no puede dejar vacío un campo
mal importado (por ejemplo, un folio anterior que no corresponde). Propuesta para una fase futura, cuando se decida:

- Acción **explícita** «Quitar este dato» por campo (nunca al dejar la casilla vacía), con motivo obligatorio.
- Validada: solo campos que pueden faltar (no nombre, región ni trámite); el expediente vuelve a validarse y,
  si queda incompleto, sale de «Listo» como cualquier otro faltante.
- Auditada: evento en el historial con el **nombre** del campo y el motivo, nunca el valor.
- Bloqueada durante operaciones activas (en el plan con coincidencia, operación preparada, confirmación en curso o
  incierta, cita confirmada), igual que hoy los cambios de región, sucursal o trámite.

## 6. Decisiones confirmadas por el propietario (2026-11-19)

- Las siete correcciones D1–D7 quedan aceptadas.
- «ID cliente» permanece ambiguo: exige asignación manual en la vista previa.
- Se conservan separados `id_registro_previo` (identificador del formulario), `id_externo` (identificador interno
  de la lista, clave de no duplicación) y `folio_anterior` (folio de la cita que se reprograma).
- Los catálogos y reglas son del **laboratorio**; no se presentan como requisitos de un servicio externo.
