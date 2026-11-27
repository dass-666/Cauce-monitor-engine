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
import json
import logging
import os
import re
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
# Veredicto por interfaz que da Network Engine desde E4 (`status`); si no viene, se deduce de la salud.
VEREDICTO = {"ok": "DISPONIBLE", "degraded": "DEGRADADA", "down": "CAÍDA", "unverified": "SIN VERIFICAR"}
# Origen de los datos: el motor dice si mide de verdad o si es su simulador. Nunca se presenta lo simulado como físico.
ORIGEN = {"simulated": "SIMULADO", "real": "MEDIDO"}
# Fases de las sesiones de navegador (Network Engine, lista cerrada) -> texto para personas
FASES = {"iniciada": "Iniciada", "activa": "Activa", "perdida_conectividad": "Pérdida de conectividad",
         "recuperada": "Recuperada", "requiere_intervencion": "Requiere intervención", "incierta": "Estado incierto",
         "interrumpida": "Interrumpida", "cerrada": "Cerrada"}
MAX_SESIONES = 8
_ETIQUETA = re.compile(r"^[A-Za-z0-9 _.-]{1,40}$")
_CODIGO_S = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
# Resultados de la validación integral del laboratorio (opcional; solo lectura de un JSON local)
RESULTADOS_ENV = "MONITOREO_RESULTADOS_VALIDACION"
_ESTADOS_PRUEBA = ("PASA", "FALLA", "PENDIENTE", "OMITIDA", "INFO")
_EVIDENCIAS = ("FÍSICA", "LOCAL", "SIMULADA")
_MATRIZ = ("Aprobado físicamente", "Aprobado en simulación", "Fallido", "Pendiente")


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
        self._origen: Optional[str] = None
        self._sesiones: Optional[dict] = None
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
            veredicto = VEREDICTO.get(str(c.get("status")))
            if veredicto is None:                                  # motor anterior a E4: se deduce, sin inventar
                veredicto = ("SIN VERIFICAR" if vieja or c.get("health") in (None, "unknown") else
                             "CAÍDA" if c.get("health") == "down" or c.get("link") != "up" else
                             "DEGRADADA" if c.get("health") == "degraded" else "DISPONIBLE")
            if vieja:
                veredicto = "SIN VERIFICAR"
            filas.append({"nombre": nombre, "tipo": tipo, "enlace": "activo" if c.get("link") == "up" else "sin enlace",
                          "salud": salud, "veredicto": veredicto, "disponible": c.get("available") is True and not vieja,
                          # una interfaz caída o sin verificar no tiene latencia actual
                          "latencia_ms": None if vieja or c.get("health") == "down" else _num(c.get("avg_latency_ms"), 60_000),
                          "disponibilidad_pct": _num(c.get("availability_pct"), 100),
                          "medida_hace_s": _num(c.get("last_probe_age_s"), 86_400),
                          "proximo_sondeo_s": _num(c.get("next_probe_in_s"), 86_400)})
        epoca = (v.get("engine") or {}).get("epoch")
        origen = ORIGEN.get(str((v.get("engine") or {}).get("mode")), "DESCONOCIDO")
        with self._lock:
            self._interfaces, self._nombres, self._origen = filas[:16], nombres, origen
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
        elif kind == "inventory.reidentified":
            self._evento("Interfaz reconectada con otro identificador", nombre, e.get("ts"))
        elif kind == "system.resume_detected":
            self._evento("El equipo se reanudó (suspensión): se vuelve a medir", None, e.get("ts"))
        elif kind == "session.requires_human":
            self._evento("Sesión de navegador: requiere intervención", None, e.get("ts"), "perdida")
        elif kind == "session.recovered":
            self._evento("Sesión de navegador recuperada", None, e.get("ts"), "recuperada")
        elif kind == "session.human_resolved":
            self._evento("Sesión de navegador revisada por una persona", None, e.get("ts"))

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
        ses = self.lector.leer_sesiones()
        with self._lock:
            self._sesiones = self._sanear_sesiones(ses) if ses else None

    @staticmethod
    def _sanear_sesiones(v: dict) -> dict:
        """Solo etiqueta (texto corto validado), fase, estado de red y si pide a una persona; nunca ids ni credenciales."""
        def fila(x: dict, cerrada: bool = False) -> Optional[dict]:
            if not isinstance(x, dict):
                return None
            fase = "cerrada" if cerrada else x.get("phase")
            if fase not in FASES:
                fase = None
            etiqueta = x.get("label") if isinstance(x.get("label"), str) and _ETIQUETA.match(x["label"]) else "sesión"
            red = (x.get("network") or {}).get("state") if isinstance(x.get("network"), dict) else None
            hb = x.get("heartbeat") if isinstance(x.get("heartbeat"), dict) else {}
            motivo = x.get("human_reason") or x.get("reason")
            return {"etiqueta": etiqueta, "fase": FASES.get(fase, "Desconocida"), "fase_codigo": fase,
                    "requiere_persona": x.get("requires_human") is True,
                    "operacion_en_curso": x.get("operation") == "in_progress",
                    "motivo": motivo if isinstance(motivo, str) and _CODIGO_S.match(motivo) else None,
                    "red": red if isinstance(red, str) and _CODIGO_S.match(red) else None,
                    "latido_hace_s": _num(hb.get("last_age_s"), 86_400),
                    "cerrada_hace_s": _num(x.get("closed_age_s"), 86_400 * 30) if cerrada else None}
        activas = [f for f in (fila(x) for x in (v.get("sessions") or [])[:MAX_SESIONES]) if f]
        cerradas = [f for f in (fila(x, True) for x in (v.get("closed") or [])[:MAX_SESIONES]) if f]
        return {"activas": activas, "cerradas": cerradas,
                "requieren_persona": sum(1 for f in activas if f["requiere_persona"])}

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
                # SIMULADO = el simulador de Network Engine (laboratorio); MEDIDO = el motor mide las interfaces reales.
                # Ni siquiera MEDIDO es una prueba física de conmutación: eso lo dice la validación integral.
                "origen_datos": self._origen if motor in ("disponible", "desactualizado") else None,
                "sesiones": self._sesiones if motor == "disponible" else None,
                "ultima_validacion": ultima_validacion(),
                "nota": "Estado según Network Engine (solo lectura). No es una prueba física de los enlaces.",
            }


_CACHE_VALIDACION: dict = {}


def ultima_validacion(entorno=None) -> Optional[dict]:
    """Resumen de la última validación integral del laboratorio (si MONITOREO_RESULTADOS_VALIDACION apunta a su
    carpeta `informes`). Solo cifras y estados de listas cerradas: ningún detalle libre del informe sale de aquí."""
    env = os.environ if entorno is None else entorno
    raiz = (env.get(RESULTADOS_ENV) or "").strip()
    if not raiz or not os.path.isdir(raiz):
        return None
    try:
        cand = sorted(d for d in os.listdir(raiz) if d.startswith("integral-")
                      and os.path.isfile(os.path.join(raiz, d, "validacion_integral.json")))
        if not cand:
            return None
        ruta = os.path.join(raiz, cand[-1], "validacion_integral.json")
        if os.path.getsize(ruta) > 2_000_000:
            return None
        clave = (ruta, os.path.getmtime(ruta))
        if _CACHE_VALIDACION.get("clave") == clave:
            return _CACHE_VALIDACION["valor"]
        with open(ruta, encoding="utf-8") as fh:
            d = json.load(fh)
        pasos = [p for p in d.get("pasos") or [] if isinstance(p, dict)]
        cuenta = {e: sum(1 for p in pasos if p.get("estado") == e) for e in _ESTADOS_PRUEBA}
        por_evidencia = {ev: {e: sum(1 for p in pasos if p.get("estado") == e and p.get("evidencia") == ev)
                              for e in ("PASA", "FALLA")} for ev in _EVIDENCIAS}
        matriz = {}
        for k, val in (d.get("matriz") or {}).items():
            if re.fullmatch(r"F(?:[1-9]|1[0-2])", str(k)) and isinstance(val, dict) and val.get("estado") in _MATRIZ:
                matriz[k] = val["estado"]
        perfil = d.get("perfil") if d.get("perfil") in ("rapido", "completo", "prolongado", "nocturno") else None
        valor = {"cuando": _iso(os.path.getmtime(ruta)), "perfil": perfil, "pasos": cuenta,
                 "por_evidencia": por_evidencia, "matriz": matriz}
        _CACHE_VALIDACION.update(clave=clave, valor=valor)
        return valor
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def estado_no_configurado(razon: str = "no_configurado") -> dict:
    return {"fuente": "network_engine", "network_engine": razon, "internet": None, "razon": razon, "degradada": False,
            "conexiones": None, "disponibles": None, "interfaces": [], "eventos": [], "actualizado": None, "antiguedad_s": None,
            "ultima_respuesta": None, "reinicios": 0, "origen_datos": None, "sesiones": None,
            "ultima_validacion": ultima_validacion(),
            "nota": "Monitor no está configurado para leer Network Engine (MONITOREO_NETWORK_TOKEN_FILE). Monitor funciona igual."}


def desde_entorno(entorno=None) -> tuple[Optional[ServicioRed], str]:
    """(servicio sin arrancar, "") si hay configuración; (None, razón) si no (sin hilo ni peticiones)."""
    from .lector import desde_entorno as lector_desde_entorno
    prueba = lector_desde_entorno(entorno)
    if not prueba.activo:                          # sin token, sin paquete, desactivado…: no hay servicio
        return None, prueba.razon_inactivo
    return ServicioRed(lambda **kw: lector_desde_entorno(entorno, **kw)), ""
