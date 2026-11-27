"""Servicio de conectividad para la interfaz de Monitor: la ÚNICA fuente de estado de red que se muestra.

Monitor no mide la red: pregunta a Network Engine (solo lectura, `/status/connectivity` y `/status/events`) desde un
hilo propio y guarda la última respuesta. La API de Monitor (`/api/red`) solo lee esa copia en memoria, así que:

- Si Network Engine está apagado, lento o rechaza la credencial, Monitor no se bloquea ni falla: muestra
  «Network Engine no disponible» y sigue trabajando.
- Sin configuración, el servicio no existe: no se arranca ningún hilo ni se importa el paquete de la red.

Saneado propio (además del de lector.py): por interfaz solo se guarda el TIPO (lista cerrada), la salud (lista cerrada),
si está disponible, la latencia media y la disponibilidad. Nunca IP, MAC, nombres de adaptador ni identificadores del
motor; las interfaces se nombran «Wi-Fi 1», «Cable (Ethernet) 1»…, y los eventos usan esos mismos nombres.
Esto es lectura de estado, no prueba física: hasta la validación con dos enlaces reales, ver docs/RED_NETWORK_ENGINE.md.
"""
from __future__ import annotations

import collections
import datetime as _dt
import logging
import threading
import time
from typing import Any, Callable, Optional

from .lector import LectorConectividad

log = logging.getLogger("monitoreo.red_lectura")

TIPOS = {"ethernet": "Cable (Ethernet)", "wifi": "Wi-Fi", "mobile": "Datos móviles", "tether": "Teléfono (anclaje)",
         "vpn": "VPN", "virtual": "Virtual", "loopback": "Este equipo"}
SALUD = {"healthy": "SANA", "degraded": "DEGRADADA", "down": "CAÍDA", "unknown": "SIN MEDIR"}
SIN_VERIFICAR = "SIN VERIFICAR"       # había una medición, pero ya no se puede confirmar que siga valiendo
RECUPERANDO = "RECUPERÁNDOSE"         # estaba caída y respondió bien una vez; Network Engine la está confirmando
# Si la última respuesta buena de Network Engine es más vieja que esto, lo que Monitor tiene ya no es estado actual
# (hilo atascado, Network Engine colgado sin cerrar la conexión…): se muestra «desactualizado», nunca «sana».
DESACTUALIZADO_MIN_S = 30.0
# Razones del lector que significan «Monitor no está configurado para leer la red» (no es un fallo de Network Engine)
RAZONES_INACTIVO = {"no_configurado", "desactivado", "paquete_no_instalado", "token_ilegible", "puerto_invalido",
                    "directorio_invalido"}
MAX_EVENTOS = 50


def _iso(ts: Optional[float]) -> Optional[str]:
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None


def _num(v: Any, maximo: float) -> Optional[float]:
    return round(float(v), 1) if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= maximo else None


class ServicioRed:
    def __init__(self, lector_factory: Callable[..., LectorConectividad], reloj: Callable[[], float] = time.time):
        self._lock = threading.Lock()
        self._reloj = reloj
        self._interfaces: list[dict] = []
        self._nombres: dict[str, str] = {}            # id del motor -> «Wi-Fi 1» (el id nunca sale de aquí)
        self._eventos: collections.deque = collections.deque(maxlen=MAX_EVENTOS)
        self._desde: Optional[int] = None
        self._epoca: Optional[str] = None
        self._ultima_ok: Optional[float] = None
        self._actualizado: Optional[float] = None
        self._lectura = None
        self._vio_respuesta = False
        self._motor_responde: Optional[bool] = None
        self.lector = lector_factory(al_leer=self._detalle)
        self._stop = threading.Event()
        self._hilo: Optional[threading.Thread] = None

    # ----------------------------------------------------------------- detalle saneado por interfaz
    def _detalle(self, v: dict) -> None:
        conns = v.get("connections") if isinstance(v.get("connections"), list) else []
        cuenta: dict[str, int] = {}
        nombres, filas = {}, []
        for c in sorted((c for c in conns if isinstance(c, dict)), key=lambda c: str(c.get("id"))):
            tipo = TIPOS.get(str(c.get("kind")), "Otra")
            cuenta[tipo] = cuenta.get(tipo, 0) + 1
            nombre = f"{tipo} {cuenta[tipo]}"
            if isinstance(c.get("id"), str):
                nombres[c["id"]] = nombre
            vieja = c.get("stale") is True                     # Network Engine ya no está midiendo esta interfaz
            salud = SIN_VERIFICAR if vieja else SALUD.get(str(c.get("health")), "SIN MEDIR")
            if not vieja and c.get("health") == "down" and c.get("recovering") is True:
                salud = RECUPERANDO
            filas.append({"nombre": nombre, "tipo": tipo, "enlace": "activo" if c.get("link") == "up" else "sin enlace",
                          "salud": salud, "disponible": c.get("available") is True and not vieja,
                          # una interfaz caída o sin verificar no tiene latencia actual
                          "latencia_ms": None if vieja or c.get("health") == "down" else _num(c.get("avg_latency_ms"), 60_000),
                          "disponibilidad_pct": _num(c.get("availability_pct"), 100),
                          "medida_hace_s": _num(c.get("last_probe_age_s"), 86_400),
                          "proximo_sondeo_s": _num(c.get("next_probe_in_s"), 86_400)})
        epoca = (v.get("engine") or {}).get("epoch")
        with self._lock:
            self._interfaces, self._nombres = filas[:16], nombres
            if epoca != self._epoca:
                if self._epoca is not None:
                    self._evento("Network Engine se reinició", None, None)
                self._epoca, self._desde = epoca, None
            self._ultima_ok = self._reloj()
            self._vio_respuesta = True

    def _evento(self, texto: str, interfaz: Optional[str], ts: Optional[str], tipo: str = "info") -> None:
        self._eventos.appendleft({"ts": ts or _iso(self._reloj()), "evento": texto, "interfaz": interfaz, "tipo": tipo})

    def _traducir(self, e: dict) -> None:
        kind = e.get("kind")
        nombre = self._nombres.get(e.get("connection_id")) or "una interfaz"
        if kind == "health.change":
            nuevo, viejo = e.get("new"), e.get("old")
            if nuevo == "down" and viejo in ("healthy", "degraded"):
                self._evento("Pérdida de conectividad", nombre, e.get("ts"), "perdida")
            elif nuevo == "down":
                self._evento("Sin conectividad", nombre, e.get("ts"), "perdida")       # nunca estuvo sana desde que se mide
            elif nuevo == "degraded":
                self._evento("Conectividad degradada", nombre, e.get("ts"), "degradada")
            elif nuevo == "healthy" and viejo not in (None, "unknown", "down"):
                self._evento("Vuelve a estar sana", nombre, e.get("ts"), "recuperada")
        elif kind == "health.recovered":
            self._evento("Conectividad recuperada", nombre, e.get("ts"), "recuperada")
        elif kind == "inventory.added":
            self._evento("Interfaz detectada", nombre, e.get("ts"))
        elif kind == "inventory.removed":
            self._evento("Interfaz retirada", nombre, e.get("ts"), "perdida")

    # ----------------------------------------------------------------- ciclo
    def paso(self) -> None:
        """Una lectura (respeta el intervalo y las esperas del lector) y, si respondió, los eventos nuevos."""
        with self._lock:
            self._vio_respuesta = False
        lectura = self.lector.leer()
        with self._lock:
            self._lectura = lectura
            self._actualizado = self._reloj()
            if lectura.hubo_peticion:
                self._motor_responde = self._vio_respuesta
                if not self._vio_respuesta:
                    self._interfaces = []             # sin respuesta: no se muestra un detalle viejo como actual
        if not lectura.hubo_peticion or self._estado_motor(lectura) != "disponible":
            return
        desde = self._desde
        v = self.lector.leer_eventos(desde or 0)
        if not v:
            return
        with self._lock:
            if desde is None or v.get("resync_required"):
                # Primera lectura o reinicio: no se vuelca el historial completo, solo lo que pase desde ahora
                # (salvo pérdidas/recuperaciones recientes, que sí se muestran).
                for e in (v.get("events") or [])[-MAX_EVENTOS:]:
                    if isinstance(e, dict):
                        self._traducir(e)
            else:
                for e in v.get("events") or []:
                    if isinstance(e, dict):
                        self._traducir(e)
            nxt = v.get("next_since")
            self._desde = nxt if isinstance(nxt, int) and not isinstance(nxt, bool) and nxt >= 0 else self._desde

    def _bucle(self) -> None:
        while not self._stop.is_set():
            try:
                self.paso()
            except Exception:                                              # noqa: BLE001  nunca tumba a Monitor
                log.warning("red: fallo inesperado en el servicio de conectividad")
            self._stop.wait(min(5.0, self.lector.intervalo_s))

    def iniciar(self) -> "ServicioRed":
        self._hilo = threading.Thread(target=self._bucle, name="monitoreo-red", daemon=True)
        self._hilo.start()
        return self

    def detener(self) -> None:
        self._stop.set()
        if self._hilo:
            self._hilo.join(timeout=5)

    # ----------------------------------------------------------------- vista
    def _estado_motor(self, lectura) -> str:
        """disponible | no_disponible | credencial_rechazada | consultando | razón de inactividad del lector.
        (estado() añade «desactualizado» cuando la última respuesta buena es demasiado vieja.)"""
        if lectura is None:
            return "consultando"
        if self.lector.detenido:
            return "credencial_rechazada"
        if lectura.razon in RAZONES_INACTIVO:
            return lectura.razon
        if self._motor_responde is None:
            return "consultando"
        return "disponible" if self._motor_responde else "no_disponible"

    def umbral_desactualizado_s(self) -> float:
        return max(DESACTUALIZADO_MIN_S, 3 * float(self.lector.intervalo_s))

    def estado(self) -> dict:
        with self._lock:
            l = self._lectura
            motor = self._estado_motor(l)
            antiguedad = self._reloj() - self._ultima_ok if self._ultima_ok else None
            interfaces = list(self._interfaces) if motor == "disponible" else []
            internet = l.internet.value if l else None
            if motor == "disponible" and antiguedad is not None and antiguedad > self.umbral_desactualizado_s():
                # La copia de Monitor es vieja: se conserva qué interfaces había, pero ninguna se da por sana.
                motor, internet = "desactualizado", "DESCONOCIDO"
                interfaces = [{**i, "salud": SIN_VERIFICAR, "disponible": False, "latencia_ms": None} for i in interfaces]
            return {
                "fuente": "network_engine",
                "network_engine": motor,
                "internet": internet,
                "razon": l.razon if l else None,
                "degradada": bool(l and l.degradada),
                "conexiones": l.conexiones if l else None,
                "disponibles": l.disponibles if l else None,
                "interfaces": interfaces,
                "antiguedad_s": round(antiguedad, 1) if antiguedad is not None else None,
                "eventos": list(self._eventos),
                "actualizado": _iso(self._actualizado),
                "ultima_respuesta": _iso(self._ultima_ok),
                "reinicios": self.lector.reinicios,
                "nota": "Estado según Network Engine (solo lectura). No es una prueba física de los enlaces.",
            }


def estado_no_configurado(razon: str = "no_configurado") -> dict:
    return {"fuente": "network_engine", "network_engine": razon, "internet": None, "razon": razon, "degradada": False,
            "conexiones": None, "disponibles": None, "interfaces": [], "eventos": [], "actualizado": None, "antiguedad_s": None,
            "ultima_respuesta": None, "reinicios": 0,
            "nota": "Monitor no está configurado para leer Network Engine (MONITOREO_NETWORK_TOKEN_FILE). Monitor funciona igual."}


def desde_entorno(entorno=None) -> tuple[Optional[ServicioRed], str]:
    """(servicio sin arrancar, "") si hay configuración; (None, razón) si no (sin hilo ni peticiones)."""
    from .lector import desde_entorno as lector_desde_entorno
    prueba = lector_desde_entorno(entorno)
    if not prueba.activo:                          # sin token, sin paquete, desactivado…: no hay servicio
        return None, prueba.razon_inactivo
    return ServicioRed(lambda **kw: lector_desde_entorno(entorno, **kw)), ""
