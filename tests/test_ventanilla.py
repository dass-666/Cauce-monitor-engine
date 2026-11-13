"""v2.0 · Modo ventanilla (atención presencial) contra el portal de PRUEBA como proceso (red real
en 127.0.0.1), en SQLite y PostgreSQL. Cubre los escenarios de la misión 2.0 que dependen de la
ventanilla: seis atenciones seguidas con folio verificado, duplicados, carreras con el motor y entre
dos operadores, sin disponibilidad, confirmación incierta que se resuelve sola, retomar tras un
reinicio, idempotencia y privacidad."""
from __future__ import annotations

import json
import threading
import uuid

import pytest
from sqlalchemy import select, text

from monitoreo.captura import expedientes as ex
from monitoreo.captura import seguimiento
from monitoreo.captura.formularios.navegador import NavegadorHTTP, ResultadoIncierto
from monitoreo.config import settings
from monitoreo.db import Database
from monitoreo.engine.pipeline import maintenance, poll_source, process_pending_events
from monitoreo.models import Assignment, Atencion, Expediente, Request, SlotState, Source
from portal_reservas.proceso import PortalProceso

from .captura_util import ADMIN_PWD, MANUALES, LabDia
from .conftest import BACKENDS, PG_URL


@pytest.fixture(scope="module")
def portal(tmp_path_factory):
    d = tmp_path_factory.mktemp("portal")
    p = PortalProceso(d / "estado.json", extra=["--zona-horaria", "America/Mexico_City"])
    p.iniciar()
    yield p
    p.detener()


@pytest.fixture(params=BACKENDS)
def lab(request, portal, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "admin_password", ADMIN_PWD)
    monkeypatch.setenv("MONITOREO_PORTAL_URL", portal.base)
    monkeypatch.setenv("MONITOREO_PORTAL_TOKEN", portal.token)
    portal.fallos(reset=True)
    portal.ctl("POST", "/control/api", caida=False)
    if request.param == "postgres":
        Database(PG_URL).reset_for_tests()
        url = PG_URL
    else:
        url = f"sqlite:///{(tmp_path / 'v.db').as_posix()}"
    lb = LabDia(url, portal)
    yield lb
    portal.fallos(reset=True)
    lb.cerrar()
    if request.param == "postgres":
        Database(PG_URL).reset_for_tests()


def iniciar(lab, h, clave=None):
    r = lab.c.post("/api/ventanilla/atenciones", headers=h, json={"clave": clave or uuid.uuid4().hex})
    assert r.status_code in (200, 201), r.text
    return r.json()


def con_expediente(lab, h, datos=None, **kw):
    a = iniciar(lab, h)
    r = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/expediente", headers=h,
                   json={"datos": datos or lab.datos_expediente(**kw)})
    assert r.status_code == 200, r.text
    return r.json()


def atender_hasta_folio(lab, h, **kw):
    a = con_expediente(lab, h, **kw)
    eid = a["expediente"]["id"]
    assert a["estado"] == "EN_PROCESO" and a["siguiente"]["clave"] == "preparar"
    rid, dia = lab.hasta_fecha(eid, h)
    r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
    assert r.status_code == 200, r.text
    v = lab.c.get(f"/api/ventanilla/atenciones/{a['id']}", headers=h).json()
    assert v["siguiente"]["clave"] == "cerrar"
    r = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/cerrar", headers=h, json={})
    assert r.status_code == 200, r.text
    return r.json(), eid


def folio_en_portal(lab, folio):
    return lab.portal.http.get(lab.portal.base + f"/api/reservaciones/{folio}",
                               headers={"Authorization": f"Bearer {lab.portal.token}"}).json()


def solicitud(lab, eid):
    with lab.db.session() as s:
        e = s.get(Expediente, eid)
        return s.get(Request, e.request_id).status, e.estado, e.operator_id


# ---------------------------------------------------------------- escenario 1: seis atenciones seguidas
def test_seis_atenciones_consecutivas_con_folio_verificado_y_meta(lab):
    h = lab.ops["op1"]
    folios = []
    for _ in range(6):
        a, eid = atender_hasta_folio(lab, h)
        assert a["estado"] == "CONFIRMADA" and a["folio"] and not a["abierta"]
        assert folio_en_portal(lab, a["folio"])["estado"] == "CONFIRMADA"       # evidencia independiente
        assert solicitud(lab, eid)[:2] == ("COMPLETADO", "RESERVACION_CONFIRMADA")
        folios.append(a["folio"])
    assert len(set(folios)) == 6
    d = lab.c.get("/api/ventanilla", headers=h).json()
    assert d["meta_citas_dia"] == 6 and d["citas_confirmadas_hoy"] == 6 and d["progreso_pct"] == 100
    assert d["mias"]["con_cita"] == 6 and d["mias"]["sin_cita"] == 0 and d["mias"]["tiempo_medio_s"] is not None
    assert d["abiertas"] == []
    # el motor no asignó ninguna de esas solicitudes a otro operador
    lab.ciclo()
    with lab.db.session() as s:
        assert s.scalar(select(Assignment.id).limit(1)) is None
        assert ex.coherencia(s) == []


# ---------------------------------------------------------------- idempotencia, retomar, aislamiento
def test_inicio_idempotente_y_retomar_tras_reinicio(lab):
    h = lab.ops["op1"]
    clave = uuid.uuid4().hex
    a1 = lab.c.post("/api/ventanilla/atenciones", headers=h, json={"clave": clave})
    a2 = lab.c.post("/api/ventanilla/atenciones", headers=h, json={"clave": clave})
    assert a1.status_code == 201 and a2.status_code == 200 and a1.json()["id"] == a2.json()["id"]
    aid = a1.json()["id"]
    r = lab.c.post(f"/api/ventanilla/atenciones/{aid}/expediente", headers=h, json={"datos": lab.datos_expediente()})
    eid = r.json()["expediente"]["id"]
    rid = lab.c.post(f"/api/expedientes/{eid}/preparar", headers=h).json()["reservacion"]["id"]
    r = lab.c.post(f"/api/reservaciones/{rid}/enviar-datos", headers=h, json={"revisado": True, "manuales": MANUALES})
    assert r.status_code == 200
    # "reinicio": otro proceso del motor sobre la misma base; el operador vuelve a entrar
    url = lab.db.url
    lab.cerrar()
    from fastapi.testclient import TestClient
    from monitoreo.app import create_app
    c2 = TestClient(create_app(url, start_engine=False, seed_demo=False))
    c2.__enter__()
    try:
        tok = c2.post("/api/auth/login", json={"username": "op1", "password": "Operador-123"}).json()["token"]
        h2 = {"Authorization": f"Bearer {tok}"}
        d = c2.get("/api/ventanilla", headers=h2).json()
        assert [x["id"] for x in d["abiertas"]] == [aid]
        ab = d["abiertas"][0]
        assert ab["estado"] == "EN_PROCESO" and ab["reservacion"]["estado"] == "DATOS_ENVIADOS"
        assert ab["siguiente"]["clave"] == "fecha"            # se retoma en el paso exacto
        seguimiento.mantenimiento(c2.app.state.db, lab.clock.now())     # el motor no se la quita
        with c2.app.state.db.session() as s:
            assert s.get(Expediente, eid).estado == "CAPTURA_EN_PROCESO"
    finally:
        c2.__exit__(None, None, None)
        lab.c.__enter__()


def test_otro_operador_no_ve_ni_cierra_la_atencion(lab):
    a = con_expediente(lab, lab.ops["op1"])
    for metodo, ruta, body in (("GET", "", None), ("POST", "/cerrar", {"resultado": "ERROR"}),
                               ("POST", "/buscar", {"codigo": a["expediente"]["codigo"]})):
        r = lab.c.request(metodo, f"/api/ventanilla/atenciones/{a['id']}{ruta}", headers=lab.ops["op2"], json=body)
        assert r.status_code == 404, (ruta, r.status_code)
    assert lab.c.get(f"/api/expedientes/{a['expediente']['id']}", headers=lab.ops["op2"]).status_code == 404


# ---------------------------------------------------------------- duplicados
def test_duplicado_se_detecta_y_se_recupera_el_existente(lab):
    h = lab.ops["op1"]
    datos = lab.datos_expediente()
    lab.crear(**{k: v for k, v in datos.items() if k != "region"})          # ya existía (alta previa)
    a = iniciar(lab, h)
    r = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/expediente", headers=h, json={"datos": datos})
    assert r.status_code == 409
    dup = r.json()["detail"]["duplicado"]
    assert len(dup) == 1 and dup[0]["puede_atenderse"] and "•" in dup[0]["titular"]
    assert datos["nombre"] not in json.dumps(dup)                          # enmascarado
    r = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/expediente", headers=h, json={"expediente_id": dup[0]["id"]})
    assert r.status_code == 200 and r.json()["expediente"]["codigo"] == dup[0]["codigo"]
    with lab.db.session() as s:
        assert s.scalar(select(text("count(*)")).select_from(Expediente)) == 1


def test_persona_con_cita_confirmada_no_se_atiende_dos_veces(lab):
    h = lab.ops["op1"]
    a, eid = atender_hasta_folio(lab, h)
    with lab.db.session() as s:
        codigo = s.get(Expediente, eid).codigo
    b = iniciar(lab, h)
    r = lab.c.post(f"/api/ventanilla/atenciones/{b['id']}/buscar", headers=h, json={"codigo": codigo})
    x = r.json()["expedientes"][0]
    assert not x["puede_atenderse"] and a["folio"] in x["motivo"]
    r = lab.c.post(f"/api/ventanilla/atenciones/{b['id']}/expediente", headers=h, json={"expediente_id": eid})
    assert r.status_code == 409


def test_busqueda_solo_exacta(lab):
    h = lab.ops["op1"]
    a = iniciar(lab, h)
    r = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/buscar", headers=h, json={"nombre": "Persona"})
    assert r.status_code == 422            # no se puede recorrer la base con búsquedas parciales


# ---------------------------------------------------------------- carreras
def test_el_motor_no_asigna_lo_que_se_atiende_en_ventanilla(lab):
    h = lab.ops["op1"]
    a = con_expediente(lab, h)
    eid = a["expediente"]["id"]
    assert solicitud(lab, eid)[:2] == ("EN_VENTANILLA", "ASIGNADO")
    for _ in range(2):
        lab.ciclo()
    with lab.db.session() as s:
        assert s.scalar(select(Assignment.id).where(Assignment.request_id == s.get(Expediente, eid).request_id)) is None
        assert s.get(Expediente, eid).estado == "ASIGNADO"


def test_si_el_motor_se_adelanta_la_ventanilla_recibe_409(lab):
    e = lab.crear()
    lab.ciclo()                                       # el motor lo asigna a op1 u op2
    dueno = lab.operador_de(e["id"])
    otro = "op2" if dueno == "op1" else "op1"
    a = iniciar(lab, lab.ops[otro])
    r = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/expediente", headers=lab.ops[otro],
                   json={"expediente_id": e["id"]})
    assert r.status_code == 409 and "otro operador" in r.json()["detail"]["mensaje"]
    # el dueño sí puede atenderlo en ventanilla (ya era suyo): sigue su asignación del motor
    a = iniciar(lab, lab.ops[dueno])
    r = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/expediente", headers=lab.ops[dueno],
                   json={"expediente_id": e["id"]})
    assert r.status_code == 200 and solicitud(lab, e["id"])[0] == "ASIGNADO"


def test_motor_asigna_al_mismo_operador_antes_del_seguimiento(lab):
    """Defecto encontrado por la demo en navegador (v2.0): el motor asigna la solicitud recién creada
    al MISMO operador que está atendiendo a la persona, antes de que el seguimiento marque el
    expediente como ASIGNADO. La ventanilla respondía 409 «el motor acaba de asignar» y la persona
    se quedaba sin atender (y el expediente creado, sin atención)."""
    e = lab.crear()
    maintenance(lab.db, lab.clock)                       # valida la solicitud (PENDIENTE -> ELEGIBLE)
    with lab.db.session() as s:
        s.get(Source, lab.source_id).next_poll_at = None
    poll_source(lab.db, lab.source_id, lab.clock, lab.runtime, lab.rng)
    process_pending_events(lab.db, lab.clock)            # asignado por el motor; SIN seguimiento todavía
    st, est, _ = solicitud(lab, e["id"])
    assert st == "ASIGNADO" and est == "LISTO"
    with lab.db.session() as s:
        from monitoreo.models import Operator, User
        op_id = s.get(Request, s.get(Expediente, e["id"]).request_id).operator_id
        dueno = s.get(User, s.get(Operator, op_id).user_id).username
    otro = "op2" if dueno == "op1" else "op1"
    a = iniciar(lab, lab.ops[otro])                     # otro operador: sigue sin poder quitárselo
    r = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/expediente", headers=lab.ops[otro],
                   json={"expediente_id": e["id"]})
    assert r.status_code == 409
    a = iniciar(lab, lab.ops[dueno])
    r = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/expediente", headers=lab.ops[dueno],
                   json={"expediente_id": e["id"]})
    assert r.status_code == 200, r.text
    st, est, op = solicitud(lab, e["id"])
    assert (st, est) == ("EN_VENTANILLA", "ASIGNADO")
    with lab.db.session() as s:
        asg_ = s.scalars(select(Assignment).where(Assignment.request_id == s.get(Expediente, e["id"]).request_id)).all()
        assert [x.status for x in asg_] == ["CANCELADA"] and asg_[0].live_request_id is None
        assert s.get(SlotState, asg_[0].slot_id).held == 0                      # el cupo retenido se libera
    lab.ciclo()
    assert solicitud(lab, e["id"])[:2] == ("EN_VENTANILLA", "ASIGNADO")        # el seguimiento lo respeta


def test_dos_operadores_toman_el_mismo_expediente_a_la_vez(lab):
    e = lab.crear()
    ats = {u: iniciar(lab, lab.ops[u]) for u in ("op1", "op2")}
    codigos = {}
    barrera = threading.Barrier(2)

    def tomar(u):
        barrera.wait()
        r = lab.c.post(f"/api/ventanilla/atenciones/{ats[u]['id']}/expediente", headers=lab.ops[u],
                       json={"expediente_id": e["id"]})
        codigos[u] = r.status_code
    hs = [threading.Thread(target=tomar, args=(u,)) for u in ats]
    for t in hs:
        t.start()
    for t in hs:
        t.join()
    assert sorted(codigos.values()) == [200, 409], codigos
    with lab.db.session() as s:
        assert len(list(s.scalars(select(Atencion).where(Atencion.expediente_id == e["id"])))) == 1


# ---------------------------------------------------------------- sin cita
def test_sin_disponibilidad_devuelve_a_la_cola_y_el_motor_sigue(lab):
    h = lab.ops["op1"]
    a = con_expediente(lab, h)
    eid = a["expediente"]["id"]
    rid = lab.c.post(f"/api/expedientes/{eid}/preparar", headers=h).json()["reservacion"]["id"]
    # sin resultado no se cierra: no hay cita y hay que decir qué pasó
    r = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/cerrar", headers=h, json={})
    assert r.status_code == 422
    r = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/cerrar", headers=h,
                   json={"resultado": "SIN_DISPONIBILIDAD", "motivo": "sin dias"})
    assert r.status_code == 200 and r.json()["estado"] == "SIN_DISPONIBILIDAD" and r.json()["folio"] is None
    assert r.json()["resultado"] == "SINDIAS"
    st, est, op = solicitud(lab, eid)
    assert (st, est, op) == ("ELEGIBLE", "LISTO", None)
    with lab.db.session() as s:
        from monitoreo.models import Reservacion
        assert s.get(Reservacion, rid).estado == "CANCELADA"
    lab.ciclo()                                       # el motor lo vuelve a vigilar y lo asigna
    assert solicitud(lab, eid)[1] == "ASIGNADO"
    d = lab.c.get("/api/ventanilla", headers=h).json()
    assert d["mias"]["sin_cita"] == 1 and d["mias"]["con_cita"] == 0 and d["citas_confirmadas_hoy"] == 0


def test_cancelada_cierra_el_expediente_si_se_pide(lab):
    h = lab.ops["op1"]
    a = con_expediente(lab, h)
    r = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/cerrar", headers=h,
                   json={"resultado": "CANCELADA", "cerrar_expediente": True})
    assert r.status_code == 200
    st, est, _ = solicitud(lab, a["expediente"]["id"])
    assert (st, est) == ("CANCELADO", "CERRADO")


def test_nunca_confirmada_sin_reservacion_confirmada(lab):
    h = lab.ops["op1"]
    a = con_expediente(lab, h)
    rid, _ = lab.hasta_fecha(a["expediente"]["id"], h)
    r = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/cerrar", headers=h, json={"resultado": "ERROR"})
    assert r.json()["estado"] == "ERROR" and r.json()["folio"] is None
    with lab.db.session() as s:
        assert s.scalar(select(text("count(*)")).select_from(Atencion).where(Atencion.estado == "CONFIRMADA")) == 0


# ---------------------------------------------------------------- escenario 15: confirmación incierta
def test_confirmacion_incierta_queda_pendiente_y_se_resuelve_sola(lab, monkeypatch):
    h = lab.ops["op1"]
    a = con_expediente(lab, h)
    eid = a["expediente"]["id"]
    rid, _ = lab.hasta_fecha(eid, h)
    lab.portal.fallos(modo="cortar_tras_confirmar", restantes=1)

    def caido(self, *a_, **kw):
        raise ResultadoIncierto("RED", "simulado: sin red al verificar")
    with monkeypatch.context() as m:
        m.setattr(NavegadorHTTP, "buscar_por_clave", caido)
        m.setattr(NavegadorHTTP, "verificar_folio", caido)
        r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
        assert r.status_code == 202
        r = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/cerrar", headers=h, json={"resultado": "ERROR"})
        v = r.json()
        assert v["estado"] == "PENDIENTE" and v["resultado"] == "VERIFICACION" and v["folio"] is None
        assert v["siguiente"]["clave"] == "verificacion"
        seguimiento.mantenimiento(lab.db, lab.clock.now())          # sin red: no se decide nada
        assert lab.c.get(f"/api/ventanilla/atenciones/{a['id']}", headers=h).json()["estado"] == "PENDIENTE"
    out = seguimiento.mantenimiento(lab.db, lab.clock.now())          # vuelve la red
    assert out["ventanilla"]["confirmadas"] == 1
    v = lab.c.get(f"/api/ventanilla/atenciones/{a['id']}", headers=h).json()
    assert v["estado"] == "CONFIRMADA" and v["folio"] and folio_en_portal(lab, v["folio"])["estado"] == "CONFIRMADA"
    seguimiento.mantenimiento(lab.db, lab.clock.now())
    assert solicitud(lab, eid)[0] == "COMPLETADO"


# ---------------------------------------------------------------- región y privacidad
def test_operador_no_atiende_otra_region(lab):
    a = iniciar(lab, lab.ops["op1"])          # op1 es de NORTE
    r = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/expediente", headers=lab.ops["op1"],
                   json={"datos": lab.datos_expediente(region="SUR", sucursal="SUR-1", sucursales_alternativas=[])})
    assert r.status_code == 409 and "región" in r.json()["detail"]["mensaje"]


def test_sin_datos_personales_en_atenciones_ni_auditoria(lab):
    h = lab.ops["op1"]
    datos = lab.datos_expediente()
    a, _ = atender_hasta_folio(lab, h, **{k: v for k, v in datos.items() if k not in ("region",)})
    with lab.db.session() as s:
        blob = json.dumps([[str(v) for v in row] for t in ("atenciones", "audit_log", "expediente_eventos")
                           for row in s.execute(text(f"SELECT * FROM {t}"))])
    for valor in (datos["nombre"], datos["telefono"], datos["correo"], MANUALES["clave_identidad"]):
        assert valor not in blob, valor


def test_meta_configurable(lab):
    r = lab.c.put("/api/settings/engine", headers=lab.h, json={"meta_citas_dia": 10})
    assert r.status_code == 200, r.text
    assert lab.c.get("/api/ventanilla", headers=lab.ops["op1"]).json()["meta_citas_dia"] == 10
    assert lab.c.put("/api/settings/engine", headers=lab.h, json={"meta_citas_dia": 0}).status_code == 422


def test_supervision_ve_la_ventanilla_y_la_redistribucion_no_la_toca(lab):
    """v2.0 + supervisión agrupada (1.7): una atención presencial abierta (solicitud EN_VENTANILLA,
    expediente ASIGNADO, sin asignación del motor) aparece en la supervisión del operador y la
    redistribución NO la libera: la persona está delante de ese operador."""
    h = lab.ops["op1"]
    a = con_expediente(lab, h)
    eid = a["expediente"]["id"]
    with lab.db.session() as s:
        oid = s.get(Expediente, eid).operator_id
    otro = lab.crear()
    lab.ciclo()                                           # el motor asigna OTRO expediente (puede ser a op1)
    sup = {o["id"]: o for o in lab.c.get("/api/supervision", headers=lab.h).json()["operadores"]}
    assert sup[oid]["ventanilla_abiertas"] == 1
    assert sup[oid]["expedientes"].get("ASIGNADO", 0) >= 1
    r = lab.c.post(f"/api/supervision/operadores/{oid}/redistribuir", headers=lab.h,
                   json={"confirmo": True, "motivo": "prueba ventanilla", "incluir_en_proceso": True}).json()
    assert any(x.get("atencion") == a["id"] and "Ventanilla" in x["motivo"] for x in r["no_liberadas"])
    assert solicitud(lab, eid) == ("EN_VENTANILLA", "ASIGNADO", oid)
    lab.ciclo()
    assert solicitud(lab, eid) == ("EN_VENTANILLA", "ASIGNADO", oid)      # el seguimiento tampoco la toca
    # la atención sigue y llega a su folio
    rid, _ = lab.hasta_fecha(eid, h)
    assert lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True}).status_code == 200
    v = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/cerrar", headers=h, json={}).json()
    assert v["estado"] == "CONFIRMADA" and v["folio"]
    sup = {o["id"]: o for o in lab.c.get("/api/supervision", headers=lab.h).json()["operadores"]}
    assert sup[oid]["ventanilla_abiertas"] == 0
    assert otro["id"] != eid
