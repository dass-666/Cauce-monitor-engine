"""v2.1 · P3 · Observador PERSISTENTE con navegador real: la configuración se guarda, se restaura al
reiniciar el motor, no se duplica entre dos procesos sobre la misma base y se detiene (con motivo)
al superar el límite de reinicios. También: varias unidades vistas por la estrategia DOM.
Se omite sin Node + Playwright."""
from __future__ import annotations

import time

from sqlalchemy import select

from monitoreo.db import utcnow
from monitoreo.models import ObservacionDia, ObservacionEvento, ObservadorConfig
from monitoreo.planificador import observador as ob
from monitoreo.planificador.observador import Observador, Supervisor

from .test_observador import _esperar, _fecha_libre, entorno, pytestmark  # noqa: F401
from .test_observador import pytest_generate_tests  # noqa: F401


def _fabrica(lab, tmp, **kw):
    def f(c):
        return Observador(lab.db, c.sucursal, c.tramite, c.mes, c.estrategia, c.intervalo_visual_ms, atender=False,
                          evidencia_dir=str(tmp / "evid"), congelado_s=5, **kw)
    return f


def _sup_de_la_app(lab):
    sup = lab.c.app.state.supervisor
    assert sup is not None
    return sup


def _caer(sup: Supervisor):
    """Simula que el proceso del motor muere: su hilo se para y su navegador se cierra, pero NO suelta
    el arrendamiento (como un kill)."""
    sup._stop.set()
    if sup._hilo:
        sup._hilo.join(15)
    for o in list(sup.activos.values()):
        o.matar_navegador()
        o.detener(espera=5)
    sup.activos.clear()
    sup.arriendos.clear()


def _listo(o, t=40):
    return _esperar(lambda: any(x.get("tipo") == "LISTO" for x in o.lineas), t)


def test_configuracion_persistente_restaura_tras_reinicio_y_no_duplica(entorno):
    lab, p, obs, tmp = entorno
    fecha = _fecha_libre(p, "NOR-1", "alta")
    a = _sup_de_la_app(lab)
    a.fabrica = _fabrica(lab, tmp)
    ob.configurar(lab.db, "NOR-1", "alta", fecha[:7], "ambas", 150, "admin", utcnow())
    r = a.ciclo(utcnow())
    assert r["arrancados"] == ["NOR-1|alta"], r
    o1 = a.activos["NOR-1|alta"]
    assert _listo(o1)
    # un SEGUNDO proceso del motor sobre la misma base no abre otro navegador
    b = Supervisor(lab.db, fabrica=_fabrica(lab, tmp), holder="proceso-b")
    obs.append(b)
    r = b.ciclo(utcnow())
    assert r["arrancados"] == [] and r["de_otro_proceso"] == ["NOR-1|alta"] and not b.activos, r
    # el proceso A muere sin avisar; mientras está caído aparece un día
    _caer(a)
    p.ctl("POST", "/control/dias/habilitar", sucursal="NOR-1", tramite="alta", fecha=fecha, capacidad=2)
    t0 = time.monotonic()
    assert b.ciclo(utcnow())["arrancados"] == []               # el arrendamiento de A aún no vence
    arr = _esperar(lambda: b.ciclo(utcnow())["arrancados"], ob.TTL_ARRIENDO_S + 10, paso=0.5)
    restaurado_s = time.monotonic() - t0
    assert arr == ["NOR-1|alta"], arr
    o2 = b.activos["NOR-1|alta"]
    assert _listo(o2)
    # lo que cambió mientras estaba caído se detecta al restaurar (estado inicial frente a lo guardado)
    ev = _esperar(lambda: _ev(lab, fecha), 15)
    assert ev and ev[0].tipo == "DIA_DISPONIBLE" and ev[0].unidades == 2, ev
    with lab.db.session() as s:
        c = s.get(ObservadorConfig, "NOR-1|alta")
        assert c.activo and c.estado == "ACTIVO" and c.titular == "proceso-b"
    print(f"MEDIDO restauracion_tras_caida_s={restaurado_s:.1f}")
    # detener desde la API: queda DETENIDO (persistente) y ningún proceso lo vuelve a arrancar
    assert ob.desactivar(lab.db, "admin", utcnow()) == 1
    assert b.ciclo(utcnow())["detenidos"] == ["NOR-1|alta"]
    assert b.ciclo(utcnow())["arrancados"] == [] and not b.activos


def _ev(lab, fecha):
    with lab.db.session() as s:
        return list(s.scalars(select(ObservacionEvento).where(ObservacionEvento.fecha == fecha)
                              .order_by(ObservacionEvento.id)))


def test_reinicio_ordenado_restaura_enseguida(entorno):
    lab, p, obs, tmp = entorno
    fecha = _fecha_libre(p, "NOR-1", "alta", desde=25)
    a = _sup_de_la_app(lab)
    a.fabrica = _fabrica(lab, tmp)
    ob.configurar(lab.db, "NOR-1", "alta", fecha[:7], "dom", 250, "admin", utcnow())
    a.ciclo(utcnow())
    assert _listo(a.activos["NOR-1|alta"])
    a.stop()                                   # apagado ordenado: suelta el arrendamiento
    b = Supervisor(lab.db, fabrica=_fabrica(lab, tmp), holder="proceso-b2")
    obs.append(b)
    t0 = time.monotonic()
    assert b.ciclo(utcnow())["arrancados"] == ["NOR-1|alta"]
    assert _listo(b.activos["NOR-1|alta"])
    print(f"MEDIDO restauracion_tras_apagado_ordenado_s={time.monotonic() - t0:.1f}")


def test_limite_de_reinicios_detiene_con_motivo(entorno):
    lab, p, obs, tmp = entorno
    fecha = _fecha_libre(p, "NOR-1", "alta", desde=27)
    a = _sup_de_la_app(lab)
    a.fabrica = _fabrica(lab, tmp, max_reinicios=2)
    ob.configurar(lab.db, "NOR-1", "alta", fecha[:7], "dom", 250, "admin", utcnow())
    a.ciclo(utcnow())
    o = a.activos["NOR-1|alta"]
    for i in range(3):
        assert _esperar(lambda: o.proc and o.proc.poll() is None and o.stats["arranques"] == i + 1, 40), o.estado()
        assert _listo(o) if i == 0 else True
        time.sleep(0.5)
        o.matar_navegador()
    assert _esperar(lambda: o.fallo, 40), o.estado()
    assert "se relanzó 3 veces" in o.fallo
    r = a.ciclo(utcnow())
    assert r["fallidos"] == ["NOR-1|alta"], r
    with lab.db.session() as s:
        c = s.get(ObservadorConfig, "NOR-1|alta")
        assert c.estado == "DETENIDO_POR_FALLOS" and not c.activo and "relanzó" in c.motivo
    assert a.ciclo(utcnow())["arrancados"] == []                 # no insiste


def test_varias_unidades_vistas_por_dom(entorno):
    lab, p, obs, tmp = entorno
    fecha = _fecha_libre(p, "NOR-1", "alta", desde=29)
    o = Observador(lab.db, "NOR-1", "alta", mes=fecha[:7], evidencia_dir=str(tmp / "evid"), atender=False,
                   congelado_s=5, intervalo_visual_ms=150)
    o.iniciar()
    obs.append(o)
    assert _listo(o)
    p.ctl("POST", "/control/dias/habilitar", sucursal="NOR-1", tramite="alta", fecha=fecha, capacidad=3)
    assert _esperar(lambda: [e for e in _ev(lab, fecha) if e.unidades == 3], 15), [x for x in o.lineas if x.get("fecha") == fecha]
    p.ctl("POST", "/control/dias/liberar", sucursal="NOR-1", tramite="alta", fecha=fecha, unidades=2)
    assert _esperar(lambda: [e for e in _ev(lab, fecha) if e.unidades == 5 and e.tipo == "DIA_DISPONIBLE"], 15)
    clave = f"NOR-1|alta|{fecha}"
    with lab.db.session() as s:
        assert s.get(ObservacionDia, clave).libres == 5
    tipos = [(e.tipo, e.unidades_anteriores, e.unidades) for e in _ev(lab, fecha)]
    assert tipos == [("DIA_DISPONIBLE", None, 3), ("DIA_DISPONIBLE", 3, 5)], tipos      # sin duplicados
