"""v2.1 · P2 · Supervisor del observador de calendario (proceso Node + Playwright).

Lanza `scripts/observador_calendario.js` contra la vista pública del PORTAL DE LABORATORIO
configurado (`MONITOREO_PORTAL_URL` + /observar/calendario): no admite otra dirección. Lee sus
líneas, las convierte en eventos normalizados (`observaciones.registrar`) y, ante un día disponible,
llama al motor de coincidencias.

Resistencia:
- Si el navegador o el proceso caen, se relanza con espera creciente (1, 2, 4… hasta 30 s). Al
  reabrir, el estado INICIAL se compara con lo guardado: lo que cambió mientras estaba caído genera
  su evento; lo que no cambió no se duplica.
- Sesión caducada: se reabre la página con espera creciente (no en bucle) y queda registrado.
- Desconexión o calendario congelado: se registra el estado; NO se interpreta como «sin días».
- Si la base de datos no responde, la línea se reintenta más tarde (cola en memoria acotada).
- Límites: más de `max_reinicios` relanzamientos en `ventana_reinicios_s`, o más de
  `max_reaperturas` reaperturas seguidas sin volver a ACTIVA, DETIENEN el observador con su motivo
  (no se insiste sin fin contra una página que no responde).
- Navegación y capturas de imagen van en serie (candado de página en el script): se reprodujo que
  una captura en curso durante la navegación deja bloqueadas las capturas siguientes ~30 s.

Persistencia (v2.1): la configuración vive en `observadores_config`. `Supervisor` (un hilo por
proceso del motor) la lee cada pocos segundos: arranca lo configurado, detiene lo que se desactivó y
marca DETENIDO_POR_FALLOS lo que superó los límites. Para que dos procesos del motor sobre la misma
base no abran dos navegadores para lo mismo, cada observador exige un arrendamiento en la base
(`scheduler_leases`, nombre `obs:<sucursal|trámite>`): solo el titular lo ejecuta; si muere, otro
proceso lo toma al vencer (15 s).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from sqlalchemy import select

from .. import navegador
from ..db import utcnow
from ..texto_seguro import sanitizar
from . import coincidencias, observaciones, operacion

log = logging.getLogger("monitoreo.planificador")
ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "observador_calendario.js"


class Observador:
    def __init__(self, db, sucursal: str, tramite: str, mes: str | None = None, estrategia: str = "ambas",
                 intervalo_visual_ms: int = 250, congelado_s: float = 5.0, evidencia_dir: str | None = None,
                 nombre: str | None = None, atender: bool = True, jornada=None, max_reinicios: int = 8,
                 ventana_reinicios_s: float = 600.0, max_reaperturas: int = 10):
        from ..captura import portal
        self.db, self.sucursal, self.tramite, self.mes = db, sucursal, tramite, mes
        self.estrategia, self.atender, self.jornada = estrategia, atender, jornada
        q = {"sucursal": sucursal, "tramite": tramite, **({"mes": mes} if mes else {})}
        self.url = portal.url().rstrip("/") + "/observar/calendario?" + urlencode(q)
        self.nombre = (nombre or f"obs-{sucursal}-{tramite}")[:64]
        self.ctx = {"observador": self.nombre, "sucursal": sucursal, "tramite": tramite}
        self.cfg = {"url": self.url, "estrategia": estrategia, "intervalo_visual_ms": intervalo_visual_ms,
                    "congelado_s": congelado_s, "evidencia_dir": evidencia_dir}
        self.proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._hilo: threading.Thread | None = None
        self._lock = threading.Lock()
        self.pendientes: deque = deque(maxlen=5000)
        self.stats = {"lineas": 0, "eventos": 0, "reinicios_navegador": 0, "reaperturas_sesion": 0,
                      "errores_bd": 0, "ultimo_latido": None, "vista": {}, "coincidencias": 0, "arranques": 0}
        self.eventos: list[dict] = []          # eventos nuevos (para el recorrido y las pruebas)
        self.lineas: list[dict] = []           # todo lo que emitió el observador (CELDA crudas incluidas)
        self._espera_reabrir = 2.0
        self._reabrir_pedido: float | None = None     # monotónico: reapertura pedida y aún sin confirmar
        self._latido_mono = time.monotonic()
        self.stats["reinicios_por_vigilancia"] = 0
        self._reabriendo = threading.Event()
        self._cfg_file: str | None = None
        self.max_reinicios, self.ventana_reinicios_s, self.max_reaperturas = max_reinicios, ventana_reinicios_s, max_reaperturas
        self._reinicios_t: deque = deque()
        self._reaperturas_seguidas = 0
        self.fallo: str | None = None          # motivo por el que se detuvo solo (límite superado)
        # Diagnóstico (cierre v2.1): los ERROR del script y la salida de error de Node, saneados.
        self.stats["errores_navegador"] = 0
        self.ultimo_error: dict | None = None
        self.errores: deque = deque(maxlen=20)
        self.stderr: deque = deque(maxlen=20)

    # ------------------------------------------------------------------ ciclo de vida
    def iniciar(self) -> None:
        if shutil.which("node") is None:
            raise RuntimeError("Node.js no está disponible: el observador necesita Node + Playwright")
        fd, self._cfg_file = tempfile.mkstemp(prefix="observador-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f)
        self._hilo = threading.Thread(target=self._bucle, name=f"observador-{self.nombre}", daemon=True)
        self._hilo.start()
        threading.Thread(target=self._vigilar, name=f"vigia-{self.nombre}", daemon=True).start()

    def _vigilar(self, reapertura_s: float = 20.0, latido_s: float = 20.0) -> None:
        """Vigilante: si una reapertura no se confirma o el observador deja de latir, se reinicia el
        navegador (el relanzamiento vuelve a abrir la página y compara con lo guardado)."""
        while not self._stop.wait(2.0):
            ahora = time.monotonic()
            motivo = None
            if self._reabrir_pedido is not None and ahora - self._reabrir_pedido > reapertura_s:
                motivo = f"la página no se reabrió en {reapertura_s:.0f} s"
            elif self.proc and self.proc.poll() is None and ahora - self._latido_mono > latido_s:
                motivo = f"sin latido en {latido_s:.0f} s"
            if motivo:
                self._reabrir_pedido, self._latido_mono = None, ahora
                self.stats["reinicios_por_vigilancia"] += 1
                log.warning("observador %s: %s; se reinicia el navegador", self.nombre, motivo)
                self.matar_navegador()

    def detener(self, espera: float = 15) -> None:
        self._stop.set()
        p = self.proc
        if p and p.poll() is None:
            try:
                p.stdin.write('{"cmd":"salir"}\n')
                p.stdin.flush()
                p.stdin.close()
                p.wait(espera)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                p.kill()
        if self._hilo:
            self._hilo.join(espera)
        if self._cfg_file:
            try:
                os.unlink(self._cfg_file)
            except OSError:
                pass

    def matar_navegador(self) -> None:
        """Para las pruebas de resistencia: corte brusco del proceso observador (y su Chromium)."""
        p = self.proc
        if p and p.poll() is None:
            p.kill()

    def orden(self, cmd: str) -> None:
        p = self.proc
        if p and p.poll() is None:
            with self._lock:
                p.stdin.write(json.dumps({"cmd": cmd}) + "\n")
                p.stdin.flush()

    def _bucle(self) -> None:
        espera = 1.0
        while not self._stop.is_set():
            self.stats["arranques"] += 1
            self._latido_mono, self._reabrir_pedido = time.monotonic(), None
            self.proc = subprocess.Popen(["node", str(SCRIPT), self._cfg_file], cwd=ROOT, stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                         errors="replace", bufsize=1, env=navegador.entorno())
            hilo_err = threading.Thread(target=self._leer_stderr, args=(self.proc,), name=f"stderr-{self.nombre}",
                                        daemon=True)
            hilo_err.start()
            t0 = time.monotonic()
            for linea in self.proc.stdout:
                if not linea.startswith("OBS "):
                    continue
                try:
                    obs = json.loads(linea[4:])
                except ValueError:
                    continue
                self._procesar(obs)
            self.proc.wait()
            hilo_err.join(2)
            if self._stop.is_set():
                break
            if self.proc.returncode and not self._error_en(self.stats["arranques"]) and self.stderr:
                # Node terminó con error sin emitir una línea ERROR (p. ej. no encontró el módulo
                # «playwright»): se registra la última salida de error, saneada.
                self._anotar_error("node", " ".join(list(self.stderr)[-3:]))
            self.stats["reinicios_navegador"] += 1
            ahora = time.monotonic()
            self._reinicios_t.append(ahora)
            while self._reinicios_t and ahora - self._reinicios_t[0] > self.ventana_reinicios_s:
                self._reinicios_t.popleft()
            if len(self._reinicios_t) > self.max_reinicios:
                base = (f"el navegador se relanzó {len(self._reinicios_t)} veces en "
                        f"{self.ventana_reinicios_s / 60:.0f} min; se detiene para no insistir")
                if self.ultimo_error:          # el motivo se guarda en 200 caracteres: el error se recorta
                    base += "; último error: " + sanitizar(self.ultimo_error["mensaje"], max(20, 200 - len(base) - 16))
                self._fallar(base)
                break
            log.warning("observador %s: el proceso terminó (código %s); se relanza en %.0f s", self.nombre,
                        self.proc.returncode, espera)
            if time.monotonic() - t0 > 60:
                espera = 1.0
            if self._stop.wait(espera):
                break
            espera = min(30.0, espera * 2)

    # ------------------------------------------------------------------ diagnóstico
    def _leer_stderr(self, proc: subprocess.Popen) -> None:
        """Lee la salida de error de Node (hay que vaciarla para que el proceso no se bloquee) y
        conserva sus últimas líneas, saneadas. No se registra cada línea: solo si Node termina con
        error sin haber emitido una línea ERROR propia."""
        try:
            for linea in proc.stderr:
                s = sanitizar(linea, 240)
                if s:
                    self.stderr.append(s)
        except (OSError, ValueError):
            pass

    def _anotar_error(self, origen: str, mensaje) -> None:
        m = sanitizar(mensaje, 300) or "(sin mensaje)"
        self.stats["errores_navegador"] += 1
        self.ultimo_error = {"origen": origen, "mensaje": m, "arranque": self.stats["arranques"],
                             "en": utcnow().isoformat() + "Z"}
        self.errores.append(self.ultimo_error)
        log.error("observador %s: ERROR del navegador (%s): %s", self.nombre, origen, m)

    def _error_en(self, arranque: int) -> bool:
        return bool(self.ultimo_error and self.ultimo_error["arranque"] == arranque)

    # ------------------------------------------------------------------ procesamiento
    def _procesar(self, obs: dict) -> None:
        self.stats["lineas"] += 1
        tipo = obs.get("tipo")
        if tipo == "ERROR":
            self._anotar_error(obs.get("estrategia") or "observador", obs.get("mensaje"))
        elif tipo == "VISTA" and obs.get("estado") == "DESCONECTADO" and obs.get("detalle"):
            log.warning("observador %s: página DESCONECTADA (%s): %s", self.nombre, obs.get("estrategia"),
                        sanitizar(obs.get("detalle"), 200))
        if tipo == "LATIDO":
            self.stats["ultimo_latido"] = obs.get("ts_ms")
            self._latido_mono = time.monotonic()
            self._reintentar()
            return
        self.lineas.append(obs)
        if tipo in ("INICIAL", "CELDA", "VISTA"):
            self.pendientes.append(obs)
            self._reintentar()
        if tipo == "VISTA":
            self.stats["vista"][obs.get("estrategia") or "?"] = obs.get("estado")
            if obs.get("estado") == "SESION_CADUCADA" and not self._reabriendo.is_set():
                self._reabriendo.set()            # una sola reapertura aunque lo digan las dos estrategias
                threading.Thread(target=self._reabrir, daemon=True).start()
            elif obs.get("estado") == "ACTIVA":
                self._espera_reabrir = 2.0
                self._reabrir_pedido = None
                self._reaperturas_seguidas = 0
        if tipo in ("REABIERTA", "LISTO"):
            self._latido_mono = time.monotonic()

    def _reabrir(self) -> None:
        espera = self._espera_reabrir
        self._espera_reabrir = min(60.0, espera * 2)
        if self._stop.wait(espera):
            return
        self.stats["reaperturas_sesion"] += 1
        self._reaperturas_seguidas += 1
        if self._reaperturas_seguidas > self.max_reaperturas:
            self._reabriendo.clear()
            self._fallar(f"la sesión caducó {self._reaperturas_seguidas - 1} veces seguidas sin volver a estar activa")
            return
        log.warning("observador %s: la sesión de consulta caducó; se reabre la página (espera %.0f s)", self.nombre, espera)
        try:
            self._reabrir_pedido = time.monotonic()
            self.orden("reabrir")
        finally:
            self._reabriendo.clear()

    def _fallar(self, motivo: str) -> None:
        self.fallo = motivo[:200]
        log.error("observador %s DETENIDO: %s", self.nombre, motivo)
        self._stop.set()
        self.matar_navegador()

    def _reintentar(self) -> None:
        while self.pendientes:
            obs = self.pendientes[0]
            try:
                nuevos = observaciones.registrar(self.db, obs, self.ctx, utcnow())
            except Exception as e:  # noqa: BLE001 - p. ej. la base no responde: se reintenta en el siguiente latido
                self.stats["errores_bd"] += 1
                log.warning("observador %s: no se pudo registrar (%s); se reintenta", self.nombre, e.__class__.__name__)
                return
            self.pendientes.popleft()
            self.eventos.extend(nuevos)
            self.stats["eventos"] += len(nuevos)
            bajan = {e["sucursal"] + "|" + e["tramite"] + "|" + e["fecha"] for e in nuevos
                     if e["tipo"] in (observaciones.DIA_NO_DISPONIBLE, observaciones.UNIDADES_REDUCIDAS)}
            if self.atender and bajan:
                try:
                    for c in sorted(bajan):
                        coincidencias.ajustar(self.db, c, utcnow())
                except Exception as e:  # noqa: BLE001
                    log.warning("ajustar unidades falló: %s", e)
            if self.atender and any(e["tipo"] == observaciones.DIA_DISPONIBLE for e in nuevos):
                try:
                    r = coincidencias.atender_pendientes(self.db, utcnow(), self.jornada)
                    self.stats["coincidencias"] += sum(1 for x in r if x.get("asignado"))
                except Exception as e:  # noqa: BLE001 - se reintenta con el siguiente evento o por mantenimiento
                    log.warning("coincidencias fallaron: %s", e)

    def estado(self) -> dict:
        return {"nombre": self.nombre, "url": self.url, "estrategia": self.estrategia,
                "vivo": bool(self.proc and self.proc.poll() is None), "pendientes": len(self.pendientes),
                "fallo": self.fallo, "ultimo_error": self.ultimo_error, "errores": list(self.errores),
                "stderr_node": list(self.stderr)[-5:], **self.stats}


def mantenimiento(db, now) -> dict:
    """Lo llama el planificador del motor: propiedad de los expedientes del plan, unidades que bajaron,
    eventos sin atender, unidades que quedaron libres otra vez y estado de las operaciones."""
    from . import propiedad
    return {"propiedad": propiedad.reconciliar(db, now),
            "devueltos": coincidencias.liberar_desaparecidos(db, now),
            "coincidencias": [x for x in coincidencias.atender_pendientes(db, now) if x.get("asignado")],
            "reevaluadas": [x for x in coincidencias.reevaluar(db, now) if x.get("asignado")],
            "sincronizacion": operacion.sincronizar(db, now)}


# ------------------------------------------------------------------ persistencia y supervisión
ACTIVO, DETENIDO, DETENIDO_POR_FALLOS = "ACTIVO", "DETENIDO", "DETENIDO_POR_FALLOS"
TTL_ARRIENDO_S = 15.0


def _clave(sucursal: str, tramite: str) -> str:
    return f"{sucursal}|{tramite}"


def _naive(now: datetime) -> datetime:
    return now.replace(tzinfo=None) if now.tzinfo else now


def vista_config(c) -> dict:
    return {"clave": c.clave, "sucursal": c.sucursal, "tramite": c.tramite, "mes": c.mes, "estrategia": c.estrategia,
            "intervalo_visual_ms": c.intervalo_visual_ms, "activo": c.activo, "estado": c.estado, "motivo": c.motivo,
            "titular": c.titular, "reinicios": c.reinicios, "creado_por": c.creado_por,
            "actualizado": c.updated_at.isoformat() + "Z"}


def configurar(db, sucursal: str, tramite: str, mes: str | None, estrategia: str, intervalo_visual_ms: int,
               usuario: str, now: datetime) -> dict:
    """Guarda (o reactiva) la configuración. El Supervisor lo arranca; persiste tras reiniciar."""
    from ..models import ObservadorConfig
    clave = _clave(sucursal, tramite)
    with db.session() as s:
        c = s.get(ObservadorConfig, clave)
        if c is not None and c.activo:
            return {**vista_config(c), "ya_existia": True}
        if c is None:
            c = ObservadorConfig(clave=clave, sucursal=sucursal, tramite=tramite, created_at=now, creado_por=usuario[:64])
            s.add(c)
        c.mes, c.estrategia, c.intervalo_visual_ms = mes, estrategia, int(intervalo_visual_ms)
        c.activo, c.estado, c.motivo, c.reinicios, c.updated_at = True, ACTIVO, "", 0, now
        s.flush()
        return {**vista_config(c), "ya_existia": False}


def desactivar(db, usuario: str, now: datetime, clave: str | None = None) -> int:
    from ..models import ObservadorConfig
    n = 0
    with db.session() as s:
        for c in s.scalars(select(ObservadorConfig).where(ObservadorConfig.activo.is_(True))):
            if clave is None or c.clave == clave:
                c.activo, c.estado, c.motivo, c.updated_at = False, DETENIDO, f"detenido por {usuario}"[:200], now
                n += 1
    return n


def listar_config(db) -> list[dict]:
    from ..models import ObservadorConfig
    with db.session() as s:
        return [vista_config(c) for c in s.scalars(select(ObservadorConfig).order_by(ObservadorConfig.clave))]


class Supervisor:
    """Un hilo por proceso del motor: mantiene en marcha lo configurado (y solo una vez entre procesos)."""

    def __init__(self, db, periodo_s: float = 3.0, fabrica=None, holder: str | None = None):
        from ..engine.lease import make_holder_id
        self.db, self.periodo_s = db, periodo_s
        self.holder = holder or make_holder_id()
        self.fabrica = fabrica or (lambda c: Observador(db, c.sucursal, c.tramite, c.mes, c.estrategia,
                                                        c.intervalo_visual_ms))
        self.activos: dict[str, Observador] = {}
        self.arriendos: dict = {}
        self._stop = threading.Event()
        self._hilo: threading.Thread | None = None
        self._lock = threading.Lock()
        self.ultimo_error: str | None = None

    def start(self) -> None:
        self._hilo = threading.Thread(target=self._run, name="supervisor-observadores", daemon=True)
        self._hilo.start()

    def stop(self, espera: float = 15) -> None:
        self._stop.set()
        if self._hilo:
            self._hilo.join(espera)
        with self._lock:
            for clave in list(self.activos):
                self._parar(clave, utcnow(), soltar=True)

    detener = stop

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.ciclo(utcnow())
                self.ultimo_error = None
            except Exception as e:  # noqa: BLE001 - p. ej. la base no responde: se reintenta en el siguiente ciclo
                self.ultimo_error = f"{e.__class__.__name__}: {str(e)[:120]}"
                log.warning("supervisor de observadores: %s", self.ultimo_error)
            self._stop.wait(self.periodo_s)

    def _parar(self, clave: str, now: datetime, soltar: bool) -> None:
        o = self.activos.pop(clave, None)
        if o is not None:
            o.detener()
        a = self.arriendos.pop(clave, None)
        if a is not None and soltar:
            try:
                a.release(_naive(now))
            except Exception:  # noqa: BLE001
                pass

    def ciclo(self, now: datetime) -> dict:
        from ..engine.lease import Lease
        from ..models import ObservadorConfig
        now = _naive(now)
        out = {"arrancados": [], "detenidos": [], "fallidos": [], "de_otro_proceso": []}
        with self._lock:
            with self.db.session() as s:
                cfgs = {c.clave: (c.activo, c.estado) for c in s.scalars(select(ObservadorConfig))}
            for clave, o in list(self.activos.items()):          # límites superados o desactivados
                activo, _ = cfgs.get(clave, (False, None))
                if o.fallo:
                    with self.db.session() as s:
                        c = s.get(ObservadorConfig, clave)
                        if c is not None:
                            c.activo, c.estado, c.motivo, c.updated_at = False, DETENIDO_POR_FALLOS, o.fallo, now
                            c.reinicios = o.stats.get("reinicios_navegador", 0)
                    self._parar(clave, now, soltar=True)
                    out["fallidos"].append(clave)
                elif not activo:
                    self._parar(clave, now, soltar=True)
                    out["detenidos"].append(clave)
            for clave, (activo, estado) in cfgs.items():
                if not activo or estado != ACTIVO:
                    continue
                a = self.arriendos.get(clave) or Lease(self.db, name=f"obs:{clave}"[:64], holder=self.holder,
                                                       ttl_s=TTL_ARRIENDO_S)
                if not a.acquire_or_renew(now):
                    if clave in self.activos:                      # otro proceso lo tomó: este se aparta
                        self._parar(clave, now, soltar=False)
                    out["de_otro_proceso"].append(clave)
                    continue
                self.arriendos[clave] = a
                if clave not in self.activos:
                    with self.db.session() as s:
                        c = s.get(ObservadorConfig, clave)
                        s.expunge(c)
                    o = self.fabrica(c)
                    try:
                        o.iniciar()
                    except RuntimeError as e:          # sin Node/Playwright: queda registrado, no se reintenta en bucle
                        with self.db.session() as s:
                            c2 = s.get(ObservadorConfig, clave)
                            c2.activo, c2.estado, c2.motivo, c2.updated_at = False, DETENIDO_POR_FALLOS, str(e)[:200], now
                        a.release(now)
                        self.arriendos.pop(clave, None)
                        out["fallidos"].append(clave)
                        continue
                    self.activos[clave] = o
                    with self.db.session() as s:
                        c2 = s.get(ObservadorConfig, clave)
                        c2.titular, c2.updated_at = self.holder, now
                    out["arrancados"].append(clave)
        return out

    def estado(self) -> dict:
        return {"proceso": self.holder, "ultimo_error": self.ultimo_error,
                "observadores": [o.estado() for o in list(self.activos.values())]}


_supervisor: Supervisor | None = None


def supervisor(db=None) -> Supervisor | None:
    global _supervisor
    if _supervisor is None and db is not None:
        _supervisor = Supervisor(db)
    return _supervisor


def arrancar_supervisor(db) -> Supervisor:
    global _supervisor
    _supervisor = Supervisor(db)
    _supervisor.start()
    return _supervisor


def parar_supervisor() -> None:
    global _supervisor
    if _supervisor is not None:
        _supervisor.stop()
        _supervisor = None
