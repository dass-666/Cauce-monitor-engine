# Informe v2.4 · Recuperación segura tras restaurar un respaldo

Fecha: 2026-11-19. Rama `claude/project-thread-6fxbwl` (la de este hilo; avanza en línea recta desde la remota), con base `e63d5e6`. Es la misma base que la rama
de la RC1 administrativa (recorrido integral v2.3 + Ventanilla v2.3.1). Sin fusionar y sin desplegar. No se
tocó la rama de la RC1 ni los contratos de Jarvis (`jarvis/`).

Convención: **HECHO** (comprobado), **INFERENCIA** (razonable, sin demostrar), **PENDIENTE**.

## 1. Qué se entrega

| Archivo | Qué es |
|---|---|
| `monitoreo/recuperacion.py` (nuevo) | Diario firmado, clave, copia externa, detección, bloqueo, conciliación, consola. |
| `monitoreo/api/recuperacion.py` (nuevo) | API del administrador: estado, resolver, levantar y reiniciar el diario. |
| `monitoreo/captura/reservaciones.py` | Puerta en `_exigir_proveedor`; intento y resultado de la confirmación y de la cancelación; `recuperar` no actúa con el bloqueo. |
| `monitoreo/captura/consentimiento.py` | Revocación al diario. Un consentimiento con una revocación pendiente en el diario no es vigente, y no se puede registrar otro encima. |
| `monitoreo/migrate.py` | `restaurar` registra la restauración en el diario **antes** de sustituir la base, y la marca también dentro de la base restaurada. |
| `monitoreo/app.py`, `monitoreo/diagnostico.py` | Comprobación al arrancar y una tarjeta «Recuperación» en el autodiagnóstico. |
| `tests/test_recuperacion_v24.py` (nuevo) | 24 pruebas (SQLite y PostgreSQL). |
| `scripts/recorrido_integral.py` | El recorrido integral demuestra ahora la recuperación: 50 comprobaciones, antes 32. |
| `docs/RECUPERACION_SEGURA.md` | Guía de operación para el administrador. |

- **Sin tablas, migraciones ni dependencias nuevas.** Todo es biblioteca estándar de Python: `hmac`,
  `hashlib` y `ctypes` para DPAPI. Por eso no debería chocar con la migración `0016_rc1` de la RC1.
- **Tamaño (HECHO).** El módulo tiene unas 1 160 líneas, y el total es de unas 1 600 con API y pruebas. Mi
  cálculo del diseño era de 300 a 400 líneas. La diferencia viene de lo que pediste añadir:
  - la firma con clave y su almacenamiento con DPAPI;
  - la detección de truncamientos;
  - la copia externa;
  - el reinicio auditado del diario;
  - los seudónimos.

  No es un servicio aparte: es un módulo y una API.

## 2. Cómo se cumplió cada punto

| Punto | Cómo | Evidencia |
|---|---|---|
| Copia externa configurable desde el paso 1, sin activar | `MONITOREO_DIARIO_COPIA`. Está desactivada por omisión, y el laboratorio no hereda la variable. Si está configurada y falla, bloquea. | `test_copia_externa_configurada` |
| La falta de copia, clara en el diagnóstico | Aviso «Sin copia externa…: la recuperación NO está protegida contra la pérdida del disco». | `test_flujo_normal…` |
| 1. Protección criptográfica, no solo SHA-256 | Ver abajo. | `test_alterar_una_linea…`, `test_borrar_o_reordenar…`, `test_otra_clave_no_verifica`, `test_truncamiento…`, `test_clave_protegida_con_dpapi_en_windows` (solo en Windows) |
| 2. El bloqueo persiste al reiniciar | Ver abajo. | Pruebas y recorrido: «persiste tras reiniciar el motor» |
| 3. Nunca se reactiva un consentimiento revocado | `vigente()` consulta el diario: con una revocación pendiente responde «no vigente» (`REVOCACION_EN_DIARIO`). Tampoco se puede registrar otro consentimiento encima. La única resolución es reaplicar la revocación. | Recorrido: «el motor NO trata a G como vigente» |
| 4. Nunca se repite sola una confirmación incierta | Ver abajo. | Recorrido: «nada se repitió solo: una sola cita» |
| 5. Un fallo del diario no pasa inadvertido | Ver abajo. | `test_fallo_de_escritura…`, `test_revocacion_sin_diario…` |
| 6. Conciliación auditada y explícita | Ver abajo. | Recorrido: 403 al operador, `FOLIO_NO_COINCIDE`, 2 conciliaciones y el desbloqueo en la auditoría |
| 7. Códigos y folios protegidos | Ver abajo. | Prueba y recorrido: el diario no contiene ningún código ni folio |
| 8. Ventanilla y recorrido integral intactos | Ver abajo. | Sección 4 |

**1. Protección criptográfica.**
- Cada línea lleva un **HMAC-SHA256 con clave secreta**, encadenado con la anterior. Sin la clave, nadie puede
  alterar, borrar, reordenar ni recalcular la cadena sin que se note.
- La clave está fuera de la base y de los respaldos:
  - en Windows, cifrada con **DPAPI**, ligada a la cuenta de Windows del motor;
  - en Linux, en un archivo con permisos 0600.
- **Truncamiento:** la base guarda un ancla con el número y el HMAC de la última línea, y la copia externa es
  un segundo punto de referencia.

**2. Persistencia del bloqueo.** El bloqueo vive en dos sitios:
- en el diario firmado (`RESTAURACION` y `BLOQUEO` sin un `LEVANTAMIENTO` posterior);
- en la propia base (`settings`: `recuperacion_restaurada` y `recuperacion_fallo_diario`).

**4. Ninguna repetición automática.**
- Con el bloqueo, `recuperar()` no toca nada.
- Ninguna acción de conciliación llama al portal.
- El intento de confirmación se escribe en el diario **antes** de la llamada. Si no se puede escribir, no hay
  llamada.

**5. Fallos del diario.**
- Si falla la escritura del intento: **503**, no se llama al portal, y la reservación vuelve a su estado
  anterior.
- Cualquier fallo de escritura o de verificación deja `FALLO_ESCRITURA` o el motivo correspondiente en la
  base y en el registro (`CRITICAL`), y bloquea.
- Si la verificación falla por un error inesperado, el resultado es «bloqueado».

**6. Conciliación.**
- Solo el administrador, con un motivo obligatorio.
- El folio debe coincidir con el que anotó el diario, o se rechaza con `FOLIO_NO_COINCIDE`.
- Queda en `audit_log` y en una línea firmada `CONCILIADO`.
- El desbloqueo solo se acepta con 0 discrepancias, y también se audita.

**7. Identificadores protegidos.**
- El diario guarda **seudónimos** (HMAC con otra subclave) de los códigos de expediente y de consentimiento,
  de las reservaciones, las claves y los folios. No hay nombres, teléfonos ni correos.
- El motivo escrito por el administrador va solo a la auditoría de la base, porque es texto libre.

**8. Ventanilla y recorrido integral.** Ventanilla no cambia. El recorrido integral se amplió y sigue
pasando.

**Un defecto que encontré al diseñarlo (HECHO, en el código).** Los códigos `EXP-000007` y `CON-000007` se
generan con el id de la fila, y **se reutilizan tras restaurar**. Un expediente creado después de un respaldo
y uno creado después de restaurarlo pueden tener el mismo código. Si el diario identificara por código, una
revocación perdida podría «aplicarse» a otra persona. Por eso la identidad que se guarda en el diario combina
el código con la hora de creación de la fila.

## 3. La demostración pedida (HECHO, Linux local)

Es `scripts/recorrido_integral.py`, que se lanza con `recorrido_integral.bat`. Todos los datos son
ficticios y el portal es el de laboratorio propio.

1. Expedientes ficticios. A llega a su folio F1. B queda con el día elegido.
2. **Respaldo.**
3. Después del respaldo:
   - se **confirma B** (el portal le da el folio F2);
   - se **revoca G**.
4. **Restauración** con el motor detenido.
5. **Detección.** El motor arranca con `RECUPERACION_PENDIENTE` y exactamente 2 discrepancias:
   `CONFIRMACION_AUSENTE` (B) y `REVOCACION_AUSENTE` (G). G figura VIGENTE en la base, pero el motor no lo
   trata como vigente.
6. **Bloqueo:**
   - confirmar B da 409, tanto al operador como al administrador;
   - el portal sigue con una sola cita;
   - no se puede levantar el bloqueo;
   - el bloqueo persiste tras reiniciar el motor.
7. **Conciliación:**
   - el operador recibe 403;
   - un folio inventado se rechaza;
   - con el folio leído en el portal, B queda CONFIRMADA con evidencia `conciliacion_manual`;
   - la revocación de G se reaplica, y la cadena de sellos sigue íntegra.
8. **Solo entonces se desbloquea**, con 2 conciliaciones y el desbloqueo en la auditoría.
9. Continuidad, retención +2 días y entorno, igual que en la v2.3.
   - **Cambio esperado:** G ahora está revocada y cerrada, así que la retención la anonimiza. En la v2.3, G
     conservaba su consentimiento por error de la restauración y no se tocaba.

**Resultado local: 50/50 comprobaciones.** La evidencia está en `recorrido_integral.json`, en la carpeta de
entrega.

## 4. Regresiones (HECHO, Linux local, Python 3.13, SQLite y PostgreSQL 16)

- `tests/test_recuperacion_v24.py`: 24 pruebas; las de la base corren con SQLite y PostgreSQL. Se omiten dos:
  - DPAPI, porque es solo de Windows;
  - la restauración con PostgreSQL, porque `migrate restaurar` solo restaura SQLite.
- **Suites afectadas.** Se corrieron 29 archivos con SQLite y PostgreSQL: captura, consentimiento,
  migraciones, restauración, reinicio, autodiagnóstico, API, Ventanilla, planificador, retención, proveedor,
  Jarvis y recorrido v2.3.
  - **1.ª pasada: 3 fallos de 525, y los causé yo.**
    1. `test_dos_solicitudes_por_la_ultima_unidad[postgres]`. Dos confirmaciones simultáneas creaban la clave
       a la vez: una respondía 500 y la otra podía sustituir la clave. Ahora la clave se crea en exclusiva
       (`O_EXCL`) y bajo un candado. Lo cubre una prueba nueva con 8 hilos.
    2. `test_otra_instancia_con_otro_portal…` (SQLite y PostgreSQL). La prueba simula «el motor se cayó
       antes de guardar el resultado» retrocediendo la base, pero dejaba en el diario la línea de resultado,
       que en la realidad no llegaría a escribirse. El diario decía CONFIRMADA y la base CONFIRMANDO, así que
       el sistema bloqueó: es lo correcto ante una base que retrocedió.
       - Ajusté la **preparación** de la prueba para que tampoco escriba esa línea. Sus comprobaciones no
         cambian.
       - Además, `recuperar` vuelve a informar `bloqueadas_otro_proveedor` aunque haya bloqueo.
  - **2.ª pasada: 547 pasan, 4 se omiten, 0 fallan.**
- **Recorrido integral: 50/50 en 2 ejecuciones**, antes y después de las correcciones.
- **No se cambió ningún umbral ni se quitó ninguna prueba.**
- **CI:** ver la respuesta de entrega.

## 5. Limitaciones y pendientes en Windows físico

- **PENDIENTE.** DPAPI solo se ha probado en la CI de Windows, no en tu equipo. Si el motor llega a correr
  como servicio con otra cuenta, la clave no se abre (`CLAVE_NO_DISPONIBLE`) y el motor bloquea. Es un fallo
  seguro, pero hay que preverlo: exportar la clave y volver a importarla.
- **PENDIENTE.** No sé cuánto retrasa el antivirus el `fsync` de cada línea. Hay que medirlo.
- **PENDIENTE (tu decisión ya tomada).** La copia externa está sin activar. Mientras no se active, la
  recuperación **no** protege frente a la pérdida del disco.
- **Límite.** Quien controla la cuenta de Windows del motor puede abrir la clave y reescribir el diario. La
  firma protege frente a cambios accidentales y frente a quien no tiene esa cuenta.
- **Límite.** Sin copia externa, recortar el final del diario y además restaurar una base anterior a ese
  recorte no se detecta.
- **Límite.** Con PostgreSQL, `pg_restore` no pasa por `migrate`. El administrador debe ejecutar
  `python -m monitoreo.recuperacion marcar-restauracion`. Sin esa marca, el bloqueo depende de que haya
  discrepancias, que sí se detectan.
- **Límite.** Un solo proceso escribe el diario. Varias instancias contra la misma base PostgreSQL quedan
  fuera del alcance.
- **No hay interfaz gráfica de conciliación.** La API y la tarjeta del autodiagnóstico están, pero los
  botones no. Lo dejé fuera a propósito para no chocar con `web/admin.js` de la RC1. Puede ir en la mesa de
  control al integrar.
