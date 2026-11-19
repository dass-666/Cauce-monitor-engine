"""v2.3 · Huecos cerrados para recorrer Ventanilla de principio a fin desde la interfaz.

- Dejar a la persona en el plan del día desde su atención (`POST /api/ventanilla/atenciones/{id}/plan`).
- «Calendario del laboratorio»: el panel maneja el portal PROPIO en 127.0.0.1 solo con su token de control.

Portal de laboratorio como proceso real (127.0.0.1); SQLite y PostgreSQL (fixtures de test_ventanilla)."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from monitoreo.models import Atencion, PlanElemento

from .test_planificador import _hoy, _plan
from .test_ventanilla import con_expediente, lab, portal, solicitud  # noqa: F401  (fixtures)


def _al_plan(lab, h, aid, **body):
    return lab.c.post(f"/api/ventanilla/atenciones/{aid}/plan", headers=h, json=body)


def test_sin_plan_abierto_no_se_toca_nada(lab):
    h = lab.ops["op1"]
    a = con_expediente(lab, h)
    r = _al_plan(lab, h, a["id"])
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "SIN_PLAN", r.text
    v = lab.c.get(f"/api/ventanilla/atenciones/{a['id']}", headers=h).json()
    assert v["abierta"] and solicitud(lab, a["expediente"]["id"])[1] == "ASIGNADO"


def test_la_persona_queda_en_el_plan_y_la_cola_general_no_la_toma(lab):
    h = lab.ops["op1"]
    pid = _plan(lab)
    a = con_expediente(lab, h)
    eid = a["expediente"]["id"]
    lab.c.post(f"/api/expedientes/{eid}/preparar", headers=h).raise_for_status()   # formulario ya preparado
    desde, hasta = _hoy() + timedelta(days=40), _hoy() + timedelta(days=50)
    r = _al_plan(lab, h, a["id"], fechas_desde=desde.isoformat(), fechas_hasta=hasta.isoformat(), prioridad=2)
    assert r.status_code == 200, r.text
    out = r.json()
    assert not out["abierta"] and out["estado"] == "SIN_DISPONIBILIDAD" and out["resultado"] == "EN_PLAN_DEL_DIA"
    el = out["plan"]["elemento"]
    assert el["estado"] == "EN_ESPERA" and el["prioridad"] == 2
    assert el["fechas_aceptables"][0] == desde.isoformat() and el["fechas_aceptables"][-1] == hasta.isoformat()
    assert solicitud(lab, eid)[:2] == ("EN_PLAN", "LISTO")
    lab.ciclo()                                   # la cola general NO lo asigna: su única autoridad es el plan
    assert solicitud(lab, eid)[:2] == ("EN_PLAN", "LISTO")
    with lab.db.session() as s:
        assert s.scalar(select(PlanElemento.estado).where(PlanElemento.plan_id == pid,
                                                          PlanElemento.expediente_id == eid)) == "EN_ESPERA"
    t = lab.c.get(f"/api/expedientes/{eid}/trazabilidad", headers=lab.h).json()
    tipos = [x["tipo"] for x in t["linea_de_tiempo"]]
    assert "PLAN_AGREGADO" in tipos and "RESERVACION_DESCARTADA" in tipos      # el formulario preparado se descartó
    assert [x["resultado"] for x in t["atenciones"]] == ["EN_PLAN_DEL_DIA"]
    d = lab.c.get("/api/ventanilla", headers=h).json()
    assert d["mias"]["sin_cita"] == 1 and d["mias"]["con_cita"] == 0


def test_fechas_invalidas_o_plan_completo_dejan_la_atencion_intacta(lab):
    h = lab.ops["op1"]
    pid = _plan(lab, capacidad=1)
    a = con_expediente(lab, h)
    r = _al_plan(lab, h, a["id"], fechas_desde=(_hoy() - timedelta(days=2)).isoformat())
    assert r.status_code == 422, r.text
    assert solicitud(lab, a["expediente"]["id"])[1] == "ASIGNADO"
    assert _al_plan(lab, h, a["id"]).status_code == 200                       # ocupa el único lugar
    b = con_expediente(lab, h)
    r = _al_plan(lab, h, b["id"])
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "PLAN_COMPLETO", r.text
    with lab.db.session() as s:
        assert s.get(Atencion, b["id"]).cerrada_en is None
    assert solicitud(lab, b["expediente"]["id"])[1] == "ASIGNADO"
    # el administrador amplía y entonces sí entra
    lab.c.post(f"/api/planes/{pid}/ampliar", headers=lab.h, json={"capacidad": 2}).raise_for_status()
    assert _al_plan(lab, h, b["id"]).status_code == 200


def test_otro_operador_no_puede_mandar_al_plan_una_atencion_ajena(lab):
    _plan(lab)
    a = con_expediente(lab, lab.ops["op1"])
    assert _al_plan(lab, lab.ops["op2"], a["id"]).status_code == 404
    assert _al_plan(lab, lab.ops["op1"], 999999).status_code == 404


def test_atencion_sin_expediente_no_va_al_plan(lab):
    _plan(lab)
    h = lab.ops["op1"]
    a = lab.c.post("/api/ventanilla/atenciones", headers=h, json={"clave": "x" * 20}).json()
    assert _al_plan(lab, h, a["id"]).status_code == 409


# ---------------------------------------------------------------- calendario del laboratorio
def test_calendario_del_laboratorio_solo_con_token_y_portal_local(lab, monkeypatch):
    monkeypatch.delenv("MONITOREO_PORTAL_TOKEN_CONTROL", raising=False)
    st = lab.c.get("/api/laboratorio/portal", headers=lab.h).json()
    assert st["disponible"] is False
    q = {"sucursal": "NOR-1", "tramite": "alta"}
    assert lab.c.get("/api/laboratorio/portal/calendario", headers=lab.h, params=q).status_code == 409
    monkeypatch.setenv("MONITOREO_PORTAL_TOKEN_CONTROL", lab.portal.token_control)
    assert lab.c.get("/api/laboratorio/portal", headers=lab.h).json()["disponible"] is True
    # un operador no maneja el calendario
    assert lab.c.get("/api/laboratorio/portal", headers=lab.ops["op1"]).status_code == 403
    assert lab.c.post("/api/laboratorio/portal/calendario/habilitar", headers=lab.ops["op1"],
                      json={**q, "fecha": "2030-01-01"}).status_code == 403
    # un portal que no está en este equipo: el token nunca sale
    monkeypatch.setenv("MONITOREO_PORTAL_URL", "http://10.0.0.5:8200")
    assert lab.c.get("/api/laboratorio/portal", headers=lab.h).json()["disponible"] is False
    assert lab.c.get("/api/laboratorio/portal/calendario", headers=lab.h, params=q).status_code == 409


def test_abrir_y_agotar_un_dia_desde_el_panel(lab, monkeypatch):
    monkeypatch.setenv("MONITOREO_PORTAL_TOKEN_CONTROL", lab.portal.token_control)
    hoy = lab.c.get("/api/laboratorio/portal/calendario", headers=lab.h,
                    params={"sucursal": "NOR-1", "tramite": "alta"}).json()["hoy"]
    y, m = int(hoy[:4]), int(hoy[5:7])
    y, m = (y + 1, m - 9) if m > 9 else (y, m + 3)          # tres meses adelante
    mes = f"{y:04d}-{m:02d}"
    fecha = f"{mes}-20"
    q = {"sucursal": "NOR-1", "tramite": "alta", "mes": mes}
    r = lab.c.post("/api/laboratorio/portal/calendario/habilitar", headers=lab.h,
                   json={"sucursal": "NOR-1", "tramite": "alta", "fecha": fecha, "capacidad": 2})
    assert r.status_code == 200, r.text
    dia = next(x for x in lab.c.get("/api/laboratorio/portal/calendario", headers=lab.h, params=q).json()["dias"]
               if x["fecha"] == fecha)
    assert dia["estado"] == "DISPONIBLE" and dia["libres"] >= 2
    assert lab.c.post("/api/laboratorio/portal/calendario/agotar", headers=lab.h,
                      json={"sucursal": "NOR-1", "tramite": "alta", "fecha": fecha}).status_code == 200
    dia = next(x for x in lab.c.get("/api/laboratorio/portal/calendario", headers=lab.h, params=q).json()["dias"]
               if x["fecha"] == fecha)
    assert dia["estado"] == "AGOTADO"
    # sucursal inexistente: el portal lo rechaza y el panel lo dice (sin 500)
    r = lab.c.post("/api/laboratorio/portal/calendario/habilitar", headers=lab.h,
                   json={"sucursal": "NOPE-9", "tramite": "alta", "fecha": fecha})
    assert r.status_code == 422, r.text
    regs = lab.c.get("/api/laboratorio/portal/registros", headers=lab.h, params={"cuantos": 3, "tramite": "alta"}).json()
    assert len(regs["registros"]) == 3 and all(x["tramite"] == "alta" for x in regs["registros"])
    with lab.db.session() as s:
        from monitoreo.models import AuditLog
        assert s.scalar(select(AuditLog.id).where(AuditLog.action == "laboratorio_calendario")) is not None


def test_bat_de_la_23_son_ascii_y_crlf():
    from pathlib import Path
    raiz = Path(__file__).resolve().parent.parent
    for nombre in ("ventanilla_laboratorio.bat", "recorrido_ventanilla.bat"):
        b = (raiz / nombre).read_bytes()
        b.decode("ascii")
        assert b"\r\n" in b and b.count(b"\n") == b.count(b"\r\n"), nombre
    assert "MONITOREO_SIN_PAUSA" in (raiz / "recorrido_ventanilla.bat").read_text(encoding="ascii")
