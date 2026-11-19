"""Conector de disponibilidad POR DÍA del portal de PRUEBA (v1.6).

Publica para el núcleo del motor un recurso por (sucursal, trámite):
  resource_key  "dia:<sucursal>:<tramite>"
  resource_type "cita_dia"
  available     unidades libres sumando los días futuros disponibles
  attributes    {"sucursal", "tramite", "dias_disponibles", "primer_dia"}
Las solicitudes de expedientes POR_DIA filtran por estos atributos (sucursal preferida y
alternativas, trámite). El detalle por día vive en el calendario del motor (calendario_dias).

Configuración: {"url_base", "token_env", "mecanismo": "periodico" | "eventos", "timeout_s"}
- periodico: cada consulta pide el calendario completo (GET /api/dias).
- eventos:   un oyente aparte recibe los cambios al momento (eventos_portal.py); la consulta
             periódica solo lee lo ya recibido y SOLO pide el calendario completo si el flujo de
             eventos no está sano (respaldo).
Si el calendario no pudo verificarse, la consulta FALLA (no publica datos viejos como actuales).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..contracts import Observation, Snapshot
from .base import Connector, ConnectorError, TemporaryError


class PortalDiasConnector(Connector):
    type_name = "portal_dias"
    uses_network = True

    def validate_config(self) -> None:
        from .http_api import check_url
        self.base = str(self.config.get("url_base", "")).rstrip("/")
        check_url(self.base, self.config.get("_allowed_hosts"))
        self.timeout = float(self.config.get("timeout_s", 5))
        self.mecanismo = str(self.config.get("mecanismo", "periodico")).lower()
        if self.mecanismo not in ("periodico", "eventos"):
            raise ConnectorError("mecanismo debe ser 'periodico' o 'eventos'")

    def _token(self) -> str:
        from ..captura.portal import TOKEN_LOCAL
        return self.config.get("_token") or TOKEN_LOCAL

    def _eventos_sanos(self, s, sid: int, now: datetime) -> bool:
        from ..captura import calendario_dias as cd
        from ..models import CalendarioFuente
        f = s.get(CalendarioFuente, sid)
        if f is None or not f.conectado or cd.sin_verificar(f) or f.confirmado_en is None:
            return False
        return (now - f.confirmado_en) <= timedelta(seconds=max(10.0, 3 * float(self.config.get("latido_s", 2))))

    def fetch(self) -> Snapshot:
        from ..captura import calendario_dias as cd
        db, sid = self.context.get("db"), self.context.get("source_id")
        if db is None or sid is None:
            raise ConnectorError("el conector portal_dias necesita la base de datos del motor")
        now = self.context.get("now") or datetime.utcnow()
        consultado = False
        with db.session() as s:
            usar_eventos = self.mecanismo == "eventos" and self._eventos_sanos(s, sid, now)
        if not usar_eventos:
            try:
                j = cd.consultar_portal(self.base, self._token(), self.timeout, self.config.get("_allowed_hosts"))
            except cd.CalendarioNoVerificado as e:
                with db.session() as s:
                    cd.marcar_error(s, sid, e.codigo, e.mensaje, now,
                                    mecanismo=None if self.mecanismo == "eventos" else cd.PERIODICO)
                raise TemporaryError(f"calendario sin verificar: {e.mensaje}") from e
            consultado = True
            with db.session() as s:
                res = cd.aplicar(s, sid, j["dias"], cd.PERIODICO, now, completo=True,
                                 ts_portal=None, hoy_portal=j.get("hoy"), zona_portal=j.get("zona_horaria"))
                if self.mecanismo == "eventos":
                    f = cd.fuente(s, sid)
                    f.resincronizaciones += 1
                    cd.marcar_ok(s, sid, None, now)
                else:
                    cd.marcar_ok(s, sid, cd.PERIODICO, now, secuencia=j.get("secuencia"), conectado=True)
        with db.session() as s:
            recursos = cd.resumen_recursos(s, sid, now)
        obs = [Observation(resource_key=f"dia:{a['sucursal']}:{a['tramite']}",
                           name=f"{a['nombre']} ({a['sucursal']}) · {a['tramite']}", resource_type="cita_dia",
                           region=a["region"].upper(),
                           starts_at=datetime.fromisoformat(a["primero"]) if a["primero"] else None,
                           capacity=a["capacidad"], available=a["libres"],
                           attributes={"sucursal": a["sucursal"], "tramite": a["tramite"],
                                       "dias_disponibles": a["dias"], "primer_dia": a["primero"]})
               for a in recursos]
        meta = {"mecanismo": self.mecanismo, "consulta_completa": consultado, "recursos": len(obs)}
        if consultado:
            meta["cambios_calendario"] = len(res["cambios"])
        return Snapshot(observations=obs, complete=True, meta=meta)
