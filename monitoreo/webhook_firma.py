"""Firma de los webhooks del motor y cómo verificarla en el receptor (v1.4).

Cabeceras que envía el motor:
- `X-Marca-Tiempo`: segundos Unix en el momento del envío (cambia en cada reintento).
- `X-Firma-V2`: HMAC-SHA256 de `"<marca>." + cuerpo` con el secreto del canal.
- `X-Firma-SHA256`: firma antigua (solo del cuerpo), se mantiene por compatibilidad con v1.0–v1.3.
- `Idempotency-Key`: igual en todos los reintentos de la misma alerta y canal.

Un receptor debe: (1) comprobar `X-Firma-V2`; (2) rechazar marcas con más de 5 minutos de
diferencia (una petición capturada deja de valer pronto); (3) descartar `Idempotency-Key` ya
procesadas (cubre reintentos legítimos y repeticiones dentro de la ventana).
"""
from __future__ import annotations

import hashlib
import hmac
import time


def sign_v2(body: bytes, secret: str, ts: int) -> str:
    return hmac.new(secret.encode(), str(int(ts)).encode() + b"." + body, hashlib.sha256).hexdigest()


def verify(body: bytes, headers, secret: str, tolerance_s: float = 300, now: float | None = None) -> tuple[bool, str]:
    """Devuelve (válida, motivo). `headers` es cualquier mapeo sin distinguir mayúsculas
    (p. ej. el de FastAPI/Starlette) o un dict con los nombres tal como los envía el motor."""
    get = (lambda k: headers.get(k) or headers.get(k.lower())) if isinstance(headers, dict) else headers.get
    ts, sig = get("X-Marca-Tiempo"), get("X-Firma-V2")
    if not ts or not sig:
        return False, "faltan X-Marca-Tiempo o X-Firma-V2"
    try:
        ts_i = int(ts)
    except ValueError:
        return False, "marca de tiempo inválida"
    now = time.time() if now is None else now
    if abs(now - ts_i) > tolerance_s:
        return False, "marca de tiempo fuera de la ventana permitida (posible repetición)"
    if not hmac.compare_digest(sign_v2(body, secret, ts_i), sig):
        return False, "firma inválida"
    return True, "ok"
