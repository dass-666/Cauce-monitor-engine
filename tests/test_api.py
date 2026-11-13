import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

from monitoreo.app import create_app
from monitoreo.config import settings

ADMIN_PWD = "Admin-Prueba-123"


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "admin_password", ADMIN_PWD)
    url = f"sqlite:///{(tmp_path / 'api.db').as_posix()}"
    return url


def client(url, seed=True):
    return TestClient(create_app(url, start_engine=False, seed_demo=seed))


def login(c, user="admin", pwd=ADMIN_PWD):
    r = c.post("/api/auth/login", json={"username": user, "password": pwd})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_auth_required_and_login_flow(app_db):
    with client(app_db) as c:
        assert c.get("/api/dashboard").status_code == 401
        assert c.get("/api/dashboard", headers={"Authorization": "Bearer falso"}).status_code == 401
        assert c.post("/api/auth/login", json={"username": "admin", "password": "mal"}).status_code == 401
        h = login(c)
        assert c.get("/api/auth/me", headers=h).json()["role"] == "admin"
        c.post("/api/auth/logout", headers=h)
        assert c.get("/api/auth/me", headers=h).status_code == 401   # token revocado


def test_login_throttling(app_db):
    with client(app_db) as c:
        for _ in range(5):
            c.post("/api/auth/login", json={"username": "victima", "password": "x"})
        assert c.post("/api/auth/login", json={"username": "victima", "password": "x"}).status_code == 429


def test_roles_and_operator_scope(app_db):
    with client(app_db) as c:
        h = login(c)
        r = c.post("/api/users", headers=h, json={"username": "op1", "password": "Operador-123",
                                                  "role": "operador", "regions": ["NORTE"]})
        assert r.status_code == 201
        assert c.post("/api/users", headers=h, json={"username": "debil", "password": "aaaaaaaaaa",
                                                     "role": "operador"}).status_code == 422
        ho = login(c, "op1", "Operador-123")
        assert c.get("/api/dashboard", headers=ho).status_code == 200
        assert c.get("/api/audit", headers=ho).status_code == 403
        assert c.put("/api/settings/engine", headers=ho, json={"ttl_asignacion_s": 5}).status_code == 403
        assert c.post("/api/lab/items/1/liberar", headers=ho, json={"units": 1}).status_code == 403
        assert c.post("/api/sources/1/suspender", headers=ho).status_code == 403
        # un operador sí puede registrar solicitudes
        r = c.post("/api/requests", headers=ho, json={"holder": "Ficticio", "region": "NORTE",
                                                      "resource_type": "entrada"})
        assert r.status_code == 201 and r.json()["status"] == "ELEGIBLE"


def test_input_validation(app_db):
    with client(app_db) as c:
        h = login(c)
        bad = [
            {"holder": "x", "region": "NORTE", "resource_type": "entrada", "priority": 9},
            {"holder": "x", "region": "NO-EXISTE", "resource_type": "entrada"},
            {"holder": "x", "region": "NORTE", "resource_type": "entrada", "campo_extra": 1},
            {"holder": "", "region": "NORTE", "resource_type": "entrada"},
            {"holder": "x", "region": "NORTE", "resource_type": "entrada", "code": "<script>"},
        ]
        for b in bad:
            assert c.post("/api/requests", headers=h, json=b).status_code == 422, b
        r = c.post("/api/sources", headers=h, json={"code": "malo", "name": "x", "region": "NORTE",
                                                    "availability_type": "evento", "connector_type": "json",
                                                    "config": {"path": "../../etc/passwd"}})
        assert r.status_code == 422
        r = c.post("/api/sources", headers=h, json={"code": "malo2", "name": "x", "region": "NORTE",
                                                    "availability_type": "evento", "connector_type": "http",
                                                    "config": {"url": "http://169.254.169.254/latest"}})
        assert r.status_code == 422
        assert c.put("/api/settings/engine", headers=h, json={"desconocido": 1}).status_code == 422
        assert c.put("/api/settings/engine", headers=h, json={"ttl_asignacion_s": -5}).status_code == 422


def test_secrets_masked_and_audit_redacted(app_db):
    with client(app_db) as c:
        h = login(c)
        r = c.post("/api/channels", headers=h, json={"name": "WH", "type": "webhook",
                                                     "config": {"url": "http://127.0.0.1:9/x", "secret": "s3cr3t"}})
        assert r.json()["config"]["secret"] == "********"
        assert "s3cr3t" not in c.get("/api/channels", headers=h).text
        assert "s3cr3t" not in c.get("/api/audit", headers=h).text
        # editar sin reenviar el secreto lo conserva
        cid = r.json()["id"]
        c.patch(f"/api/channels/{cid}", headers=h, json={"config": {"secret": "********", "timeout_s": 2}})
        from monitoreo.db import Database
        from monitoreo.models import Channel
        d = Database(app_db)
        with d.session() as s:
            assert s.get(Channel, cid).config["secret"] == "s3cr3t"
        d.engine.dispose()


def test_security_headers(app_db):
    with client(app_db) as c:
        r = c.get("/")
        assert "default-src 'self'" in r.headers["content-security-policy"]
        assert r.headers["x-frame-options"] == "DENY"


def test_acceptance_flow_via_api_and_restart(app_db):
    """Criterios de aceptación: 5 regiones, registrar solicitud, liberar, detectar, asignar, alertar,
    historial, y reinicio sin pérdida de datos."""
    with client(app_db) as c:
        h = login(c)
        dash = c.get("/api/dashboard", headers=h).json()
        assert len(dash["regiones"]) == 5
        # consultar todas las fuentes simuladas para tener la línea base
        sources = [s for s in c.get("/api/sources", headers=h).json() if s["connector_type"] == "simulacion"]
        assert len(sources) == 5
        for s in sources:
            assert c.post(f"/api/sources/{s['id']}/consultar", headers=h).json()["resultado"] in ("OK", "ERROR_TEMPORAL")
        _drain(c)
        r = c.post("/api/requests", headers=h, json={"code": "ACEPT-1", "holder": "Titular Ficticio", "region": "ESTE",
                                                     "resource_type": "unidad", "priority": 1})
        rid = r.json()["id"]
        item = next(i for i in c.get("/api/lab/items", headers=h).json() if i["region"] == "ESTE")
        assert c.post(f"/api/lab/items/{item['id']}/liberar", headers=h, json={"units": 1}).status_code == 200
        src = next(s for s in sources if s["region"] == "ESTE")
        res = None
        for _ in range(5):   # el simulador tiene 2% de error temporal: se reintenta
            res = c.post(f"/api/sources/{src['id']}/consultar", headers=h).json()
            if res["resultado"] == "OK":
                break
            time.sleep(1.1)
        assert res["eventos"] >= 1
        _drain(c)
        req = c.get(f"/api/requests/{rid}", headers=h).json()
        assert req["status"] == "ASIGNADO", req
        assert [x["to"] for x in req["historial"]] == ["PENDIENTE", "ELEGIBLE", "ASIGNADO"]
        alerts = c.get("/api/alerts", headers=h).json()
        mine = [a for a in alerts if a["request_id"] == rid]
        assert mine and mine[0]["kind"] == "ASIGNACION" and mine[0]["operator_name"]
        assert c.post(f"/api/alerts/{mine[0]['id']}/confirmar", headers=h).json()["ok"]
        events = c.get("/api/events?region=ESTE", headers=h).json()
        assert any(e["change_type"] == "NUEVA_DISPONIBILIDAD" for e in events)
        n_fuentes = len(c.get("/api/sources", headers=h).json())
    # ---- reinicio: nueva instancia de la aplicación sobre la misma base de datos
    with client(app_db) as c2:
        h = login(c2)
        req = c2.get(f"/api/requests/{rid}", headers=h).json()
        assert req["status"] == "ASIGNADO" and len(req["asignaciones"]) == 1
        a = [x for x in c2.get("/api/alerts", headers=h).json() if x["request_id"] == rid][0]
        assert a["status"] == "CONFIRMADA"
        assert len(c2.get("/api/sources", headers=h).json()) == n_fuentes   # no se resiembra
        assert c2.get("/api/metrics", headers=h).json()["integridad"] == []


def _drain(c):
    from monitoreo.engine.clock import RealClock
    from monitoreo.engine.pipeline import maintenance, process_pending_events
    from monitoreo.engine.params import get_params
    from monitoreo.engine import alerts as al
    db = c.app.state.db
    maintenance(db, RealClock())
    process_pending_events(db, RealClock())
    with db.session() as s:
        params = get_params(s)
    al.dispatch_pending(db, RealClock().now(), params)


def test_operator_take_complete_via_api(app_db):
    with client(app_db) as c:
        h = login(c)
        c.post("/api/users", headers=h, json={"username": "opx", "password": "Operador-123", "role": "operador",
                                              "regions": ["OESTE"], "operator_name": "Operador X"})
        # desactivar los demás operadores para forzar que la asignación sea de opx
        for op in c.get("/api/operators", headers=h).json():
            if op["name"] != "Operador X":
                c.patch(f"/api/operators/{op['id']}", headers=h, json={"active": False})
        c.post("/api/requests", headers=h, json={"code": "OP-1", "holder": "Ficticio", "region": "OESTE",
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
        assert len(mine) == 1
        aid = mine[0]["id"]
        assert c.post(f"/api/assignments/{aid}/tomar", headers=ho).status_code == 200
        # v1.4: el MISMO operador que repite "tomar" (p. ej. tras perder la respuesta) recibe éxito
        again = c.post(f"/api/assignments/{aid}/tomar", headers=ho)
        assert again.status_code == 200 and again.json()["ya_tomada"] is True
        assert c.post(f"/api/assignments/{aid}/completar", headers=ho).status_code == 200
        assert c.post(f"/api/assignments/{aid}/tomar", headers=ho).status_code == 409   # ya completada
        r = c.get(f"/api/requests/{mine[0]['request_id']}", headers=h).json()
        assert r["status"] == "COMPLETADO"


def test_lab_competition_assigns_by_priority(app_db):
    with client(app_db) as c:
        h = login(c)
        src = next(s for s in c.get("/api/sources", headers=h).json() if s["code"] == "sim-centro")
        c.patch(f"/api/sources/{src['id']}", headers=h, json={"config": {"error_rate": 0}})
        c.post(f"/api/sources/{src['id']}/consultar", headers=h)
        for q in c.get("/api/requests?region=CENTRO", headers=h).json()["items"]:  # aislar la competencia
            c.post(f"/api/requests/{q['id']}/cancelar", headers=h)
        item = next(i for i in c.get("/api/lab/items", headers=h).json() if i["region"] == "CENTRO")
        r = c.post("/api/lab/competencia", headers=h, json={"item_id": item["id"], "requests": 20, "units": 2}).json()
        time.sleep(1.1)
        c.post(f"/api/sources/{src['id']}/consultar", headers=h)
        _drain(c)
        reqs = c.get(f"/api/requests?q={r['grupo']}", headers=h).json()["items"]
        assigned = [x for x in reqs if x["status"] == "ASIGNADO"]
        assert len(assigned) == 2
        assert all(x["priority"] == 1 for x in assigned)
        assert c.get("/api/metrics", headers=h).json()["integridad"] == []


def test_v20_bd_caida_responde_503_explicito_sin_cortar_conexion(app_db, monkeypatch):
    """Resistencia 2.0: con la base caída, una petición autenticada (POST con cuerpo) debe recibir
    503 BD_NO_DISPONIBLE. Antes la excepción del driver escapaba del middleware y el cliente veía
    «Connection reset by peer» sin respuesta (hallado con scripts/resistencia_v20.py contra PG real)."""
    from sqlalchemy.exc import OperationalError

    from monitoreo import security

    with client(app_db) as c:
        h = login(c)

        def caida(*a, **k):
            raise OperationalError("SELECT 1", {}, Exception("connection is bad: socket no existe"))

        monkeypatch.setattr(security, "resolve_token", caida)
        import monitoreo.api.deps as deps
        monkeypatch.setattr(deps, "resolve_token", caida)
        for metodo, ruta in (("GET", "/api/auth/me"), ("POST", "/api/reservaciones/1/confirmar")):
            r = c.request(metodo, ruta, headers=h, json={"confirmo": True} if metodo == "POST" else None)
            assert r.status_code == 503, (ruta, r.status_code, r.text)
            assert r.json()["codigo"] == "BD_NO_DISPONIBLE" and r.headers["Retry-After"] == "5"
            assert "socket" not in r.text and "SELECT" not in r.text   # sin detalles internos


def test_v20_health_da_503_si_la_base_no_responde(app_db, monkeypatch):
    with client(app_db) as c:
        assert c.get("/api/health").status_code == 200
        from sqlalchemy.exc import OperationalError
        db = c.app.state.db

        class Rota:
            def __enter__(self):
                raise OperationalError("SELECT 1", {}, Exception("sin socket"))

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(db, "session", lambda: Rota())
        r = c.get("/api/health")
        assert r.status_code == 503 and r.json()["ok"] is False and r.json()["base_de_datos"] == "SIN_CONEXION"
