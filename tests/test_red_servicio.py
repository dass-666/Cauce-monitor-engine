"""Estado de red en la interfaz de Monitor: única fuente = Network Engine (solo lectura).

- UNITARIAS: detalle saneado por interfaz, eventos de pérdida/recuperación, reinicio del motor, motor apagado,
  credencial rechazada; la ruta /api/red nunca espera a Network Engine.
- CONTRA EL MOTOR REAL EN SIMULACIÓN (NETWORK_ENGINE_REPO): Monitor arranca con la configuración, muestra interfaces,
  ve una pérdida y su recuperación, y sigue funcionando al apagar Network Engine. Datos ficticios; no es hardware.
"""
import json
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from monitoreo.app import create_app
from monitoreo.red_lectura import servicio as SV
from monitoreo.red_lectura.lector import LectorConectividad

from .captura_util import ADMIN_PWD
from .test_red_lectura import REPO_RED, Motor, real

IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def vista(epoch="e1", seq=5, conns=None, state="usable", usable=True):
    return {"schema": "cauce.connectivity/1", "api": "v1", "engine": {"version": "0.2.0.dev0", "mode": "simulated", "epoch": epoch},
            "generated_at": "2026-11-27T01:00:00Z", "last_seq": seq, "state": state, "usable": usable, "reason": None,
            "counts": {"connections": 2, "available": 2, "measured": 2},
            "connections": conns if conns is not None else [
                {"id": "Wi-Fi-SECRETO", "kind": "wifi", "link": "up", "available": True, "measured": True, "health": "healthy",
                 "availability_pct": 100.0, "avg_latency_ms": 21.5, "ip": "192.168.1.20", "mac": "aa:bb:cc:dd:ee:ff"},
                {"id": "eth-12", "kind": "ethernet", "link": "up", "available": True, "measured": True, "health": "degraded",
                 "availability_pct": 88.0, "avg_latency_ms": 950.0}]}


def eventos(evs, nxt=10, resync=False):
    return {"schema": "cauce.status-events/1", "api": "v1", "epoch": "e1", "last_seq": nxt, "gap": False, "restarted": False,
            "resync_required": resync, "has_more": False, "next_since": nxt, "events": evs}


class Falso:
    def __init__(self):
        self.conn = [vista()]
        self.evs = [eventos([])]
        self.pedidos = []
        self.espera = 0.0

    def connectivity(self):
        time.sleep(self.espera)
        r = self.conn.pop(0) if len(self.conn) > 1 else self.conn[0]
        if isinstance(r, Exception):
            raise r
        return r

    def events(self, since=0, limit=200):
        self.pedidos.append(since)
        r = self.evs.pop(0) if len(self.evs) > 1 else self.evs[0]
        if isinstance(r, Exception):
            raise r
        return r


class Reloj:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def servicio(cliente, reloj=None):
    reloj = reloj or Reloj()
    return SV.ServicioRed(lambda **kw: LectorConectividad(cliente, reloj=reloj, entorno={}, **kw), reloj=reloj), reloj


class ErrorAPI(Exception):
    def __init__(self, status, code):
        super().__init__(code)
        self.status, self.code = status, code


# ============================================================================================ unitarias
def test_detalle_por_interfaz_saneado_y_con_latencia():
    svc, _ = servicio(Falso())
    svc.paso()
    e = svc.estado()
    assert (e["network_engine"], e["internet"]) == ("disponible", "DISPONIBLE")
    assert [(i["nombre"], i["salud"], i["latencia_ms"]) for i in e["interfaces"]] == [
        ("Wi-Fi 1", "SANA", 21.5), ("Cable (Ethernet) 1", "DEGRADADA", 950.0)]
    texto = json.dumps(e)
    for prohibido in ("SECRETO", "eth-12", "aa:bb", "192.168"):
        assert prohibido not in texto
    assert not IP.search(texto)


def test_perdida_y_recuperacion_con_nombre_de_interfaz_sin_id():
    f = Falso()
    f.evs = [eventos([], nxt=5),
             eventos([{"seq": 6, "ts": "2026-11-27T01:00:10Z", "kind": "health.change", "connection_id": "eth-12", "old": "healthy", "new": "down"},
                      {"seq": 9, "ts": "2026-11-27T01:00:40Z", "kind": "health.recovered", "connection_id": "eth-12"}], nxt=9)]
    svc, reloj = servicio(f)
    svc.paso()
    reloj.t += 11
    svc.paso()
    ev = svc.estado()["eventos"]
    assert [(x["evento"], x["interfaz"], x["tipo"]) for x in ev] == [
        ("Conectividad recuperada", "Cable (Ethernet) 1", "recuperada"), ("Pérdida de conectividad", "Cable (Ethernet) 1", "perdida")]
    assert f.pedidos == [0, 5]                              # pide solo lo nuevo
    assert "eth-12" not in json.dumps(ev)


def test_reinicio_del_motor_se_anota_y_vuelve_a_sincronizar():
    f = Falso()
    f.conn = [vista(epoch="e1"), vista(epoch="e2", seq=1)]
    f.evs = [eventos([], nxt=40)]
    svc, reloj = servicio(f)
    svc.paso()
    reloj.t += 11
    svc.paso()
    assert svc.estado()["eventos"][0]["evento"] == "Network Engine se reinició"
    assert f.pedidos == [0, 0]                               # tras reiniciar empieza de cero, no en 40


def test_motor_apagado_no_disponible_sin_detalle_viejo():
    f = Falso()
    f.conn = [vista(), ConnectionRefusedError("x")]
    svc, reloj = servicio(f)
    svc.paso()
    assert svc.estado()["interfaces"]
    reloj.t += 11
    svc.paso()
    e = svc.estado()
    assert (e["network_engine"], e["internet"], e["interfaces"]) == ("no_disponible", "DESCONOCIDO", [])
    assert e["ultima_respuesta"]                             # se conserva cuándo respondió por última vez


def test_medicion_vieja_de_network_engine_no_se_muestra_como_sana():
    conns = vista()["connections"]
    conns[0].update(stale=True, last_probe_age_s=300.0)
    conns[1].update(health="down", recovering=True, last_probe_age_s=2.0, next_probe_in_s=5.0)
    f = Falso()
    f.conn = [vista(conns=conns)]
    svc, _ = servicio(f)
    svc.paso()
    wifi, cable = svc.estado()["interfaces"]
    assert (wifi["salud"], wifi["disponible"], wifi["latencia_ms"], wifi["medida_hace_s"]) == ("SIN VERIFICAR", False, None, 300.0)
    assert (cable["salud"], cable["latencia_ms"], cable["proximo_sondeo_s"]) == ("RECUPERÁNDOSE", None, 5.0)


def test_copia_vieja_en_monitor_pasa_a_desactualizado():
    """Network Engine respondió, pero desde entonces Monitor no recibe nada (hilo atascado o motor colgado)."""
    svc, reloj = servicio(Falso())
    svc.paso()
    assert svc.estado()["network_engine"] == "disponible"
    reloj.t += svc.umbral_desactualizado_s() + 1             # sin paso(): no llegó ninguna respuesta nueva
    e = svc.estado()
    assert (e["network_engine"], e["internet"]) == ("desactualizado", "DESCONOCIDO")
    assert e["interfaces"] and all(i["salud"] == "SIN VERIFICAR" and i["latencia_ms"] is None and not i["disponible"]
                                   for i in e["interfaces"])
    assert e["antiguedad_s"] > 30


def test_reinicio_del_motor_vuelve_a_disponible_y_lo_anota():
    f = Falso()
    f.conn = [vista(epoch="e1"), ConnectionRefusedError("x"), vista(epoch="e2", seq=1)]
    svc, reloj = servicio(f)
    svc.paso()
    reloj.t += 11
    svc.paso()
    assert svc.estado()["network_engine"] == "no_disponible"
    reloj.t += 60                                            # espera creciente del lector tras el fallo
    svc.paso()
    e = svc.estado()
    assert (e["network_engine"], len(e["interfaces"])) == ("disponible", 2)
    assert e["eventos"][0]["evento"] == "Network Engine se reinició"


def test_credencial_rechazada_detiene_tambien_los_eventos():
    f = Falso()
    f.evs = [ErrorAPI(401, "token_revoked")]
    svc, reloj = servicio(f)
    svc.paso()
    assert svc.estado()["network_engine"] == "credencial_rechazada"
    reloj.t += 100
    svc.paso()
    assert f.pedidos == [0]                                  # ni una petición más hasta reanudar


def test_sin_configuracion_no_hay_servicio_ni_hilo(tmp_path):
    assert SV.desde_entorno({}) == (None, "no_configurado")
    assert SV.desde_entorno({"MONITOREO_NETWORK_TOKEN_FILE": "x", "MONITOREO_NETWORK_DIR": str(tmp_path)})[1] == "directorio_invalido"


def test_ruta_de_monitor_no_espera_a_network_engine():
    f = Falso()
    f.espera = 3.0                                           # Network Engine colgado
    svc, _ = servicio(f)
    svc.iniciar()
    try:
        t0 = time.time()
        e = svc.estado()
        assert time.time() - t0 < 0.2
        assert e["network_engine"] == "consultando"
    finally:
        svc._stop.set()


@pytest.fixture
def app_sin_red(tmp_path, monkeypatch):
    from monitoreo.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "admin_password", ADMIN_PWD)
    monkeypatch.delenv("MONITOREO_NETWORK_TOKEN_FILE", raising=False)
    c = TestClient(create_app(f"sqlite:///{(tmp_path / 'm.db').as_posix()}", start_engine=False, seed_demo=False))
    with c:
        yield c


def _login(c):
    r = c.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PWD})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def test_api_red_sin_configurar_responde_y_monitor_sigue(app_sin_red):
    h = _login(app_sin_red)
    assert app_sin_red.get("/api/red").status_code == 401              # exige sesión
    r = app_sin_red.get("/api/red", headers=h).json()
    assert (r["network_engine"], r["interfaces"], r["eventos"]) == ("no_configurado", [], [])
    assert app_sin_red.app.state.red is None
    assert app_sin_red.get("/api/info", headers=h).status_code == 200


def test_la_interfaz_incluye_la_pantalla_de_red():
    raiz = Path(__file__).resolve().parents[1] / "monitoreo" / "web"
    assert '<script src="/static/red.js"></script>' in (raiz / "index.html").read_text("utf-8")
    js = (raiz / "app.js").read_text("utf-8")
    assert 'red: { t: "Red", f: () => viewRed() }' in js and '"salud", "red", "conexiones"' in js
    red = (raiz / "red.js").read_text("utf-8")
    assert 'api("/api/red")' in red and "POST" not in red                # solo lectura


# ============================================================================================ E4: origen, veredicto, sesiones, validación
class FalsoConSesiones(Falso):
    def __init__(self, ses):
        super().__init__()
        self.ses = ses

    def sessions(self):
        if isinstance(self.ses, Exception):
            raise self.ses
        return self.ses


SESIONES = {"schema": "cauce.sessions/1", "api": "v1", "sessions": [
    {"id": "bs_SECRETO", "label": "lab-forms", "registered_by": "forms-token-SECRETO", "state": "network_down",
     "phase": "requiere_intervencion", "operation": "in_progress", "requires_human": True, "human_reason": "operation_interrupted",
     "network": {"source": "connection", "connection_id": "Wi-Fi-SECRETO", "state": "down"}, "heartbeat": {"last_age_s": 2.0}},
    {"id": "bs_2", "label": "<script>x</script>", "phase": "algo_raro", "network": {"state": "ok"}}],
    "closed": [{"label": "vieja", "phase": "cerrada", "closed_age_s": 30.0}]}


def test_origen_de_los_datos_y_veredicto_por_interfaz():
    f = Falso()
    v = vista()
    v["connections"][0]["status"] = "unverified"
    v["connections"].append({"id": "x", "kind": "tether", "link": "down", "available": False, "health": "unknown"})
    f.conn = [v]
    svc, _ = servicio(f)
    svc.paso()
    e = svc.estado()
    assert e["origen_datos"] == "SIMULADO", "lo simulado se dice siempre"
    assert [i["veredicto"] for i in e["interfaces"]] == ["SIN VERIFICAR", "DEGRADADA", "SIN VERIFICAR"]
    v2 = vista()
    v2["engine"]["mode"] = "real"
    f.conn = [v2]
    svc.lector._proximo = 0
    svc.paso()
    assert svc.estado()["origen_datos"] == "MEDIDO"


def test_sesiones_saneadas_con_intervencion_humana():
    svc, _ = servicio(FalsoConSesiones(SESIONES))
    svc.paso()
    s = svc.estado()["sesiones"]
    assert s["requieren_persona"] == 1
    a = s["activas"][0]
    assert (a["etiqueta"], a["fase"], a["requiere_persona"], a["operacion_en_curso"], a["motivo"], a["red"]) == (
        "lab-forms", "Requiere intervención", True, True, "operation_interrupted", "down")
    assert s["activas"][1]["etiqueta"] == "sesión" and s["activas"][1]["fase"] == "Desconocida"
    assert s["cerradas"][0]["fase"] == "Cerrada" and s["cerradas"][0]["cerrada_hace_s"] == 30.0
    assert "SECRETO" not in json.dumps(s) and "script" not in json.dumps(s)


def test_motor_sin_sesiones_o_que_falla_no_rompe_la_pantalla():
    svc, _ = servicio(Falso())                        # cliente sin sessions(): NE sin el PR #10
    svc.paso()
    assert svc.estado()["sesiones"] is None and svc.estado()["network_engine"] == "disponible"
    svc, _ = servicio(FalsoConSesiones(ErrorAPI(404, "not_found")))
    svc.paso()
    assert svc.estado()["sesiones"] is None and not svc.lector.detenido


def test_ultima_validacion_solo_cifras_y_listas_cerradas(tmp_path):
    assert SV.ultima_validacion({SV.RESULTADOS_ENV: str(tmp_path)}) is None
    d = tmp_path / "integral-20261127-010000"
    d.mkdir()
    (d / "validacion_integral.json").write_text(json.dumps({
        "perfil": "completo",
        "pasos": [{"id": "05", "estado": "PASA", "evidencia": "FÍSICA", "detalle": "IP 192.168.1.5 token abc"},
                  {"id": "08a", "estado": "PASA", "evidencia": "SIMULADA"}, {"id": "08c", "estado": "PENDIENTE", "evidencia": "FÍSICA"}],
        "matriz": {"F1": {"estado": "Aprobado físicamente"}, "F6": {"estado": "Aprobado en simulación"}, "F99": {"estado": "x"},
                   "F2": {"estado": "inventado"}}}), "utf-8")
    r = SV.ultima_validacion({SV.RESULTADOS_ENV: str(tmp_path)})
    assert r["perfil"] == "completo" and r["pasos"]["PASA"] == 2 and r["pasos"]["PENDIENTE"] == 1
    assert r["por_evidencia"]["FÍSICA"]["PASA"] == 1 and r["por_evidencia"]["SIMULADA"]["PASA"] == 1
    assert r["matriz"] == {"F1": "Aprobado físicamente", "F6": "Aprobado en simulación"}
    assert "192.168" not in json.dumps(r) and "token" not in json.dumps(r)


# ============================================================================================ contra el motor real
@real
def test_monitor_con_network_engine_real_en_simulacion(tmp_path, monkeypatch):
    from monitoreo.config import settings
    (tmp_path / "red").mkdir()
    m = Motor(tmp_path / "red").arrancar()
    try:
        m.crear_credencial()
        monkeypatch.setattr(settings, "data_dir", tmp_path)
        monkeypatch.setattr(settings, "admin_password", ADMIN_PWD)
        for k, v in m.entorno().items():
            monkeypatch.setenv(k, v)
        monkeypatch.setenv("MONITOREO_NETWORK_DIR", REPO_RED)
        c = TestClient(create_app(f"sqlite:///{(tmp_path / 'm.db').as_posix()}", start_engine=False, seed_demo=False))
        with c:
            h = _login(c)
            svc = c.app.state.red
            assert svc is not None
            svc.lector.intervalo_s = 5.0

            def esperar(cond, limite=40):
                t0 = time.time()
                while time.time() - t0 < limite:
                    e = c.get("/api/red", headers=h).json()
                    if cond(e):
                        return e
                    svc.lector._proximo = 0                   # acelera la prueba: no esperar el intervalo
                    time.sleep(0.3)
                raise AssertionError(json.dumps(e, ensure_ascii=False))

            e = esperar(lambda e: e["network_engine"] == "disponible" and len(e["interfaces"]) == 3)
            assert {i["tipo"] for i in e["interfaces"]} == {"Cable (Ethernet)", "Datos móviles", "Wi-Fi"}
            assert any(i["latencia_ms"] for i in e["interfaces"])
            assert e["origen_datos"] == "SIMULADO", "el simulador nunca se presenta como medición física"
            assert all(i["veredicto"] in ("DISPONIBLE", "DEGRADADA", "CAÍDA", "SIN VERIFICAR") for i in e["interfaces"])
            # pérdida de conectividad del enlace principal (control del simulador, con la credencial de administración)
            m.admin()._call("POST", "/sim/connections/sim-a/state", {"state": "down"})
            e = esperar(lambda e: any(x["tipo"] == "perdida" for x in e["eventos"]))
            m.admin()._call("POST", "/sim/connections/sim-a/state", {"state": "up"})
            e = esperar(lambda e: any(x["tipo"] == "recuperada" for x in e["eventos"]))
            assert not IP.search(json.dumps(e)) and "sim-a" not in json.dumps(e)
            # Network Engine se apaga: Monitor sigue respondiendo y lo dice
            m.parar()
            t0 = time.time()
            e = esperar(lambda e: e["network_engine"] == "no_disponible")
            assert e["interfaces"] == []
            assert c.get("/api/info", headers=h).status_code == 200
            assert time.time() - t0 < 40
            # Network Engine vuelve (nueva época): Monitor se reengancha solo con la misma credencial
            m.arrancar()
            e = esperar(lambda e: e["network_engine"] == "disponible" and len(e["interfaces"]) == 3, limite=90)
            assert any(x["evento"] == "Network Engine se reinició" for x in e["eventos"])
    finally:
        m.parar()
