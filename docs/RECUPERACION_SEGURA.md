# Recuperación segura tras restaurar un respaldo (v2.4)

Guía de operación para el administrador. Código: `monitoreo/recuperacion.py`.

## Qué protege

Restaurar un respaldo devuelve la base a un momento anterior, y todo lo que ocurrió después desaparece de ella.
Dos casos son peligrosos:

- **Una revocación posterior desaparece.** El consentimiento vuelve a figurar como VIGENTE, y el sistema
  podría operar contra la voluntad de la persona.
- **Una cita confirmada en el portal desaparece.** La base ya no la conoce. Si alguien la confirma otra vez,
  un portal que no sea idempotente podría crear una segunda cita.

Para evitarlo, el motor guarda esos hechos en un **diario de eventos críticos fuera de la base**. Al
arrancar compara el diario con la base. Si se restauró un respaldo o algo no cuadra, **bloquea las operaciones
con el portal** hasta que un administrador concilie.

## Qué se bloquea y qué no

| Bloqueado (409 `RECUPERACION_PENDIENTE`) | Sigue funcionando |
|---|---|
| Enviar datos al portal, elegir día u horario, confirmar y cancelar citas | Ventanilla, alta de expedientes, consentimientos, plan, observador (solo lee) y consultas |
| La recuperación automática de intentos a medias | El descarte local de un intento sin confirmar |

Además, **un consentimiento cuya revocación está en el diario no se considera vigente**, aunque la base
restaurada diga VIGENTE. Tampoco se puede registrar otro consentimiento encima hasta conciliar.

## Dónde están los archivos

| Qué | Dónde | Notas |
|---|---|---|
| Diario | `data\diario\eventos-criticos.jsonl` | Una línea JSON por evento, firmada con HMAC-SHA256. No contiene nombres, teléfonos ni correos. Los códigos de expediente, los folios y las claves se guardan como seudónimos. |
| Clave del diario | `data\claves\diario.clave` | En Windows va cifrada con DPAPI, ligada a la cuenta que ejecuta el motor. **No** va en la base ni en los respaldos. |
| Copia externa (opcional) | Carpeta indicada en `MONITOREO_DIARIO_COPIA` | Está **desactivada** por omisión. |

## Cuando el motor arranca bloqueado

1. Abre el autodiagnóstico, o consulta `GET /api/recuperacion` como administrador. Verás los motivos del
   bloqueo y cada discrepancia con sus acciones posibles.
2. Resuelve cada discrepancia con `POST /api/recuperacion/discrepancias/{ref}/resolver`. El cuerpo lleva
   `{"tipo", "accion", "motivo", "folio"?}`. El motivo es obligatorio.

   | Discrepancia | Qué hacer |
   |---|---|
   | `REVOCACION_AUSENTE` | `reaplicar_revocacion`. Es la única opción: la revocación es definitiva. |
   | `CONFIRMACION_AUSENTE` | Comprueba la cita **en el portal**. Si existe, usa `registrar_folio` con su folio: debe coincidir con el que anotó el diario, o se rechaza. Si no existe, usa `no_existe`. |
   | `CONFIRMACION_INCIERTA` | Igual que la anterior. El sistema **no** vuelve a confirmar solo. |
   | `CANCELACION_AUSENTE` / `_INCIERTA` | Comprueba en el portal. Si la cita ya no existe, usa `aplicar_cancelacion`. Si sigue, usa `sigue_vigente`. |
   | `*_SIN_CONSENTIMIENTO` / `*_SIN_RESERVACION` | La base no conoce ese registro, porque se creó después del respaldo. Revísalo en el portal y usa `reconocer`. |

3. Cuando no quede ninguna pendiente, usa `POST /api/recuperacion/levantar {"motivo": "..."}`. Solo se acepta
   con 0 discrepancias pendientes y el diario íntegro.

Cada resolución y el desbloqueo quedan en dos sitios:
- en el registro de auditoría, con usuario, hora y motivo;
- como línea firmada en el diario (`CONCILIADO` y `LEVANTAMIENTO`).

## Otros motivos de bloqueo

| Motivo | Qué significa | Qué hacer |
|---|---|---|
| `FALLO_ESCRITURA` | No se pudo escribir una línea del diario, por ejemplo con el disco lleno o sin permisos. Si el fallo ocurrió antes de una confirmación, **no se llamó al portal**. | Corrige la causa y levanta el bloqueo. |
| `DIARIO_MANIPULADO` | Una línea no supera la firma, o la cadena está rota. | Investiga. Si no hay arreglo, usa `POST /api/recuperacion/reiniciar-diario`. Archiva el diario actual, no lo borra. |
| `DIARIO_TRUNCADO` / `DIARIO_AUSENTE` | El diario es más corto que lo que la base o la copia externa registraron. | Igual que la anterior. |
| `CLAVE_NO_DISPONIBLE` | Falta la clave, o no se puede abrir; por ejemplo, el motor corre con **otra cuenta de Windows** (DPAPI). | Importa la clave exportada (abajo) o reinicia el diario. |
| `COPIA_NO_DISPONIBLE` / `COPIA_DIVERGENTE` | La copia externa está configurada pero no se puede usar, o no coincide. | Conecta la unidad, o corrige la variable. |

**Reiniciar el diario tiene un coste.** Los eventos que solo estaban en el diario anterior ya no se comparan.
Revisa a mano las revocaciones y las citas posteriores al último respaldo antes de levantar el bloqueo.

## Consola

```powershell
.venv\Scripts\python.exe -m monitoreo.recuperacion estado
.venv\Scripts\python.exe -m monitoreo.recuperacion exportar-clave E:\resguardo\cauce-diario.clave.txt
.venv\Scripts\python.exe -m monitoreo.recuperacion importar-clave E:\resguardo\cauce-diario.clave.txt
.venv\Scripts\python.exe -m monitoreo.recuperacion marcar-restauracion "pg_restore 2026-11-20"
```

- **`marcar-restauracion`** es para PostgreSQL. `pg_restore` no pasa por `migrate restaurar`, así que esta
  orden deja la marca de restauración a mano.
- **Exporta la clave** y guárdala fuera del equipo. Sin la clave, la copia externa no se puede verificar ni
  relacionar con los expedientes.

## Límites

- **Sin copia externa, la recuperación no protege frente a la pérdida o el daño del disco.** La base y el
  diario se pierden a la vez. El autodiagnóstico lo avisa mientras `MONITOREO_DIARIO_COPIA` no esté
  configurada.
- **Quien controle la cuenta de Windows del motor puede leer la clave** y, con ella, reescribir el diario.
  La firma protege frente a quien no tiene esa cuenta y frente a cambios accidentales. No protege frente a un
  administrador del equipo malintencionado.
- **Un caso que no se detecta sin copia externa:** si alguien recorta el final del diario y además restaura
  una base anterior a ese recorte, no queda punto de referencia. La copia externa en otra unidad cubre ese
  caso.
- **Un solo proceso escribe el diario:** el motor, o `migrate restaurar` con el motor detenido. Varias
  instancias contra la misma base PostgreSQL tendrían diarios distintos. Eso queda fuera del alcance.
