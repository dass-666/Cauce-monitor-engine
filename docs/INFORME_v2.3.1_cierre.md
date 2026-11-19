# Informe v2.3.1 · Cierre de Ventanilla (ronda corta)

Fecha: 2026-11-19. Rama `claude/project-thread-2wwz8e` (sin fusionar, sin desplegar). Convención:
**HECHO** (con evidencia), **INFERENCIA**, **PENDIENTE**. No toca `jarvis/` ni las ramas
`claude/integracion-v2.2.2` y `claude/project-thread-6fxbwl`.

## 1. demo_v22 paso 6 en Windows 3.11: era una carrera, no un intermitente (HECHO)

**Síntoma (CI 44478520364):** al revocar el consentimiento con el día ya elegido, la respuesta decía
`intento_descartado: None`, `solicitud_cancelada: True`, `expediente_cerrado: True`. El paso siguiente
(confirmar después de revocar → 409, el portal no recibe nada) **sí pasó**: el estado final era correcto.

**Causa demostrada:** `consentimiento.revocar` guarda el REVOCADO en una transacción y después aplica
los efectos (`aplicar_revocacion`). El mantenimiento del motor (`aplicar_revocaciones`, otro hilo, cada
pocos segundos) busca revocados con expediente activo y aplica los mismos efectos. Si corre en ese
hueco, descarta él el intento; la petición ya no lo encuentra y lo informa como «no había intento».

**Reproducción determinista:** `tests/test_consentimiento_v22.py::test_revocar_mientras_el_mantenimiento_aplica_la_misma_revocacion`
fuerza ese orden con dos hilos y obtiene **exactamente** la huella de la CI. Falla sin el arreglo
(`assert (None)`) y pasa con él.

**Gravedad:** baja. Nunca hubo confirmación tras revocar ni efectos repetidos; el defecto era que la
respuesta a quien revoca era falsa. Ninguna pantalla usa esos campos hoy; los scripts de evidencia sí.

**Arreglo (mínimo):** `consentimiento._efectos_finales` completa la respuesta con el estado FINAL del
expediente (intento cancelado por la revocación, solicitud cancelada, expediente cerrado por
`CONSENTIMIENTO_REVOCADO`), lo haya hecho la petición o el mantenimiento. No cambia reglas, umbrales,
la política de consentimiento ni el mantenimiento.

**INFERENCIA:** por qué Windows 3.11: es la máquina más lenta y la que más tarda entre las dos
transacciones; el orden también es posible en Linux. No es demostrable sin instrumentar la CI.

## 2. El plan pasa a CONFIRMADO tras el folio (HECHO)

El recorrido v2.3 comprueba ahora, fuera del navegador, que el elemento del plan llega a **CONFIRMADO**
(lo hace el mantenimiento del planificador, `operacion.sincronizar`) y que el historial lo refleja: plan
CONFIRMADO, reservación CONFIRMADA con el mismo folio y la última atención con ese folio.
Resultado: **21/21** (`docs/resultados/v2.3/recorrido_v23.json`). Lo de la v2.3 («aún dice Operación
preparada») era la captura tomada en el instante del cierre, antes de la sincronización.

## 3. Permisos (HECHO, validados en el servidor)

Decisión de Diego registrada: el operador deja en el plan abierto de hoy **solo** el expediente de su
propia atención. `tests/test_recorrido_v23.py::test_permisos_por_api_directa_operador_y_administrador`
llama a la API directamente con el token del operador:

- **403** en: crear plan, ampliar, seleccionar, agregar elementos, observador (iniciar y detener),
  sincronizar, decidir duplicados, Calendario lab (estado, abrir día, registros ficticios).
- Sin plan de hoy (aunque exista uno de mañana): `SIN_PLAN`. Sin consentimiento vigente: 409 y la
  atención sigue abierta. Atención de otro operador: 404. Repetir la misma: 409, sin elemento duplicado.
- El administrador sí puede (no son rutas rotas). Ya cubiertos antes: capacidad (`PLAN_COMPLETO`),
  fechas inválidas (422), atención sin expediente (409). Sucursales y fechas: las mismas reglas de
  `plan.agregar`.

## 4. Regresiones (HECHO, local)

| Qué | Resultado |
|---|---|
| Suites afectadas (recorrido v2.3, consentimiento, ventanilla, planificador, propiedad) SQLite + PostgreSQL 16 | 135/135 |
| `demo_v22` (paso 6 incluido), 3 ejecuciones seguidas | 36/36 las tres |
| `demo_v20` | 28/28 |
| `aceptacion_v21` | 42/42 |
| `recorrido_ventanilla_v23` | 21/21 |
| Suite completa SQLite | 595 pasan, 8 omitidas, 0 fallan |
| CI de GitHub de este commit | ver §6 |

## 5. Windows físico

Paquete: **`docs/VALIDAR_v2.3_WINDOWS.md`** (bajar la rama, instalar, demostración automática,
recorrido a mano con datos ficticios, detener con `Ctrl + C`, guardar el resultado en un ZIP, qué
reportar).

**PENDIENTE (lo tiene que hacer Diego en el MSI):** nada de esto se ha ejecutado en Windows físico.
Tampoco está comprobado en Windows que cerrar con `Ctrl + C` detenga motor y portal sin dejar procesos
(INFERENCIA por diseño; la guía incluye cómo comprobarlo).

## 6. Pendiente

1. Resultado de la CI de este commit (se anota abajo al terminar).
2. Validación en el MSI con la guía del §5.
3. Si el recorrido entra al manifiesto de Jarvis: exige volver a fijar; no se hizo.
