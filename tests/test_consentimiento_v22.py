"""v2.2 · Consentimiento verificable por expediente, efectos de la revocación, cadena de sellos,
posibles duplicados y trazabilidad. Datos FICTICIOS y portal de laboratorio propio (proceso local
en 127.0.0.1). SQLite y PostgreSQL. La exigencia de consentimiento está ACTIVA en todo el módulo
(MONITOREO_EXIGIR_CONSENTIMIENTO=1), como en una instalación nueva."""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text, update

from monitoreo.captura import consentimiento as cs
from monitoreo.captura import seguimiento
from monitoreo.captura.formularios.navegador import NavegadorHTTP, ResultadoIncierto
from monitoreo.db import utcnow
from monitoreo.models import (Alert, Atencion, AuditLog, Consentimiento, ConsentimientoEvento, Expediente,
                              PlanElemento, Request, Reservacion)
from monitoreo.planificador import coincidencias

from .test_captura_dia import lab as _lab_dia, portal  # noqa: F401  (fixtures)
from .test_planificador import _elementos, _f, _plan, _ver


@pytest.fixture
def lab(_lab_dia, monkeypatch):
    monkeypatch.setenv("MONITOREO_EXIGIR_CONSENTIMIENTO", "1")
    return _lab_dia


ADMIN = __import__("types").SimpleNamespace(username="admin", is_admin=True, operator_id=None)


def cons(n: int = 1, **kw) -> dict:
    d = {"medio": "FIRMA_PAPEL", "aviso_privacidad": "AP-2026-01",
         "evidencia_sha256": hashlib.sha256(f"formato ficticio {n}".encode()).hexdigest(),
         "evidencia_ref": "Carpeta LAB-01"}
    d.update(kw)
    return d


def crear(lab, consentimiento=True, **kw):
    if consentimiento:
        kw["consentimiento"] = cons() if consentimiento is True else consentimiento
    return lab.crear(**kw)


def asignado(lab, **kw):
    e = crear(lab, **kw)
    assert e["estado"] == "LISTO", e
    lab.ciclo()
    u = lab.operador_de(e["id"])
    return e["id"], u, lab.ops[u]


def consentimiento_de(lab, eid, h=None):
    r = lab.c.get(f"/api/expedientes/{eid}/consentimientos", headers=h or lab.h)
    assert r.status_code == 200, r.text
    return r.json()


def revocar(lab, eid, h=None, **kw):
    cid = consentimiento_de(lab, eid)["consentimientos"][-1]["id"]
    r = lab.c.post(f"/api/consentimientos/{cid}/revocar", headers=h or lab.h,
                   json={"medio": "ESCRITO", "motivo": "PERSONA_LO_PIDE", **kw})
    assert r.status_code == 200, r.text
    return r.json()


def exp(lab, eid):
    with lab.db.session() as s:
        e = s.get(Expediente, eid)
        req = s.get(Request, e.request_id) if e.request_id else None
        return {"estado": e.estado, "motivo": e.motivo_cierre, "req": req.status if req else None}


def res_estado(lab, rid):
    with lab.db.session() as s:
        return s.get(Reservacion, rid).estado


def _confirmadas_en_portal(lab):
    return sum(r.get("estado") == "CONFIRMADA" for r in lab.portal.ctl("GET", "/control/reservaciones")["reservaciones"])


def verificar(lab):
    r = lab.c.get("/api/consentimientos/verificar", headers=lab.h)
    assert r.status_code == 200, r.text
    return r.json()


# ================================================================ registro y puerta
def test_sin_consentimiento_no_avanza_y_con_el_si(lab):
    e = crear(lab, consentimiento=False)
    assert e["estado"] == "INCOMPLETO"
    ficha = lab.c.get(f"/api/expedientes/{e['id']}", headers=lab.h).json()
    assert "consentimiento" in json.dumps(ficha["faltantes"] if "faltantes" in ficha else ficha, ensure_ascii=False)
    assert consentimiento_de(lab, e["id"])["bloqueo"]["codigo"] == "SIN_CONSENTIMIENTO"
    lab.ciclo()
    st = exp(lab, e["id"])
    assert st["estado"] == "INCOMPLETO" and st["req"] is None       # nunca hubo solicitud: no se asigna
    r = lab.c.post(f"/api/expedientes/{e['id']}/consentimientos", headers=lab.h, json=cons(2))
    assert r.status_code == 201, r.text
    c = r.json()["consentimiento"]
    assert c["codigo"].startswith("CON-") and c["estado"] == "VIGENTE" and c["responsable"] == "admin"
    assert c["finalidad"] == "GESTION_CITA" and c["aviso_privacidad"] == "AP-2026-01"
    assert r.json()["expediente"]["estado"] == "LISTO"
    # un solo consentimiento vigente por expediente y trámite
    assert lab.c.post(f"/api/expedientes/{e['id']}/consentimientos", headers=lab.h, json=cons(3)).status_code == 409
    lab.ciclo()
    assert exp(lab, e["id"])["estado"] == "ASIGNADO"
    with lab.db.session() as s:
        tipos = [a.action for a in s.scalars(select(AuditLog).where(AuditLog.target == c["codigo"]))]
    assert tipos == ["consentimiento_otorgado"]
    assert verificar(lab)["ok"]


def test_consentimiento_invalido_no_crea_el_expediente(lab):
    with lab.db.session() as s:
        antes = s.scalar(select(func.count(Expediente.id)))
    casos = [cons(evidencia_sha256="no-es-una-huella"), cons(aviso_privacidad="aviso con espacios"),
             cons(medio="VERBAL"), cons(evidencia_ref="Tel 5512345678"), cons(evidencia_ref="persona@correo.test"),
             cons(otorgado_en=(utcnow() + timedelta(days=2)).isoformat()),
             cons(otorgado_en=(utcnow() - timedelta(days=400)).isoformat()),
             cons(finalidad="PUBLICIDAD"), cons(tipo_tramite="otro")]
    for c in casos:
        r = lab.c.post("/api/expedientes", headers=lab.h, json=lab.datos_expediente(consentimiento=c))
        assert r.status_code == 422, (c, r.text)
    # campos desconocidos o faltantes: los rechaza el esquema
    r = lab.c.post("/api/expedientes", headers=lab.h, json=lab.datos_expediente(consentimiento={"medio": "FIRMA_PAPEL"}))
    assert r.status_code == 422
    with lab.db.session() as s:
        assert s.scalar(select(func.count(Expediente.id))) == antes           # el alta se deshizo entera
        assert s.scalar(select(func.count(Consentimiento.id))) == 0
    # el consentimiento no se cambia por PATCH: se revoca y se registra otro
    e = crear(lab)
    r = lab.c.patch(f"/api/expedientes/{e['id']}", headers=lab.h, json={"consentimiento": cons(), "version": 1})
    assert r.status_code == 422


def test_consentimiento_vencido_u_otro_tramite_no_cuenta(lab):
    hace = utcnow() - timedelta(days=10)
    e = crear(lab, consentimiento=cons(otorgado_en=hace.isoformat(), vigente_hasta=(hace + timedelta(days=5)).isoformat()))
    assert e["estado"] == "INCOMPLETO"
    assert consentimiento_de(lab, e["id"])["bloqueo"]["codigo"] == "CONSENTIMIENTO_VENCIDO"
    lab.ciclo()
    assert exp(lab, e["id"])["req"] is None


def test_permisos(lab):
    eid, u, h = asignado(lab)
    otro = next(x for x in lab.ops if x != u)
    cid = consentimiento_de(lab, eid)["consentimientos"][0]["id"]
    # quien no ve el expediente no ve ni revoca su consentimiento
    assert lab.c.get(f"/api/expedientes/{eid}/consentimientos", headers=lab.ops[otro]).status_code == 404
    r = lab.c.post(f"/api/consentimientos/{cid}/revocar", headers=lab.ops[otro], json={"medio": "ESCRITO"})
    assert r.status_code == 404
    assert lab.c.get(f"/api/expedientes/{eid}/trazabilidad", headers=lab.ops[otro]).status_code == 404
    # la verificación de cadenas es de administrador
    assert lab.c.get("/api/consentimientos/verificar", headers=h).status_code == 403
    # el operador asignado sí ve el suyo
    assert consentimiento_de(lab, eid, h)["vigente"]
    r = lab.c.post(f"/api/consentimientos/{cid}/revocar", headers=h, json={"medio": "PALOMA"})
    assert r.status_code == 422


# ================================================================ efectos de la revocación
def test_revocar_asignado_cancela_solicitud_y_cierra(lab):
    eid, u, h = asignado(lab)
    out = revocar(lab, eid, h)
    assert out["repetida"] is False and out["consentimiento"]["estado"] == "REVOCADO"
    assert out["consentimiento"]["revocado_por"] == u and out["consentimiento"]["revocacion_medio"] == "ESCRITO"
    assert out["efectos"]["solicitud_cancelada"] and out["efectos"]["expediente_cerrado"]
    st = exp(lab, eid)
    assert st["estado"] == "CERRADO" and st["motivo"] == "CONSENTIMIENTO_REVOCADO" and st["req"] == "CANCELADO"
    # ninguna operación nueva: preparar, reabrir, ni un consentimiento nuevo sobre el expediente cerrado
    r = lab.c.post(f"/api/expedientes/{eid}/preparar", headers=h)
    assert r.status_code == 409, r.text
    assert lab.c.post(f"/api/expedientes/{eid}/consentimientos", headers=lab.h, json=cons(9)).status_code == 409
    lab.ciclo()
    assert exp(lab, eid)["estado"] == "CERRADO"
    # revocar otra vez: misma revocación, sin efectos nuevos
    again = revocar(lab, eid, h)
    assert again["repetida"] is True and again["consentimiento"]["revocado_en"] == out["consentimiento"]["revocado_en"]
    with lab.db.session() as s:
        n = s.scalar(select(func.count(ConsentimientoEvento.id)).where(ConsentimientoEvento.expediente_id == eid))
        assert n == 2                                                         # OTORGADO + REVOCADO
    assert verificar(lab)["ok"]


def test_revocar_con_fecha_elegida_descarta_el_intento_y_no_confirma(lab):
    eid, u, h = asignado(lab)
    rid, _ = lab.hasta_fecha(eid, h)
    confirmadas = _confirmadas_en_portal(lab)
    out = revocar(lab, eid, h)
    assert out["efectos"]["intento_descartado"] and out["efectos"]["expediente_cerrado"]
    assert res_estado(lab, rid) == "CANCELADA"
    r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
    assert r.status_code == 409, r.text
    assert _confirmadas_en_portal(lab) == confirmadas                     # nada llegó al portal
    with lab.db.session() as s:
        assert s.get(Reservacion, rid).folio is None


def test_revocar_con_cita_confirmada_avisa_y_no_cancela_sola(lab):
    eid, u, h = asignado(lab)
    rid, _ = lab.hasta_fecha(eid, h)
    folio = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True}).json()["reservacion"]["folio"]
    out = revocar(lab, eid, h)
    assert out["efectos"]["aviso_cita_confirmada"] and not out["efectos"]["expediente_cerrado"]
    assert res_estado(lab, rid) == "CONFIRMADA" and exp(lab, eid)["estado"] == "RESERVACION_CONFIRMADA"
    for _ in range(2):
        lab.ciclo()                                       # el mantenimiento no repite el aviso
    with lab.db.session() as s:
        avisos = s.scalar(select(func.count(ConsentimientoEvento.id)).where(
            ConsentimientoEvento.expediente_id == eid, ConsentimientoEvento.tipo == "AVISO_CITA_CONFIRMADA"))
        alertas = [a.title for a in s.scalars(select(Alert)) if "consentimiento revocado con cita" in a.title]
    assert avisos == 1 and len(alertas) == 1, alertas
    portal_res = lab.portal.http.get(lab.portal.base + f"/api/reservaciones/{folio}",
                                     headers={"Authorization": f"Bearer {lab.portal.token}"}).json()
    assert portal_res["estado"] == "CONFIRMADA"
    assert verificar(lab)["ok"]


def test_revocar_durante_la_verificacion_espera_y_despues_avisa(lab, monkeypatch):
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
        out = revocar(lab, eid, h)
        assert out["efectos"]["pendiente"] and not out["efectos"]["expediente_cerrado"]
        assert res_estado(lab, rid) == "EN_VERIFICACION"                     # no se decide a ciegas
    # vuelve la red: la recuperación confirma (la cita SÍ se hizo en el portal) y luego se avisa
    out = seguimiento.mantenimiento(lab.db, utcnow())
    assert out["recuperacion"]["confirmadas"] == 1, out
    assert any(x["expediente_id"] == eid and x["aviso_cita_confirmada"] for x in out["revocaciones"]), out
    assert res_estado(lab, rid) == "CONFIRMADA"
    assert verificar(lab)["ok"]


def test_revocar_y_confirmar_a_la_vez_nunca_queda_incoherente(lab):
    for _ in range(3):
        eid, u, h = asignado(lab)
        rid, _ = lab.hasta_fecha(eid, h)
        cid = consentimiento_de(lab, eid)["consentimientos"][0]["id"]
        barrera, cod = threading.Barrier(2), {}

        def confirmar():
            barrera.wait()
            cod["confirmar"] = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h,
                                          json={"confirmo": True}).status_code

        def revocar_():
            barrera.wait()
            cod["revocar"] = lab.c.post(f"/api/consentimientos/{cid}/revocar", headers=lab.h,
                                        json={"medio": "PRESENCIAL"}).status_code
        hs = [threading.Thread(target=f) for f in (confirmar, revocar_)]
        for t in hs:
            t.start()
        for t in hs:
            t.join()
        assert cod["revocar"] == 200, cod
        lab.ciclo()
        with lab.db.session() as s:
            r = s.get(Reservacion, rid)
            e = s.get(Expediente, eid)
            avisos = s.scalar(select(func.count(ConsentimientoEvento.id)).where(
                ConsentimientoEvento.expediente_id == eid, ConsentimientoEvento.tipo == "AVISO_CITA_CONFIRMADA"))
            print(f"carrera revocar/confirmar: {r.estado} {cod}")
            if r.estado == "CONFIRMADA":
                # la confirmación ganó la carrera: la cita existe y queda el aviso (no se borra sola)
                assert cod["confirmar"] == 200 and avisos == 1 and e.estado == "RESERVACION_CONFIRMADA"
            else:
                # la revocación ganó: nada se confirmó y el expediente quedó cerrado
                assert cod["confirmar"] == 409 and r.folio is None, (cod, r.estado)
                assert e.estado == "CERRADO" and e.motivo_cierre == "CONSENTIMIENTO_REVOCADO"
    assert verificar(lab)["ok"]


def test_revocar_retira_del_plan_y_la_fecha_va_a_otro(lab):
    a = crear(lab, tipo_tramite="alta")
    b = crear(lab, tipo_tramite="alta")
    pid = _plan(lab)
    dia = _f(30)
    r = lab.c.post(f"/api/planes/{pid}/elementos", headers=lab.h, json={"elementos": [
        {"expediente_id": x["id"], "fechas_aceptables": [dia]} for x in (a, b)]}).json()
    assert len(r["aceptados"]) == 2, r
    revocar(lab, a["id"])
    els = _elementos(lab, pid)
    assert els[a["id"]]["estado"] == "RETIRADO"
    nuevos = _ver(lab, fecha=dia, libres=2)
    out = coincidencias.atender(lab.db, nuevos[0]["id"], utcnow())
    assert out["asignado"], out
    els = _elementos(lab, pid)
    assert els[b["id"]]["estado"] == "ASIGNADO" and els[a["id"]]["estado"] == "RETIRADO"
    with lab.db.session() as s:
        assert s.scalar(select(func.count(PlanElemento.id)).where(PlanElemento.activo_expediente_id == a["id"])) == 0


def test_expediente_anterior_sin_consentimiento_no_lo_toma_el_plan(lab, monkeypatch):
    """Instalación que activa la exigencia con expedientes LISTO de antes (sin consentimiento)."""
    monkeypatch.setenv("MONITOREO_EXIGIR_CONSENTIMIENTO", "0")
    viejo = crear(lab, consentimiento=False, tipo_tramite="alta")
    assert viejo["estado"] == "LISTO"
    monkeypatch.setenv("MONITOREO_EXIGIR_CONSENTIMIENTO", "1")
    pid = _plan(lab)
    dia = _f(31)
    lab.c.post(f"/api/planes/{pid}/elementos", headers=lab.h, json={"elementos": [
        {"expediente_id": viejo["id"], "fechas_aceptables": [dia]}]})
    out = coincidencias.atender(lab.db, _ver(lab, fecha=dia, libres=1)[0]["id"], utcnow())
    assert not out.get("elementos"), out
    # tampoco se puede preparar a mano
    lab.ciclo()
    st = exp(lab, viejo["id"])
    assert st["estado"] != "CAPTURA_EN_PROCESO"


# ================================================================ Ventanilla
def _atencion(lab, h):
    r = lab.c.post("/api/ventanilla/atenciones", headers=h, json={"clave": uuid.uuid4().hex})
    assert r.status_code in (200, 201), r.text
    return r.json()


def test_ventanilla_registra_consentimiento_de_la_persona_presente(lab):
    h = lab.ops["op1"]
    a = _atencion(lab, h)
    r = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/expediente", headers=h, json={"datos": lab.datos_expediente()})
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["expediente"]["estado"] == "INCOMPLETO"
    # sin consentimiento la atención no puede preparar
    eid = v["expediente"]["id"]
    assert lab.c.post(f"/api/expedientes/{eid}/preparar", headers=h).status_code in (403, 404, 409)
    r = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/consentimiento", headers=h,
                   json=cons(5, medio="PRESENCIAL_VENTANILLA"))
    assert r.status_code == 201, r.text
    v = r.json()["atencion"]
    assert v["estado"] == "EN_PROCESO" and v["siguiente"]["clave"] == "preparar", v
    c = consentimiento_de(lab, eid, h)["consentimientos"][0]
    assert c["responsable"].startswith("op1") and c["medio"] == "PRESENCIAL_VENTANILLA"
    # la persona se arrepiente a mitad de la atención: se revoca y al cerrar NO vuelve a la cola
    rid, _ = lab.hasta_fecha(eid, h)
    revocar(lab, eid, h, medio="PRESENCIAL")
    assert res_estado(lab, rid) == "CANCELADA"
    r = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/cerrar", headers=h, json={"resultado": "SIN_DISPONIBILIDAD"})
    assert r.status_code in (200, 409), r.text
    st = exp(lab, eid)
    assert st["estado"] == "CERRADO" and st["motivo"] == "CONSENTIMIENTO_REVOCADO"
    with lab.db.session() as s:
        at = s.get(Atencion, a["id"])
        assert at.viva_expediente_id is None
    assert verificar(lab)["ok"]


def test_ventanilla_no_toma_expediente_revocado(lab):
    e = crear(lab)
    revocar(lab, e["id"])
    h = lab.ops["op1"]
    a = _atencion(lab, h)
    r = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/expediente", headers=h, json={"expediente_id": e["id"]})
    assert r.status_code == 409, r.text


# ================================================================ cadena de sellos
def test_cadena_detecta_cambios_borrados_e_intercalados(lab):
    eid, u, h = asignado(lab)
    revocar(lab, eid, h)
    assert verificar(lab)["ok"]
    with lab.db.session() as s:
        c = s.scalar(select(Consentimiento).where(Consentimiento.expediente_id == eid))
        cid, fin, resp = c.id, c.finalidad, c.responsable
        evs = list(s.scalars(select(ConsentimientoEvento).where(ConsentimientoEvento.consentimiento_id == cid)
                             .order_by(ConsentimientoEvento.id)))
        ev_rev = evs[-1]
        det, ev_id = dict(ev_rev.detalle), ev_rev.id
    # 1) cambiar un dato del consentimiento (p. ej. la finalidad) después de sellado
    with lab.db.session() as s:
        s.execute(update(Consentimiento).where(Consentimiento.id == cid).values(responsable="otra_persona"))
    v = verificar(lab)
    assert not v["ok"] and any("no coinciden" in p["problema"] for p in v["problemas"]), v
    with lab.db.session() as s:
        s.execute(update(Consentimiento).where(Consentimiento.id == cid).values(responsable=resp))
    assert verificar(lab)["ok"]
    # 2) alterar el detalle de un evento
    with lab.db.session() as s:
        s.execute(update(ConsentimientoEvento).where(ConsentimientoEvento.id == ev_id).values(
            detalle={**det, "medio": "TELEFONO"}))
    v = verificar(lab)
    assert not v["ok"] and any("cadena rota" in p["problema"] for p in v["problemas"]), v
    with lab.db.session() as s:
        s.execute(update(ConsentimientoEvento).where(ConsentimientoEvento.id == ev_id).values(detalle=det))
    assert verificar(lab)["ok"]
    # 3) borrar el evento de revocación (que pareciera vigente)
    with lab.db.session() as s:
        row = s.get(ConsentimientoEvento, ev_id)
        guardado = {k: getattr(row, k) for k in ("consentimiento_id", "expediente_id", "tipo", "ts", "actor",
                                                  "detalle", "anterior", "sello")}
        s.delete(row)
    v = verificar(lab)
    assert not v["ok"] and {"versión" in p["problema"] or "sin su evento" in p["problema"] for p in v["problemas"]} == {True}
    # 4) intercalar un evento con el mismo "anterior": lo impide la base de datos
    with lab.db.session() as s:
        s.add(ConsentimientoEvento(**guardado))
    with pytest.raises(Exception):
        with lab.db.session() as s:
            s.add(ConsentimientoEvento(**{**guardado, "sello": "f" * 64, "tipo": "OTORGADO"}))
    assert verificar(lab)["ok"]
    # 5) la verificación también la ve el autodiagnóstico
    from monitoreo import diagnostico
    with lab.db.session() as s:
        s.execute(update(Consentimiento).where(Consentimiento.id == cid).values(finalidad="OTRA"))
    items = []
    diagnostico.check_consentimientos(items, lab.db, utcnow(), {})
    assert [i.estado for i in items if i.clave == "cons:cadena"] == ["CRITICO"], items
    with lab.db.session() as s:
        s.execute(update(Consentimiento).where(Consentimiento.id == cid).values(finalidad=fin))
    items = []
    diagnostico.check_consentimientos(items, lab.db, utcnow(), {})
    assert {i.estado for i in items} == {"OK"}, items


# ================================================================ duplicados no exactos
def _nacimiento(lab, eid):
    with lab.db.session() as s:
        return s.get(Expediente, eid).fecha_nacimiento.isoformat()


def test_posibles_duplicados_van_a_revision_y_un_admin_los_descarta(lab):
    a = crear(lab, nombre="María José Pérez Ficticia", tipo_tramite="alta")
    a["fecha_nacimiento"] = _nacimiento(lab, a["id"])
    assert a["estado"] == "LISTO"
    # mismo nacimiento y el nombre sin acentos y con una letra cambiada: otro registro previo
    b = crear(lab, nombre="Maria Jose Peres Ficticia", fecha_nacimiento=a["fecha_nacimiento"], tipo_tramite="alta")
    assert b["estado"] == "REQUIERE_REVISION", b
    assert f"POSIBLE_DUPLICADO:{a['codigo']}" in (b.get("ultimo_error") or json.dumps(b, ensure_ascii=False))
    p = lab.c.get(f"/api/expedientes/{b['id']}/posibles-duplicados", headers=lab.h).json()["posibles"]
    assert [x["codigo"] for x in p] == [a["codigo"]] and "nombre parecido" in p[0]["razones"][0]
    # mismo teléfono que A, nombre distinto: también
    with lab.db.session() as s:
        tel = s.get(Expediente, a["id"]).telefono
    c = crear(lab, nombre="Otra Persona Ficticia", telefono=tel, telefono_confirmacion=tel, tipo_tramite="alta")
    assert c["estado"] == "REQUIERE_REVISION", c
    # el operador no puede descartar; el admin sí, con un motivo (queda en los dos expedientes)
    op = lab.ops["op1"]
    body = {"otro": a["codigo"], "motivo": "Revisé el formato firmado: son personas distintas"}
    assert lab.c.post(f"/api/expedientes/{b['id']}/duplicados/descartar", headers=op, json=body).status_code == 403
    r = lab.c.post(f"/api/expedientes/{b['id']}/duplicados/descartar", headers=lab.h, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "LISTO", r.json()
    assert lab.c.get(f"/api/expedientes/{b['id']}/posibles-duplicados", headers=lab.h).json()["posibles"] == []
    with lab.db.session() as s:
        acc = [x.action for x in s.scalars(select(AuditLog).where(AuditLog.action == "duplicado_descartado"))]
    assert acc == ["duplicado_descartado"]
    # el motivo no admite datos personales largos
    r = lab.c.post(f"/api/expedientes/{c['id']}/duplicados/descartar", headers=lab.h, json={"otro": a["codigo"], "motivo": "x"})
    assert r.status_code == 422


def test_nombres_distintos_con_misma_fecha_no_son_duplicados(lab):
    a = crear(lab, nombre="Ana Laura Ficticia Uno", tipo_tramite="alta")
    a["fecha_nacimiento"] = _nacimiento(lab, a["id"])
    b = crear(lab, nombre="Roberto Carlos Ficticio Dos", fecha_nacimiento=a["fecha_nacimiento"], tipo_tramite="alta")
    assert b["estado"] == "LISTO", b


# ================================================================ trazabilidad
def test_trazabilidad_de_punta_a_punta_sin_datos_personales(lab):
    eid, u, h = asignado(lab)
    rid, _ = lab.hasta_fecha(eid, h)
    assert lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True}).status_code == 200
    revocar(lab, eid, h)
    r = lab.c.get(f"/api/expedientes/{eid}/trazabilidad", headers=h)
    assert r.status_code == 200, r.text
    t = r.json()
    tipos = [x["tipo"] for x in t["linea_de_tiempo"]]
    assert "CONSENTIMIENTO_OTORGADO" in tipos
    orden = ["CREADO", "CONSENTIMIENTO_REGISTRADO", "SOLICITUD_CREADA", "RESERVACION_CONFIRMADA",
             "CONSENTIMIENTO_REVOCADO", "CONSENTIMIENTO_AVISO_CITA_CONFIRMADA"]
    pos = [next(i for i, x in enumerate(tipos) if x == o) for o in orden]
    assert pos == sorted(pos), tipos
    ts = [x["ts"] for x in t["linea_de_tiempo"]]
    assert ts == sorted(ts)
    assert t["verificacion_consentimientos"]["ok"] and t["expediente"]["motivo_cierre"] is None
    with lab.db.session() as s:
        e = s.get(Expediente, eid)
        personales = [e.nombre, e.telefono, e.correo, e.fecha_nacimiento.isoformat()]
    blob = json.dumps(t, ensure_ascii=False)
    for p in personales:
        assert p not in blob, p
    # y tampoco en la auditoría ni en las alertas
    with lab.db.session() as s:
        blob = json.dumps([[str(v) for v in row] for tb in ("audit_log", "alerts", "consentimiento_eventos")
                           for row in s.execute(text(f"SELECT * FROM {tb}"))], ensure_ascii=False)
    for p in personales[:3]:
        assert p not in blob, p


# ================================================================ recuperación (procesos, sesiones, base de datos)
def test_revocacion_interrumpida_la_completa_el_mantenimiento(lab, monkeypatch):
    """El proceso muere justo después de registrar la revocación (antes de aplicar sus efectos): la
    revocación ya es definitiva y el mantenimiento del motor (otro proceso, otro ciclo) termina el resto."""
    eid, u, h = asignado(lab)
    rid, _ = lab.hasta_fecha(eid, h)

    def muere(*a, **kw):
        raise RuntimeError("simulado: el proceso se cayó tras guardar la revocación")
    with monkeypatch.context() as m:
        m.setattr(cs, "aplicar_revocacion", muere)
        cid = consentimiento_de(lab, eid)["consentimientos"][0]["id"]
        with pytest.raises(RuntimeError):
            cs.revocar(lab.db, cid, {"medio": "ESCRITO"}, ADMIN, utcnow())
    with lab.db.session() as s:
        assert s.get(Consentimiento, cid).estado == "REVOCADO"
    assert res_estado(lab, rid) == "FECHA_ELEGIDA" and exp(lab, eid)["estado"] != "CERRADO"
    # mientras tanto, ninguna operación nueva pasa la puerta
    r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
    assert r.status_code == 409, r.text
    out = seguimiento.mantenimiento(lab.db, utcnow())
    assert any(x["expediente_id"] == eid and x["expediente_cerrado"] for x in out["revocaciones"]), out["revocaciones"]
    assert res_estado(lab, rid) == "CANCELADA" and exp(lab, eid)["estado"] == "CERRADO"
    assert verificar(lab)["ok"]


def test_fallo_a_mitad_de_la_revocacion_no_deja_nada_a_medias(lab, monkeypatch):
    """La base de datos falla entre el cambio de estado y el evento sellado: se deshace TODO (el
    consentimiento sigue vigente, sin eventos huérfanos) y la cadena sigue íntegra."""
    eid, u, h = asignado(lab)
    cid = consentimiento_de(lab, eid)["consentimientos"][0]["id"]
    real = cs._agregar_evento

    def cae(s, c, tipo, *a, **kw):
        if tipo == "REVOCADO":
            raise RuntimeError("simulado: conexión perdida")
        return real(s, c, tipo, *a, **kw)
    with monkeypatch.context() as m:
        m.setattr(cs, "_agregar_evento", cae)
        with pytest.raises(RuntimeError):
            cs.revocar(lab.db, cid, {"medio": "ESCRITO"}, ADMIN, utcnow())
    with lab.db.session() as s:
        c = s.get(Consentimiento, cid)
        assert c.estado == "VIGENTE" and c.version == 1 and c.revocado_en is None
        assert s.scalar(select(func.count(ConsentimientoEvento.id)).where(ConsentimientoEvento.consentimiento_id == cid)) == 1
        assert s.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "consentimiento_revocado")) == 0
    assert exp(lab, eid)["estado"] == "ASIGNADO" and verificar(lab)["ok"]
    # se reintenta y ahora sí
    assert revocar(lab, eid, h)["repetida"] is False and exp(lab, eid)["estado"] == "CERRADO"


def test_sesion_vencida_no_revoca_y_al_volver_se_retoma(lab):
    from monitoreo.models import ApiToken
    h = lab.ops["op1"]
    a = _atencion(lab, h)
    r = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/expediente", headers=h, json={"datos": lab.datos_expediente()})
    assert r.status_code == 200 and r.json()["siguiente"]["clave"] == "consentimiento", r.text
    with lab.db.session() as s:          # vence la sesión del operador (como tras MONITOREO_TOKEN_TTL_HORAS)
        s.execute(update(ApiToken).values(expires_at=utcnow() - timedelta(minutes=1)))
    r = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/consentimiento", headers=h, json=cons(30))
    assert r.status_code == 401
    with lab.db.session() as s:
        assert s.scalar(select(func.count(Consentimiento.id))) == 0
    h2 = lab.login("op1", "Operador-123")
    v = lab.c.get(f"/api/ventanilla/atenciones/{a['id']}", headers=h2).json()
    assert v["abierta"] and v["siguiente"]["clave"] == "consentimiento"       # «Retomar»: mismo paso
    r = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/consentimiento", headers=h2, json=cons(30))
    assert r.status_code == 201 and r.json()["atencion"]["siguiente"]["clave"] == "preparar", r.text
    # el operador revoca con su sesión vencida: 401 y nada cambia
    eid = v["expediente"]["id"]
    cid = consentimiento_de(lab, eid, h2)["consentimientos"][0]["id"]
    with lab.db.session() as s:
        s.execute(update(ApiToken).values(expires_at=utcnow() - timedelta(minutes=1)))
    assert lab.c.post(f"/api/consentimientos/{cid}/revocar", headers=h2, json={"medio": "ESCRITO"}).status_code == 401
    with lab.db.session() as s:
        assert s.get(Consentimiento, cid).estado == "VIGENTE"


def test_reinicio_del_motor_conserva_consentimientos_y_revocaciones(lab):
    from fastapi.testclient import TestClient

    from monitoreo.app import create_app
    e1, u, h = asignado(lab)
    e2 = crear(lab)
    revocar(lab, e2["id"])
    v_antes = verificar(lab)
    url = lab.db.url
    lab.cerrar()
    c2 = TestClient(create_app(url, start_engine=False, seed_demo=False))
    c2.__enter__()
    try:
        db2 = c2.app.state.db
        seguimiento.mantenimiento(db2, utcnow())
        v = cs.verificar(db2)
        assert v["ok"] and v["eventos"] == v_antes["eventos"]
        with db2.session() as s:
            assert s.get(Expediente, e2["id"]).estado == "CERRADO"
            e = s.get(Expediente, e1)
            assert e.estado == "ASIGNADO" and cs.vigente(s, e, utcnow()) is not None
    finally:
        c2.__exit__(None, None, None)
        lab.c.__enter__()


def test_retencion_borrar_conserva_la_constancia_de_consentimiento(lab):
    """Con `retencion_accion=BORRAR`, un expediente revocado (con consentimientos) se ANONIMIZA: la
    constancia de la autorización y de su revocación se conserva y la retención no se detiene."""
    from monitoreo.captura import retencion
    from monitoreo.engine.params import set_params
    e = crear(lab)
    revocar(lab, e["id"])
    with lab.db.session() as s:
        set_params(s, {"retencion_accion": "BORRAR", "retencion_cerrados_dias": 1})
    res = retencion.aplicar(lab.db, utcnow() + timedelta(days=3))
    assert res["borrados"] == 0 and res["anonimizados"] >= 1, res
    with lab.db.session() as s:
        x = s.get(Expediente, e["id"])
        assert x is not None and x.anonimizado_en is not None and x.nombre != "" and x.telefono is None
        assert s.scalar(select(func.count(Consentimiento.id)).where(Consentimiento.expediente_id == e["id"])) == 1
    assert verificar(lab)["ok"]
