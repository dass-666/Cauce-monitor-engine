"""Prueba de resistencia (Fase 1.1): 7 días virtuales con fallos INYECTADOS.

QUÉ ES Y QUÉ NO ES
- Es una simulación acelerada con reloj virtual sobre una base temporal aislada, que usa el
  mismo código del motor (consulta, detector, asignación, alertas, retención, arrendamiento y
  recuperación). Los fallos (caídas, errores de base de datos, destinatarios inalcanzables,
  fuentes caídas) se PROVOCAN a propósito y se cuentan por separado.
- Tiempo, CPU y memoria son mediciones reales DE ESTE PROCESO en la máquina donde se ejecuta,
  no de un servidor de producción. Las latencias "virtuales" dependen del reloj simulado.
- "Dos instancias" son dos candidatos en hilos distintos compitiendo por el mismo arrendamiento
  en la misma base (concurrencia real sobre la BD). La prueba con dos PROCESOS reales y kill -9
  está en tests/test_restart.py.

Uso:
    python scripts/resistencia.py                      (SQLite temporal)
    python scripts/resistencia.py --db postgresql+psycopg://usuario@host/base_de_pruebas
    python scripts/resistencia.py --dias 1 --solicitudes-por-region 50   (versión corta)
ATENCIÓN: con --db la base indicada se BORRA por completo; usa solo una base de pruebas.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
import tempfile
import threading
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MONITOREO_SECRET_KEY", "resistencia")

import psutil  # noqa: E402
from sqlalchemy import func, select, update  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402

from monitoreo import security  # noqa: E402
from monitoreo.db import Database  # noqa: E402
from monitoreo.engine import alerts as al  # noqa: E402
from monitoreo.engine import assignment as asg  # noqa: E402
from monitoreo.engine.clock import VirtualClock  # noqa: E402
from monitoreo.engine.integrity import check  # noqa: E402
from monitoreo.engine.lease import Lease  # noqa: E402
from monitoreo.engine.params import get_params, set_params  # noqa: E402
from monitoreo.engine.pipeline import (RuntimeState, due_sources, maintenance, poll_source,  # noqa: E402
                                       process_pending_events)
from monitoreo.engine.recovery import run_recovery  # noqa: E402
from monitoreo.engine.retention import run_history_maintenance  # noqa: E402
from monitoreo.models import (Alert, Assignment, AuditLog, ChangeEvent, Channel, Delivery,  # noqa: E402
                              NotificationLog, PollLog, PollStatDaily, Request, RequestEvent, Source)
from monitoreo.simulator.seed import HOLDERS, seed_demo  # noqa: E402
from monitoreo.simulator.world import FEEDS, World  # noqa: E402

INJECTED = "FALLO_INYECTADO"


class FaultyDatabase(Database):
    """Base de datos que falla a propósito con probabilidad `p`: al abrir la transacción
    (conexión perdida) o justo antes de confirmarla (el trabajo hecho se revierte)."""

    def __init__(self, url: str, seed: int):
        super().__init__(url)
        self.p = 0.0
        self.rng = random.Random(seed)
        self.injected = Counter()
        self._lock = threading.Lock()

    def _roll(self, where: str) -> None:
        if self.p <= 0:
            return
        with self._lock:
            hit = self.rng.random() < self.p / 2
            if hit:
                self.injected[where] += 1
        if hit:
            raise OperationalError(INJECTED, {}, Exception(f"{INJECTED}: {where}"))

    @contextmanager
    def session(self):
        self._roll("al_abrir")
        with super().session() as s:
            yield s
            self._roll("antes_de_confirmar")


class Instance:
    """Una instancia del motor: su arrendamiento y su estado en memoria."""
    n = 0

    def __init__(self, db, ttl_s):
        Instance.n += 1
        self.name = f"instancia-{Instance.n}"
        self.lease = Lease(db, holder=f"{self.name}:{random.getrandbits(24):06x}", ttl_s=ttl_s)
        self.runtime = RuntimeState()
        self.was_leader = False
        self.cycles = 0


def run(days: float, step_s: float, per_region: int, new_per_hour: int, seed: int, db_url: str | None,
        keep: bool) -> dict:
    rng = random.Random(seed)
    tmpdir = None
    if db_url is None:
        tmpdir = tempfile.mkdtemp(prefix="monitoreo_resistencia_")
        db_url = f"sqlite:///{Path(tmpdir, 'resistencia.db').as_posix()}"
    db = FaultyDatabase(db_url, seed + 1)
    db.reset_for_tests()
    db.create_all()
    clock = VirtualClock(datetime(2030, 3, 4, 0, 0, 0))
    seed_demo(db, rng=rng, requests_per_region=per_region, interval_s=step_s, with_file_sources=False, clock=clock)

    # ---- configuración del escenario
    os.environ.update({"MONITOREO_TELEGRAM_TOKEN": "999:RESISTENCIA", "MONITOREO_TELEGRAM_CHATS": "1001,1002",
                       "MONITOREO_TELEGRAM_API": "http://127.0.0.1:9"})   # puerto cerrado: inalcanzable
    lease_ttl = step_s * 3
    with db.session() as s:
        set_params(s, {"ttl_asignacion_s": 900, "retencion_consultas_dias": 2,
                       "retencion_eventos_sin_alerta_dias": 3, "retencion_estadisticas_horarias_dias": 5,
                       "retencion_pausa_lote_s": 0.0, "retencion_lote": 500})
        for src in s.scalars(select(Source)):
            src.config = {**src.config, "error_rate": 0.03, "rate_limit_rate": 0.01, "duplicate_rate": 0.05,
                          "retry_after_s": 120}
            src.rate_limit_per_min = 60
        for ch in s.scalars(select(Channel)):
            ch.enabled = ch.type in ("panel", "simulado", "webhook", "telegram")
            if ch.type == "simulado":
                ch.config = {"fail_rate": 0.1}
            if ch.type == "webhook":
                ch.config = {"url": "http://127.0.0.1:9/inalcanzable", "timeout_s": 1}
            if ch.type == "telegram":
                ch.config = {"timeout_s": 1}
        down_source = s.scalar(select(Source.id).where(Source.region == "SUR"))
        total_requests = s.scalar(select(func.count()).select_from(Request))
    al.SimulatedSender.rng.seed(seed)
    al.SimulatedSender.received = []
    world = World(db, clock=clock, rng=rng)
    with db.session() as s:
        params = get_params(s)

    steps = int(days * 86400 / step_s)
    per_day = int(86400 / step_s)
    hour_steps = max(1, int(3600 / step_s))
    crash_steps = {d * per_day + rng.randint(per_day // 4, per_day - per_day // 4) for d in range(int(days))}
    db_fault_windows = [(1 * per_day + 6 * hour_steps, 1 * per_day + 12 * hour_steps, 0.05),   # día 2: 5 %
                        (5 * per_day, 5 * per_day + 2 * hour_steps, 0.20)]                    # día 6: 20 %
    down_window = (2 * per_day + 10 * hour_steps, 2 * per_day + 18 * hour_steps)              # día 3: 8 h caída

    # ---- medición
    proc = psutil.Process()
    cpu0 = proc.cpu_times()
    rss = {"inicial_mb": proc.memory_info().rss / 1048576, "pico_mb": 0.0}
    sampling = threading.Event()

    def sampler():
        while not sampling.wait(0.5):
            rss["pico_mb"] = max(rss["pico_mb"], proc.memory_info().rss / 1048576)
    threading.Thread(target=sampler, daemon=True).start()

    errors = Counter()
    unexpected: list[str] = []

    def guarded(name, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as e:  # noqa: BLE001
            if INJECTED in str(e):
                errors[f"{name}:inyectado"] += 1
            else:
                errors[f"{name}:NO_INYECTADO"] += 1
                if len(unexpected) < 30:
                    unexpected.append(f"{clock.now().isoformat()} {name}: {type(e).__name__}: {str(e)[:200]}")
            return None

    # ---- instancias que compiten por el planificador
    instances = [Instance(db, lease_ttl), Instance(db, lease_ttl)]
    leadership = Counter()
    double_leader_steps = 0
    no_leader_steps = 0
    recoveries: list[dict] = []
    crashes: list[dict] = []
    # ---- mantenimiento de historial en paralelo (hilo real, mientras el motor trabaja)
    hist_runs: list[dict] = []
    hist_thread: threading.Thread | None = None
    protected_max = {"alertas": 0, "asignaciones": 0, "auditoria": 0, "solicitudes": 0, "eventos_solicitud": 0}
    protected_drops: list[str] = []
    integrity_checks, violations = 0, []
    reprocess = {"pasadas": 0, "eventos_reprocesados": 0, "alertas_nuevas_por_reproceso": 0}
    down = {"inicio": None, "error_en": None, "alertas_sistema": 0, "reanudada_en": None, "primer_ok_tras_reanudar": None}
    created_requests = 0
    cycle_ms: list[float] = []
    t_start = time.perf_counter()
    req_seq = total_requests

    for step in range(steps):
        now = clock.now()
        c0 = time.perf_counter()
        # Ventanas de fallo de base de datos
        db.p = next((p for a, b, p in db_fault_windows if a <= step < b), 0.0)
        # El mundo cambia siempre (es externo al motor)
        guarded("mundo", world.random_step, intensity=1.0, burst=step > 0 and step % 180 == 0)
        # Llegan solicitudes nuevas
        if step % hour_steps == 0:
            with_db_ok = guarded("nuevas_solicitudes", _new_requests, db, rng, now, new_per_hour, req_seq)
            if with_db_ok:
                req_seq += with_db_ok
                created_requests += with_db_ok
        # Fuente que deja de responder (día 3)
        if step % per_day == 0:
            guarded("auditoria", _audit, db, f"inicio del día virtual {step // per_day + 1}")
        if step == down_window[0]:
            guarded("caida_fuente", _set_source_error_rate, db, down_source, 1.0)
            down["inicio"] = now.isoformat()
        if step == down_window[1]:
            def _resume():  # lo que hace el administrador al pulsar "Reanudar"
                with db.session() as s:
                    src = s.get(Source, down_source)
                    src.config = {**src.config, "error_rate": 0.03}
                    src.status, src.circuit_state, src.circuit_opened_at = "ACTIVA", "CERRADO", None
                    src.consecutive_failures, src.circuit_open_count, src.next_poll_at = 0, 0, None
                    src.status_reason, src.status_changed_at = "Reanudada (prueba de resistencia)", now
                    security.audit(s, "admin-simulado", "fuente_reanudar", src.code)
            if guarded("reanudar_fuente", _resume) is None:
                down["reanudada_en"] = now.isoformat()
        # Ambas instancias intentan ser líder A LA VEZ (hilos reales sobre la misma BD)
        barrier = threading.Barrier(len(instances))
        results = {}

        def contend(inst):
            barrier.wait()
            results[inst.name] = guarded("arrendamiento", inst.lease.acquire_or_renew, now)
        threads = [threading.Thread(target=contend, args=(i,)) for i in instances]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        leaders = [i for i in instances if i.lease.is_valid(now)]
        if len(leaders) > 1:
            double_leader_steps += 1
        if not leaders:
            no_leader_steps += 1
        for inst in instances:
            if inst in leaders and not inst.was_leader:
                rep = guarded("recuperacion", run_recovery, db, now, inst.lease.holder,
                              0 if inst.cycles == 0 else inst.lease.ttl_s)
                if rep:
                    recoveries.append({"momento": now.isoformat(), "instancia": inst.name,
                                       **{k: v for k, v in rep.items() if isinstance(v, int) and v}})
            inst.was_leader = inst in leaders
            inst.cycles += 1
        if leaders:
            inst = leaders[0]
            leadership[inst.name] += 1
            guard = lambda: inst.lease.is_valid(clock.now())  # noqa: E731
            for sid in guarded("fuentes_pendientes", due_sources, db, now) or []:
                out = guarded("consulta", poll_source, db, sid, clock, inst.runtime, rng, None, guard)
                if sid == down_source and out is not None:
                    if down["reanudada_en"] and out.outcome == "OK" and not down["primer_ok_tras_reanudar"]:
                        down["primer_ok_tras_reanudar"] = now.isoformat()
            guarded("eventos", process_pending_events, db, clock, 500, guard)
            if step in crash_steps:
                # Caída inesperada justo después de reclamar entregas y ANTES de enviarlas:
                # quedan en ENVIANDO y la instancia desaparece sin liberar el arrendamiento.
                stuck = guarded("simular_caida", _simulate_inflight, db, now)
                crashes.append({"momento": now.isoformat(), "instancia": inst.name, "entregas_a_medias": stuck})
                instances[instances.index(inst)] = Instance(db, lease_ttl)   # el proceso se reinicia, en espera
                clock.advance(step_s)
                continue
            if step % 4 == 0:
                guarded("mantenimiento", maintenance, db, clock)
            # Dos despachadores a la vez (p. ej. un relevo mal coordinado): no debe enviarse nada dos veces
            d_threads = [threading.Thread(target=guarded, args=("despacho", al.dispatch_pending, db, now, params),
                                          kwargs={"rng": random.Random(step * 2 + k)}) for k in range(2)]
            for t in d_threads:
                t.start()
            for t in d_threads:
                t.join()
            guarded("operadores", _operators, db, now, rng, params)
        # ¿Cuándo detuvo el motor la fuente caída (estado ERROR, requiere intervención)?
        if down["inicio"] and not down["error_en"] and not down["reanudada_en"]:
            st = guarded("estado_fuente", _source_status, db, down_source)
            if st == "ERROR":
                down["error_en"] = now.isoformat()
        # Reproceso deliberado de eventos ya procesados (cada 6 h): no debe duplicar alertas
        if step % (6 * hour_steps) == 0 and step and db.p == 0:
            r = guarded("reproceso", _reprocess, db, clock, rng)
            if r:
                reprocess["pasadas"] += 1
                reprocess["eventos_reprocesados"] += r[0]
                reprocess["alertas_nuevas_por_reproceso"] += r[1]
        # Limpieza del historial cada 6 h en un hilo aparte, MIENTRAS el motor sigue trabajando
        if step % (6 * hour_steps) == 3 and (hist_thread is None or not hist_thread.is_alive()):
            def _hist(t=now):
                t0 = time.perf_counter()
                res = guarded("historial", run_history_maintenance, db, t, "resistencia", time.sleep)
                if res:
                    hist_runs.append({"momento": t.isoformat(), "s_real": round(time.perf_counter() - t0, 3),
                                      "agregado": res.get("agregado"), "borrado": res.get("borrado")})
            hist_thread = threading.Thread(target=_hist, daemon=True)
            hist_thread.start()
        cycle_ms.append((time.perf_counter() - c0) * 1000)
        # Integridad y tablas protegidas (cada 2 h virtuales)
        if step % (2 * hour_steps) == 0 and db.p == 0:
            r = guarded("integridad", _integrity, db, protected_max, protected_drops)
            if r is not None:
                integrity_checks += 1
                violations += r
        clock.advance(step_s)

    if hist_thread:
        hist_thread.join()
    db.p = 0.0
    # Cierre ordenado: el líder recupera lo que quede a medias y vacía colas
    now = clock.now()
    final_leader = instances[0]
    clock.advance(lease_ttl + 1)
    now = clock.now()
    for inst in instances:
        inst.lease.acquire_or_renew(now)
    run_recovery(db, now, final_leader.lease.holder, 0)
    process_pending_events(db, clock)
    for _ in range(int(params["reintentos_notificacion"]) + 2):
        clock.advance(600)
        al.dispatch_pending(db, clock.now(), params, rng=random.Random(1))
    final_hist = run_history_maintenance(db, clock.now(), "resistencia-final", sleep_fn=lambda _: None)
    wall = time.perf_counter() - t_start
    sampling.set()
    cpu1 = proc.cpu_times()
    rss["pico_mb"] = max(rss["pico_mb"], proc.memory_info().rss / 1048576)

    with db.session() as s:
        final_violations = check(s)
        violations += final_violations
        integrity_checks += 1
        dup_sends = s.execute(select(NotificationLog.alert_id, NotificationLog.channel, func.count())
                              .where(NotificationLog.outcome == "OK")
                              .group_by(NotificationLog.alert_id, NotificationLog.channel)
                              .having(func.count() > 1)).all()
        dup_alert_keys = s.execute(select(Alert.dedup_key, func.count()).group_by(Alert.dedup_key)
                                   .having(func.count() > 1)).all()
        by_channel = {}
        for name, status, n in s.execute(select(Channel.name, Delivery.status, func.count())
                                         .join(Channel, Channel.id == Delivery.channel_id)
                                         .group_by(Channel.name, Delivery.status)):
            by_channel.setdefault(name, {})[status] = n
        raw_after_wm = None
        wm_row = s.execute(select(func.min(PollLog.started_at))).scalar()
        polls_daily = s.scalar(select(func.coalesce(func.sum(PollStatDaily.polls), 0)))
        from monitoreo.engine.retention import _get_watermark
        wm = _get_watermark(s)
        raw_after_wm = s.scalar(select(func.count()).select_from(PollLog).where(PollLog.started_at >= wm)) if wm else None
        max_poll_id = s.scalar(select(func.max(PollLog.id)))
        down_code = s.get(Source, down_source).code
        down["alertas_sistema"] = s.scalar(select(func.count()).select_from(Alert).where(
            Alert.kind == "SISTEMA", Alert.source_code == down_code))
        res = {
            "aviso": ("Simulación acelerada con reloj virtual y fallos INYECTADOS a propósito. Tiempo, CPU y "
                      "memoria son mediciones reales de este proceso en esta máquina; no son mediciones de un "
                      "servidor en producción."),
            "maquina": {"cpus": psutil.cpu_count(), "python": sys.version.split()[0], "plataforma": sys.platform},
            "parametros": {"dias_virtuales": days, "paso_s": step_s, "semilla": seed,
                           "solicitudes_iniciales": total_requests, "solicitudes_nuevas_por_hora": new_per_hour,
                           "base_de_datos": "sqlite temporal" if tmpdir else db_url.split("://")[0],
                           "arrendamiento_ttl_s_virtual": lease_ttl},
            "tiempo_real_s": round(wall, 1),
            "aceleracion_x": round(days * 86400 / wall, 1),
            "cpu_s": round((cpu1.user - cpu0.user) + (cpu1.system - cpu0.system), 1),
            "cpu_medio_pct_de_un_nucleo": round(100 * ((cpu1.user - cpu0.user) + (cpu1.system - cpu0.system)) / wall, 1),
            "memoria_mb": {"inicial": round(rss["inicial_mb"], 1), "pico": round(rss["pico_mb"], 1)},
            "ciclo_ms": _pct(cycle_ms),
            "solicitudes": {"total": total_requests + created_requests,
                            "por_estado": dict(s.execute(select(Request.status, func.count()).group_by(Request.status)).all())},
            "asignaciones": dict(s.execute(select(Assignment.status, func.count()).group_by(Assignment.status)).all()),
            "eventos": s.scalar(select(func.count()).select_from(ChangeEvent)),
            "eventos_sin_procesar_al_final": s.scalar(select(func.count()).select_from(ChangeEvent)
                                                      .where(ChangeEvent.processed.is_(False))),
            "alertas_por_tipo": dict(s.execute(select(Alert.kind, func.count()).group_by(Alert.kind)).all()),
            "entregas_por_canal": by_channel,
            "fallos_inyectados": {"base_de_datos": dict(db.injected), "caidas_de_instancia": len(crashes),
                                  "fuente_caida_horas": (down_window[1] - down_window[0]) / hour_steps,
                                  "destinatarios_inalcanzables": ["Webhook local (127.0.0.1:9)",
                                                                  "Telegram (API en 127.0.0.1:9)"]},
            "errores_capturados_por_fase": dict(errors),
            "errores_no_inyectados": unexpected,
            "liderazgo": {"pasos_con_dos_lideres": double_leader_steps, "pasos_sin_lider": no_leader_steps,
                          "pasos_por_instancia": dict(leadership), "caidas": crashes, "recuperaciones": recoveries},
            "fuente_caida": down,
            "reproceso_de_eventos": reprocess,
            "envios_duplicados": len(dup_sends), "claves_de_alerta_duplicadas": len(dup_alert_keys),
            "historial": {"pasadas_en_paralelo": len(hist_runs), "ultimas": hist_runs[-3:], "final": final_hist,
                          "filas_poll_log_al_final": s.scalar(select(func.count()).select_from(PollLog)),
                          "consultas_en_agregados_diarios": polls_daily, "consultas_sin_agregar": raw_after_wm,
                          "consultas_registradas_total_sqlite_max_id": max_poll_id if db.is_sqlite else None,
                          "consultas_cuadran": (polls_daily + (raw_after_wm or 0) == max_poll_id) if db.is_sqlite else None,
                          "consulta_mas_antigua_conservada": wm_row.isoformat() if wm_row else None},
            "tablas_protegidas": {"maximos": protected_max, "disminuciones": protected_drops},
            "integridad": {"verificaciones": integrity_checks, "violaciones": len(violations),
                           "ejemplos": violations[:10]},
            "tamano_bd_mb": round(Path(db_url.split("///", 1)[1]).stat().st_size / 1048576, 1) if db.is_sqlite else None,
        }
    db.engine.dispose()
    if tmpdir and not keep:
        shutil.rmtree(tmpdir, ignore_errors=True)
    elif tmpdir:
        res["base_conservada_en"] = tmpdir
    return res


def _audit(db, text):
    with db.session() as s:
        security.audit(s, "resistencia", "marca", text)


def _source_status(db, sid):
    with db.session() as s:
        return s.get(Source, sid).status


def _new_requests(db, rng, now, n, seq) -> int:
    with db.session() as s:
        for k in range(n):
            region, _atype, rtype, _ = rng.choice(FEEDS)
            seq += 1
            s.add(Request(code=f"SOL-R{seq:05d}", holder=f"{rng.choice(HOLDERS)} R{seq:05d}", region=region,
                          resource_type=rtype, priority=rng.choice([1, 2, 3, 3, 4, 5]), quantity=rng.choice([1, 1, 2]),
                          conditions={}, status="PENDIENTE", created_at=now))
    return n


def _set_source_error_rate(db, sid, rate):
    with db.session() as s:
        src = s.get(Source, sid)
        src.config = {**src.config, "error_rate": rate}


def _simulate_inflight(db, now) -> int:
    """Reclama hasta 20 entregas pendientes como haría el despachador, sin llegar a enviarlas."""
    with db.session() as s:
        ids = list(s.scalars(select(Delivery.id).where(Delivery.status == "PENDIENTE").limit(20)))
        if ids:
            s.execute(update(Delivery).where(Delivery.id.in_(ids)).values(status="ENVIANDO", claimed_at=now))
        return len(ids)


def _operators(db, now, rng, params):
    with db.session() as s:
        active = list(s.execute(select(Assignment.id, Assignment.operator_id).where(Assignment.status == "ACTIVA")))
        working = list(s.execute(select(Assignment.id, Assignment.operator_id).where(Assignment.status == "EN_PROCESO")))
    for aid, op in active:
        r = rng.random()
        if r < 0.05:
            asg.reject_assignment(db, aid, op, now, "sim-operador", params)
        elif r < 0.55:
            asg.take_assignment(db, aid, op, now, "sim-operador")
    for aid, op in working:
        if rng.random() < 0.4:
            asg.complete_assignment(db, aid, op, now, "sim-operador")


def _reprocess(db, clock, rng) -> tuple[int, int]:
    with db.session() as s:
        ids = list(s.scalars(select(ChangeEvent.id).where(ChangeEvent.processed.is_(True), ChangeEvent.relevant.is_(True))
                             .order_by(ChangeEvent.id.desc()).limit(200)))
        ids = rng.sample(ids, min(30, len(ids)))
        keys = [f"evt:{k}" for k in s.scalars(select(ChangeEvent.event_key).where(ChangeEvent.id.in_(ids)))]
        before = s.scalar(select(func.count()).select_from(Alert).where(Alert.dedup_key.in_(keys))) if keys else 0
        if ids:
            s.execute(update(ChangeEvent).where(ChangeEvent.id.in_(ids)).values(processed=False))
    process_pending_events(db, clock)
    with db.session() as s:
        after = s.scalar(select(func.count()).select_from(Alert).where(Alert.dedup_key.in_(keys))) if keys else 0
    return len(ids), after - before


def _integrity(db, maxima, drops) -> list[str]:
    with db.session() as s:
        counts = {"alertas": s.scalar(select(func.count()).select_from(Alert)),
                  "asignaciones": s.scalar(select(func.count()).select_from(Assignment)),
                  "auditoria": s.scalar(select(func.count()).select_from(AuditLog)),
                  "solicitudes": s.scalar(select(func.count()).select_from(Request)),
                  "eventos_solicitud": s.scalar(select(func.count()).select_from(RequestEvent))}
        for k, v in counts.items():
            if v < maxima[k]:
                drops.append(f"{k}: {maxima[k]} -> {v}")
            maxima[k] = max(maxima[k], v)
        return check(s)


def _pct(values):
    if not values:
        return {"n": 0}
    v = sorted(values)
    q = lambda p: round(v[min(len(v) - 1, int(p * (len(v) - 1)))], 2)  # noqa: E731
    return {"n": len(v), "p50": q(0.5), "p95": q(0.95), "p99": q(0.99), "max": round(v[-1], 2)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dias", type=float, default=7)
    ap.add_argument("--paso", type=float, default=60, help="segundos virtuales por paso")
    ap.add_argument("--solicitudes-por-region", type=int, default=200)
    ap.add_argument("--nuevas-por-hora", type=int, default=4)
    ap.add_argument("--semilla", type=int, default=11)
    ap.add_argument("--db", default=None, help="URL de una base de PRUEBAS (se borra entera)")
    ap.add_argument("--salida", default=None, help="archivo JSON de resultados")
    ap.add_argument("--conservar", action="store_true", help="no borrar la base SQLite temporal")
    a = ap.parse_args()
    res = run(a.dias, a.paso, a.solicitudes_por_region, a.nuevas_por_hora, a.semilla, a.db, a.conservar)
    text = json.dumps(res, ensure_ascii=False, indent=2, default=str)
    if a.salida:
        Path(a.salida).write_text(text, encoding="utf-8")
    print(text)
    ok = (not res["integridad"]["violaciones"] and not res["liderazgo"]["pasos_con_dos_lideres"]
          and not res["envios_duplicados"] and not res["claves_de_alerta_duplicadas"]
          and not res["tablas_protegidas"]["disminuciones"] and not res["errores_no_inyectados"]
          and not res["reproceso_de_eventos"]["alertas_nuevas_por_reproceso"])
    print("\nRESULTADO:", "SIN VIOLACIONES" if ok else "HAY PROBLEMAS (ver arriba)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
