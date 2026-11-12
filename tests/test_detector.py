from datetime import datetime, timedelta

from sqlalchemy import func, select

from monitoreo.config import ENGINE_DEFAULTS
from monitoreo.contracts import Observation, Snapshot
from monitoreo.engine import detector as d
from monitoreo.models import ChangeEvent, SlotState, Source

from .helpers import make_source

T0 = datetime(2026, 6, 1, 12)


def obs(key="R1", available=0, starts=None, status="ACTIVO", name="Recurso"):
    return Observation(resource_key=key, name=name, resource_type="entrada", available=available, capacity=10,
                       starts_at=starts or datetime(2026, 7, 1, 20), status=status)


def run(db, sid, observations, now=T0, complete=True):
    with db.session() as s:
        src = s.get(Source, sid)
        res = d.detect(s, src, Snapshot(observations=observations, complete=complete), now, ENGINE_DEFAULTS)
        return [e.change_type for e in res.events], res


def test_sequence_of_changes(db):
    sid = make_source(db)
    assert run(db, sid, [obs(available=0)])[0] == [d.NUEVO_RECURSO]
    assert run(db, sid, [obs(available=3)])[0] == [d.NUEVA]
    assert run(db, sid, [obs(available=5)])[0] == [d.AUMENTO]
    assert run(db, sid, [obs(available=2)])[0] == [d.REDUCCION]
    assert run(db, sid, [obs(available=0)])[0] == [d.AGOTADO]
    assert run(db, sid, [obs(available=1)])[0] == [d.REAPARICION]
    assert run(db, sid, [obs(available=1, starts=datetime(2026, 7, 2, 20))])[0] == [d.HORARIO]
    assert run(db, sid, [obs(available=1, status="CANCELADO")])[0] == [d.CANCELACION]
    assert run(db, sid, [obs(available=2)])[0] == [d.REAPARICION]


def test_no_change_no_event_and_duplicates(db):
    sid = make_source(db)
    run(db, sid, [obs(available=2)])
    types, res = run(db, sid, [obs(available=2), obs(available=2)])
    assert types == []
    assert res.unchanged == 1 and res.duplicates == 1
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(ChangeEvent)) == 1


def test_event_keys_are_stable_and_unique(db):
    k1 = d.event_key(1, "R", d.NUEVA, 3, "abc")
    assert k1 == d.event_key(1, "R", d.NUEVA, 3, "abc")
    assert k1 != d.event_key(1, "R", d.NUEVA, 4, "abc")


def test_disappearance_requires_complete_snapshots_and_threshold(db):
    sid = make_source(db)
    run(db, sid, [obs("A", 2), obs("B", 2)])
    assert run(db, sid, [obs("A", 2)], complete=False)[0] == []      # parcial: no se infiere nada
    assert run(db, sid, [obs("A", 2)])[0] == []                      # 1ª ausencia
    assert run(db, sid, [obs("A", 2)])[0] == [d.CANCELACION]         # 2ª ausencia -> cancelación
    assert run(db, sid, [obs("A", 2)])[0] == []
    assert run(db, sid, [obs("A", 2), obs("B", 2)])[0] == [d.REAPARICION]


def test_relevance_filter(db):
    p = dict(ENGINE_DEFAULTS)
    assert d.is_relevant(d.NUEVA, {}, {"available": 1}, p)
    assert not d.is_relevant(d.AGOTADO, {"available": 1}, {"available": 0}, p)
    p["alerta_minimo_aumento"] = 3
    assert not d.is_relevant(d.AUMENTO, {"available": 1}, {"available": 2}, p)
    assert d.is_relevant(d.AUMENTO, {"available": 1}, {"available": 4}, p)


def test_detection_latency_uses_source_timestamp(db):
    sid = make_source(db)
    o = obs(available=1)
    o.changed_at = T0 - timedelta(seconds=2)
    run(db, sid, [o])
    with db.session() as s:
        ev = s.scalars(select(ChangeEvent)).one()
        assert abs(ev.detection_latency_ms - 2000) < 1
        st = s.scalars(select(SlotState)).one()
        assert st.version == 1 and st.ever_available
