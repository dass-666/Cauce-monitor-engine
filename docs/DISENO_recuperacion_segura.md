# Diseño mínimo · Recuperación segura tras restaurar un respaldo (propuesta, sin implementar)

Fecha: 2026-11-19. Base: rama `claude/recorrido-integral-v2.3`.

Convención: **HECHO** (lo verifiqué en el código), **INFERENCIA**, **PROPUESTA**.

## 1. El problema, con lo que hoy hace el código (HECHO)

- `python -m monitoreo.migrate restaurar` sustituye la base completa. Antes guarda un respaldo
  `antes-de-restaurar` en `data\respaldos\` (`migrate.py`, función `restore`).
- Todo lo que ocurrió después del respaldo desaparece de la base. Lo demostró el recorrido integral:
  - Una **revocación posterior** desaparece y el consentimiento vuelve a VIGENTE. Es el caso grave, porque
    el sistema podría volver a operar contra la voluntad de la persona.
  - Una **confirmación posterior** desaparece del motor, aunque el portal sí la tiene.
- En el laboratorio no se duplicó la cita por dos razones:
  1. la clave de confirmación la entrega el **portal** (`reservaciones.py`, líneas 435-441);
  2. el portal de laboratorio devuelve la misma reservación si se repite esa clave
     (`portal_reservas/estado.py`).

  **No hay que suponer que el proveedor real haga lo mismo.**
- Todas las operaciones que escriben en el portal (retener, enviar datos, confirmar, cancelar) pasan por un
  mismo punto de control: `_exigir_proveedor(s, res)` en `captura/reservaciones.py` (líneas 183, 209, 576
  y 664). Es el lugar natural para un bloqueo.
- Hoy no existe ningún registro de eventos fuera de la base. `security.audit` escribe en la tabla
  `audit_log`, que también retrocede al restaurar.

## 2. Diseño propuesto: cuatro piezas pequeñas

### 2.1 Diario de eventos críticos fuera de la base (punto 1)

- **Qué es.** Un archivo de solo añadir, `data\diario\eventos-criticos.jsonl`: una línea JSON por evento,
  encadenada con SHA-256 (cada línea lleva la huella de la anterior), igual que los sellos de
  consentimiento.
- **Qué eventos.** Solo aquellos cuya pérdida es **peligrosa**:

  | Evento | Cuándo se escribe | Qué guarda |
  |---|---|---|
  | `REVOCACION` | después de confirmar la transacción de `consentimiento.revocar` | código del expediente, id del consentimiento, sello del evento, medio, hora |
  | `CONFIRMACION_INTENTO` | después de guardar CONFIRMANDO y **antes** de llamar al portal | código, id de la reservación, clave, sucursal, fecha, hora |
  | `CONFIRMACION_RESULTADO` | al quedar CONFIRMADA o rechazada | id de la reservación, folio o código de error |
  | `CANCELACION_INTENTO` / `_RESULTADO` | igual, en `cancelar` | id de la reservación, folio |

- **Sin datos personales.** El diario no guarda nombre, teléfono, correo ni fecha de nacimiento. Solo
  códigos, ids, sellos y folios, que la retención ya conserva. Por eso **no** necesita anonimizarse.
- **Por qué solo esos eventos.** Perder un alta o un consentimiento nuevo deja el sistema **más
  restrictivo**, y eso es seguro. Perder una revocación o una operación en el portal lo deja **menos**
  restrictivo, o desconectado de lo que ya ocurrió fuera.
- **Ubicación.** Va fuera de `data\respaldos\` y no lo toca ni la restauración ni la purga de respaldos.
- **Copia externa opcional.** Una variable de configuración, `MONITOREO_DIARIO_COPIA`, permitiría
  escribir una segunda copia en otra unidad (USB o carpeta de red). Ver el límite del punto 4.
- **Formato.** Elegí JSONL frente a un segundo archivo SQLite porque se lee con el Bloc de notas, se audita
  y se copia fácilmente. Un corte en mitad de una escritura deja como mucho una última línea incompleta,
  que se ignora y se señala.

### 2.2 Detección al arrancar (punto 2)

- **Marca de restauración.** `restaurar` escribe `data\diario\RESTAURADO.json` con la hora y el nombre del
  respaldo. Es una línea más en `migrate.py`.
- **Comparación.** Al arrancar, el motor recorre el diario y comprueba que la base contiene cada evento:
  - una revocación: que exista el evento de consentimiento con ese sello;
  - una confirmación: que la reservación esté CONFIRMADA con ese folio;
  - un intento sin resultado: que la reservación esté en CONFIRMANDO o EN_VERIFICACION.

  Cada evento ausente es una **discrepancia**. Con 6 a 50 eventos al día, el recorrido tarda
  milisegundos.
- **Respaldo previo como apoyo.** El respaldo `antes-de-restaurar`, que ya existe, se cita como evidencia
  secundaria en el informe de conciliación.

### 2.3 Bloqueo de operaciones externas (punto 3)

- **Cuándo.** Después de **cualquier** restauración, aunque no haya discrepancias, el motor queda en modo
  `RECUPERACION_PENDIENTE` hasta que un administrador lo revise. También entra en ese modo si encuentra
  discrepancias sin marca de restauración, por ejemplo si alguien copió la base a mano. Es la opción
  conservadora y cuesta poco.
- **Qué se bloquea.** Las escrituras en el portal: retener, enviar datos, confirmar y cancelar. Se hace con
  **una sola comprobación** añadida a `_exigir_proveedor`. Responde 409 con el código
  `RECUPERACION_PENDIENTE` y un mensaje claro para el operador.
- **Qué sigue funcionando.** Ventanilla, el alta de expedientes, el consentimiento, el plan, el observador
  y las consultas. El observador solo lee el calendario, así que la disponibilidad se sigue viendo y no se
  pierde nada. Lo único que no se puede es operar en el portal.
- **Dónde se guarda el estado.** En la tabla `settings`, como un estado más del motor, y también en la
  marca `RESTAURADO.json`. Si alguien volviera a restaurar, la marca reactivaría el bloqueo.

### 2.4 Conciliación supervisada y auditada (punto 4)

- **Informe.** Un administrador ve el informe de discrepancias en una tarjeta de autodiagnóstico y por la
  API `GET /api/recuperacion`. Resuelve cada discrepancia con una acción explícita y un motivo escrito:

  | Discrepancia | Acciones posibles |
  |---|---|
  | Revocación ausente | **Reaplicar la revocación** (obligatorio antes de desbloquear). Usa `consentimiento.revocar`, que ya existe: se añade un evento nuevo a la cadena, con la referencia de la línea del diario. La cadena sigue íntegra. |
  | Confirmación con folio ausente | El administrador **comprueba en el portal** y registra el resultado: «existe con folio X» (la reservación pasa a CONFIRMADA con evidencia `conciliacion_manual`) o «no existe». |
  | Intento sin resultado | Se marca EN_VERIFICACION, un estado que ya existe. Lo resuelve una persona; **no** se reintenta solo. |

- **Sin idempotencia del proveedor.** Ninguna acción vuelve a confirmar de forma automática. La
  verificación en el portal la hace una persona. En el laboratorio se le puede mostrar lo que dice el
  portal como ayuda, pero la decisión es humana.
- **Auditoría.** Cada resolución queda en `audit_log` y también como una línea `CONCILIADO` en el diario.
- **Desbloqueo.** `POST /api/recuperacion/levantar` solo se acepta con 0 discrepancias pendientes, y queda
  auditado con usuario, hora y motivo.

## 3. Compatibilidad con lo que ya existe

| Área | Efecto |
|---|---|
| Laboratorio y demostraciones (demo_v22, aceptacion_v21, recorrido_ventanilla_v23) | Ninguno en el flujo normal: el diario coincide con la base y no hay bloqueo. Solo se añaden líneas al diario. |
| `recorrido_integral` | **Cambia a propósito.** Después de restaurar, la confirmación de B debe dar 409 `RECUPERACION_PENDIENTE`. Luego el administrador concilia (reaplica la revocación de G y registra el folio F2) y levanta el bloqueo. Esa es la demostración nueva. |
| Reglas de consentimiento | Intactas. La reaplicación usa la misma función `revocar` y la cadena de sellos se verifica igual. |
| Retención | Sin cambios, porque el diario no tiene datos personales. `purgar_respaldos` no toca `data\diario\` (otra carpeta). |
| Supervisor, planificador y observador | Sin cambios. El observador sigue leyendo. |
| Contratos de Jarvis | Sin cambios. |
| Pruebas | Usan `MONITOREO_DATA_DIR` temporal desde la v2.2.2, así que el diario no escribe en el `data\` del proyecto. **Hay que comprobarlo**: la guarda `data_sin_cambios` del ejecutor lo detectaría. |
| Windows | Hace falta añadir con `fsync` en cada línea. Windows lo admite (`os.fsync`). El antivirus podría retrasar la escritura (INFERENCIA, PENDIENTE de medir en el equipo físico). |
| PostgreSQL con varias instancias | **Limitación.** Cada máquina tendría su propio diario. La detección revisaría el de la máquina que arranca. Para este proyecto, con una sola máquina Windows y SQLite, basta. Con varias instancias habría que compartir la carpeta del diario. Queda fuera del alcance mínimo. |

## 4. Límites honestos

- **Si se pierde el disco**, se pierden la base y el diario a la vez. El diario protege frente a una base
  dañada, una migración fallida o un error humano, pero no frente a la pérdida del equipo. Para eso sirve
  la copia externa opcional (§2.1). Recomiendo activarla en el equipo físico con una USB o una carpeta de
  red, como decisión de operación.
- **La ventana entre la base y el diario.** Si el motor cae después de confirmar la transacción y antes de
  escribir el diario, falta esa línea. Para una revocación no hay riesgo, porque la base sí la tiene. Para
  una confirmación, el intento se escribe en el diario **antes** de llamar al portal, así que nunca hay una
  llamada al portal sin línea en el diario.

## 5. Tamaño y plan

- **Cálculo (INFERENCIA):** unas 300 a 400 líneas, repartidas así:
  - `monitoreo/recuperacion.py`: diario, detección y conciliación (~200);
  - enganches en `revocar`, `confirmar`, `cancelar` y `restore`, y la comprobación en `_exigir_proveedor`
    (~30);
  - API (~60);
  - una tarjeta en el autodiagnóstico (~40);
  - pruebas.
- No es un subsistema grande: no lleva tablas nuevas, servicios nuevos ni dependencias nuevas.

Orden propuesto, cada paso verificable:

1. Diario, marca de restauración y bloqueo en `_exigir_proveedor`, con pruebas unitarias:
   - una revocación tras un respaldo y la restauración deben dar un bloqueo con 1 discrepancia;
   - una confirmación tras un respaldo debe dar un bloqueo con un folio ausente;
   - el flujo normal no debe bloquear.
2. Conciliación por API y desbloqueo auditado.
3. `recorrido_integral` actualizado para demostrar bloqueo → conciliación → desbloqueo → continuidad.
4. Tarjeta del administrador y pasos en la guía de Windows físico.

**Decisión que te toca:** si la copia externa del diario entra ya en el paso 1, o queda como
configuración posterior para el equipo físico.
