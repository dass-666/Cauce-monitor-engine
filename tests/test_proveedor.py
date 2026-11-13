"""v2.0 · Identidad del proveedor por instancia: dos instancias que comparten base de datos pero
apuntan a portales DISTINTOS no deben recuperar (ni confirmar) reservaciones del otro portal.

Riesgo hallado con scripts/resistencia_v20.py: un motor apuntando a otro portal preguntó por la
clave, ese portal no la conocía y la confirmación se REABRIÓ aunque el portal correcto sí la tenía."""
from __future__ import annotations

from datetime import timedelta

import pytest

from monitoreo.captura import reservaciones as rs
from monitoreo.db import utcnow
from monitoreo.models import Reservacion
from portal_reservas.proceso import PortalProceso

from .captura_util import MANUALES
from .test_captura_dia import asignado, lab, portal  # noqa: F401  (fixtures)


@pytest.fixture(scope="module")
def portal_b(tmp_path_factory):
    """Un SEGUNDO portal de prueba (otra instancia, otros datos): el «portal equivocado»."""
    d = tmp_path_factory.mktemp("portal_b")
    p = PortalProceso(d / "estado.json", extra=["--zona-horaria", "America/Mexico_City"])
    p.iniciar()
    yield p
    p.detener()


def _confirmada_pero_escritura_perdida(lab):
    """Confirma de verdad en el portal A y deja la reservación como si el motor se hubiera caído
    antes de guardar el resultado: CONFIRMANDO desde hace rato (el caso que la recuperación resuelve)."""
    eid, u, h = asignado(lab)
    rid, _ = lab.hasta_fecha(eid, h, manuales=MANUALES)
    r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
    assert r.status_code == 200, r.text
    folio = r.json()["reservacion"]["folio"]
    with lab.db.session() as s:
        res = s.get(Reservacion, rid)
        res.estado, res.folio, res.evidencia = "CONFIRMANDO", None, {}
        res.confirmando_desde = utcnow() - timedelta(minutes=5)
    return rid, folio, h


def test_otra_instancia_con_otro_portal_no_reabre_lo_que_el_portal_correcto_confirmo(lab, portal_b, monkeypatch):
    rid, folio, h = _confirmada_pero_escritura_perdida(lab)
    # «Otra instancia»: misma base, pero configurada contra el portal B
    monkeypatch.setenv("MONITOREO_PORTAL_URL", portal_b.base)
    monkeypatch.setenv("MONITOREO_PORTAL_TOKEN", portal_b.token)
    out = rs.recuperar(lab.db, utcnow())
    with lab.db.session() as s:
        res = s.get(Reservacion, rid)
        assert res.estado == "CONFIRMANDO", f"se reabrió con el portal equivocado: {res.estado} {out}"
    assert out.get("bloqueadas_otro_proveedor") == 1
    # tampoco puede confirmarla de nuevo ni tocarla por el portal equivocado
    with lab.db.session() as s:
        s.get(Reservacion, rid).estado = "FECHA_ELEGIDA"
    r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
    assert r.status_code == 409 and "otro proveedor" in r.text.lower(), r.text
    with lab.db.session() as s:
        s.get(Reservacion, rid).estado = "CONFIRMANDO"
    # ni prepara trabajo nuevo desde la instancia equivocada
    eid2, _, h2 = asignado(lab)
    r = lab.c.post(f"/api/expedientes/{eid2}/preparar", headers=h2)
    assert r.status_code == 409 and "portal distinto" in r.text, r.text
    # La instancia con el portal CORRECTO la recupera como confirmada
    monkeypatch.setenv("MONITOREO_PORTAL_URL", lab.portal.base)
    monkeypatch.setenv("MONITOREO_PORTAL_TOKEN", lab.portal.token)
    out = rs.recuperar(lab.db, utcnow())
    with lab.db.session() as s:
        res = s.get(Reservacion, rid)
        assert res.estado == "CONFIRMADA" and res.folio == folio, (res.estado, out)
    assert lab.portal.ctl("GET", "/control/estado")["confirmadas"] >= 1


def test_identidad_persistente_deteccion_y_adopcion_segura(lab, portal_b, monkeypatch):
    from monitoreo import proveedor as pv
    from monitoreo.diagnostico import run
    base_a = pv.identidad_local()
    assert base_a["id"] == pv.identidad_local()["id"]                        # determinista
    pv.registrar_instancia(lab.db, "inst-A", utcnow())
    assert pv.estado(lab.db)["compatible"] is True
    # localhost y 127.0.0.1 son el mismo destino
    monkeypatch.setenv("MONITOREO_PORTAL_URL", lab.portal.base.replace("127.0.0.1", "localhost"))
    assert pv.identidad_local()["id"] == base_a["id"]
    # otra instancia, otro portal: detectada
    monkeypatch.setenv("MONITOREO_PORTAL_URL", portal_b.base)
    assert pv.identidad_local()["id"] != base_a["id"]
    pv.registrar_instancia(lab.db, "inst-B", utcnow())
    e = pv.estado(lab.db)
    assert e["compatible"] is False and e["identidad_base"]["id"] == base_a["id"]
    assert {i["instancia"] for i in e["instancias_activas"]} == {"inst-A", "inst-B"} and e["instancias_discrepantes"]
    diag = run(lab.db)
    assert any(i["clave"] == "proveedor:incompatible" and i["estado"] == "CRITICO" for i in diag["comprobaciones"])
    # adoptar la identidad nueva (sin reservaciones a medias del proveedor anterior): permitido y auditado
    r = lab.c.post("/api/proveedor/adoptar", headers=lab.h, json={"confirmo": True})
    assert r.status_code == 200, r.text                                     # sin pendientes: se permite
    assert pv.estado(lab.db)["compatible"] is True
    r = lab.c.get("/api/proveedor", headers=lab.h).json()
    assert r["identidad_base"]["id"] == pv.identidad_local()["id"]
    assert lab.c.post("/api/proveedor/adoptar", headers=lab.ops["op1"], json={"confirmo": True}).status_code == 403


def test_adoptar_se_niega_con_reservaciones_a_medias_del_proveedor_anterior(lab, portal_b, monkeypatch):
    rid, folio, h = _confirmada_pero_escritura_perdida(lab)
    monkeypatch.setenv("MONITOREO_PORTAL_URL", portal_b.base)
    monkeypatch.setenv("MONITOREO_PORTAL_TOKEN", portal_b.token)
    r = lab.c.post("/api/proveedor/adoptar", headers=lab.h, json={"confirmo": True})
    assert r.status_code == 409 and r.json()["detail"]["pendientes"] == 1, r.text
