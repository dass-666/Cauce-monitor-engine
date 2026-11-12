"""Simulación acelerada: muchas horas de actividad en segundos.

Se ejecuta SIEMPRE sobre una base de datos temporal aislada, para no mezclar
miles de eventos de prueba (con fechas virtuales futuras) con los datos de
operación del panel. Usa exactamente el mismo código del motor (poll_source,
detector, asignación, alertas) con un reloj virtual.
"""
from __future__ import annotations

import os
import random
import statistics
import tempfile
import time
from collections import Counter
from datetime import datetime

import psutil
from sqlalchemy import func, select

from ..db import Database
from ..engine import alerts as al
from ..engine import assignment as asg
from ..engine.clock import VirtualClock
from ..engine.integrity import check
from ..engine.params import get_params, set_params
from ..engine.pipeline import RuntimeState, due_sources, maintenance, poll_source, process_pending_events
from ..models import Alert, Assignment, ChangeEvent, Delivery, PollLog, Request, SlotState, Source
from .seed import seed_demo
from .world import World


def run_accelerated(hours: float = 24, step_s: float = 30, seed: int = 7, requests_per_region: int = 40,
                    interval_s: float = 30, error_rate: float = 0.03, rate_limit_rate: float = 0.01,
                    duplicate_rate: float = 0.05, notify_fail_rate: float = 0.1, intensity: float = 1.0,
                    burst_every_steps: int = 120, operator_take_p: float = 0.5, operator_complete_p: float = 0.4,
                    operator_reject_p: float = 0.05, db_url: str | None = None, keep_db: bool = False) -> dict:
    rng = random.Random(seed)
    tmpdir = None
    if db_url is None:
        tmpdir = tempfile.mkdtemp(prefix="monitoreo_sim_")
        db_url = f"sqlite:///{os.path.join(tmpdir, 'sim.db')}"
    db = Database(db_url)
    db.drop_all()
    db.create_all()
    clock = VirtualClock(datetime(2030, 1, 1, 8, 0, 0))
    seed_demo(db, rng=rng, requests_per_region=requests_per_region, interval_s=interval_s,
              with_file_sources=False, clock=clock)
    with db.session() as s:
        set_params(s, {"ttl_asignacion_s": 600})
        for src in s.scalars(select(Source)):
            src.config = {**src.config, "error_rate": error_rate, "rate_limit_rate": rate_limit_rate,
                          "duplicate_rate": duplicate_rate, "retry_after_s": 60}
            src.rate_limit_per_min = 60
        from ..models import Channel
        for ch in s.scalars(select(Channel)):
            ch.enabled = ch.type in ("panel", "simulado")
            if ch.type == "simulado":
                ch.config = {"fail_rate": notify_fail_rate}
    al.SimulatedSender.rng.seed(seed)
    world = World(db, clock=clock, rng=rng)
    runtime = RuntimeState()
    proc = psutil.Process()
    cpu0 = proc.cpu_times()
    rss_peak = proc.memory_info().rss
    t_start = time.perf_counter()
    steps = int(hours * 3600 / step_s)
    sim_changes = 0
    poll_ms: list[float] = []
    cycle_ms: list[float] = []
    integrity_checks, violations = 0, []
    maintenance(db, clock)
    with db.session() as s:
        params = get_params(s)
    for step in range(steps):
        c0 = time.perf_counter()
        # 1) el mundo cambia (incluye picos de cambios simultáneos)
        burst = burst_every_steps > 0 and step > 0 and step % burst_every_steps == 0
        sim_changes += len(world.random_step(intensity=intensity, burst=burst))
        # 2) el planificador consulta las fuentes que tocan
        for sid in due_sources(db, clock.now()):
            out = poll_source(db, sid, clock, runtime, rng)
            if out.outcome not in ("OMITIDA", "CIRCUITO_ABIERTO", "LIMITE_LOCAL"):
                poll_ms.append(out.duration_ms)
        # 3) eventos -> asignaciones -> alertas -> envío
        process_pending_events(db, clock)
        if step % 4 == 0:
            maintenance(db, clock)
        al.dispatch_pending(db, clock.now(), params, rng=rng)
        # 4) operadores ficticios actúan
        _simulate_operators(db, clock, rng, params, operator_take_p, operator_complete_p, operator_reject_p)
        cycle_ms.append((time.perf_counter() - c0) * 1000)
        if step % 50 == 0:
            rss_peak = max(rss_peak, proc.memory_info().rss)
            with db.session() as s:
                integrity_checks += 1
                violations += check(s)
        clock.advance(step_s)
    wall = time.perf_counter() - t_start
    cpu1 = proc.cpu_times()
    rss_peak = max(rss_peak, proc.memory_info().rss)
    with db.session() as s:
        integrity_checks += 1
        final_violations = check(s)
        violations += final_violations
        res = {
            "parametros": {"horas_virtuales": hours, "paso_s": step_s, "semilla": seed,
                           "solicitudes_por_region": requests_per_region, "intervalo_fuente_s": interval_s,
                           "tasa_error_fuente": error_rate, "tasa_limitacion": rate_limit_rate,
                           "tasa_duplicados": duplicate_rate, "tasa_fallo_notificacion": notify_fail_rate,
                           "base_de_datos": "sqlite temporal" if tmpdir else db_url.split("://")[0]},
            "tiempo_real_s": round(wall, 2),
            "aceleracion_x": round(hours * 3600 / wall, 1) if wall else None,
            "cambios_simulados": sim_changes,
            "consultas": s.scalar(select(func.count()).select_from(PollLog)),
            "consultas_por_resultado": dict(s.execute(select(PollLog.outcome, func.count())
                                                      .group_by(PollLog.outcome)).all()),
            "duplicados_descartados": s.scalar(select(func.coalesce(func.sum(PollLog.duplicates), 0))),
            "eventos_detectados": s.scalar(select(func.count()).select_from(ChangeEvent)),
            "eventos_por_tipo": dict(s.execute(select(ChangeEvent.change_type, func.count())
                                               .group_by(ChangeEvent.change_type)).all()),
            "eventos_pendientes": s.scalar(select(func.count()).select_from(ChangeEvent)
                                           .where(ChangeEvent.processed.is_(False))),
            "asignaciones": dict(s.execute(select(Assignment.status, func.count()).group_by(Assignment.status)).all()),
            "solicitudes": dict(s.execute(select(Request.status, func.count()).group_by(Request.status)).all()),
            "alertas": dict(s.execute(select(Alert.kind, func.count()).group_by(Alert.kind)).all()),
            "alertas_por_estado": dict(s.execute(select(Alert.status, func.count()).group_by(Alert.status)).all()),
            "entregas": dict(s.execute(select(Delivery.status, func.count()).group_by(Delivery.status)).all()),
            "regiones": sorted({r for (r,) in s.execute(select(SlotState.region).distinct())}),
            "latencia_deteccion_virtual_s": _pct([e / 1000 for (e,) in s.execute(
                select(ChangeEvent.detection_latency_ms).where(ChangeEvent.detection_latency_ms.is_not(None)))]),
            "duracion_consulta_ms": _pct(poll_ms),
            "duracion_ciclo_ms": _pct(cycle_ms),
            "cpu_s": round((cpu1.user - cpu0.user) + (cpu1.system - cpu0.system), 2),
            "memoria_pico_mb": round(rss_peak / 1024 / 1024, 1),
            "verificaciones_integridad": integrity_checks,
            "violaciones_integridad": violations[:20],
            "total_violaciones": len(violations),
        }
    db.engine.dispose()
    if tmpdir and not keep_db:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    return res


def _pct(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    v = sorted(values)

    def q(p):
        return round(v[min(len(v) - 1, int(p * (len(v) - 1)))], 3)

    return {"n": len(v), "media": round(statistics.fmean(v), 3), "p50": q(0.5), "p95": q(0.95), "p99": q(0.99),
            "max": round(v[-1], 3)}


def _simulate_operators(db, clock, rng, params, take_p, complete_p, reject_p):
    now = clock.now()
    with db.session() as s:
        active = list(s.execute(select(Assignment.id, Assignment.operator_id).where(Assignment.status == "ACTIVA")))
        working = list(s.execute(select(Assignment.id, Assignment.operator_id)
                                 .where(Assignment.status == "EN_PROCESO")))
    for aid, op in active:
        r = rng.random()
        if r < reject_p:
            asg.reject_assignment(db, aid, op, now, "sim-operador", params)
        elif r < reject_p + take_p:
            asg.take_assignment(db, aid, op, now, "sim-operador")
    for aid, op in working:
        if rng.random() < complete_p:
            asg.complete_assignment(db, aid, op, now, "sim-operador")
