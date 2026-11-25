"""Folio DECLARADO (portal sin vía autorizada de verificación): PENDIENTE_DE_VERIFICACION, nunca CONFIRMADO."""
from monitoreo.autorrelleno import coincidencia as cz
from monitoreo.models import CapturaManual, Expediente, PlanElemento, Reservacion

from .test_autorrelleno_coincidencia import esperar, folio, lab, portal, preparar_coincidencia, pulsar  # noqa: F401


def declarar(lab, h, rid, f, f2=None, visto=True):
    return lab.c.post(f"/api/autorrelleno/reservaciones/{rid}/folio-declarado", headers=h,
                      json={"folio": f, "folio_confirmacion": f2 or f, "folio_visto": visto})


def lista_sin_via(lab, h):
    k = preparar_coincidencia(lab, h)
    assert pulsar(lab, k["aid"], h).status_code == 202
    esperar(lab, k["aid"], h)
    with lab.db.session() as s:
        s.get(Reservacion, k["rid"]).contrato = "portal_sin_api"       # este portal no tiene vía de verificación
    return k


def nada_confirmado(lab, k):
    with lab.db.session() as s:
        assert s.get(Reservacion, k["rid"]).estado == "PREPARADA"
        assert s.get(PlanElemento, k["elid"]).estado == "OPERACION_PREPARADA"
        assert s.get(Expediente, k["exp_id"]).estado != "RESERVACION_CONFIRMADA"


def test_declarar_queda_pendiente_de_verificacion_con_su_constancia(lab):
    h = lab.ops["op1"]
    k = lista_sin_via(lab, h)
    assert declarar(lab, h, k["rid"], "ABC-1234", visto=False).json()["detail"]["codigo"] == "FOLIO_NO_VISTO"
    assert declarar(lab, h, k["rid"], "ABC-1234", "ABC-9999").json()["detail"]["codigo"] == "FOLIOS_DISTINTOS"
    r = declarar(lab, h, k["rid"], " abc-1234 ")
    assert r.status_code == 200, r.text
    d = r.json()["declarado"]
    assert d["estado"] == "PENDIENTE_DE_VERIFICACION" and d["folio"] == "ABC-1234" and d["operador"] == "op1"
    assert d["procedencia"] and d["portal"] and d["registrado_en"] and d["es_cita_confirmada"] is False
    nada_confirmado(lab, k)
    kk = lab.c.get(f"/api/autorrelleno/atenciones/{k['aid']}", headers=h).json()["coincidencia"]
    assert kk["confirmada"] is False and kk["folio_declarado"]["estado"] == "PENDIENTE_DE_VERIFICACION"
    tl = [x["tipo"] for x in lab.c.get(f"/api/expedientes/{k['exp_id']}/trazabilidad",
                                       headers=lab.h).json()["linea_de_tiempo"]]
    assert "FOLIO_DECLARADO" in tl and "RESERVACION_CONFIRMADA" not in tl


def test_sin_duplicados(lab):
    h = lab.ops["op1"]
    k = lista_sin_via(lab, h)
    assert declarar(lab, h, k["rid"], "ABC-1234").status_code == 200
    r = declarar(lab, h, k["rid"], "ABC-1234")                       # repetir el mismo: idempotente
    assert r.status_code == 200 and r.json()["repetido"] is True
    r = declarar(lab, h, k["rid"], "OTRO-5678")                      # otro folio sobre la misma operación: no
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "YA_DECLARADO"
    with lab.db.session() as s:
        assert s.query(CapturaManual).count() == 1


def test_verificacion_posterior_sin_via_sigue_pendiente(lab):
    h = lab.ops["op1"]
    k = lista_sin_via(lab, h)
    declarar(lab, h, k["rid"], "ABC-1234")
    r = lab.c.post(f"/api/autorrelleno/reservaciones/{k['rid']}/folio-declarado/verificar", headers=h)
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "SIN_VERIFICACION_AUTORIZADA"
    assert r.json()["detail"]["declarado"]["estado"] == "PENDIENTE_DE_VERIFICACION"
    assert r.json()["detail"]["declarado"]["ultimo_intento"]
    nada_confirmado(lab, k)


def test_con_via_autorizada_no_se_declara(lab):
    h = lab.ops["op1"]
    k = preparar_coincidencia(lab, h)
    assert pulsar(lab, k["aid"], h).status_code == 202
    esperar(lab, k["aid"], h)
    r = declarar(lab, h, k["rid"], "ABC-1234")
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "VERIFICACION_DISPONIBLE"


def test_verificacion_posterior_con_via_confirma_solo_si_el_portal_coincide(lab):
    """Declarado cuando no había vía; después existe una vía autorizada: un folio que el portal no tiene queda en
    DISCREPANCIA y nada se confirma."""
    h = lab.ops["op1"]
    k = lista_sin_via(lab, h)
    declarar(lab, h, k["rid"], "DIA-20991231-99999Z")
    with lab.db.session() as s:
        s.get(Reservacion, k["rid"]).contrato = next(iter(cz.ADAPTADOR_POR_CONTRATO))   # ya hay vía
    r = lab.c.post(f"/api/autorrelleno/reservaciones/{k['rid']}/folio-declarado/verificar", headers=h)
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "FOLIO_INEXISTENTE", r.text
    assert r.json()["detail"]["declarado"]["estado"] == "DISCREPANCIA"
    nada_confirmado(lab, k)


def test_otro_operador_no_declara_en_operacion_ajena(lab):
    k = lista_sin_via(lab, lab.ops["op1"])
    otro = next(v for n, v in lab.ops.items() if n != "op1")
    assert declarar(lab, otro, k["rid"], "ABC-1234").status_code == 404
