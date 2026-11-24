"""Eslabón coincidencia -> Forms -> confirmación supervisada -> folio verificado (laboratorio, datos FICTICIOS).

Recorrido con los componentes reales de Monitor y el portal de laboratorio como proceso; Forms lo hace el ESPÍA
(o Forms REAL con FORMS_REPO, en el recorrido scripts/recorrido_coincidencia_forms.py):

  Ventanilla: expediente con consentimiento -> «dejar en el plan» con fechas aceptables
  laboratorio habilita un día -> observación -> coincidencia -> operación preparada (operador)
  Ventanilla: autorrellenar -> la coincidencia viaja a Forms y su resultado vuelve a la reservación
  -> operación LISTA PARA CONFIRMACIÓN (no es cita) -> la persona confirma en el portal (simulada aquí)
  -> folio tecleado dos veces -> Monitor lo comprueba con la API del portal -> CONFIRMADA y plan CONFIRMADO.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import select

from monitoreo.autorrelleno import adaptador as ad
from monitoreo.autorrelleno import coincidencia as cz
from monitoreo.captura import portal as portal_mod
from monitoreo.captura import zona
from monitoreo.captura.formularios import contrato as ct
from monitoreo.db import utcnow
from monitoreo.models import Expediente, ExpedienteEvento, PlanElemento, Reservacion
from monitoreo.planificador import coincidencias, observaciones

from .captura_util import MANUALES
from .test_autorrelleno_forms import _pin, cons, esperar, lab, portal, pulsar, textos_guardados  # noqa: F401


def _f(n: int) -> str:
    return (zona.hoy(utcnow()) + timedelta(days=n)).isoformat()


def _fecha_libre(lab, tram, desde=25, hasta=60, sucursal="NOR-1"):
    d = lab.portal.ctl("GET", "/control/dias")
    usadas = {x["fecha"] for x in d["dias"].values() if x["sucursal"] == sucursal and x["tramite"] == tram}
    hoy = date.fromisoformat(d["hoy"])
    return next(f for f in ((hoy + timedelta(days=i)).isoformat() for i in range(desde, hasta)) if f not in usadas)


def _atencion(lab, h, **body):
    r = lab.c.post("/api/ventanilla/atenciones", headers=h, json={"clave": uuid.uuid4().hex})
    assert r.status_code in (200, 201), r.text
    aid = r.json()["id"]
    r = lab.c.post(f"/api/ventanilla/atenciones/{aid}/expediente", headers=h, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def preparar_coincidencia(lab, h, op="op1"):
    """Del alta en Ventanilla a la operación preparada desde una coincidencia, como en el recorrido v2.3."""
    r = lab.c.post("/api/planes", headers=lab.h, json={"fecha_jornada": zona.hoy(utcnow()).isoformat(), "capacidad": 6})
    assert r.status_code in (201, 409), r.text
    datos = lab.datos_expediente()
    datos["consentimiento"] = cons()
    a = _atencion(lab, h, datos=datos)
    exp_id, tram = a["expediente"]["id"], datos["tipo_tramite"]
    fecha = _fecha_libre(lab, tram)
    r = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/plan", headers=h,
                   json={"fechas_desde": _f(1), "fechas_hasta": (date.fromisoformat(fecha) + timedelta(days=5)).isoformat()})
    assert r.status_code == 200, r.text
    with lab.db.session() as s:
        assert s.get(Expediente, exp_id).estado == "LISTO"
    # el laboratorio habilita el día; el observador lo ve (línea normalizada como la emite el observador)
    lab.portal.ctl("POST", "/control/dias/habilitar", sucursal="NOR-1", tramite=tram, fecha=fecha, capacidad=1)
    ev = observaciones.registrar(lab.db, {"tipo": "CELDA", "estrategia": "dom", "fecha": fecha, "estado": "DISPONIBLE",
                                          "ts_ms": utcnow().timestamp() * 1000, "libres": 1},
                                 {"observador": "obs-prueba", "sucursal": "NOR-1", "tramite": tram}, utcnow())
    out = coincidencias.atender(lab.db, ev[0]["id"], utcnow())
    assert out["asignado"], out
    elid = out["elemento_id"]
    # una coincidencia detectada NO es una cita
    with lab.db.session() as s:
        assert s.get(PlanElemento, elid).estado == "ASIGNADO"
        assert s.scalar(select(Reservacion).where(Reservacion.expediente_id == exp_id)) is None
    r = lab.c.post(f"/api/planificador/elementos/{elid}/preparar", headers=h)
    assert r.status_code == 200, r.text
    rid = r.json()["reservacion_id"]
    a2 = _atencion(lab, h, expediente_id=exp_id)
    return {"aid": a2["id"], "exp_id": exp_id, "elid": elid, "rid": rid, "fecha": fecha, "sucursal": "NOR-1",
            "datos": datos, "tram": tram}


def persona_confirma_en_portal(lab, exp_id, fecha, sucursal):
    """La PERSONA (simulada en esta prueba) termina en el portal de laboratorio lo que Forms dejó listo:
    teclea los campos manuales, elige el día de la coincidencia y confirma. Devuelve el folio que el portal muestra."""
    with lab.db.session() as s:
        exp = s.get(Expediente, exp_id)
        valores = ct.valores_desde(portal_mod.contrato("POR_DIA"), exp)
        region = (exp.region or "").lower()
    with httpx.Client(base_url=lab.portal.base, follow_redirects=True, timeout=10) as c:
        c.get("/cita")
        datos = {k: v for k, v in valores.items() if v is not None}
        datos.update(MANUALES)
        r = c.post("/cita/datos", data=datos)
        assert r.status_code == 200 and 'data-pantalla="calendario"' in r.text, r.text[:400]
        mes = fecha[:7]
        r = c.get("/cita/calendario", params={"estado": region, "sucursal": sucursal, "mes": mes})
        clave = re.search(r'name="clave" value="([0-9a-f]+)"', r.text).group(1)
        r = c.post("/cita/continuar", data={"estado": region, "sucursal": sucursal, "mes": mes, "clave": clave, "dia": fecha})
        assert r.status_code == 200, r.text[:400]
        return re.search(r'data-folio="([^"]+)"', r.text).group(1)


def folio(lab, h, rid, f, f2=None):
    return lab.c.post(f"/api/autorrelleno/reservaciones/{rid}/folio", headers=h,
                      json={"folio": f, "folio_confirmacion": f2 or f})


def _tipos(lab, exp_id):
    with lab.db.session() as s:
        return [e.tipo for e in s.scalars(select(ExpedienteEvento).where(ExpedienteEvento.expediente_id == exp_id)
                                          .order_by(ExpedienteEvento.id))]


# ================================================================ recorrido completo
def test_coincidencia_a_forms_a_folio_verificado(lab):
    h = lab.ops["op1"]
    k = preparar_coincidencia(lab, h)
    st = lab.c.get(f"/api/autorrelleno/atenciones/{k['aid']}", headers=h).json()
    assert st["disponible"], st
    assert st["coincidencia"]["fecha"] == k["fecha"] and st["coincidencia"]["reservacion_id"] == k["rid"]
    assert st["coincidencia"]["lista_para_confirmacion"] is False
    assert pulsar(lab, k["aid"], h).status_code == 202
    fin = esperar(lab, k["aid"], h)
    assert fin["estado"] == "PREPARADO" and fin["resultado_operacion"] == cz.LISTA, fin
    assert fin["coincidencia"]["fecha"] == k["fecha"] and "CONFIRMACIÓN SUPERVISADA" in fin["mensaje"]
    # el perfil del portal por día NO declara fecha/sucursal (van en su 2.ª pantalla): no viajan como campo
    sol = json.loads((lab.espia / "solicitud.json").read_text(encoding="utf-8"))
    assert cz.CAMPO_FECHA not in sol["campos"]
    # preparada para confirmación supervisada: reservación PREPARADA, plan OPERACION_PREPARADA, expediente sin folio
    with lab.db.session() as s:
        res = s.get(Reservacion, k["rid"])
        assert res.estado == "PREPARADA" and res.folio is None and res.evidencia["forms"]["resultado"] == cz.LISTA
        assert s.get(PlanElemento, k["elid"]).estado == "OPERACION_PREPARADA"
        assert s.get(Expediente, k["exp_id"]).estado != "RESERVACION_CONFIRMADA"
    st = lab.c.get(f"/api/autorrelleno/atenciones/{k['aid']}", headers=h).json()
    assert st["coincidencia"]["lista_para_confirmacion"] and st["coincidencia"]["verificable"]

    f = persona_confirma_en_portal(lab, k["exp_id"], k["fecha"], k["sucursal"])
    assert folio(lab, h, k["rid"], f, f + "X").status_code == 422           # tecleado distinto
    assert folio(lab, lab.ops["op2"], k["rid"], f).status_code == 404       # otro operador
    r = folio(lab, h, k["rid"], " " + f.lower() + " ")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["reservacion"]["estado"] == "CONFIRMADA" and out["reservacion"]["folio"] == f
    ev = out["reservacion"]["evidencia"]
    assert ev["fuente"] == "PORTAL_API_FOLIO" and ev["verificada_con_proveedor"] is True
    assert ev["comprobado"] == {"estado_confirmada": True, "folio": True, "fecha": True, "sucursal": True,
                                "titular": True, "tramite": True}
    assert ev["forms"]["resultado"] == cz.LISTA
    assert out["estado_plan"] == "CONFIRMADO"
    with lab.db.session() as s:
        assert s.get(Expediente, k["exp_id"]).estado == "RESERVACION_CONFIRMADA"
    assert folio(lab, h, k["rid"], f).json()["repetida"] is True           # idempotente
    tipos = _tipos(lab, k["exp_id"])
    i = tipos.index
    assert i("PLAN_OPERACION_PREPARADA") < i("COINCIDENCIA_ENTREGADA_A_FORMS") < i("OPERACION_LISTA_PARA_CONFIRMACION") \
        < i("RESERVACION_CONFIRMADA")
    guardado = textos_guardados(lab)
    for v in (k["datos"]["telefono"], k["datos"]["correo"], k["datos"]["id_registro_previo"], MANUALES["clave_identidad"]):
        assert v not in guardado


def test_folio_de_otra_persona_o_de_otro_dia_no_confirma(lab):
    h = lab.ops["op1"]
    k = preparar_coincidencia(lab, h)
    assert pulsar(lab, k["aid"], h).status_code == 202
    assert esperar(lab, k["aid"], h)["resultado_operacion"] == cz.LISTA
    # otra persona confirma OTRO día en el portal: su folio no sirve para este expediente
    otra = preparar_coincidencia(lab, lab.ops["op2"], op="op2")
    f_otra = persona_confirma_en_portal(lab, otra["exp_id"], otra["fecha"], otra["sucursal"])
    r = folio(lab, h, k["rid"], f_otra)
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "FOLIO_NO_COINCIDE", r.text
    comp = r.json()["detail"]["comprobado"]
    assert comp["titular"] is False and comp["estado_confirmada"] is True
    # inexistente
    r = folio(lab, h, k["rid"], "DIA-20991231-99999Z")
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "FOLIO_INEXISTENTE", r.text
    # formato que no es del portal
    assert folio(lab, h, k["rid"], "ABC-123").status_code == 422
    with lab.db.session() as s:
        assert s.get(Reservacion, k["rid"]).estado == "PREPARADA"
        assert s.get(PlanElemento, k["elid"]).estado == "OPERACION_PREPARADA"
    assert "FOLIO_RECHAZADO" in _tipos(lab, k["exp_id"])


def test_sin_forms_listo_no_se_registra_folio(lab, monkeypatch):
    h = lab.ops["op1"]
    k = preparar_coincidencia(lab, h)
    # nunca se autorrellenó: no hay «lista para confirmación»
    r = folio(lab, h, k["rid"], "DIA-20991231-99999Z")
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "FORMS_NO_PREPARO", r.text
    # Forms con incidencias: la operación NO queda lista
    import sys
    from .test_autorrelleno_forms import ESPIA
    monkeypatch.setenv("MONITOREO_FORMS_COMANDO", json.dumps([sys.executable, str(ESPIA), str(lab.espia), "incidencias"]))
    assert pulsar(lab, k["aid"], h).status_code == 202
    fin = esperar(lab, k["aid"], h)
    assert fin["resultado_operacion"] == cz.SIN_PREPARAR, fin
    assert folio(lab, h, k["rid"], "DIA-20991231-99999Z").json()["detail"]["codigo"] == "FORMS_NO_PREPARO"
    assert "OPERACION_FORMS_SIN_PREPARAR" in _tipos(lab, k["exp_id"])


def test_perfil_con_fecha_y_sucursal_las_entrega_a_forms(lab, monkeypatch):
    """Un formulario de una pantalla con fecha y sucursal (p. ej. un portal externo autorizado): si su perfil
    aprobado las declara, la coincidencia viaja en la solicitud a Forms."""
    m = json.loads(ad.Path(__file__).resolve().parents[1].joinpath(
        "monitoreo/autorrelleno/perfil_portal_laboratorio_dia.json").read_text(encoding="utf-8"))
    campos = sorted(list(m["campos"]) + [cz.CAMPO_FECHA, cz.CAMPO_SUCURSAL])
    monkeypatch.setenv("MONITOREO_AUTORRELLENO_PERFIL", str(_pin(lab.tmp, lab.portal, campos=campos)))
    h = lab.ops["op1"]
    k = preparar_coincidencia(lab, h)
    assert pulsar(lab, k["aid"], h).status_code == 202
    esperar(lab, k["aid"], h)
    sol = json.loads((lab.espia / "solicitud.json").read_text(encoding="utf-8"))
    assert sol["campos"][cz.CAMPO_FECHA] == k["fecha"] and sol["campos"][cz.CAMPO_SUCURSAL] == "NOR-1"


def test_sin_coincidencia_el_autorrelleno_sigue_como_antes(lab):
    from .test_autorrelleno_forms import atencion
    h = lab.ops["op1"]
    a, _ = atencion(lab, h)
    st = lab.c.get(f"/api/autorrelleno/atenciones/{a['id']}", headers=h).json()
    assert st["coincidencia"] is None
    assert pulsar(lab, a["id"], h).status_code == 202
    fin = esperar(lab, a["id"], h)
    assert fin["estado"] == "PREPARADO" and fin["coincidencia"] is None and fin["resultado_operacion"] == ""


def test_folio_sin_adaptador_de_verificacion_va_a_confirmacion_asistida(lab):
    h = lab.ops["op1"]
    k = preparar_coincidencia(lab, h)
    assert pulsar(lab, k["aid"], h).status_code == 202
    esperar(lab, k["aid"], h)
    with lab.db.session() as s:
        s.get(Reservacion, k["rid"]).contrato = "portal_sin_api"
    r = folio(lab, h, k["rid"], "DIA-20991231-99999Z")
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "SIN_VERIFICACION_AUTORIZADA", r.text
