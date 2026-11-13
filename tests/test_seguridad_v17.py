"""v1.7 (auditoría de seguridad y privacidad): un operador intenta leer o tocar lo de otro operador
por TODAS las rutas que aceptan un identificador o devuelven listas. Datos 100 % ficticios."""
import json

from sqlalchemy import func, select

from monitoreo.models import Alert, Request

from .test_api import app_db, client, login  # noqa: F401  (fixture)
from .test_asignaciones_abandonadas import _assigned

TITULAR = "Titular Ficticio Privado"


def _escenario(c):
    """X tiene una asignación (con alerta) de una solicitud con titular; Y es otro operador."""
    h, ho, aid, rid = _assigned(c)
    db = c.app.state.db
    with db.session() as s:
        s.get(Request, rid).holder = TITULAR
    c.post("/api/users", headers=h, json={"username": "opy", "password": "Operador-456", "role": "operador",
                                          "regions": ["OESTE"], "operator_name": "Operador Y"})
    hy = login(c, "opy", "Operador-456")
    with db.session() as s:
        alerta = s.scalar(select(Alert.id).where(Alert.assignment_id == aid))
    assert alerta
    return h, ho, hy, aid, rid, alerta


def test_operador_no_ve_alertas_de_asignaciones_ajenas(app_db):
    with client(app_db) as c:
        h, ho, hy, aid, rid, alerta = _escenario(c)
        assert all(a["assignment_id"] != aid for a in c.get("/api/alerts", headers=hy).json())
        assert c.get(f"/api/alerts/{alerta}", headers=hy).status_code == 404
        assert c.post(f"/api/alerts/{alerta}/confirmar", headers=hy).status_code == 404
        assert c.get(f"/api/alerts/{alerta}", headers=h).json()["acknowledged_at"] is None   # no la confirmó Y
        # el dueño y el administrador sí la ven
        assert any(a["assignment_id"] == aid for a in c.get("/api/alerts", headers=ho).json())
        assert c.get(f"/api/alerts/{alerta}", headers=ho).status_code == 200
        assert any(a["assignment_id"] == aid for a in c.get("/api/alerts", headers=h).json())
        n = c.get("/api/notificaciones", headers=hy).json()
        assert all(a["assignment_id"] != aid for a in n["items"])


def test_operador_no_ve_solicitudes_ajenas(app_db):
    with client(app_db) as c:
        h, ho, hy, aid, rid, alerta = _escenario(c)
        lst = c.get("/api/requests", headers=hy).json()
        assert all(r["id"] != rid for r in lst["items"])
        assert c.get(f"/api/requests?q={TITULAR[:8]}", headers=hy).json()["total"] == 0
        assert c.get(f"/api/requests/{rid}", headers=hy).status_code == 404
        assert c.get(f"/api/requests/{rid}", headers=ho).status_code == 200      # la tiene asignada
        assert c.get(f"/api/requests/{rid}", headers=h).status_code == 200
        # lo que Y registra, Y lo ve
        own = c.post("/api/requests", headers=hy, json={"code": "PROPIA-Y", "holder": "Ficticio", "region": "OESTE",
                                                        "resource_type": "turno"}).json()
        assert c.get(f"/api/requests/{own['id']}", headers=hy).status_code == 200
        assert any(r["id"] == own["id"] for r in c.get("/api/requests", headers=hy).json()["items"])


def test_alertas_no_llevan_el_nombre_del_titular(app_db):
    """Las alertas se guardan y salen a canales externos (webhook): antes llevaban el nombre completo."""
    with client(app_db) as c:
        h, ho, aid, rid = _assigned(c)
        db = c.app.state.db
        with db.session() as s:
            req = s.get(Request, rid)
            textos = [json.dumps(a.payload, ensure_ascii=False) + a.title for a in s.scalars(select(Alert))]
        assert req.holder and all(req.holder not in t for t in textos)
        with db.session() as s:
            a = s.scalar(select(Alert).where(Alert.assignment_id == aid))
            iniciales = " ".join(x[0].upper() + "." for x in req.holder.split() if x[:1].isalpha())
            assert a.payload["titular"] == iniciales            # la clave se conserva (compatibilidad)


# ----------------------------------------------------------------- centro de notificaciones
def test_categorias_silencio_y_agrupacion(db):
    from datetime import datetime, timedelta

    from monitoreo.engine import alerts as al
    from monitoreo.engine.params import get_params, set_params
    from monitoreo.models import Delivery
    now = datetime(2026, 6, 1, 12)

    def crear(key, kind="SISTEMA", payload=None, t=now, **kw):
        with db.session() as s:
            a = al.create_alert(s, dedup_key=key, kind=kind, title="Prueba", source_code="src-1", region="NORTE",
                                resource="r", now=t, payload=payload, **kw)
            return None if a is None else (a.id, a.categoria, a.nivel, a.status)

    assert crear("d1", "DISPONIBILIDAD", {"cambio": "NUEVA_DISPONIBILIDAD"})[1:3] == ("NUEVA_DISPONIBILIDAD", "MEDIA")
    assert crear("d2", "DISPONIBILIDAD", {"cambio": "AGOTADO"})[1:3] == ("DISPONIBILIDAD_AGOTADA", "BAJA")
    assert crear("s1", payload={"evento_sistema": "asignacion_inactiva"})[1] == "OPERACION_ABANDONADA"
    # agrupación: la misma causa repetida dentro de la ventana suma repeticiones y no crea entregas nuevas
    e1 = crear("e1", payload={"evento_sistema": "circuito"})
    assert e1[1:3] == ("ERROR_COMUNICACION", "ALTA")
    assert crear("e2", payload={"evento_sistema": "circuito"}, t=now + timedelta(seconds=60)) is None
    assert crear("e3", payload={"evento_sistema": "circuito"}, t=now + timedelta(seconds=120)) is None
    with db.session() as s:
        a = s.get(Alert, e1[0])
        assert a.repeticiones == 3 and a.ultima_en == now + timedelta(seconds=120)
        n_ent = s.scalar(select(func.count()).select_from(Delivery).where(Delivery.alert_id == e1[0]))
    assert crear("e4", payload={"evento_sistema": "circuito"}, t=now + timedelta(seconds=1000)) is not None  # ventana vencida
    # silencio: solo categorías no críticas; la alerta queda en el historial SIN entregas
    with db.session() as s:
        try:
            set_params(s, {"alertas_silenciadas": ["PROBLEMA_MOTOR"]})
            raise AssertionError("una categoría crítica no debe poder silenciarse")
        except ValueError:
            pass
    with db.session() as s:
        set_params(s, {"alertas_silenciadas": ["NUEVA_DISPONIBILIDAD"]})
        assert get_params(s)["alertas_silenciadas"] == ["NUEVA_DISPONIBILIDAD"]
    sil = crear("d3", "DISPONIBILIDAD", {"cambio": "NUEVA_DISPONIBILIDAD"})
    crit = crear("m1", payload={"evento_sistema": "autodiagnostico"})
    with db.session() as s:
        assert sil[3] == "SILENCIADA"
        assert s.scalar(select(func.count()).select_from(Delivery).where(Delivery.alert_id == sil[0])) == 0
        assert crit[1:3] == ("PROBLEMA_MOTOR", "CRITICA") and crit[3] == "PENDIENTE"
        assert s.scalar(select(func.count()).select_from(Delivery).where(Delivery.alert_id == crit[0])) == n_ent


def test_centro_de_notificaciones_api(app_db):
    with client(app_db) as c:
        h, ho, hy, aid, rid, alerta = _escenario(c)
        d = c.get("/api/notificaciones", headers=h).json()
        assert len(d["categorias"]) == 10
        assert d["resumen"]["EXPEDIENTE_ASIGNADO"]["total"] >= 1
        it = next(a for a in d["items"] if a["id"] == alerta)
        assert it["categoria"] == "EXPEDIENTE_ASIGNADO" and it["nivel"] == "ALTA" and it["categoria_nombre"]
        assert isinstance(it["entregas"], dict) and d["canales"]                 # indicadores de entrega
        solo = c.get("/api/notificaciones?categoria=EXPEDIENTE_ASIGNADO", headers=h).json()["items"]
        assert solo and all(a["categoria"] == "EXPEDIENTE_ASIGNADO" for a in solo)
        assert c.get("/api/notificaciones?nivel=CRITICA&categoria=EXPEDIENTE_ASIGNADO", headers=h).json()["items"] == []
        # confirmación en bloque: Y no puede confirmar la de X; X sí
        r = c.post("/api/notificaciones/confirmar", headers=hy, json={"ids": [alerta]}).json()
        assert r == {"confirmadas": 0, "ignoradas": 1}
        assert c.post("/api/notificaciones/confirmar", headers=ho, json={"ids": [alerta]}).json()["confirmadas"] == 1
        assert c.post("/api/notificaciones/confirmar", headers=ho, json={"ids": [alerta]}).json()["confirmadas"] == 0
        assert c.get("/api/notificaciones?sin_confirmar=true&categoria=EXPEDIENTE_ASIGNADO",
                     headers=h).json()["items"] == []
        assert c.get("/api/notificaciones", headers=hy).json()["canales"] == []   # el operador no ve canales
