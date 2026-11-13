# Guía del administrador · v1.7

Para quien configura el motor, reparte el trabajo y vigila la operación. Complementa
`GUIA_RAPIDA_WINDOWS.md` (instalación) y `SOLUCION_PROBLEMAS.md`.

## 1. Centro de operaciones (Inicio)

La pantalla **Inicio** muestra, con cifras reales de la base de datos:

- fuentes vigiladas, consultas, errores y tiempos de detección;
- solicitudes, asignaciones, confirmaciones y tiempos de atención;
- expedientes por estado y bloque de captura: calendario sin verificar, fuentes con error, operaciones
  a medias y confirmaciones en verificación;
- operadores conectados y notificaciones sin confirmar.

Se filtra por **región, sucursal, operador, estado y periodo** (hoy, 24 h, 7 días, 30 días). Un
operador que abre Inicio solo ve sus propias cifras: el filtro de operador se le fija a él.

## 2. Reparto del trabajo (colas)

En *Configuración → Parámetros del motor*:

| Parámetro | Valor por defecto | Qué hace |
|---|---|---|
| `reparto_modo` | `menor_carga` | `menor_carga`: el operador compatible con menos trabajo abierto (igual que v1.0–v1.6). `equitativo_dia`: el que menos asignaciones lleva **hoy**, para repartir el día de forma pareja. |
| `operador_ausente_s` | `0` (no se mira) | Si es mayor que 0, no se asigna trabajo nuevo a quien no usa el panel hace más de N segundos. |

Cada asignación guarda su **explicación**: por qué se eligió a ese operador, qué otros candidatos
había y por qué se descartaron (región, sucursal, capacidad, rechazo previo…). Se abre con el botón
**¿Por qué?** en la tabla de asignaciones. El operador ve solo el resumen, sin los nombres de sus
compañeros.

## 3. Supervisión

Menú **Supervisión** (solo administradores). Para cada operador: presencia, carga, asignadas, en
proceso, completadas hoy, rechazos, reasignaciones, errores y trabajo sin atender.

| Presencia | Significa |
|---|---|
| **CONECTADO** | Usa el panel (señal en los últimos `operador_conexion_perdida_s`, 90 s por defecto). |
| **CONEXION_PERDIDA** | Tiene la sesión abierta pero no da señal desde hace un rato. Puede volver (red, portátil suspendido). |
| **INACTIVO** | Sesión abierta y sin señal más de `operador_inactivo_s` (15 min por defecto). |
| **DESCONECTADO** | Cerró sesión o su sesión caducó. |
| **SIN_USUARIO** | Operador sin cuenta en el panel (recibe por otro canal). |

### Redistribuir el trabajo de un operador

Botón **Redistribuir…** en su fila (aparece si tiene trabajo asignado o en proceso). Pide un **motivo** y una casilla de confirmación.

- Las asignaciones **no empezadas** vuelven a la cola siempre.
- Las **empezadas** solo si marcas *Incluir las tomadas (en proceso)* **y** el operador no da señal desde
  hace más de `operador_conexion_perdida_s`.
- Nunca se libera un expediente con una **confirmación en curso o en verificación**: la resuelve el
  motor contra el portal. Aparece en la respuesta como *no liberada* con el motivo.
- Opcional: *desactivar* al operador para que no reciba trabajo nuevo hasta reactivarlo.

Cada liberación es una transacción con condición (si el operador la tomó o terminó mientras tanto,
no se toca) y todo queda en la **auditoría** (`operador_redistribuido`).

## 4. Notificaciones

Menú **Notificaciones**. Diez categorías con nivel:

| Categoría | Nivel |
|---|---|
| Nueva disponibilidad | MEDIA |
| Disponibilidad agotada | BAJA |
| Expediente asignado | ALTA |
| Expediente incompleto | MEDIA |
| Operación abandonada | ALTA |
| Confirmación pendiente | ALTA |
| Reservación confirmada | BAJA |
| Error de comunicación | ALTA |
| Problema del motor | CRÍTICA |
| Requiere intervención | CRÍTICA |

- **Filtros**: categoría, nivel, estado, región, sin confirmar y periodo. **Confirmar las N visibles** marca
  como vistas las que estás viendo (hasta 200) (queda en la auditoría).
- **Silenciar**: se puede silenciar cualquier categoría que no sea CRÍTICA. Las silenciadas quedan
  en el historial pero no se envían por ningún canal.
- **Agrupar**: los avisos repetidos de la misma causa (una fuente que falla una y otra vez) dentro de
  `alertas_agrupar_s` (300 s) se suman en uno con su número de repeticiones.
- **Canales**: cada canal puede limitarse a ciertas categorías (`config.categorias`). La tabla de
  canales muestra entregas por estado y latencia. Un canal lento o caído **no detiene** al motor: las
  entregas se hacen aparte y se reintentan. Desde la v1.7 cada canal tiene además un tiempo máximo
  por pasada (`despacho_canal_presupuesto_s`, 3 s): un canal que responde despacio no retrasa a los demás.
- Las notificaciones **no llevan datos personales**: el titular aparece con iniciales.

## 5. Diagnóstico y reparaciones

*Configuración → Autodiagnóstico* (o `diagnostico.bat`). Cada comprobación dice OK, AVISO o
CRÍTICO, con una explicación en lenguaje llano y qué hacer. Áreas nuevas en la v1.7: eventos del
calendario, operaciones a medias o en verificación, coherencia de expedientes, retención y
reparaciones disponibles.

| Reparación | Tipo | Cuándo se aplica |
|---|---|---|
| Recalcular la carga de operadores y lo retenido en cada recurso | Segura | **Sola**, en cada autodiagnóstico, y queda en la auditoría. |
| Devolver a la cola notificaciones atascadas en «enviando» | Segura | Sola, igual que la anterior. |
| Borrar entregas cuya alerta ya no existe | **Borra datos** | Solo con el botón **Reparar (con confirmación)** y un motivo escrito por un administrador. |

## 6. Usuarios y privacidad

- Un operador solo ve y puede abrir **sus** expedientes, solicitudes, asignaciones y notificaciones
  (corregido en la v1.7: antes veía las notificaciones y solicitudes de todos).
- Limita a cada operador a sus **regiones** y **sucursales** en *Configuración → Usuarios*.
- La política de retención (v1.6) anonimiza los expedientes antiguos; revisa sus plazos.

## 7. Comprobar que todo funciona

- `diagnostico.bat`: estado del motor, sin cambiar nada.
- `pruebas.bat`: toda la batería de pruebas.
- `laboratorio.bat` (nuevo): unos 10 minutos con datos ficticios en carpetas temporales. Simula
  10 operadores, errores de captura, operadores que dejan de responder, confirmaciones inciertas,
  un canal de notificaciones lento y reinicios, y lo compara todo con el portal de prueba.
