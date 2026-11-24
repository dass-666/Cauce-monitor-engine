"""Observador universal (fuentes web por URL, sin código por proveedor) con navegador REAL sobre sitios LOCALES:

- laboratorio_web: página de turnos renderizada por JavaScript, con reloj, anuncio rotatorio y animación (ruido),
  ubicaciones en texto, fechas DD/MM/AAAA, estados en texto y un formulario que nunca debe enviarse;
- el calendario del portal de laboratorio, observado con el MISMO script genérico y solo un perfil.
Se omite si no hay Node + Playwright."""
from __future__ import annotations

import shutil
import subprocess
import time
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from laboratorio_web.servidor import PERFIL_CALENDARIO_PORTAL, PERFIL_TURNOS, SitioTurnos
from monitoreo.config import settings
from monitoreo.db import utcnow
from monitoreo.captura import zona
from monitoreo.models import ObservacionEvento, PlanElemento
from monitoreo.planificador import fuentes_web as fw
from monitoreo.planificador.observador import ObservadorWeb
from portal_reservas.proceso import PortalProceso

from .captura_util import ADMIN_PWD, LabDia


def _playwright() -> bool:
    if not shutil.which("node"):
        return False
    return subprocess.run(["node", "-e", "require('playwright')"], capture_output=True).returncode == 0


navegador = pytest.mark.skipif(not _playwright(), reason="sin Node.js + Playwright")


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    p = PortalProceso(tmp_path / "portal.json", extra=["--zona-horaria", "America/Mexico_City"])
    p.iniciar()
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "admin_password", ADMIN_PWD)
    monkeypatch.setenv("MONITOREO_PORTAL_URL", p.base)
    monkeypatch.setenv("MONITOREO_PORTAL_TOKEN", p.token)
    lab = LabDia(f"sqlite:///{(tmp_path / 'm.db').as_posix()}", p)
    sitio = SitioTurnos().iniciar()
    obs: list = []
    yield lab, p, sitio, obs, tmp_path
    for o in obs:
        o.detener()
    sitio.detener()
    lab.cerrar()
    p.detener()


def _esperar(pred, t=20.0, paso=0.1):
    fin = time.monotonic() + t
    while time.monotonic() < fin:
        v = pred()
        if v:
            return v
        time.sleep(paso)
    return None


def _f(dias: int) -> str:
    return (zona.hoy(utcnow()) + timedelta(days=dias)).isoformat()


def _dmy(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{d}/{m}/{y}"


def _turno(lugar, iso, estado="Disponible", cupo=2, hora="09:30"):
    return {"lugar": lugar, "fecha": _dmy(iso), "hora": hora, "estado": estado, "cupo": cupo}


def _fuente(sitio, nombre="turnos-lab", tramite="alta", **kw):
    return {"nombre": nombre, "url": sitio.url, "tramite": tramite, "perfil": PERFIL_TURNOS, **kw}


def _arrancar(lab, obs, d, tmp_path, atender=False):
    """Como en el motor: se guarda la fuente y el SUPERVISOR de la aplicación la arranca (arrendamiento incluido)."""
    c = fw.configurar(lab.db, d, "admin", utcnow())
    sup = lab.c.app.state.supervisor
    sup.ciclo(utcnow())
    o = sup.activos[c["clave"]]
    assert isinstance(o, ObservadorWeb)
    o.atender = atender
    assert _esperar(lambda: o.stats["vista"].get("dom") == "ACTIVA", 30), o.estado()
    return o


def _eventos(lab, tipo=None):
    with lab.db.session() as s:
        q = select(ObservacionEvento).where(ObservacionEvento.tipo != "VISTA")
        if tipo:
            q = q.where(ObservacionEvento.tipo == tipo)
        return [(e.tipo, e.sucursal, e.fecha, e.estado, e.evidencia) for e in s.scalars(q.order_by(ObservacionEvento.id))]


# ================================================================ validación (sin navegador)
def test_validacion_de_fuentes_y_permisos_de_sitios():
    base = {"nombre": "x1", "url": "http://127.0.0.1:9/turnos.html", "tramite": "alta", "perfil": PERFIL_TURNOS}
    assert fw.validar(base, produccion=False)["autorizacion"] is None
    with pytest.raises(fw.FuenteInvalida, match="producción"):
        fw.validar(base, produccion=True)
    ext = {**base, "url": "https://citas.ejemplo.test/turnos"}
    with pytest.raises(fw.FuenteInvalida, match="MONITOREO_OBSERVADOR_HOSTS"):
        fw.validar(ext, produccion=False, env={})
    env = {"MONITOREO_OBSERVADOR_HOSTS": "citas.ejemplo.test"}
    with pytest.raises(fw.FuenteInvalida, match="autorización escrita"):
        fw.validar(ext, produccion=False, env=env)
    aut = {"ref": "AUT-2026-01", "documento_sha256": "a" * 64, "vigente_hasta": "2099-12-31"}
    assert fw.validar({**ext, "autorizacion": aut}, produccion=False, env=env)["autorizacion"]["host"] == "citas.ejemplo.test"
    with pytest.raises(fw.FuenteInvalida, match="vencida"):
        fw.validar({**ext, "autorizacion": {**aut, "vigente_hasta": "2020-01-01"}}, produccion=False, env=env)
    with pytest.raises(fw.FuenteInvalida, match="https"):
        fw.validar({**ext, "url": "http://citas.ejemplo.test/t", "autorizacion": aut}, produccion=False, env=env)
    with pytest.raises(fw.FuenteInvalida, match="tabla"):
        fw.validar({**base, "perfil": {**PERFIL_TURNOS, "ubicaciones": None}}, produccion=False)
    with pytest.raises(fw.FuenteInvalida, match="sucursal fija"):
        fw.validar({**base, "perfil": PERFIL_CALENDARIO_PORTAL}, produccion=False)
    assert fw.validar({**base, "sucursal": "NOR-1", "perfil": PERFIL_CALENDARIO_PORTAL}, produccion=False)
    with pytest.raises(fw.FuenteInvalida, match="desconocidas"):
        fw.validar({**base, "perfil": {**PERFIL_TURNOS, "enviar": True}}, produccion=False)
    with pytest.raises(fw.FuenteInvalida, match="código de sucursal"):
        fw.validar({**base, "perfil": {**PERFIL_TURNOS, "ubicaciones": {"Oficina": "nor 1"}}}, produccion=False)


# ================================================================ con navegador real
@navegador
def test_vista_previa_reconoce_lo_configurado_e_ignora_lo_demas(entorno):
    lab, p, sitio, obs, tmp = entorno
    sitio.poner(turnos=[_turno("Oficina Norte Centro", _f(30)), _turno("Oficina Norte Centro", _f(30), hora="11:00", cupo=1),
                        _turno("Oficina Sur", _f(31)), _turno("Oficina Norte Poniente", _f(32), estado="Por confirmar"),
                        _turno("Oficina Norte Poniente", _f(33), estado="Sin lugares", cupo=0)])
    r = fw.probar(_fuente(sitio))
    dias = {(d["sucursal"], d["fecha"]): d for d in r["dias"]}
    assert dias[("NOR-1", _f(30))] == {"sucursal": "NOR-1", "fecha": _f(30), "estado": "DISPONIBLE", "libres": 3,
                                       "horas": ["09:30", "11:00"]}
    assert dias[("NOR-2", _f(33))]["estado"] == "AGOTADO"
    assert len(dias) == 2 and sorted(r["no_reconocidos"]) == ["estado", "ubicacion"]
    assert r["errores"] == [] and sitio.envios == 0


@navegador
def test_ruido_no_cuenta_y_un_cambio_real_se_registra_una_vez_con_evidencia(entorno):
    lab, p, sitio, obs, tmp = entorno
    o = _arrancar(lab, obs, _fuente(sitio), tmp)
    time.sleep(4)                                  # reloj cada 100 ms, anuncio cada 300 ms, animación continua
    assert _eventos(lab) == [] and o.stats["lineas"] > 0
    # parpadeo más corto que la ventana de estabilidad (600 ms): no es disponibilidad
    sitio.poner(turnos=[_turno("Oficina Norte Centro", _f(40))])
    time.sleep(0.25)
    sitio.poner(turnos=[])
    time.sleep(3)
    assert _eventos(lab) == [], _eventos(lab)
    # aparece de verdad (y uno de una ubicación sin equivalencia, que se ignora)
    t0 = time.time()
    sitio.poner(turnos=[_turno("Oficina Norte Centro", _f(41), cupo=2), _turno("Oficina Sur", _f(41))])
    ev = _esperar(lambda: _eventos(lab, "DIA_DISPONIBLE"), 15)
    assert ev and len(ev) == 1, ev
    tipo, suc, fecha, estado, evid = ev[0]
    assert (suc, fecha, estado) == ("NOR-1", _f(41), "DISPONIBLE")
    assert evid["url"] == sitio.url and len(evid["sha256"]) == 64 and evid["horas"] == ["09:30"]
    assert evid.get("imagen") and len(evid["imagen_sha256"]) == 64 and Path(evid["imagen"]).exists()
    assert time.time() - t0 < 10
    time.sleep(2)
    assert len(_eventos(lab, "DIA_DISPONIBLE")) == 1                  # sin duplicados mientras siga igual
    sitio.poner(turnos=[])
    assert _esperar(lambda: _eventos(lab, "DIA_NO_DISPONIBLE"), 15)
    assert sitio.envios == 0


@navegador
def test_coincidencia_con_expediente_del_plan(entorno):
    lab, p, sitio, obs, tmp = entorno
    e = lab.crear(tipo_tramite="alta")
    r = lab.c.post("/api/planes", headers=lab.h, json={"fecha_jornada": zona.hoy(utcnow()).isoformat(), "capacidad": 6})
    pid = r.json()["id"]
    dia = _f(45)
    r = lab.c.post(f"/api/planes/{pid}/elementos", headers=lab.h, json={"elementos": [
        {"expediente_id": e["id"], "fechas_aceptables": [dia], "sucursales": ["NOR-2"]}]})
    assert r.json()["aceptados"], r.json()
    _arrancar(lab, obs, _fuente(sitio), tmp, atender=True)
    # otra sucursal y otra fecha: no coinciden
    sitio.poner(turnos=[_turno("Oficina Norte Centro", dia), _turno("Oficina Norte Poniente", _f(46))])
    assert _esperar(lambda: len(_eventos(lab, "DIA_DISPONIBLE")) == 2, 15)
    with lab.db.session() as s:
        assert s.scalar(select(PlanElemento).where(PlanElemento.expediente_id == e["id"])).estado == "EN_ESPERA"
    sitio.poner(turnos=[_turno("Oficina Norte Centro", dia), _turno("Oficina Norte Poniente", _f(46)),
                        _turno("Oficina Norte Poniente", dia, cupo=1)])

    def asignado():
        with lab.db.session() as s:
            el = s.scalar(select(PlanElemento).where(PlanElemento.expediente_id == e["id"]))
            return el if el.estado == "ASIGNADO" else None
    el = _esperar(asignado, 15)
    assert el and el.fecha_asignada == dia and el.sucursal_asignada == "NOR-2"


@navegador
def test_cierre_del_navegador_y_sesion_perdida_sin_duplicar(entorno):
    lab, p, sitio, obs, tmp = entorno
    sitio.poner(turnos=[_turno("Oficina Norte Centro", _f(50))])
    o = _arrancar(lab, obs, _fuente(sitio), tmp)
    assert _esperar(lambda: len(_eventos(lab, "DIA_DISPONIBLE")) == 1, 10)
    # el navegador se cierra de golpe; mientras está caído aparece otro día
    o.matar_navegador()
    sitio.poner(turnos=[_turno("Oficina Norte Centro", _f(50)), _turno("Oficina Norte Centro", _f(51))])
    assert _esperar(lambda: o.stats["arranques"] >= 2 and o.stats["vista"].get("dom") == "ACTIVA", 30), o.estado()
    assert _esperar(lambda: len(_eventos(lab, "DIA_DISPONIBLE")) == 2, 10)
    time.sleep(2)
    assert sorted(f for _, _, f, _, _ in _eventos(lab, "DIA_DISPONIBLE")) == [_f(50), _f(51)]   # el 50 no se repite
    # sesión de consulta caducada: se reabre la página y no se duplica nada
    sitio.poner(sesion=False)
    assert _esperar(lambda: o.stats["reaperturas_sesion"] >= 1, 20), o.estado()
    sitio.poner(sesion=True)
    assert _esperar(lambda: o.stats["vista"].get("dom") == "ACTIVA", 30)
    time.sleep(2)
    assert len(_eventos(lab, "DIA_DISPONIBLE")) == 2 and o.fallo is None


@navegador
@pytest.mark.parametrize("control", ["captcha", "http429"])
def test_control_de_acceso_detiene_sin_reintentar(entorno, control):
    lab, p, sitio, obs, tmp = entorno
    o = _arrancar(lab, obs, _fuente(sitio), tmp)
    if control == "captcha":
        sitio.poner(captcha=True)
    else:
        sitio.codigo_api = 429
    assert _esperar(lambda: o.fallo, 20), o.estado()
    assert "control de acceso" in o.fallo
    arranques = o.stats["arranques"]
    time.sleep(3)
    assert o.stats["arranques"] == arranques and sitio.envios == 0       # no insiste


@navegador
def test_el_calendario_del_portal_con_el_mismo_script_generico(entorno):
    lab, p, sitio, obs, tmp = entorno
    d = p.ctl("GET", "/control/dias")
    hoy = date.fromisoformat(d["hoy"])
    usadas = {x["fecha"] for x in d["dias"].values() if x["sucursal"] == "NOR-1" and x["tramite"] == "alta"}
    fecha = next(f for f in ((hoy + timedelta(days=i)).isoformat() for i in range(20, 50)) if f not in usadas)
    url = f"{p.base}/observar/calendario?sucursal=NOR-1&tramite=alta&mes={fecha[:7]}"
    _arrancar(lab, obs, {"nombre": "calendario-portal", "url": url, "tramite": "alta", "sucursal": "NOR-1",
                         "perfil": PERFIL_CALENDARIO_PORTAL}, tmp)
    n0 = len(_eventos(lab, "DIA_DISPONIBLE"))
    p.ctl("POST", "/control/dias/habilitar", sucursal="NOR-1", tramite="alta", fecha=fecha, capacidad=1)
    ev = _esperar(lambda: [x for x in _eventos(lab, "DIA_DISPONIBLE")[n0:] if x[2] == fecha], 15)
    assert ev and ev[0][1] == "NOR-1", _eventos(lab)


def test_api_de_fuentes_web(entorno):
    lab, p, sitio, obs, tmp = entorno
    r = lab.c.post("/api/fuentes-web", headers=lab.h, json={**_fuente(sitio), "perfil": {"zona": "#x"}})
    assert r.status_code in (422, 503), r.text
    r = lab.c.post("/api/fuentes-web/probar", headers=lab.h, json={**_fuente(sitio), "perfil": {"zona": "#x"}})
    assert r.status_code == 422 and r.json()["detail"]["campo"] == "perfil.elemento", r.text
    assert lab.c.get("/api/fuentes-web", headers=lab.ops["op1"]).status_code == 403
    assert lab.c.get("/api/fuentes-web", headers=lab.h).json() == {"fuentes": []}
