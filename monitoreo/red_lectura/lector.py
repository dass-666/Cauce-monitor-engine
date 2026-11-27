"""Adaptador de lectura de conectividad (ver monitoreo/red_lectura/__init__.py)."""
from __future__ import annotations

import enum
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

log = logging.getLogger("monitoreo.red_lectura")

INTERRUPTORES_ENV = ("MONITOREO_NETWORK_DESACTIVADO", "CAUCE_STATUS_DISABLED")
ARCHIVO_TOKEN_ENV = "MONITOREO_NETWORK_TOKEN_FILE"
PUERTO_ENV = "MONITOREO_NETWORK_PUERTO"
# Puerto por defecto de Network Engine desde su 0.2.0 (8765 lo usa la interfaz de Forms). Ver docs/MATRIZ_COMPATIBILIDAD.md.
PUERTO_DEFECTO = 8780
# Carpeta de Network Engine (clon o copia). Network Engine solo usa la biblioteca estándar: basta con añadirla al
# path de importación, sin instalar nada en el entorno de Monitor. Opcional: si `cauce` ya está instalado, se usa ese.
DIRECTORIO_ENV = "MONITOREO_NETWORK_DIR"
ARCHIVO_APAGADO_ENV = "MONITOREO_NETWORK_ARCHIVO_APAGADO"
INTERVALO_MIN_S = 5.0
INTERVALO_S = 10.0
ESPERA_MAX_S = 60.0
ESQUEMA = "cauce.connectivity/1"
# (state del motor) -> (usable que debe acompañarlo). Cualquier otra combinación es una respuesta incoherente.
_COHERENCIA = {"usable": True, "degraded": True, "unavailable": False, "unknown": None}
_DETIENEN = (400, 401, 403)                       # no se arreglan reintentando
_CODIGO = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_EPOCA = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Métodos que un cliente de solo lectura NO debe tener: si aparecen, es un cliente de administración mal configurado.
_METODOS_ADMIN = ("request_route", "release_route", "connect_via", "select", "start_task", "stop_task", "stop_all",
                  "create_rotation", "set_rotation", "shutdown", "connections", "create_token", "revoke_token", "tokens")


class Internet(str, enum.Enum):
    DISPONIBLE = "DISPONIBLE"
    NO_DISPONIBLE = "NO_DISPONIBLE"
    DESCONOCIDO = "DESCONOCIDO"


class ClienteNoPermitido(ValueError):
    """El cliente entregado tiene capacidades de administración: no se acepta (solo credenciales `read`)."""


@dataclass(frozen=True)
class Lectura:
    internet: Internet
    razon: str                      # código corto y saneado; nunca texto de excepciones
    degradada: bool = False
    conexiones: Optional[int] = None
    disponibles: Optional[int] = None
    hubo_peticion: bool = False     # False = respuesta sin tocar la red (interruptor, detenido, espera, inactivo)


def _codigo(valor: Any, defecto: str) -> str:
    return valor if isinstance(valor, str) and _CODIGO.match(valor) else defecto


def _entero(valor: Any) -> Optional[int]:
    return valor if isinstance(valor, int) and not isinstance(valor, bool) and 0 <= valor < 10_000 else None


class LectorConectividad:
    def __init__(self, cliente: Any = None, *, razon_inactivo: str = "no_configurado", intervalo_s: float = INTERVALO_S,
                 archivo_apagado: Optional[str] = None, entorno: Optional[Mapping[str, str]] = None,
                 reloj: Callable[[], float] = time.monotonic, al_leer: Optional[Callable[[dict], None]] = None):
        self._leer = None
        self._eventos = None
        self._sesiones = None
        # `al_leer` recibe la vista ya validada (solo si fue coherente). Lo usa monitoreo/red_lectura/servicio.py para
        # sacar el detalle por interfaz con su PROPIO saneado; esta clase sigue sin guardar ni registrar ese detalle.
        self._al_leer = al_leer
        if cliente is not None:
            if not callable(getattr(cliente, "connectivity", None)):
                raise ClienteNoPermitido("el cliente no ofrece connectivity()")
            if any(hasattr(cliente, m) for m in _METODOS_ADMIN):
                raise ClienteNoPermitido("cliente con métodos de administración: use solo una credencial `read`")
            self._leer = cliente.connectivity                # solo estas funciones: el resto del cliente no se conserva
            if callable(getattr(cliente, "events", None)):
                self._eventos = cliente.events
            if callable(getattr(cliente, "sessions", None)):     # Network Engine con sesiones de navegador (PR #10)
                self._sesiones = cliente.sessions
        self._razon_inactivo = razon_inactivo
        self.intervalo_s = max(INTERVALO_MIN_S, float(intervalo_s))
        self._archivo_apagado = archivo_apagado
        self._entorno = os.environ if entorno is None else entorno
        self._reloj = reloj
        self._proximo = 0.0
        self.fallos = 0
        self.detenido: Optional[str] = None                  # código de la API que detuvo el lector
        self.reinicios = 0
        self._epoca: Optional[str] = None
        self._ultima_seq = 0
        self._ultima: Optional[Lectura] = None

    # ------------------------------------------------------------------ control
    @property
    def activo(self) -> bool:
        """True si hay un cliente configurado (puede seguir desactivado por el interruptor)."""
        return self._leer is not None

    @property
    def razon_inactivo(self) -> str:
        return self._razon_inactivo

    def desactivado(self) -> bool:
        return any(self._entorno.get(v) == "1" for v in INTERRUPTORES_ENV) or bool(
            self._archivo_apagado and os.path.exists(self._archivo_apagado))

    def reanudar(self) -> None:
        """Solo tras corregir la credencial o el permiso: NUNCA desde un bucle de reintentos."""
        self.detenido = None
        self.fallos = 0
        self._proximo = 0.0

    def siguiente_espera_s(self) -> float:
        return self.intervalo_s if not self.fallos else min(ESPERA_MAX_S, self.intervalo_s * 2 ** self.fallos)

    def resumen(self) -> dict:
        """Lo único que puede ir a logs o pantallas: estados y contadores."""
        u = self._ultima
        return {"internet": u.internet.value if u else None, "razon": u.razon if u else None, "detenido": self.detenido,
                "reinicios": self.reinicios, "fallos_seguidos": self.fallos}

    # ------------------------------------------------------------------ lectura
    def _fijar(self, lectura: Lectura) -> Lectura:
        previa = self._ultima
        if previa is None or (previa.internet, previa.razon, previa.degradada) != (lectura.internet, lectura.razon, lectura.degradada):
            log.info("red: internet=%s razon=%s", lectura.internet.value, lectura.razon)     # nada más
        self._ultima = lectura
        return lectura

    def _desconocido(self, razon: str, *, peticion: bool) -> Lectura:
        return self._fijar(Lectura(Internet.DESCONOCIDO, razon, hubo_peticion=peticion))

    def leer(self) -> Lectura:
        """Devuelve la lectura actual. Nunca lanza excepciones y nunca bloquea el arranque ni el uso de Monitor."""
        if self.desactivado():
            return self._desconocido("desactivado", peticion=False)          # cero peticiones
        if self._leer is None:
            return self._desconocido(self._razon_inactivo, peticion=False)
        if self.detenido:
            return self._desconocido("detenido_" + self.detenido, peticion=False)   # cero peticiones hasta reanudar()
        ahora = self._reloj()
        if ahora < self._proximo and self._ultima is not None:
            return Lectura(self._ultima.internet, self._ultima.razon, self._ultima.degradada, self._ultima.conexiones,
                           self._ultima.disponibles, hubo_peticion=False)       # respeta el intervalo: no consulta de más
        try:
            v = self._leer()
        except Exception as e:                                                  # noqa: BLE001  (Monitor no debe caer por esto)
            return self._error(e, ahora)
        lectura = self._interpretar(v)
        if lectura.razon != "respuesta_invalida" and self._al_leer is not None:
            try:
                self._al_leer(v)
            except Exception:                                                   # noqa: BLE001  el detalle nunca tumba la lectura
                log.warning("red: no se pudo interpretar el detalle por interfaz")
        self.fallos = self.fallos + 1 if lectura.razon == "respuesta_invalida" else 0   # «unknown» real del motor no es fallo
        self._proximo = ahora + self.siguiente_espera_s()
        return self._fijar(lectura)

    def leer_eventos(self, desde: int) -> Optional[dict]:
        """Eventos reducidos del motor (`/status/events`). None si no se debe o no se puede consultar. Nunca lanza;
        un 401/403/400 detiene el lector igual que en `leer()`."""
        if self._eventos is None or self.detenido or self.desactivado():
            return None
        try:
            v = self._eventos(since=max(0, int(desde)), limit=200)
        except Exception as e:                                                  # noqa: BLE001
            estado = getattr(e, "status", None)
            if isinstance(estado, int) and not isinstance(estado, bool) and estado in _DETIENEN:
                self._error(e, self._reloj())                                   # credencial rota: se detiene todo
            return None                                                         # lo demás lo cuenta leer(), no aquí
        return v if isinstance(v, dict) and v.get("schema") == "cauce.status-events/1" else None

    def leer_sesiones(self) -> Optional[dict]:
        """Sesiones de navegador (`/status/sessions`, solo lectura). None si el motor no las ofrece o no responde.
        Nunca lanza; un 401/403/400 detiene el lector igual que en `leer()`; un 404 (motor sin sesiones) solo da None."""
        if self._sesiones is None or self.detenido or self.desactivado():
            return None
        try:
            v = self._sesiones()
        except Exception as e:                                                  # noqa: BLE001
            estado = getattr(e, "status", None)
            if isinstance(estado, int) and not isinstance(estado, bool) and estado in _DETIENEN:
                self._error(e, self._reloj())
            return None
        return v if isinstance(v, dict) and v.get("schema") == "cauce.sessions/1" else None

    def _error(self, e: Exception, ahora: float) -> Lectura:
        estado, codigo = getattr(e, "status", None), getattr(e, "code", None)
        if isinstance(estado, int) and not isinstance(estado, bool):
            if estado in _DETIENEN:
                self.detenido = _codigo(codigo, "http_%d" % estado)
                log.error("red: credencial o petición rechazada (%s); lector detenido hasta intervención del operador", self.detenido)
                return self._desconocido("detenido_" + self.detenido, peticion=True)
            self.fallos += 1                                                     # 5xx (p. ej. credentials_locked) u otro 4xx
            self._proximo = ahora + self.siguiente_espera_s()
            return self._desconocido(_codigo(codigo, "http_%d" % estado), peticion=True)
        self.fallos += 1
        self._proximo = ahora + self.siguiente_espera_s()
        log.warning("red: el motor no responde (%s)", type(e).__name__)          # el tipo, no el texto
        razon = "motor_no_responde" if isinstance(e, (OSError, ValueError)) else "error_inesperado"
        return self._desconocido(razon, peticion=True)

    def _interpretar(self, v: Any) -> Lectura:
        malo = Lectura(Internet.DESCONOCIDO, "respuesta_invalida", hubo_peticion=True)
        if not isinstance(v, dict) or v.get("schema") != ESQUEMA:
            return malo
        estado, usable = v.get("state"), v.get("usable")
        motor = v.get("engine")
        seq = v.get("last_seq")
        if estado not in _COHERENCIA or usable is not _COHERENCIA[estado]:
            return malo
        if not isinstance(motor, dict) or not isinstance(motor.get("epoch"), str) or not _EPOCA.match(motor["epoch"]):
            return malo
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
            return malo
        if self._epoca is not None and (motor["epoch"] != self._epoca or seq < self._ultima_seq):
            self.reinicios += 1                                                  # el motor se reinició: lo anterior no vale
            log.warning("red: motor reiniciado; se usa la lectura nueva")
        self._epoca, self._ultima_seq = motor["epoch"], seq
        cuentas = v.get("counts") if isinstance(v.get("counts"), dict) else {}
        c, d = _entero(cuentas.get("connections")), _entero(cuentas.get("available"))
        if usable is True:
            return Lectura(Internet.DISPONIBLE, "ok" if estado == "usable" else "degradada", estado == "degraded", c, d, True)
        if usable is False:
            return Lectura(Internet.NO_DISPONIBLE, _codigo(v.get("reason"), "sin_conexion_utilizable"), False, c, d, True)
        return Lectura(Internet.DESCONOCIDO, _codigo(v.get("reason"), "desconocido"), False, c, d, True)   # null nunca pasa a False


def _ruta_red(env: Mapping[str, str]) -> Optional[str]:
    d = (env.get(DIRECTORIO_ENV) or "").strip()
    return d if d and os.path.isfile(os.path.join(d, "cauce", "client.py")) else None


def desde_entorno(entorno: Optional[Mapping[str, str]] = None, **opciones) -> LectorConectividad:
    """Crea el lector según el entorno. Sin configuración, sin el paquete `cauce` o con el interruptor puesto: inactivo."""
    env = os.environ if entorno is None else entorno
    apagado = env.get(ARCHIVO_APAGADO_ENV) or None
    base = dict(archivo_apagado=apagado, entorno=env, **opciones)
    if any(env.get(v) == "1" for v in INTERRUPTORES_ENV):
        return LectorConectividad(razon_inactivo="desactivado", **base)
    ruta = env.get(ARCHIVO_TOKEN_ENV)
    if not ruta:
        return LectorConectividad(razon_inactivo="no_configurado", **base)
    try:
        puerto = int(env.get(PUERTO_ENV, str(PUERTO_DEFECTO)))
        if not 1024 <= puerto <= 65535:
            raise ValueError
    except ValueError:
        return LectorConectividad(razon_inactivo="puerto_invalido", **base)
    ruta_red = _ruta_red(env)
    if env.get(DIRECTORIO_ENV) and ruta_red is None:
        return LectorConectividad(razon_inactivo="directorio_invalido", **base)
    if ruta_red and ruta_red not in sys.path:
        sys.path.append(ruta_red)                       # al final: nunca tapa módulos de Monitor ni de la biblioteca estándar
    try:
        from cauce.client import CauceReadClient        # import perezoso: Monitor no exige el paquete
    except ImportError:
        return LectorConectividad(razon_inactivo="paquete_no_instalado", **base)
    try:
        cliente = CauceReadClient.from_token_file(ruta, port=puerto)
    except Exception:                                   # noqa: BLE001  (ni la ruta ni el motivo van a logs)
        return LectorConectividad(razon_inactivo="token_ilegible", **base)
    return LectorConectividad(cliente, **base)
