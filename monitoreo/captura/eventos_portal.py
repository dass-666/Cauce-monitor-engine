"""Mecanismo A (v1.6): el motor ESCUCHA los eventos del portal de PRUEBA (text/event-stream).

Un hilo por fuente `portal_dias` con `mecanismo: "eventos"`, solo en la instancia líder:
  1. abre una sesión de suscripción (caduca: el portal lo avisa con `sesion_caducada`);
  2. se conecta a /api/eventos desde la última secuencia aplicada (reanudable);
  3. cada evento `dia` se aplica con la regla de versión (lo que llega fuera de orden se descarta);
  4. `resincronizar` (hueco en la secuencia) => pide el calendario completo y sigue;
  5. `latido` confirma que el calendario sigue al día (actualiza `confirmado_en`);
  6. corte de conexión => DESCONEXION registrada, reintento con espera creciente (1-30 s).
Cuando un evento cambia la disponibilidad, avisa al planificador para consultar esa fuente YA
(la consulta lee lo recibido sin volver a pedirlo al portal) y así asignar en seguida.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from ..config import settings
from ..connectors.http_api import USER_AGENT, check_url
from ..db import Database
from ..models import Source
from . import calendario_dias as cd
from . import zona

log = logging.getLogger("monitoreo.calendario")


def _ahora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class OyenteEventos(threading.Thread):
    def __init__(self, db: Database, source_id: int, base: str, token: str, al_cambiar=None,
                 timeout_lectura: float = 15.0):
        super().__init__(name=f"eventos-portal-{source_id}", daemon=True)
        self.db, self.sid, self.base, self.token = db, source_id, base.rstrip("/"), token
        self.al_cambiar = al_cambiar or (lambda sid: None)
        self.timeout_lectura = timeout_lectura
        self.parar = threading.Event()
        self.estadisticas = {"conexiones": 0, "eventos": 0, "errores": 0}
        check_url(self.base, list(settings.webhook_allowed_hosts))
        self.http = httpx.Client(trust_env=False, headers={"User-Agent": USER_AGENT,
                                                           "Authorization": f"Bearer {self.token}"})

    def detener(self) -> None:
        self.parar.set()

    # ---------------------------------------------------------------- utilidades
    def _resincronizar(self, motivo: str) -> int:
        j = cd.consultar_portal(self.base, self.token, 10, list(settings.webhook_allowed_hosts), client=self.http)
        now = _ahora()
        with self.db.session() as s:
            res = cd.aplicar(s, self.sid, j["dias"], cd.EVENTOS, now, completo=True, hoy_portal=j.get("hoy"),
                             zona_portal=j.get("zona_horaria"))
            f = cd.fuente(s, self.sid, cd.EVENTOS)
            f.resincronizaciones += 1
            f.ultima_secuencia = int(j.get("secuencia") or 0)
            cd.marcar_ok(s, self.sid, cd.EVENTOS, now, conectado=True)
            if not res["linea_base"]:
                s.add(cd.CalendarioCambio(source_id=self.sid, tipo="RESINCRONIZACION", mecanismo=cd.EVENTOS,
                                          detectado_en=now, detalle={"motivo": motivo, "cambios": len(res["cambios"])}))
        if res["cambios"]:
            self.al_cambiar(self.sid)
        return int(j.get("secuencia") or 0)

    def _error(self, codigo: str, mensaje: str) -> None:
        self.estadisticas["errores"] += 1
        try:
            with self.db.session() as s:
                cd.marcar_error(s, self.sid, codigo, mensaje, _ahora(), mecanismo=cd.EVENTOS)
        except Exception:  # noqa: BLE001
            log.exception("no se pudo registrar el error del calendario")

    # ---------------------------------------------------------------- bucle
    def run(self) -> None:
        espera = 1.0
        with self.db.session() as s:
            f = cd.fuente(s, self.sid, cd.EVENTOS)
            seq = f.ultima_secuencia or 0
            primera = f.confirmado_en is None
        hubo_error = False
        while not self.parar.is_set():
            try:
                if primera or hubo_error:
                    seq = self._resincronizar("inicio" if primera else "reconexion")
                    if hubo_error:
                        with self.db.session() as s:
                            cd.fuente(s, self.sid).reconexiones += 1
                            s.add(cd.CalendarioCambio(source_id=self.sid, tipo="RECONEXION", mecanismo=cd.EVENTOS,
                                                      detectado_en=_ahora(), detalle={}))
                    primera = hubo_error = False
                r = self.http.post(self.base + "/api/eventos/sesion", timeout=10)
                if r.status_code != 200:
                    raise httpx.HTTPStatusError(f"sesión de eventos HTTP {r.status_code}", request=r.request, response=r)
                sesion = r.json()["sesion"]
                seq = self._escuchar(sesion, seq)
                espera = 1.0
            except _SesionCaducada:
                self._error("SESION_CADUCADA", "la sesión de eventos caducó: se abre otra")
                hubo_error = True
                espera = 0.2
            except (httpx.HTTPError, cd.CalendarioNoVerificado, ValueError, KeyError) as e:
                self._error("DESCONEXION", f"{e.__class__.__name__}: {str(e)[:150]}")
                hubo_error = True
            except Exception as e:  # noqa: BLE001 - el hilo nunca debe morir en silencio
                log.exception("fallo inesperado escuchando eventos")
                self._error("DESCONEXION", f"{e.__class__.__name__}")
                hubo_error = True
            if not self.parar.is_set() and hubo_error:
                self.parar.wait(espera)
                espera = min(30.0, espera * 2)
        self.http.close()

    def _escuchar(self, sesion: str, seq: int) -> int:
        params = {"sesion": sesion, "desde": seq}
        tmo = httpx.Timeout(10, read=self.timeout_lectura)
        with self.http.stream("GET", self.base + "/api/eventos", params=params, timeout=tmo) as r:
            if r.status_code == 401:
                raise _SesionCaducada()
            if r.status_code != 200:
                raise httpx.HTTPStatusError(f"eventos HTTP {r.status_code}", request=r.request, response=r)
            self.estadisticas["conexiones"] += 1
            with self.db.session() as s:
                cd.marcar_ok(s, self.sid, cd.EVENTOS, _ahora(), conectado=True)
            tipo, datos = None, []
            for linea in r.iter_lines():
                if self.parar.is_set():
                    break
                if linea.startswith("event:"):
                    tipo = linea[6:].strip()
                elif linea.startswith("data:"):
                    datos.append(linea[5:].strip())
                elif linea == "" and tipo:
                    seq = self._procesar(tipo, "\n".join(datos), seq)
                    tipo, datos = None, []
            else:
                if not self.parar.is_set():
                    raise httpx.ReadError("el portal cerró el flujo de eventos")
        return seq

    def _procesar(self, tipo: str, data: str, seq: int) -> int:
        if tipo == "sesion_caducada":
            raise _SesionCaducada()
        payload = json.loads(data or "{}")
        now = _ahora()
        if tipo == "dia":
            self.estadisticas["eventos"] += 1
            with self.db.session() as s:
                res = cd.aplicar(s, self.sid, [payload], cd.EVENTOS, now, completo=False,
                                 ts_portal=zona.parse_iso(payload.get("ts")))
                f = cd.fuente(s, self.sid)
                f.eventos_recibidos += 1
                f.ultima_secuencia = max(f.ultima_secuencia or 0, int(payload["seq"]))
                cd.marcar_ok(s, self.sid, cd.EVENTOS, now, conectado=True)
            if res["cambios"]:
                self.al_cambiar(self.sid)
            return max(seq, int(payload["seq"]))
        if tipo == "resincronizar":
            return self._resincronizar("hueco en la secuencia")
        if tipo in ("latido", "abierto"):
            with self.db.session() as s:
                cd.marcar_ok(s, self.sid, cd.EVENTOS, now, conectado=True)
        return seq


class _SesionCaducada(Exception):
    pass


class GestorEventos:
    """Mantiene un oyente vivo por cada fuente `portal_dias` en modo eventos (lo usa el planificador)."""

    def __init__(self, db: Database, al_cambiar=None):
        self.db, self.al_cambiar = db, al_cambiar
        self.oyentes: dict[int, OyenteEventos] = {}

    def revisar(self) -> None:
        from .portal import TOKEN_LOCAL
        import os
        with self.db.session() as s:
            deseadas = {src.id: dict(src.config or {}) for src in s.scalars(
                select(Source).where(Source.connector_type == "portal_dias", Source.status == "ACTIVA"))
                if str((src.config or {}).get("mecanismo", "")).lower() == "eventos"}
        for sid in list(self.oyentes):
            if sid not in deseadas or not self.oyentes[sid].is_alive():
                self.oyentes.pop(sid).detener()
        for sid, cfg in deseadas.items():
            if sid not in self.oyentes:
                token = os.environ.get(cfg.get("token_env") or "") or TOKEN_LOCAL
                try:
                    o = OyenteEventos(self.db, sid, str(cfg.get("url_base", "")), token, self.al_cambiar,
                                      timeout_lectura=max(5.0, 4 * float(cfg.get("latido_s", 2))))
                except Exception as e:  # noqa: BLE001 - configuración inválida: se registra y se omite
                    log.warning("fuente %s: no se puede escuchar eventos: %s", sid, e)
                    continue
                o.start()
                self.oyentes[sid] = o

    def detener(self) -> None:
        for o in self.oyentes.values():
            o.detener()
        for o in self.oyentes.values():
            o.join(timeout=5)
        self.oyentes.clear()

    def estado(self) -> dict:
        return {sid: {"vivo": o.is_alive(), **o.estadisticas} for sid, o in self.oyentes.items()}


__all__ = ["OyenteEventos", "GestorEventos", "time"]
