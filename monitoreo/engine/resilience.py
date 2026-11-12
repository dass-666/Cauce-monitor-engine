"""Mecanismos de resiliencia: límite de solicitudes, retroceso exponencial y circuit breaker.

Glosario:
- Token bucket ("cubeta de fichas"): cada fuente tiene N fichas por minuto; cada
  consulta gasta una. Si no hay fichas, la consulta se pospone. Garantiza que
  nunca superamos el límite configurado aunque haya errores o reinicios rápidos.
- Retroceso exponencial: tras cada fallo se espera el doble (2, 4, 8 s...), con
  un tope, para no saturar una fuente que ya tiene problemas.
- Circuit breaker ("disyuntor"): tras varios fallos seguidos se "abre" y deja de
  consultar durante un enfriamiento; luego deja pasar una consulta de prueba
  (SEMIABIERTO). Si va bien se cierra; si falla, vuelve a abrirse.
"""
from __future__ import annotations

import random
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta


class TokenBucket:
    def __init__(self, rate_per_min: float, capacity: float | None = None):
        self.rate = max(rate_per_min, 0.0001) / 60.0  # fichas por segundo
        self.capacity = capacity if capacity is not None else max(1.0, rate_per_min / 6)  # ráfaga de ~10 s
        self.tokens = self.capacity
        self.updated: datetime | None = None
        self._lock = threading.Lock()

    def try_acquire(self, now: datetime) -> tuple[bool, float]:
        """Devuelve (permitido, segundos_hasta_próxima_ficha)."""
        with self._lock:
            if self.updated is not None:
                elapsed = max(0.0, (now - self.updated).total_seconds())
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.updated = now
            if self.tokens >= 1:
                self.tokens -= 1
                return True, 0.0
            return False, (1 - self.tokens) / self.rate


def backoff_delay(attempt: int, base: float, cap: float, rng: random.Random | None = None) -> float:
    """Retroceso exponencial con 'jitter completo' (evita que todos reintenten a la vez)."""
    rng = rng or random
    exp = min(cap, base * (2 ** max(0, attempt - 1)))
    return rng.uniform(exp / 2, exp)


def jittered_interval(interval: float, jitter_pct: float, rng: random.Random | None = None) -> float:
    """Variación temporal controlada: intervalo ± jitter_pct."""
    rng = rng or random
    j = max(0.0, min(jitter_pct, 0.9))
    return interval * rng.uniform(1 - j, 1 + j)


@dataclass
class CircuitDecision:
    allowed: bool
    state: str
    wait_s: float = 0.0


def circuit_check(state: str, opened_at: datetime | None, cooldown_s: float, now: datetime) -> CircuitDecision:
    if state == "ABIERTO":
        if opened_at and now >= opened_at + timedelta(seconds=cooldown_s):
            return CircuitDecision(True, "SEMIABIERTO")
        wait = (opened_at + timedelta(seconds=cooldown_s) - now).total_seconds() if opened_at else cooldown_s
        return CircuitDecision(False, "ABIERTO", max(wait, 0))
    return CircuitDecision(True, state)
