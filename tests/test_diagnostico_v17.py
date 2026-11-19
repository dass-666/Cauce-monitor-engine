"""v1.7 · diagnóstico ampliado y reparaciones (seguras automáticas y auditadas; las que borran,
solo con confirmación de un administrador)."""
from sqlalchemy import select

from monitoreo import diagnostico, reparaciones
from monitoreo.db import utcnow
from monitoreo.engine.integrity import check
from monitoreo.models import Assignment, AuditLog, Operator

from .test_api import app_db, client, login  # noqa: F401  (fixture)
from .test_asignaciones_abandonadas import _assigned


def test_carga_descuadrada_se_repara_sola_y_queda_auditada(app_db):
    with client(app_db) as c:
        h, ho, aid, rid = _assigned(c)
        db = c.app.state.db
        with db.session() as s:
            o = s.scalar(select(Operator).where(Operator.name == "Operador X"))
            o.current_load = 3                                   # simula un descuadre (la real es 1)
            oid = o.id
        with db.session() as s:
            assert any(f"operador {oid}" in p for p in check(s))
        rev = {r["clave"]: r for r in reparaciones.revisar(db, utcnow())}
        assert rev["cargas"]["pendientes"] == 1 and rev["cargas"]["segura"]
        res = diagnostico.run_and_alert(db, None, utcnow())
        assert res["reparaciones_automaticas"] == {"cargas": 1}
        with db.session() as s:
            assert s.get(Operator, oid).current_load == 1 and check(s) == []
            log = s.scalar(select(AuditLog).where(AuditLog.action == "reparacion_cargas"))
            assert log.detail["cambios"][0] == {"operador": oid, "antes": 3, "despues": 1}
            assert s.scalar(select(AuditLog).where(AuditLog.action == "reparacion_aplicada")).username.startswith("motor")
        # segunda pasada: nada que hacer
        assert diagnostico.run_and_alert(db, None, utcnow())["reparaciones_automaticas"] == {}


def test_reparaciones_por_api_permisos_y_confirmacion(app_db):
    with client(app_db) as c:
        h, ho, aid, rid = _assigned(c)
        assert c.get("/api/diagnostico/reparaciones", headers=ho).status_code == 403
        assert c.post("/api/diagnostico/reparaciones/cargas", headers=ho, json={}).status_code == 403
        lst = c.get("/api/diagnostico/reparaciones", headers=h).json()["reparaciones"]
        assert {r["clave"] for r in lst} >= {"cargas", "entregas_atascadas", "entregas_huerfanas"}
        r = c.post("/api/diagnostico/reparaciones/entregas_huerfanas", headers=h, json={})
        assert r.status_code == 422                                     # borra datos: exige confirmación
        r = c.post("/api/diagnostico/reparaciones/entregas_huerfanas", headers=h, json={"confirmo": True, "motivo": "limpieza"})
        assert r.status_code == 200 and r.json()["aplicada"]
        assert c.post("/api/diagnostico/reparaciones/no-existe", headers=h, json={}).status_code == 404
        audit = c.get("/api/audit?limit=50", headers=h).json()
        assert any(a["accion"] == "reparacion_aplicada" and a["objetivo"] == "entregas_huerfanas" for a in audit)


def test_diagnostico_cubre_operaciones_eventos_expedientes_y_retencion(app_db):
    with client(app_db) as c:
        h, ho, aid, rid = _assigned(c)
        assert c.post(f"/api/assignments/{aid}/tomar", headers=ho).status_code == 200
        db = c.app.state.db
        with db.session() as s:
            s.get(Assignment, aid).inactive_alerted_at = utcnow()
        d = c.get("/api/autodiagnostico", headers=h).json()
        areas = {i["area"] for i in d["comprobaciones"]}
        assert {"Eventos", "Operaciones pendientes", "Expedientes", "Retención"} <= areas
        ab = next(i for i in d["comprobaciones"] if i["clave"] == "op:abandonadas")
        assert ab["estado"] == "AVISO" and "No se liberan solas" in ab["detalle"]
        with db.session() as s:
            assert s.get(Assignment, aid).status == "EN_PROCESO"          # el diagnóstico no toca nada


def test_reparacion_de_cargas_en_ambos_motores(db):
    """SQLite y PostgreSQL (FOR UPDATE + recuento en la misma sentencia)."""
    from .helpers import make_operator
    oid = make_operator(db, "Descuadrado")
    with db.session() as s:
        s.get(Operator, oid).current_load = 2
    out = reparaciones.aplicar(db, "cargas", utcnow(), "admin")
    assert out["pendientes"] == 1
    with db.session() as s:
        assert s.get(Operator, oid).current_load == 0 and check(s) == []
