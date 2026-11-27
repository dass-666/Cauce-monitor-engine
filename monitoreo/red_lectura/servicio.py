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
# E8 (M1): antes se mostraban como máximo 16 filas y las caídas del final quedaban ocultas (y el total era 16). Ahora el
# total y las cuentas se calculan sobre TODAS las interfaces; si hay más de MAX_FILAS, se muestran primero las alertas.
MAX_FILAS = 64
_PRIORIDAD = {"CAÍDA": 0, "SIN VERIFICAR": 1, "DEGRADADA": 2, "DISPONIBLE": 3}
# E8 (M2): nombres estables. Un id del motor conserva su nombre mientras Monitor esté en marcha (aunque desaparezca y
# vuelva, o lleguen otras interfaces delante en el orden). Con «inventory.reidentified» el nombre pasa al id nuevo.
MAX_NOMBRES = 512
_ETIQUETA = re.compile(r"^[A-Za-z0-9 _.-]{1,40}$")
_CODIGO_S = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_PENDIENTE_ID = re.compile(r"^pm_[0-9a-f]{12}$")   # id opaco de la marca en Network Engine (no es una credencial)
MAX_REVISIONES = 64
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
        self._cuenta_tipo: dict[str, int] = {}         # último número dado por tipo (no se reutiliza)
        self._resumen: dict = _resumen([])
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
    def _nombre(self, cid: Any, tipo: str, sin_id: dict[str, int]) -> str:
        """Llamar con el lock tomado."""
        if not isinstance(cid, str) or not cid:
            sin_id[tipo] = sin_id.get(tipo, 0) + 1            # sin id no hay identidad: nombre solo de esta lectura
            return f"{tipo} (sin identificar) {sin_id[tipo]}"
        if cid not in self._nombres:
            if len(self._nombres) >= MAX_NOMBRES:              # memoria acotada: se olvida el más antiguo
                self._nombres.pop(next(iter(self._nombres)))
            self._cuenta_tipo[tipo] = self._cuenta_tipo.get(tipo, 0) + 1
            self._nombres[cid] = f"{tipo} {self._cuenta_tipo[tipo]}"
        return self._nombres[cid]

    def _detalle(self, v: dict) -> None:
        conns = v.get("connections") if isinstance(v.get("connections"), list) else []
        filas, sin_id = [], {}
        validas = sorted((c for c in conns if isinstance(c, dict)), key=lambda c: str(c.get("id")))
        with self._lock:
            nombres = [self._nombre(c.get("id"), TIPOS.get(str(c.get("kind")), "Otra"), sin_id) for c in validas]
        for c, nombre in zip(validas, nombres):
            tipo = TIPOS.get(str(c.get("kind")), "Otra")
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
            self._interfaces, self._origen = filas, origen
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
            # E8 (M2): el mismo adaptador con otro id (p. ej. otro puerto USB) conserva su nombre
            prev, nuevo = e.get("previous_id"), e.get("connection_id")
            if isinstance(prev, str) and isinstance(nuevo, str) and prev in self._nombres:
                nombre = self._nombres[prev]
                provisional = self._nombres.get(nuevo)
                self._nombres[nuevo] = nombre
                for f in self._interfaces:
                    if f["nombre"] == provisional:
                        f["nombre"] = nombre
            self._evento("Interfaz reconectada con otro identificador", nombre, e.get("ts"))
        elif kind == "system.resume_detected":
            self._evento("El equipo se reanudó (suspensión): se vuelve a medir", None, e.get("ts"))
        elif kind == "session.requires_human":
            self._evento("Sesión de navegador: requiere intervención", None, e.get("ts"), "perdida")
        elif kind == "session.recovered":
            self._evento("Sesión de navegador recuperada", None, e.get("ts"), "recuperada")
        elif kind == "session.human_resolved":
            self._evento("Sesión de navegador revisada por una persona", None, e.get("ts"))
        # E5: persistencia del estado de sesiones en Network Engine
        elif kind == "session.restored":
            texto = "Sesión de navegador restaurada tras reinicio" + (": requiere intervención" if e.get("requires_human") is True
                                                                       else " (incierta hasta su próximo latido)")
            self._evento(texto, None, e.get("ts"), "perdida" if e.get("requires_human") is True else "info")
        elif kind == "session.expired":
            self._evento("Sesión de navegador caducada sin latidos" + (" (quedó pendiente de revisión)"
                                                                       if e.get("requires_human") is True else ""),
                         None, e.get("ts"), "perdida" if e.get("requires_human") is True else "info")
        elif kind == "session.replaced":
            self._evento("Sesión de navegador registrada de nuevo tras reinicio", None, e.get("ts"))
        elif kind == "session.state_corrupt":
            self._evento("Estado de sesiones dañado: se empezó vacío; revisa a mano las operaciones en curso", None,
                         e.get("ts"), "perdida")
        elif kind == "session.state_recovered_from_backup":
            self._evento("Estado de sesiones recuperado de la copia anterior", None, e.get("ts"), "degradada")
        elif kind == "session.pending_resolved":
            self._evento("Revisión pendiente resuelta por una persona administradora", None, e.get("ts"), "recuperada")
        elif kind == "session.state_write_failed":
            self._evento("Network Engine no pudo guardar el estado de sesiones", None, e.get("ts"), "degradada")

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
        # E8 (E): marcas «revisar a mano» cuya sesión ya no existe. Antes no se mostraban y el contador decía 0.
        crudas = [p for p in (v.get("pending_human_review") or []) if isinstance(p, dict)]
        revisiones = []
        for p in crudas[:MAX_REVISIONES]:
            pid = p.get("id") if isinstance(p.get("id"), str) and _PENDIENTE_ID.match(p["id"]) else None
            etiqueta = p.get("label") if isinstance(p.get("label"), str) and _ETIQUETA.match(p["label"]) else None
            general = p.get("kind") == "general" or (p.get("label") is None and p.get("kind") != "session")
            conf = p.get("confirm_with")
            revisiones.append({"id": pid, "tipo": "general" if general else "sesion", "etiqueta": etiqueta,
                               "motivo": p["reason"] if isinstance(p.get("reason"), str) and _CODIGO_S.match(p["reason"]) else None,
                               "hace_s": _num(p.get("age_s"), 86_400 * 365),
                               "confirmar_con": conf if isinstance(conf, str) and _ETIQUETA.match(conf) else None})
        activas_persona = sum(1 for f in activas if f["requiere_persona"])
        return {"activas": activas, "cerradas": cerradas, "revisiones": revisiones,
                "requieren_persona": activas_persona,
                # el contador cuenta TODAS: sesiones activas que piden persona + marcas sin sesión (aunque no quepan)
                "revisiones_pendientes": activas_persona + len(crudas)}

    def quitar_revision(self, pid: str) -> None:
        """Tras resolverla, deja de mostrarse sin esperar a la próxima lectura (la siguiente lo confirma)."""
        with self._lock:
            ses = self._sesiones
            if ses and any(r.get("id") == pid for r in ses.get("revisiones", [])):
                ses["revisiones"] = [r for r in ses["revisiones"] if r.get("id") != pid]
                ses["revisiones_pendientes"] = max(0, ses["revisiones_pendientes"] - 1)

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
            interfaces = [dict(i) for i in self._interfaces] if motor == "disponible" else []
            internet = l.internet.value if l else None
            if motor == "disponible" and antiguedad is not None and antiguedad > self.umbral_desactualizado_s():
                # La copia de Monitor es vieja: se conserva qué interfaces había, pero ninguna se da por sana.
                motor, internet = "desactualizado", "DESCONOCIDO"
                interfaces = [{**i, "salud": SIN_VERIFICAR, "disponible": False, "latencia_ms": None} for i in interfaces]
            resumen = _resumen(interfaces)
            interfaces = _mostrar(interfaces)
            resumen["mostradas"] = len(interfaces)
            resumen["ocultas"] = resumen["total"] - len(interfaces)
            return {
                "fuente": "network_engine",
                "network_engine": motor,
                "internet": internet,
                "razon": l.razon if l else None,
                "degradada": bool(l and l.degradada),
                "conexiones": l.conexiones if l else None,
                "disponibles": l.disponibles if l else None,
                "interfaces": interfaces,
                # E8 (M1): cuentas sobre TODAS las interfaces, aunque no quepan en la tabla
                "resumen_interfaces": resumen,
                "antiguedad_s": round(antiguedad, 1) if antiguedad is not None else None,
                "eventos": list(self._eventos),
                "actualizado": _iso(self._actualizado),
                "ultima_respuesta": _iso(self._ultima_ok),
                "reinicios": self.lector.reinicios,
                # SIMULADO = el simulador de Network Engine (laboratorio); MEDIDO = el motor mide las interfaces reales.
                # Ni siquiera MEDIDO es una prueba física de conmutación: eso lo dice la validación integral.
                "origen_datos": self._origen if motor in ("disponible", "desactualizado") else None,
                "sesiones": self._sesiones if motor == "disponible" else None,
                # E8: ¿hay credencial admin SEPARADA para resolver desde Monitor? (si no, se muestra la orden del motor)
                "revisiones_resolubles": bool((os.environ.get("MONITOREO_NETWORK_REVISIONES_TOKEN_FILE") or "").strip()),
                "ultima_validacion": ultima_validacion(),
                "nota": "Estado según Network Engine (solo lectura). No es una prueba física de los enlaces.",
            }


def _alerta(i: dict) -> bool:
    return i.get("veredicto") in ("CAÍDA", "SIN VERIFICAR", "DEGRADADA") or i.get("salud") in ("CAÍDA", RECUPERANDO,
                                                                                                SIN_VERIFICAR)


def _resumen(interfaces: list[dict]) -> dict:
    por = {k: 0 for k in _PRIORIDAD}
    for i in interfaces:
        if i.get("veredicto") in por:
            por[i["veredicto"]] += 1
    alertas = sum(1 for i in interfaces if _alerta(i))
    return {"total": len(interfaces), "por_veredicto": por, "caidas": por["CAÍDA"], "alertas": alertas,
            "sanas": sum(1 for i in interfaces if i.get("salud") == "SANA"),
            "degradadas": sum(1 for i in interfaces if i.get("salud") == "DEGRADADA"),
            "mostradas": len(interfaces), "ocultas": 0}


def _mostrar(interfaces: list[dict]) -> list[dict]:
    """Todas si caben (en su orden estable). Si no, primero las alertas: una caída nunca queda fuera por una sana."""
    if len(interfaces) <= MAX_FILAS:
        return interfaces
    orden = sorted(range(len(interfaces)), key=lambda k: (_PRIORIDAD.get(interfaces[k].get("veredicto"), 9), k))
    return [interfaces[k] for k in orden[:MAX_FILAS]]


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
            "conexiones": None, "disponibles": None, "interfaces": [], "resumen_interfaces": _resumen([]), "eventos": [], "actualizado": None, "antiguedad_s": None,
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
