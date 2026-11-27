"""E8 · M3: la persona reserva en el portal ANTES de que se teclee el folio. La página descuenta la unidad en ese
momento y no vuelve a cambiar. RC2 contaba esa unidad dos veces (en la página y como confirmada) y otra persona del
mismo día perdía su lugar hasta otro cambio del portal. Portal de laboratorio propio (127.0.0.1), datos ficticios."""
from __future__ import annotations

from datetime import date, timedelta

from monitoreo.db import utcnow
from monitoreo.models import ObservacionDia
from monitoreo.planificador import coincidencias, observaciones, operacion

from .captura_util import MANUALES
from .test_captura_dia import lab, portal  # noqa: F401  (fixtures)
from .test_plan_unidades_propiedad import _plan_con
from .test_planificador import _elementos, _ver


def _leer_pagina(lab, fecha, libres, now):
    """La página sigue viva y sin cambios: el observador real NO emite CELDA (solo cambios); lo dice su LATIDO con la
    vista DOM ACTIVA, que llama a anotar_vista_fresca (ver test_vista_fresca_con_el_observador_real)."""
    return observaciones.anotar_vista_fresca(lab.db, "NOR-1", "alta", fecha[:7], now)


def _reabrir_pagina(lab, fecha, libres, now):
    """Reapertura: INICIAL con la misma celda (sin cambio)."""
    ctx = {"observador": "obs-test", "sucursal": "NOR-1", "tramite": "alta"}
    o = {"tipo": "INICIAL", "estrategia": "dom", "ts_ms": now.timestamp() * 1000,
         "celdas": [{"fecha": fecha, "estado": "DISPONIBLE", "libres": libres}]}
    return observaciones.registrar(lab.db, o, ctx, now)


def _fecha_libre(lab, desde=60):
    d = lab.portal.ctl("GET", "/control/dias")
    usadas = {x["fecha"] for x in d["dias"].values() if x["sucursal"] == "NOR-1" and x["tramite"] == "alta"}
    return next(f for f in ((date.fromisoformat(d["hoy"]) + timedelta(days=i)).isoformat() for i in range(desde, 119))   # el plan no acepta fechas a más de 120 días
                if f not in usadas)


def _hasta_seleccionar(lab, el):
    rid = lab.c.post(f"/api/planificador/elementos/{el['id']}/preparar", headers=lab.ops["op1"]).json()["reservacion_id"]
    lab.c.post(f"/api/reservaciones/{rid}/enviar-datos", headers=lab.ops["op1"],
               json={"revisado": True, "manuales": dict(MANUALES), "sucursal": "NOR-1"})
    return rid


def test_reserva_antes_del_folio_no_pierde_el_lugar_de_la_otra_persona(lab):
    fecha = _fecha_libre(lab)
    pid, exps = _plan_con(lab, 3, [fecha], prioridades=[1, 2, 3])
    lab.portal.ctl("POST", "/control/dias/habilitar", sucursal="NOR-1", tramite="alta", fecha=fecha, capacidad=2)
    coincidencias.atender(lab.db, _ver(lab, fecha=fecha, libres=2)[0]["id"], utcnow())
    els = _elementos(lab, pid)
    el1, el2 = els[exps[0]["id"]], els[exps[1]["id"]]
    assert (el1["estado"], el2["estado"], els[exps[2]["id"]]["estado"]) == ("ASIGNADO", "ASIGNADO", "EN_ESPERA")
    clave = f"NOR-1|alta|{fecha}"
    rid = _hasta_seleccionar(lab, el1)
    # 1) la persona reserva en el portal: la página pasa de 2 a 1 ANTES de que se teclee el folio
    lab.c.post(f"/api/reservaciones/{rid}/seleccionar-dia", headers=lab.ops["op1"], json={"fecha": fecha, "sucursal": "NOR-1"})
    assert _ver(lab, fecha=fecha, libres=1)[0]["tipo"] == "UNIDADES_REDUCIDAS"
    r = coincidencias.ajustar(lab.db, clave, utcnow())
    # prudente mientras la operación está en curso: no se sabe si la baja es suya o de otra persona
    assert r["devueltos"] == [el2["id"]] and r["sobrecompromiso"] == 0
    # 2) se teclea el folio: confirmada
    r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=lab.ops["op1"], json={"confirmo": True})
    assert r.json()["reservacion"]["estado"] == "CONFIRMADA", r.text
    assert operacion.sincronizar(lab.db, utcnow())["confirmados"] == 1
    t = utcnow()
    # 3) la página sigue en 1 (ya la había descontado). Antes del margen: nada cambia (no se reparte de más)
    _leer_pagina(lab, fecha, 1, t + timedelta(seconds=5))
    assert coincidencias.reevaluar(lab.db, t + timedelta(seconds=5)) == []
    with lab.db.session() as s:
        assert len(coincidencias.comprometidas(s, clave, s.get(ObservacionDia, clave))) == 1
    # 4) pasado el margen, la página sigue mostrando 1 libre: la confirmada ya está descontada y la unidad vuelve
    #    a la persona de mayor prioridad en espera (RC2: se quedaba en espera hasta otro cambio del portal)
    tarde = t + timedelta(seconds=getattr(coincidencias, "MARGEN_REFLEJO_S", 60.0) + 1)
    _leer_pagina(lab, fecha, 1, tarde)
    with lab.db.session() as s:
        assert coincidencias.comprometidas(s, clave, s.get(ObservacionDia, clave)) == []
    out = coincidencias.reevaluar(lab.db, tarde)
    assert len(out) == 1 and out[0]["asignado"] and out[0]["elementos"] == [el2["id"]], out
    # nunca más de las unidades que se ven: 1 libre -> 1 asignado; la tercera sigue en espera
    els = _elementos(lab, pid)
    assert [els[e["id"]]["estado"] for e in exps] == ["CONFIRMADO", "ASIGNADO", "EN_ESPERA"]
    assert coincidencias.reevaluar(lab.db, tarde + timedelta(seconds=5)) == []


def test_pagina_que_tarda_en_descontar_sigue_contando_la_confirmada(lab):
    """Lo contrario: la persona confirma y la página AÚN no descuenta. Dentro del margen se sigue contando (no se
    vende de más); en cuanto la página baja, cuenta la baja y nadie más pierde su lugar."""
    fecha = _fecha_libre(lab, 80)
    pid, exps = _plan_con(lab, 3, [fecha], prioridades=[1, 2, 3])
    lab.portal.ctl("POST", "/control/dias/habilitar", sucursal="NOR-1", tramite="alta", fecha=fecha, capacidad=2)
    coincidencias.atender(lab.db, _ver(lab, fecha=fecha, libres=2)[0]["id"], utcnow())
    el1 = _elementos(lab, pid)[exps[0]["id"]]
    rid = _hasta_seleccionar(lab, el1)
    lab.c.post(f"/api/reservaciones/{rid}/seleccionar-dia", headers=lab.ops["op1"], json={"fecha": fecha, "sucursal": "NOR-1"})
    assert lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=lab.ops["op1"], json={"confirmo": True}).status_code == 200
    operacion.sincronizar(lab.db, utcnow())
    clave = f"NOR-1|alta|{fecha}"
    t = utcnow()
    _leer_pagina(lab, fecha, 2, t + timedelta(seconds=20))           # la página todavía dice 2
    assert coincidencias.reevaluar(lab.db, t + timedelta(seconds=20)) == [], "no se reparte una unidad ya usada"
    assert _ver(lab, fecha=fecha, libres=1)[0]["tipo"] == "UNIDADES_REDUCIDAS"
    assert coincidencias.ajustar(lab.db, clave, utcnow())["devueltos"] == []
    assert _elementos(lab, pid)[exps[1]["id"]]["estado"] == "ASIGNADO"


def test_leer_sin_cambios_no_escribe_en_cada_lectura(lab):
    """visto_en se anota como mucho cada VISTO_CADA_S: una lectura sin cambios no es una escritura por día y lectura
    (con SQLite eso bloquea al resto de escritores)."""
    fecha = _fecha_libre(lab, 70)
    clave = f"NOR-1|alta|{fecha}"
    t0 = utcnow()
    _reabrir_pagina(lab, fecha, 3, t0)
    with lab.db.session() as s:
        v0 = s.get(ObservacionDia, clave).visto_en
    _reabrir_pagina(lab, fecha, 3, t0 + timedelta(seconds=observaciones.VISTO_CADA_S - 1))
    with lab.db.session() as s:
        assert s.get(ObservacionDia, clave).visto_en == v0
    t2 = t0 + timedelta(seconds=observaciones.VISTO_CADA_S + 1)
    _reabrir_pagina(lab, fecha, 3, t2)
    with lab.db.session() as s:
        assert s.get(ObservacionDia, clave).visto_en == t2
