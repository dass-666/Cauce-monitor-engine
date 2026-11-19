"""v1.7 · auditoría adversarial de lo nuevo: carreras, zona horaria, presencia y redistribución
frente a una confirmación en curso. Estas pruebas FIJAN el comportamiento correcto (si alguna
falla en el futuro, es una regresión)."""
from datetime import datetime, timedelta

from sqlalchemy import select

from monitoreo.db import utcnow
from monitoreo.engine import assignment as asg
from monitoreo.engine.params import get_params
from monitoreo.models import Assignment, AuditLog, Operator

from .test_api import app_db, client, login  # noqa: F401  (fixture)
from .test_asignaciones_abandonadas import _assigned


def test_redistribuir_no_gana_a_una_toma_simultanea(app_db):
    """Carrera: el administrador lee la asignación como ACTIVA y, antes de liberarla, el operador la
    toma. La liberación es un UPDATE condicional (solo si sigue ACTIVA): no debe quitársela."""
    with client(app_db) as c:
        h, ho, aid, rid = _assigned(c)
        db = c.app.state.db
        with db.session() as s:
            oid = s.get(Assignment, aid).operator_id
            params = get_params(s)
        assert c.post(f"/api/assignments/{aid}/tomar", headers=ho).status_code == 200      # la toma llega primero
        ok = asg.reject_assignment(db, aid, oid, utcnow(), "admin (redistribución)", params,
                                   reason="redistribución: carrera", only_status=("ACTIVA",), status="DECLINADA")
        assert ok is False
        with db.session() as s:
            assert s.get(Assignment, aid).status == "EN_PROCESO" and s.get(Operator, oid).current_load == 1


def test_presencia_se_escribe_como_mucho_cada_15_s(app_db):
    with client(app_db) as c:
        h, ho, aid, rid = _assigned(c)
        db = c.app.state.db
        c.get("/api/auth/me", headers=ho)
        with db.session() as s:
            v1 = s.scalar(select(Operator.visto_en).where(Operator.name == "Operador X"))
        for _ in range(5):
            c.get("/api/auth/me", headers=ho)
        with db.session() as s:
            assert s.scalar(select(Operator.visto_en).where(Operator.name == "Operador X")) == v1
            s.scalar(select(Operator).where(Operator.name == "Operador X")).visto_en = v1 - timedelta(seconds=20)
        c.get("/api/auth/me", headers=ho)
        with db.session() as s:
            assert s.scalar(select(Operator.visto_en).where(Operator.name == "Operador X")) > v1 - timedelta(seconds=1)


def test_periodo_hoy_usa_la_zona_horaria_configurada(monkeypatch):
    """«Hoy» empieza a medianoche LOCAL, no a medianoche UTC (a las 01:00 UTC en México aún es ayer)."""
    from monitoreo.api.centro import _desde
    monkeypatch.setenv("MONITOREO_ZONA_HORARIA", "America/Mexico_City")
    now = datetime(2026, 11, 14, 1, 0)                 # 13/11 19:00 en Ciudad de México (UTC-6)
    assert _desde("hoy", now) == datetime(2026, 11, 13, 6, 0)
    now = datetime(2026, 11, 14, 7, 0)                 # 14/11 01:00 local
    assert _desde("hoy", now) == datetime(2026, 11, 14, 6, 0)


def test_redistribucion_queda_auditada_aunque_no_libere_nada(app_db):
    with client(app_db) as c:
        h, ho, aid, rid = _assigned(c)
        assert c.post(f"/api/assignments/{aid}/tomar", headers=ho).status_code == 200
        db = c.app.state.db
        with db.session() as s:
            oid = s.get(Assignment, aid).operator_id
        r = c.post(f"/api/supervision/operadores/{oid}/redistribuir", headers=h,
                   json={"confirmo": True, "motivo": "comprobación"}).json()
        assert r["liberadas"] == []
        with db.session() as s:
            log = s.scalar(select(AuditLog).where(AuditLog.action == "operador_redistribuido"))
            assert log.detail["no_liberadas"] == 1
        assert c.post("/api/supervision/operadores/99999/redistribuir", headers=h,
                      json={"confirmo": True, "motivo": "comprobación"}).status_code == 404
