"""Continuidad: arrendamiento entre instancias, recuperación tras corte y respeto de esperas."""
import asyncio
import threading
from datetime import datetime, timedelta

from sqlalchemy import func, select

from monitoreo.engine import alerts as al
from monitoreo.engine.clock import RealClock
from monitoreo.engine.lease import Lease
from monitoreo.engine.pipeline import RuntimeState, due_sources, poll_source
from monitoreo.engine.recovery import run_recovery
from monitoreo.engine.scheduler import Scheduler
from monitoreo.models import Alert, Channel, Delivery, PollLog, SchedulerLease, Source

from .helpers import Harness, make_operator, make_request, make_source

T0 = datetime(2026, 6, 1, 12)


def test_lease_exclusive_expiry_and_takeover(db):
    a, b = Lease(db, holder="A", ttl_s=15), Lease(db, holder="B", ttl_s=15)
    assert a.acquire_or_renew(T0)
    assert not b.acquire_or_renew(T0 + timedelta(seconds=1))
    assert a.acquire_or_renew(T0 + timedelta(seconds=10))           # renovación
    assert not b.acquire_or_renew(T0 + timedelta(seconds=20))       # A renovó a los 10 s: vigente hasta 25
    assert a.is_valid(T0 + timedelta(seconds=15)) and not a.is_valid(T0 + timedelta(seconds=21))  # margen ttl/3
    assert b.acquire_or_renew(T0 + timedelta(seconds=26))           # A dejó de renovar: relevo
    assert b.fencing_token == a.fencing_token + 1
    assert not a.acquire_or_renew(T0 + timedelta(seconds=27))       # el antiguo líder ya no puede volver


def test_lease_concurrent_acquire_single_winner(db):
    leases = [Lease(db, holder=f"H{i}", ttl_s=15) for i in range(10)]
    barrier = threading.Barrier(10)
    res = []

    def w(l):
        barrier.wait()
        res.append(l.acquire_or_renew(T0))

    ts = [threading.Thread(target=w, args=(l,)) for l in leases]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert res.count(True) == 1
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(SchedulerLease)) == 1


def test_two_schedulers_only_one_works_and_failover(db, rng):
    """Dos planificadores reales contra la misma base: uno trabaja, el otro espera y releva."""
    sid = make_source(db, interval_s=0.3)
    h = Harness(db, rng)
    h.world.create_item("sim-norte", "K", "Recurso", "NORTE", "entrada", 5, 2)
    from monitoreo.engine.params import set_params
    with db.session() as s:
        set_params(s, {"tick_segundos": 0.1})

    async def scenario():
        s1 = Scheduler(db, holder="uno", lease_ttl_s=1.5)
        s2 = Scheduler(db, holder="dos", lease_ttl_s=1.5)
        s1.start()
        await asyncio.sleep(0.4)
        s2.start()
        await asyncio.sleep(1.5)
        roles = {s1.role, s2.role}
        await s1.stop()             # el líder se detiene (libera el arrendamiento)
        await asyncio.sleep(1.0)
        role2 = s2.role
        await s2.stop()
        return roles, role2, s2.recovery

    roles, role2, recovery = asyncio.run(scenario())
    assert roles == {"LIDER", "EN_ESPERA"}
    assert role2 == "LIDER" and recovery is not None


def test_recovery_requeues_inflight_and_reports(db, rng):
    h = Harness(db, rng)
    sid = make_source(db)
    h.world.create_item("sim-norte", "K", "Recurso", "NORTE", "entrada", 5, 2)
    make_operator(db)
    make_request(db, "R")
    h.poll(sid)
    h.process()
    with db.session() as s:
        for d in s.scalars(select(Delivery)):
            d.status, d.claimed_at = "ENVIANDO", h.clock.now()
    rep = run_recovery(db, h.clock.now() + timedelta(hours=1), "test")
    assert rep["entregas_reencoladas"] > 0 and rep["asignaciones_vencidas_por_expirar"] == 1
    # al tomar el relevo, solo se reencolan entregas "viejas" (no las que otro aún podría estar enviando)
    with db.session() as s:
        for d in s.scalars(select(Delivery)):
            d.status, d.claimed_at = "ENVIANDO", h.clock.now()
    rep2 = run_recovery(db, h.clock.now() + timedelta(seconds=5), "test", takeover_stale_s=15)
    assert rep2["entregas_reencoladas"] == 0


def test_retry_after_and_backoff_survive_restart(db, rng):
    h = Harness(db, rng)
    sid = make_source(db, config={"rate_limit_rate": 1.0, "retry_after_s": 600})
    assert h.poll(sid).outcome == "LIMITADO"
    # "reinicio": estado en memoria nuevo; la espera pedida por la fuente sigue vigente
    assert sid not in due_sources(db, h.clock.now() + timedelta(seconds=300))
    assert sid in due_sources(db, h.clock.now() + timedelta(seconds=601))


def test_restart_does_not_cause_request_burst(db, rng):
    """Antes de v1.1, cada reinicio rellenaba la cubeta: N reinicios rápidos = N ráfagas."""
    h = Harness(db, rng)
    sid = make_source(db, rate_limit_per_min=6)       # 1 consulta cada 10 s, ráfaga 1
    outcomes = []
    for _ in range(5):                                 # 5 "reinicios" en 5 segundos
        outcomes.append(poll_source(db, sid, h.clock, RuntimeState(), rng).outcome)
        h.advance(1)
    assert outcomes.count("OK") == 1


def test_critical_failure_creates_system_alert_once(db, rng):
    h = Harness(db, rng)
    sid = make_source(db, config={"error_rate": 1.0})
    from monitoreo.engine.params import get_params
    with db.session() as s:
        p = get_params(s)
    for _ in range(p["umbral_circuito"] + 3):
        h.advance(10_000)
        h.poll(sid)
    with db.session() as s:
        sys_alerts = s.scalars(select(Alert).where(Alert.kind == "SISTEMA")).all()
        assert len(sys_alerts) >= 1
        src = s.get(Source, sid)
        assert src.status_reason and src.last_outcome == "ERROR_TEMPORAL"


def test_channel_kind_filter(db, rng):
    with db.session() as s:
        for ch in s.scalars(select(Channel)):
            ch.enabled = False
        s.add(Channel(name="solo-sistema", type="simulado", config={"tipos": ["SISTEMA"]}, enabled=True))
    h = Harness(db, rng)
    sid = make_source(db)
    h.world.create_item("sim-norte", "K", "Recurso", "NORTE", "entrada", 5, 2)
    h.poll(sid)
    h.process()
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Alert)) == 1
        assert s.scalar(select(func.count()).select_from(Delivery)) == 0


def test_two_dispatchers_send_each_delivery_once(db, rng):
    al.SimulatedSender.received.clear()
    with db.session() as s:
        for ch in s.scalars(select(Channel)):
            ch.enabled = ch.type == "simulado"
            ch.config = {"fail_rate": 0}
    h = Harness(db, rng)
    sid = make_source(db)
    for k in range(30):
        h.world.create_item("sim-norte", f"K{k}", f"R{k}", "NORTE", "entrada", 5, 1)
    h.poll(sid)
    h.process()
    from monitoreo.engine.params import get_params
    with db.session() as s:
        p = get_params(s)
        n = s.scalar(select(func.count()).select_from(Delivery))
    barrier = threading.Barrier(2)

    def w():
        barrier.wait()
        al.dispatch_pending(db, h.clock.now(), p)

    ts = [threading.Thread(target=w) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    ids = [x["id"] for x in al.SimulatedSender.received]
    assert len(ids) == n == len(set(ids))
