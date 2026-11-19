# Inventario de campos del expediente · RC1

Fecha: 2026-11-19. Fuente: código de la rama RC1 (`monitoreo/models.py`, `monitoreo/captura/validacion.py`,
`monitoreo/captura/importacion.py`, `monitoreo/captura/formularios/contratos/portal_laboratorio_dia.json`) y la
documentación existente (`docs/CONTRATO_ADAPTADORES.md`, `docs/ALCANCE_Y_AUTORIZACIONES_v2.2.md`).

**Advertencia importante.** El único "formulario" que Cauce conoce es el del **portal de laboratorio propio**
(`portal_laboratorio_dia.json`), que se diseñó en la v1.5–v1.6 como imitación ficticia. **No es el formulario de
ningún servicio externo.** Por eso la columna "Procedencia" distingue entre lo que exige el laboratorio y lo que
falta confirmar con documentación real. No se inventó ningún campo ni regla nueva salvo las tres fechas de la RC1
(fecha solicitada y rango aceptable), que pidió el propietario en esta misión.

Leyenda de "Obligatorio": **Alta** = sin él no se crea el expediente (ni a mano ni importado). **Captura** = el
expediente se crea pero queda INCOMPLETO y bloqueado hasta tenerlo. **Opcional**.

| Campo (BD) | Tipo | Obligatorio | Procedencia del requisito | Validaciones actuales | En BD | En interfaz | Importable | ¿Falta documentación real? |
|---|---|---|---|---|---|---|---|---|
| `nombre` | texto ≤128 | Alta | Mínimo interno v1.6 | Nombre y apellido, solo letras; 5–128 car.; se normaliza espacios. Importación acepta "Nombre completo" o Nombre(s)+apellidos | Sí | Sí (enmascarado en listados) | Sí | No para Cauce; sí para saber cómo lo escribe el servicio real (orden de apellidos, acentos) |
| `fecha_nacimiento` | fecha | Captura | Formulario del portal de laboratorio | No futura; ≤120 años. Importación: DD/MM/AAAA (o MM/DD si se elige), AAAA-MM-DD, celda fecha de Excel; año de 4 dígitos | Sí | Sí | Sí | Sí |
| `id_registro_previo` | texto ≤32 | Captura | Formulario del portal de laboratorio ("Identificador de registro") | 3–32 letras/números/guiones, mayúsculas. **Se lee como texto: conserva ceros iniciales** (o respeta formato Excel `00000000`) | Sí | Sí | Sí | **Sí**: formato real del identificador (longitud, letras, dígito verificador) no documentado |
| `telefono` | texto 10 díg. | Captura | Formulario del portal de laboratorio | 10 dígitos; en laboratorio, prefijo 555 (solo datos ficticios) | Sí | Sí | Sí | Sí (¿10 dígitos? ¿lada?) |
| `telefono_confirmacion` | — | (solo al teclear) | Formulario de laboratorio | Debe coincidir; **no se guarda**. En importación el archivo es la fuente: se usa el mismo valor | No | Sí (alta manual) | No aplica | — |
| `correo` | texto ≤128 | Captura | Formulario del portal de laboratorio | Formato de correo; en laboratorio solo dominios de prueba (.test, example.com…) | Sí | Sí | Sí | Sí |
| `correo_confirmacion` | — | (solo al teclear) | Formulario de laboratorio | Igual que teléfono; no se guarda | No | Sí | No aplica | — |
| `region` | código | Alta | Modelo interno (regiones del motor) | Debe existir en `regions`. Importación: columna "Región" o deducida de la sucursal si el calendario ya la conoce | Sí | Sí | Sí | No (interno) |
| `sucursal` | texto ≤32 | Opcional (necesaria para planificar por sucursal) | Calendario del laboratorio | 2–32 mayúsculas/números/guiones; si no pertenece a la región → revisión | Sí | Sí | Sí | **Sí**: catálogo real de sucursales |
| `sucursales_alternativas` | lista ≤5 | Opcional | v1.6 interno | Mismo patrón que sucursal | Sí | Sí | Sí ("a, b" o "a; b") | Sí |
| `tipo_tramite` | código | Alta | Catálogo del laboratorio (alta, renovación, constancia: **ficticios**) | Debe estar en el catálogo; importación acepta "Alta", "Renovación"… | Sí | Sí | Sí | **Sí**: catálogo real de trámites |
| `operacion` | nueva / reprogramar | Opcional (por omisión "nueva") | Formulario de laboratorio | Valor del catálogo; reprogramar exige registro previo y folio anterior | Sí | Sí | Sí | Sí |
| `folio_anterior` | texto ≤32 | Solo si reprogramar | Formulario de laboratorio | 3–32 letras/números/guiones | Sí | Sí | Sí | Sí |
| `prioridad` | 1–5 | Opcional (3) | Interno | Entero 1 (máxima) a 5 | Sí | Sí | Sí | No |
| `id_externo` | texto ≤64 | Opcional | Interno (identificador de la lista del cliente, p. ej. n.º de empleado) | Único en toda la base: **clave principal de no duplicación al importar** | Sí | Sí | Sí | No |
| `fecha_solicitada` | fecha | Opcional | **RC1 (pedido del propietario)** | AAAA-MM-DD; ≤2 años | Sí (migr. 0016) | Sí (Expedientes › Fechas) | Sí | No; es informativa (lo que la persona pidió) |
| `fechas_desde` / `fechas_hasta` | fecha | Opcional (sin rango, el plan usa el intervalo que indique el administrador) | **RC1** | Desde ≤ hasta; el rango no puede haber pasado al crear | Sí (migr. 0016) | Sí | Sí | No |
| Consentimiento (tabla `consentimientos`) | registro con medio, aviso, huella SHA-256 del documento, referencia | **Captura** (exigido por defecto) | v2.2 (decisión del propietario: uso real con consentimiento) | Medio del catálogo, versión del aviso, huella de 64 hex, referencia sin datos personales, ≤365 días; cadena de sellos | Sí (tabla aparte) | Sí (Ventanilla y RC1 › Expedientes) | **No, a propósito**: una columna "consentimiento" del archivo se ignora | Sí: formato de aviso de privacidad real y política de conservación de originales |
| `clave_identidad` (campo del portal) | texto | Se teclea en la operación | Formulario de laboratorio (MANUAL_REQUIRED) | Se teclea, no se pega ni se guarda | **No** | Sí (solo en la operación) | **No**: columnas tipo clave/contraseña/NIP se descartan | Sí |
| `acepto_privacidad` (portal) | casilla | En la operación | Formulario de laboratorio | Marcar en el momento | No | Sí | No | Sí |
| Fecha **confirmada** | fecha + sucursal + folio | Resultado | Portal (respuesta con folio) | Solo con folio leído del portal con la misma clave | En `reservaciones` (no en el expediente) | Sí (Mesa de control, historial) | No | — |

## Separación de fechas (pedido explícito)

| Concepto | Dónde vive | Quién lo pone |
|---|---|---|
| Datos de identificación | `expedientes` (nombre, nacimiento, registro previo, contacto) | Importación o captura |
| Fecha que pidió la persona | `expedientes.fecha_solicitada` | Importación o administrador |
| Rango que acepta | `expedientes.fechas_desde/hasta`; al entrar al plan se copia a `plan_elementos.fechas_aceptables` (recortado a la jornada y al horizonte de 120 días) | Importación o administrador |
| Día de una **coincidencia** | `plan_elementos.fecha_asignada` (no es cita) | Planificador, a partir del observador |
| Fecha **confirmada** | `reservaciones.fecha` + `folio` (estado CONFIRMADA) | Solo el portal, con folio comprobado |

## Lo que falta confirmar con documentación real (antes de usarlo fuera del laboratorio)

1. Catálogo real de trámites y sucursales, y formato del identificador de registro previo.
2. Qué datos exige realmente el servicio (y cuáles no debemos pedir).
3. Política de consentimiento: texto del aviso, medio válido, plazo de vigencia y conservación del original.
4. Validaciones "solo sintéticos" (prefijo 555, correos .test): son **del laboratorio** y deben seguir activas
   mientras no exista autorización y documentación para datos reales.
