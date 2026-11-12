"""Planificador en tiempo real (asyncio).

Un único bucle revisa cada `tick_segundos` qué fuentes deben consultarse y las
lanza en hilos, con un semáforo que limita la concurrencia global. Una fuente
nunca se consulta dos veces a la vez (conjunto `in_flight`).

¿Por qué no Redis + Celery? Con un solo proceso y la base de datos como cola
durable (patrón outbox) se cumplen todos los requisitos de esta fase con menos
piezas que instalar, vigilar y asegurar. Si en el futuro se necesitan varios
procesos trabajadores, el diseño ya usa reclamos atómicos en BD que lo permiten.
"""
from __future__ import annotations

import asyncio
import logging
import random

from ..db import Database
from . import alerts as alerts_mod
from .clock import RealClock
from .params import get_params
from .pipeline import RuntimeState, due_sources, maintenance, poll_source, process_pending_events

log = logging.getLogger("monitoreo.planificador")


class Scheduler:
    def __init__(self, db: Database, clock=None, seed: int | None = None):
        self.db = db
        self.clock = clock or RealClock()
        self.runtime = RuntimeState()
        self.rng = random.Random(seed)
        self.in_flight: set[int] = set()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.running = False
        self.cycles = 0
        self.last_error: str | None = None

    async def _poll(self, sem: asyncio.Semaphore, sid: int) -> None:
        async with sem:
            try:
                await asyncio.to_thread(poll_source, self.db, sid, self.clock, self.runtime, self.rng)
            except Exception as e:
                self.last_error = f"fuente {sid}: {e}"
                log.exception("fallo consultando fuente %s", sid)
            finally:
                self.in_flight.discard(sid)

    async def run(self) -> None:
        self.running = True
        alerts_mod.recover_inflight(self.db)
        maint_every = 0
        polls: set[asyncio.Task] = set()
        try:
            while not self._stop.is_set():
                with self.db.session() as s:
                    params = get_params(s)
                sem_size = max(1, int(params["concurrencia_maxima"]))
                if not hasattr(self, "_sem") or self._sem_size != sem_size:
                    self._sem, self._sem_size = asyncio.Semaphore(sem_size), sem_size
                try:
                    for sid in due_sources(self.db, self.clock.now()):
                        if sid not in self.in_flight:
                            self.in_flight.add(sid)
                            t = asyncio.create_task(self._poll(self._sem, sid))
                            polls.add(t)
                            t.add_done_callback(polls.discard)
                    await asyncio.to_thread(process_pending_events, self.db, self.clock)
                    maint_every += 1
                    if maint_every >= 4:
                        maint_every = 0
                        await asyncio.to_thread(maintenance, self.db, self.clock)
                    await asyncio.to_thread(alerts_mod.dispatch_pending, self.db, self.clock.now(), params)
                    self.cycles += 1
                except Exception as e:
                    self.last_error = str(e)
                    log.exception("error en ciclo del planificador")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=float(params["tick_segundos"]))
                except asyncio.TimeoutError:
                    pass
        finally:
            if polls:
                await asyncio.gather(*polls, return_exceptions=True)
            self.running = False

    def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.get_event_loop().create_task(self.run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
