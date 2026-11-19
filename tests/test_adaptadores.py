"""v2.0 · P2 · Contrato de adaptadores, adaptador simulado, de laboratorio (portal real en otro
proceso), adaptador sin integración y captura manual con verificación."""
from __future__ import annotations

import json
from datetime import date

import pytest

from monitoreo import adaptadores
from monitoreo.adaptadores import base
from monitoreo.adaptadores.base import DatoInvalido, NoAutorizado, normalizar_fecha
from monitoreo.adaptadores.laboratorio import AdaptadorLaboratorio
from monitoreo.adaptadores.simulado import AdaptadorSimulado
from monitoreo.adaptadores.sin_integracion import AdaptadorSinIntegracion
from monitoreo.models import CapturaManual, ConexionEvento, Expediente

from .captura_util import MANUALES
from .test_captura_dia import api_portal, asignado, lab, portal  # noqa: F401  (fixtures)

DATOS_FICTICIOS = {"nombre": "Persona Ficticia Zeta", "correo": "zeta@ejemplo.test", "telefono": "5550000001",
                   "fecha_nacimiento": "1990-01-01", "region": "NORTE", "tipo_tramite": "PRIMERA_VEZ",
                   "id_registro_previo": "REG-FICT-1"}


@pytest.fixture
def simulado():
    ad = AdaptadorSimulado(hoy=date(2026, 11, 13))
    adaptadores.registrar("simulado", ad)
    yield ad
    adaptadores.registrar("simulado", AdaptadorSimulado())


# ---------------------------------------------------------------- contrato común
@pytest.mark.parametrize("ad", [AdaptadorSimulado(), AdaptadorLaboratorio(), AdaptadorSinIntegracion()],
                         ids=["simulado", "laboratorio", "oficial_manual"])
def test_todo_adaptador_cumple_el_contrato(ad):
    cap = ad.capacidades()
    assert cap.nombre in adaptadores.NOMBRES and cap.captura_manual
    if cap.confirmar or cap.observar or cap.verificar:
        assert cap.autorizado, "observar, verificar o confirmar exigen una integración autorizada"
    # Preparación: dice qué falta, pero su resultado NUNCA contiene los datos de la persona.
    p = ad.preparar(DATOS_FICTICIOS)
    texto = json.dumps(p.como_dict(), ensure_ascii=False)
    for v in DATOS_FICTICIOS.values():
        assert len(v) < 6 or v not in texto
    assert ad.preparar({}).faltantes, "sin datos tiene que faltar algo"
    # 429/403 detienen la operación; 5xx es incierto; nunca se convierte en algo reintentable.
    r429 = ad.interpretar(429, {"Retry-After": "120"})
    assert r429.tipo == base.RESTRICCION and r429.detener and r429.reintentar_tras_s == 120
    assert ad.interpretar(403).detener and ad.interpretar(403).tipo == base.RESTRICCION
    assert ad.interpretar(503).tipo == base.INCIERTO and not ad.interpretar(503).detener
    assert ad.interpretar(401).tipo == base.NO_AUTORIZADO
    assert ad.interpretar(409, cuerpo={"codigo": "DIA_AGOTADO"}).codigo == "DIA_AGOTADO"
    assert ad.interpretar(200, {"X-Transaccion-Id": "tx-9"}).transaccion_id == "tx-9"


def test_normalizacion_de_fechas_no_adivina():
    assert normalizar_fecha("2026-11-19") == normalizar_fecha("19/11/2026") == normalizar_fecha("19-11-2026") \
        == normalizar_fecha("19.11.2026") == normalizar_fecha(date(2026, 11, 19)) == "2026-11-19"
    for malo in ("11/31/2026", "2026-02-30", "mañana", "", "19 nov 2026"):
        with pytest.raises(DatoInvalido):
            normalizar_fecha(malo)


def test_sin_integracion_no_se_conecta_a_nada():
    ad = AdaptadorSinIntegracion()
    cap = ad.capacidades()
    assert not (cap.observar or cap.confirmar or cap.verificar or cap.autorizado or cap.red)
    with pytest.raises(NoAutorizado):
        ad.observar({})
    with pytest.raises(NoAutorizado):
        ad.confirmar({"fecha": "2026-11-19"})
    v = ad.verificar("ABC-123", "2026-11-19", None)
    assert v.resultado.tipo == base.NO_AUTORIZADO and v.coincide is None
    assert any("sitio oficial" in x for x in ad.preparar(DATOS_FICTICIOS).pasos_manuales)


def test_simulado_restriccion_detiene_sin_reintento_y_confirmar_es_idempotente(simulado):
    obs = simulado.observar({"sucursal": "SIM-1"})
    assert obs.resultado.tipo == base.OK and obs.dias and all(d.sucursal == "SIM-1" for d in obs.dias)
    libre = next(d for d in obs.dias if d.libres)
    simulado.proximo_fallo = "429"
    antes = simulado.llamadas
    r = simulado.confirmar({"sucursal": "SIM-1", "fecha": libre.fecha, "clave": "k-1"})
    assert r.tipo == base.RESTRICCION and r.detener and simulado.llamadas == antes + 1
    assert not simulado._folios, "con restricción no se creó nada"
    r1 = simulado.confirmar({"sucursal": "SIM-1", "fecha": libre.fecha, "clave": "k-1"})
    r2 = simulado.confirmar({"sucursal": "SIM-1", "fecha": libre.fecha, "clave": "k-1"})
    assert r1.tipo == r2.tipo == base.OK and r1.transaccion_id == r2.transaccion_id and len(simulado._folios) == 1
    agotado = next(d for d in obs.dias if d.libres == 0)
    assert simulado.confirmar({"sucursal": "SIM-1", "fecha": agotado.fecha, "clave": "k-2"}).tipo == base.RECHAZO


# ---------------------------------------------------------------- captura manual (API)
def test_captura_manual_declarada_verificada_discrepancia_y_restriccion(lab, simulado):  # noqa: F811
    h = lab.ops["op1"]
    folio = simulado.registrar_externo("SIM-1", "2026-11-20")      # el operador lo hizo en el flujo «oficial»
    c = lab.c
    cuerpo = {"adaptador": "simulado", "folio": folio.lower(), "fecha": "20/11/2026", "sucursal": "sim-1"}
    assert c.post("/api/capturas-manuales", headers=h, json=cuerpo).status_code == 422      # sin «folio visto»
    r = c.post("/api/capturas-manuales", headers=h, json={**cuerpo, "folio_visto": True})
    assert r.status_code == 201, r.text
    v = r.json()
    assert v["estado"] == "VERIFICADA" and v["folio"] == folio and v["fecha"] == "2026-11-20"
    assert v["es_cita_confirmada_por_el_motor"] is False
    assert c.post("/api/capturas-manuales", headers=h, json={**cuerpo, "folio_visto": True}).status_code == 409

    # fecha distinta a la del proveedor: DISCREPANCIA (no se da por buena)
    f2 = simulado.registrar_externo("SIM-2", "2026-11-21")
    r = c.post("/api/capturas-manuales", headers=h, json={"adaptador": "simulado", "folio": f2, "fecha": "2026-11-22",
                                                           "folio_visto": True})
    assert r.json()["estado"] == "DISCREPANCIA" and r.json()["detalle"]["verificacion"]["fecha_proveedor"] == "2026-11-21"

    # el proveedor restringe: queda DECLARADA, se anota y NO se reintenta solo
    f3 = simulado.registrar_externo("SIM-1", "2026-11-23")
    simulado.proximo_fallo = "429"
    antes = simulado.llamadas
    r = c.post("/api/capturas-manuales", headers=h, json={"adaptador": "simulado", "folio": f3, "fecha": "2026-11-23",
                                                           "folio_visto": True})
    d = r.json()
    assert d["estado"] == "DECLARADA" and d["detalle"]["verificacion"]["detenida"] is True
    assert simulado.llamadas == antes + 1
    lab.ciclo()                                                      # el ciclo del motor no la reintenta
    assert simulado.llamadas == antes + 1
    r = c.post(f"/api/capturas-manuales/{d['id']}/verificar", headers=h)   # una persona lo pide más tarde
    assert r.json()["estado"] == "VERIFICADA"

    # formato de folio inválido y folio inexistente
    assert c.post("/api/capturas-manuales", headers=h, json={"adaptador": "simulado", "folio": "X", "fecha": "2026-11-23",
                                                              "folio_visto": True}).status_code == 422
    r = c.post("/api/capturas-manuales", headers=h, json={"adaptador": "simulado", "folio": "SIM-999999",
                                                           "fecha": "2026-11-23", "folio_visto": True})
    assert r.json()["estado"] == "DISCREPANCIA" and r.json()["detalle"]["verificacion"]["existe_en_proveedor"] is False

    # sin integración: queda SIN_VERIFICACION (declarada por el operador, nunca «confirmada»)
    r = c.post("/api/capturas-manuales", headers=h, json={"adaptador": "oficial_manual", "folio": "ABC-2026-77",
                                                           "fecha": "2026-11-25", "folio_visto": True})
    assert r.status_code == 201 and r.json()["estado"] == "SIN_VERIFICACION"

    # el otro operador no ve las capturas de op1; el administrador sí
    assert c.get("/api/capturas-manuales", headers=lab.ops["op2"]).json()["capturas"] == []
    assert len(c.get("/api/capturas-manuales", headers=lab.h).json()["capturas"]) == 5
    assert c.post(f"/api/capturas-manuales/{d['id']}/verificar", headers=lab.ops["op2"]).status_code == 404


def test_discrepancia_con_expediente_lo_manda_a_revision(lab, simulado):  # noqa: F811
    eid, u, h = asignado(lab)
    f = simulado.registrar_externo("SIM-1", "2026-11-20")
    r = lab.c.post("/api/capturas-manuales", headers=h, json={"adaptador": "simulado", "folio": f, "fecha": "2026-11-21",
                                                               "expediente_id": eid, "folio_visto": True})
    assert r.status_code == 201 and r.json()["estado"] == "DISCREPANCIA"
    with lab.db.session() as s:
        assert s.get(Expediente, eid).estado == "REQUIERE_REVISION"
    otro = lab.ops["op2" if u == "op1" else "op1"]
    assert lab.c.post("/api/capturas-manuales", headers=otro, json={
        "adaptador": "simulado", "folio": simulado.registrar_externo("SIM-1", "2026-11-20"), "fecha": "2026-11-20",
        "expediente_id": eid, "folio_visto": True}).status_code == 404


# ---------------------------------------------------------------- adaptador de laboratorio (portal real)
def test_laboratorio_observa_verifica_y_se_detiene_ante_429(lab):  # noqa: F811
    ad = AdaptadorLaboratorio()
    obs = ad.observar({"region": "NORTE", "sucursal": "NOR-1"})
    assert obs.resultado.tipo == base.OK and obs.dias
    assert all(d.sucursal == "NOR-1" and len(d.fecha) == 10 for d in obs.dias)

    # una cita real del laboratorio, confirmada por el flujo del motor, y verificada por el adaptador
    eid, u, h = asignado(lab)
    rid, dia = lab.hasta_fecha(eid, h, manuales=MANUALES)
    r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
    assert r.status_code == 200, r.text
    folio = r.json()["reservacion"]["folio"]
    v = ad.verificar(folio, dia["fecha"], dia["sucursal"])
    assert v.coincide is True and v.estado_proveedor == "CONFIRMADA"
    assert "identificador" not in json.dumps(v.evidencia)            # sin datos de la persona
    assert ad.verificar(folio, "2030-01-01", None).coincide is False
    r = lab.c.post("/api/capturas-manuales", headers=h, json={"adaptador": "laboratorio", "folio": folio,
                                                               "fecha": dia["fecha"], "folio_visto": True})
    assert r.status_code == 201 and r.json()["estado"] == "VERIFICADA", r.text

    # el portal limita su API: el adaptador se detiene (una sola petición) y queda registrado
    lab.portal.fallos(modo="limite", pantallas=["api"])
    try:
        obs = ad.observar({"region": "NORTE"})
        aplicados = lab.portal.ctl("GET", "/control/estado")["fallos"].get("aplicados", {})
    finally:
        lab.portal.fallos(reset=True)
    assert obs.resultado.tipo == base.RESTRICCION and obs.resultado.detener and not obs.dias
    assert aplicados.get("limite") == 1
    from monitoreo import conexiones
    conexiones.volcar(lab.db)
    with lab.db.session() as s:
        assert s.query(ConexionEvento).filter(ConexionEvento.evento == "RESTRICCION").count() >= 1

    # API caída (503): incierto, no restricción
    lab.portal.ctl("POST", "/control/api", caida=True)
    try:
        assert ad.observar({}).resultado.tipo == base.INCIERTO
    finally:
        lab.portal.ctl("POST", "/control/api", caida=False)


def test_api_de_adaptadores(lab, simulado):  # noqa: F811
    lst = lab.c.get("/api/adaptadores", headers=lab.ops["op1"]).json()["adaptadores"]
    assert {a["nombre"] for a in lst} == set(adaptadores.NOMBRES)
    assert lab.c.post("/api/adaptadores/simulado/observar", headers=lab.ops["op1"], json={}).status_code == 403
    assert lab.c.post("/api/adaptadores/oficial_manual/observar", headers=lab.h, json={}).status_code == 409
    assert lab.c.post("/api/adaptadores/simulado/observar", headers=lab.h, json={}).json()["resultado"]["tipo"] == "OK"
    assert lab.c.post("/api/adaptadores/nada/observar", headers=lab.h, json={}).status_code == 404
    eid, u, h = asignado(lab)
    p = lab.c.post("/api/adaptadores/oficial_manual/preparar", headers=h, json={"expediente_id": eid})
    assert p.status_code == 200 and p.json()["pasos_manuales"]
    with lab.db.session() as s:
        e = s.get(Expediente, eid)
        assert e.correo not in p.text and e.telefono not in p.text      # no devuelve datos personales
    with lab.db.session() as s:
        assert s.query(CapturaManual).count() == 0
