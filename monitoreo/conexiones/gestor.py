"""v2.0 · Gestor de conexiones: por qué conexión de red sale el motor hacia el portal.

Reglas (y por qué):

1. Se usa UNA conexión activa, la mejor según `conexion_orden_tipos` (por defecto Ethernet, Wi-Fi,
   USB, módem). No hay reparto ni rotación entre conexiones.
2. Solo se cambia de conexión por fallos de RED (no conecta, se corta, no responde) repetidos
   `conexion_umbral_fallos` veces. **Nunca** por una respuesta del proveedor que limite o niegue el
   acceso (HTTP 429/403 o equivalente): eso es una RESTRICCIÓN, se registra con su motivo, se
   detiene la operación y NO se reintenta por otra conexión. Cambiar de conexión para seguir
   después de una restricción sería sortearla, y el motor no lo hace.
3. Solo admiten conexión de respaldo las operaciones que empiezan algo nuevo o solo leen
   (`nueva_sesion`, `consulta`, `verificacion`). Una sesión del portal ya abierta sigue SIEMPRE
   por la conexión con la que empezó (`continuar_sesion`, `confirmar`): si esa conexión cae, la
   operación falla como «resultado incierto» y la confirmación se resuelve verificando por clave,
   nunca repitiéndola por otra vía.
4. Una conexión caída se vuelve a probar tras `conexion_reintento_s`; la preferida vuelve a usarse
   cuando lleva `conexion_estable_s` respondiendo bien, y solo para operaciones nuevas.
5. Si el destino es la propia máquina (portal de laboratorio en 127.0.0.1) no se elige interfaz:
   se registra «LOCAL». Así el laboratorio no aparenta probar redes físicas que no está probando.

Limitación honesta: el motor elige la conexión fijando la DIRECCIÓN DE ORIGEN de sus conexiones.
Que el sistema operativo saque ese tráfico por esa interfaz depende de su tabla de rutas (en
Windows suele bastar; en Linux con varias salidas hace falta enrutamiento por origen). Esto no se
ha podido verificar con interfaces físicas desde el entorno de desarrollo (PENDIENTE en el equipo).
"""
from __future__ import annotations

import ipaddress
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from typing import Callable

from . import descubrimiento as desc

OPS_CON_RESPALDO = ("nueva_sesion", "consulta", "verificacion")
OPS_SIN_RESPALDO = ("continuar_sesion", "confirmar")
OPERACIONES = OPS_CON_RESPALDO + OPS_SIN_RESPALDO
RESULTADOS = ("OK", "RED", "RESTRICCION", "PORTAL")
LOCAL = "LOCAL"


class SinConexion(Exception):
    """No hay ninguna conexión utilizable para esta operación."""


@dataclass
class Eleccion:
    nombre: str
    tipo: str
    ip_origen: str | None
    motivo: str


class _Estado:
    def __init__(self, i: desc.Interfaz):
        self.interfaz = i
        self.estado = "SIN_PROBAR"               # OK | CAIDA | SIN_PROBAR
        self.fallos_seguidos = 0
        self.exitos = 0
        self.fallos = 0
        self.restricciones = 0
        self.latencias: deque[float] = deque(maxlen=50)
        self.ultimo_error: str | None = None
        self.caida_desde: float | None = None
        self.bien_desde: float | None = None
        self.ultimo_uso: float | None = None


def es_local(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def sonda_tcp(ip_origen: str | None, host: str, puerto: int, timeout: float = 3.0) -> float:
    """Tiempo (ms) de abrir una conexión TCP desde `ip_origen` hasta host:puerto. Lanza OSError si falla.
    Solo abre y cierra la conexión: no envía nada al servicio."""
    t0 = time.perf_counter()
    with socket.create_connection((host, puerto), timeout=timeout,
                                  source_address=(ip_origen, 0) if ip_origen else None):
        pass
    return (time.perf_counter() - t0) * 1000


def _pct(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(round(q * (len(s) - 1))))], 1)


class Gestor:
    def __init__(self, descubrir: Callable[[], list[desc.Interfaz]] = desc.descubrir,
                 sonda: Callable[..., float] = sonda_tcp, reloj: Callable[[], float] = time.monotonic,
                 al_evento: Callable[[str, dict], None] | None = None, cache_s: float = 10.0):
        self._descubrir, self._sonda, self._reloj = descubrir, sonda, reloj
        self.al_evento = al_evento
        self.cache_s = cache_s
        self._lock = threading.RLock()
        self._estados: dict[str, _Estado] = {}
        self._visto = -1e18
        self.activa: str | None = None

    # ------------------------------------------------------------ inventario
    def actualizar(self, forzar: bool = False) -> list[desc.Interfaz]:
        with self._lock:
            if forzar or self._reloj() - self._visto >= self.cache_s:
                vistas = set()
                for i in self._descubrir():
                    vistas.add(i.nombre)
                    e = self._estados.get(i.nombre)
                    if e is None:
                        self._estados[i.nombre] = _Estado(i)
                    else:
                        e.interfaz = i
                # Una interfaz que desapareció (p. ej. un adaptador USB desconectado) deja de ser
                # utilizable: se conserva su historial, pero sin dirección ni actividad.
                for n, e in self._estados.items():
                    if n not in vistas and (e.interfaz.activa or e.interfaz.ipv4):
                        e.interfaz = replace(e.interfaz, activa=False, ipv4=[])
                self._visto = self._reloj()
            return [e.interfaz for e in self._estados.values()]

    def _candidatas(self, params: dict) -> list[_Estado]:
        orden = list(params.get("conexion_orden_tipos") or desc.TIPOS_USABLES)
        fuera = set(params.get("conexion_deshabilitadas") or [])
        cand = [e for e in self._estados.values() if e.interfaz.usable and e.interfaz.nombre not in fuera
                and e.interfaz.tipo in orden]
        return sorted(cand, key=lambda e: (orden.index(e.interfaz.tipo), e.interfaz.nombre))

    def _evento(self, evento: str, **d) -> None:
        if self.al_evento:
            try:
                self.al_evento(evento, d)
            except Exception:      # noqa: BLE001 - registrar nunca debe tumbar la operación
                pass

    def _utilizable(self, e: _Estado, params: dict) -> bool:
        if e.estado != "CAIDA":
            return True
        return self._reloj() - (e.caida_desde or 0) >= float(params.get("conexion_reintento_s", 60))

    # ------------------------------------------------------------ elegir
    def elegir(self, operacion: str, params: dict, host: str | None = None, fija: str | None = None) -> Eleccion:
        if operacion not in OPERACIONES:
            raise ValueError(f"operación desconocida: {operacion}")
        if es_local(host):
            return Eleccion(LOCAL, "LOOPBACK", None, "destino en este mismo equipo (laboratorio)")
        with self._lock:
            self.actualizar()
            if operacion in OPS_SIN_RESPALDO or fija:
                if not fija:
                    raise SinConexion("una sesión abierta debe seguir por la conexión con la que empezó")
                if fija == LOCAL:
                    return Eleccion(LOCAL, "LOOPBACK", None, "sesión iniciada en este mismo equipo")
                e = self._estados.get(fija)
                if e is None or not e.interfaz.usable:
                    raise SinConexion(f"la conexión de esta sesión ({fija}) ya no está disponible; "
                                      "no se cambia de conexión a mitad de una sesión")
                return Eleccion(fija, e.interfaz.tipo, e.interfaz.ipv4[0], "misma conexión que el resto de la sesión")
            cand = self._candidatas(params)
            if not cand:
                raise SinConexion("no hay ninguna conexión de red utilizable")
            actual = self._estados.get(self.activa) if self.activa else None
            elegido, motivo = None, ""
            if actual is not None and actual in cand and self._utilizable(actual, params) and actual.estado != "CAIDA":
                elegido, motivo = actual, "conexión activa"
                mejor = cand[0]
                estable = float(params.get("conexion_estable_s", 120))
                if (mejor is not actual and mejor.estado == "OK" and mejor.bien_desde is not None
                        and self._reloj() - mejor.bien_desde >= estable):
                    elegido, motivo = mejor, f"vuelve la preferida ({mejor.interfaz.tipo}) tras {int(estable)} s estable"
            if elegido is None:
                for e in cand:
                    if self._utilizable(e, params):
                        elegido = e
                        if actual is None:
                            motivo = "primera elección"
                        elif e is actual:
                            motivo = "se vuelve a probar tras el tiempo de espera"
                        else:
                            motivo = f"respaldo: {self.activa} no está disponible ({actual.ultimo_error or actual.estado})"
                        break
            if elegido is None:
                raise SinConexion("todas las conexiones están caídas; se reintentará tras el tiempo de espera")
            nombre = elegido.interfaz.nombre
            if nombre != self.activa:
                self._evento("CAMBIO", conexion=nombre, tipo=elegido.interfaz.tipo, anterior=self.activa,
                             operacion=operacion, motivo=motivo)
                self.activa = nombre
            elegido.ultimo_uso = self._reloj()
            return Eleccion(nombre, elegido.interfaz.tipo, elegido.interfaz.ipv4[0], motivo)

    # ------------------------------------------------------------ resultados
    def registrar(self, nombre: str, operacion: str, resultado: str, params: dict,
                  latencia_ms: float | None = None, detalle: str = "") -> None:
        if resultado not in RESULTADOS:
            raise ValueError(resultado)
        if resultado == "RESTRICCION":
            # registrar y detener; no cuenta como fallo de red y no provoca ningún cambio de conexión
            with self._lock:
                e = self._estados.get(nombre)
                if e is not None:
                    e.restricciones += 1
            self._evento("RESTRICCION", conexion=nombre, operacion=operacion, motivo=detalle[:200])
            return
        if nombre == LOCAL:
            return
        with self._lock:
            e = self._estados.get(nombre)
            if e is None:
                return
            if resultado == "OK":
                e.exitos += 1
                e.fallos_seguidos = 0
                if latencia_ms is not None:
                    e.latencias.append(latencia_ms)
                if e.estado != "OK":
                    if e.estado == "CAIDA":
                        self._evento("RECUPERADA", conexion=nombre, tipo=e.interfaz.tipo, operacion=operacion)
                    e.estado, e.bien_desde, e.caida_desde = "OK", self._reloj(), None
            elif resultado == "RED":
                e.fallos += 1
                e.fallos_seguidos += 1
                e.ultimo_error = detalle[:200] or "fallo de red"
                e.bien_desde = None
                if e.estado != "CAIDA" and e.fallos_seguidos >= int(params.get("conexion_umbral_fallos", 2)):
                    e.estado, e.caida_desde = "CAIDA", self._reloj()
                    self._evento("CAIDA", conexion=nombre, tipo=e.interfaz.tipo, operacion=operacion,
                                 motivo=e.ultimo_error)
                elif e.estado == "CAIDA":
                    e.caida_desde = self._reloj()       # sigue caída: se espera otra vez antes de reintentar
            # PORTAL (p. ej. 5xx): la red funcionó; no dice nada de la conexión

    def marcar_caida(self, nombre: str, operacion: str, motivo: str) -> None:
        """La conexión ni siquiera se pudo establecer (nada llegó al destino): se da por caída ya."""
        with self._lock:
            e = self._estados.get(nombre)
            if e is None or e.estado == "CAIDA":
                if e is not None:
                    e.caida_desde = self._reloj()
                return
            e.fallos += 1
            e.fallos_seguidos += 1
            e.ultimo_error, e.bien_desde = motivo[:200], None
            e.estado, e.caida_desde = "CAIDA", self._reloj()
        self._evento("CAIDA", conexion=nombre, tipo=e.interfaz.tipo, operacion=operacion, motivo=motivo[:200])

    # ------------------------------------------------------------ medir
    def sondear(self, params: dict, host: str, puerto: int) -> list[dict]:
        """Mide cada conexión candidata abriendo una conexión TCP al destino PERMITIDO (sin enviar
        nada). Las caídas solo se prueban cuando ha pasado su tiempo de espera."""
        if es_local(host):
            return []
        self.actualizar(forzar=True)
        out = []
        for e in self._candidatas(params):
            if not self._utilizable(e, params):
                continue
            n = e.interfaz.nombre
            try:
                ms = self._sonda(e.interfaz.ipv4[0], host, puerto)
                self.registrar(n, "consulta", "OK", params, ms)
                out.append({"conexion": n, "ok": True, "latencia_ms": round(ms, 1)})
            except OSError as err:
                self.registrar(n, "consulta", "RED", params, detalle=f"sonda: {err.__class__.__name__}")
                out.append({"conexion": n, "ok": False, "error": err.__class__.__name__})
        return out

    def resumen(self, params: dict) -> dict:
        with self._lock:
            self.actualizar()
            cand = {e.interfaz.nombre for e in self._candidatas(params)}
            fuera = set(params.get("conexion_deshabilitadas") or [])
            filas = []
            for e in sorted(self._estados.values(), key=lambda x: x.interfaz.nombre):
                i = e.interfaz
                filas.append({**i.a_dict(), "estado": e.estado, "activa": i.nombre == self.activa,
                              "candidata": i.nombre in cand, "deshabilitada": i.nombre in fuera,
                              "exitos": e.exitos, "fallos": e.fallos, "fallos_seguidos": e.fallos_seguidos,
                              "restricciones": e.restricciones, "ultimo_error": e.ultimo_error,
                              "latencia_ms": {"p50": _pct(e.latencias, 0.5), "p95": _pct(e.latencias, 0.95),
                                              "n": len(e.latencias)}})
            return {"activa": self.activa, "conexiones": filas}
