"""Relojes: real y virtual (para acelerar el tiempo en el laboratorio)."""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from ..db import utcnow


class RealClock:
    virtual = False

    def now(self) -> datetime:
        return utcnow()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class VirtualClock:
    """Reloj controlado: `sleep` avanza el tiempo al instante, sin esperar."""
    virtual = True

    def __init__(self, start: datetime | None = None):
        self._now = start or utcnow()

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
