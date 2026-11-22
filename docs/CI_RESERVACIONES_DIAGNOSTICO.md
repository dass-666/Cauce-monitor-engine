# Diagnóstico del fallo intermitente «celda DISPONIBLE no aparece» (recorrido v2.3)

Solo diagnóstico: **no corrige nada**, no cambia aserciones, criterios de éxito ni tiempos. Contexto: informe A (`informe-A-ci-reservaciones.md`).
Marcas: **[V]** verificado · **[P]** pendiente.

## Qué añade (2 archivos)
- `scripts/recorrido_ventanilla_v23.js`: si el recorrido falla, antes de cerrar el navegador consulta a la página del administrador (con plazos de 10 s y 20 s para no colgarse) y guarda en el JSON `diagnostico`.
- `scripts/recorrido_ventanilla_v23.py`: si hay `diagnostico`, imprime las dos líneas de abajo.
En ejecución normal no se ejecuta nada nuevo [V: 21/21 comprobaciones, sin líneas DIAGNOSTICO].

## Cómo identificarlo en los logs
En el paso «v2.3 · Recorrido de Ventanilla…» del job (o con `gh api repos/<repo>/actions/jobs/<id>/logs | grep DIAGNOSTICO`):

```
DIAGNOSTICO_NAVEGADOR {"vista": ..., "repintando": ..., "pendiente": ..., "sucio": ..., "sin_conexion": ...,
   "celda_en_pantalla": ..., "error_en_pantalla": ..., "tarjeta_error": ..., "celda_en_servidor": ...}
DIAGNOSTICO_SOCKETS {"ESTABLISHED": n, "TIME_WAIT": n, "LISTENING": n, ...}
FALLA interfaz · recorrido completo sin interrupciones  page.waitForSelector: Timeout 30000ms exceeded.
```
| Campo | Qué dice |
|---|---|
| `celda_en_pantalla` | estado que **dibujaba** la interfaz para el día (`NO_HABILITADO`, `DISPONIBLE`, `NO_HAY_CELDA`…) |
| `celda_en_servidor` | estado que **contesta ahora** el servidor para ese día (o `servidor`: error/«sin respuesta en 10 s») |
| `repintando` | `true` = un repintado quedó colgado (la bandera `rendering` de `app.js` impide los siguientes) |
| `error_en_pantalla`, `tarjeta_error` | si la interfaz mostraba un error visible |
| `DIAGNOSTICO_SOCKETS` | conteo de sockets TCP por estado (no direcciones) |

**Cómo leerlo:** servidor ≠ `DISPONIBLE` ⇒ el día no se abrió (acción POST); servidor = `DISPONIBLE` y pantalla ≠ ⇒ la interfaz no se actualizó (revisar `repintando`); `servidor` con error ⇒ el motor no respondió.

## Privacidad [V]
Solo estados, banderas y contadores; el texto de errores se corta a 160 caracteres y proviene del calendario del laboratorio (fechas y estados). Sin datos personales, tokens ni direcciones (netstat se resume por estado, sin IP ni puertos).

## Comprobado
- Ejecución normal local (Linux): 21/21, sin líneas de diagnóstico; `tests/test_recorrido_v23.py` 9 pruebas verdes.
- Fallo forzado en una copia temporal (selector inexistente; no se confirmó en el repositorio): imprimió `DIAGNOSTICO_NAVEGADOR` con celda en pantalla y en servidor. En el contenedor sin `netstat`, `DIAGNOSTICO_SOCKETS` devuelve `{"error": "FileNotFoundError"}` (no rompe nada) [V]. En Windows el comando existe [P: se verá cuando falle en CI].
