"""v1.7: centro de operaciones, supervisión, redistribución y explicación de la asignación.

Las cifras del centro se comparan con conteos hechos directamente sobre la base de datos: si el
panel inventara o redondeara algo, estas pruebas fallan."""
from datetime import timedelta

from sqlalchemy import func, select

from monitoreo.db import utcnow
from monitoreo.models import Assignment, AuditLog, Operator, Request, Source

from .test_api import app_db, client, login  # noqa: F401  (fixture)
from .test_asignaciones_abandonadas import _assigned


def _otro_operador(c, h, user="opy", nombre="Operador Y"):
    c.post("/api/users", headers=h, json={"username": user, "password": "Operador-456", "role": "operador",
                                          "regions": ["OESTE"], "operator_name": nombre})
    return login(c, user, "Operador-456")


def test_centro_figures_match_database(app_db):
    with client(app_db) as c:
        h, ho, aid, rid = _assigned(c)
        db = c.app.state.db
        r = c.get("/api/centro", headers=h)
        assert r.status_code == 200, r.text
        d = r.json()
        with db.session() as s:
            activas = s.scalar(select(func.count()).select_from(Assignment).where(
                Assignment.status.in_(["ACTIVA", "EN_PROCESO"])))
            fuentes = s.scalar(select(func.count()).select_from(Source))
            cola = s.scalar(select(func.count()).select_from(Request).where(
                Request.status.in_(["ELEGIBLE", "REINTENTO", "PENDIENTE"])))
        assert d["indicadores"]["asignaciones_activas"] == activas == 1
        assert d["estado_general"]["fuentes"]["total"] == fuentes
        assert d["estado_general"]["solicitudes_pendientes"] == cola
        assert d["indicadores"]["disponibilidades_nuevas"] >= 1      # la liberación del laboratorio
        assert d["indicadores"]["consultas"] >= 2
        assert "captura" in d                                          # laboratorio habilitado (no producción)
        # filtro por región: la asignación es de OESTE
        assert c.get("/api/centro?region=NORTE", headers=h).json()["indicadores"]["asignaciones_activas"] == 0
        assert c.get("/api/centro?region=OESTE", headers=h).json()["indicadores"]["asignaciones_activas"] == 1
        assert c.get("/api/centro?periodo=1h", headers=h).status_code == 200
        assert c.get("/api/centro?periodo=hoy", headers=h).status_code == 200
        assert c.get("/api/centro?periodo=1año", headers=h).status_code == 422


def test_operator_only_sees_own_figures_and_explanations(app_db):
    with client(app_db) as c:
        h, ho, aid, rid = _assigned(c)
        hy = _otro_operador(c, h)
        dx = c.get("/api/centro", headers=ho).json()
        dy = c.get("/api/centro?operador=1", headers=hy).json()      # intenta ver a otro: se ignora
        assert dx["indicadores"]["asignaciones_activas"] == 1
        assert dy["indicadores"]["asignaciones_activas"] == 0
        assert dy["filtros"]["operador"] != 1 or dy["filtros"]["operador"] == dx["filtros"]["operador"] is None
        assert all(a.get("assignment_id") != aid for a in dy["alertas_importantes"])
        assert c.get("/api/supervision", headers=ho).status_code == 403
        assert c.post("/api/supervision/operadores/1/redistribuir", headers=ho,
                      json={"confirmo": True, "motivo": "prueba de acceso"}).status_code == 403
        assert c.get(f"/api/asignaciones/{aid}/explicacion", headers=hy).status_code == 404
        mia = c.get(f"/api/asignaciones/{aid}/explicacion", headers=ho).json()["explicacion"]
        assert set(mia) == {"resumen", "criterio"} and "Operador X" in mia["resumen"]
        assert "Descartados" not in mia["resumen"] and "Otros candidatos" not in mia["resumen"]   # no ve a compañeros


def test_assignment_explanation_is_stored_and_readable(app_db):
    with client(app_db) as c:
        h, ho, aid, rid = _assigned(c)
        e = c.get(f"/api/asignaciones/{aid}/explicacion", headers=h).json()["explicacion"]
        assert e["criterio"] and e["reparto_modo"] == "menor_carga"
        assert e["elegido"] == "Operador X"
        assert e["region"] == "OESTE" and e["solicitud"]["prioridad"] == 1
        # los demás operadores se desactivaron en _assigned: aparecen como descartados con su motivo
        assert any(x["motivo"] == "desactivado" for x in e["descartados"])
        assert "Operador X" in e["resumen"] and "menor carga" in e["resumen"]


def test_supervision_presence_states(app_db):
    with client(app_db) as c:
        h, ho, aid, rid = _assigned(c)
        db = c.app.state.db
        c.get("/api/auth/me", headers=ho)

        def presencia():
            ops = c.get("/api/supervision", headers=h).json()["operadores"]
            return next(o for o in ops if o["nombre"] == "Operador X")
        fila = presencia()
        assert fila["presencia"] == "CONECTADO" and fila["asignadas"] == 1 and fila["carga"] == 1
        with db.session() as s:
            o = s.scalar(select(Operator).where(Operator.name == "Operador X"))
            o.visto_en = utcnow() - timedelta(seconds=300)          # > 90 s, < 900 s
        assert presencia()["presencia"] == "CONEXION_PERDIDA"
        with db.session() as s:
            s.scalar(select(Operator).where(Operator.name == "Operador X")).visto_en = utcnow() - timedelta(hours=2)
        assert presencia()["presencia"] == "INACTIVO"
        assert c.post("/api/auth/logout", headers=ho).status_code == 200
        assert presencia()["presencia"] == "DESCONECTADO"
        sin_usuario = [o for o in c.get("/api/supervision", headers=h).json()["operadores"]
                       if o["presencia"] == "SIN_USUARIO"]
        assert sin_usuario                                            # operadores de demostración sin cuenta


def test_redistribution_releases_untaken_work_and_is_audited(app_db):
    with client(app_db) as c:
        h, ho, aid, rid = _assigned(c)
        db = c.app.state.db
        with db.session() as s:
            oid = s.scalar(select(Operator.id).where(Operator.name == "Operador X"))
        url = f"/api/supervision/operadores/{oid}/redistribuir"
        assert c.post(url, headers=h, json={"motivo": "no contesta"}).status_code == 422     # falta confirmar
        r = c.post(url, headers=h, json={"confirmo": True, "motivo": "no contesta", "desactivar": True})
        assert r.status_code == 200, r.text
        assert r.json()["liberadas"] == [{"asignacion": aid}]
        with db.session() as s:
            a = s.get(Assignment, aid)
            assert a.status == "DECLINADA"
            assert s.get(Operator, oid).current_load == 0 and s.get(Operator, oid).active is False
            assert s.get(Request, rid).status in ("ELEGIBLE", "REINTENTO")   # vuelve a la cola
            log = s.scalar(select(AuditLog).where(AuditLog.action == "operador_redistribuido"))
            assert log.detail["liberadas"] == 1 and log.username == "admin"
        acc = c.get("/api/supervision", headers=h).json()["acciones_recientes"]
        assert acc[0]["accion"] == "operador_redistribuido"


def test_redistribution_never_releases_work_in_progress_with_recent_signal(app_db):
    with client(app_db) as c:
        h, ho, aid, rid = _assigned(c)
        db = c.app.state.db
        assert c.post(f"/api/assignments/{aid}/tomar", headers=ho).status_code == 200
        with db.session() as s:
            oid = s.scalar(select(Operator.id).where(Operator.name == "Operador X"))
        url = f"/api/supervision/operadores/{oid}/redistribuir"
        # sin incluir_en_proceso: la tomada no se toca
        r = c.post(url, headers=h, json={"confirmo": True, "motivo": "revisión"}).json()
        assert r["liberadas"] == [] and r["no_liberadas"][0]["asignacion"] == aid
        # incluyéndola, pero el operador acaba de dar señal: tampoco
        r = c.post(url, headers=h, json={"confirmo": True, "motivo": "revisión", "incluir_en_proceso": True}).json()
        assert r["liberadas"] == [] and "señal" in r["no_liberadas"][0]["motivo"]
        with db.session() as s:
            assert s.get(Assignment, aid).status == "EN_PROCESO"
            s.get(Operator, oid).visto_en = utcnow() - timedelta(hours=1)
        # sin señal hace una hora y sin confirmación en curso: se libera
        r = c.post(url, headers=h, json={"confirmo": True, "motivo": "revisión", "incluir_en_proceso": True}).json()
        assert r["liberadas"] == [{"asignacion": aid}]
        with db.session() as s:
            assert s.get(Assignment, aid).status == "DECLINADA"
