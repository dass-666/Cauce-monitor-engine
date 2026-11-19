"""v1.6: modo POR DÍA del portal de PRUEBA (pantallas sin hora, inventario diario, eventos,
reloj de laboratorio y ventana nocturna simulada). En el mismo proceso con TestClient."""
import json
import random
import re
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from portal_reservas.servidor import crear_app

API = {"Authorization": "Bearer t"}
CTL = {"X-Control-Token": "c"}


@pytest.fixture
def app(tmp_path):
    return crear_app(tmp_path / "estado.json", "t", "c", zona="America/Mexico_City")


def datos(app, n=0, **kw):
    ident, reg = app.state.portal.registro_ejemplo(n)
    d = dict(operacion="nueva", tramite=reg["tramite"], identificador=ident, fecha_nacimiento=reg["fecha_nacimiento"],
             telefono="5550001111", telefono_confirmacion="5550001111", correo="a@ejemplo.test",
             correo_confirmacion="a@ejemplo.test", clave_identidad="ABC123456", acepto_privacidad="si")
    d.update(kw)
    return d


def dia_disponible(app, tramite, sucursal=None):
    inv = app.state.portal.dias
    for x in inv.listar(tramite=tramite, sucursal=sucursal):
        if x["estado"] == "DISPONIBLE":
            return x
    raise AssertionError("sin días disponibles")


def hasta_calendario(c, app, n=0, **kw):
    c.get("/cita")
    d = datos(app, n, **kw)
    r = c.post("/cita/datos", data=d, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/cita/calendario", r.text
    return d


def test_tres_pantallas_sin_hora_y_folio(app):
    c = TestClient(app)
    d = hasta_calendario(c, app)
    x = dia_disponible(app, d["tramite"])
    page = c.get("/cita/calendario", params={"estado": x["region"], "sucursal": x["sucursal"],
                                             "mes": x["fecha"][:7]}).text
    assert 'data-pantalla="calendario"' in page and 'data-calendario="mensual"' in page
    assert 'data-campo="ficha_identificador"' in page and 'readonly data-solo-lectura="1"' in page
    assert 'name="horario"' not in page and "hora" not in re.sub(r"<[^>]+>", "", page).lower().replace("ahora", "")
    assert f'name="dia" value="{x["fecha"]}"' in page
    clave = re.search(r'name="clave" value="(\w+)"', page).group(1)
    r = c.post("/cita/continuar", data={"estado": x["region"], "sucursal": x["sucursal"], "mes": x["fecha"][:7],
                                        "clave": clave, "dia": x["fecha"]}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/cita/resultado/DIA-")
    res = c.get(r.headers["location"]).text
    folio = re.search(r'data-folio="([^"]+)"', res).group(1)
    for campo in ("estado", "fecha", "sucursal"):
        assert f'data-campo="{campo}"' in res
    api = c.get(f"/api/reservaciones/{folio}", headers=API).json()
    assert api["estado"] == "CONFIRMADA" and api["modo"] == "dia" and api["hora"] is None and api["fecha"] == x["fecha"]
    despues = app.state.portal.dias.publico(x["clave"])
    assert despues["libres"] == x["libres"] - 1 and despues["version"] == x["version"] + 1


def test_campos_manuales_y_validacion_dependiente(app):
    c = TestClient(app)
    page = c.get("/cita").text
    assert 'data-manual="1" data-sin-pegar="1" onpaste="return false"' in page
    r = c.post("/cita/datos", data=datos(app, clave_identidad="", acepto_privacidad=""))
    assert r.status_code == 422 and 'data-error="clave_identidad"' in r.text and 'data-error="acepto_privacidad"' in r.text
    # el resto de lo escrito se conserva; el campo manual NO se devuelve (se vuelve a teclear)
    assert 'value="5550001111"' in r.text
    r = c.post("/cita/datos", data=datos(app, clave_identidad="12"))
    assert 'Formato' in r.text
    r = c.post("/cita/datos", data=datos(app, operacion="reprogramar"))
    assert r.status_code == 422 and 'data-error="folio_anterior"' in r.text
    r = c.post("/cita/datos", data=datos(app, folio_anterior="DIA-1"))
    assert 'Solo se indica al reprogramar' in r.text
    r = c.post("/cita/datos", data=datos(app, operacion="reprogramar", folio_anterior="DIA-20990101-00001X"))
    assert 'No existe una cita confirmada' in r.text


def _confirmar(c, app, d, x):
    page = c.get("/cita/calendario", params={"estado": x["region"], "sucursal": x["sucursal"],
                                             "mes": x["fecha"][:7]}).text
    clave = re.search(r'name="clave" value="(\w+)"', page).group(1)
    return c.post("/cita/continuar", data={"sucursal": x["sucursal"], "clave": clave, "dia": x["fecha"]},
                  follow_redirects=False), clave


def test_reprogramar_cancela_la_anterior_en_la_misma_operacion(app):
    c = TestClient(app)
    d = hasta_calendario(c, app, 3)
    x = dia_disponible(app, d["tramite"])
    r, _ = _confirmar(c, app, d, x)
    folio1 = re.search(r'data-folio="([^"]+)"', c.get(r.headers["location"]).text).group(1)
    c2 = TestClient(app)
    d2 = hasta_calendario(c2, app, 3, operacion="reprogramar", folio_anterior=folio1)
    y = [z for z in app.state.portal.dias.listar(tramite=d["tramite"]) if z["estado"] == "DISPONIBLE"
         and z["clave"] != x["clave"]][0]
    libres_x = app.state.portal.dias.publico(x["clave"])["libres"]
    r, _ = _confirmar(c2, app, d2, y)
    assert r.status_code == 303
    assert c.get(f"/api/reservaciones/{folio1}", headers=API).json()["estado"] == "REPROGRAMADA"
    assert app.state.portal.dias.publico(x["clave"])["libres"] == libres_x + 1


def test_ultima_unidad_dos_solicitudes_solo_una_gana(app):
    inv = app.state.portal.dias
    c1, c2 = TestClient(app), TestClient(app)
    d1 = hasta_calendario(c1, app, 10)
    hasta_calendario(c2, app, 11, tramite=d1["tramite"])
    x = dia_disponible(app, d1["tramite"])
    with app.state.portal.lock:          # dejar exactamente una unidad
        dd = app.state.portal.d["dias"][x["clave"]]
        dd["capacidad"] = dd["ocupados"] + 1
    x = inv.publico(x["clave"])
    r1, _ = _confirmar(c1, app, d1, x)
    r2, _ = _confirmar(c2, app, d1, x)
    assert r1.status_code == 303 and r2.status_code == 409 and 'data-error-general="DIA_AGOTADO"' in r2.text
    assert inv.publico(x["clave"])["estado"] == "AGOTADO"


def test_idempotencia_por_clave_y_dia_deshabilitado(app):
    c = TestClient(app)
    d = hasta_calendario(c, app, 12)
    x = dia_disponible(app, d["tramite"])
    r, clave = _confirmar(c, app, d, x)
    again = c.post("/cita/continuar", data={"sucursal": x["sucursal"], "clave": clave, "dia": x["fecha"]},
                   follow_redirects=False)
    assert again.headers["location"] == r.headers["location"]          # misma reservación, no otra
    c2 = TestClient(app)
    d2 = hasta_calendario(c2, app, 13)
    y = dia_disponible(app, d2["tramite"])
    c2.post("/control/dias/deshabilitar", headers=CTL, json={"clave": y["clave"]})
    r2, _ = _confirmar(c2, app, d2, y)
    assert r2.status_code == 409 and "DIA_DESHABILITADO" in r2.text


def test_api_dias_estados_y_versiones(app):
    c = TestClient(app)
    j = c.get("/api/dias", headers=API).json()
    assert j["zona_horaria"] == "America/Mexico_City" and j["secuencia"] == app.state.portal.d["seq_dias"]
    estados = {x["estado"] for x in j["dias"]}
    assert {"DISPONIBLE", "AGOTADO"} <= estados
    assert all(x["fecha"] > j["hoy"] for x in j["dias"])
    x = next(z for z in j["dias"] if z["estado"] == "DISPONIBLE")
    ev = c.post("/control/dias/agotar", headers=CTL, json={"clave": x["clave"]}).json()
    assert ev["estado"] == "AGOTADO" and ev["version"] == x["version"] + 1 and ev["causa"] == "AGOTADO"
    ev = c.post("/control/dias/liberar", headers=CTL, json={"clave": x["clave"], "unidades": 2}).json()
    assert ev["estado"] == "DISPONIBLE" and ev["libres"] == 2
    nueva = (date.fromisoformat(j["hoy"]) + timedelta(days=40)).isoformat()
    ev = c.post("/control/dias/habilitar", headers=CTL, json={"sucursal": x["sucursal"], "tramite": x["tramite"],
                                                              "fecha": nueva, "capacidad": 3}).json()
    assert ev["causa"] == "NUEVA_FECHA" and ev["estado"] == "DISPONIBLE"
    c.post("/control/api", headers=CTL, json={"caida": True})
    assert c.get("/api/dias", headers=API).status_code == 503          # caída: error, NO lista vacía
    c.post("/control/api", headers=CTL, json={"caida": False})
    assert c.get("/api/dias", headers={"Authorization": "Bearer x"}).status_code == 401


def test_disponibilidad_que_expira_y_ventana_nocturna(app):
    c = TestClient(app)
    inv = app.state.portal.dias
    x = next(z for z in inv.listar() if z["estado"] == "AGOTADO")
    ev = c.post("/control/dias/liberar", headers=CTL, json={"clave": x["clave"], "unidades": 1,
                                                            "vigencia_min": 30}).json()
    assert ev["estado"] == "DISPONIBLE" and ev["disponible_hasta"]
    c.post("/control/reloj", headers=CTL, json={"desplazar_s": 31 * 60})
    evs = c.post("/control/dias/tic", headers=CTL, json={}).json()["eventos"]
    assert any(e["clave"] == x["clave"] and e["causa"] == "EXPIRACION" and e["estado"] == "AGOTADO" for e in evs)
    # Ventana 22:00-07:30 en la zona configurada: 23:00 local está dentro, 12:00 fuera
    c.post("/control/reloj", headers=CTL, json={"real": True})
    v = c.post("/control/ventana", headers=CTL, json={"activa": True, "prob": 1.0}).json()
    assert v["inicio"] == "22:00" and v["fin"] == "07:30"
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/Mexico_City")
    manana = (datetime.now(tz) + timedelta(days=1)).date()
    noche = datetime.combine(manana, datetime.min.time(), tz).replace(hour=23).astimezone(timezone.utc)
    mediodia = noche.replace(hour=(noche.hour + 13) % 24)
    assert inv.en_ventana(noche) and not inv.en_ventana(datetime.combine(manana, datetime.min.time(), tz)
                                                         .replace(hour=12).astimezone(timezone.utc))
    c.post("/control/reloj", headers=CTL, json={"fijar": noche.isoformat()})
    evs = c.post("/control/dias/tic", headers=CTL, json={"semilla": 1}).json()["eventos"]
    assert evs and evs[-1]["causa"] == "LIBERACION_NOCTURNA" and evs[-1]["estado"] == "DISPONIBLE"
    assert mediodia  # (variable usada para documentar el caso)


def test_eventos_del_servidor_reanudables_y_sesion_que_caduca(tmp_path):
    """Con el portal como PROCESO real (TestClient no transmite flujos infinitos)."""
    import httpx
    from portal_reservas.proceso import PortalProceso
    p = PortalProceso(tmp_path / "estado.json", token="t", token_control="c",
                      extra=["--zona-horaria", "America/Mexico_City"])
    p.iniciar()
    try:
        h = {"Authorization": "Bearer t"}
        p.ctl("POST", "/control/api", latido_s=0.2)
        ses = p.http.post(p.base + "/api/eventos/sesion", headers=h).json()["sesion"]
        x = next(z for z in p.http.get(p.base + "/api/dias", headers=h).json()["dias"] if z["estado"] == "DISPONIBLE")
        seq0 = p.ctl("GET", "/control/dias")["secuencia"]
        p.ctl("POST", "/control/dias/agotar", clave=x["clave"])
        p.ctl("POST", "/control/dias/liberar", clave=x["clave"])
        p.ctl("POST", "/control/dias/reenviar-antiguo", clave=x["clave"])
        vistos, latidos = [], 0
        with httpx.stream("GET", p.base + "/api/eventos", params={"sesion": ses, "desde": seq0}, headers=h,
                          timeout=10, trust_env=False) as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            ev = None
            for linea in r.iter_lines():
                if linea.startswith("event:"):
                    ev = linea.split(":", 1)[1].strip()
                    latidos += ev == "latido"
                elif linea.startswith("data:") and ev == "dia":
                    vistos.append(json.loads(linea[5:]))
                if len(vistos) == 3 and latidos:
                    break
        assert [v["causa"] for v in vistos[:2]] == ["AGOTADO", "LIBERACION"]
        assert vistos[2].get("reenviado") and vistos[2]["version"] < vistos[1]["version"]   # fuera de orden
        # reanudar con Last-Event-ID: solo lo posterior
        with httpx.stream("GET", p.base + "/api/eventos", params={"sesion": ses},
                          headers={**h, "Last-Event-ID": str(vistos[1]["seq"])}, timeout=10, trust_env=False) as r:
            primero = None
            ev = None
            for linea in r.iter_lines():
                if linea.startswith("event:"):
                    ev = linea.split(":", 1)[1].strip()
                elif linea.startswith("data:") and ev == "dia":
                    primero = json.loads(linea[5:])
                    break
        assert primero["seq"] == vistos[2]["seq"]
        # sesión caducada: explícita, nunca una lista vacía
        p.ctl("POST", "/control/api", caducar_sesiones_eventos=True)
        r = p.http.get(p.base + "/api/eventos", params={"sesion": ses}, headers=h)
        assert r.status_code == 401 and r.json()["codigo"] == "SESION_CADUCADA"
    finally:
        p.detener()


def test_hueco_pide_resincronizar(app):
    inv = app.state.portal.dias
    k = next(iter(app.state.portal.d["dias"]))
    with app.state.portal.lock:
        for _ in range(3):
            inv.agotar(k)
        del inv.eventos[:2]                     # como si el portal hubiera reiniciado
    evs, hueco = inv.eventos_desde(0)
    assert hueco and len(evs) == 1


def test_estado_v15_recibe_inventario_diario(tmp_path):
    """Un archivo del portal de la v1.5 (sin días) se completa al arrancar la v1.6."""
    a = crear_app(tmp_path / "e.json", "t", "c")
    d = json.loads((tmp_path / "e.json").read_text(encoding="utf-8"))
    del d["dias"], d["seq_dias"]
    (tmp_path / "e.json").write_text(json.dumps(d), encoding="utf-8")
    b = crear_app(tmp_path / "e.json", "t", "c")
    assert len(b.state.portal.d["dias"]) > 100 and a is not b
    assert random  # noqa
