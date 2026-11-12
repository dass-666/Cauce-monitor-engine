# Notificaciones: qué se garantiza y qué depende del destinatario (v1.2)

Este documento explica, sin adornos, qué puede prometer el motor al enviar avisos por
**webhook** y **Telegram**, y qué parte depende del sistema que los recibe.

## Cómo funciona (resumen)

1. Cuando ocurre algo relevante se crea una **alerta** (`alerts`) con una clave única
   (`dedup_key`). La base de datos impide dos alertas con la misma clave.
2. En la **misma transacción** se crea una **entrega** (`deliveries`) por cada canal activo.
   Esto se llama *bandeja de salida* (patrón *outbox*): la alerta y su intención de envío se
   guardan juntas o no se guarda ninguna.
3. El **despachador** reclama entregas pendientes, las marca `ENVIANDO` con un **testigo de
   reclamo** (`claim_token`, un identificador aleatorio) y las envía.
4. El resultado se escribe **solo si el testigo sigue siendo el suyo**. Si otro despachador
   ya se hizo cargo, el resultado tardío se anota en el registro como
   `[resultado tardío …]` y no toca el estado.

Estados: `PENDIENTE` → `ENVIANDO` → `ENVIADA` | `PENDIENTE` (reintento) | `FALLIDA`.

## Garantías que ofrece el motor por sí solo

Cada una tiene una prueba en `tests/test_notificaciones.py`, que se ejecuta en SQLite y en
PostgreSQL.

| Garantía | Cómo se consigue | Prueba |
|---|---|---|
| Ninguna alerta se pierde si el proceso muere **antes** de enviar | La entrega queda en `ENVIANDO`; la recuperación al arrancar o al ganar el liderazgo la devuelve a `PENDIENTE` | `test_crash_before_send_resends_once_without_duplicate` |
| Un reclamo colgado no bloquea para siempre | Pasados `entrega_reclamo_vence_s` (300 s por defecto) se reencola, aunque no cambie el líder | `test_stuck_claim_is_requeued_without_leadership_change` |
| Dos despachadores a la vez no envían dos veces | El reclamo es un `UPDATE … WHERE status='PENDIENTE'` condicional: solo uno lo gana | `test_double_dispatcher_many_deliveries_each_sent_once` (60 entregas, 3 hilos) |
| Un resultado tardío no pisa un estado más nuevo | La escritura final exige el mismo `claim_token` | `test_late_result_does_not_overwrite_newer_state` |
| Errores temporales se reintentan con espera creciente; errores permanentes no se insisten | 5xx/red: retroceso exponencial hasta `reintentos_notificacion`; 4xx (salvo 429): `FALLIDA` al primer intento | `test_retries_backoff_and_permanent_errors` |
| Se respeta la espera que pide el receptor | Webhook: cabecera `Retry-After` en 429/503 (máx. 3600 s). Telegram: `retry_after` del 429 | `test_webhook_retry_after_is_respected`, `test_telegram_blocked_recipient_and_429_partial` |
| Telegram: un destinatario bloqueado no impide avisar al resto | 400/403 por chat se anota en `rechazado_por` y se sigue con los demás | `test_telegram_blocked_recipient_and_429_partial` |
| Telegram: al reintentar no se repite a quien ya lo recibió | Tras cada chat servido se guarda al instante `entregado_a` (punto de control) | `test_telegram_crash_mid_delivery_does_not_repeat_served_chats` |
| Identificadores estables entre reintentos | `X-Id-Alerta = dedup_key` y `Idempotency-Key = dedup_key@id_canal`, iguales en cada intento | `test_idempotency_key_is_stable_across_retries` |

## Lo que NO puede garantizarse sin ayuda del destinatario

### 1. Ventana de duplicado "enviado pero no confirmado"

Si el receptor ya recibió el mensaje y el motor muere **antes de guardar** que lo envió, al
reiniciar lo enviará otra vez. Ningún sistema puede evitarlo del todo sin la cooperación del
receptor: el motor no tiene forma de saber si la petición llegó. Esto se llama entrega
**"al menos una vez"**. La ventana es corta (milisegundos entre la respuesta HTTP y la
escritura en la base), pero existe.

- **Webhook:** el receptor **debe** descartar repetidos usando `Idempotency-Key` (o
  `X-Id-Alerta`). Con eso el efecto final es "exactamente una vez".
  Prueba: `test_crash_after_send_before_commit_duplicates_but_key_is_stable`.
- **Telegram:** la Bot API **no** admite claves de idempotencia. Un mensaje repetido llega
  repetido. Lo único que se puede hacer es dejar una pista: cada mensaje lleva la línea
  `Ref: …`, con la misma referencia en el original y en la copia, para que la persona vea
  que es el mismo aviso. Con puntos de control por chat, la ventana se reduce a un solo
  destinatario (el que estaba en curso cuando cayó el proceso).

### 2. Autenticidad y repetición maliciosa de webhooks

Cada webhook lleva `X-Firma-SHA256` (HMAC-SHA256 del cuerpo con el secreto del canal o
`MONITOREO_SECRET_KEY`). El receptor debe **verificar la firma** para saber que viene del
motor. La firma **no incluye una marca de tiempo**: quien capture una petición válida podría
reenviarla. La defensa es la misma que para los duplicados: descartar `Idempotency-Key` ya
vistos. Además, usar siempre HTTPS (ver `PRODUCCION.md`).

### 3. Orden

No se garantiza el orden de llegada entre alertas distintas: un reintento puede llegar
después de una alerta más nueva. Si el orden importa, el receptor debe ordenar por
`created_at` del contenido.

### 4. Entrega final

`ENVIADA` significa que el servicio receptor **aceptó** el mensaje (HTTP 2xx, o `ok: true`
de Telegram). No significa que una persona lo haya leído.

## Qué tiene que hacer un receptor de webhooks (lista corta)

1. Verificar `X-Firma-SHA256` con el secreto compartido; rechazar si no coincide.
2. Guardar las `Idempotency-Key` procesadas (al menos 7 días) y responder 200 sin repetir el
   efecto si llega una repetida.
3. Responder rápido (menos de 5 s, o el `timeout_s` del canal) y hacer el trabajo pesado después.
4. Si está saturado, responder 429 o 503 con `Retry-After: <segundos>`.
5. Responder 4xx solo si el mensaje nunca va a poder aceptarse (el motor no reintenta).

## Parámetros relacionados (panel → Configuración del motor)

| Parámetro | Por defecto | Efecto |
|---|---|---|
| `reintentos_notificacion` | 5 | Intentos antes de `FALLIDA` |
| `backoff_notificacion_s` | 2 | Base del retroceso exponencial |
| `entrega_reclamo_vence_s` | 300 | Cuándo se considera colgado un `ENVIANDO` |

El autodiagnóstico avisa de entregas `ENVIANDO` de más de 10 minutos, de pendientes antiguas
y de una tasa de fallos superior al 20 % en 24 h.
