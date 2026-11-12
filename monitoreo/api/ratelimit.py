"""Límite de peticiones a la API del panel (v1.2).

Independiente de los límites de consulta a las fuentes (`sources.rate_limit_per_min`): aquel
protege a los proveedores de datos; este protege al propio servidor frente a abusos o a un
script descontrolado. Cubeta de fichas en memoria por dirección IP del cliente:

- MONITOREO_API_LIMITE_MIN          peticiones /api por minuto e IP (por defecto 600)
- MONITOREO_API_ESCRITURA_MIN       de ellas, las que modifican (POST/PUT/PATCH/DELETE) (por defecto 120)
- 0 desactiva el límite correspondiente.

Detrás de un proxy inverso la IP real llega en X-Forwarded-For; uvicorn solo la acepta si el
proxy está en MONITOREO_PROXY_CONFIABLE (ver __main__.py), así un cliente no puede falsificarla.
Al ser en memoria, cada instancia lleva su propia cuenta (suficiente para 1–2 instancias).
"""
from __future__ import annotations

import os
import threading
import time

from fastapi import Request
from fastapi.responses import JSONResponse

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
MAX_KEYS = 20_000


class _Bucket:
    __slots__ = ("tokens", "ts")

    def __init__(self, capacity: float, now: float):
        self.tokens, self.ts = capacity, now


class ApiRateLimiter:
    def __init__(self, per_min: float | None = None, writes_per_min: float | None = None, clock=time.monotonic):
        self.per_min = float(os.environ.get("MONITOREO_API_LIMITE_MIN", "600")) if per_min is None else per_min
        self.writes_per_min = (float(os.environ.get("MONITOREO_API_ESCRITURA_MIN", "120"))
                               if writes_per_min is None else writes_per_min)
        self.clock = clock
        self._b: dict[tuple[str, str], _Bucket] = {}
        self._lock = threading.Lock()
        self.rejected = 0

    def _take(self, key: tuple[str, str], rate_per_min: float, now: float) -> float:
        """Devuelve 0 si se permite; si no, los segundos hasta la próxima ficha."""
        if rate_per_min <= 0:
            return 0.0
        b = self._b.get(key)
        if b is None:
            if len(self._b) >= MAX_KEYS:  # evita crecer sin límite con muchas IP distintas
                cutoff = now - 120
                self._b = {k: v for k, v in self._b.items() if v.ts > cutoff}
            b = self._b[key] = _Bucket(rate_per_min, now)
        b.tokens = min(rate_per_min, b.tokens + (now - b.ts) * rate_per_min / 60)
        b.ts = now
        if b.tokens >= 1:
            b.tokens -= 1
            return 0.0
        return (1 - b.tokens) * 60 / rate_per_min

    def check(self, ip: str, method: str) -> float:
        now = self.clock()
        with self._lock:
            wait = self._take((ip, "all"), self.per_min, now)
            if not wait and method in WRITE_METHODS:
                wait = self._take((ip, "w"), self.writes_per_min, now)
            if wait:
                self.rejected += 1
            return wait


def middleware(limiter: ApiRateLimiter):
    async def _mw(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") or path.startswith("/api-prueba/"):
            ip = request.client.host if request.client else "desconocida"
            wait = limiter.check(ip, request.method)
            if wait:
                return JSONResponse({"detail": "Demasiadas peticiones. Espera unos segundos."}, status_code=429,
                                    headers={"Retry-After": str(max(1, int(wait + 0.999)))})
        return await call_next(request)
    return _mw
