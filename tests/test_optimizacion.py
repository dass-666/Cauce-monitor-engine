"""v1.4: regresiones de rendimiento medidas con perfil (ver docs/INFORME_v1.4.md, P3)."""
import time

from sqlalchemy import func, select

from monitoreo.db import utcnow
from monitoreo.engine.clock import RealClock
from monitoreo.engine.pipeline import maintenance
from monitoreo.models import Request, SlotState, Source

from .test_api import app_db, client, login  # noqa: F401  (fixture)


def test_maintenance_is_linear_in_the_number_of_requests(db):
    """La validación de 2.000 solicitudes en una sesión tardaba ~12 s (crecía con el cuadrado)."""
    now = utcnow()
    with db.session() as s:
        for k in range(2000):
            s.add(Request(code=f"LIN-{k}", holder="Ficticio", region="NORTE", resource_type="entrada", priority=1,
                          quantity=1, conditions={}, status="PENDIENTE", created_at=now, updated_at=now))
    t = time.perf_counter()
    maintenance(db, RealClock())
    assert time.perf_counter() - t < 6
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Request).where(Request.status == "PENDIENTE")) == 0


def test_dashboard_region_counts_match_direct_queries(app_db):
    with client(app_db) as c:
        h = login(c)
        for k in range(12):
            c.post("/api/requests", headers=h, json={"code": f"DSH-{k}", "holder": "Ficticio", "priority": 2,
                                                     "region": ["NORTE", "SUR", "ESTE"][k % 3], "resource_type": "entrada"})
        d = c.get("/api/dashboard", headers=h).json()
        db = c.app.state.db
        with db.session() as s:
            for r in d["regiones"]:
                srcs = list(s.scalars(select(Source).where(Source.region == r["code"])))
                assert r["fuentes"] == len(srcs)
                assert r["fuentes_activas"] == sum(x.status == "ACTIVA" for x in srcs)
                assert r["fuentes_con_error"] == sum(x.status == "ERROR" or x.circuit_state != "CERRADO" for x in srcs)
                assert r["unidades_libres"] == (s.scalar(select(func.coalesce(func.sum(SlotState.available - SlotState.held), 0))
                                                         .where(SlotState.region == r["code"], SlotState.status == "ACTIVO")))
                assert r["solicitudes_en_cola"] == s.scalar(select(func.count()).select_from(Request).where(
                    Request.region == r["code"], Request.status.in_(["PENDIENTE", "ELEGIBLE", "REINTENTO"])))
