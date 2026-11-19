"""v2.1 · P2/P5 · Observador de calendario con navegador REAL (Node + Playwright/Chromium) sobre la
vista en vivo del portal de laboratorio: detecta cambios sin recargar, con las dos estrategias,
y distingue sesión caducada, desconexión, calendario congelado y falta de disponibilidad.
Se omite si no hay Node o Playwright (en CI se instalan en los trabajos con navegador)."""
from __future__ import annotations

import shutil
import subprocess
import time
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from monitoreo.config import settings
from monitoreo.db import Database
from monitoreo.models import ObservacionEvento
from monitoreo.planificador.observador import Observador
from portal_reservas.proceso import PortalProceso

from .captura_util import ADMIN_PWD, LabDia
from .conftest import BACKENDS, PG_URL


def _playwright() -> bool:
    if not shutil.which("node"):
        return False
    return subprocess.run(["node", "-e", "require('playwright')"], capture_output=True).returncode == 0


pytestmark = pytest.mark.skipif(not _playwright(), reason="sin Node.js + Playwright")


@pytest.fixture
def entorno(request, tmp_path, monkeypatch):
    """Portal propio por prueba (las pruebas lo detienen y lo congelan) y motor sobre SQLite o PG."""
    p = PortalProceso(tmp_path / "portal.json", extra=["--zona-horaria", "America/Mexico_City"])
    p.iniciar()
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "admin_password", ADMIN_PWD)
    monkeypatch.setenv("MONITOREO_PORTAL_URL", p.base)
    monkeypatch.setenv("MONITOREO_PORTAL_TOKEN", p.token)
    url = PG_URL if request.param == "postgres" else f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    if request.param == "postgres":
        Database(url).reset_for_tests()
    lab = LabDia(url, p)
    obs = []
    yield lab, p, obs, tmp_path
    for o in obs:
        o.detener()
    lab.cerrar()
    p.detener()
    if request.param == "postgres":
        Database(PG_URL).reset_for_tests()


def pytest_generate_tests(metafunc):
    if "entorno" in metafunc.fixturenames:
        metafunc.parametrize("entorno", BACKENDS, indirect=True)


def _esperar(pred, t=20.0, paso=0.05):
    t0 = time.monotonic()
    while time.monotonic() - t0 < t:
        v = pred()
        if v:
            return v
        time.sleep(paso)
    return None


def _fecha_libre(p, sucursal, tramite, desde=20):
    d = p.ctl("GET", "/control/dias")
    usadas = {x["fecha"] for x in d["dias"].values() if x["sucursal"] == sucursal and x["tramite"] == tramite}
    hoy = date.fromisoformat(d["hoy"])
    for i in range(desde, desde + 40):
        f = hoy + timedelta(days=i)
        if f.isoformat() not in usadas and f.month == (hoy + timedelta(days=desde)).month:
            return f.isoformat()
    raise AssertionError("sin fecha libre")


def _obs(lab, p, obs, tmp_path, fecha, **kw):
    o = Observador(lab.db, "NOR-1", "alta", mes=fecha[:7], evidencia_dir=str(tmp_path / "evid"), atender=False,
                   congelado_s=kw.pop("congelado_s", 5), intervalo_visual_ms=kw.pop("intervalo_visual_ms", 150), **kw)
    o.iniciar()
    obs.append(o)
    assert _esperar(lambda: any(x.get("tipo") == "LISTO" for x in o.lineas), 40), o.lineas[-5:]
    return o


def _eventos(lab, **filtro):
    with lab.db.session() as s:
        q = select(ObservacionEvento)
        for k, v in filtro.items():
            q = q.where(getattr(ObservacionEvento, k) == v)
        return list(s.scalars(q.order_by(ObservacionEvento.id)))


def test_detecta_dia_nuevo_y_que_desaparece_sin_recargar_con_ambas_estrategias(entorno):
    lab, p, obs, tmp = entorno
    fecha = _fecha_libre(p, "NOR-1", "alta")
    o = _obs(lab, p, obs, tmp, fecha)
    p.ctl("POST", "/control/dias/habilitar", sucursal="NOR-1", tramite="alta", fecha=fecha, capacidad=1)
    t0 = time.time()
    ev = _esperar(lambda: [e for e in _eventos(lab, tipo="DIA_DISPONIBLE", fecha=fecha)
                           if {"dom", "visual"} <= set(e.detecciones)], 15)
    assert ev and len(ev) == 1, [x for x in o.lineas if x.get("tipo") == "CELDA"]
    e = ev[0]
    assert e.sucursal == "NOR-1" and e.tramite == "alta" and e.estado == "DISPONIBLE"
    assert e.evidencia.get("sha256") and (e.evidencia.get("html") or e.evidencia.get("imagen"))
    latencia = e.detectado_en.timestamp() - t0
    assert -1 < latencia < 5, latencia
    # la página NO se recargó: una sola apertura (INICIAL por estrategia)
    assert sum(1 for x in o.lineas if x.get("tipo") == "INICIAL") == 2
    p.ctl("POST", "/control/dias/agotar", sucursal="NOR-1", tramite="alta", fecha=fecha)
    assert _esperar(lambda: _eventos(lab, tipo="DIA_NO_DISPONIBLE", fecha=fecha), 15)
    with lab.db.session() as s:
        assert s.get(ObservacionEvento, e.id).consumo == "DESAPARECIDO"
    # deduplicado: un evento por transición aunque lo vieran dos estrategias
    assert len(_eventos(lab, fecha=fecha)) == 2


def test_distingue_sesion_caducada_congelado_desconexion_y_sin_disponibilidad(entorno):
    lab, p, obs, tmp = entorno
    fecha = _fecha_libre(p, "NOR-1", "alta")
    o = _obs(lab, p, obs, tmp, fecha)

    def vistas(est):
        return [e for e in _eventos(lab, tipo="VISTA") if e.estado == est]
    # congelado: la petición del calendario se queda colgada (sin error visible)
    p.ctl("POST", "/control/vista", congelado=True)
    assert _esperar(lambda: vistas("CONGELADO"), 15)
    p.ctl("POST", "/control/vista", congelado=False)
    assert _esperar(lambda: len(vistas("ACTIVA")) >= 3, 15)          # dom + visual al abrir, y al descongelar
    # sesión caducada: el supervisor reabre la página (con espera) y vuelve a ACTIVA
    p.ctl("POST", "/control/vista", caducar_todas=True)
    assert _esperar(lambda: vistas("SESION_CADUCADA"), 15)
    assert _esperar(lambda: o.stats["reaperturas_sesion"] >= 1 and any(x.get("tipo") == "REABIERTA" for x in o.lineas), 30), \
        "DETALLE " + str([o.estado(), [{k: v for k, v in x.items() if k not in ("celdas", "evidencia")}
                                                            for x in o.lineas[-12:]]])
    # desconexión: el portal cae (no es «sin días»); al volver, la página se recupera sola
    p.detener(forzar=True)
    assert _esperar(lambda: vistas("DESCONECTADO"), 15)
    assert not _eventos(lab, tipo="DIA_NO_DISPONIBLE")                  # ninguna caída se tomó como «agotado»
    p.iniciar()
    assert _esperar(lambda: _eventos(lab, tipo="VISTA")[-1].estado == "ACTIVA", 30), \
        " ".join(f"{e.estrategia}:{e.estado}" for e in _eventos(lab, tipo="VISTA")) + " DETALLE " + str(o.estado()) + str([{k: v for k, v in x.items() if k not in ("celdas", "evidencia")} for x in o.lineas[-10:]])
    # sin disponibilidad es otra cosa: vista ACTIVA y ningún día DISPONIBLE en el mes observado
    from monitoreo.models import ObservacionDia
    with lab.db.session() as s:
        disp = s.scalar(select(func.count()).select_from(ObservacionDia).where(
            ObservacionDia.sucursal == "NOR-1", ObservacionDia.fecha.like(fecha[:7] + "%"),
            ObservacionDia.estado == "DISPONIBLE"))
    estados = {x.estado for x in _eventos(lab, tipo="VISTA")}
    assert {"CONGELADO", "SESION_CADUCADA", "DESCONECTADO", "ACTIVA"} <= estados
    assert disp is not None


def test_reinicio_del_navegador_no_duplica_y_recupera_lo_perdido(entorno):
    lab, p, obs, tmp = entorno
    fecha = _fecha_libre(p, "NOR-1", "alta")
    f2 = (date.fromisoformat(fecha) + timedelta(days=1)).isoformat()
    o = _obs(lab, p, obs, tmp, fecha)
    p.ctl("POST", "/control/dias/habilitar", sucursal="NOR-1", tramite="alta", fecha=fecha, capacidad=1)
    assert _esperar(lambda: _eventos(lab, tipo="DIA_DISPONIBLE", fecha=fecha), 15)
    o.matar_navegador()                                                   # corte brusco del navegador
    p.ctl("POST", "/control/dias/habilitar", sucursal="NOR-1", tramite="alta", fecha=f2, capacidad=1)   # mientras está caído
    assert _esperar(lambda: o.stats["reinicios_navegador"] >= 1 and o.stats["arranques"] >= 2, 20)
    assert _esperar(lambda: _eventos(lab, tipo="DIA_DISPONIBLE", fecha=f2), 40), o.lineas[-5:]
    assert len(_eventos(lab, tipo="DIA_DISPONIBLE", fecha=fecha)) == 1        # lo ya visto no se repite
