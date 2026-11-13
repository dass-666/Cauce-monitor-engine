# Informe de la versión 1.6: captura y reservaciones POR DÍA

> **BORRADOR**: faltan las cifras finales del laboratorio y de CI (marcadas con <<...>>).

Fecha: 13 de noviembre de 2026 · Rama `fase-1.6` (sale de `fase-1.5` `eb7f469`; **sin fusionar en `master`**)
Repositorio: `dass-666/Cauce-monitor-engine` (privado)

Leyenda: **MEDIDO** (ejecución real con archivo de resultados) · **SIMULADO** (datos o actores
ficticios a propósito) · **ESTIMADO** (deducción, no medido) · **PENDIENTE** (no verificable desde aquí).

## Resumen en cinco líneas

1. Un expediente ficticio POR DÍA llega a un folio con un navegador real: el motor lo valida y lo
   asigna solo, el operador teclea los campos manuales, elige un **día** (sin hora) y confirma;
   tras matar motor y portal, el folio se recupera en ambos y en el panel. **27/27 comprobaciones**
   (MEDIDO, §11).
2. Laboratorio multisucursal: <<LAB_RESUMEN>>
3. Pruebas automáticas: <<SUITE>> (MEDIDO, §7).
4. Se encontraron y corrigieron **4 defectos reales** del motor (zona horaria, carrera en
   PostgreSQL, asignación con datos de una fuente caída, versión del expediente) (§10).
5. Todo sigue siendo **solo contra nuestro portal de prueba en 127.0.0.1**, con datos sintéticos y
   confirmación humana obligatoria. **No validado en el MSI físico** (PENDIENTE).

## Advertencia de uso (sin cambios respecto de la v1.5)

La v1.6 conserva todas las barreras de la v1.5: hosts permitidos, "He revisado" y "Confirmo"
obligatorios, rutas de captura inexistentes en modo producción, y **ningún mecanismo para superar
CAPTCHA, límites de velocidad, detección de bots ni restricciones de pegado**. Lo nuevo de esta
fase va en la misma dirección: los campos que el portal marca como de captura manual los
**teclea el operador** y el motor no los guarda; el panel respeta que el portal no deje pegarlos.
La ventana nocturna 22:00-07:30 es un **parámetro de simulación** del laboratorio, no una
afirmación sobre ningún proveedor real.

## Qué está implementado, qué está probado y qué no se ha verificado

| Función | Implementado | Probado (cómo) | No verificado |
|---|---|---|---|
| Modo POR_DIA / POR_HORARIO configurable | Sí | Pruebas unitarias e integración; recorridos v1.5 (23/23) y v1.6 (27/27) | — |
| Calendario por día (5 estados visibles) | Sí | `test_calendario_motor.py`, panel en navegador real | Portal real (no existe) |
| Mecanismo A (eventos) y B (consulta) | Sí | Pruebas + laboratorio A/B con fallos (§9) | Redes lentas o con proxy |
| Zona horaria del portal | Sí | Prueba con motor en UTC y portal en Ciudad de México | Cambio de horario de verano en vivo |
| Asistente con campos manuales | Sí | Navegador real: error de un campo conserva los demás; se teclea, no se pega | Lectores de pantalla |
| 10 estados del expediente + coherencia | Sí | Pruebas de transición y `/captura/coherencia` en el laboratorio | — |
| Asignación por región, sucursal, alternativas, prioridad | Sí | Pruebas + laboratorio (10 operadores, 5 regiones) | — |
| Rechazo con motivo | Sí | `test_rechazo_con_motivo_y_recuperacion` | — |
| Confirmación segura (8 escenarios pedidos) | Sí | 8 pruebas, SQLite y PostgreSQL (§7) | — |
| Retención y privacidad | Sí | `test_retencion.py` (SQLite y PostgreSQL) | ACL de respaldos en Windows |
| Panel escritorio y teléfono | Sí | Capturas con Playwright, sin desborde a 390 px | Navegadores distintos de Chromium |
| Laboratorio multisucursal | Sí | §9 (MEDIDO con actores SIMULADOS) | Tiempos de personas reales |
| Windows | Sí | GitHub Actions en Windows limpio (§8) | **MSI físico (PENDIENTE)** |

## 1. Rama y commits

| Commit | Contenido |
|---|---|
| `e276785` | Portal de prueba con inventario POR DÍA, pantallas sin hora, eventos del servidor reanudables, reloj y ventana nocturna simulados |
| `e83a5a6` | Calendario por día en el motor (eventos y consulta periódica), conector `portal_dias`, contrato del formulario por día |
| `48e04b1` | Expedientes con 10 estados, flujo de reservación POR DÍA, rechazo con motivo, retención, panel |
| `3d280cb` | Recorrido en navegador real por día con reinicio y recuperación del folio; capturas |
| `f5a6c7e` | Laboratorio multisucursal A/B; corrección "no asignar con datos de una fuente que está fallando"; CI v1.6; guía y arquitectura |
| (último) | Este informe, resultados y ajustes finales |

Cambios en el núcleo del motor (`monitoreo/engine/`): **4 archivos, unas 85 líneas**. Son la tarea
del planificador, el motivo DECLINADA del rechazo y el filtro de fuentes caídas en la reevaluación.
Lo demás vive en `monitoreo/captura/`, `portal_reservas/` y el panel.

## 2. Código nuevo o cambiado

| Módulo | Qué hace |
|---|---|
| `portal_reservas/dias.py`, `servidor_dias.py` | Inventario por día del portal de PRUEBA, 3 pantallas sin hora, API de días con versión, eventos del servidor con secuencia, reloj y ventana nocturna de laboratorio |
| `monitoreo/captura/calendario_dias.py` | Aplica lo que dice el portal comparando **versiones**, clasifica cada transición y guarda la última verificación real |
| `monitoreo/captura/eventos_portal.py` | Oyente del mecanismo A: reanuda por secuencia, resincroniza ante un hueco, detecta latido perdido y sesión caducada |
| `monitoreo/connectors/portal_dias.py` | Conector del núcleo: usa eventos si están sanos; si no, consulta completa (B) |
| `monitoreo/captura/zona.py` | "Hoy" en la zona que declara el portal |
| `monitoreo/captura/expedientes.py` | 10 estados, transiciones validadas, coherencia, duplicados, tres identificadores separados |
| `monitoreo/captura/reservaciones.py` | Preparar, enviar, elegir día, confirmar con evidencia, EN_VERIFICACION y recuperación |
| `monitoreo/captura/seguimiento.py` | Refleja asignaciones en el expediente; rechazo con motivo; mantenimiento |
| `monitoreo/captura/retencion.py` | Anonimizar o borrar según plazos, respaldos protegidos y purgados |
| `monitoreo/migrations/versions/0007_*` | Columnas nuevas, traducción de estados de la v1.5, tablas de calendario. No borra datos |
| `web/captura.js`, `web/app.css` | Resumen con indicadores, calendario mensual por sucursal, recorrido visual, asistente, móvil |
| `scripts/demo_dia.py`, `recorrido_dia.js` | Demostración verificable con navegador real |
| `scripts/laboratorio_dias.py` | Laboratorio multisucursal A/B con fallos |

## 3. Calendario por día

- Unidad: **región + sucursal + trámite + fecha**. No hay hora en ninguna pantalla ni en la API.
- Estados que ve el operador: **Disponible**, **Agotado**, **No habilitado**, **Pendiente de
  actualización** (sin verificar hace más de `calendario_frescura_s`, 60 s por defecto) y
  **Desconocido** (sin comunicación). Un fallo de comunicación **nunca** se muestra como agotado:
  el panel dice "Se muestra lo ALMACENADO; última verificación real: … No es falta de
  disponibilidad" (captura `30_calendario_sin_conexion.png`).
- Transiciones detectadas y guardadas: apertura, reapertura, cambio de unidades, agotamiento,
  deshabilitado, rehabilitado, expiración, retirada, desconexión, reconexión, sesión caducada,
  resincronización y **fuera de orden** (un evento con versión vieja se descarta y se cuenta).
- El panel distingue la hora de la **última verificación real** de la hora en que se guardó el dato.

## 4. Asistente con campos manuales

Las tres pantallas del portal de prueba (datos, calendario, confirmación) están descritas en el
contrato `portal_laboratorio_dia.json`. Los campos marcados `MANUAL_REQUIRED` (clave de identidad,
aceptación del aviso de privacidad) aparecen en un recuadro aparte: "la teclea el operador". El
motor los valida (formato y dependencias), los manda al portal **solo en memoria** y no los
guarda ni los registra; si la sesión del portal caduca, el operador los vuelve a teclear. Si uno
falla, solo ese campo se marca y lo demás se conserva (captura `03_campo_manual_con_error.png`).
Las confirmaciones de correo y teléfono **no se guardan**: se comparan al crear y se descartan.

## 5. Expedientes y asignaciones

- Estados: NUEVO, INCOMPLETO, EN_VALIDACION, LISTO, ASIGNADO, CAPTURA_EN_PROCESO,
  RESERVACION_PENDIENTE, RESERVACION_CONFIRMADA, REQUIERE_REVISION y CERRADO (con motivo).
- **Un incompleto no se asigna nunca**: la solicitud del motor solo se crea en LISTO.
- Asignación por región, sucursal preferida y alternativas (hasta 5), trámite, prioridad y
  antigüedad. Un operador puede limitarse a sucursales concretas y no ve otras.
- **Nunca dos operadores en la misma solicitud** (asignación con UPDATE condicional; prueba con 4
  hilos simultáneos y comprobación en el laboratorio).
- Rechazo con motivo (NO_DISPONIBLE, CARGA_EXCESIVA, CONFLICTO_DE_INTERES, DATOS_DUDOSOS, OTRO).
  La nota se revisa para que no lleve teléfonos ni correos. DATOS_DUDOSOS manda el expediente a
  revisión humana; los demás lo devuelven a la cola sin ofrecérselo otra vez a ese operador.

## 6. Política de retención y privacidad

| Regla (parámetro) | Por defecto | Acción |
|---|---|---|
| `retencion_confirmados_dias` | 90 | Anonimizar (se conserva folio, código, fechas y auditoría) |
| `retencion_cerrados_dias` + `retencion_accion` | 30 · ANONIMIZAR | Anonimizar, o BORRAR solo si nunca llegó al portal |
| `retencion_incompletos_dias` | 60 | Cerrar (motivo RETENCION) y anonimizar |
| `retencion_respaldos_dias` | 30 | Borrar respaldos viejos; siempre quedan los 3 últimos |

Los plazos van de 1 a 3650 días: la política se puede alargar, **no apagar**. Nunca se toca un
expediente con operación activa ni con reservación apartada o en verificación. El administrador
ve una vista previa antes de aplicarla a mano, y la aplicación exige confirmarla y queda auditada.
La exportación sale enmascarada por defecto. La completa exige un motivo, queda auditada sin datos
personales y se envía con `Cache-Control: no-store`. Los respaldos se crean con permisos solo para
el dueño (Linux; en Windows heredan los de la carpeta del usuario).

**Límite que conviene conocer:** un dato anonimizado sigue existiendo en los respaldos anteriores
hasta que estos se purgan (30 días por defecto, y siempre se conservan los 3 últimos).

## 7. Pruebas

<<SUITE_DETALLE>>

Pruebas nuevas de la v1.6 (cada una corre en SQLite y en PostgreSQL cuando usa base de datos):

| Archivo | Pruebas | Qué cubre |
|---|---|---|
| `test_portal_dias.py` | 10 | Pantallas sin hora, campos manuales, reprogramación, última unidad, idempotencia, versiones, expiración y ventana nocturna, eventos reanudables, hueco, estado v1.5 |
| `test_calendario_motor.py` | 6 | Todas las transiciones, fuera de orden, **error de conexión ≠ agotado**, disponibilidad que vence, B sin inventar, A con corte, reconexión y sesión caducada |
| `test_captura_dia.py` | 15 | Recorrido hasta folio; incompleto no se asigna; **día que se agota antes de confirmar; última unidad con dos solicitudes; confirmación repetida; respuesta perdida; reinicio a mitad de la confirmación; error del servidor; folio duplicado; sesión caducada**; reprogramar; rechazo; sucursales del operador; asignaciones simultáneas; sin datos personales en alertas ni auditoría |
| `test_retencion.py` | 3 | Anonimizar, borrar y respetar operaciones activas; respaldos; exportación protegida |
| `test_assignment.py` | +1 | No asignar con datos de una fuente cuya última consulta falló |

Pruebas de la v1.5 que cambiaron (y por qué):
- `test_falta_dato` (v1.5) asignaba un expediente incompleto; **eso contradice el requisito nuevo**
  "no asignar expedientes incompletos". Se reescribió: INCOMPLETO → no se asigna → se completa →
  LISTO → ASIGNADO.
- Los nombres de estado cambiaron (`CONFIRMADO` → `RESERVACION_CONFIRMADA`, etc.) y los
  expedientes de esas pruebas declaran `modo: POR_HORARIO`.
- El recorrido de la v1.5 abre ahora la pestaña *Expedientes* de forma explícita, porque la
  pestaña inicial del panel pasó a ser *Resumen*.

## 8. GitHub Actions

<<CI>>

## 9. Laboratorio multisucursal (A = eventos, B = consulta cada 10 s)

<<LAB>>

## 10. Defectos encontrados y corregidos

| # | Defecto | Gravedad | Cómo se encontró | Corrección y prueba |
|---|---|---|---|---|
| 1 | Con el motor en UTC y el portal en Ciudad de México, "mañana" aparecía como *no habilitado* entre las 18:00 y las 24:00 locales | Alta: ocultaba disponibilidad real | Prueba de calendario | "Hoy" se calcula en la zona que declara el portal (`calendario_fuentes.zona_horaria`, migración 0007) |
| 2 | En PostgreSQL, el oyente de eventos y la consulta periódica podían crear a la vez la fila de estado de la fuente (`UniqueViolation`) | Media: un ciclo fallido | Suite en PostgreSQL | Punto de guardado y relectura; prueba de eventos con corte |
| 3 | La reevaluación periódica asignaba con el último estado guardado aunque la última consulta de la fuente hubiera fallado | Media: operadores avisados de un cupo no verificado (no se reservaba nada: cada paso vuelve a leer el portal) | Revisión del código al redactar la arquitectura | `free_slots` exige que la fuente no esté fallando; prueba que falla sin la corrección |
| 4 | La versión del expediente quedaba desalineada tras validar al crear (409 en la primera edición) | Baja | Prueba de modificación | `crear` fija `version=1` tras la validación en línea |

Defectos de las propias pruebas (corregidos para que no den falsos positivos):
- La prueba de asignaciones simultáneas era **vacía**: pasaba con 0 asignaciones. Ahora valida
  las solicitudes antes y exige al menos 2.
- La demostración comprobaba el mecanismo A antes de que el oyente conectara; ahora espera a que
  conecte (3 s medidos) en vez de aflojar la comprobación.
- El laboratorio podía pasar con 0 confirmaciones; ahora exige confirmaciones reales.
- Un comentario de la retención decía "0 desactiva la regla", pero los parámetros no lo permiten.
  Se corrigió el comentario: la política no se puede apagar, a propósito.

## 11. Capturas del recorrido

En `docs/capturas/v1.6/` (Chromium real, 1366×900 y 390×844):

| Captura | Qué muestra |
|---|---|
| `01_resumen_operador.png` | Resumen del operador |
| `02_ficha_asignada_recorrido.png` | Expediente asignado con el recorrido visual |
| `03_campo_manual_con_error.png` | Clave mal tecleada: solo ese campo se marca, lo demás se conserva |
| `04_campos_manuales_listos.png` | Campos manuales tecleados |
| `05_calendario_mensual.png` | Calendario del mes con días disponibles, agotados y no habilitados (sin horas) |
| `06_confirmacion_humana.png` | Confirmación: el botón no se activa sin la casilla |
| `07_folio.png` | Folio y fecha confirmada |
| `08_folio_tras_reinicio.png` | El folio sigue ahí tras matar motor y portal |
| `10`–`14_panel_*.png` | Resumen con indicadores, calendarios por sucursal, expedientes, confirmaciones, historial |
| `20_movil_resumen.png`, `21_movil_calendario.png` | Teléfono, sin desborde horizontal |
| `30_calendario_sin_conexion.png` | Calendario sin comunicación: DESCONOCIDO, no "agotado" |

Resultado de la demostración: `docs/resultados/v1.6/demo_dia.json` (27/27, MEDIDO).

## 12. Instrucciones de actualización

Ver [GUIA_RAPIDA_WINDOWS.md](GUIA_RAPIDA_WINDOWS.md), apartado B (actualizar desde v1.5) y D (probar
la reservación por día). En resumen: `actualizar.ps1 -Anterior C:\motor-monitoreo-v1.5` en la
carpeta nueva; hace respaldo, aplica la migración 0007 (no borra nada; los expedientes de la v1.5
siguen *por horario*) y comprueba la integridad. Recomendado añadir
`MONITOREO_ZONA_HORARIA=America/Mexico_City` al `.env`. CI prueba esta actualización desde la v1.5
en Windows limpio.

## 13. Riesgos pendientes

| Riesgo | Gravedad | Recomendación | Cómo comprobarlo |
|---|---|---|---|
| **No probado en el MSI físico** | Alta hasta probarlo | Instalar y ejecutar `scripts\demo_dia.py` en el equipo | 27/27 en su `demo_dia.json` |
| Límite de peticiones **por IP** (600/min, 120 escrituras/min): varios operadores detrás de la misma IP (proxy o NAT sin `X-Forwarded-For`) lo comparten | Media | En oficina, cada equipo con su IP o proxy declarado en `MONITOREO_PROXY_CONFIABLE` | En el laboratorio sin ampliarlo aparecieron respuestas 429 con 10 operadores en 127.0.0.1 |
| Los datos anonimizados persisten en respaldos anteriores hasta su purga | Media (privacidad) | Acortar `retencion_respaldos_dias` si el plazo legal lo exige | Lista de respaldos tras la purga |
| Respaldos en Windows: no se cambian permisos (heredan los de la carpeta del usuario) | Media | Instalar fuera de carpetas compartidas | `icacls data\respaldos` |
| El laboratorio usa operadores simulados por la API: sus tiempos de preparación **no** representan personas | Media (interpretación) | No usar esas cifras para planear personal | Piloto con personas |
| En la noche simulada el reloj del portal se acelera y el del motor no: cerca de medianoche discrepan en qué día es "hoy" | Baja (solo laboratorio) | Ninguna en uso normal (ambos relojes reales) | — |
| Contadores estadísticos de la fuente (eventos recibidos, reconexiones) pueden perder algún incremento entre hilos | Baja (solo estadística) | Aceptado; no afecta decisiones | — |
| `disponible_hasta` se interpreta con el reloj del portal: si el reloj del servidor del motor está muy desfasado, un día puede verse vigente unos segundos de más | Baja | NTP en el servidor | La confirmación siempre vuelve a leer el portal |
| Prueba de temporización `test_ciclo_despertar` sensible a carga (0,52 s frente a 0,4 s una vez) | Baja | Vigilar en CI | Repetición aislada pasa |
| Conectar un portal real | Fuera de alcance | Solo con autorización escrita del dueño, sus términos y su API oficial | — |

## Cómo reproducir

```bash
python -m pytest -q                                   # SQLite (y PostgreSQL con MONITOREO_TEST_PG)
python scripts/demo_dia.py --salida resultados/demo_dia --capturas docs/capturas/v1.6
python scripts/laboratorio_dias.py --mecanismo ambos --expedientes 600 --operadores 10 --noche-s 300
python scripts/demo_reservacion.py                    # recorrido de la v1.5 (por horario)
```
