# Contrato de adaptadores de proveedor (v2.0 · P2)

Código: `monitoreo/adaptadores/` · Pruebas: `tests/test_adaptadores.py` · API: `/api/adaptadores`, `/api/capturas-manuales`
(solo fuera de producción, igual que la captura de laboratorio).

## Para qué sirve

Un **adaptador** es la única pieza del motor que sabe cómo habla un proveedor concreto. El resto del
motor trabaja con tipos normalizados (`Resultado`, `Disponibilidad`, `Preparacion`, `Verificacion`).
Así, añadir un proveedor nuevo es escribir un adaptador, sin tocar la ventanilla, el planificador ni la
base de datos.

## Las cuatro etapas (separadas a propósito)

| Etapa | Método | Red | Quién la usa |
|---|---|---|---|
| 1. Preparación | `preparar(datos) -> Preparacion` | No | Qué pide el trámite, qué falta y qué pasos son manuales. **No devuelve valores** (sin datos personales). |
| 2. Observación | `observar(filtros) -> Observacion` | Sí, si `capacidades().observar` | Disponibilidad normalizada (fecha ISO, sucursal, estado, lugares). |
| 3. Captura manual | (servicio `capturas.registrar`) | No | El operador hace el trámite en el **flujo oficial** y registra el folio y la fecha. |
| 4. Confirmación | `confirmar(ref) -> Resultado` | Sí, solo con integración **autorizada** | Sin autorización lanza `NoAutorizado`: el camino es la captura manual. |

Además: `verificar(folio, fecha, sucursal) -> Verificacion` (para comprobar una captura manual por una
vía autorizada), `interpretar(status, headers, cuerpo) -> Resultado`, `normalizar_fecha()` y `validar_folio()`.

## Capacidades (`capacidades()`)

`nombre, descripcion, version_contrato, preparar, observar, confirmar, verificar, captura_manual,
autorizado, red, modos, formato_folio, notas`.

Regla comprobada por las pruebas: **observar, verificar o confirmar exigen `autorizado=True`**.

## Resultados normalizados

| Tipo | Significa | Qué hace el motor |
|---|---|---|
| `OK` | El proveedor respondió bien | Sigue |
| `RECHAZO` | Rechazo de negocio (4xx: día agotado, datos inválidos…) | Lo muestra; no reintenta igual |
| `RESTRICCION` | **429 o 403**: cuota, límite, acceso negado | **Detiene la operación**, anota el motivo (y `Retry-After` solo como dato). No reintenta, no cambia de conexión ni de identidad |
| `INCIERTO` | 5xx, corte, tiempo agotado | No se da por buena ni por mala: se verifica después |
| `RED` | No se pudo conectar | Igual que incierto; el gestor de conexiones puede usar el respaldo **solo si la conexión no llegó a establecerse** |
| `NO_AUTORIZADO` | 401 o falta de integración autorizada | Detiene; requiere credenciales/autorización |

`Resultado.detener` es `True` para `RESTRICCION` y `NO_AUTORIZADO`. `transaccion_id` guarda el
identificador de transacción del proveedor (cuerpo `transaccion_id`, o cabeceras `X-Transaccion-Id` /
`X-Request-Id`).

## Fechas

`normalizar_fecha` acepta `AAAA-MM-DD`, `DD/MM/AAAA`, `DD-MM-AAAA`, `AAAA/MM/DD`, `DD.MM.AAAA` y objetos
`date`. **No adivina**: `MM/DD/AAAA` no se acepta (en México `03/04` es 3 de abril) y una fecha
imposible (`2026-02-30`) falla.

## Captura manual (modo demostración / proveedor sin integración)

Estados de `capturas_manuales` (tabla nueva, migración `0011_v2_0`):

- `DECLARADA`: el operador la registró; nadie la ha comprobado (o la comprobación quedó incierta o
  restringida; se anota el motivo y **no se reintenta sola**; una persona pulsa «Verificar de nuevo»).
- `VERIFICADA`: el adaptador la comprobó por una vía autorizada y **coinciden folio y fecha** (y sucursal si se dio).
- `DISCREPANCIA`: el proveedor no la tiene o tiene otra fecha. Si está ligada a un expediente, el
  expediente pasa a `REQUIERE_REVISION`.
- `SIN_VERIFICACION`: el adaptador no tiene vía autorizada (p. ej. `oficial_manual`).

Reglas: exige marcar «el sitio oficial mostró este folio» (sin folio no hay cita); el folio se valida
con el formato del proveedor; un folio no se registra dos veces por proveedor; cada respuesta lleva
`es_cita_confirmada_por_el_motor: false`; **no cambia el estado de reservaciones ni del día del
operador** (lo declarado no se mezcla con lo confirmado por el motor). Auditoría: `captura_manual_registrada`.

## Adaptadores incluidos

| Nombre | Qué es | Observa | Verifica | Confirma |
|---|---|---|---|---|
| `simulado` | En memoria, determinista, sin red. Todo es SIMULADO | Sí | Sí | Sí (idempotente por clave) |
| `laboratorio` | Portal de prueba propio en 127.0.0.1, por el gestor de conexiones | Sí (`/api/dias`) | Sí (`/api/reservaciones/{folio}`) | No: lo hace el flujo de reservaciones del motor |
| `oficial_manual` | Portal oficial **sin** integración autorizada | No | No | No (solo captura manual) |

## Cómo se añade un proveedor real (cuando haya autorización)

1. Autorización formal y credenciales del proveedor; documentar el alcance permitido.
2. Nuevo módulo `monitoreo/adaptadores/<proveedor>.py` que herede de `Adaptador`; `capacidades()`
   con `autorizado=True` solo para lo autorizado.
3. Pasar por `tests/test_adaptadores.py::test_todo_adaptador_cumple_el_contrato` (añadirlo al parámetro).
4. El host debe estar permitido en `MONITOREO_WEBHOOK_HOSTS`; hoy el motor no puede apuntar a hosts externos.

Fuera de alcance, siempre: rotación de IP, evasión de CAPTCHA, de cuotas o de controles de acceso.

## Límites conocidos (honestos)

- La captura manual no cierra la atención de ventanilla ni cuenta en «citas del día»: es deliberado
  (lo declarado no es lo confirmado). Integrarla con el cierre de la atención requiere una decisión de producto.
- Solo existe un adaptador con red (laboratorio). Ningún proveedor real está conectado ni probado.
