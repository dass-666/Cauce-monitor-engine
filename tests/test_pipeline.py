from datetime import timedelta

from sqlalchemy import func, select

from monitoreo.engine import alerts as al
from monitoreo.engine.params import get_params, set_params
from monitoreo.models import (Alert, Assignment, ChangeEvent, Channel, Delivery, NotificationLog, PollLog, Request,
                              Source)

from .helpers import Harness, make_operator, make_request, make_source


def count(db, model, *where):
    with db.session() as s:
        return s.scalar(select(func.count()).select_from(model).where(*where))


def setup_world(db, rng, units=0):
    h = Harness(db, rng)
    sid = make_source(db, "sim-norte", "NORTE")
    item = h.world.create_item("sim-norte", "N-1", "Concierto Ficticio", "NORTE", "entrada", capacity=5,
                               available=units)
    return h, sid, item.id


def test_end_to_end_release_detect_assign_alert(db, rng):
    h, sid, item = setup_world(db, rng)
    op = make_operator(db, "Ana Ficticia", regions=["NORTE"])
    rid = make_request(db, "SOL-1", priority=1)
    assert h.poll(sid).outcome == "OK"
    h.process()
    assert count(db, Alert) == 0                    # sin disponibilidad: nada que alertar
    h.world.release(item, 1)                        # 1) liberación simulada
    out = h.poll(sid)                               # 2) detección
    assert out.events == 1
    stats = h.process()                             # 3) asignación + alerta
    assert stats == {"eventos": 1, "asignaciones": 1, "alertas": 1}
    with db.session() as s:
        a = s.scalars(select(Alert)).one()
        assert a.kind == "ASIGNACION" and a.request_id == rid and a.operator_name == "Ana Ficticia"
        assert a.region == "NORTE" and a.priority == 1 and a.source_code == "sim-norte"
        assert s.get(Request, rid).status == "ASIGNADO"
        ev = s.scalars(select(ChangeEvent).where(ChangeEvent.change_type == "NUEVA_DISPONIBILIDAD")).one()
        assert ev.processed and ev.detection_latency_ms is not None
    res = al.dispatch_pending(db, h.clock.now(), get_params_db(db))   # 4) envío por canales
    assert res["enviadas"] == count(db, Channel, Channel.enabled.is_(True))
    with db.session() as s:
        assert s.scalars(select(Alert)).one().status == "ENVIADA"


def get_params_db(db):
    with db.session() as s:
        return get_params(s)


def test_repeated_polls_without_changes_do_not_alert(db, rng):
    h, sid, item = setup_world(db, rng, units=2)
    for _ in range(20):
        h.advance(10)
        h.poll(sid)
        h.process()
    assert count(db, ChangeEvent) == 1     # solo la primera aparición
    assert count(db, Alert) == 1


def test_reprocessing_is_idempotent(db, rng):
    h, sid, item = setup_world(db, rng, units=2)
    make_operator(db)
    make_request(db, "R")
    h.poll(sid)
    h.process()
    with db.session() as s:       # simulamos un reinicio que "olvida" que ya se procesó
        for ev in s.scalars(select(ChangeEvent)):
            ev.processed = False
    h.process()
    assert count(db, Alert) == 1
    assert count(db, Assignment) == 1


def test_crash_between_detection_and_processing_recovers(db, rng):
    h, sid, item = setup_world(db, rng)
    make_operator(db)
    make_request(db, "R")
    h.world.release(item, 2)
    h.poll(sid)                    # el evento se guarda... y el proceso "muere" antes de procesarlo
    assert count(db, ChangeEvent, ChangeEvent.processed.is_(False)) == 1
    h2 = Harness(db, rng)          # nuevo proceso, estado en memoria vacío
    h2.clock = h.clock
    h2.process()
    assert count(db, Assignment) == 1 and count(db, Alert) >= 1


def test_temporary_errors_retry_backoff_and_circuit(db, rng):
    h, sid, _ = setup_world(db, rng)
    with db.session() as s:
        src = s.get(Source, sid)
        src.config = {**src.config, "error_rate": 1.0}
        src.max_retries = 2
    out = h.poll(sid)
    assert out.outcome == "ERROR_TEMPORAL"
    with db.session() as s:
        log = s.scalars(select(PollLog)).one()
        assert log.attempts == 3                   # 1 intento + 2 reintentos
    params = get_params_db(db)
    for _ in range(params["umbral_circuito"] - 1):
        h.advance(1000)
        h.poll(sid)
    with db.session() as s:
        src = s.get(Source, sid)
        assert src.circuit_state == "ABIERTO"
    assert h.poll(sid).outcome == "CIRCUITO_ABIERTO"   # no consulta durante el enfriamiento
    with db.session() as s:                            # la fuente se recupera
        src = s.get(Source, sid)
        src.config = {**src.config, "error_rate": 0.0}
    h.advance(params["enfriamiento_circuito_s"] + 1)
    assert h.poll(sid).outcome == "OK"
    with db.session() as s:
        src = s.get(Source, sid)
        assert src.circuit_state == "CERRADO" and src.consecutive_failures == 0


def test_errors_never_look_like_sold_out(db, rng):
    """Un error de consulta NO debe interpretarse como 'todo agotado'."""
    h, sid, item = setup_world(db, rng, units=3)
    h.poll(sid)
    with db.session() as s:
        src = s.get(Source, sid)
        src.config = {**src.config, "error_rate": 1.0}
    for _ in range(5):
        h.advance(1000)
        h.poll(sid)
    assert count(db, ChangeEvent) == 1


def test_rate_limited_respects_retry_after(db, rng):
    h, sid, _ = setup_world(db, rng)
    with db.session() as s:
        src = s.get(Source, sid)
        src.config = {**src.config, "rate_limit_rate": 1.0, "retry_after_s": 120}
    now = h.clock.now()
    assert h.poll(sid).outcome == "LIMITADO"
    with db.session() as s:
        src = s.get(Source, sid)
        assert (src.next_poll_at - now).total_seconds() >= 120
        assert src.circuit_state == "CERRADO"   # limitar no es un fallo de la fuente
        assert s.scalars(select(PollLog)).one().attempts == 1  # no se reintenta


def test_local_rate_limit_token_bucket(db, rng):
    h, sid, _ = setup_world(db, rng)
    with db.session() as s:
        s.get(Source, sid).rate_limit_per_min = 6   # capacidad de ráfaga = 1
    outs = []
    for _ in range(5):
        outs.append(poll(h, sid))
    assert outs.count("OK") == 1 and outs.count("LIMITE_LOCAL") == 4


def poll(h, sid):
    from monitoreo.engine.pipeline import poll_source
    return poll_source(h.db, sid, h.clock, h.runtime, h.rng).outcome


def test_suspended_source_is_not_polled(db, rng):
    h, sid, _ = setup_world(db, rng)
    with db.session() as s:
        s.get(Source, sid).status = "SUSPENDIDA"
    assert h.poll(sid).outcome == "OMITIDA"
    assert count(db, PollLog) == 0


def test_repeated_circuit_openings_set_error_status(db, rng):
    h, sid, _ = setup_world(db, rng)
    with db.session() as s:
        set_params(s, {"aperturas_hasta_error": 2, "umbral_circuito": 1})
        src = s.get(Source, sid)
        src.config = {**src.config, "error_rate": 1.0}
    for _ in range(4):
        h.advance(10_000)
        h.poll(sid)
    with db.session() as s:
        assert s.get(Source, sid).status == "ERROR"


def test_notification_failures_retry_then_fail(db, rng):
    h, sid, item = setup_world(db, rng, units=1)
    with db.session() as s:
        for ch in s.scalars(select(Channel)):
            ch.enabled = ch.type == "simulado"
            if ch.type == "simulado":
                ch.config = {"fail_rate": 1.0}
    h.poll(sid)
    h.process()
    p = get_params_db(db)
    for _ in range(p["reintentos_notificacion"] + 2):
        al.dispatch_pending(db, h.clock.now(), p)
        h.advance(3600)
    with db.session() as s:
        d = s.scalars(select(Delivery)).one()
        assert d.status == "FALLIDA" and d.attempts == p["reintentos_notificacion"]
        assert s.scalars(select(Alert)).one().status == "FALLIDA"
    assert count(db, NotificationLog) == p["reintentos_notificacion"]


def test_notification_transient_failure_then_success(db, rng):
    h, sid, item = setup_world(db, rng, units=1)
    al.SimulatedSender.rng.seed(7)
    with db.session() as s:
        for ch in s.scalars(select(Channel)):
            ch.enabled = ch.type == "simulado"
            if ch.type == "simulado":
                ch.config = {"fail_rate": 0.5}
    h.poll(sid)
    h.process()
    p = get_params_db(db)
    for _ in range(10):
        al.dispatch_pending(db, h.clock.now(), p)
        h.advance(3600)
    with db.session() as s:
        assert s.scalars(select(Delivery)).one().status == "ENVIADA"


def test_ack_and_dedup(db, rng):
    h, sid, item = setup_world(db, rng, units=1)
    h.poll(sid)
    h.process()
    with db.session() as s:
        aid = s.scalars(select(Alert.id)).one()
        assert al.create_alert(s, dedup_key=s.get(Alert, aid).dedup_key, kind="X", title="t", source_code="x",
                               region="NORTE", resource="r", now=h.clock.now()) is None
    assert al.acknowledge(db, aid, "admin", h.clock.now())
    assert not al.acknowledge(db, aid, "admin", h.clock.now())   # idempotente
    with db.session() as s:
        assert s.get(Alert, aid).status == "CONFIRMADA"


def test_inflight_deliveries_recovered_after_crash(db, rng):
    h, sid, item = setup_world(db, rng, units=1)
    h.poll(sid)
    h.process()
    with db.session() as s:
        for d in s.scalars(select(Delivery)):
            d.status = "ENVIANDO"
    assert al.recover_inflight(db) > 0
    assert count(db, Delivery, Delivery.status == "PENDIENTE") > 0


def test_sold_out_cancels_unstarted_assignments(db, rng):
    h, sid, item = setup_world(db, rng, units=2)
    make_operator(db)
    make_request(db, "A")
    make_request(db, "B")
    h.poll(sid)
    h.process()
    assert count(db, Assignment, Assignment.status == "ACTIVA") == 2
    h.world.exhaust(item)
    h.poll(sid)
    h.process()
    assert count(db, Assignment, Assignment.status == "ACTIVA") == 0
    assert count(db, Request, Request.status == "REINTENTO") == 2


def test_maintenance_validates_pending_and_expires(db, rng):
    h, sid, item = setup_world(db, rng, units=1)
    make_operator(db)
    rid = make_request(db, "R", status="PENDIENTE")
    bad = make_request(db, "MAL", status="PENDIENTE", conditions={"desde": "2026-02-01", "hasta": "2026-01-01"})
    h.poll(sid)
    h.process()                   # aún PENDIENTE: no se asigna
    assert count(db, Assignment) == 0
    out = h.maintain()            # valida -> ELEGIBLE y reasigna recursos libres
    assert out["validadas"] == 2 and out["reasignadas"] == 1
    with db.session() as s:
        assert s.get(Request, bad).status == "ERROR"
    h.advance(10_000)
    out = h.maintain()
    assert out["expiradas"] == 1
