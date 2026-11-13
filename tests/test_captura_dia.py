"""v1.6: flujo POR DÍA de punta a punta contra el portal de PRUEBA como proceso (red real en
127.0.0.1): expediente -> validación -> asignación por sucursal -> campos manuales -> calendario
diario -> día elegido -> confirmación humana -> folio con evidencia. Incluye los fallos pedidos:
día que se agota antes de confirmar, dos solicitudes por la última unidad, respuesta perdida,
reinicio a mitad, confirmación repetida, folio duplicado, sesión caducada y error del servidor."""
from __future__ import annotations

import json
import logging
import threading
import time

import pytest
from sqlalchemy import func, select

from monitoreo.captura import expedientes as ex
from monitoreo.captura import seguimiento
from monitoreo.captura.formularios.navegador import NavegadorHTTP, ResultadoIncierto
from monitoreo.config import settings
from monitoreo.db import Database
from monitoreo.engine.params import set_params
from monitoreo.models import (Alert, Assignment, AuditLog, Expediente, ExpedienteEvento, Operator, Request,
                              Reservacion)
from portal_reservas.proceso import PortalProceso

from .captura_util import ADMIN_PWD, MANUALES, LabDia, siguiente_registro
from .conftest import BACKENDS, PG_URL

API = None


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
        url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    lb = LabDia(url, portal)
    yield lb
    portal.fallos(reset=True)
    lb.cerrar()
    if request.param == "postgres":
        Database(PG_URL).reset_for_tests()


def asignado(lab, **kw):
    e = lab.crear(**kw)
    lab.ciclo()
    u = lab.operador_de(e["id"])
    return e["id"], u, lab.ops[u]


def api_portal(lab, ruta, **params):
    return lab.portal.http.get(lab.portal.base + ruta, params=params,
                               headers={"Authorization": f"Bearer {lab.portal.token}"}).json()


def estado(lab, eid):
    with lab.db.session() as s:
        return s.get(Expediente, eid).estado


def res_estado(lab, rid):
    with lab.db.session() as s:
        return s.get(Reservacion, rid).estado


# ---------------------------------------------------------------- recorrido completo
def test_recorrido_por_dia_hasta_folio_con_campos_manuales(lab, caplog):
    caplog.set_level(logging.DEBUG)
    eid, u, h = asignado(lab)
    assert estado(lab, eid) == "ASIGNADO"
    r = lab.c.post(f"/api/expedientes/{eid}/preparar", headers=h)
    rev = r.json()["revision"]
    manual = {c["campo"]: c for c in rev["campos"] if c["manual"]}
    assert set(manual) == {"clave_identidad", "acepto_privacidad"}
    assert manual["clave_identidad"]["sin_pegar"] and manual["clave_identidad"]["instrucciones"]
    assert manual["clave_identidad"]["valor"] is None                  # el motor nunca lo llena
    assert rev["sucursales_permitidas"][:1] == ["NOR-1"] and "NOR-2" in rev["sucursales_permitidas"]
    rid = r.json()["reservacion"]["id"]
    # sin lo manual: no se llama al portal, se dice qué falta
    r = lab.c.post(f"/api/reservaciones/{rid}/enviar-datos", headers=h, json={"revisado": True})
    assert r.status_code == 422 and set(r.json()["detail"]["errores"]) == {"clave_identidad", "acepto_privacidad"}
    # un campo mal y otro bien: SOLO se marca el que falla (el otro se conserva en el formulario)
    r = lab.c.post(f"/api/reservaciones/{rid}/enviar-datos", headers=h,
                   json={"revisado": True, "manuales": {"clave_identidad": "12", "acepto_privacidad": "si"}})
    assert r.status_code == 422 and list(r.json()["detail"]["errores"]) == ["clave_identidad"]
    # no se puede "colar" un campo automático como manual
    r = lab.c.post(f"/api/reservaciones/{rid}/enviar-datos", headers=h,
                   json={"revisado": True, "manuales": {**MANUALES, "telefono": "5550000000"}})
    assert r.status_code == 422 and "telefono" in r.json()["detail"]["errores"]
    # bien (en minúsculas: el contrato lo normaliza a mayúsculas)
    r = lab.c.post(f"/api/reservaciones/{rid}/enviar-datos", headers=h,
                   json={"revisado": True, "manuales": {**MANUALES, "clave_identidad": MANUALES["clave_identidad"].lower()}})
    assert r.status_code == 200, r.text
    cal = r.json()["calendario"]
    assert cal["sucursal"] == "NOR-1" and cal["dias"] and all("hora" not in d for d in cal["dias"])
    assert {d["estado"] for d in cal["dias"]} <= {"DISPONIBLE", "AGOTADO", "DESHABILITADO", "NO_HABILITADO"}
    dia = next(d for d in cal["dias"] if d["estado"] == "DISPONIBLE")
    # una sucursal fuera de las permitidas se rechaza
    r = lab.c.get(f"/api/reservaciones/{rid}/calendario", headers=h, params={"sucursal": "SUR-1"})
    assert r.status_code == 403
    r = lab.c.post(f"/api/reservaciones/{rid}/seleccionar-dia", headers=h, json={"fecha": dia["fecha"]})
    assert r.status_code == 200 and r.json()["reservacion"]["estado"] == "FECHA_ELEGIDA"
    assert estado(lab, eid) == "RESERVACION_PENDIENTE"
    # sin la marca expresa del operador no se confirma
    assert lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={}).status_code == 422
    r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
    assert r.status_code == 200, r.text
    res = r.json()["reservacion"]
    assert res["estado"] == "CONFIRMADA" and res["folio"].startswith("DIA-") and res["hora"] is None
    assert res["fecha"] == dia["fecha"] and res["sucursal"] == "NOR-1"
    assert res["evidencia"]["clave_coincide"] and res["evidencia"]["fuente"] in ("api_por_folio", "api_por_clave")
    assert api_portal(lab, f"/api/reservaciones/{res['folio']}")["estado"] == "CONFIRMADA"
    ficha = lab.c.get(f"/api/expedientes/{eid}", headers=h).json()
    assert ficha["estado"] == "RESERVACION_CONFIRMADA"
    tipos = [x["tipo"] for x in ficha["historial"]]
    for t in ("CREADO", "SOLICITUD_CREADA", "DISPONIBILIDAD_DETECTADA", "FORMULARIO_PREPARADO", "DATOS_ENVIADOS",
              "RECURSO_SELECCIONADO", "RESERVACION_PENDIENTE", "RESERVACION_CONFIRMADA"):
        assert t in tipos, t
    estados = [(x["detalle"]["antes"], x["detalle"]["despues"]) for x in ficha["historial"] if x["tipo"] == "ESTADO"]
    assert estados == [("NUEVO", "EN_VALIDACION"), ("EN_VALIDACION", "LISTO"), ("LISTO", "ASIGNADO"),
                       ("ASIGNADO", "CAPTURA_EN_PROCESO"), ("CAPTURA_EN_PROCESO", "RESERVACION_PENDIENTE"),
                       ("RESERVACION_PENDIENTE", "RESERVACION_CONFIRMADA")]
    # tres identificadores distintos y sin mezclar
    assert ficha["codigo"].startswith("EXP-") and ficha["id_registro_previo"] and res["folio"] != ficha["codigo"]
    # la clave de identidad tecleada NO está en ninguna tabla, ni en el historial, ni en los registros
    clave = MANUALES["clave_identidad"]
    with lab.db.session() as s:
        blob = json.dumps([[str(v) for v in row] for t in ("expedientes", "expediente_eventos", "reservaciones",
                                                            "audit_log", "alerts", "requests")
                           for row in s.execute(__import__("sqlalchemy").text(f"SELECT * FROM {t}"))])
    assert clave not in blob and clave.lower() not in blob
    assert clave not in caplog.text and clave.lower() not in caplog.text
    with lab.db.session() as s:
        assert ex.coherencia(s) == []


def test_incompleto_no_se_asigna_y_sucursal_alternativa(lab):
    inc = lab.crear(telefono=None, telefono_confirmacion=None)
    assert inc["estado"] == "INCOMPLETO" and "telefono" in inc["faltan"]
    # la preferida (NOR-1) sin cupo para su trámite: se asigna por la ALTERNATIVA (NOR-2)
    e = lab.crear(sucursal="NOR-1", sucursales_alternativas=["NOR-2"])
    tramite = e["tipo_tramite"]
    dias = lab.portal.ctl("GET", "/control/dias")
    agotados = []
    for k, x in dias["dias"].items():
        if x["sucursal"] == "NOR-1" and x["tramite"] == tramite and x["estado"] == "DISPONIBLE":
            lab.portal.ctl("POST", "/control/dias/agotar", clave=k)
            agotados.append(k)
    try:
        lab.ciclo()
        with lab.db.session() as s:
            assert s.get(Expediente, inc["id"]).estado == "INCOMPLETO" and s.get(Expediente, inc["id"]).operator_id is None
            exp = s.get(Expediente, e["id"])
            assert exp.estado == "ASIGNADO"
            a = s.scalar(select(Assignment).where(Assignment.request_id == exp.request_id))
            from monitoreo.models import SlotState
            assert s.get(SlotState, a.slot_id).attributes["sucursal"] == "NOR-2"
            assert not s.scalars(select(Assignment).join(Request, Request.id == Assignment.request_id)
                                 .where(Request.holder == inc["codigo"])).all()
        u = lab.operador_de(e["id"])
        r = lab.c.post(f"/api/expedientes/{e['id']}/preparar", headers=lab.ops[u])
        assert r.json()["revision"]["sucursales_permitidas"][0] == "NOR-2"   # la del recurso asignado primero
    finally:
        for k in agotados:
            lab.portal.ctl("POST", "/control/dias/liberar", clave=k, unidades=1)


# ---------------------------------------------------------------- confirmación segura
def test_dia_que_se_agota_antes_de_confirmar(lab):
    eid, u, h = asignado(lab)
    rid, dia = lab.hasta_fecha(eid, h)
    k = f"{dia['sucursal']}|{lab.c.get(f'/api/expedientes/{eid}', headers=h).json()['tipo_tramite']}|{dia['fecha']}"
    lab.portal.ctl("POST", "/control/dias/agotar", clave=k)
    try:
        r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
        assert r.status_code == 409 and r.json()["detail"]["codigo"] == "DIA_AGOTADO"
        cal = r.json()["detail"]["calendario"]
        assert next(d for d in cal["dias"] if d["fecha"] == dia["fecha"])["estado"] == "AGOTADO"   # calendario al día
        assert res_estado(lab, rid) == "DATOS_ENVIADOS" and estado(lab, eid) == "CAPTURA_EN_PROCESO"
        otro = next(d for d in cal["dias"] if d["estado"] == "DISPONIBLE")
        assert lab.c.post(f"/api/reservaciones/{rid}/seleccionar-dia", headers=h,
                          json={"fecha": otro["fecha"], "sucursal": dia["sucursal"]}).status_code == 200
        r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
        assert r.status_code == 200 and r.json()["reservacion"]["fecha"] == otro["fecha"]
    finally:
        lab.portal.ctl("POST", "/control/dias/liberar", clave=k, unidades=1)


def test_dos_solicitudes_por_la_ultima_unidad(lab):
    n1 = siguiente_registro()
    t1 = lab.portal.registro(n1)["tramite"]
    n2 = next(n for n in iter(siguiente_registro, None) if lab.portal.registro(n)["tramite"] == t1)
    eid1, u1, h1 = asignado(lab, n=n1)
    eid2, u2, h2 = asignado(lab, n=n2)
    rid1, dia = lab.hasta_fecha(eid1, h1)
    k = f"{dia['sucursal']}|{t1}|{dia['fecha']}"
    lab.portal.ctl("POST", "/control/dias/agotar", clave=k)
    lab.portal.ctl("POST", "/control/dias/liberar", clave=k, unidades=1)        # queda UNA
    rid2, _ = lab.hasta_fecha(eid2, h2, elegir=lambda ds: next(d for d in ds if d["fecha"] == dia["fecha"]))
    codigos = {}

    def pulsar(rid, h):
        codigos[rid] = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
    hilos = [threading.Thread(target=pulsar, args=a) for a in ((rid1, h1), (rid2, h2))]
    for t in hilos:
        t.start()
    for t in hilos:
        t.join()
    st = sorted(r.status_code for r in codigos.values())
    assert st == [200, 409], {k: v.text for k, v in codigos.items()}
    perdedor = next(r for r in codigos.values() if r.status_code == 409)
    assert perdedor.json()["detail"]["codigo"] == "DIA_AGOTADO"
    assert lab.portal.ctl("GET", "/control/dias")["dias"][k]["libres"] == 0


def test_confirmacion_repetida_no_duplica(lab):
    eid, u, h = asignado(lab)
    rid, _ = lab.hasta_fecha(eid, h)
    antes = lab.portal.ctl("GET", "/control/estado")["confirmadas"]
    r1 = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
    r2 = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
    assert r1.status_code == 200 and r2.status_code == 200 and r2.json()["repetida"]
    assert r1.json()["reservacion"]["folio"] == r2.json()["reservacion"]["folio"]
    assert lab.portal.ctl("GET", "/control/estado")["confirmadas"] == antes + 1


def test_respuesta_perdida_tras_confirmar(lab):
    eid, u, h = asignado(lab)
    rid, _ = lab.hasta_fecha(eid, h)
    lab.portal.fallos(modo="cortar_tras_confirmar", restantes=1)
    r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
    assert r.status_code == 200, r.text
    assert r.json()["reservacion"]["evidencia"]["fuente"] == "api_por_clave"


def test_incierto_queda_en_verificacion_y_reinicio_lo_resuelve(lab, monkeypatch):
    eid, u, h = asignado(lab)
    rid, _ = lab.hasta_fecha(eid, h)
    lab.portal.fallos(modo="cortar_tras_confirmar", restantes=1)

    def caido(self, *a, **kw):
        raise ResultadoIncierto("RED", "simulado: el motor perdió la red")
    with monkeypatch.context() as m:
        m.setattr(NavegadorHTTP, "buscar_por_clave", caido)
        m.setattr(NavegadorHTTP, "verificar_folio", caido)
        r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
        assert r.status_code == 202 and r.json()["reservacion"]["estado"] == "EN_VERIFICACION"
        # nadie puede volver a confirmar mientras se verifica
        assert lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True}).status_code == 409
        # y si el portal sigue sin poder verificarse, la recuperación NO decide nada
        out = seguimiento.mantenimiento(lab.db, lab.clock.now())
        assert out["recuperacion"]["sin_portal"] == 1 and res_estado(lab, rid) == "EN_VERIFICACION"
    assert estado(lab, eid) == "RESERVACION_PENDIENTE"
    # "reinicio": otro proceso del motor sobre la misma base
    url = lab.db.url
    lab.cerrar()
    from fastapi.testclient import TestClient
    from monitoreo.app import create_app
    c2 = TestClient(create_app(url, start_engine=False, seed_demo=False))
    c2.__enter__()
    try:
        db2 = c2.app.state.db
        out = seguimiento.mantenimiento(db2, lab.clock.now())
        assert out["recuperacion"]["confirmadas"] == 1
        with db2.session() as s:
            res = s.get(Reservacion, rid)
            assert res.estado == "CONFIRMADA" and res.evidencia["fuente"] == "recuperacion_por_clave"
            assert s.get(Expediente, eid).estado == "RESERVACION_CONFIRMADA"
            folio = res.folio
        # recuperación del folio tras el reinicio: el panel lo sigue mostrando
        assert api_portal(lab, f"/api/reservaciones/{folio}")["estado"] == "CONFIRMADA"
    finally:
        c2.__exit__(None, None, None)
        lab.c.__enter__()


def test_error_del_servidor_al_confirmar_y_reapertura_segura(lab):
    with lab.db.session() as s:
        set_params(s, {"captura_gracia_confirmando_s": 1})
    eid, u, h = asignado(lab)
    rid, _ = lab.hasta_fecha(eid, h)
    lab.portal.fallos(modo="error_interno", restantes=1, pantallas=["continuar"])
    r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
    assert r.status_code == 202 and r.json()["reservacion"]["estado"] == "EN_VERIFICACION"
    time.sleep(1.1)
    out = seguimiento.mantenimiento(lab.db, lab.clock.now())
    assert out["recuperacion"]["reabiertas"] == 1 and res_estado(lab, rid) == "FECHA_ELEGIDA"
    r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
    assert r.status_code == 200 and r.json()["reservacion"]["intentos_confirmacion"] == 2


def test_folio_duplicado_no_se_confirma(lab):
    eid1, _, h1 = asignado(lab)
    rid1, _ = lab.hasta_fecha(eid1, h1)
    assert lab.c.post(f"/api/reservaciones/{rid1}/confirmar", headers=h1, json={"confirmo": True}).status_code == 200
    eid2, _, h2 = asignado(lab)
    rid2, _ = lab.hasta_fecha(eid2, h2)
    lab.portal.ctl("POST", "/control/repetir-folio")
    r = lab.c.post(f"/api/reservaciones/{rid2}/confirmar", headers=h2, json={"confirmo": True})
    assert r.status_code == 202, r.text
    v = r.json()["reservacion"]
    assert v["estado"] == "EN_VERIFICACION" and v["error_codigo"] == "FOLIO_DUPLICADO" and v["folio"] is None
    assert estado(lab, eid2) == "REQUIERE_REVISION"
    out = seguimiento.mantenimiento(lab.db, lab.clock.now())
    assert res_estado(lab, rid2) == "EN_VERIFICACION"              # la recuperación no lo "arregla" sola
    assert out["recuperacion"]["confirmadas"] == 0


def test_sesion_caducada_al_confirmar_pide_reenviar_y_reteclear(lab):
    eid, u, h = asignado(lab)
    rid, dia = lab.hasta_fecha(eid, h)
    lab.portal.ctl("POST", "/control/caducar-sesiones")
    r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
    assert r.status_code == 409 and r.json()["detail"]["tipo"] == "SESION"
    assert res_estado(lab, rid) == "PREPARADA"
    # los campos manuales NO se guardaron: hay que volver a teclearlos
    r = lab.c.post(f"/api/reservaciones/{rid}/enviar-datos", headers=h, json={"revisado": True})
    assert r.status_code == 422
    rid2, _ = lab.hasta_fecha(eid, h)
    assert rid2 == rid
    assert lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True}).status_code == 200


def test_reprogramar_con_folio_anterior(lab):
    n = siguiente_registro()
    eid, u, h = asignado(lab, n=n)
    rid, _ = lab.hasta_fecha(eid, h)
    folio = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True}).json()["reservacion"]["folio"]
    sin_folio = lab.crear(n=n, operacion="reprogramar")
    assert sin_folio["estado"] == "INCOMPLETO" and "folio_anterior" in sin_folio["faltan"]   # validación dependiente
    lab.c.post(f"/api/expedientes/{sin_folio['id']}/cancelar", headers=lab.h)
    e2, u2, h2 = asignado(lab, n=n, operacion="reprogramar", folio_anterior=folio)
    rid2, _ = lab.hasta_fecha(e2, h2)
    r = lab.c.post(f"/api/reservaciones/{rid2}/confirmar", headers=h2, json={"confirmo": True})
    assert r.status_code == 200, r.text
    assert api_portal(lab, f"/api/reservaciones/{folio}")["estado"] == "REPROGRAMADA"


# ---------------------------------------------------------------- asignación y rechazo con motivo
def test_rechazo_con_motivo_y_recuperacion(lab):
    eid, u, h = asignado(lab)
    r = lab.c.post(f"/api/expedientes/{eid}/rechazar", headers=h, json={"motivo": "OTRO", "nota": "llamar al 5551234567"})
    assert r.status_code == 422                                        # sin datos personales en la nota
    r = lab.c.post(f"/api/expedientes/{eid}/rechazar", headers=h, json={"motivo": "NO_DISPONIBLE", "nota": "turno"})
    assert r.status_code == 200 and r.json()["estado"] == "LISTO", r.text
    lab.ciclo()
    otro = lab.operador_de(eid)
    assert otro != u                                                    # no vuelve al que la rechazó
    r = lab.c.post(f"/api/expedientes/{eid}/rechazar", headers=lab.ops[otro], json={"motivo": "DATOS_DUDOSOS"})
    assert r.status_code == 200 and r.json()["estado"] == "REQUIERE_REVISION"
    lab.ciclo()
    with lab.db.session() as s:
        e = s.get(Expediente, eid)
        assert e.operator_id is None and s.get(Request, e.request_id).status == "CANCELADO"
        motivos = [x.detalle.get("motivo") for x in s.scalars(select(ExpedienteEvento).where(
            ExpedienteEvento.expediente_id == eid, ExpedienteEvento.tipo == "ASIGNACION_RECHAZADA"))]
        assert motivos == ["NO_DISPONIBLE", "DATOS_DUDOSOS"]
    # tras corregirlo, revalidar crea una NUEVA ronda (otro código de solicitud)
    r = lab.c.post(f"/api/expedientes/{eid}/revalidar", headers=lab.h)
    assert r.status_code == 200 and r.json()["estado"] == "LISTO" and r.json()["ronda"] == 2
    with lab.db.session() as s:
        assert s.get(Request, s.get(Expediente, eid).request_id).code.endswith("-R2")


def test_operador_limitado_a_su_sucursal(lab):
    r = lab.c.post("/api/users", headers=lab.h, json={"username": "opnor2", "password": "Operador-123",
                                                      "role": "operador", "regions": ["NORTE"], "sucursales": ["NOR-2"]})
    assert r.status_code == 201
    with lab.db.session() as s:     # los demás operadores, ocupados
        for o in s.scalars(select(Operator)):
            if o.sucursales != ["NOR-2"]:
                o.active = False
    e = lab.crear(sucursal="NOR-1", sucursales_alternativas=[])
    lab.ciclo()
    assert estado(lab, e["id"]) == "LISTO"                             # nadie habilitado para NOR-1
    e2 = lab.crear(sucursal="NOR-2", sucursales_alternativas=[])
    lab.ciclo()
    assert estado(lab, e2["id"]) == "ASIGNADO" and lab.operador_de(e2["id"]) == "opnor2"
    h = lab.login("opnor2", "Operador-123")
    v = lab.c.get("/api/captura/calendario-dias", headers=h).json()
    assert {x["sucursal"] for x in v["sucursales"]} == {"NOR-2"}
    assert lab.c.get(f"/api/expedientes/{e['id']}", headers=h).status_code == 404


def test_asignaciones_simultaneas_sin_duplicados(lab):
    ids = [lab.crear()["id"] for _ in range(6)]
    from monitoreo.engine.pipeline import process_pending_events
    from monitoreo.engine import assignment as asg
    from monitoreo.engine.params import get_params
    with lab.db.session() as s:
        from monitoreo.models import Source
        s.get(Source, lab.source_id).next_poll_at = None
    from monitoreo.engine.pipeline import poll_source
    poll_source(lab.db, lab.source_id, lab.clock, lab.runtime, lab.rng)
    with lab.db.session() as s:
        params = get_params(s)
        for req in s.scalars(select(Request).where(Request.status == "PENDIENTE")):
            asg.validate_request(s, req, lab.clock.now())          # PENDIENTE -> ELEGIBLE, sin asignar aún
    hilos = [threading.Thread(target=asg.rebalance, args=(lab.db, lab.clock.now(), params)) for _ in range(4)]
    hilos.append(threading.Thread(target=process_pending_events, args=(lab.db, lab.clock)))
    for t in hilos:
        t.start()
    for t in hilos:
        t.join()
    seguimiento.mantenimiento(lab.db, lab.clock.now())
    with lab.db.session() as s:
        vivas = s.scalars(select(Assignment).where(Assignment.status.in_(["ACTIVA", "EN_PROCESO"]))).all()
        por_req = {}
        for a in vivas:
            por_req.setdefault(a.request_id, []).append(a.operator_id)
        assert len(vivas) >= 2                                            # la prueba no es vacía
        assert all(len(v) == 1 for v in por_req.values())                 # nunca dos operadores por solicitud
        for o in s.scalars(select(Operator)):
            assert o.current_load <= o.max_concurrent
            assert o.current_load == sum(1 for a in vivas if a.operator_id == o.id)
        assert ex.coherencia(s) == []
    assert len([i for i in ids if estado(lab, i) == "ASIGNADO"]) == len(vivas)


def test_sin_datos_personales_en_alertas_ni_auditoria(lab):
    d = lab.datos_expediente(n=siguiente_registro())
    eid = lab.c.post("/api/expedientes", headers=lab.h, json=d).json()["id"]
    lab.ciclo()
    u = lab.operador_de(eid)
    rid, _ = lab.hasta_fecha(eid, lab.ops[u])
    lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=lab.ops[u], json={"confirmo": True})
    secretos = [d["nombre"], d["telefono"], d["correo"], d["fecha_nacimiento"], MANUALES["clave_identidad"]]
    with lab.db.session() as s:
        textos = [json.dumps(a.detail, ensure_ascii=False) + a.target for a in s.scalars(select(AuditLog))]
        textos += [json.dumps(a.payload, ensure_ascii=False) + a.title + a.resource for a in s.scalars(select(Alert))]
        textos += [json.dumps(e.detalle, ensure_ascii=False) for e in s.scalars(select(ExpedienteEvento))]
        textos += [r.holder + json.dumps(r.conditions) for r in s.scalars(select(Request))]
    blob = "\n".join(textos)
    for v in secretos:
        assert v not in blob, v


def test_reasignacion_entre_dos_pasadas_sigue_a_la_asignacion_viva(lab):
    """Defecto hallado en el laboratorio: el motor cancela la asignación (el recurso se agotó) y,
    antes de la siguiente sincronización, asigna la misma solicitud a OTRO operador. El expediente
    debe pasar al operador nuevo; el anterior deja de verlo y el nuevo puede prepararlo."""
    from datetime import timedelta
    from monitoreo.models import User
    eid, u, h = asignado(lab)
    otro = next(x for x in lab.ops if x != u)
    with lab.db.session() as s:
        e = s.get(Expediente, eid)
        viejo = s.scalar(select(Assignment).where(Assignment.request_id == e.request_id,
                                                  Assignment.status == "ACTIVA"))
        op_nuevo = s.scalar(select(Operator.id).join(User, User.id == Operator.user_id).where(User.username == otro))
        now = lab.clock.now()
        viejo.status, viejo.closed_at, viejo.live_request_id = "CANCELADA", now, None
        s.flush()
        s.add(Assignment(request_id=e.request_id, slot_id=viejo.slot_id, operator_id=op_nuevo, quantity=1,
                         status="ACTIVA", live_request_id=e.request_id, created_at=now,
                         expires_at=now + timedelta(minutes=15)))
    assert lab.c.post(f"/api/expedientes/{eid}/preparar", headers=h).status_code in (404, 409)   # antes del arreglo: para siempre
    seguimiento.sincronizar(lab.db, lab.clock.now())
    assert lab.operador_de(eid) == otro
    assert lab.c.get(f"/api/expedientes/{eid}", headers=h).status_code == 404                     # el anterior ya no lo ve
    r = lab.c.post(f"/api/expedientes/{eid}/preparar", headers=lab.ops[otro])
    assert r.status_code == 200, r.text
    with lab.db.session() as s:
        assert [x.tipo for x in s.scalars(select(ExpedienteEvento).where(
            ExpedienteEvento.expediente_id == eid, ExpedienteEvento.tipo == "ASIGNACION_CAMBIADA"))] == ["ASIGNACION_CAMBIADA"]


# ---------------------------------------------------------------- v1.7: notificaciones de expedientes
def test_v17_notificaciones_de_expediente_sin_datos_personales(lab):
    n = siguiente_registro()
    d = lab.datos_expediente(n=n)
    inc = lab.crear(n=n, operacion="reprogramar")                    # sin folio anterior: INCOMPLETO
    assert inc["estado"] == "INCOMPLETO"
    lab.c.post(f"/api/expedientes/{inc['id']}/cancelar", headers=lab.h)
    eid, u, h = asignado(lab)
    rid, _ = lab.hasta_fecha(eid, h)
    assert lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True}).status_code == 200
    with lab.db.session() as s:
        al = {a.categoria: a for a in s.scalars(select(Alert).where(Alert.kind == "CAPTURA"))}
        assert {"EXPEDIENTE_INCOMPLETO", "RESERVACION_CONFIRMADA"} <= set(al)
        conf = al["RESERVACION_CONFIRMADA"]
        assert conf.assignment_id is not None and conf.title.startswith("EXP-")
        blob = "\n".join(json.dumps(a.payload, ensure_ascii=False) + a.title for a in al.values())
    for v in (d["nombre"], d["telefono"], d["correo"], d["fecha_nacimiento"], MANUALES["clave_identidad"]):
        assert v not in blob, v
    # el operador dueño ve la de su reservación; el otro operador no; el incompleto es del administrador
    otro = next(x for x in lab.ops if x != u)
    vis = lambda hh: {a["id"] for a in lab.c.get("/api/notificaciones", headers=hh).json()["items"]}  # noqa: E731
    assert conf.id in vis(h) and conf.id not in vis(lab.ops[otro])
    assert al["EXPEDIENTE_INCOMPLETO"].id not in vis(h) and al["EXPEDIENTE_INCOMPLETO"].id in vis(lab.h)


def test_v17_mi_trabajo_siguiente_paso_y_retomar(lab):
    n = siguiente_registro()
    d = lab.datos_expediente(n=n)
    eid, u, h = asignado(lab, n=n)
    otro = lab.ops[next(x for x in lab.ops if x != u)]

    def mio():
        r = lab.c.get("/api/captura/mi-trabajo", headers=h)
        assert r.status_code == 200, r.text
        return next(x for x in r.json()["items"] if x["id"] == eid), r.json()
    x, todo = mio()
    assert x["paso"]["clave"] == "preparar" and x["paso"]["n"] == 6 and x["asignacion"]["estado"] == "ACTIVA"
    assert not x["interrumpido"] and todo["resumen"]["por_empezar"] >= 1
    blob = json.dumps(todo, ensure_ascii=False)
    for v in (d["nombre"], d["telefono"], d["correo"], d["fecha_nacimiento"]):
        assert v not in blob, v                                           # sin datos personales en la lista
    assert all(i["id"] != eid for i in lab.c.get("/api/captura/mi-trabajo", headers=otro).json()["items"])
    rid = lab.c.post(f"/api/expedientes/{eid}/preparar", headers=h).json()["reservacion"]["id"]
    x, _ = mio()
    assert x["paso"]["clave"] == "manuales" and x["interrumpido"]        # p. ej. se cortó la conexión aquí
    r = lab.c.post(f"/api/reservaciones/{rid}/enviar-datos", headers=h, json={"revisado": True, "manuales": dict(MANUALES)})
    assert r.status_code == 200, r.text
    x, todo = mio()
    assert x["paso"]["clave"] == "disponibilidad" and todo["resumen"]["a_medias"] >= 1
    # retomar: volver a preparar devuelve el MISMO intento (no se duplica trabajo)
    assert lab.c.post(f"/api/expedientes/{eid}/preparar", headers=h).status_code in (200, 409)
    with lab.db.session() as s:
        from monitoreo.models import Reservacion
        assert s.scalar(select(func.count()).select_from(Reservacion).where(Reservacion.viva_expediente_id == eid)) == 1


def test_v17_calendario_salta_al_mes_siguiente_si_el_actual_no_tiene_dias(lab):
    """Defecto de la v1.6 (visto el 13/11/2026 en la línea base): con los días del mes en curso
    agotados, el calendario llegaba vacío aunque el mes siguiente tuviera días. Ahora se muestra el
    siguiente con un aviso. Se fuerza agotando en el portal todos los días del mes en curso."""
    eid, u, h = asignado(lab)
    rid = lab.c.post(f"/api/expedientes/{eid}/preparar", headers=h).json()["reservacion"]["id"]
    r = lab.c.post(f"/api/reservaciones/{rid}/enviar-datos", headers=h, json={"revisado": True, "manuales": dict(MANUALES)})
    cal = r.json()["calendario"]
    mes = cal["mes"] if not cal.get("aviso") else None
    if mes is None:                                      # el mes en curso ya no tenía días: el salto ya ocurrió
        assert any(d["estado"] == "DISPONIBLE" for d in cal["dias"])
        return
    for d in cal["dias"]:
        if d["estado"] == "DISPONIBLE":
            lab.portal.ctl("POST", "/control/dias/agotar", sucursal=cal["sucursal"], fecha=d["fecha"],
                           tramite=cal["solo_lectura"]["tramite_ficha"])
    r = lab.c.get(f"/api/reservaciones/{rid}/calendario", headers=h, params={"sucursal": cal["sucursal"]})
    assert r.status_code == 200, r.text
    c2 = r.json()["calendario"]
    assert c2["mes"] != mes and "aviso" in c2 and any(d["estado"] == "DISPONIBLE" for d in c2["dias"])
    # si el operador pide un mes concreto se respeta (aunque esté vacío)
    c3 = lab.c.get(f"/api/reservaciones/{rid}/calendario", headers=h, params={"sucursal": cal["sucursal"], "mes": mes}).json()
    assert c3["calendario"]["mes"] == mes and not any(d["estado"] == "DISPONIBLE" for d in c3["calendario"]["dias"])


def test_v17_centro_con_calendario_por_dia_cifras_de_la_base(lab):
    """El centro de operaciones con una fuente de calendario por día real (hallado en el recorrido con
    navegador: un campo mal nombrado daba 500 en cuanto existía una fuente de calendario)."""
    from monitoreo.models import CalendarioCambio
    eid, u, h = asignado(lab)
    rid, _ = lab.hasta_fecha(eid, lab.ops[u])
    assert lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=lab.ops[u], json={"confirmo": True}).status_code == 200
    r = lab.c.get("/api/centro?periodo=24h", headers=lab.h)
    assert r.status_code == 200, r.text
    cap = r.json()["captura"]
    with lab.db.session() as s:
        aperturas = s.scalar(select(func.count()).select_from(CalendarioCambio).where(
            CalendarioCambio.tipo.in_(["REAPERTURA", "NUEVA_FECHA", "CANCELACION_DEVUELVE", "REHABILITADA"])))
        conf = s.scalar(select(func.count()).select_from(Expediente).where(Expediente.estado == "RESERVACION_CONFIRMADA"))
    assert cap["calendario"]["fuentes"] and cap["calendario"]["disponibilidades_nuevas"] == aperturas
    assert cap["reservaciones"]["confirmadas_periodo"] >= 1
    assert cap["expedientes"]["por_estado"].get("RESERVACION_CONFIRMADA", 0) == conf
    assert cap["tiempo_hasta_confirmacion_ms"]["n"] >= 1          # ASIGNADO -> confirmada, medido del historial
    assert lab.c.get("/api/centro?periodo=24h", headers=lab.ops[u]).status_code == 200


def test_v17_redistribuir_no_toca_una_confirmacion_en_verificacion(lab):
    """Un operador deja de responder justo con una confirmación EN_VERIFICACION (resultado incierto).
    La redistribución NO debe liberarla aunque se incluyan las tomadas y no haya señal: el motor la
    resuelve preguntando al portal. Si se liberara, otro operador podría reservar dos veces."""
    from datetime import timedelta
    from monitoreo.db import utcnow
    from monitoreo.models import Reservacion
    eid, u, h = asignado(lab)
    rid, _ = lab.hasta_fecha(eid, h)
    with lab.db.session() as s:
        s.get(Reservacion, rid).estado = "EN_VERIFICACION"
        e = s.get(Expediente, eid)
        oid = e.operator_id
        aid = s.scalar(select(Assignment.id).where(Assignment.request_id == e.request_id,
                                                   Assignment.status.in_(["ACTIVA", "EN_PROCESO"])))
        s.get(Operator, oid).visto_en = utcnow() - timedelta(hours=2)
    r = lab.c.post(f"/api/supervision/operadores/{oid}/redistribuir", headers=lab.h,
                   json={"confirmo": True, "motivo": "no responde", "incluir_en_proceso": True}).json()
    assert all(x["asignacion"] != aid for x in r["liberadas"])
    assert any(x["asignacion"] == aid and "confirmación" in x["motivo"] for x in r["no_liberadas"])
    with lab.db.session() as s:
        assert s.get(Assignment, aid).status in ("ACTIVA", "EN_PROCESO")
        assert s.get(Expediente, eid).operator_id == oid
