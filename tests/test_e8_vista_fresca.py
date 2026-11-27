"""E8 · M3 con el observador REAL (Chromium + portal de laboratorio propio, 127.0.0.1): el observador solo emite
CELDA cuando algo cambia, así que «la página sigue igual» solo se sabe por su LATIDO con la vista DOM ACTIVA.
La jornada de 6 de RC3 (primer arreglo) perdió un lugar justo por eso. Se omite sin Node + Playwright."""
from __future__ import annotations

import time

from monitoreo.db import utcnow
from monitoreo.models import ObservacionDia
from monitoreo.planificador import observaciones
from monitoreo.planificador.observador import Observador

from .test_observador import _esperar, _fecha_libre, entorno, pytestmark  # noqa: F401
from .test_observador import pytest_generate_tests  # noqa: F401
from .test_observador_persistente import _ev, _listo


def _visto(lab, clave):
    with lab.db.session() as s:
        d = s.get(ObservacionDia, clave)
        return d.visto_en if d else None


def test_vista_fresca_con_el_observador_real(entorno, monkeypatch):
    lab, p, obs, tmp = entorno
    monkeypatch.setattr(observaciones, "VISTO_CADA_S", 1.0)
    fecha = _fecha_libre(p, "NOR-1", "alta", desde=31)
    o = Observador(lab.db, "NOR-1", "alta", mes=fecha[:7], evidencia_dir=str(tmp / "evid"), atender=False,
                   congelado_s=5, intervalo_visual_ms=150)
    o.iniciar()
    obs.append(o)
    assert _listo(o)
    p.ctl("POST", "/control/dias/habilitar", sucursal="NOR-1", tramite="alta", fecha=fecha, capacidad=2)
    assert _esperar(lambda: [e for e in _ev(lab, fecha) if e.unidades == 2], 15)
    clave = f"NOR-1|alta|{fecha}"
    with lab.db.session() as s:
        cambio = s.get(ObservacionDia, clave).actualizado_en
    v0 = _visto(lab, clave)
    assert v0 is not None
    # sin ningún cambio en la página, los latidos con la vista DOM ACTIVA siguen anotando que se vio
    assert _esperar(lambda: (_visto(lab, clave) or v0) > v0, 20), o.stats
    with lab.db.session() as s:
        d = s.get(ObservacionDia, clave)
        assert d.actualizado_en == cambio and d.libres == 2          # ningún cambio inventado
    # vista CONGELADA (el sello deja de cambiar): ya no se anota
    o.stats["vista"]["dom"] = "CONGELADO"
    time.sleep(1.5)
    v1 = _visto(lab, clave)
    time.sleep(3)
    assert _visto(lab, clave) == v1
    # con una CELDA pendiente de registrar (p. ej. la base no respondió), tampoco
    o.stats["vista"]["dom"] = "ACTIVA"
    o.pendientes.append({"tipo": "CELDA", "estrategia": "dom", "fecha": "2099-01-01", "estado": "DESCONOCIDO"})
    monkeypatch.setattr(o, "_reintentar", lambda: None)
    time.sleep(1.5)
    v2 = _visto(lab, clave)
    time.sleep(3)
    assert _visto(lab, clave) == v2
    assert utcnow() > v2
