"""v1.4 (auditoría): carreras reales con PostgreSQL (READ COMMITTED).

Cada prueba fuerza el orden exacto: la transacción A lee, la transacción B (otro hilo) hace su
cambio y confirma, y A continúa. Antes de la corrección A sobrescribía a B en silencio:
- cancelar una solicitud que otro acababa de COMPLETAR la dejaba CANCELADA y liberaba dos veces
  las unidades retenidas y la carga del operador;
- anular por falta de unidades una asignación que el operador acababa de TOMAR le quitaba el
  trabajo sin avisar (la toma había respondido 200).
En SQLite las transacciones de escritura ya eran secuenciales; estas pruebas solo corren con
PostgreSQL (MONITOREO_TEST_PG).
"""
import threading
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from monitoreo.engine import assignment as asg
from monitoreo.engine.integrity import check
from monitoreo.models import Assignment, Operator, Request, SlotState

from .helpers import make_operator, make_request, make_source

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture
def pg(db):
    if db.is_sqlite:
        pytest.skip("la carrera solo existe con PostgreSQL (SQLite serializa las escrituras)")
    return db


NOW = datetime(2026, 6, 1, 12, 0, 0)


def _world(db):
    sid = make_source(db, "sim-norte", "NORTE")
    op = make_operator(db, "Operador Ficticio", regions=["NORTE"])
    rid = make_request(db, "CAR-1", priority=1)
    with db.session() as s:
        slot = SlotState(source_id=sid, resource_key="R-1", name="Recurso Ficticio", region="NORTE",
                         resource_type="entrada", capacity=5, available=1, held=0, status="ACTIVO",
                         fingerprint="x", first_seen_at=NOW, last_seen_at=NOW)
        s.add(slot)
        s.flush()
        slot_id = slot.id
    out = asg.try_assign(db, slot_id, rid, op, NOW, 900)
    assert out.assignment_id
    return slot_id, op, rid, out.assignment_id


def _interleave(monkeypatch, after_first_scalar_of, other):
    """Ejecuta `other` (en otro hilo, otra transacción, confirmada) justo después de que la
    transacción en curso lea por primera vez un objeto del tipo indicado."""
    real = Session.scalar
    fired = []

    def scalar(self, stmt, *a, **kw):
        res = real(self, stmt, *a, **kw)
        if not fired and isinstance(res, after_first_scalar_of):
            fired.append(True)
            t = threading.Thread(target=other)
            t.start()
            t.join(20)
            assert not t.is_alive(), "la otra transacción quedó bloqueada"
        return res
    monkeypatch.setattr(Session, "scalar", scalar)
    return fired


def _state(db, slot_id, op, rid, aid):
    with db.session() as s:
        return (s.get(Request, rid).status, s.get(Assignment, aid).status, s.get(SlotState, slot_id).held,
                s.get(Operator, op).current_load, check(s))


def test_cancel_does_not_overwrite_a_concurrent_completion(pg, monkeypatch):
    slot_id, op, rid, aid = _world(pg)
    assert asg.take_assignment(pg, aid, op, NOW, "op")
    fired = _interleave(monkeypatch, Assignment,
                        lambda: asg.complete_assignment(pg, aid, op, NOW, "op"))
    assert asg.cancel_request(pg, rid, NOW, "admin") is False      # ya estaba completada
    assert fired
    req, a, held, load, problems = _state(pg, slot_id, op, rid, aid)
    assert (req, a) == ("COMPLETADO", "COMPLETADA")
    assert held == 0 and load == 0 and problems == []               # nada liberado dos veces


def test_cancel_sees_an_assignment_created_concurrently(pg, monkeypatch):
    slot_id, op, rid, aid = _world(pg)
    asg.reject_assignment(pg, aid, op, NOW, "op", {"max_intentos_solicitud": 5})   # solicitud en REINTENTO
    with pg.session() as s:
        s.get(SlotState, slot_id).available = 2
    new = {}

    def assign():
        new["id"] = asg.try_assign(pg, slot_id, rid, op, NOW, 900).assignment_id
    # La cancelación lee la solicitud (REINTENTO) y busca su asignación viva (no hay); justo
    # entonces otro proceso la asigna y confirma.
    _interleave(monkeypatch, type(None), assign)
    assert asg.cancel_request(pg, rid, NOW, "admin") is True
    with pg.session() as s:
        assert s.get(Request, rid).status == "CANCELADO"
        assert new.get("id")
        assert s.get(Assignment, new["id"]).status == "CANCELADA"       # la nueva asignación también
        assert check(s) == []


def test_invalidate_respects_a_take_that_happened_meanwhile(pg, monkeypatch):
    slot_id, op, rid, aid = _world(pg)
    with pg.session() as s:
        s.get(SlotState, slot_id).available = 0                     # la fuente dejó de ofrecerlo
    fired = _interleave(monkeypatch, Assignment, lambda: asg.take_assignment(pg, aid, op, NOW, "op"))
    asg.invalidate_overheld(pg, slot_id, NOW, {})
    assert fired
    req, a, held, load, problems = _state(pg, slot_id, op, rid, aid)
    assert (req, a) == ("EN_PROCESO", "EN_PROCESO")                 # la toma (200) se respeta
    assert held == 1 and load == 1 and problems == []


def test_expire_request_does_not_overwrite_a_concurrent_assignment(pg, monkeypatch):
    slot_id, op, rid, aid = _world(pg)
    asg.reject_assignment(pg, aid, op, NOW, "op", {"max_intentos_solicitud": 5})
    with pg.session() as s:
        r = s.get(Request, rid)
        r.conditions = {**(r.conditions or {}), "vence": (NOW - timedelta(minutes=1)).isoformat()}
        s.get(SlotState, slot_id).available = 2
    got = {}

    def assign():
        got["id"] = asg.try_assign(pg, slot_id, rid, op, NOW, 900).assignment_id
    real = Session.scalars
    fired = []

    def scalars(self, stmt, *a, **kw):
        res = list(real(self, stmt, *a, **kw))
        if not fired and res and isinstance(res[0], Request):
            fired.append(True)
            t = threading.Thread(target=assign)
            t.start()
            t.join(20)
        return iter(res)
    monkeypatch.setattr(Session, "scalars", scalars)
    asg.expire_requests(pg, NOW)
    assert fired and got.get("id")
    with pg.session() as s:
        assert s.get(Request, rid).status == "ASIGNADO"             # no se pisó la asignación
        assert check(s) == []
