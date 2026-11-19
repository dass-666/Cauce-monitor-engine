# Pendientes de diseño

Asuntos acordados para más adelante. **No están implementados.** Antes de abordarlos hay que proponer el diseño
y obtener la aprobación de Diego.

## 1. Simplificar el menú superior (registrado el 2026-11-19)

**Situación.** En laboratorio, el administrador ve 17 secciones en una sola fila:

Mesa de control, Importar, Expedientes, Inicio, Monitor, Salud, Solicitudes, Ventanilla, Captura,
Notificaciones, Supervisión, Trámites a mano, Conexiones, Calendario lab, Laboratorio, Recuperación y
Configuración.

En una pantalla de 1366 px las últimas, entre ellas «Recuperación», quedan fuera de la vista. Hoy se llega a
«Recuperación» por la tarjeta de la Mesa de control.

**Objetivo.** Dejar a la vista el flujo de trabajo diario y agrupar las secciones técnicas, **sin eliminar
ninguna función** ni cambiar sus permisos.

**Propuesta inicial, por validar:**

| Grupo | Secciones |
|---|---|
| A la vista (trabajo diario) | Mesa de control, Importar, Expedientes, Ventanilla, Recuperación (con señal si está bloqueada) |
| «Operación» (desplegable) | Captura, Solicitudes, Notificaciones, Supervisión, Trámites a mano |
| «Sistema» (desplegable) | Inicio/Monitor, Salud, Conexiones, Configuración |
| «Laboratorio» (desplegable, solo fuera de producción) | Calendario lab, Laboratorio |

**Restricciones:**

- Sin bibliotecas nuevas.
- Se mantiene la CSP actual (sin estilos ni scripts en línea).
- Debe funcionar con el teclado.
- Las vistas siguen filtradas por rol (`admin`), por laboratorio (`lab`) y por flujo (`flujo`). Ver
  `VIEWS` en `monitoreo/web/app.js`.
- Los recorridos con navegador (`recorrido_rc1.js`, `recorrido_ventanilla_v23.js` y
  `recorrido_recuperacion_ui.js`) navegan por `#vista`, no por la posición en el menú. Hay que comprobar que siguen
  pasando.

**Cómo verificarlo cuando se haga:**

- Captura a 1366 px y a ancho de móvil.
- Que las 17 secciones sigan siendo alcanzables según el rol.
- Que los tres recorridos pasen sin cambios en sus selectores de sección.
