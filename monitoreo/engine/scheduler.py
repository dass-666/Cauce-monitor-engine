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
        # v1.4 (optimización medida): una consulta que detecta cambios despierta el ciclo para
        # asignar en seguida, en lugar de esperar al siguiente tick (media de tick/2 = 250 ms).
        self._wake = asyncio.Event()
        self.wakeups = 0
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
        self.last_selfcheck_at = time.monotonic()  # el primero, pasado un intervalo (no al arrancar)
        self._selfcheck_task: asyncio.Task | None = None
        self.last_history: dict | None = None
        self.recovery: dict | None = None
        self._history_task: asyncio.Task | None = None
        self._dispatch_task: asyncio.Task | None = None
        # v1.5: seguimiento de expedientes y recuperación de reservaciones (tarea propia: habla con
        # el portal de prueba y un portal lento no debe frenar el ciclo del motor).
        self._captura_task: asyncio.Task | None = None
        self.last_captura_at = 0.0
        self.last_captura: dict | None = None
        # v1.6: oyentes de eventos de calendarios POR DÍA (mecanismo A); solo en la instancia líder
        self.eventos = None
        self.last_eventos_at = 0.0
        self._loop = None
        self.started_at = self.clock.now()

    async def _sleep_or_wake(self, delay: float, batch_s: float = 0.03) -> None:
        """Espera `delay` o hasta que una consulta traiga cambios. Tras despertar espera `batch_s`
        para agrupar los cambios de varias fuentes en un solo ciclo (evita un ciclo por evento)."""
        stop, wake = asyncio.ensure_future(self._stop.wait()), asyncio.ensure_future(self._wake.wait())
        try:
            done, _ = await asyncio.wait({stop, wake}, timeout=delay, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (stop, wake):
                if not t.done():
                    t.cancel()
        if wake in done and not self._stop.is_set():
            self.wakeups += 1
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=batch_s)
            except asyncio.TimeoutError:
                pass
        self._wake.clear()

    def guard(self) -> bool:
        return self.lease.is_valid(self.clock.now())

    async def _poll(self, sem: asyncio.Semaphore, sid: int) -> None:
        async with sem:
            try:
                out = await asyncio.to_thread(poll_source, self.db, sid, self.clock, self.runtime, self.rng,
                                              None, self.guard)
                if getattr(out, "events", 0):
                    self._wake.set()
            except Exception as e:
                self._error(f"fuente {sid}: {e.__class__.__name__}: {e}")
                log.exception("fallo consultando fuente %s", sid)
            finally:
                self.in_flight.discard(sid)

    def _error(self, msg: str) -> None:
        self.last_error, self.last_error_at = msg[:500], self.clock.now()

    async def _selfcheck(self) -> None:
        try:
            from ..diagnostico import run_and_alert
            res = await asyncio.to_thread(run_and_alert, self.db, self, self.clock.now())
            self.last_selfcheck = {k: res[k] for k in ("momento", "estado", "criticos", "avisos")}
        except Exception as e:  # noqa: BLE001
            log.warning("autodiagnóstico falló: %s", e)

    async def _dispatch(self, params: dict) -> None:
        try:
            await asyncio.to_thread(alerts_mod.dispatch_pending, self.db, self.clock.now(), params)
        except Exception as e:
            self._error(f"envío de alertas: {e}")
            log.exception("fallo enviando alertas")

    async def _captura(self) -> None:
        try:
            from ..captura.seguimiento import mantenimiento as captura_mant
            self.last_captura = await asyncio.to_thread(captura_mant, self.db, self.clock.now())
        except Exception as e:
            self._error(f"captura: {e}")
            log.exception("fallo en el seguimiento de expedientes")

    def _al_cambiar_calendario(self, sid: int) -> None:
        """Lo llama un hilo oyente: consultar esa fuente en el siguiente ciclo y despertar el bucle."""
        from ..models import Source
        try:
            with self.db.session() as s:
                src = s.get(Source, sid)
                if src is not None:
                    src.next_poll_at = self.clock.now()
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._wake.set)
        except Exception:  # noqa: BLE001
            log.exception("no se pudo adelantar la consulta del calendario %s", sid)

    def _revisar_eventos(self, leader: bool) -> None:
        if leader:
            if self.eventos is None:
                from ..captura.eventos_portal import GestorEventos
                self.eventos = GestorEventos(self.db, self._al_cambiar_calendario)
            self.eventos.revisar()
        elif self.eventos is not None:
            self.eventos.detener()
            self.eventos = None

    async def _history(self) -> None:
        try:
            self.last_history = await asyncio.to_thread(run_history_maintenance, self.db, self.clock.now(),
                                                        self.lease.holder)
        except Exception as e:
            self._error(f"mantenimiento de historial: {e}")
            log.exception("fallo en mantenimiento de historial")

    async def run(self) -> None:
        self.running = True
        self._loop = asyncio.get_running_loop()
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
                    if (leader != was_leader or time.monotonic() - self.last_eventos_at >= 5) and (
                            leader or self.eventos is not None):
                        self.last_eventos_at = time.monotonic()
                        await asyncio.to_thread(self._revisar_eventos, leader)
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
                        # v1.4: el envío de alertas va en su propia tarea. Antes se esperaba aquí y
                        # un destino colgado paraba consultas, asignaciones y la renovación del arrendamiento.
                        if self.guard() and (self._dispatch_task is None or self._dispatch_task.done()):
                            self._dispatch_task = asyncio.create_task(self._dispatch(params))
                        now_m = time.monotonic()
                        if (now_m - self.last_captura_at >= 2 and self.guard()
                                and (self._captura_task is None or self._captura_task.done())):
                            self.last_captura_at = now_m
                            self._captura_task = asyncio.create_task(self._captura())
                        if (now_m - self.last_history_at >= float(params["mantenimiento_historial_cada_s"])
                                and (self._history_task is None or self._history_task.done())):
                            self.last_history_at = now_m
                            self._history_task = asyncio.create_task(self._history())
                        every = float(params.get("autodiagnostico_cada_s", 600))
                        if (every > 0 and now_m - self.last_selfcheck_at >= every
                                and (self._selfcheck_task is None or self._selfcheck_task.done())):
                            self.last_selfcheck_at = now_m
                            self._selfcheck_task = asyncio.create_task(self._selfcheck())
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
                if self.consecutive_cycle_errors == 0:
                    await self._sleep_or_wake(delay)
                else:  # con errores se respeta la espera creciente
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=delay)
                    except asyncio.TimeoutError:
                        pass
        finally:
            if polls:
                await asyncio.gather(*polls, return_exceptions=True)
            for task in (self._history_task, self._selfcheck_task, self._dispatch_task, self._captura_task):
                if task:
                    await asyncio.gather(task, return_exceptions=True)
            if self.eventos is not None:
                await asyncio.to_thread(self.eventos.detener)
                self.eventos = None
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
                "ultimo_autodiagnostico": getattr(self, "last_selfcheck", None),
                "ultimo_mantenimiento_historial": self.last_history,
                "oyentes_calendario": self.eventos.estado() if self.eventos else {},
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
