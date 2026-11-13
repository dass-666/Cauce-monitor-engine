"""v1.5: el portal de reservaciones de PRUEBA por sí solo (pantallas, errores, idempotencia,
doble reservación, persistencia). Se prueba en el mismo proceso con TestClient; las pruebas del
flujo completo lo arrancan como proceso aparte (test_captura_flujo.py)."""
import re
import threading

import pytest
from fastapi.testclient import TestClient

from portal_reservas.__main__ import main as portal_main
from portal_reservas.estado import Portal
from portal_reservas.servidor import crear_app

API = {"Authorization": "Bearer t"}
CTL = {"X-Control-Token": "c"}


@pytest.fixture
def app(tmp_path):
    return crear_app(tmp_path / "estado.json", "t", "c")


def datos(app, n=0, **kw):
    ident, reg = app.state.portal.registro_ejemplo(n)
    d = dict(operacion="nueva", tramite=reg["tramite"], identificador=ident, fecha_nacimiento=reg["fecha_nacimiento"],
             telefono="5550001111", telefono_confirmacion="5550001111", correo="a@ejemplo.test",
             correo_confirmacion="a@ejemplo.test")
    d.update(kw)
    return d


def hasta_retener(c, app, n=0, region="norte", indice=0):
    c.get("/reservar")
    r = c.post("/reservar/datos", data=datos(app, n), follow_redirects=False)
    assert r.status_code == 303, r.text
    page = c.get(f"/reservar/disponibilidad?region={region}").text
    hs = re.findall(r'name="horario" value="([^"]+)"', page)
    r = c.post("/reservar/horario", data={"horario": hs[indice]}, follow_redirects=False)
    assert r.status_code == 303, r.text
    clave = re.search(r'name="clave" value="(\w+)"', c.get("/reservar/confirmacion").text).group(1)
    return hs[indice], clave


def test_cuatro_pantallas_y_folio(app):
    c = TestClient(app)
    r = c.get("/reservar")
    assert 'data-pantalla="datos"' in r.text and "Continuar" in r.text and "portal_sesion" in c.cookies
    hid, clave = hasta_retener(c, app)
    r = c.post("/reservar/confirmar", data={"clave": clave}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/reservar/resultado/LAB-")
    page = c.get(r.headers["location"]).text
    folio = re.search(r'data-folio="([^"]+)"', page).group(1)
    assert 'data-pantalla="resultado"' in page and 'data-historial' in page
    api = c.get(f"/api/reservaciones/{folio}", headers=API).json()
    assert api["estado"] == "CONFIRMADA" and api["clave"] == clave and api["horario"] == hid


def test_errores_de_campos_y_confirmaciones(app):
    c = TestClient(app)
    c.get("/reservar")
    r = c.post("/reservar/datos", data=datos(app, telefono_confirmacion="5550002222",
                                             correo_confirmacion="b@ejemplo.test", telefono="123"))
    errs = dict(re.findall(r'data-error="(\w+)">([^<]+)<', r.text))
    assert r.status_code == 422 and {"telefono", "correo_confirmacion"} <= set(errs)
    r = c.post("/reservar/datos", data={"operacion": "nueva"})
    assert r.status_code == 422 and len(re.findall(r'data-error="', r.text)) == 7
    r = c.post("/reservar/datos", data=datos(app, identificador="REG999999"))
    assert 'data-error="identificador">Identificador inexistente' in r.text
    r = c.post("/reservar/datos", data=datos(app, fecha_nacimiento="1900-01-01"))
    assert 'data-error="fecha_nacimiento">La fecha no coincide' in r.text


def test_confirmacion_idempotente_y_duplicada(app):
    c = TestClient(app)
    _, clave = hasta_retener(c, app)
    r1 = c.post("/reservar/confirmar", data={"clave": clave}, follow_redirects=False)
    r2 = c.post("/reservar/confirmar", data={"clave": clave}, follow_redirects=False)   # reintento
    assert r1.headers["location"] == r2.headers["location"]
    assert app.state.portal.resumen()["confirmadas"] == 1
    # misma persona y trámite en otra sesión: duplicada
    c2 = TestClient(app)
    _, clave2 = hasta_retener(c2, app, n=0, indice=1)
    r = c2.post("/reservar/confirmar", data={"clave": clave2})
    assert r.status_code == 409 and 'data-error-general="RESERVACION_DUPLICADA"' in r.text


def test_mismo_horario_no_se_reserva_dos_veces(app):
    """Varias sesiones se lanzan a la vez sobre el MISMO horario: solo una lo aparta."""
    portal = app.state.portal
    hid = next(h["id"] for h in portal.disponibilidad(region="norte") if h["libres"] > 0)
    toks = []
    for n in range(8):
        t = portal.nueva_sesion()
        ident, reg = portal.registro_ejemplo(n + 10)
        assert portal.validar_datos(t, dict(datos(app, n + 10))) == {}
        toks.append(t)
    resultados = []

    def intento(t):
        try:
            portal.retener(t, hid)
            resultados.append("ok")
        except Exception as e:  # noqa: BLE001
            resultados.append(getattr(e, "codigo", str(e)))
    hilos = [threading.Thread(target=intento, args=(t,)) for t in toks]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert resultados.count("ok") == 1 and resultados.count("HORARIO_AGOTADO") == 7


def test_sesion_caducada_y_retencion_vencida(tmp_path):
    app = crear_app(tmp_path / "e.json", "t", "c", retencion_s=0.2)
    c = TestClient(app)
    _, clave = hasta_retener(c, app)
    import time
    time.sleep(0.3)
    r = c.post("/reservar/confirmar", data={"clave": clave})
    assert r.status_code == 409 and "RETENCION_VENCIDA" in r.text
    c.post("/control/caducar-sesiones", headers=CTL)
    r = c.get("/reservar/disponibilidad?region=norte")
    assert r.status_code == 440 and "SESION_CADUCADA" in r.text


def test_fallos_simulables(app):
    c = TestClient(app)
    c.post("/control/fallos", headers=CTL, json={"modo": "renombrar_campo"})
    page = c.get("/reservar").text
    assert 'name="telefono_movil"' in page and 'name="telefono"' not in page
    c.post("/control/fallos", headers=CTL, json={"modo": "error_interno", "restantes": 1})
    assert c.post("/reservar/datos", data=datos(app)).status_code == 500
    assert c.post("/reservar/datos", data=datos(app), follow_redirects=False).status_code == 303   # ya no falla
    assert c.post("/control/fallos", headers=CTL, json={"modo": "inventado"}).status_code == 422
    assert c.get("/control/estado").status_code == 403                       # sin token de control
    assert c.get("/api/disponibilidad").status_code == 401                    # sin token de la API


def test_cancelar_libera_y_persistencia(tmp_path):
    ruta = tmp_path / "e.json"
    app = crear_app(ruta, "t", "c")
    c = TestClient(app)
    hid, clave = hasta_retener(c, app)
    folio = c.post("/reservar/confirmar", data={"clave": clave}, follow_redirects=False).headers["location"].rsplit("/", 1)[1]
    libres = {h["id"]: h["libres"] for h in app.state.portal.disponibilidad()}
    assert libres[hid] == 0
    # "reinicio": otro proceso leería el mismo archivo
    p2 = Portal(ruta)
    assert p2.reservacion(folio)["estado"] == "CONFIRMADA" and p2.por_clave(clave)["folio"] == folio
    assert c.post(f"/api/reservaciones/{folio}/cancelar", headers=API).json()["estado"] == "CANCELADA"
    assert {h["id"]: h["libres"] for h in app.state.portal.disponibilidad()}[hid] == 1


def test_arranque_solo_local_y_con_tokens(monkeypatch, capsys):
    monkeypatch.delenv("PORTAL_TOKEN", raising=False)
    assert portal_main(["--host", "0.0.0.0"]) == 2
    assert portal_main([]) == 2
    assert "PORTAL_TOKEN" in capsys.readouterr().err
