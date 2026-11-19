"""v1.4: asignaciones abandonadas (hallazgo 7.2 de la v1.3).

Caso real de la v1.3: el motor guardó la toma 13 ms antes de un corte brusco y ningún operador
recibió la respuesta; la asignación quedó EN_PROCESO sin nadie que la atendiera.
"""
import threading
import time
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from monitoreo.engine import assignment as asg
from monitoreo.engine.clock import RealClock
from monitoreo.engine.params import get_params, set_params
from monitoreo.engine.pipeline import flag_inactive_assignments, maintenance, process_pending_events
from monitoreo.models import Alert, Assignment, AuditLog, Operation, Request, RequestEvent

from .test_api import app_db, client, login  # noqa: F401  (fixture)


def _drain(c):
    db = c.app.state.db
    maintenance(db, RealClock())
    process_pending_events(db, RealClock())


def _assigned(c):
    """Crea una asignación ACTIVA para el operador 'opx' por el camino normal de la API."""
    h = login(c)
    c.post("/api/users", headers=h, json={"username": "opx", "password": "Operador-123", "role": "operador",
                                          "regions": ["OESTE"], "operator_name": "Operador X"})
    for op in c.get("/api/operators", headers=h).json():
        if op["name"] != "Operador X":
            c.patch(f"/api/operators/{op['id']}", headers=h, json={"active": False})
    c.post("/api/requests", headers=h, json={"code": "AB-1", "holder": "Ficticio", "region": "OESTE",
                                             "resource_type": "turno", "priority": 1})
    src = next(s for s in c.get("/api/sources", headers=h).json() if s["code"] == "sim-oeste")
    c.patch(f"/api/sources/{src['id']}", headers=h, json={"config": {"error_rate": 0}})
    c.post(f"/api/sources/{src['id']}/consultar", headers=h)
    item = next(i for i in c.get("/api/lab/items", headers=h).json() if i["region"] == "OESTE")
    c.post(f"/api/lab/items/{item['id']}/liberar", headers=h, json={"units": 1})
    time.sleep(1.1)
    c.post(f"/api/sources/{src['id']}/consultar", headers=h)
    _drain(c)
    ho = login(c, "opx", "Operador-123")
    mine = c.get("/api/assignments?mine=true", headers=ho).json()
    assert len(mine) == 1 and mine[0]["status"] == "ACTIVA"
    return h, ho, mine[0]["id"], mine[0]["request_id"]


def _transitions(db, rid, to):
    with db.session() as s:
        return s.scalar(select(func.count()).select_from(RequestEvent)
                        .where(RequestEvent.request_id == rid, RequestEvent.to_status == to))


def test_retake_with_same_key_returns_stored_result_without_repeating(app_db):
    with client(app_db) as c:
        h, ho, aid, rid = _assigned(c)
        k = {"Idempotency-Key": "tomar-ab1-0001"}
        assert c.get("/api/operaciones/tomar-ab1-0001", headers=ho).status_code == 404   # aún no existe
        r1 = c.post(f"/api/assignments/{aid}/tomar", headers={**ho, **k})
        r2 = c.post(f"/api/assignments/{aid}/tomar", headers={**ho, **k})   # "se perdió la respuesta"
        assert r1.status_code == r2.status_code == 200 and r2.json()["repetida"] is True
        assert _transitions(c.app.state.db, rid, "EN_PROCESO") == 1        # el efecto ocurrió UNA vez
        op = c.get("/api/operaciones/tomar-ab1-0001", headers=ho).json()
        assert op["accion"] == "tomar" and op["codigo"] == 200 and op["asignacion"] == str(aid)
        # cada usuario ve solo sus operaciones
        assert c.get("/api/operaciones/tomar-ab1-0001", headers=h).status_code == 404
        # la misma clave para otra operación es un error del cliente, no un éxito silencioso
        assert c.post(f"/api/assignments/{aid}/completar", headers={**ho, **k}).status_code == 422
        a = c.get("/api/assignments?mine=true", headers=ho).json()[0]
        assert a["taken_by"] == "opx" and a["taken_at"] and a["last_activity_at"]


def test_failed_operation_is_replayed_as_failure(app_db):
    with client(app_db) as c:
        h, ho, aid, rid = _assigned(c)
        c.post(f"/api/assignments/{aid}/rechazar", headers=ho)
        k = {"Idempotency-Key": "tomar-tarde-01"}
        assert c.post(f"/api/assignments/{aid}/tomar", headers={**ho, **k}).status_code == 409
        assert c.post(f"/api/assignments/{aid}/tomar", headers={**ho, **k}).status_code == 409
        assert c.get("/api/operaciones/tomar-tarde-01", headers=ho).json()["codigo"] == 409


def test_bad_keys_are_rejected(app_db):
    with client(app_db) as c:
        h, ho, aid, rid = _assigned(c)
        for bad in ("corta", "x" * 81, "con espacio 123", "barra/no/vale"):
            assert c.post(f"/api/assignments/{aid}/tomar", headers={**ho, "Idempotency-Key": bad}).status_code == 422


def test_admin_competition_without_key_still_has_one_winner(app_db):
    """La idempotencia no debe convertir una competencia real en dos éxitos."""
    with client(app_db) as c:
        h, ho, aid, rid = _assigned(c)
        assert c.post(f"/api/assignments/{aid}/tomar", headers=h).status_code == 200
        assert c.post(f"/api/assignments/{aid}/tomar", headers=h).status_code == 409


def test_same_key_sent_twice_concurrently_has_one_effect(app_db):
    with client(app_db) as c:
        h, ho, aid, rid = _assigned(c)
        k = {**ho, "Idempotency-Key": "concurrente-01"}
        barrier, codes = threading.Barrier(2), []

        def go():
            barrier.wait()
            codes.append(c.post(f"/api/assignments/{aid}/tomar", headers=k).status_code)
        ts = [threading.Thread(target=go) for _ in range(2)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert codes == [200, 200]
        assert _transitions(c.app.state.db, rid, "EN_PROCESO") == 1
        with c.app.state.db.session() as s:
            assert s.scalar(select(func.count()).select_from(Operation)) == 1


def test_crash_before_commit_leaves_nothing_and_retry_is_safe(app_db, monkeypatch):
    """Momento crítico: el proceso muere DESPUÉS de cambiar la asignación pero ANTES de confirmar.
    Como el resultado se guarda en la misma transacción, o están las dos cosas o ninguna."""
    with client(app_db) as c:
        h, ho, aid, rid = _assigned(c)
        real = asg.transition

        def boom(*a, **kw):
            raise RuntimeError("corte simulado")
        monkeypatch.setattr(asg, "transition", boom)
        k = {**ho, "Idempotency-Key": "corte-0000001"}
        with pytest.raises(RuntimeError):
            c.post(f"/api/assignments/{aid}/tomar", headers=k)
        assert c.get("/api/operaciones/corte-0000001", headers=ho).status_code == 404     # no se registró
        assert c.get("/api/assignments?mine=true", headers=ho).json()[0]["status"] == "ACTIVA"
        monkeypatch.setattr(asg, "transition", real)
        assert c.post(f"/api/assignments/{aid}/tomar", headers=k).status_code == 200        # reintento seguro
        assert _transitions(c.app.state.db, rid, "EN_PROCESO") == 1


def test_lost_take_response_is_recovered_by_retrying_without_key(app_db):
    """El caso de la v1.3: la toma se guardó, la respuesta no llegó y el operador reintenta."""
    with client(app_db) as c:
        h, ho, aid, rid = _assigned(c)
        db = c.app.state.db
        with db.session() as s:
            opid = s.get(Assignment, aid).operator_id
        assert asg.take_assignment(db, aid, opid, RealClock().now(), "opx")   # guardada; respuesta "perdida"
        r = c.post(f"/api/assignments/{aid}/tomar", headers=ho)
        assert r.status_code == 200 and r.json()["ya_tomada"] is True
        assert c.post(f"/api/assignments/{aid}/completar", headers=ho).status_code == 200


def test_inactive_assignment_is_flagged_once_not_released(app_db):
    with client(app_db) as c:
        h, ho, aid, rid = _assigned(c)
        db = c.app.state.db
        assert c.post(f"/api/assignments/{aid}/tomar", headers=ho).status_code == 200
        with db.session() as s:
            params = get_params(s)
        now = RealClock().now()
        assert flag_inactive_assignments(db, now + timedelta(minutes=29), params) == 0
        assert flag_inactive_assignments(db, now + timedelta(minutes=31), params) == 1
        assert flag_inactive_assignments(db, now + timedelta(minutes=90), params) == 0   # una vez por episodio
        with db.session() as s:
            a = s.get(Assignment, aid)
            assert a.status == "EN_PROCESO"                                             # NO se libera sola
            al = s.scalars(select(Alert).where(Alert.assignment_id == aid, Alert.kind == "SISTEMA")).one()
            assert al.operator_name == "Operador X" and al.payload["evento_sistema"] == "asignacion_inactiva"
            assert s.scalar(select(func.count()).select_from(AuditLog)
                            .where(AuditLog.action == "asignacion_inactiva")) == 1
        assert c.get("/api/assignments?mine=true", headers=ho).json()[0]["inactiva"] is True
        # el operador dice "sigo con esto": nuevo episodio, se puede volver a avisar más tarde
        assert c.post(f"/api/assignments/{aid}/actividad", headers=ho).status_code == 200
        assert c.get("/api/assignments?mine=true", headers=ho).json()[0]["inactiva"] is False
        assert flag_inactive_assignments(db, RealClock().now() + timedelta(minutes=31), params) == 1
        # umbral configurable; 0 desactiva
        with db.session() as s:
            set_params(s, {"inactividad_en_proceso_min": 0})
            params = get_params(s)
        assert flag_inactive_assignments(db, RealClock().now() + timedelta(days=3), params) == 0


def test_manual_recovery_is_admin_only_confirmed_and_audited(app_db):
    with client(app_db) as c:
        h, ho, aid, rid = _assigned(c)
        db = c.app.state.db
        c.post(f"/api/assignments/{aid}/tomar", headers=ho)
        with db.session() as s:
            params = get_params(s)
        flag_inactive_assignments(db, RealClock().now() + timedelta(minutes=31), params)
        body = {"resultado": "devolver_a_cola", "motivo": "el operador confirma que no reservó"}
        assert c.post(f"/api/assignments/{aid}/recuperar", headers=ho, json={**body, "confirmar": True}).status_code == 403
        assert c.post(f"/api/assignments/{aid}/recuperar", headers=h, json=body).status_code == 422   # sin confirmar
        r = c.post(f"/api/assignments/{aid}/recuperar", headers=h, json={**body, "confirmar": True})
        assert r.status_code == 200
        with db.session() as s:
            assert s.get(Assignment, aid).status == "RECHAZADA"
            assert s.get(Request, rid).status == "REINTENTO"
            log = s.scalars(select(AuditLog).where(AuditLog.action == "asignacion_recuperar")).one()
            assert log.detail["motivo"] == body["motivo"] and log.detail["tomada_por"] == "opx"
            al = s.scalars(select(Alert).where(Alert.assignment_id == aid, Alert.kind == "SISTEMA")).one()
            assert al.acknowledged_by == "admin"                                         # aviso reconocido
        # repetir la recuperación no hace nada más
        assert c.post(f"/api/assignments/{aid}/recuperar", headers=h, json={**body, "confirmar": True}).status_code == 409


def test_manual_recovery_as_completed(app_db):
    with client(app_db) as c:
        h, ho, aid, rid = _assigned(c)
        c.post(f"/api/assignments/{aid}/tomar", headers=ho)
        r = c.post(f"/api/assignments/{aid}/recuperar", headers=h,
                   json={"resultado": "completada", "motivo": "reserva verificada en la fuente", "confirmar": True})
        assert r.status_code == 200
        assert c.get(f"/api/requests/{rid}", headers=h).json()["status"] == "COMPLETADO"


def test_activity_only_for_own_in_progress_assignment(app_db):
    with client(app_db) as c:
        h, ho, aid, rid = _assigned(c)
        assert c.post(f"/api/assignments/{aid}/actividad", headers=ho).status_code == 409    # aún ACTIVA
        c.post(f"/api/assignments/{aid}/tomar", headers=ho)
        c.post("/api/users", headers=h, json={"username": "opy", "password": "Operador-123", "role": "operador",
                                              "regions": ["OESTE"], "operator_name": "Operador Y"})
        hy = login(c, "opy", "Operador-123")
        assert c.post(f"/api/assignments/{aid}/actividad", headers=hy).status_code == 409    # no es suya
        assert c.post(f"/api/assignments/{aid}/tomar", headers=hy).status_code == 409        # tampoco "ya_tomada"
