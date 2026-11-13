import threading
from datetime import datetime, timedelta

from sqlalchemy import func, select

from monitoreo.config import ENGINE_DEFAULTS
from monitoreo.engine import assignment as asg
from monitoreo.models import Assignment, Operator, Request, RequestEvent, SlotState

from .helpers import Harness, make_operator, make_request, make_source

P = dict(ENGINE_DEFAULTS)


def _slot(db, available=1, region="NORTE", rtype="entrada"):
    sid = make_source(db)
    with db.session() as s:
        st = SlotState(source_id=sid, resource_key="X", region=region, resource_type=rtype, name="Función X",
                       available=available, held=0, status="ACTIVO", fingerprint="f", version=1)
        s.add(st)
        s.flush()
        return st.id


def test_priority_then_age(db):
    slot = _slot(db, available=1)
    make_operator(db)
    make_request(db, "VIEJA-P3", priority=3, created_at=datetime(2026, 1, 1))
    make_request(db, "NUEVA-P1", priority=1, created_at=datetime(2026, 3, 1))
    make_request(db, "VIEJA-P1", priority=1, created_at=datetime(2026, 2, 1))
    created = asg.allocate_slot(db, slot, datetime(2026, 6, 1), P)
    assert len(created) == 1
    with db.session() as s:
        a = s.get(Assignment, created[0])
        assert s.get(Request, a.request_id).code == "VIEJA-P1"


def test_region_and_type_must_match(db):
    slot = _slot(db, available=3, region="NORTE", rtype="entrada")
    make_operator(db)
    make_request(db, "SUR", region="SUR")
    make_request(db, "MESA", rtype="mesa")
    assert asg.allocate_slot(db, slot, datetime(2026, 6, 1), P) == []


def test_no_operator_capacity_means_no_assignment(db):
    slot = _slot(db, available=5)
    make_operator(db, max_concurrent=1)
    for i in range(3):
        make_request(db, f"R{i}")
    created = asg.allocate_slot(db, slot, datetime(2026, 6, 1), P)
    assert len(created) == 1
    with db.session() as s:
        assert s.get(SlotState, slot).held == 1


def test_operator_region_filter_and_load_balance(db):
    slot = _slot(db, available=4)
    o_sur = make_operator(db, "Sur", regions=["SUR"])
    o1 = make_operator(db, "N1", regions=["NORTE"])
    o2 = make_operator(db, "N2", regions=[])
    for i in range(4):
        make_request(db, f"R{i}")
    created = asg.allocate_slot(db, slot, datetime(2026, 6, 1), P)
    with db.session() as s:
        ops = [s.get(Assignment, a).operator_id for a in created]
    assert o_sur not in ops
    assert sorted(ops) == sorted([o1, o2, o1, o2])


def test_quantity_respects_free_units(db):
    slot = _slot(db, available=2)
    make_operator(db)
    make_request(db, "TRES", quantity=3, priority=1)
    make_request(db, "DOS", quantity=2, priority=2)
    created = asg.allocate_slot(db, slot, datetime(2026, 6, 1), P)
    with db.session() as s:
        assert [s.get(Request, s.get(Assignment, a).request_id).code for a in created] == ["DOS"]


def test_full_lifecycle_and_history(db):
    slot = _slot(db, available=1)
    op = make_operator(db)
    rid = make_request(db, "R")
    now = datetime(2026, 6, 1)
    [aid] = asg.allocate_slot(db, slot, now, P)
    assert not asg.take_assignment(db, aid, op + 999, now, "otro")  # otro operador no puede tomarla
    assert asg.take_assignment(db, aid, op, now, "op")
    assert not asg.take_assignment(db, aid, op, now, "op")          # ya tomada
    assert asg.complete_assignment(db, aid, op, now, "op")
    with db.session() as s:
        r = s.get(Request, rid)
        assert r.status == "COMPLETADO"
        hist = [e.to_status for e in s.scalars(select(RequestEvent).where(RequestEvent.request_id == rid)
                                                .order_by(RequestEvent.id))]
        assert hist == ["ASIGNADO", "EN_PROCESO", "COMPLETADO"]
        st = s.get(SlotState, slot)
        assert st.held == 0 and st.available == 0
        assert s.get(Operator, op).current_load == 0


def test_expiry_returns_to_queue_and_resource_is_reassigned(db):
    slot = _slot(db, available=1)
    make_operator(db)
    r1 = make_request(db, "R1", priority=1)
    r2 = make_request(db, "R2", priority=2)
    now = datetime(2026, 6, 1)
    [a1] = asg.allocate_slot(db, slot, now, P)
    later = now + timedelta(seconds=P["ttl_asignacion_s"] + 1)
    assert asg.expire_assignments(db, later, P) == 1
    with db.session() as s:
        assert s.get(Request, r1).status == "REINTENTO"
        assert s.get(SlotState, slot).held == 0
    created = asg.rebalance(db, later, P)
    with db.session() as s:
        # R1 (prioridad 1) vuelve a ser elegida primero
        assert s.get(Assignment, created[0]).request_id == r1


def test_rejection_excludes_same_slot_and_max_attempts(db):
    slot = _slot(db, available=1)
    op = make_operator(db)
    r1 = make_request(db, "R1", priority=1)
    r2 = make_request(db, "R2", priority=2)
    now = datetime(2026, 6, 1)
    [a1] = asg.allocate_slot(db, slot, now, P)
    assert asg.reject_assignment(db, a1, op, now, "op", P)
    created = asg.rebalance(db, now, P)
    with db.session() as s:
        assert s.get(Assignment, created[0]).request_id == r2   # R1 rechazó este recurso


def test_invalid_transition_rejected(db):
    rid = make_request(db, "R", status="COMPLETADO")
    with db.session() as s:
        r = s.get(Request, rid)
        try:
            asg.transition(s, r, "ASIGNADO", "x", "", datetime(2026, 1, 1))
            raise AssertionError("debió fallar")
        except asg.TransitionError:
            pass


def test_overheld_invalidation(db):
    slot = _slot(db, available=2)
    make_operator(db)
    make_request(db, "A")
    make_request(db, "B")
    now = datetime(2026, 6, 1)
    assert len(asg.allocate_slot(db, slot, now, P)) == 2
    with db.session() as s:
        s.get(SlotState, slot).available = 1
    assert asg.invalidate_overheld(db, slot, now, P) == 1
    with db.session() as s:
        assert s.get(SlotState, slot).held == 1
        assert s.scalar(select(func.count()).select_from(Request).where(Request.status == "REINTENTO")) == 1


def test_concurrent_operators_cannot_double_assign(db):
    """Muchos hilos intentan asignar el MISMO recurso de 1 unidad a solicitudes distintas."""
    slot = _slot(db, available=1)
    ops = [make_operator(db, f"Op{i}") for i in range(10)]
    reqs = [make_request(db, f"R{i}") for i in range(10)]
    now = datetime(2026, 6, 1)
    barrier = threading.Barrier(10)
    results = []

    def worker(i):
        barrier.wait()
        results.append(asg.try_assign(db, slot, reqs[i], ops[i], now, 600).assignment_id)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(1 for r in results if r) == 1
    with db.session() as s:
        assert s.get(SlotState, slot).held == 1
        assert s.scalar(select(func.count()).select_from(Assignment)) == 1
        assert s.scalar(select(func.sum(Operator.current_load))) == 1


def test_concurrent_take_same_assignment(db):
    """Dos operadores (o dos pestañas) intentan tomar la misma asignación a la vez."""
    slot = _slot(db, available=1)
    make_operator(db)
    make_request(db, "R")
    now = datetime(2026, 6, 1)
    [aid] = asg.allocate_slot(db, slot, now, P)
    barrier = threading.Barrier(8)
    res = []

    def worker():
        barrier.wait()
        res.append(asg.take_assignment(db, aid, None, now, "x"))

    ts = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert res.count(True) == 1


def test_same_request_two_slots_concurrently(db):
    """Una solicitud no puede quedar asignada a dos recursos a la vez."""
    s1 = _slot(db, available=1)
    with db.session() as s:
        st = SlotState(source_id=1, resource_key="Y", region="NORTE", resource_type="entrada", name="Y",
                       available=1, held=0, status="ACTIVO", fingerprint="g", version=1)
        s.add(st)
        s.flush()
        s2 = st.id
    o1, o2 = make_operator(db, "A"), make_operator(db, "B")
    rid = make_request(db, "UNICA")
    now = datetime(2026, 6, 1)
    barrier = threading.Barrier(2)
    res = []

    def w(slot, op):
        barrier.wait()
        res.append(asg.try_assign(db, slot, rid, op, now, 600).assignment_id)

    ts = [threading.Thread(target=w, args=(s1, o1)), threading.Thread(target=w, args=(s2, o2))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(1 for r in res if r) == 1
    with db.session() as s:
        assert s.scalar(select(func.sum(SlotState.held))) == 1


def test_rebalance_no_usa_datos_de_una_fuente_que_esta_fallando(db):
    """v1.6: si la última consulta de la fuente falló, lo almacenado puede estar desactualizado:
    la reevaluación periódica NO crea asignaciones nuevas con eso. Al primer éxito vuelve a asignar."""
    from monitoreo.models import Source
    slot = _slot(db, available=2)
    make_operator(db)
    make_request(db, "ESPERA")
    with db.session() as s:
        src = s.get(Source, s.get(SlotState, slot).source_id)
        src.consecutive_failures = 1                       # la fuente acaba de fallar (p. ej. portal caído)
    assert asg.rebalance(db, datetime(2026, 6, 1), P) == []
    with db.session() as s:
        s.get(Source, s.get(SlotState, slot).source_id).consecutive_failures = 0   # vuelve a responder
    assert len(asg.rebalance(db, datetime(2026, 6, 1), P)) == 1


# ----------------------------------------------------------------- v1.7: colas inteligentes
def test_v17_reparto_equitativo_dia_vs_menor_carga(db):
    """menor_carga (predeterminado, igual que v1.6) elige al de menos carga actual; equitativo_dia
    elige a quien recibió menos asignaciones en las últimas 24 h aunque tenga algo de carga."""
    now = datetime(2026, 6, 1, 12)
    slot = _slot(db, available=1)
    a = make_operator(db, "Muy ocupado hoy", regions=["NORTE"])
    b = make_operator(db, "Poco hoy", regions=["NORTE"])
    rid = make_request(db, "EQ-1")
    with db.session() as s:
        s.get(Operator, b).current_load = 1
        for i in range(3):   # A completó 3 hoy (carga actual 0)
            r = Request(code=f"HIST-{i}", holder="Ficticio", region="NORTE", resource_type="entrada", priority=3,
                        quantity=1, status="COMPLETADO", conditions={}, created_at=now - timedelta(hours=3))
            s.add(r)
            s.flush()
            s.add(Assignment(request_id=r.id, slot_id=slot, operator_id=a, status="COMPLETADA",
                             created_at=now - timedelta(hours=2), expires_at=now))
    with db.session() as s:
        st, req = s.get(SlotState, slot), s.get(Request, rid)
        ops, ex1 = asg.elegir_operadores(s, st, req, now, P, set())
        assert [o.id for o in ops] == [a, b] and ex1["reparto_modo"] == "menor_carga"
        ops, ex2 = asg.elegir_operadores(s, st, req, now, {**P, "reparto_modo": "equitativo_dia"}, set())
        assert [o.id for o in ops] == [b, a]
        assert {c["operador"]: c["asignaciones_24h"] for c in ex2["candidatos"]} == {"Muy ocupado hoy": 3, "Poco hoy": 0}
    created = asg.allocate_slot(db, slot, now, {**P, "reparto_modo": "equitativo_dia"})
    with db.session() as s:
        x = s.get(Assignment, created[0])
        assert x.operator_id == b and x.explicacion["elegido"] == "Poco hoy"
        assert "últimas 24 h" in x.explicacion["resumen"]


def test_v17_operador_ausente_se_salta_solo_si_se_configura(db):
    from monitoreo.models import User
    now = datetime(2026, 6, 1, 12)
    slot = _slot(db, available=1)
    with db.session() as s:
        u = User(username="ausente", password_hash="x", role="operador")
        s.add(u)
        s.flush()
        uid = u.id
    aus = make_operator(db, "Ausente", regions=["NORTE"])
    pres = make_operator(db, "Presente", regions=["NORTE"])
    with db.session() as s:
        s.get(Operator, aus).user_id = uid
        s.get(Operator, aus).visto_en = now - timedelta(hours=1)
        s.get(Operator, pres).current_load = 2                       # el ausente tiene menos carga
    rid = make_request(db, "AUS-1")
    with db.session() as s:
        ops, _ = asg.elegir_operadores(s, s.get(SlotState, slot), s.get(Request, rid), now, P, set())
        assert ops[0].id == aus                                       # apagado (0): comportamiento v1.6
        ops, ex = asg.elegir_operadores(s, s.get(SlotState, slot), s.get(Request, rid), now,
                                        {**P, "operador_ausente_s": 600}, set())
        assert [o.id for o in ops] == [pres]
        assert ex["descartados"] == [{"operador": "Ausente", "motivo": "ausente: sin actividad en el panel hace 60 min"}]


def test_v17_explicacion_no_lleva_datos_del_titular(db):
    slot = _slot(db, available=1)
    make_operator(db, "N1", regions=["NORTE"])
    make_request(db, "PRIV-1")                                       # titular "Titular Ficticio"
    created = asg.allocate_slot(db, slot, datetime(2026, 6, 1), P)
    with db.session() as s:
        e = s.get(Assignment, created[0]).explicacion
    assert "Titular" not in repr(e) and e["solicitud"]["codigo"] == "PRIV-1"
