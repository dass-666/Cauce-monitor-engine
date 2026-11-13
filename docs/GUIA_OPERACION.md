# Guía de operación (operadores) · v1.7

Para las personas que atienden expedientes desde el panel. Todo lo que se describe aquí funciona con
el **portal de prueba** (ficticio y local); la integración con un portal real no está validada.

## 1. Entrar

1. Abre el panel (normalmente http://127.0.0.1:8000) y entra con tu usuario y contraseña.
2. Pulsa **Captura**. Se abre directamente la pestaña **Mi trabajo**.

Solo ves **tus** expedientes y **tus** notificaciones. Los de otros operadores no aparecen ni
se pueden abrir, aunque se conozca su número.

## 2. Mi trabajo

Arriba hay cuatro contadores: *por empezar*, *a medias*, *en verificación* y *confirmados hoy*.
Debajo, una tarjeta por expediente. Van primero los que puedes atender ahora; dentro de ellos, los
**urgentes** (prioridad 1 o con la asignación o el día apartado a punto de vencer), después por
**prioridad** (1 = más urgente) y, a igual prioridad, los que dejaste a medias.

Cada tarjeta dice:

| Dato | Para qué sirve |
|---|---|
| Código, trámite, sucursal y modo | Identificar el expediente sin mostrar datos personales. |
| **Paso N de 12** y su texto | En qué punto está y qué toca hacer ahora. |
| Etiqueta **A medias** | Se quedó una operación sin terminar (cerraste el navegador, se cortó la red o se reinició el motor). Se retoma en el mismo paso. |
| Avisos en naranja | Datos que faltan o validaciones pendientes. Un expediente **incompleto** no se puede preparar hasta completarlo. |
| Botón **Continuar** | Lleva al siguiente paso que corresponde. |

## 3. Atender un expediente (el flujo normal)

1. **Continuar** en la primera tarjeta: el motor prepara el formulario con los datos guardados.
2. Revisa los datos. Teclea a mano los **campos manuales** (por ejemplo, la clave de identidad que
   te dicta el solicitante: no se puede pegar y el motor no la guarda).
3. Marca **He revisado los campos** y pulsa **Enviar**.
4. Aparece el calendario del mes. Si este mes ya no tiene días libres, el panel muestra
   **automáticamente el mes siguiente** con un aviso que lo explica.
5. Elige un día en verde, marca **Confirmo esta reservación** y pulsa **Confirmar**.
6. Verás el **folio**. Debajo aparece **Siguiente expediente**: pulsa y se prepara el siguiente de tu
   lista sin volver a la tabla.

Las dos casillas (*He revisado* y *Confirmo*) son obligatorias a propósito: el motor nunca confirma
por ti. Con la v1.7 dos reservaciones seguidas necesitan 17 interacciones (antes 21), medido con
un navegador real; las validaciones son las mismas.

## 4. Situaciones especiales

| Ves | Significa | Qué hacer |
|---|---|---|
| **Sin conexión** (franja arriba) | El navegador no llega al motor. Lo que ves se queda en pantalla pero puede estar desactualizado. | Espera: cuando vuelve la conexión, la franja desaparece sola. No repitas la confirmación. |
| **En verificación** | El portal no respondió con claridad al confirmar. El motor comprueba por su cuenta si hay folio. | No confirmes otra vez. Aparecerá el folio o el expediente volverá a tu lista. |
| Calendario **sin comunicación** | Los días aparecen como *desconocidos*: no se pudo comprobar el portal. | Espera unos segundos o atiende otro expediente. |
| Una tarjeta desaparece de *Mi trabajo* | Un administrador la **redistribuyó** (por ejemplo, porque dejaste de dar señal). | Nada; si estabas trabajando en ella, avisa al administrador. |
| No ves **Supervisión** en el menú | Es solo para administradores. | Normal. |

## 5. Notificaciones

En **Notificaciones** verás solo las tuyas: expedientes asignados, confirmaciones, expedientes
incompletos, intervenciones requeridas, etc. Se filtran por categoría con los botones de arriba y se
marcan como vistas con **Confirmar**. Las notificaciones **no incluyen datos personales**: el titular
aparece con iniciales.

## 6. Buenas prácticas

- Cierra sesión al terminar (menú *Salir*). Si solo cierras el navegador, el administrador te verá
  como *conexión perdida* durante un rato.
- No compartas tu usuario: todo lo que haces queda en la auditoría con tu nombre.
- Usa solo datos ficticios en el laboratorio (teléfonos `555…`, correos `@ejemplo.test`).
