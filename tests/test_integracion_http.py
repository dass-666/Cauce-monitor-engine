"""v1.3: integración con HTTP REAL contra el proveedor de prueba ejecutándose como OTRO PROCESO.

Lo real: el proceso, el puerto, las peticiones, los códigos HTTP, las latencias y los cortes.
Lo ficticio: los datos (recursos, titulares, solicitudes).
No se conecta a ningún servicio de terceros: el proveedor escucha solo en 127.0.0.1.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from monitoreo.connectors.proveedor_prueba import ProveedorPruebaConnector
from monitoreo.engine import alerts as al
from monitoreo.engine import assignment as asg
from monitoreo.engine import pipeline as pl
from monitoreo.engine.clock import RealClock
from monitoreo.engine.integrity import check
from monitoreo.engine.params import get_params
from monitoreo.models import (Alert, Assignment, ChangeEvent, Channel, Delivery, Operator, PollLog, Request,
                              RequestEvent, SlotState, Source)
from proveedor_prueba.proceso import ProveedorProceso

from .helpers import make_operator, make_request

TOKEN_ENV = "PROVEEDOR_TOKEN_MOTOR_PRUEBAS"


@pytest.fixture(scope="module")
def prov(tmp_path_factory):
    d = tmp_path_factory.mktemp("proveedor")
    p = ProveedorProceso(d / "estado.json", extra=["--cuota-por-min", "0"])
    p.iniciar()
    yield p
    p.detener()


@pytest.fixture
def motor(db, prov, monkeypatch):
    """Motor con una fuente HTTP por región 'norte' apuntando al proveedor (otro proceso)."""
    monkeypatch.setenv(TOKEN_ENV, prov.token)
    prov.ctl("POST", "/control/reiniciar")                           # inventario inicial para cada prueba
    return Motor(db, prov)


class Motor:
    def __init__(self, db, prov, region="norte", limite=50, timeout_s=2.0, max_retries=1):
        self.db, self.prov, self.clock, self.runtime = db, prov, RealClock(), pl.RuntimeState()
        with db.session() as s:
            src = Source(code=f"http-{region}", name=f"Proveedor de prueba · {region}", region=region.upper(),
                         availability_type="reservacion", connector_type="proveedor_prueba",
                         config={"url_base": prov.base, "region": region, "token_env": TOKEN_ENV,
                                 "timeout_s": timeout_s, "limite": limite},
                         interval_s=1, jitter_pct=0, rate_limit_per_min=6000, max_retries=max_retries)
            s.add(src)
            s.flush()
            self.sid = src.id

    def poll(self):
        return pl.poll_source(self.db, self.sid, self.clock, self.runtime)

    def process(self):
        return pl.process_pending_events(self.db, self.clock)

    def slot(self, key):
        with self.db.session() as s:
            st = s.scalar(select(SlotState).where(SlotState.source_id == self.sid, SlotState.resource_key == key))
            return None if st is None else {"available": st.available, "held": st.held, "status": st.status}

    def events(self, key=None):
        with self.db.session() as s:
            q = select(ChangeEvent).where(ChangeEvent.source_id == self.sid).order_by(ChangeEvent.id)
            if key:
                q = q.where(ChangeEvent.resource_key == key)
            return [(e.change_type, e.after.get("available")) for e in s.scalars(q)]

    def source(self):
        with self.db.session() as s:
            src = s.get(Source, self.sid)
            return {c: getattr(src, c) for c in ("last_outcome", "consecutive_failures", "connector_state",
                                                  "last_result", "status", "next_poll_at", "total_errors")}


def _ok_integrity(db):
    with db.session() as s:
        problems = check(s)
    assert problems == [], problems


# ---------------------------------------------------------------- flujo completo
def test_full_path_release_detect_assign_notify_and_trace(motor, prov):
    """Liberación en el proveedor -> consulta HTTP -> cambio -> solicitud compatible -> operador -> aviso."""
    db = motor.db
    op = make_operator(db, "Operadora Norte", regions=["NORTE"])
    make_request(db, "SOL-HTTP-1", region="NORTE", rtype="entrada", priority=1)
    assert motor.poll().outcome == "OK"                    # línea base: 12 recursos sin unidades
    rel = prov.liberar("NOR-001", 2)
    out = motor.poll()
    assert out.outcome == "OK" and out.events == 1
    assert motor.events("NOR-001") == [("NUEVO_RECURSO", 0), ("NUEVA_DISPONIBILIDAD", 2)]
    stats = motor.process()
    assert stats["asignaciones"] == 1
    with db.session() as s:
        a = s.scalars(select(Assignment)).one()
        assert a.operator_id == op and a.status == "ACTIVA"
        alert = s.scalars(select(Alert).where(Alert.kind == "ASIGNACION")).one()
        ev = s.get(ChangeEvent, a.change_event_id)
        assert alert.change_event_id == ev.id and ev.processed_at is not None
        # trazabilidad temporal: cambio en la fuente <= detección <= procesamiento <= asignación
        t_fuente = datetime.fromisoformat(rel["t"].replace("Z", "+00:00")).replace(tzinfo=None)
        assert abs((ev.observed_at - t_fuente).total_seconds()) < 0.01
        assert ev.observed_at <= ev.detected_at <= a.created_at <= ev.processed_at
        assert 0 <= ev.detection_latency_ms < 5000
        p = get_params(s)
    al.dispatch_pending(db, RealClock().now(), p)
    with db.session() as s:
        d = s.scalars(select(Delivery).join(Channel, Channel.id == Delivery.channel_id)
                      .where(Delivery.alert_id == alert.id, Channel.type == "panel")).one()
        assert d.status == "ENVIADA" and d.delivered_at >= a.created_at
    # La segunda consulta usa el ETag guardado: el proveedor responde 304 y no se compara nada.
    st = motor.source()["connector_state"]
    assert st["etag"] and st["version"] >= 2
    out = motor.poll()
    assert out.outcome == "OK" and out.events == 0 and motor.source()["last_result"]["sin_modificar"] is True
    _ok_integrity(db)


# ---------------------------------------------------------------- fallos de red y del proveedor
def test_provider_hangs_is_a_temporary_error_and_recovers(motor, prov):
    prov.fallos(modo="colgar", colgar_s=5)
    t0 = time.monotonic()
    out = motor.poll()
    assert out.outcome == "ERROR_TEMPORAL" and "tiempo de espera" in out.error
    assert time.monotonic() - t0 < 2 * 2.0 + 3                     # timeout 2 s x (1 + 1 reintento) + pausa
    assert motor.source()["consecutive_failures"] == 1
    prov.fallos(reset=True)
    assert motor.poll().outcome == "OK" and motor.source()["consecutive_failures"] == 0


def test_http_429_respects_retry_after_and_does_not_count_as_failure(motor, prov):
    prov.fallos(modo="429", retry_after=7)
    before = RealClock().now()
    out = motor.poll()
    src = motor.source()
    assert out.outcome == "LIMITADO" and src["consecutive_failures"] == 0
    assert (src["next_poll_at"] - before).total_seconds() >= 7          # no vuelve antes de lo pedido
    with motor.db.session() as s:
        assert s.scalar(select(func.count()).select_from(PollLog).where(PollLog.outcome == "LIMITADO")) == 1


def test_real_provider_quota_produces_429(tmp_path, db, monkeypatch):
    """Cuota real del servidor (no inyectada): 5 peticiones por minuto."""
    p = ProveedorProceso(tmp_path / "q.json", extra=["--cuota-por-min", "5"])
    p.iniciar()
    try:
        monkeypatch.setenv(TOKEN_ENV, p.token)
        m = Motor(db, p, limite=200)
        outs = []
        for _ in range(7):
            time.sleep(0.05)                                         # deja pasar la cubeta local del motor
            outs.append(m.poll().outcome)
        assert outs[:5] == ["OK"] * 5 and outs[5] == "LIMITADO"
        assert (m.source()["next_poll_at"] - RealClock().now()).total_seconds() > 30
    finally:
        p.detener()


def test_connection_cut_mid_response_is_retried_without_partial_data(motor, prov):
    prov.fallos(modo="cortar", restantes=1)                          # solo la primera respuesta
    out = motor.poll()
    assert out.outcome == "OK"
    with motor.db.session() as s:
        log = s.scalars(select(PollLog).order_by(PollLog.id.desc())).first()
        assert log.attempts == 2                                     # se reintentó dentro de la consulta
    prov.fallos(modo="cortar")                                       # todas cortadas
    prov.liberar("NOR-002", 1)
    out = motor.poll()
    assert out.outcome == "ERROR_TEMPORAL" and "conexión" in out.error
    assert motor.slot("NOR-002")["available"] == 0                   # nada a medias
    prov.fallos(reset=True)
    assert motor.poll().events == 1 and motor.slot("NOR-002")["available"] == 1


def test_revoked_credential_stops_the_source_without_insisting(motor, prov):
    prov.fallos(modo="401")
    out = motor.poll()
    assert out.outcome == "ERROR" and motor.source()["status"] == "ERROR"
    with motor.db.session() as s:
        assert s.scalar(select(Alert).where(Alert.kind == "SISTEMA")) is not None


def test_malformed_item_is_discarded_and_no_false_cancellation(motor, prov):
    m = Motor(motor.db, prov, region="oeste")
    prov.fallos(modo="malformada", restantes=1)
    out = m.poll()
    src = m.source()
    assert out.outcome == "OK" and src["last_result"]["incompleta"] is True
    assert src["last_result"]["conector"]["descartados"] == 1 and "etag" not in src["connector_state"]
    for _ in range(3):                                               # vuelve a ser completa (sin 304)
        assert m.poll().outcome == "OK"
    assert m.source()["last_result"].get("incompleta") is None
    assert "CANCELACION" not in [t for t, _ in m.events()]
    assert all(m.slot(f"OES-{i:03d}")["status"] == "ACTIVO" for i in range(1, 13))


# ---------------------------------------------------------------- coherencia de los datos
def test_old_response_repeated_is_rejected(motor, prov):
    motor.poll()
    prov.liberar("NOR-003", 3)
    assert motor.poll().events == 1
    n_events = len(motor.events())
    prov.fallos(repetir_antigua=1)                                   # una caché devuelve la versión vieja
    out = motor.poll()
    assert out.outcome == "OBSOLETA"
    assert motor.slot("NOR-003")["available"] == 3                   # no se "deshizo" la liberación
    assert len(motor.events()) == n_events and motor.source()["consecutive_failures"] == 0


def test_data_changing_between_pages(motor, prov):
    m = Motor(motor.db, prov, region="sur", limite=5)                # 12 recursos -> 3 páginas
    assert m.poll().outcome == "OK"
    prov.liberar("SUR-001", 1)                                       # algo cambia: no habrá 304
    prov.fallos(cambiar_entre_paginas=1)
    m.poll()
    meta = m.source()["last_result"]["conector"]
    assert meta["reinicios"] == 1 and meta["incompleta"] is False    # se reinició y quedó coherente
    prov.liberar("SUR-002", 1)
    prov.fallos(cambiar_entre_paginas=10)                            # cambia en cada página
    m.poll()
    lr = m.source()["last_result"]
    assert lr["incompleta"] is True and "etag" not in m.source()["connector_state"]
    prov.fallos(reset=True)
    m.poll()
    m.poll()
    # convergencia: lo que cree el motor == lo que publica el proveedor; sin cancelaciones falsas
    for it in prov.disponibilidad("sur"):
        assert m.slot(it["id"])["available"] == it["disponibles"], it["id"]
    assert "CANCELACION" not in [t for t, _ in m.events()]


def test_window_expiring_before_processing_does_not_alert_or_assign(motor, prov):
    db = motor.db
    make_operator(db, "Operador Norte", regions=["NORTE"])
    make_request(db, "SOL-HTTP-2", region="NORTE", rtype="entrada", conditions={"nombre_contiene": "Norte · 4"})
    motor.poll()
    prov.liberar("NOR-004", 1, ventana_s=1.0)                        # disponibilidad de 1 segundo
    assert motor.poll().events == 1
    time.sleep(1.3)                                                  # vence antes de procesar
    motor.poll()
    assert motor.events("NOR-004")[-2:] == [("NUEVA_DISPONIBILIDAD", 1), ("AGOTADO", 0)]
    motor.process()
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Assignment)) == 0
        titles = [a.title for a in s.scalars(select(Alert).where(Alert.resource.contains("Norte · 4")))]
    assert titles == [], titles                                      # nada de avisos engañosos


def test_window_expiring_after_assignment_releases_it(motor, prov):
    db = motor.db
    op = make_operator(db, "Operador Norte", regions=["NORTE"])
    rid = make_request(db, "SOL-HTTP-3", region="NORTE", rtype="entrada",
                       conditions={"nombre_contiene": "Norte · 5"})
    motor.poll()
    prov.liberar("NOR-005", 1, ventana_s=1.5)
    motor.poll()
    assert motor.process()["asignaciones"] == 1
    time.sleep(1.8)
    motor.poll()
    motor.process()
    with db.session() as s:
        a = s.scalars(select(Assignment)).one()
        assert a.status == "CANCELADA" and "dejó de estar disponible" in a.reason
        assert s.get(Operator, op).current_load == 0 and s.get(Request, rid).status == "REINTENTO"
        hist = [(e.from_status, e.to_status) for e in s.scalars(
            select(RequestEvent).where(RequestEvent.request_id == rid).order_by(RequestEvent.id))]
        assert hist[-2:] == [("ELEGIBLE", "ASIGNADO"), ("ASIGNADO", "REINTENTO")]
    _ok_integrity(db)


def test_window_expired_in_transit_is_not_counted():
    """La respuesta dice 2 disponibles, pero 1 venía de una ventana que venció mientras viajaba."""
    c = ProveedorPruebaConnector({"url_base": "http://127.0.0.1:1", "region": "norte"})
    hace = (datetime.now(timezone.utc) - timedelta(milliseconds=50)).isoformat()
    o = c._map({"id": "X", "nombre": "X", "tipo": "entrada", "capacidad": 4, "disponibles": 2, "estado": "ABIERTO",
                "ventanas": [{"unidades": 1, "hasta": hace}], "actualizado_en": hace}, datetime.now(timezone.utc))
    assert o.available == 1 and o.attributes["vencidas_en_transito"] == 1


# ---------------------------------------------------------------- concurrencia
def test_two_operators_compete_for_the_same_assignment(motor, prov):
    db = motor.db
    op1 = make_operator(db, "Operador A", regions=["NORTE"])
    op2 = make_operator(db, "Operador B", regions=["NORTE"])
    make_request(db, "SOL-HTTP-4", region="NORTE", rtype="entrada", conditions={"nombre_contiene": "Norte · 6"})
    motor.poll()
    prov.liberar("NOR-006", 1)
    motor.poll()
    motor.process()
    with db.session() as s:
        aid = s.scalars(select(Assignment.id)).one()
    results, barrier = [], threading.Barrier(2)

    def take():
        barrier.wait()
        results.append(asg.take_assignment(db, aid, None, RealClock().now(), "panel"))
    ts = [threading.Thread(target=take) for _ in (op1, op2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == [False, True]
    _ok_integrity(db)


def test_two_engine_workers_processing_same_events_never_overallocate(motor, prov):
    db = motor.db
    m = Motor(db, prov, region="centro")                             # región que no tocan otras pruebas
    for i in range(4):
        make_operator(db, f"Operador {i}", regions=["CENTRO"], max_concurrent=10)
    for i in range(30):
        make_request(db, f"SOL-C-{i}", region="CENTRO", rtype="entrada", priority=1 + i % 5)
    m.poll()
    for rid in ("CEN-007", "CEN-008", "CEN-009"):
        prov.liberar(rid, 2)
    m.poll()
    barrier = threading.Barrier(2)

    def worker():
        barrier.wait()
        pl.process_pending_events(db, RealClock())
    ts = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    with db.session() as s:
        live = s.scalars(select(Assignment).where(Assignment.status == "ACTIVA")).all()
        assert len(live) == 6                                        # 3 recursos x 2 unidades
        assert len({a.request_id for a in live}) == 6                # ninguna solicitud dos veces
        for st in s.scalars(select(SlotState).where(SlotState.held > 0)):
            assert st.held <= st.available
        assert s.scalar(select(func.count()).select_from(Alert).where(Alert.kind == "ASIGNACION")) == 6
    _ok_integrity(db)


# ---------------------------------------------------------------- reinicios
def test_engine_restart_keeps_connector_state_and_catches_up(motor, prov, db):
    motor.poll()
    st = motor.source()["connector_state"]
    # "reinicio" del motor: estado en memoria nuevo, misma base de datos
    m2 = Motor.__new__(Motor)
    m2.db, m2.prov, m2.clock, m2.runtime, m2.sid = db, prov, RealClock(), pl.RuntimeState(), motor.sid
    assert m2.poll().outcome == "OK" and m2.source()["last_result"].get("sin_modificar") is True
    prov.liberar("NOR-010", 2)                                       # cambio mientras el motor "estaba caído"
    assert m2.poll().events == 1 and m2.slot("NOR-010")["available"] == 2
    assert m2.source()["connector_state"]["version"] > st["version"]


def test_provider_restart_graceful_and_abrupt(tmp_path, db, monkeypatch):
    p = ProveedorProceso(tmp_path / "r.json", extra=["--cuota-por-min", "0"])
    p.iniciar()
    monkeypatch.setenv(TOKEN_ENV, p.token)
    m = Motor(db, p, region="este", max_retries=0)
    try:
        m.poll()
        p.liberar("EST-001", 2)
        assert m.poll().events == 1
        p.detener()                                                  # parada ordenada: guarda el estado
        out = m.poll()
        assert out.outcome == "ERROR_TEMPORAL" and "conexión" in out.error
        p.iniciar()
        assert p.estado()["arranques"] == 2
        assert m.poll().events == 0                                  # mismo estado: ningún cambio falso
        # corte brusco justo después de un cambio: puede perderse lo no guardado (< 0,5 s)
        p.liberar("EST-002", 1)
        p.detener(forzar=True)
        p.iniciar()
        m.runtime = pl.RuntimeState()
        out = m.poll()
        assert out.outcome == "OK"
        for it in p.disponibilidad("este"):                          # el motor converge a lo publicado
            assert m.slot(it["id"])["available"] == it["disponibles"]
        _ok_integrity(db)
    finally:
        p.detener()


# ---------------------------------------------------------------- errores temporales de la base de datos
def test_db_error_while_saving_poll_loses_nothing(motor, prov, monkeypatch):
    motor.poll()
    prov.liberar("NOR-011", 2)
    real = pl.detect
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("UPDATE slot_states", {}, Exception("database is locked"))
        return real(*a, **kw)
    monkeypatch.setattr(pl, "detect", flaky)
    with pytest.raises(OperationalError):
        motor.poll()
    assert motor.slot("NOR-011")["available"] == 0                   # transacción revertida entera
    # el ETag tampoco avanzó: la siguiente consulta trae los datos y detecta el cambio (no un 304)
    out = motor.poll()
    assert out.events == 1 and motor.slot("NOR-011")["available"] == 2


def test_db_error_while_processing_event_retries_exactly_once(motor, prov, monkeypatch):
    db = motor.db
    make_operator(db, "Operador Norte", regions=["NORTE"])
    make_request(db, "SOL-HTTP-5", region="NORTE", rtype="entrada", conditions={"nombre_contiene": "Norte · 12"})
    motor.poll()
    prov.liberar("NOR-012", 1)
    motor.poll()
    real = pl._assignment_alert
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("INSERT alerts", {}, Exception("database is locked"))
        return real(*a, **kw)
    monkeypatch.setattr(pl, "_assignment_alert", flaky)
    with pytest.raises(OperationalError):
        motor.process()
    motor.process()
    pl.maintenance(db, RealClock())                                  # la reevaluación tampoco duplica
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Assignment)) == 1
        assert s.scalar(select(func.count()).select_from(ChangeEvent).where(ChangeEvent.processed.is_(False))) == 0
        # el operador TIENE que enterarse: exactamente un aviso de asignación, aunque hubo un fallo a mitad
        aid = s.scalar(select(Assignment.id))
        assert s.scalar(select(func.count()).select_from(Alert).where(Alert.assignment_id == aid)) == 1
    _ok_integrity(db)


def test_db_error_during_rebalance_still_notifies_the_operator(motor, prov, monkeypatch):
    db = motor.db
    m = Motor(db, prov, region="este")
    m.poll()
    prov.liberar("EST-010", 1)
    m.poll()
    m.process()                                                      # sin solicitudes: queda libre
    make_operator(db, "Operador Este", regions=["ESTE"])
    make_request(db, "SOL-HTTP-6", region="ESTE", rtype="unidad", conditions={"nombre_contiene": "Este · 10"})
    real = pl._assignment_alert
    monkeypatch.setattr(pl, "_assignment_alert", lambda *a, **k: (_ for _ in ()).throw(
        OperationalError("INSERT alerts", {}, Exception("database is locked"))))
    with pytest.raises(OperationalError):
        pl.maintenance(db, RealClock())                              # asigna y falla antes de avisar
    monkeypatch.setattr(pl, "_assignment_alert", real)
    pl.maintenance(db, RealClock())
    pl.maintenance(db, RealClock())
    with db.session() as s:
        aid = s.scalar(select(Assignment.id).where(Assignment.status == "ACTIVA"))
        assert s.scalar(select(func.count()).select_from(Alert).where(Alert.assignment_id == aid)) == 1


# ---------------------------------------------------------------- independencia del motor
def test_engine_core_has_no_provider_specific_code():
    """El núcleo (motor, API, modelos) no sabe nada de este proveedor: solo el conector."""
    root = Path(__file__).resolve().parent.parent / "monitoreo"
    offenders = []
    for f in root.rglob("*.py"):
        rel = f.relative_to(root).as_posix()
        if rel.startswith("connectors/") or rel.startswith("migrations/"):
            continue
        text = f.read_text(encoding="utf-8")
        if "proveedor_prueba" in text or "ProveedorPrueba" in text or "X-Control-Token" in text:
            offenders.append(rel)
    assert offenders == []
