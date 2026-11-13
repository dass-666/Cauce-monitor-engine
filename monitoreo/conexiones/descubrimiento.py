"""v2.0 · Descubrimiento de las conexiones de red del equipo (Linux y Windows).

Clasifica cada interfaz en ETHERNET, WIFI, USB (módem/teléfono por USB o adaptador USB), MODEM
(banda ancha móvil, PPP), LOOPBACK, VIRTUAL o DESCONOCIDO, y dice CÓMO lo decidió:

- Linux: datos del núcleo en /sys/class/net (directorio `wireless`, tipo ARPHRD, si el
  dispositivo cuelga de un bus USB, nombres wwan*/ppp*). Es fiable.
- Windows: si PowerShell responde, `Get-NetAdapter` (descripción y tipo de medio físico); si no,
  solo el nombre de la interfaz («Wi-Fi», «Ethernet», «Celular»…), que es una HEURÍSTICA: un
  teléfono compartiendo internet por USB suele llamarse «Ethernet 2» y se clasificaría mal.

Solo LEE el estado del sistema: no cambia rutas, métricas, direcciones ni nada del equipo.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass, field

import psutil

TIPOS = ("ETHERNET", "WIFI", "USB", "MODEM", "LOOPBACK", "VIRTUAL", "DESCONOCIDO")
TIPOS_USABLES = ("ETHERNET", "WIFI", "USB", "MODEM")       # las que el gestor puede elegir para salir

# tipos ARPHRD de Linux (include/uapi/linux/if_arp.h)
_ARPHRD = {1: "ETHER", 512: "PPP", 519: "RAWIP", 772: "LOOPBACK", 776: "SIT", 778: "IPGRE", 65534: "NONE"}
_VIRTUALES = ("veth", "docker", "br-", "virbr", "vmnet", "vboxnet", "tun", "tap", "ifb", "dummy", "bond",
              "team", "wg", "zt", "tailscale", "lxc", "cni", "flannel", "vxlan", "sit", "ip6tnl", "gre")


@dataclass
class Interfaz:
    nombre: str
    tipo: str
    activa: bool
    ipv4: list[str] = field(default_factory=list)
    velocidad_mbps: int | None = None
    mtu: int | None = None
    descripcion: str | None = None
    clasificado_por: str = ""                 # de dónde salió el tipo (núcleo, Get-NetAdapter o nombre)

    def a_dict(self) -> dict:
        return asdict(self)

    @property
    def usable(self) -> bool:
        return self.activa and self.tipo in TIPOS_USABLES and bool(self.ipv4)


# ---------------------------------------------------------------- Linux
def _leer(ruta: str) -> str | None:
    try:
        with open(ruta, encoding="ascii", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return None


def clasificar_linux(nombre: str, sysnet: str = "/sys/class/net") -> tuple[str, str]:
    base = os.path.join(sysnet, nombre)
    tipo_arp = _leer(os.path.join(base, "type"))
    arp = _ARPHRD.get(int(tipo_arp)) if tipo_arp and tipo_arp.isdigit() else None
    if arp == "LOOPBACK" or nombre == "lo":
        return "LOOPBACK", "núcleo: tipo LOOPBACK"
    if arp in ("PPP", "RAWIP") or nombre.startswith(("wwan", "ppp", "wwp")):
        return "MODEM", f"núcleo: tipo {arp or '?'} / nombre {nombre}"
    if os.path.isdir(os.path.join(base, "wireless")) or os.path.exists(os.path.join(base, "phy80211")):
        return "WIFI", "núcleo: interfaz inalámbrica (wireless)"
    disp = os.path.join(base, "device")
    if os.path.exists(disp):
        real = os.path.realpath(disp)
        if "/usb" in real:
            return "USB", "núcleo: el dispositivo cuelga de un bus USB"
        if arp == "ETHER":
            return "ETHERNET", "núcleo: dispositivo físico de tipo Ethernet"
    if nombre.startswith(_VIRTUALES) or not os.path.exists(disp):
        return "VIRTUAL", "núcleo: sin dispositivo físico"
    return "DESCONOCIDO", f"núcleo: tipo {arp or tipo_arp}"


# ---------------------------------------------------------------- Windows
def clasificar_windows_nombre(nombre: str, descripcion: str | None = None, medio: str | None = None) -> tuple[str, str]:
    d = f"{nombre} {descripcion or ''}".lower()
    m = (medio or "").lower()
    if "loopback" in d:
        return "LOOPBACK", "Windows: loopback"
    if any(x in d for x in ("hyper-v", "vethernet", "virtualbox", "vmware", "wsl", "tap-", "wireguard", "tailscale",
                             "zerotier", "teredo", "isatap", "6to4", "kernel debug", "wan miniport")):
        return "VIRTUAL", "Windows: adaptador virtual"
    if "remote ndis" in d or "rndis" in d or ("usb" in d and "wireless" not in m):
        return "USB", "Windows: adaptador USB / anclaje de red por USB" + (" (descripción)" if descripcion else " (nombre)")
    if m in ("wireless wan",) or any(x in d for x in ("cellular", "celular", "mobile broadband", "banda ancha móvil", "wwan")):
        return "MODEM", "Windows: banda ancha móvil" + (" (tipo de medio)" if m else " (nombre)")
    if m in ("native 802.11", "wireless lan") or any(x in d for x in ("wi-fi", "wifi", "wireless", "wlan", "802.11")):
        return "WIFI", "Windows: inalámbrica" + (" (tipo de medio)" if m else " (nombre)")
    if m == "802.3" or "ethernet" in d:
        return "ETHERNET", "Windows: Ethernet" + (" (tipo de medio)" if m else " (nombre; un anclaje USB también puede llamarse así)")
    return "DESCONOCIDO", "Windows: sin datos suficientes"


def _get_netadapter() -> dict[str, dict]:
    """Descripción y tipo de medio de cada adaptador (Windows). Si PowerShell no responde en 6 s,
    se devuelve {} y se clasifica solo por el nombre."""
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            "Get-NetAdapter | Select-Object Name,InterfaceDescription,PhysicalMediaType | ConvertTo-Json -Compress"],
                           capture_output=True, text=True, timeout=6,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        datos = json.loads(p.stdout or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    if isinstance(datos, dict):
        datos = [datos]
    return {d.get("Name"): d for d in datos if isinstance(d, dict) and d.get("Name")}


# ---------------------------------------------------------------- común
def descubrir(sistema: str | None = None, sysnet: str = "/sys/class/net", adaptadores: dict | None = None,
              direcciones: dict | None = None, estadisticas: dict | None = None) -> list[Interfaz]:
    """Interfaces del equipo. Los parámetros permiten probar con datos fijos (Linux o Windows)."""
    sistema = sistema or ("windows" if sys.platform.startswith("win") else "linux")
    direcciones = direcciones if direcciones is not None else psutil.net_if_addrs()
    estadisticas = estadisticas if estadisticas is not None else psutil.net_if_stats()
    if sistema == "windows" and adaptadores is None:
        adaptadores = _get_netadapter()
    out = []
    for nombre, addrs in sorted(direcciones.items()):
        st = estadisticas.get(nombre)
        ipv4 = [a.address for a in addrs if a.family == socket.AF_INET]
        desc = None
        if sistema == "windows":
            ad = (adaptadores or {}).get(nombre) or {}
            desc = ad.get("InterfaceDescription")
            tipo, como = clasificar_windows_nombre(nombre, desc, ad.get("PhysicalMediaType"))
        else:
            tipo, como = clasificar_linux(nombre, sysnet)
        out.append(Interfaz(nombre=nombre, tipo=tipo, activa=bool(st and st.isup), ipv4=ipv4,
                            velocidad_mbps=(st.speed or None) if st else None, mtu=st.mtu if st else None,
                            descripcion=desc, clasificado_por=como))
    return out
