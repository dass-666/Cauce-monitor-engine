"""Planificador en tiempo real (asyncio) con liderazgo por arrendamiento en BD.

Un único bucle revisa cada `tick_segundos` qué fuentes deben consultarse y las lanza en
hilos, con un semáforo que limita la concurrencia global. Una fuente nunca se consulta dos
veces a la vez (`in_flight`).

v1.1: antes de trabajar, la instancia debe tener el arrendamiento `planificador`
(ver engine/lease.py). Si arrancan dos procesos contra la misma base, uno trabaja y el otro
queda EN_ESPERA sirviendo el panel; si el líder muere, el otro toma el relevo al vencer
el arrendamiento y ejecuta la recuperación.

¿Por qué no Redis + Celery? Con un solo trabajador activo y la BD como cola durable
(outbox) se cumplen los requisitos con menos piezas que instalar, vigilar y asegurar.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time

import psutil

from ..db import Database
from . import alerts as alerts_mod
from .clock import RealClock
from .lease import Lease
from .params import get_params
from .pipeline import RuntimeState, due_sources, maintenance, poll_source, process_pending_events
from .recovery import run_recovery
from .retention import run_history_maintenance

log = logging.getLogger("monitoreo.planificador")


class Scheduler:
    def __init__(self, db: Database, clock=None, seed: int | None = None, holder: str | None = None,
                 lease_ttl_s: float | None = None):
        self.db = db
        self.clock = clock or RealClock()
        self.runtime = RuntimeState()
        self.rng = random.Random(seed)
        self.in_flight: set[int] = set()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        with db.session() as s:
            ttl = lease_ttl_s or float(os.environ.get("MONITOREO_LEASE_S") or get_params(s)["lease_duracion_s"])
        self.lease = Lease(db, holder=holder, ttl_s=ttl)
        self.running = False
        self.role = "INICIANDO"  # LIDER | EN_ESPERA | DETENIDO
        self.cycles = 0
        self.last_cycle_at = None
        self.last_cycle_ms = None
        self.last_error: str | None = None
        self.last_error_at = None
        self.consecutive_cycle_errors = 0
        self.last_history_at = 0.0
        self.last_history: dict | None = None
        self.recovery: dict | None = None
        self._history_task: asyncio.Task | None = None
        self.started_at = self.clock.now()

    def guard(self) -> bool:
        return self.lease.is_valid(self.clock.now())

    async def _poll(self, sem: asyncio.Semaphore, sid: int) -> None:
        async with sem:
            try:
                await asyncio.to_thread(poll_source, self.db, sid, self.clock, self.runtime, self.rng,
                                        None, self.guard)
            except Exception as e:
                self._error(f"fuente {sid}: {e.__class__.__name__}: {e}")
                log.exception("fallo consultando fuente %s", sid)
            finally:
                self.in_flight.discard(sid)

    def _error(self, msg: str) -> None:
        self.last_error, self.last_error_at = msg[:500], self.clock.now()

    async def _history(self) -> None:
        try:
            self.last_history = await asyncio.to_thread(run_history_maintenance, self.db, self.clock.now(),
                                                        self.lease.holder)
        except Exception as e:
            self._error(f"mantenimiento de historial: {e}")
            log.exception("fallo en mantenimiento de historial")

    async def run(self) -> None:
        self.running = True
        maint_every = 0
        polls: set[asyncio.Task] = set()
        was_leader = False
        tick = 0.5
        try:
            while not self._stop.is_set():
                t0 = time.perf_counter()
                try:
                    with self.db.session() as s:
                        params = get_params(s)
                    tick = float(params["tick_segundos"])
                    leader = await asyncio.to_thread(self.lease.acquire_or_renew, self.clock.now())
                    if leader and not was_leader:
                        # Recién elegido (arranque o relevo): recuperar lo que quedó a medias.
                        self.recovery = await asyncio.to_thread(
                            run_recovery, self.db, self.clock.now(), self.lease.holder,
                            0 if self.cycles == 0 else self.lease.ttl_s)
                        log.warning("Instancia %s es LÍDER del planificador", self.lease.holder)
                    was_leader = leader
                    self.role = "LIDER" if leader else "EN_ESPERA"
                    if leader:
                        sem_size = max(1, int(params["concurrencia_maxima"]))
                        if not hasattr(self, "_sem") or self._sem_size != sem_size:
                            self._sem, self._sem_size = asyncio.Semaphore(sem_size), sem_size
                        for sid in due_sources(self.db, self.clock.now()):
                            if sid not in self.in_flight:
                                self.in_flight.add(sid)
                                t = asyncio.create_task(self._poll(self._sem, sid))
                                polls.add(t)
                                t.add_done_callback(polls.discard)
                        await asyncio.to_thread(process_pending_events, self.db, self.clock, 500, self.guard)
                        maint_every += 1
                        if maint_every >= 4 and self.guard():
                            maint_every = 0
                            await asyncio.to_thread(maintenance, self.db, self.clock)
                        if self.guard():
                            await asyncio.to_thread(alerts_mod.dispatch_pending, self.db, self.clock.now(), params)
                        now_m = time.monotonic()
                        if (now_m - self.last_history_at >= float(params["mantenimiento_historial_cada_s"])
                                and (self._history_task is None or self._history_task.done())):
                            self.last_history_at = now_m
                            self._history_task = asyncio.create_task(self._history())
                    self.cycles += 1
                    self.consecutive_cycle_errors = 0
                except Exception as e:
                    # p. ej. la base de datos no responde: se registra y se reintenta en el siguiente tick
                    self.consecutive_cycle_errors += 1
                    self._error(f"ciclo: {e.__class__.__name__}: {str(e)[:300]}")
                    log.exception("error en ciclo del planificador")
                    was_leader = False  # al recuperarse se volverá a ejecutar la recuperación
                self.last_cycle_at = self.clock.now()
                self.last_cycle_ms = (time.perf_counter() - t0) * 1000
                delay = tick if self.consecutive_cycle_errors == 0 else min(30.0, tick * 2 ** self.consecutive_cycle_errors)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
        finally:
            if polls:
                await asyncio.gather(*polls, return_exceptions=True)
            if self._history_task:
                await asyncio.gather(self._history_task, return_exceptions=True)
            try:
                if self.role == "LIDER":
                    self.lease.release(self.clock.now())
            except Exception:  # noqa: BLE001
                pass
            self.running = False
            self.role = "DETENIDO"

    def status(self) -> dict:
        proc = psutil.Process()
        lag = None
        if self.last_cycle_at:
            lag = round((self.clock.now() - self.last_cycle_at).total_seconds(), 2)
        return {"activo": self.running, "rol": self.role, "instancia": self.lease.holder,
                "ciclos": self.cycles, "ultimo_ciclo": self.last_cycle_at.isoformat() + "Z" if self.last_cycle_at else None,
                "segundos_desde_ultimo_ciclo": lag,
                "duracion_ultimo_ciclo_ms": round(self.last_cycle_ms, 1) if self.last_cycle_ms else None,
                "consultas_en_curso": len(self.in_flight), "errores_ciclo_seguidos": self.consecutive_cycle_errors,
                "ultimo_error": self.last_error,
                "ultimo_error_en": self.last_error_at.isoformat() + "Z" if self.last_error_at else None,
                "arrendamiento": self.lease.info(), "ultima_recuperacion": self.recovery,
                "ultimo_mantenimiento_historial": self.last_history,
                "iniciado": self.started_at.isoformat() + "Z",
                "proceso": {"pid": proc.pid, "memoria_mb": round(proc.memory_info().rss / 1048576, 1),
                            "cpu_pct": proc.cpu_percent(interval=None), "hilos": proc.num_threads()}}

    def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.get_event_loop().create_task(self.run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
