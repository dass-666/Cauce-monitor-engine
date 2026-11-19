# Informe v2.3 · Recorrido integral: respaldo, restauración, continuidad, cierre y retención

Fecha: 2026-11-19. Rama `claude/recorrido-integral-v2.3`, creada desde la v2.3 de Ventanilla
(`claude/project-thread-2wwz8e` @ `93328a3`). Sin fusionar y sin desplegar. Esa rama no se tocó.

Convención: **HECHO** (comprobado), **INFERENCIA** (razonable, sin demostrar), **PENDIENTE**.

## 1. Qué se reutilizó y qué es nuevo

No se cambió ningún módulo del motor, ni la Ventanilla, el planificador, el observador o los contratos de
Jarvis. Lo único nuevo es:

| Archivo | Qué es |
|---|---|
| `scripts/recorrido_integral.py` | Orquesta el recorrido y lo comprueba (32 comprobaciones). |
| `recorrido_integral.bat` | Lanzador con doble clic para Windows. |
| `.github/workflows/ci.yml` | Ejecuta el recorrido en la CI de Reservaciones, en Linux y en Windows. |

Lo que se reutiliza:
- `ventanilla_laboratorio.Laboratorio` (v2.3): motor y portal propios en 127.0.0.1, en una carpeta temporal.
- La API del motor, con las mismas llamadas que demo_v22 y la Ventanilla.
- `python -m monitoreo.migrate respaldar` y `restaurar`.
- `retencion.aplicar(db, now)`.

El recorrido por la **interfaz** (abrir Ventanilla → folio → historial) ya lo demuestra
`recorrido_ventanilla_v23.py`, así que este script no lo repite.

**Fecha simulada.** La retención se ejecuta con `now = ahora + 2 días`, pasado como argumento a
`retencion.aplicar`:
- el reloj del equipo no cambia;
- los plazos de retención (1 día) solo se cambian en la base del laboratorio;
- los plazos por defecto de una instalación no cambian.

## 2. El recorrido

Todos los datos son ficticios. El expediente A tiene la mayor prioridad. Todo corre en un mismo
laboratorio.

1. **Alta.**
   - A, B y G con consentimiento, en el plan.
   - E con consentimiento, fuera del plan.
   - D sin consentimiento.
2. **A hasta el folio.** El observador está ACTIVO. Se abre el día 1, A queda asignado, se hace la
   operación y la confirmación **simulada**, y se obtiene el folio F1. El plan pasa a CONFIRMADO.
3. **B a medias.** Se abre el día 2, B queda asignado y la operación se prepara con el día elegido, pero
   **sin confirmar**.
4. **Respaldo** con el motor en marcha, y se toma la huella del estado.
5. **Cambios posteriores al respaldo.**
   - Se confirma B: el portal registra el folio F2.
   - Se da de alta X.
   - Se revoca el consentimiento de G.
6. **Restauración.** Se detiene el motor, se ejecuta `restaurar` (que antes hace su propio respaldo
   automático) y se vuelve a arrancar el motor sobre la misma carpeta.
7. **Continuidad.** La base restaurada cree que B no está confirmada, pero el portal sí tiene la cita.
   Se confirma otra vez.
8. **Retención con fecha +2 días.**
9. **Comprobación del entorno.** Nada cambió fuera del laboratorio.

## 3. Resultados (HECHO)

En local (Linux, Python 3.13, SQLite) se hicieron 5 ejecuciones con el script final. Cuatro dieron
**32/32 comprobaciones**. La quinta dio 31/32: falló «nada cambió fuera del laboratorio» porque yo estaba
escribiendo este informe en `docs/` mientras corría. La guarda detectó un cambio real en el proyecto, y la
comprobación quedó demostrada en el caso que debe fallar. El resultado de la CI está en la respuesta de
entrega.

| Lo que pediste comprobar | Resultado |
|---|---|
| El respaldo conserva el estado correcto | La huella de expedientes, consentimientos, plan, reservaciones, número de planes y observadores tras restaurar es **idéntica** a la del respaldo. |
| La restauración no duplica operaciones | Un solo plan y un solo observador, que vuelve a ACTIVO sin intervención. Al confirmar otra vez la operación de B, el portal sigue teniendo **una sola** cita ese día, y el motor termina con el **mismo** folio F2. El plan de B pasa a CONFIRMADO. |
| Confirmaciones y folios coherentes | F1 sigue CONFIRMADA en el motor y en el portal. Las citas anteriores al respaldo no cambian. |
| Consentimientos y trazabilidad íntegros | Las cadenas de sellos verifican después de restaurar y después de anonimizar. La revocación de G, posterior al respaldo, desaparece con la restauración: G vuelve a tener su consentimiento vigente. |
| La retención anonimiza solo lo que corresponde | A y B (citas confirmadas) se anonimizan. D (incompleto) se cierra por RETENCION y se anonimiza. E (LISTO) y G (en el plan) **no** se tocan. Se conservan código, folio e historial, y desaparecen nombre, teléfono y correo. De los respaldos se conservan los 3 más recientes. |
| Nada cambia fuera del laboratorio | `git status` y la carpeta `data/` del proyecto quedan iguales antes y después. Los registros del motor y del portal no contienen los datos personales ficticios. |

## 4. Defectos y riesgos encontrados

- **Ningún defecto funcional** en lo que se recorrió (HECHO, dentro del alcance anterior).
- **Por qué no se duplicó la cita tras restaurar (HECHO, leyendo el código).** El motor guarda la clave de
  idempotencia de la confirmación antes de llamar al portal, y el portal de laboratorio devuelve la misma
  reservación cuando se repite la clave (`portal_reservas/estado.py`). El respaldo contenía esa clave, y
  por eso la segunda confirmación obtuvo F2 en lugar de crear otra cita.
- **Riesgo importante (INFERENCIA).** Esa protección depende de que el **portal real** también acepte
  confirmaciones repetidas sin crear otra cita. No sabemos si el portal real lo hace. Si no lo hace,
  restaurar un respaldo anterior a una confirmación podría generar una segunda cita. Antes de usar
  respaldos con el portal real hay que decidir una regla: tras restaurar, revisar a mano las operaciones
  que estaban en CONFIRMANDO o con el día elegido.
- **Limitación.** El laboratorio de la v2.3 solo usa SQLite. La restauración con PostgreSQL (`pg_dump` y
  `pg_restore`) **no** está cubierta por este recorrido (PENDIENTE).
- **Limitación.** «Cierre» aquí es el ciclo del expediente: confirmado, y después cerrado o anonimizado por
  retención. El cierre de la **atención** en la Ventanilla lo cubre `recorrido_ventanilla_v23`.

## 5. Cómo comprobarlo en Windows físico

Ver `docs/VALIDAR_recorrido_integral_WINDOWS.md`.
