"""Consumidor opcional de SOLO LECTURA de Network Engine (monitoreo/red_lectura).

Dos grupos:
- UNITARIAS (siempre corren, sin red): estados, null≠false, errores y esperas, detención por 401/403/400, interruptor, intervalo,
  reinicios, saneado de logs y resultados, independencia de Monitor.
- CONTRA EL MOTOR REAL EN SIMULACIÓN (solo si NETWORK_ENGINE_REPO apunta a un clon de dass-666/cauce-network-engine; se fija el
  commit d6db87f en CI): motor simulado en loopback, credencial `read` FICTICIA creada con el CLI, revocación, reinicio, enlaces caídos y
  pruebas NEGATIVAS de permisos. Datos ficticios; no es una prueba de hardware ni de Windows físico.
"""
import ast
import json
import logging
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from monitoreo.red_lectura import (ClienteNoPermitido, Internet, LectorConectividad, desde_entorno)
from monitoreo.red_lectura import lector as L

RAIZ = Path(__file__).resolve().parents[1]
IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
MAC = re.compile(r"\b[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}\b")
TOKEN_FICTICIO = "tok-ficticio-AbCdEf0123456789"


# ---------------------------------------------------------------------------------------------- ayudas
def vista(state="usable", usable=True, reason=None, epoch="aabbccdd", seq=5, **extra):
    v = {"schema": "cauce.connectivity/1", "api": "v1", "engine": {"version": "0.1.0", "mode": "simulated", "epoch": epoch},
         "generated_at": "2026-11-21T01:16:29Z", "last_seq": seq, "state": state, "usable": usable, "reason": reason,
         "counts": {"connections": 3, "available": 2, "measured": 2},
         "connections": [{"id": "sim-a", "kind": "ethernet", "link": "up", "available": True, "ip": "192.168.1.20",
                          "mac": "aa:bb:cc:dd:ee:ff"}]}
    v.update(extra)
    return v


class ErrorAPI(Exception):
    def __init__(self, status, code, mensaje="host-secreto:8765 10.9.8.7"):
        super().__init__(f"{status} {code}: {mensaje}")
        self.status, self.code, self.message = status, code, mensaje


class Cliente:
    """Cliente de solo lectura falso: devuelve o lanza lo que se le programe."""

    def __init__(self, *respuestas):
        self.respuestas, self.llamadas = list(respuestas), 0

    def connectivity(self):
        self.llamadas += 1
        r = self.respuestas.pop(0) if len(self.respuestas) > 1 else self.respuestas[0]
        if isinstance(r, Exception):
            raise r
        return r


class Reloj:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def avanzar(self, s):
        self.t += s


def lector(cliente, reloj=None, **kw):
    return LectorConectividad(cliente, reloj=reloj or Reloj(), entorno=kw.pop("entorno", {}), **kw)


# ---------------------------------------------------------------------------------------------- estados
def test_tres_estados_y_degradada():
    for v, esperado, degradada in [(vista(), Internet.DISPONIBLE, False),
                                   (vista("degraded", True), Internet.DISPONIBLE, True),
                                   (vista("unavailable", False, "all_links_down"), Internet.NO_DISPONIBLE, False),
                                   (vista("unknown", None, "starting"), Internet.DESCONOCIDO, False)]:
        r = lector(Cliente(v)).leer()
        assert (r.internet, r.degradada, r.hubo_peticion) == (esperado, degradada, True)
    assert lector(Cliente(vista("unavailable", False, "all_links_down"))).leer().razon == "all_links_down"


def test_null_nunca_se_convierte_en_false_ni_lo_incoherente_en_algo_afirmativo():
    r = lector(Cliente(vista("unknown", None))).leer()
    assert r.internet is Internet.DESCONOCIDO and r.internet is not Internet.NO_DISPONIBLE
    incoherentes = [vista("unknown", False), vista("usable", None), vista("usable", False), vista("unavailable", True),
                    vista("usable", 1), vista("unavailable", 0), vista("raro", True), vista(epoch="mala epoca!"),
                    vista(seq=-1), vista(seq=True), {"schema": "otro/1", "state": "usable", "usable": True},
                    {"state": "usable", "usable": True}, "texto", None, [], {"schema": "cauce.connectivity/1"}]
    for v in incoherentes:
        r = lector(Cliente(v)).leer()
        assert r.internet is Internet.DESCONOCIDO and r.razon == "respuesta_invalida", v


# ---------------------------------------------------------------------------------------------- errores y esperas
def test_motor_apagado_o_ilegible_es_desconocido_no_sin_internet_y_con_espera_creciente():
    reloj = Reloj()
    c = Cliente(ConnectionRefusedError("boom 127.0.0.1:8765"))
    lec = lector(c, reloj)
    esperas = []
    for _ in range(6):
        r = lec.leer()
        assert r.internet is Internet.DESCONOCIDO and r.razon == "motor_no_responde" and r.internet is not Internet.NO_DISPONIBLE
        esperas.append(lec.siguiente_espera_s())
        reloj.avanzar(lec.siguiente_espera_s())
    assert esperas == [20.0, 40.0, 60.0, 60.0, 60.0, 60.0]             # 2×, 4×, 8×… con tope de 60 s
    c.respuestas = [vista()]
    assert lec.leer().internet is Internet.DISPONIBLE and lec.fallos == 0 and lec.siguiente_espera_s() == 10.0
    for e in (TimeoutError(), ValueError("json"), json.JSONDecodeError("x", "y", 0)):
        assert lector(Cliente(e)).leer().razon == "motor_no_responde"


def test_un_fallo_no_deja_la_ultima_lectura_buena_como_si_siguiera_vigente():
    reloj, c = Reloj(), Cliente(vista(), OSError())
    lec = lector(c, reloj)
    assert lec.leer().internet is Internet.DISPONIBLE
    reloj.avanzar(11)
    assert lec.leer().internet is Internet.DESCONOCIDO


def test_503_credenciales_bloqueadas_es_transitorio_sin_informacion_y_sigue_consultando():
    reloj = Reloj()
    c = Cliente(ErrorAPI(503, "credentials_locked"), vista())
    lec = lector(c, reloj)
    r = lec.leer()
    assert (r.internet, r.razon) == (Internet.DESCONOCIDO, "credentials_locked") and lec.detenido is None
    reloj.avanzar(25)
    assert lec.leer().internet is Internet.DISPONIBLE and c.llamadas == 2


@pytest.mark.parametrize("estado,codigo", [(401, "unauthorized"), (401, "token_revoked"), (403, "insufficient_scope"),
                                           (400, "unsupported_parameter")])
def test_401_403_400_detienen_el_lector_sin_mas_peticiones_hasta_reanudar(estado, codigo, caplog):
    reloj, c = Reloj(), Cliente(ErrorAPI(estado, codigo), vista())
    lec = lector(c, reloj)
    with caplog.at_level(logging.DEBUG, logger="monitoreo.red_lectura"):
        r = lec.leer()
        assert (r.internet, r.razon) == (Internet.DESCONOCIDO, "detenido_" + codigo) and lec.detenido == codigo
        for _ in range(20):
            reloj.avanzar(120)
            assert lec.leer().razon == "detenido_" + codigo
    assert c.llamadas == 1                                                   # reintentar no arregla una credencial rota
    assert sum(1 for x in caplog.records if x.levelno >= logging.ERROR) == 1       # se avisa UNA vez
    lec.reanudar()
    assert lec.leer().internet is Internet.DISPONIBLE and c.llamadas == 2


def test_otro_4xx_inesperado_no_detiene_pero_se_ve():
    lec = lector(Cliente(ErrorAPI(429, "rate_limited")))
    r = lec.leer()
    assert (r.razon, lec.detenido) == ("rate_limited", None)
    assert lector(Cliente(ErrorAPI(418, "Codigo Raro!"))).leer().razon == "http_418"           # código no saneable → genérico


def test_error_inesperado_del_cliente_no_rompe_a_monitor():
    r = lector(Cliente(RuntimeError("fallo interno 10.0.0.1"))).leer()
    assert (r.internet, r.razon) == (Internet.DESCONOCIDO, "error_inesperado")


# ---------------------------------------------------------------------------------------------- interruptor e intervalo
def test_interruptor_por_entorno_y_por_archivo_hace_cero_peticiones(tmp_path):
    for env in ({"MONITOREO_NETWORK_DESACTIVADO": "1"}, {"CAUCE_STATUS_DISABLED": "1"}):
        c = Cliente(vista())
        r = lector(c, entorno=env).leer()
        assert (r.internet, r.razon, r.hubo_peticion, c.llamadas) == (Internet.DESCONOCIDO, "desactivado", False, 0)
    archivo, c = tmp_path / "apagado", Cliente(vista())
    lec = lector(c, archivo_apagado=str(archivo))
    assert lec.leer().internet is Internet.DISPONIBLE and c.llamadas == 1
    archivo.write_text("x")
    assert lec.leer().razon == "desactivado" and c.llamadas == 1
    archivo.unlink()
    lec._proximo = 0
    assert lec.leer().internet is Internet.DISPONIBLE and c.llamadas == 2


def test_no_consulta_mas_seguido_que_el_intervalo_y_el_minimo_es_5_s():
    reloj, c = Reloj(), Cliente(vista())
    lec = lector(c, reloj, intervalo_s=1)
    assert lec.intervalo_s == 5.0 or lec.intervalo_s == L.INTERVALO_MIN_S
    for _ in range(10):
        lec.leer()
        reloj.avanzar(0.4)
    assert c.llamadas == 1
    reloj.avanzar(10)
    lec.leer()
    assert c.llamadas == 2


def test_reinicio_del_motor_por_epoca_o_secuencia_que_retrocede():
    reloj = Reloj()
    c = Cliente(vista(epoch="aaaa0001", seq=40), vista(epoch="aaaa0001", seq=45), vista(epoch="bbbb0002", seq=50),
                vista(epoch="bbbb0002", seq=3))
    lec = lector(c, reloj)
    for esperado in (0, 0, 1, 2):
        lec.leer()
        assert lec.reinicios == esperado
        reloj.avanzar(11)


# ---------------------------------------------------------------------------------------------- nada sensible
def test_resultados_y_logs_sin_token_ip_mac_ruta_ni_texto_de_excepciones(caplog, tmp_path):
    ruta = tmp_path / "monitor-secreto.token"
    ruta.write_text(TOKEN_FICTICIO)
    reloj = Reloj()
    cliente = Cliente(vista(), OSError("fallo en 10.1.2.3:8765 mac aa:bb:cc:dd:ee:ff " + TOKEN_FICTICIO),
                      ErrorAPI(503, "credentials_locked", "detalle " + TOKEN_FICTICIO), ErrorAPI(401, "token_revoked", str(ruta)))
    lec = lector(cliente, reloj)
    salidas = []
    with caplog.at_level(logging.DEBUG):
        for _ in range(5):
            salidas.append(repr(lec.leer()))
            salidas.append(json.dumps(lec.resumen()))
            reloj.avanzar(200)
    texto = "\n".join(salidas) + "\n" + caplog.text
    assert texto.strip()
    for prohibido in (TOKEN_FICTICIO, "monitor-secreto", "host-secreto", "aa:bb:cc", "sim-a", "ethernet", "boom"):
        assert prohibido not in texto, prohibido
    assert not IP.search(texto) and not MAC.search(texto)
    assert set(L.Lectura.__dataclass_fields__) == {"internet", "razon", "degradada", "conexiones", "disponibles", "hubo_peticion"}


# ---------------------------------------------------------------------------------------------- permisos e independencia
def test_se_rechaza_un_cliente_con_metodos_de_administracion():
    class Admin(Cliente):
        def shutdown(self):
            raise AssertionError("no debe llamarse")

        def request_route(self, *a):
            raise AssertionError("no debe llamarse")

    for malo in (Admin(vista()), object(), Cliente.__new__(Cliente)):
        if hasattr(malo, "connectivity") and not hasattr(malo, "shutdown"):
            continue
        with pytest.raises(ClienteNoPermitido):
            LectorConectividad(malo)


def test_sin_configuracion_o_sin_paquete_el_lector_esta_inactivo_y_no_pide_nada(monkeypatch, tmp_path):
    assert desde_entorno({}).leer().razon == "no_configurado"
    assert desde_entorno({"MONITOREO_NETWORK_DESACTIVADO": "1", "MONITOREO_NETWORK_TOKEN_FILE": "x"}).leer().razon == "desactivado"
    assert desde_entorno({"CAUCE_STATUS_DISABLED": "1"}).leer().razon == "desactivado"
    assert desde_entorno({"MONITOREO_NETWORK_TOKEN_FILE": "x", "MONITOREO_NETWORK_PUERTO": "abc"}).leer().razon == "puerto_invalido"
    assert desde_entorno({"MONITOREO_NETWORK_TOKEN_FILE": "x", "MONITOREO_NETWORK_PUERTO": "80"}).leer().razon == "puerto_invalido"
    monkeypatch.setitem(sys.modules, "cauce", None)                    # simula que el paquete no está instalado
    monkeypatch.setitem(sys.modules, "cauce.client", None)
    r = desde_entorno({"MONITOREO_NETWORK_TOKEN_FILE": str(tmp_path / "t")}).leer()
    assert (r.internet, r.razon, r.hubo_peticion) == (Internet.DESCONOCIDO, "paquete_no_instalado", False)


def test_monitor_arranca_sin_importar_el_consumidor_ni_el_paquete_de_la_red():
    codigo = ("import sys, monitoreo.app\n"
              "bad = [m for m in sys.modules if m == 'cauce' or m.startswith('cauce.') or m.startswith('monitoreo.red_lectura')]\n"
              "assert not bad, bad\nprint('ok')")
    p = subprocess.run([sys.executable, "-c", codigo], cwd=str(RAIZ), capture_output=True, text=True, timeout=120)
    assert p.returncode == 0 and p.stdout.strip() == "ok", p.stderr[-500:]


def test_el_modulo_no_importa_cauce_al_cargarse_ni_toca_el_token_ni_otras_rutas():
    arbol = ast.parse((RAIZ / "monitoreo" / "red_lectura" / "lector.py").read_text("utf-8"))
    for nodo in arbol.body:                                            # nivel de módulo: ningún import de cauce
        if isinstance(nodo, ast.ImportFrom):
            assert not (nodo.module or "").startswith("cauce")
        if isinstance(nodo, ast.Import):
            assert not any(a.name.startswith("cauce") for a in nodo.names)
    fuente = (RAIZ / "monitoreo" / "red_lectura" / "lector.py").read_text("utf-8")
    assert "api.token" not in fuente.replace('"api.token" no', "")
    for ruta in ("/connections", "/diagnostics", "/routes", "/tokens", "/shutdown", "/selection"):
        assert ruta not in fuente


# ============================================================================================== contra el motor real (simulación)
REPO_RED = os.environ.get("NETWORK_ENGINE_REPO")
real = pytest.mark.skipif(not (REPO_RED and Path(REPO_RED, "cauce", "client.py").exists()),
                          reason="NETWORK_ENGINE_REPO no apunta a un clon de cauce-network-engine")


def _puerto_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Motor:
    """Network Engine REAL en modo simulación (subproceso, loopback, datos ficticios) con una credencial `read` ficticia."""

    def __init__(self, tmp):
        self.dir, self.puerto = Path(tmp), _puerto_libre()
        self.cfg = self.dir / "config.json"
        self.cfg.write_text(json.dumps({"data_dir": str(self.dir), "api": {"port": self.puerto}}), "utf-8")
        self.proc = None
        self.token_path = self.dir / "monitor.token"
        self.token_id = None

    def _cli(self, *args):
        return subprocess.run([sys.executable, "-m", "cauce", "--config", str(self.cfg), *args], cwd=REPO_RED,
                              capture_output=True, text=True, timeout=60)

    def arrancar(self):
        self.proc = subprocess.Popen([sys.executable, "-m", "cauce", "--config", str(self.cfg), "sim", "--port", str(self.puerto)],
                                     cwd=REPO_RED, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        limite = time.time() + 30
        while time.time() < limite:
            try:
                with socket.create_connection(("127.0.0.1", self.puerto), timeout=0.5):
                    if (self.dir / "api.token").exists():
                        return self
            except OSError:
                time.sleep(0.2)
        raise RuntimeError("el motor simulado no arrancó")

    def parar(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def crear_credencial(self):
        r = self._cli("token", "create", "--name", "monitor", "--out", str(self.token_path))
        assert r.returncode == 0, r.stderr[-300:]
        self.token_id = re.search(r"Credencial ([0-9a-f]+)", r.stdout).group(1)

    def revocar(self):
        assert self._cli("token", "revoke", self.token_id).returncode == 0

    def entorno(self):
        return {"MONITOREO_NETWORK_TOKEN_FILE": str(self.token_path), "MONITOREO_NETWORK_PUERTO": str(self.puerto)}

    def admin(self):
        sys.path.insert(0, REPO_RED)
        from cauce.client import CauceClient
        from cauce.secrets_store import read_secret
        return CauceClient(read_secret(str(self.dir / "api.token")), port=self.puerto)

    def token_read(self):
        sys.path.insert(0, REPO_RED)
        from cauce.secrets_store import read_secret
        return read_secret(str(self.token_path))

    def http(self, metodo, ruta, token, cuerpo=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.puerto}/api/v1{ruta}", method=metodo,
                                     data=json.dumps(cuerpo).encode() if cuerpo is not None else None)
        req.add_header("Authorization", "Bearer " + token)
        if cuerpo is not None:
            req.add_header("Content-Type", "application/json")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def accesos(self):
        ruta = self.dir / "access.jsonl"
        return [json.loads(x) for x in ruta.read_text("utf-8").splitlines()] if ruta.exists() else []


@pytest.fixture
def motor(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(REPO_RED)          # el paquete `cauce` instalado (aquí: el clon fijado) para el cliente de lectura
    m = Motor(tmp_path).arrancar()
    m.crear_credencial()
    yield m
    m.parar()


def lector_real(motor, reloj=None):
    lec = desde_entorno(motor.entorno())
    if reloj is not None:
        lec._reloj = reloj
    lec._entorno = {}
    return lec


@real
def test_real_disponible_y_solo_toca_la_ruta_de_lectura(motor):
    lec = lector_real(motor)
    r = lec.leer()
    assert (r.internet, r.razon, r.hubo_peticion) == (Internet.DISPONIBLE, "ok", True) and r.conexiones == 3
    mias = [x for x in motor.accesos() if x["who"] == "monitor"]
    assert mias and all((x["method"], x["path"].split("?")[0], x["status"]) == ("GET", "/api/v1/status/connectivity", 200) for x in mias)


@real
def test_real_enlaces_caidos_es_no_disponible_y_el_motor_apagado_es_desconocido(motor):
    reloj = Reloj()
    lec = lector_real(motor, reloj)
    admin = motor.admin()
    for cid in ("sim-a", "sim-b", "sim-c"):
        motor.http("POST", f"/sim/connections/{cid}/state", admin.token, {"state": "down"})
    limite = time.time() + 30
    while time.time() < limite:
        reloj.avanzar(11)
        r = lec.leer()
        if r.internet is Internet.NO_DISPONIBLE:
            break
        time.sleep(0.5)
    assert r.internet is Internet.NO_DISPONIBLE, r
    motor.parar()
    reloj.avanzar(11)
    r = lec.leer()
    assert (r.internet, r.razon) == (Internet.DESCONOCIDO, "motor_no_responde")      # apagado ≠ sin internet


@real
def test_real_revocacion_detiene_el_lector_sin_mas_peticiones(motor):
    reloj = Reloj()
    lec = lector_real(motor, reloj)
    assert lec.leer().internet is Internet.DISPONIBLE
    motor.revocar()
    reloj.avanzar(11)
    r = lec.leer()
    assert r.internet is Internet.DESCONOCIDO and r.razon.startswith("detenido_") and lec.detenido
    antes = len([x for x in motor.accesos() if x["who"] == "monitor"]) + len([x for x in motor.accesos() if x["status"] == 401])
    for _ in range(5):
        reloj.avanzar(120)
        assert lec.leer().razon == r.razon
    despues = len([x for x in motor.accesos() if x["who"] == "monitor"]) + len([x for x in motor.accesos() if x["status"] == 401])
    assert despues == antes                                                         # cero peticiones tras detenerse


@real
def test_real_reinicio_del_motor_la_credencial_read_sobrevive_y_se_detecta(motor):
    reloj = Reloj()
    lec = lector_real(motor, reloj)
    assert lec.leer().internet is Internet.DISPONIBLE and lec.reinicios == 0
    motor.parar()
    reloj.avanzar(11)
    assert lec.leer().razon == "motor_no_responde"
    motor.arrancar()
    limite = time.time() + 40
    while time.time() < limite:
        reloj.avanzar(65)
        r = lec.leer()
        if r.internet is not Internet.DESCONOCIDO or r.razon not in ("motor_no_responde",):
            break
        time.sleep(0.5)
    assert r.internet in (Internet.DISPONIBLE, Internet.DESCONOCIDO) and r.razon != "motor_no_responde", r
    assert lec.reinicios == 1 and lec.detenido is None


@real
def test_real_negativa_la_credencial_read_no_puede_hacer_nada_de_administracion(motor):
    t = motor.token_read()
    admin = motor.admin()
    antes = motor.http("GET", "/status/connectivity", t)[1]
    for metodo, ruta, cuerpo in [("GET", "/connections", None), ("GET", "/events", None), ("GET", "/diagnostics", None),
                                 ("GET", "/tokens", None), ("POST", "/tokens", {"name": "x", "scope": "read"}),
                                 ("DELETE", "/tokens/abc", None), ("POST", "/routes", {"owner": "x", "purpose": "y"}),
                                 ("POST", "/selection", {"connection_id": "sim-a"}), ("POST", "/stop-all", {}),
                                 ("POST", "/shutdown", {}), ("POST", "/rotation", {"name": "r", "pool": ["sim-a"]}),
                                 ("POST", "/sim/connections/sim-a/state", {"state": "down"})]:
        estado, resp = motor.http(metodo, ruta, t, cuerpo)
        assert estado in (403, 404), (metodo, ruta, estado)                       # ninguna ruta de administración con `read`
        if estado == 403:
            assert resp["error"]["code"] == "insufficient_scope"
    estado, resp = motor.http("GET", "/status/connectivity?refresh=1", t)
    assert estado == 400 and resp["error"]["code"] == "unsupported_parameter"        # ni parámetros extra en la vista
    despues = motor.http("GET", "/status/connectivity", t)[1]
    assert despues["state"] == antes["state"] and despues["counts"] == antes["counts"]     # no cambió nada
    assert motor.proc.poll() is None                                                   # /shutdown no apagó el motor
    assert [x["id"] for x in admin.connections()] and len(admin._call("GET", "/tokens")["tokens"]) == 1   # y no creó credenciales


@real
def test_real_un_cliente_de_administracion_no_se_acepta_y_el_token_de_administracion_no_se_usa(motor):
    admin = motor.admin()
    with pytest.raises(ClienteNoPermitido):
        LectorConectividad(admin)
    lec = lector_real(motor)
    lec.leer()
    quien = {x["who"] for x in motor.accesos() if x["path"].endswith("/status/connectivity")}
    assert quien == {"monitor"}                                                      # solo la credencial `read`, nunca la de admin
