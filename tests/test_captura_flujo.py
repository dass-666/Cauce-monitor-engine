"""v1.5: flujo completo expediente -> formulario -> calendario -> horario -> confirmación -> folio,
con el portal de prueba corriendo como PROCESO aparte (red real en 127.0.0.1) y el motor
(API + ciclo del planificador ejecutado a mano). Incluye los fallos de la especificación."""
from __future__ import annotations

import json
import threading

import pytest
from sqlalchemy import select

from monitoreo.captura import formularios  # noqa: F401
from monitoreo.captura import reservaciones as rs
from monitoreo.captura import seguimiento
from monitoreo.captura.formularios.navegador import NavegadorHTTP, ResultadoIncierto
from monitoreo.config import settings
from monitoreo.engine.params import set_params
from monitoreo.models import (Alert, Assignment, AuditLog, Expediente, ExpedienteEvento, Request, Reservacion)
from portal_reservas.proceso import PortalProceso

from monitoreo.db import Database

from .captura_util import ADMIN_PWD, Lab, siguiente_registro
from .conftest import BACKENDS, PG_URL


@pytest.fixture(scope="module")
def portal(tmp_path_factory):
    d = tmp_path_factory.mktemp("portal")
    p = PortalProceso(d / "estado.json")
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
    if request.param == "postgres":
        Database(PG_URL).reset_for_tests()
        url = PG_URL
    else:
        url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    lb = Lab(url, portal)
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


# ---------------------------------------------------------------- recorrido feliz y trazabilidad
def test_recorrido_completo_hasta_folio_con_evidencia(lab):
    eid, u, h = asignado(lab)
    ficha = lab.c.get(f"/api/expedientes/{eid}", headers=h).json()
    assert ficha["estado"] == "ASIGNADO" and ficha["datos_completos"]
    rid, _ = lab.hasta_confirmar(eid, h)
    r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
    assert r.status_code == 200, r.text
    res = r.json()["reservacion"]
    assert res["estado"] == "CONFIRMADA" and res["folio"].startswith("LAB-")
    assert res["evidencia"]["clave_coincide"] and res["evidencia"]["estado_portal"] == "CONFIRMADA"
    # el portal (otro proceso) tiene exactamente esa reservación
    assert lab.portal.http.get(f"{lab.portal.base}/api/reservaciones/{res['folio']}",
                               headers={"Authorization": f"Bearer {lab.portal.token}"}).json()["estado"] == "CONFIRMADA"
    ficha = lab.c.get(f"/api/expedientes/{eid}", headers=h).json()
    tipos = [x["tipo"] for x in ficha["historial"]]
    for t in ("DISPONIBILIDAD_DETECTADA", "FORMULARIO_PREPARADO", "RECURSO_SELECCIONADO", "RESERVACION_PENDIENTE",
              "RESERVACION_CONFIRMADA"):
        assert t in tipos, (t, tipos)
    assert ficha["estado"] == "CONFIRMADO"
    # trazabilidad: cada evento normalizado enlaza la asignación del motor y su evento de cambio
    with lab.db.session() as s:
        exp = s.get(Expediente, eid)
        a = s.scalar(select(Assignment).where(Assignment.request_id == exp.request_id))
        assert a.status == "COMPLETADA" and s.get(Request, exp.request_id).status == "COMPLETADO"
        evs = s.scalars(select(ExpedienteEvento).where(ExpedienteEvento.expediente_id == eid)).all()
        det = next(e for e in evs if e.tipo == "DISPONIBILIDAD_DETECTADA")
        assert det.assignment_id == a.id and det.change_event_id is not None
        assert all(e.assignment_id == a.id for e in evs if e.tipo.startswith("RESERVACION_") or e.tipo == "RECURSO_SELECCIONADO")
    # repetir la confirmación no crea otra reservación
    r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
    assert r.status_code == 200 and r.json()["repetida"]


def test_revision_humana_obligatoria(lab):
    eid, u, h = asignado(lab)
    rid = lab.c.post(f"/api/expedientes/{eid}/preparar", headers=h).json()["reservacion"]["id"]
    assert lab.c.post(f"/api/reservaciones/{rid}/enviar-datos", headers=h, json={}).status_code == 422
    r = lab.c.post(f"/api/reservaciones/{rid}/enviar-datos", headers=h, json={"revisado": True})
    hid = r.json()["calendario"]["horarios"][0]["id"]
    lab.c.post(f"/api/reservaciones/{rid}/seleccionar", headers=h, json={"horario": hid})
    assert lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={}).status_code == 422
    with lab.db.session() as s:
        assert s.get(Reservacion, rid).estado == "RETENIDA"


# ---------------------------------------------------------------- errores del portal
def test_campo_incorrecto_detiene_para_revision(lab):
    eid, u, h = asignado(lab)
    lab.portal.fallos(modo="renombrar_campo")
    rid = lab.c.post(f"/api/expedientes/{eid}/preparar", headers=h).json()["reservacion"]["id"]
    r = lab.c.post(f"/api/reservaciones/{rid}/enviar-datos", headers=h, json={"revisado": True})
    assert r.status_code == 422
    d = r.json()["detail"]
    assert d["tipo"] == "REVISION" and d["codigo"] == "CAMPOS_NO_COINCIDEN"
    assert d["errores"]["telefono"] == "no está en el portal" and "telefono_movil" in d["errores"]
    with lab.db.session() as s:
        assert s.get(Reservacion, rid).estado == "PREPARADA"


def test_identificador_inexistente(lab):
    eid, u, h = asignado(lab, id_registro_previo="REG999999")
    rid = lab.c.post(f"/api/expedientes/{eid}/preparar", headers=h).json()["reservacion"]["id"]
    r = lab.c.post(f"/api/reservaciones/{rid}/enviar-datos", headers=h, json={"revisado": True})
    assert r.status_code == 422 and r.json()["detail"]["errores"] == {"identificador": "Identificador inexistente"}


def test_falta_dato_obligatorio_del_contrato(lab):
    eid, u, h = asignado(lab, id_registro_previo=None)
    r = lab.c.post(f"/api/expedientes/{eid}/preparar", headers=h)
    assert r.json()["revision"]["faltan"] == ["identificador"] and not r.json()["revision"]["listo"]
    rid = r.json()["reservacion"]["id"]
    r = lab.c.post(f"/api/reservaciones/{rid}/enviar-datos", headers=h, json={"revisado": True})
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "DATOS_INCOMPLETOS"


def test_reservacion_duplicada_en_el_portal(lab):
    n = siguiente_registro()
    eid, u, h = asignado(lab, n=n)
    rid, _ = lab.hasta_confirmar(eid, h)
    assert lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True}).status_code == 200
    # otra persona (otro nombre) con el MISMO registro previo y trámite: el portal la rechaza
    eid2, u2, h2 = asignado(lab, n=n, nombre="Otra Persona Ficticia", telefono="5559990000",
                            telefono_confirmacion="5559990000")
    rid2, _ = lab.hasta_confirmar(eid2, h2)
    r = lab.c.post(f"/api/reservaciones/{rid2}/confirmar", headers=h2, json={"confirmo": True})
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "RESERVACION_DUPLICADA"
    with lab.db.session() as s:
        assert s.get(Reservacion, rid2).estado == "RECHAZADA"
        assert s.get(Expediente, eid2).estado == "EN_CAPTURA"         # el operador decide qué hacer
    hist = lab.c.get(f"/api/expedientes/{eid2}", headers=h2).json()["historial"]
    assert "RESERVACION_RECHAZADA" in [x["tipo"] for x in hist]


def test_dos_reservaciones_para_el_mismo_horario(lab):
    eid1, u1, h1 = asignado(lab)
    eid2, u2, h2 = asignado(lab)
    preps = []
    for eid, h in ((eid1, h1), (eid2, h2)):
        rid = lab.c.post(f"/api/expedientes/{eid}/preparar", headers=h).json()["reservacion"]["id"]
        cal = lab.c.post(f"/api/reservaciones/{rid}/enviar-datos", headers=h, json={"revisado": True}).json()
        preps.append((rid, h, cal["calendario"]["horarios"]))
    comun = preps[0][2][0]["id"]
    assert comun in [x["id"] for x in preps[1][2]]
    assert lab.c.post(f"/api/reservaciones/{preps[0][0]}/seleccionar", headers=preps[0][1],
                      json={"horario": comun}).status_code == 200
    r = lab.c.post(f"/api/reservaciones/{preps[1][0]}/seleccionar", headers=preps[1][1], json={"horario": comun})
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "HORARIO_AGOTADO"
    assert comun not in [x["id"] for x in r.json()["detail"]["calendario"]["horarios"]]   # calendario actualizado


def test_sesion_caducada_se_reenvian_datos(lab):
    eid, u, h = asignado(lab)
    rid = lab.c.post(f"/api/expedientes/{eid}/preparar", headers=h).json()["reservacion"]["id"]
    hs = lab.c.post(f"/api/reservaciones/{rid}/enviar-datos", headers=h, json={"revisado": True}).json()
    lab.portal.ctl("POST", "/control/caducar-sesiones")
    r = lab.c.post(f"/api/reservaciones/{rid}/seleccionar", headers=h, json={"horario": hs["calendario"]["horarios"][0]["id"]})
    assert r.status_code == 409 and r.json()["detail"]["tipo"] == "SESION"
    with lab.db.session() as s:
        assert s.get(Reservacion, rid).estado == "PREPARADA"
    hs = lab.c.post(f"/api/reservaciones/{rid}/enviar-datos", headers=h, json={"revisado": True}).json()
    r = lab.c.post(f"/api/reservaciones/{rid}/seleccionar", headers=h, json={"horario": hs["calendario"]["horarios"][0]["id"]})
    assert r.status_code == 200
    assert lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True}).status_code == 200


def test_sesion_caducada_al_confirmar_expira_el_intento(lab):
    eid, u, h = asignado(lab)
    rid, _ = lab.hasta_confirmar(eid, h)
    lab.portal.ctl("POST", "/control/caducar-sesiones")
    r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
    assert r.status_code == 409
    with lab.db.session() as s:
        assert s.get(Reservacion, rid).estado == "EXPIRADA" and s.get(Expediente, eid).estado == "EN_CAPTURA"
    # nuevo intento sobre el mismo expediente
    rid2, _ = lab.hasta_confirmar(eid, h)
    assert rid2 != rid
    assert lab.c.post(f"/api/reservaciones/{rid2}/confirmar", headers=h, json={"confirmo": True}).status_code == 200


# ---------------------------------------------------------------- respuestas perdidas y reintentos
def test_confirmacion_registrada_antes_de_perder_la_respuesta(lab):
    eid, u, h = asignado(lab)
    rid, _ = lab.hasta_confirmar(eid, h)
    lab.portal.fallos(modo="cortar_tras_confirmar", restantes=1)
    r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
    assert r.status_code == 200, r.text
    res = r.json()["reservacion"]
    assert res["estado"] == "CONFIRMADA" and res["evidencia"]["fuente"] == "api_por_clave"
    assert lab.portal.ctl("GET", "/control/estado")["fallos"]["aplicados"].get("cortar_tras_confirmar") == 1


def test_error_interno_al_confirmar_y_reintento(lab):
    with lab.db.session() as s:
        set_params(s, {"captura_gracia_confirmando_s": 1})
    eid, u, h = asignado(lab)
    rid, _ = lab.hasta_confirmar(eid, h)
    lab.portal.fallos(modo="error_interno", restantes=1, pantallas=["confirmar"])
    r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
    assert r.status_code == 202 and r.json()["reservacion"]["estado"] == "CONFIRMANDO"
    # mientras está CONFIRMANDO nadie puede volver a confirmar
    assert lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True}).status_code == 409
    import time
    time.sleep(1.1)
    out = seguimiento.mantenimiento(lab.db, lab.clock.now())
    assert out["recuperacion"]["reabiertas"] == 1
    r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
    assert r.status_code == 200 and r.json()["reservacion"]["estado"] == "CONFIRMADA"
    assert r.json()["reservacion"]["intentos_confirmacion"] == 2


def test_nunca_confirmada_sin_evidencia(lab, monkeypatch):
    eid, u, h = asignado(lab)
    rid, _ = lab.hasta_confirmar(eid, h)

    def falsa(self, folio):
        return {"folio": folio, "estado": "CONFIRMADA", "clave": "otra-clave"}
    with monkeypatch.context() as m:
        m.setattr(NavegadorHTTP, "verificar_folio", falsa)
        m.setattr(NavegadorHTTP, "buscar_por_clave", lambda self, clave: None)
        r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
    assert r.status_code == 202 and r.json()["reservacion"]["estado"] == "CONFIRMANDO"
    with lab.db.session() as s:
        assert s.get(Expediente, eid).estado != "CONFIRMADO"
    # la recuperación consulta al portal de verdad y encuentra la reservación por su clave
    assert seguimiento.mantenimiento(lab.db, lab.clock.now())["recuperacion"]["confirmadas"] == 1


def test_reinicio_del_motor_a_mitad_de_confirmar(lab, tmp_path, monkeypatch):
    eid, u, h = asignado(lab)
    rid, _ = lab.hasta_confirmar(eid, h)
    lab.portal.fallos(modo="cortar_tras_confirmar", restantes=1)

    def caido(self, *a, **kw):
        raise ResultadoIncierto("RED", "simulado: el motor perdió la red")
    with monkeypatch.context() as m:
        m.setattr(NavegadorHTTP, "buscar_por_clave", caido)
        m.setattr(NavegadorHTTP, "verificar_folio", caido)
        r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
    assert r.status_code == 202
    url = lab.db.url
    lab.cerrar()                                         # "se apaga" el motor
    lab2 = Lab.__new__(Lab)                              # y arranca otro proceso sobre la misma base
    from fastapi.testclient import TestClient
    from monitoreo.app import create_app
    lab2.c = TestClient(create_app(url, start_engine=False, seed_demo=False))
    lab2.c.__enter__()
    lab2.db = lab2.c.app.state.db
    try:
        with lab2.db.session() as s:
            assert s.get(Reservacion, rid).estado == "CONFIRMANDO"
        out = seguimiento.mantenimiento(lab2.db, lab.clock.now())
        assert out["recuperacion"]["confirmadas"] == 1
        with lab2.db.session() as s:
            res = s.get(Reservacion, rid)
            assert res.estado == "CONFIRMADA" and res.evidencia["fuente"] == "recuperacion_por_clave"
            assert s.get(Expediente, eid).estado == "CONFIRMADO"
    finally:
        lab2.c.__exit__(None, None, None)
        lab.c.__enter__()                                # para que el fixture pueda cerrar


# ---------------------------------------------------------------- dos operadores, permisos
def test_dos_operadores_no_ven_ni_confirman_lo_ajeno(lab):
    eid, u, h = asignado(lab)
    otro = next(x for x in lab.ops if x != u)
    ho = lab.ops[otro]
    assert lab.c.get(f"/api/expedientes/{eid}", headers=ho).status_code == 404
    assert lab.c.post(f"/api/expedientes/{eid}/preparar", headers=ho).status_code == 404
    assert eid not in [x["id"] for x in lab.c.get("/api/expedientes", headers=ho).json()]
    rid, _ = lab.hasta_confirmar(eid, h)
    assert lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=ho, json={"confirmo": True}).status_code == 404
    assert rid not in [x["id"] for x in lab.c.get("/api/reservaciones", headers=ho).json()]


def test_confirmaciones_simultaneas_solo_una(lab):
    """El operador y el administrador pulsan Confirmar a la vez: una sola confirmación llega al portal."""
    eid, u, h = asignado(lab)
    rid, _ = lab.hasta_confirmar(eid, h)
    antes = lab.portal.ctl("GET", "/control/estado")["confirmadas"]
    codigos = []

    def pulsar(hh):
        codigos.append(lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=hh, json={"confirmo": True}).status_code)
    hilos = [threading.Thread(target=pulsar, args=(x,)) for x in (h, lab.h, h, lab.h)]
    for t in hilos:
        t.start()
    for t in hilos:
        t.join()
    assert codigos.count(200) >= 1 and set(codigos) <= {200, 409}
    assert lab.portal.ctl("GET", "/control/estado")["confirmadas"] == antes + 1
    with lab.db.session() as s:
        assert s.get(Reservacion, rid).intentos_confirmacion == 1


def test_un_solo_intento_vivo_por_expediente(lab):
    eid, u, h = asignado(lab)
    ids = set()
    for _ in range(3):
        r = lab.c.post(f"/api/expedientes/{eid}/preparar", headers=h)
        ids.add(r.json()["reservacion"]["id"])
    assert len(ids) == 1


def test_completar_asignacion_de_expediente_a_mano_esta_bloqueado(lab):
    eid, u, h = asignado(lab)
    lab.c.post(f"/api/expedientes/{eid}/preparar", headers=h)
    with lab.db.session() as s:
        aid = s.scalar(select(Assignment.id).where(Assignment.request_id == s.get(Expediente, eid).request_id))
    assert lab.c.post(f"/api/assignments/{aid}/completar", headers=h).status_code == 409
    r = lab.c.post(f"/api/assignments/{aid}/recuperar", headers=lab.h,
                   json={"resultado": "completada", "motivo": "prueba", "confirmar": True})
    assert r.status_code == 409


# ---------------------------------------------------------------- disponibilidad y cancelaciones
def test_disponibilidad_que_desaparece_devuelve_el_expediente(lab):
    e = lab.crear(region="SUR")
    lab.ops["sur"] = None
    # nadie atiende SUR al principio: se crea un operador para esa región
    r = lab.c.post("/api/users", headers=lab.h, json={"username": "opsur", "password": "Operador-123",
                                                      "role": "operador", "regions": ["SUR"]})
    assert r.status_code == 201
    lab.ciclo()
    with lab.db.session() as s:
        assert s.get(Expediente, e["id"]).estado == "ASIGNADO"
    sur = [x["id"] for x in lab.portal.http.get(f"{lab.portal.base}/api/disponibilidad", params={"region": "sur"},
                                                 headers={"Authorization": f"Bearer {lab.portal.token}"}).json()["horarios"]]
    try:
        lab.portal.ctl("POST", "/control/agotar", horarios=sur)
        lab.ciclo()
        lab.ciclo()
        with lab.db.session() as s:
            exp = s.get(Expediente, e["id"])
            assert exp.estado == "PENDIENTE" and exp.operator_id is None
            tipos = [x.tipo for x in s.scalars(select(ExpedienteEvento).where(ExpedienteEvento.expediente_id == exp.id))]
            assert "ASIGNACION_LIBERADA" in tipos
    finally:
        for hid in sur:
            lab.portal.ctl("POST", "/control/liberar", horario=hid, unidades=1)


def test_cancelar_reservacion_libera_horario_y_permite_nuevo_expediente(lab):
    n = siguiente_registro()
    eid, u, h = asignado(lab, n=n)
    rid, hs = lab.hasta_confirmar(eid, h)
    folio = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True}).json()["reservacion"]["folio"]
    r = lab.c.post(f"/api/reservaciones/{rid}/cancelar", headers=h)
    assert r.status_code == 200 and r.json()["estado"] == "CANCELADA"
    api = lab.portal.http.get(f"{lab.portal.base}/api/reservaciones/{folio}",
                              headers={"Authorization": f"Bearer {lab.portal.token}"}).json()
    assert api["estado"] == "CANCELADA"
    with lab.db.session() as s:
        assert s.get(Expediente, eid).estado == "CANCELADO" and s.get(Expediente, eid).dedup_activo is None
    lab.crear(n=n)        # la huella de duplicado quedó libre


def test_retencion_vencida_la_expira_la_recuperacion(lab):
    eid, u, h = asignado(lab)
    rid, _ = lab.hasta_confirmar(eid, h)
    with lab.db.session() as s:
        from datetime import timedelta
        r = s.get(Reservacion, rid)
        r.vence_en = lab.clock.now() - timedelta(seconds=1)
    out = seguimiento.mantenimiento(lab.db, lab.clock.now())
    assert out["recuperacion"]["expiradas"] == 1
    with lab.db.session() as s:
        assert s.get(Reservacion, rid).estado == "EXPIRADA"
    hist = lab.c.get(f"/api/expedientes/{eid}", headers=h).json()["historial"]
    assert "RESERVACION_EXPIRADA" in [x["tipo"] for x in hist]


# ---------------------------------------------------------------- protección de datos
def test_datos_personales_no_salen_en_listados_auditoria_ni_alertas(lab):
    d = lab.datos_expediente(n=siguiente_registro())
    eid = lab.c.post("/api/expedientes", headers=lab.h, json=d).json()["id"]
    lab.ciclo()
    u = lab.operador_de(eid)
    rid, _ = lab.hasta_confirmar(eid, lab.ops[u])
    lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=lab.ops[u], json={"confirmo": True})
    listado = json.dumps(lab.c.get("/api/expedientes", headers=lab.h).json(), ensure_ascii=False)
    secretos = [d["nombre"], d["telefono"], d["correo"], d["fecha_nacimiento"]]
    for v in secretos:
        assert v not in listado
    with lab.db.session() as s:
        textos = [json.dumps(a.detail, ensure_ascii=False) + a.target for a in s.scalars(select(AuditLog))]
        textos += [json.dumps(a.payload, ensure_ascii=False) + a.title + a.resource for a in s.scalars(select(Alert))]
        textos += [json.dumps(e.detalle, ensure_ascii=False) for e in s.scalars(select(ExpedienteEvento))]
        textos += [r.holder + json.dumps(r.conditions) for r in s.scalars(select(Request))]
        blob = "\n".join(textos)
        for v in secretos:
            assert v not in blob, v
        assert not hasattr(Expediente, "telefono_confirmacion") and not hasattr(Expediente, "correo_confirmacion")
    # el portal de prueba guarda solo una huella del contacto
    datos_portal = (lab.portal.datos).read_text(encoding="utf-8")
    assert d["telefono"] not in datos_portal and d["correo"] not in datos_portal
