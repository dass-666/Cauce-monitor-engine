# Canal Telegram (v1.1)

## Estado real de esta función

| Parte | Estado |
|---|---|
| Envío por la Bot API (`sendMessage`), destinatarios autorizados, reintentos, 429/Retry-After, errores por chat | **Implementado y probado contra el emulador local** (tests/test_telegram.py) |
| Avisos de disponibilidad, de asignación y de fallo crítico (tipo SISTEMA) | Implementado y probado con el emulador |
| Confirmación de recepción | Se hace **en el panel** (botón Confirmar de la alerta). El mensaje de Telegram lo recuerda. |
| Conexión con el servicio real de Telegram | **No verificada.** Nunca se ha llamado a api.telegram.org desde este proyecto. |

El emulador imita las respuestas documentadas de la Bot API (`ok`, `error_code`, `description`,
`parameters.retry_after`), pero un emulador no garantiza que el servicio real se comporte igual.
La primera conexión real debe hacerse con un chat de prueba (ver paso 5).

## Qué se envía (y qué no)

Solo campos operativos de una lista cerrada: tipo, región, fuente, recurso, código de solicitud,
prioridad, operador, cantidad, horario, tipo de cambio y fecha. **Nunca** se envía el titular de
la solicitud ni otros datos personales. Cada mensaje lleva una referencia corta (`Ref:`) para
reconocer repeticiones: el envío es "al menos una vez", así que tras una caída a mitad de envío
un mensaje puede llegar dos veces; con la referencia se identifica.

## Cómo conectarlo cuando lo decidas

1. **Crear el bot.** En Telegram abre una conversación con `@BotFather`, envía `/newbot`, elige
   nombre y usuario. BotFather te da un **token** (`123456789:AA...`). Es una contraseña:
   no lo compartas ni lo pegues en capturas.
2. **Obtener el id del chat.** Escribe cualquier mensaje a tu bot (o añádelo a un grupo y escribe
   en el grupo). Luego abre en el navegador
   `https://api.telegram.org/bot<TOKEN>/getUpdates` y busca `"chat":{"id": ...}`.
   Los grupos tienen id negativo (p. ej. `-1001234567890`).
3. **Configurar `.env`** (nunca en el código ni en el panel):
   ```
   MONITOREO_TELEGRAM_TOKEN=123456789:AA...
   MONITOREO_TELEGRAM_CHATS=111111111,-1001234567890
   ```
   Solo esos chats pueden recibir mensajes. Si el canal pide un chat que no está en la lista,
   la entrega se rechaza como error permanente.
4. **Activar el canal.** Reinicia con `iniciar.bat`. En el panel: Configuración → Notificaciones →
   "Telegram" → Activar. La misma pantalla muestra si el token está configurado (sin mostrarlo)
   y cuántos destinatarios hay.
5. **Probar.** Pulsa "Probar" en el canal Telegram. Debe llegar "Mensaje de prueba del Motor de
   Monitoreo". Si falla, el motivo aparece en pantalla. Los fallos de alertas reales se ven en el detalle de cada alerta ("Entregas por canal").
6. (Opcional) Limitar qué avisos van a Telegram. En esta versión no hay formulario en el panel;
   se hace por la API (administrador): `PATCH /api/channels/<id>` con
   `{"config": {"tipos": ["ASIGNACION", "SISTEMA"]}}`.

## Probar todo sin Telegram real (emulador)

El servidor incluye un emulador en `/api-prueba/telegram`. En `.env`:

```
MONITOREO_TELEGRAM_API=http://127.0.0.1:8000/api-prueba/telegram
MONITOREO_TELEGRAM_TOKEN=000000:EMULADOR
MONITOREO_TELEGRAM_CHATS=1001,1002
```

Mensajes recibidos: `GET http://127.0.0.1:8000/api-prueba/telegram/mensajes`.
Simular problemas: `POST /api-prueba/telegram/control` con, por ejemplo,
`{"modo": "429", "retry_after": 30}`, `{"modo": "500", "fallos_restantes": 2}`,
`{"chats_bloqueados": ["1002"]}` (bot bloqueado por ese usuario) o `{"modo": "lento"}`.
`POST /api-prueba/telegram/reiniciar` vuelve al estado normal.

## Cómo reacciona a los errores

| Respuesta | Qué hace el motor |
|---|---|
| 200 `ok:true` | Marca ese chat como entregado; no se le reenvía en reintentos. |
| 429 con `retry_after` | Espera lo que pide Telegram (o el retroceso, lo que sea mayor) y reintenta. |
| 400 / 403 para un chat (no existe, bot bloqueado) | Error permanente **para ese chat**; los demás siguen. |
| 401 (token inválido) | Error permanente del canal; aparece en el panel. |
| 5xx o error de red | Temporal: reintenta con retroceso exponencial hasta `reintentos_notificacion`. |

El token nunca aparece en mensajes de error, registros ni en la API del panel
(probado en tests/test_telegram.py y tests/test_observability.py).

## v1.3: punta a punta desde el proveedor HTTP de prueba (emulador)

`scripts/integracion_http.py` ejecuta el emulador como **proceso aparte** y comprueba la cadena
completa: liberación en el proveedor de prueba → consulta HTTP → asignación → alerta → entrega
por Telegram (emulado) a 2 chats. Comprueba que cada entrega marcada ENVIADA llegó exactamente a
los 2 chats y mide el tiempo desde el cambio en la fuente hasta la entrega
(`auditoria_cambios.controladas.hasta_telegram_ms` en el JSON de resultados).
**Sigue sin haberse llamado nunca a api.telegram.org.** Eso queda para la prueba manual.

## Prueba manual corta con tu bot real (cuando decidas configurarlo, ~10 minutos)

Hazla con un chat de prueba tuyo, no con un grupo de operadores.

1. Pasos 1–3 de "Cómo conectarlo" (token y chat en `.env`; **quita** `MONITOREO_TELEGRAM_API`
   si estaba apuntando al emulador).
2. Reinicia y en el panel pulsa **Probar** en el canal Telegram.
   ✔ Esperado: llega "Mensaje de prueba del Motor de Monitoreo" en menos de 5 s.
3. Activa el canal solo para `ASIGNACION` y `SISTEMA` (paso 6).
4. Provoca una asignación: Laboratorio → "Liberar +1" en una región con solicitudes en espera.
   ✔ Esperado: un mensaje con código de solicitud, recurso, operador y `Ref:`; **sin** el titular.
5. En el panel abre la alerta → "Entregas por canal": estado ENVIADA, 1 intento.
6. Comprueba el registro `data/logs/monitoreo.log`: el token **no** debe aparecer
   (busca los primeros dígitos del token).
7. (Opcional) Bloquea el bot desde tu chat y repite el paso 4.
   ✔ Esperado: entrega FALLIDA para ese chat con el motivo de Telegram; el motor sigue funcionando.
8. Anota fecha, versión y resultado de cada paso. Hasta tener esta nota, el envío real
   sigue siendo **no verificado**.
