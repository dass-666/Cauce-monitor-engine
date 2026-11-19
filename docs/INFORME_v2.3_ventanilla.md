# Informe v2.3 · Ventanilla operativa de principio a fin (laboratorio)

Fecha: 2026-11-19. Rama `claude/project-thread-2wwz8e` (desde `claude/integracion-v2.2.2` @ `2546ed1`,
sin tocar esa rama). Sin fusionar. Convención: **HECHO** (verificado con evidencia), **INFERENCIA**,
**PENDIENTE**.

## 1. Punto de partida (HECHO)

- `claude/integracion-v2.2.2`: código fijado para Jarvis en `9f79a3c`, punta `2546ed1` (solo documentación).
  Su CI final seguía en curso al empezar; no se modificó.
- Esta rama no cambia `jarvis/` (contratos, manifiesto, `project.yaml`). `integracion.rama` sigue apuntando
  a `claude/integracion-v2.2.2`; la guarda del fijado se omite fuera de esa rama (comportamiento de v2.2.2).

## 2. Qué podía hacer Ventanilla antes (HECHO, por lectura de código y prueba)

Desde la pantalla ya funcionaban: atender a una persona (buscar o registrar), consentimiento en el alta o
en su paso, revocación, aviso de duplicado EXACTO con «Atender este», plan del día (crear y llenar con
«Completar con expedientes listos», solo admin), «Preparar operación» de una coincidencia, campos
manuales, calendario del portal, confirmación con folio comprobado, cierre y «Retomar».

Lo que **solo** existía por API o script (comprobado: ninguna pantalla llamaba esas rutas):

| Paso del recorrido | Hueco |
|---|---|
| 6. Incorporar al plan | Cerrar «sin cita» devolvía el expediente a la cola general, que lo **asignaba** antes de que el plan lo tomara (lo demuestra `test_sin_disponibilidad_devuelve_a_la_cola_y_el_motor_sigue`). No había forma de pasar a la persona de su atención al plan. |
| 2/8. Observador | `POST/DELETE /api/observador` sin pantalla: sin observador el plan nunca recibe fechas. |
| 8. Calendario del laboratorio | Abrir un día solo con `/control/dias/...` del portal y su token (scripts). |
| 5. Duplicado PARECIDO | `posibles-duplicados` y `duplicados/descartar` sin pantalla. |
| 10. Historial | `/api/expedientes/{id}/trazabilidad` sin pantalla (la ficha del admin mostraba solo el historial del expediente). |
| Alta de personas de prueba | Los registros ficticios que el portal acepta solo se obtenían con `/control/registro-ejemplo`. |

## 3. Qué puede hacer ahora (HECHO: recorrido con navegador real, 20/20)

1. **«Dejarla en el plan del día»** en la atención: fechas aceptables opcionales; cierra la atención sin
   cita (resultado `EN_PLAN_DEL_DIA`) y el expediente entra al plan abierto de hoy con las mismas reglas
   de `plan.agregar`. Antes de soltar nada comprueba plan abierto, capacidad, fechas, sucursales y
   consentimiento; si el plan lo rechaza en el último instante, la persona vuelve a esta atención.
2. **Observador del calendario** en Ventanilla (admin): iniciar (sucursal, trámite, mes), estado y detener.
3. **Calendario lab** (admin): calendario del mes del portal propio con Abrir / Agotar / Liberar y la
   lista de registros ficticios. Solo funciona si el motor se abrió con `MONITOREO_PORTAL_TOKEN_CONTROL`
   y el portal está en 127.0.0.1; el token nunca se envía a otro equipo; cada acción queda en auditoría.
4. **Posibles duplicados por revisar** (admin): «Son personas distintas» (con motivo) o «Es la misma
   persona: cancelar este».
5. **Historial completo** del expediente en la atención y al cerrarla: atenciones, plan, operaciones con
   el portal y folio, consentimiento con su cadena de sellos y la línea de tiempo.
6. `ventanilla_laboratorio.bat` / `scripts/ventanilla_laboratorio.py`: abre portal + motor + admin +
   operador en una carpeta propia (no toca `data\`), y conserva el estado entre aperturas.

## 4. Defectos funcionales corregidos (HECHO, reproducidos con el navegador)

| Defecto | Efecto para el operador | Arreglo |
|---|---|---|
| Expediente en revisión (duplicado parecido) o incompleto: su ficha da 404 al operador y la atención volvía a la lista **sin ningún mensaje** | No sabía qué pasó | Tarjeta que explica el motivo y quién decide; se puede cerrar y retomar |
| Una atención sin expediente (abierta por error o duplicado exacto) no tenía forma de cerrarse | Quedaba abierta para siempre | Se muestra «Terminar sin cita» |
| El ejemplo de «Dónde está el original» (`Carpeta 2026-11, hoja 14`) lo **rechaza** el validador por la coma | Quien seguía el ejemplo recibía un error | Ejemplo sin coma y el mensaje dice qué caracteres valen. La regla de validación **no** cambió |
| «Volver a la lista (la atención queda abierta)» en atenciones ya cerradas | Mensaje falso | Texto según el estado |

## 5. Pruebas (HECHO)

| Qué | Resultado |
|---|---|
| `tests/test_recorrido_v23.py` (nuevo: plan desde la atención, rechazos sin efectos, permisos, calendario del laboratorio con y sin token, `.bat`) | 8/8 (SQLite y PostgreSQL 16 local) |
| Suites afectadas: `test_ventanilla`, `test_consentimiento_v22`, `test_planificador`, `test_plan_unidades_propiedad` + nuevas | 130/130 SQLite + PostgreSQL |
| `test_ventanilla`, `test_consentimiento_v22`, `test_planificador`, `test_plan_unidades_propiedad`, `test_cierre_v21`, `test_observador_persistente`, `test_api` (solo SQLite) | 89/89 |
| `scripts/aceptacion_v21.py` (Ventanilla «Plan del día» con navegador) | 42 OK, 0 fallas |
| `scripts/demo_v22.py` (consentimiento con navegador) | 36 OK, 0 fallas |
| `scripts/recorrido_ventanilla_v23.py` (nuevo, ver §6) | 19/19 en 3 ejecuciones seguidas; tras la corrección, 20/20 (comprobación nueva de errores HTTP) |
| `scripts/demo_v20.py` (Ventanilla v2.0 con navegador), tras la corrección | 56 OK, 0 fallas |
| Suite completa local (SQLite), tras la corrección | 593 pasan, 8 omitidas, 0 fallan |
| CI de GitHub, 1.ª corrida (44471952998 / 44472069000) | **FALLÓ por dos defectos míos** (ver §5.1); corregidos y vueltos a subir |
| CI de GitHub tras la corrección (corrida 44478520364, `b34fb47`) | **29 de 30 trabajos en verde**: suites completas Linux 3.11/3.12/3.13 y Windows 3.11/3.12/3.13, Windows+PostgreSQL, integración HTTP, instalación, actualizaciones, Jarvis y regresión v2.2.2. El recorrido v2.3 pasó en Linux y en Windows 3.12 y 3.13 (en Windows 3.11 no llegó a ejecutarse, ver la fila siguiente) |
| El trabajo que falló: Reservaciones · Windows · Python 3.11 | `demo_v22`, paso 6 (revocar con el día elegido: `intento_descartado: None`). **INFERENCIA:** intermitente ajeno a la v2.3: ese paso solo usa la API de consentimiento y reservaciones, que esta rama no cambia; el mismo paso pasó en Windows 3.12 y 3.13, en Linux (SQLite y PostgreSQL) y en local. No lo reproduje ni lo demostré |

### 5.1 Lo que encontró la CI (HECHO) y cómo se corrigió

1. `test_engine_core_has_no_provider_specific_code` (todas las suites completas): el núcleo `monitoreo/` no
   debe conocer el protocolo de control del portal y mi intermediario escribía la cabecera `X-Control-Token`.
   Es una regla de arquitectura correcta: **no** se tocó la prueba; la cabecera pasó a
   `portal_reservas/control.py` (el propio portal de laboratorio) y el motor solo llama a esa función.
2. `demo_v20` («respuestas HTTP de error: solo las esperadas», Windows/Linux e instalación pwsh): al cerrar
   una atención SIN cita, la pantalla pedía el historial y la ficha de un expediente que el operador ya no
   tiene a su cargo, y eso daba 404. Era ruido nuevo causado por v2.3. Ahora la atención informa
   `operador_id` del expediente y la pantalla solo pide ficha e historial cuando el usuario puede verlos;
   si no, lo explica sin llamar al servidor. El recorrido v2.3 también comprueba ahora que no haya errores
   HTTP inesperados (20 comprobaciones).

Lección: antes de subir debí ejecutar también `demo_v20` y la suite completa, no solo las suites afectadas.

No se cambiaron umbrales, criterios ni pruebas existentes. No se añadieron benchmarks ni pruebas de estrés.

## 6. Demostración reproducible

`recorrido_ventanilla.bat` o `python scripts/recorrido_ventanilla_v23.py`. Dos navegadores (admin y
operador), solo clics y teclas; después el script comprueba por fuera (portal y trazabilidad) que lo que
dijo la pantalla es cierto. Evidencia: `docs/resultados/v2.3/recorrido_v23.json`, capturas en
`docs/capturas/v2.3/` (20). Resultado de la ejecución guardada: expediente `EXP-000001`, día abierto
en el calendario del laboratorio `2027-01-15`, folio `DIA-20270115-00001P` CONFIRMADO en el portal,
cadena de consentimiento íntegra, el parecido cancelado por el admin, registros sin los datos tecleados.

La **confirmación humana** de la cita la hace el script: es una SIMULACIÓN y así consta en el JSON.

## 7. Lo que todavía requiere acción manual o script

- Arrancar el laboratorio: un doble clic (`ventanilla_laboratorio.bat`), no programación.
- Crear plan, observador, calendario y duplicados: rol **administrador** (decisión previa: «el plan lo
  administra el admin»). El operador no puede crear el plan; sí dejar a SU persona en el plan abierto.
- Retención/anonimización, respaldo y restauración siguen por consola (`python -m monitoreo.migrate`,
  `captura/retencion.py`): no forman parte del recorrido pedido y no se tocaron.
- El historial deja de verlo el operador cuando la persona pasa al plan (ya no está a su cargo); lo ve
  el administrador y vuelve a verlo el operador que la atiende después.

## 8. Decisión que conviene que revises

**Permiso nuevo y acotado:** un operador puede pasar al plan **solo** el expediente de su propia atención
abierta, **solo** al plan ya abierto de hoy y respetando su capacidad. Crear, ampliar o llenar el plan
sigue siendo solo del administrador. Alternativa si no lo quieres: que solo el admin use el botón; el
operador cerraría «sin cita» y el riesgo es que la cola general lo asigne antes que el plan.

## 9. Qué falta para Windows físico (PENDIENTE)

- Nada de esto se ha ejecutado en el MSI. CI ejecuta el recorrido en `windows-latest` (servidor, no MSI).
- En el MSI: `instalar.ps1`, `python scripts\preparar_navegador.py`, `recorrido_ventanilla.bat` (anotar
  el JSON) y luego `ventanilla_laboratorio.bat` haciendo el recorrido a mano con la guía
  `docs/GUIA_RECORRIDO_VENTANILLA_v2.3.md`.
- **INFERENCIA:** `Ctrl+C` en la ventana de `ventanilla_laboratorio.bat` detiene motor y portal (el motor
  corre en su propio grupo de procesos y se le envía Ctrl+Break, como en los otros scripts). No comprobado
  en Windows.

## 10. Riesgos y siguiente trabajo

1. (Medio) El permiso de §8: confirmarlo o retirarlo.
2. (Bajo) Ventanilla no se refresca sola (diseño de v2.0 para no borrar lo tecleado): las coincidencias
   nuevas aparecen al volver a la lista o con «Actualizar estado».
3. (Bajo) En el historial capturado justo al cerrar, el elemento del plan aún dice «Operación preparada»;
   **INFERENCIA:** pasa a CONFIRMADO en la siguiente sincronización del planificador (así lo comprueba
   `demo_v22`), no lo verifiqué en este recorrido.
4. Siguiente prioridad: ejecutar el recorrido en el MSI físico; después decidir si el recorrido entra al
   manifiesto de Jarvis (exige volver a fijar, no se hizo).
