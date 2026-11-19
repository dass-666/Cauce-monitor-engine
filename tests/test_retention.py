"""Agregados e historial: corrección de los resúmenes, protección de datos y limpieza en caliente."""
import threading
from datetime import datetime, timedelta

from sqlalchemy import func, select

from monitoreo.engine import retention as ret
from monitoreo.engine.integrity import check
from monitoreo.engine.params import get_params, set_params
from monitoreo.models import (Alert, Assignment, AuditLog, ChangeEvent, PollLog, PollStatDaily, PollStatHourly,
                              Request, RequestEvent, Source)

from .helpers import Harness, make_operator, make_request, make_source


def cnt(db, model, *w):
    with db.session() as s:
        return s.scalar(select(func.count()).select_from(model).where(*w))


def _busy_world(db, rng, hours=48, step=600):
    """Mundo con actividad real del motor a lo largo de `hours` horas virtuales."""
    h = Harness(db, rng)
    sids = [make_source(db, f"f{i}", region, feed=f"f{i}") for i, region in enumerate(["NORTE", "SUR"])]
    for i, sid in enumerate(sids):
        for k in range(3):
            h.world.create_item(f"f{i}", f"K{k}", f"Recurso {i}-{k}", ["NORTE", "SUR"][i], "entrada", 5, 0)
    make_operator(db)
    for k in range(10):
        make_request(db, f"R{k}", region=["NORTE", "SUR"][k % 2], priority=1 + k % 3)
    for _ in range(int(hours * 3600 / step)):
        h.world.random_step()
        for sid in sids:
            h.poll(sid)
        h.process()
        h.advance(step - 2)
    return h, sids


def test_aggregates_match_raw_and_are_idempotent(db, rng):
    h, sids = _busy_world(db, rng, hours=6)
    with db.session() as s:
        raw = dict(s.execute(select(PollLog.source_id, func.count()).group_by(PollLog.source_id)).all())
        raw_changes = s.scalar(select(func.sum(PollLog.changes)))
        last = s.scalar(select(func.max(PollLog.started_at)))
    now = last.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1, minutes=1)
    r1 = ret.aggregate(db, now)
    assert r1["horas"] >= 6
    with db.session() as s:
        agg = dict(s.execute(select(PollStatHourly.source_id, func.sum(PollStatHourly.polls))
                             .group_by(PollStatHourly.source_id)).all())
        daily = s.scalar(select(func.sum(PollStatDaily.polls)))
        agg_changes = s.scalar(select(func.sum(PollStatHourly.changes)))
    assert agg == raw and daily == sum(raw.values()) and agg_changes == raw_changes
    # repetir (incluso desde cero) no duplica
    from monitoreo.models import Setting
    with db.session() as s:
        s.delete(s.get(Setting, ret.WATERMARK_KEY))
    ret.aggregate(db, now)
    with db.session() as s:
        assert s.scalar(select(func.sum(PollStatHourly.polls))) == sum(raw.values())
        assert s.scalar(select(func.sum(PollStatDaily.polls))) == sum(raw.values())


def test_purge_respects_retention_and_protected_history(db, rng):
    h, sids = _busy_world(db, rng, hours=48)
    with db.session() as s:
        set_params(s, {"retencion_consultas_dias": 1, "retencion_eventos_sin_alerta_dias": 1,
                       "retencion_lote": 50, "retencion_lotes_por_pasada": 10_000})
    protected_before = {m.__name__: cnt(db, m) for m in (Assignment, Alert, Request, RequestEvent, AuditLog)}
    relevant_before = cnt(db, ChangeEvent, ChangeEvent.relevant.is_(True))
    polls_before = cnt(db, PollLog)
    now = h.clock.now()
    ret.aggregate(db, now)
    out = ret.purge(db, now, sleep_fn=lambda s: None)
    assert out["consultas"] > 0
    cutoff = now - timedelta(days=1)
    assert cnt(db, PollLog, PollLog.started_at < cutoff) == 0
    assert cnt(db, PollLog) == polls_before - out["consultas"] > 0          # lo reciente se conserva
    assert {m.__name__: cnt(db, m) for m in (Assignment, Alert, Request, RequestEvent, AuditLog)} == protected_before
    assert cnt(db, ChangeEvent, ChangeEvent.relevant.is_(True)) == relevant_before
    # eventos irrelevantes referenciados por una asignación no se tocan
    with db.session() as s:
        refs = s.scalars(select(Assignment.change_event_id).where(Assignment.change_event_id.is_not(None))).all()
        assert all(s.get(ChangeEvent, e) is not None for e in refs)
    # los totales históricos siguen disponibles en los agregados
    with db.session() as s:
        assert s.scalar(select(func.sum(PollStatDaily.polls))) == polls_before
    with db.session() as s:
        assert check(s) == []


def test_purge_never_deletes_unaggregated_rows(db, rng):
    h, sids = _busy_world(db, rng, hours=3)
    with db.session() as s:
        set_params(s, {"retencion_consultas_dias": 0.0001})
    n = cnt(db, PollLog)
    out = ret.purge(db, h.clock.now() + timedelta(days=30), sleep_fn=lambda s: None)   # sin agregar antes
    assert out["consultas"] == 0 and cnt(db, PollLog) == n


def test_purge_is_gradual(db, rng):
    h, sids = _busy_world(db, rng, hours=30)
    with db.session() as s:
        set_params(s, {"retencion_consultas_dias": 0.1, "retencion_lote": 20, "retencion_lotes_por_pasada": 3})
    now = h.clock.now()
    ret.aggregate(db, now)
    out = ret.purge(db, now, sleep_fn=lambda s: None)
    assert out["consultas"] == 60 and out["pendiente_otra_pasada"]


def test_cleanup_while_engine_is_working(db, rng):
    """Limpieza en un hilo mientras otro hilo consulta, detecta, asigna y alerta."""
    h, sids = _busy_world(db, rng, hours=30)
    with db.session() as s:
        set_params(s, {"retencion_consultas_dias": 0.2, "retencion_eventos_sin_alerta_dias": 0.2,
                       "retencion_lote": 25, "retencion_lotes_por_pasada": 10_000})
    for k in range(20):
        make_request(db, f"X{k}", region=["NORTE", "SUR"][k % 2])
    errors = []
    stop = threading.Event()

    def engine():
        try:
            for _ in range(120):
                h.world.random_step()
                for sid in sids:
                    h.poll(sid)
                h.process()
                h.maintain()
        except Exception as e:  # noqa: BLE001
            errors.append(e)
        finally:
            stop.set()

    def cleaner():
        try:
            while not stop.is_set():
                ret.run_history_maintenance(db, h.clock.now(), sleep_fn=lambda s: None)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    t1, t2 = threading.Thread(target=engine), threading.Thread(target=cleaner)
    t1.start(); t2.start(); t1.join(); t2.join()  # noqa: E702
    assert not errors, errors
    with db.session() as s:
        assert check(s) == []
        assert s.scalar(select(func.count()).select_from(ChangeEvent).where(ChangeEvent.processed.is_(False))) == 0


def test_stats_combine_aggregated_and_raw(db, rng):
    h, sids = _busy_world(db, rng, hours=5)
    total = cnt(db, PollLog)
    since = h.clock.now() - timedelta(days=2)
    before = ret.poll_stats(db, since, "region")
    ret.aggregate(db, h.clock.now())
    after = ret.poll_stats(db, since, "region")
    assert sum(r["consultas"] for r in before) == sum(r["consultas"] for r in after) == total
    assert {r["region"] for r in after} == {"NORTE", "SUR"}
