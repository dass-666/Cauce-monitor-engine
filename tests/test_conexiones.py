"""v2.0 · Gestor de conexiones: descubrimiento (Linux y Windows con datos fijos), elección, respaldo
SOLO por fallos de red, NUNCA ante una restricción del proveedor, sesión fija y registro por operación.

Las interfaces de red son SIMULADAS (no hay Wi-Fi, USB ni módem en el entorno de pruebas); las
peticiones al portal de prueba sí son reales (127.0.0.1)."""
from __future__ import annotations

import os
import socket
from collections import namedtuple

import pytest
from sqlalchemy import select

from monitoreo import conexiones
from monitoreo.conexiones import descubrimiento as desc
gmod = conexiones.gestor_mod
from monitoreo.conexiones.gestor import Gestor, SinConexion
from monitoreo.config import ENGINE_DEFAULTS
from monitoreo.models import ConexionEvento, Reservacion

P = dict(ENGINE_DEFAULTS)
Addr = namedtuple("Addr", "family address")
Stat = namedtuple("Stat", "isup speed mtu")


# ---------------------------------------------------------------- descubrimiento
def _sys(tmp_path, nombre, tipo=1, device=None, wireless=False):
    d = tmp_path / "net" / nombre
    d.mkdir(parents=True)
    (d / "type").write_text(str(tipo))
    if wireless:
        (d / "wireless").mkdir()
    if device:
        real = tmp_path / "devices" / device
        real.mkdir(parents=True, exist_ok=True)
        os.symlink(real, d / "device")


@pytest.mark.skipif(os.name == "nt", reason="enlaces simbólicos de /sys: solo Linux")
def test_clasificacion_linux_con_datos_del_nucleo(tmp_path):
    _sys(tmp_path, "enp3s0", device="pci0000:00/0000:00:1f.6")
    _sys(tmp_path, "wlp2s0", device="pci0000:00/0000:02:00.0", wireless=True)
    _sys(tmp_path, "enx0a1b2c", device="pci0000:00/0000:00:14.0/usb1/1-2/1-2:1.0")
    _sys(tmp_path, "wwan0", tipo=519, device="pci0000:00/0000:00:14.0/usb1/1-3")
    _sys(tmp_path, "lo", tipo=772)
    _sys(tmp_path, "veth12ab")
    tipos = {n: desc.clasificar_linux(n, str(tmp_path / "net"))[0]
             for n in ("enp3s0", "wlp2s0", "enx0a1b2c", "wwan0", "lo", "veth12ab")}
    assert tipos == {"enp3s0": "ETHERNET", "wlp2s0": "WIFI", "enx0a1b2c": "USB", "wwan0": "MODEM",
                     "lo": "LOOPBACK", "veth12ab": "VIRTUAL"}


def test_clasificacion_windows_con_get_netadapter_y_solo_con_el_nombre():
    ad = {"Ethernet": {"InterfaceDescription": "Intel(R) Ethernet Connection I219-LM", "PhysicalMediaType": "802.3"},
          "Wi-Fi": {"InterfaceDescription": "Intel(R) Wi-Fi 6 AX201", "PhysicalMediaType": "Native 802.11"},
          "Ethernet 2": {"InterfaceDescription": "Remote NDIS based Internet Sharing Device", "PhysicalMediaType": "802.3"},
          "Celular": {"InterfaceDescription": "Qualcomm Snapdragon X55 5G", "PhysicalMediaType": "Wireless WAN"},
          "vEthernet (WSL)": {"InterfaceDescription": "Hyper-V Virtual Ethernet Adapter", "PhysicalMediaType": "802.3"}}
    dirs = {n: [Addr(socket.AF_INET, f"10.0.0.{k}")] for k, n in enumerate(ad, 1)}
    dirs["Loopback Pseudo-Interface 1"] = [Addr(socket.AF_INET, "127.0.0.1")]
    stats = {n: Stat(True, 1000, 1500) for n in dirs}
    got = {i.nombre: i.tipo for i in desc.descubrir("windows", adaptadores=ad, direcciones=dirs, estadisticas=stats)}
    assert got == {"Ethernet": "ETHERNET", "Wi-Fi": "WIFI", "Ethernet 2": "USB", "Celular": "MODEM",
                   "vEthernet (WSL)": "VIRTUAL", "Loopback Pseudo-Interface 1": "LOOPBACK"}
    # sin PowerShell solo queda el nombre: el anclaje USB «Ethernet 2» se ve como ETHERNET (limitación declarada)
    solo = {i.nombre: (i.tipo, i.clasificado_por) for i in desc.descubrir("windows", adaptadores={}, direcciones=dirs,
                                                                          estadisticas=stats)}
    assert solo["Ethernet 2"][0] == "ETHERNET" and "nombre" in solo["Ethernet 2"][1]
    assert solo["Wi-Fi"][0] == "WIFI" and solo["Celular"][0] == "MODEM"


def test_descubrimiento_real_de_este_equipo_no_falla():
    """En el equipo donde corre (Linux en desarrollo, Windows en CI) devuelve algo coherente."""
    ifs = desc.descubrir()
    assert ifs and all(i.tipo in desc.TIPOS for i in ifs)
    assert any(i.tipo == "LOOPBACK" for i in ifs)


# ---------------------------------------------------------------- gestor (interfaces simuladas)
class Reloj:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _ifs(*specs):
    return lambda: [desc.Interfaz(nombre=n, tipo=t, activa=a, ipv4=[ip] if ip else []) for n, t, ip, a in specs]


def _gestor(specs, reloj=None):
    ev = []
    g = Gestor(descubrir=_ifs(*specs), reloj=reloj or Reloj(), cache_s=0,
               al_evento=lambda e, d: ev.append((e, d)))
    return g, ev


TRES = [("eth0", "ETHERNET", "10.0.0.2", True), ("wlan0", "WIFI", "10.0.1.2", True), ("usb0", "USB", "10.0.2.2", True),
        ("lo", "LOOPBACK", "127.0.0.1", True), ("veth0", "VIRTUAL", "172.17.0.1", True), ("wwan0", "MODEM", None, True)]


def test_elige_por_preferencia_y_no_usa_loopback_virtual_ni_sin_direccion():
    g, ev = _gestor(TRES)
    e = g.elegir("nueva_sesion", P, host="portal.ejemplo.test")
    assert (e.nombre, e.ip_origen) == ("eth0", "10.0.0.2") and ev[0][0] == "CAMBIO"
    p = {**P, "conexion_orden_tipos": ["WIFI", "ETHERNET"]}
    g2, _ = _gestor(TRES)
    assert g2.elegir("consulta", p, host="portal.ejemplo.test").nombre == "wlan0"
    g3, _ = _gestor(TRES)
    assert g3.elegir("consulta", {**P, "conexion_deshabilitadas": ["eth0"]}, host="x.test").nombre == "wlan0"
    g4, _ = _gestor([("lo", "LOOPBACK", "127.0.0.1", True), ("veth0", "VIRTUAL", "172.17.0.1", True)])
    with pytest.raises(SinConexion):
        g4.elegir("consulta", P, host="x.test")


def test_destino_local_no_elige_interfaz():
    g, ev = _gestor(TRES)
    for host in ("127.0.0.1", "localhost", "::1"):
        e = g.elegir("nueva_sesion", P, host=host)
        assert e.nombre == conexiones.LOCAL and e.ip_origen is None
    assert ev == []


def test_respaldo_solo_tras_fallos_de_red_y_vuelta_a_la_preferida_cuando_es_estable():
    r = Reloj()
    g, ev = _gestor(TRES, r)
    assert g.elegir("nueva_sesion", P, host="x.test").nombre == "eth0"
    g.registrar("eth0", "nueva_sesion", "RED", P, detalle="ConnectTimeout")
    assert g.elegir("nueva_sesion", P, host="x.test").nombre == "eth0"          # 1 fallo < umbral 2
    g.registrar("eth0", "nueva_sesion", "RED", P, detalle="ConnectTimeout")
    e = g.elegir("nueva_sesion", P, host="x.test")
    assert e.nombre == "wlan0" and "respaldo" in e.motivo
    assert [x[0] for x in ev] == ["CAMBIO", "CAIDA", "CAMBIO"]
    # antes del tiempo de reintento, eth0 no se prueba; después sí (y se mide)
    r.t += P["conexion_reintento_s"] - 1
    assert g.elegir("consulta", P, host="x.test").nombre == "wlan0"
    r.t += 2
    g.registrar("eth0", "consulta", "OK", P, 12.0)                              # sonda: vuelve a responder
    assert ev[-1][0] == "RECUPERADA"
    assert g.elegir("consulta", P, host="x.test").nombre == "wlan0"            # aún no lleva estable_s
    r.t += P["conexion_estable_s"]
    e = g.elegir("nueva_sesion", P, host="x.test")
    assert e.nombre == "eth0" and "preferida" in e.motivo


def test_una_restriccion_del_proveedor_nunca_cambia_la_conexion():
    """429/403 no son fallos de red: por muchas que haya, la conexión no cambia ni se da por caída.
    Cambiar de conexión para seguir sería sortear la restricción."""
    g, ev = _gestor(TRES)
    g.elegir("nueva_sesion", P, host="x.test")
    for _ in range(10):
        g.registrar("eth0", "nueva_sesion", "RESTRICCION", P, detalle="HTTP 429")
    assert g.elegir("nueva_sesion", P, host="x.test").nombre == "eth0"
    r = g.resumen(P)
    eth = next(c for c in r["conexiones"] if c["nombre"] == "eth0")
    assert eth["estado"] != "CAIDA" and eth["fallos"] == 0 and eth["restricciones"] == 10
    assert [x[0] for x in ev].count("RESTRICCION") == 10 and [x[0] for x in ev].count("CAMBIO") == 1


def test_una_sesion_abierta_sigue_por_su_conexion_aunque_haya_otra_mejor():
    g, _ = _gestor(TRES)
    assert g.elegir("continuar_sesion", P, host="x.test", fija="usb0").nombre == "usb0"
    assert g.elegir("confirmar", P, host="x.test", fija="usb0").ip_origen == "10.0.2.2"
    with pytest.raises(SinConexion):
        g.elegir("confirmar", P, host="x.test")                                 # sin conexión fija: no se elige otra
    g2, _ = _gestor([s for s in TRES if s[0] != "usb0"])                         # se desconectó el USB
    with pytest.raises(SinConexion, match="no se cambia de conexión a mitad de una sesión"):
        g2.elegir("confirmar", P, host="x.test", fija="usb0")


def test_sondeo_mide_y_detecta_caidas_sin_enviar_nada():
    medidas = {"10.0.0.2": OSError("unreachable"), "10.0.1.2": 8.5, "10.0.2.2": 30.0}

    def sonda(ip, host, puerto):
        v = medidas[ip]
        if isinstance(v, Exception):
            raise v
        return v
    g = Gestor(descubrir=_ifs(*TRES), sonda=sonda, cache_s=0)
    p = {**P, "conexion_umbral_fallos": 1}
    out = {x["conexion"]: x for x in g.sondear(p, "x.test", 443)}
    assert out["eth0"]["ok"] is False and out["wlan0"]["latencia_ms"] == 8.5 and out["usb0"]["ok"]
    assert g.elegir("consulta", p, host="x.test").nombre == "wlan0"
    assert g.sondear(p, "127.0.0.1", 8200) == []


# ---------------------------------------------------------------- integración con el portal de prueba
@pytest.fixture
def gestor_simulado(monkeypatch):
    """Dos «conexiones» simuladas hacia el portal de 127.0.0.1: «eth-sim» con una dirección que no
    existe en el equipo (no se puede conectar: como un cable desenchufado) y «wifi-sim» por 127.0.0.1.
    Se trata el destino como remoto para que el gestor elija interfaz de verdad."""
    g = Gestor(descubrir=_ifs(("eth-sim", "ETHERNET", "192.0.2.123", True), ("wifi-sim", "WIFI", "127.0.0.1", True)),
               cache_s=0)
    monkeypatch.setattr(gmod, "es_local", lambda host: False)
    conexiones.usar(g)
    yield g
    conexiones.usar(None)


from .captura_util import MANUALES  # noqa: E402
from .test_ventanilla import con_expediente, lab, portal  # noqa: E402,F401  (fixtures)


def test_lab_local_registra_conexion_LOCAL_y_uso_por_operacion(lab):
    conexiones.usar(None)
    h = lab.ops["op1"]
    a = con_expediente(lab, h)
    eid = a["expediente"]["id"]
    rid, _ = lab.hasta_fecha(eid, h)
    with lab.db.session() as s:
        assert s.get(Reservacion, rid).conexion == conexiones.LOCAL
        usos = s.scalars(select(ConexionEvento).where(ConexionEvento.evento == "USO")).all()
    assert usos and all(u.conexion == conexiones.LOCAL and u.detalle.get("reservacion_id") == rid for u in usos)
    assert {u.operacion for u in usos} >= {"nueva_sesion"}
    texto = str([u.detalle for u in usos])
    assert "Persona" not in texto and "@" not in texto                         # sin datos de personas


def test_respaldo_real_si_la_conexion_no_se_establece(lab, gestor_simulado):
    """eth-sim no puede conectar (nada llega al portal): la MISMA operación se repite una vez por
    wifi-sim, la sesión queda ligada a wifi-sim y el resto del recorrido sigue por ella."""
    h = lab.ops["op1"]
    a = con_expediente(lab, h)
    eid = a["expediente"]["id"]
    rid, _ = lab.hasta_fecha(eid, h)
    r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
    assert r.status_code == 200 and r.json()["reservacion"]["estado"] == "CONFIRMADA", r.text
    conexiones.volcar(lab.db)
    with lab.db.session() as s:
        assert s.get(Reservacion, rid).conexion == "wifi-sim"
        ev = s.scalars(select(ConexionEvento).order_by(ConexionEvento.id)).all()
    tipos = [(e.evento, e.conexion) for e in ev]
    assert ("CAIDA", "eth-sim") in tipos and ("CAMBIO", "wifi-sim") in tipos
    usos = [e for e in ev if e.evento == "USO"]
    assert any(u.detalle.get("respaldo_desde") == "eth-sim" for u in usos)
    assert all(u.conexion == "wifi-sim" for u in usos if u.operacion in ("continuar_sesion", "confirmar"))


def test_restriccion_429_detiene_la_operacion_sin_cambiar_de_conexion(lab, gestor_simulado):
    gestor_simulado.marcar_caida("eth-sim", "consulta", "simulada")             # trabajar ya por wifi-sim
    h = lab.ops["op1"]
    a = con_expediente(lab, h)
    eid = a["expediente"]["id"]
    v = lab.c.post(f"/api/expedientes/{eid}/preparar", headers=h)
    assert v.status_code == 200, v.text
    res_id = v.json()["reservacion"]["id"]
    lab.portal.fallos(modo="limite")
    try:
        r = lab.c.post(f"/api/reservaciones/{res_id}/enviar-datos", headers=h,
                       json={"revisado": True, "manuales": dict(MANUALES)})
    finally:
        aplicados = lab.portal.ctl("GET", "/control/estado")["fallos"].get("aplicados", {})
        lab.portal.fallos(reset=True)
    assert r.status_code == 409 and r.json()["detail"]["tipo"] == "RESTRICCION", r.text
    assert aplicados.get("limite") == 1                                        # UNA petición: ni reintento ni otra conexión
    conexiones.volcar(lab.db)
    with lab.db.session() as s:
        res = s.get(Reservacion, res_id)
        assert res.error_codigo == "RESTRICCION_PROVEEDOR" and res.estado == "PREPARADA"
        ev = s.scalars(select(ConexionEvento).order_by(ConexionEvento.id)).all()
    restr = [e for e in ev if e.evento == "RESTRICCION"]
    assert len(restr) == 1 and restr[0].conexion == "wifi-sim" and "429" in restr[0].detalle["motivo"]
    assert not any(e.evento == "CAMBIO" and e.ts >= restr[0].ts for e in ev)
    assert gestor_simulado.resumen(P)["activa"] == "wifi-sim"
