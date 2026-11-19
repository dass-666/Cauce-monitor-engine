"""v1.6: calendario POR DÍA del motor (mecanismo A = eventos, B = consulta periódica).

- reglas puras de `aplicar()` (clasificación, fuera de orden, línea base, retiradas);
- "un error de conexión NO es falta de disponibilidad": DESCONOCIDO / PENDIENTE con dato ALMACENADO;
- conector `portal_dias` contra el portal de PRUEBA como proceso real;
- oyente de eventos (SSE) real: cambio al momento, corte, reconexión, sesión caducada.
"""
import time
from datetime import datetime, timedelta, timezone

import pytest

from monitoreo.captura import calendario_dias as cd
from monitoreo.engine.clock import RealClock
from monitoreo.engine.pipeline import RuntimeState, poll_source
from monitoreo.models import CalendarioCambio, SlotState, Source

T0 = datetime(2030, 1, 10, 15, 0, 0)


def _dia(fecha, estado="DISPONIBLE", libres=2, version=1, causa=None, suc="S1", tramite="T1", hasta=None):
    return {"clave": f"{suc}|{tramite}|{fecha}", "region": "norte", "sucursal": suc, "sucursal_nombre": "Sucursal 1",
            "tramite": tramite, "fecha": fecha, "estado": estado, "libres": libres, "capacidad": 2,
            "version": version, "causa": causa, "disponible_hasta": hasta}


def _fuente(db):
    with db.session() as s:
        src = Source(code="cal-unit", name="Calendario de prueba", region="NORTE", availability_type="calendario",
                     connector_type="portal_dias", config={"url_base": "http://127.0.0.1:9"}, interval_s=5)
        s.add(src)
        s.flush()
        return src.id


def _tipos(db, sid):
    with db.session() as s:
        return [c.tipo for c in s.query(CalendarioCambio).filter_by(source_id=sid).order_by(CalendarioCambio.id)]


# ---------------------------------------------------------------- reglas
def test_clasificacion_de_todas_las_transiciones(db):
    sid = _fuente(db)
    with db.session() as s:
        r = cd.aplicar(s, sid, [_dia("2030-01-20"), _dia("2030-01-21", "AGOTADO", 0)], cd.PERIODICO, T0, completo=True)
        assert r["linea_base"] and r["cambios"] == []          # la primera foto no inventa "cambios"
    pasos = [
        ([_dia("2030-01-20", "AGOTADO", 0, 2, "AGOTADO"), _dia("2030-01-21", "AGOTADO", 0)], ["AGOTAMIENTO"]),
        ([_dia("2030-01-20", "DISPONIBLE", 1, 3, "CANCELACION"), _dia("2030-01-21", "AGOTADO", 0)],
         ["CANCELACION_DEVUELVE"]),
        ([_dia("2030-01-20", "DISPONIBLE", 1, 3), _dia("2030-01-21", "DISPONIBLE", 2, 2, "LIBERACION")], ["REAPERTURA"]),
        ([_dia("2030-01-20", "DESHABILITADO", 0, 4), _dia("2030-01-21", "DISPONIBLE", 2, 2),
          _dia("2030-01-22", "DISPONIBLE", 2, 1)], ["DESHABILITADA", "NUEVA_FECHA"]),
        ([_dia("2030-01-20", "DISPONIBLE", 2, 5), _dia("2030-01-21", "AGOTADO", 0, 3, "EXPIRACION"),
          _dia("2030-01-22", "DISPONIBLE", 1, 2)], ["REHABILITADA", "DISPONIBILIDAD_EXPIRADA", "CAMBIO_UNIDADES"]),
        ([_dia("2030-01-20", "DISPONIBLE", 2, 5), _dia("2030-01-21", "AGOTADO", 0, 3)], ["RETIRADA"]),
    ]
    for i, (dias, esperado) in enumerate(pasos, 1):
        with db.session() as s:
            r = cd.aplicar(s, sid, dias, cd.PERIODICO, T0 + timedelta(seconds=i), completo=True, hoy_portal="2030-01-10")
        assert [c["tipo"] for c in r["cambios"]] == esperado, (i, r["cambios"])


def test_fuera_de_orden_se_descarta_y_se_cuenta(db):
    sid = _fuente(db)
    with db.session() as s:
        cd.aplicar(s, sid, [_dia("2030-01-20", version=5)], cd.EVENTOS, T0, completo=True)
    with db.session() as s:
        r = cd.aplicar(s, sid, [_dia("2030-01-20", "AGOTADO", 0, 4)], cd.EVENTOS, T0, completo=False)
        assert r["fuera_de_orden"] == 1 and r["cambios"] == []
    with db.session() as s:
        d = s.query(cd.CalendarioDia).one()
        assert d.estado == "DISPONIBLE" and d.version_portal == 5     # el dato viejo NO pisó al nuevo
        assert cd.fuente(s, sid).fuera_de_orden == 1
    assert "FUERA_DE_ORDEN" in _tipos(db, sid)


def test_error_de_conexion_no_es_falta_de_disponibilidad(db):
    sid = _fuente(db)
    with db.session() as s:
        cd.aplicar(s, sid, [_dia("2030-01-20")], cd.PERIODICO, T0, completo=True)
        cd.marcar_ok(s, sid, cd.PERIODICO, T0)
    with db.session() as s:
        v = cd.vista(s, T0 + timedelta(seconds=5), 60)
    dia = v["sucursales"][0]["tramites"]["T1"][0]
    assert dia["estado"] == "DISPONIBLE" and dia["origen"] == "ACTUAL" and dia["libres"] == 2
    # el portal deja de responder
    with db.session() as s:
        cd.marcar_error(s, sid, "DESCONEXION", "el portal no responde", T0 + timedelta(seconds=10))
        v = cd.vista(s, T0 + timedelta(seconds=11), 60)
    dia = v["sucursales"][0]["tramites"]["T1"][0]
    assert dia["estado"] == "DESCONOCIDO" and dia["origen"] == "ALMACENADO"
    assert dia["almacenado"] == "DISPONIBLE" and dia["libres"] is None and dia["libres_almacenadas"] == 2
    assert v["fuentes"][0]["sin_verificar"] and v["fuentes"][0]["verificado_en"] == "2030-01-10T15:00:00Z"
    # vuelve: actual otra vez; y sin noticias por más de la frescura => PENDIENTE (no "agotado")
    with db.session() as s:
        cd.marcar_ok(s, sid, cd.PERIODICO, T0 + timedelta(seconds=20))
        assert cd.vista(s, T0 + timedelta(seconds=21), 60)["sucursales"][0]["tramites"]["T1"][0]["estado"] == "DISPONIBLE"
        p = cd.vista(s, T0 + timedelta(seconds=200), 60)["sucursales"][0]["tramites"]["T1"][0]
    assert p["estado"] == "PENDIENTE_ACTUALIZACION" and p["origen"] == "ALMACENADO"
    assert _tipos(db, sid).count("DESCONEXION") == 1


def test_disponibilidad_que_vence_no_se_ofrece(db):
    sid = _fuente(db)
    with db.session() as s:
        cd.aplicar(s, sid, [_dia("2030-01-20", hasta="2030-01-10T15:00:30Z")], cd.PERIODICO, T0, completo=True)
        cd.marcar_ok(s, sid, cd.PERIODICO, T0 + timedelta(seconds=40))
        assert cd.resumen_recursos(s, sid, T0)[0]["libres"] == 2
        assert cd.resumen_recursos(s, sid, T0 + timedelta(seconds=40))[0]["libres"] == 0
        v = cd.vista(s, T0 + timedelta(seconds=40), 60)
    assert v["sucursales"][0]["tramites"]["T1"][0]["estado"] == "AGOTADO"


# ---------------------------------------------------------------- con el portal real
@pytest.fixture(scope="module")
def portal(tmp_path_factory):
    from portal_reservas.proceso import PortalProceso
    from monitoreo.captura.portal import TOKEN_LOCAL
    p = PortalProceso(tmp_path_factory.mktemp("portal") / "estado.json", token=TOKEN_LOCAL, token_control="c",
                      extra=["--zona-horaria", "America/Mexico_City"])
    p.iniciar()
    yield p
    p.detener()


def _fuente_real(db, portal, mecanismo, code):
    with db.session() as s:
        src = Source(code=code, name="Portal por día", region="CENTRO", availability_type="calendario",
                     connector_type="portal_dias", config={"url_base": portal.base, "mecanismo": mecanismo,
                                                           "timeout_s": 3, "latido_s": 0.3},
                     interval_s=1, jitter_pct=0, rate_limit_per_min=6000)
        s.add(src)
        s.flush()
        return src.id


def _poll(db, sid, rt):
    with db.session() as s:
        s.get(Source, sid).next_poll_at = None
    return poll_source(db, sid, RealClock(), rt)


def _disponible(portal):
    j = portal.ctl("GET", "/control/dias")
    return next(x for k, x in sorted(j["dias"].items())
                if x["estado"] == "DISPONIBLE" and x["libres"] > 0 and x["fecha"] > j["hoy"])


def test_consulta_periodica_detecta_cambios_y_no_inventa_si_el_portal_cae(db, portal):
    portal.ctl("POST", "/control/api", caida=False)
    sid, rt = _fuente_real(db, portal, "periodico", f"dias-b-{db.engine.dialect.name}"), RuntimeState()
    assert _poll(db, sid, rt).outcome == "OK"
    x = _disponible(portal)
    rk = f"dia:{x['sucursal']}:{x['tramite']}"
    with db.session() as s:
        res = s.query(SlotState).filter_by(source_id=sid, resource_key=rk).one()
        antes = res.available
        assert res.attributes["sucursal"] == x["sucursal"] and res.resource_type == "cita_dia"
    portal.ctl("POST", "/control/dias/agotar", clave=x["clave"])
    _poll(db, sid, rt)
    portal.ctl("POST", "/control/dias/liberar", clave=x["clave"], unidades=1)
    _poll(db, sid, rt)
    t = _tipos(db, sid)
    assert "AGOTAMIENTO" in t and "REAPERTURA" in t
    with db.session() as s:
        antes = s.query(SlotState).filter_by(source_id=sid, resource_key=rk).one().available
    # el portal cae: la consulta FALLA (no publica "0 libres") y el panel muestra DESCONOCIDO
    portal.ctl("POST", "/control/api", caida=True)
    try:
        out = _poll(db, sid, rt)
        assert out.outcome != "OK"
        with db.session() as s:
            assert s.query(SlotState).filter_by(source_id=sid, resource_key=rk).one().available == antes
            # (nada se publicó como agotado por la caída: el último valor publicado sigue intacto)
            v = cd.vista(s, datetime.now(timezone.utc).replace(tzinfo=None), 60, sucursal=x["sucursal"])
        d = next(z for z in v["sucursales"][0]["tramites"][x["tramite"]] if z["fecha"] == x["fecha"])
        assert d["estado"] == "DESCONOCIDO" and d["almacenado"] == "DISPONIBLE"
        assert "DESCONEXION" in _tipos(db, sid)
    finally:
        portal.ctl("POST", "/control/api", caida=False)
    assert _poll(db, sid, rt).outcome == "OK"
    with db.session() as s:
        assert not cd.sin_verificar(cd.fuente(s, sid))


def _esperar(cond, t=8.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < t:
        if cond():
            return time.monotonic() - t0
        time.sleep(0.05)
    raise AssertionError("no ocurrió a tiempo")


def test_eventos_al_momento_corte_reconexion_y_sesion_caducada(db, portal):
    from monitoreo.captura.eventos_portal import OyenteEventos
    from monitoreo.captura.portal import TOKEN_LOCAL
    portal.ctl("POST", "/control/api", caida=False, latido_s=0.3, sesion_eventos_s=600)
    sid = _fuente_real(db, portal, "eventos", f"dias-a-{db.engine.dialect.name}")
    avisos = []
    o = OyenteEventos(db, sid, portal.base, TOKEN_LOCAL, avisos.append, timeout_lectura=3)
    o.start()
    try:
        def sano():
            with db.session() as s:
                f = cd.fuente(s, sid)
                return f.conectado and f.confirmado_en is not None and o.estadisticas["conexiones"] >= 1
        _esperar(sano)
        x = _disponible(portal)
        portal.ctl("POST", "/control/dias/agotar", clave=x["clave"])
        lat = _esperar(lambda: "AGOTAMIENTO" in _tipos(db, sid))
        assert lat < 2.0 and sid in avisos
        # la consulta del conector en modo eventos lee lo recibido, sin pedir el calendario completo
        out = _poll(db, sid, RuntimeState())
        assert out.outcome == "OK"
        # dato viejo reenviado: se descarta
        portal.ctl("POST", "/control/dias/reenviar-antiguo", clave=x["clave"])
        _esperar(lambda: "FUERA_DE_ORDEN" in _tipos(db, sid))
        with db.session() as s:
            assert s.query(cd.CalendarioDia).filter_by(source_id=sid, clave=x["clave"]).one().estado == "AGOTADO"
        # corte del flujo: se registra y se reconecta sola
        portal.ctl("POST", "/control/api", cortar_eventos=True)
        _esperar(lambda: "RECONEXION" in _tipos(db, sid))
        portal.ctl("POST", "/control/dias/liberar", clave=x["clave"], unidades=1)
        _esperar(lambda: "REAPERTURA" in _tipos(db, sid))
        # sesión caducada: se nota, se abre otra y sigue
        portal.ctl("POST", "/control/api", caducar_sesiones_eventos=True)
        _esperar(lambda: "SESION_CADUCADA" in _tipos(db, sid))
        portal.ctl("POST", "/control/dias/agotar", clave=x["clave"])
        _esperar(lambda: _tipos(db, sid).count("AGOTAMIENTO") >= 2)
    finally:
        o.detener()
        o.join(5)
        portal.ctl("POST", "/control/api", caida=False)
