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
from pathlib import Path
from urllib.parse import urlencode

from ..db import utcnow
from . import coincidencias, observaciones, operacion

log = logging.getLogger("monitoreo.planificador")
ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "observador_calendario.js"


class Observador:
    def __init__(self, db, sucursal: str, tramite: str, mes: str | None = None, estrategia: str = "ambas",
                 intervalo_visual_ms: int = 250, congelado_s: float = 5.0, evidencia_dir: str | None = None,
                 nombre: str | None = None, atender: bool = True, jornada=None):
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
        self._cfg_file: str | None = None

    # ------------------------------------------------------------------ ciclo de vida
    def iniciar(self) -> None:
        if shutil.which("node") is None:
            raise RuntimeError("Node.js no está disponible: el observador necesita Node + Playwright")
        fd, self._cfg_file = tempfile.mkstemp(prefix="observador-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f)
        self._hilo = threading.Thread(target=self._bucle, name=f"observador-{self.nombre}", daemon=True)
        self._hilo.start()

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
            self.proc = subprocess.Popen(["node", str(SCRIPT), self._cfg_file], cwd=ROOT, stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                                         bufsize=1)
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
            if self._stop.is_set():
                break
            self.stats["reinicios_navegador"] += 1
            log.warning("observador %s: el proceso terminó (código %s); se relanza en %.0f s", self.nombre,
                        self.proc.returncode, espera)
            if time.monotonic() - t0 > 60:
                espera = 1.0
            if self._stop.wait(espera):
                break
            espera = min(30.0, espera * 2)

    # ------------------------------------------------------------------ procesamiento
    def _procesar(self, obs: dict) -> None:
        self.stats["lineas"] += 1
        tipo = obs.get("tipo")
        if tipo == "LATIDO":
            self.stats["ultimo_latido"] = obs.get("ts_ms")
            self._reintentar()
            return
        self.lineas.append(obs)
        if tipo in ("INICIAL", "CELDA", "VISTA"):
            self.pendientes.append(obs)
            self._reintentar()
        if tipo == "VISTA":
            self.stats["vista"][obs.get("estrategia") or "?"] = obs.get("estado")
            if obs.get("estado") == "SESION_CADUCADA" and obs.get("estrategia") in ("dom", self.estrategia):
                threading.Thread(target=self._reabrir, daemon=True).start()
            elif obs.get("estado") == "ACTIVA":
                self._espera_reabrir = 2.0

    def _reabrir(self) -> None:
        espera = self._espera_reabrir
        self._espera_reabrir = min(60.0, espera * 2)
        if self._stop.wait(espera):
            return
        self.stats["reaperturas_sesion"] += 1
        log.warning("observador %s: la sesión de consulta caducó; se reabre la página (espera %.0f s)", self.nombre, espera)
        self.orden("reabrir")

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
            if self.atender and any(e["tipo"] == observaciones.DIA_NO_DISPONIBLE for e in nuevos):
                try:
                    coincidencias.liberar_desaparecidos(self.db, utcnow())
                except Exception as e:  # noqa: BLE001
                    log.warning("liberar desaparecidos falló: %s", e)
            if self.atender and any(e["tipo"] == observaciones.DIA_DISPONIBLE for e in nuevos):
                try:
                    r = coincidencias.atender_pendientes(self.db, utcnow(), self.jornada)
                    self.stats["coincidencias"] += sum(1 for x in r if x.get("asignado"))
                except Exception as e:  # noqa: BLE001 - se reintenta con el siguiente evento o por mantenimiento
                    log.warning("coincidencias fallaron: %s", e)

    def estado(self) -> dict:
        return {"nombre": self.nombre, "url": self.url, "estrategia": self.estrategia,
                "vivo": bool(self.proc and self.proc.poll() is None), "pendientes": len(self.pendientes), **self.stats}


def mantenimiento(db, now) -> dict:
    """Lo puede llamar el planificador del motor: atiende eventos sin atender y sincroniza el plan."""
    return {"devueltos": coincidencias.liberar_desaparecidos(db, now),
            "coincidencias": [x for x in coincidencias.atender_pendientes(db, now) if x.get("asignado")],
            "sincronizacion": operacion.sincronizar(db, now)}
