"""Conector de DISPONIBILIDAD del portal de reservaciones de PRUEBA (v1.5).

Habla solo con nuestro portal de laboratorio (`portal_reservas`, en 127.0.0.1). Publica un
recurso por SUCURSAL ("agenda"), con tantas unidades disponibles como horarios libres tenga en
el periodo. Así el motor detecta "hay citas en la sucursal X" y asigna un expediente a un
operador con su lógica de siempre; el horario concreto lo elige el operador en el calendario.
(Publicar cada horario como recurso generaría cientos de avisos por consulta.)

Contrato del recurso:
  resource_key  "agenda:<sucursal>"
  name          "<nombre de la sucursal> (<sucursal>)"   -> la condición `nombre_contiene` filtra sucursal
  resource_type "cita"
  region        región del motor (mayúsculas)
  starts_at     primer horario libre
"""
from __future__ import annotations

from datetime import datetime


from ..contracts import Observation, Snapshot
from .base import Connector, ConnectorError
from .http_api import USER_AGENT, check_url, http_get, raise_for_status


class PortalReservasConnector(Connector):
    type_name = "portal_reservas"
    uses_network = True

    def validate_config(self) -> None:
        self.base = str(self.config.get("url_base", "")).rstrip("/")
        check_url(self.base, self.config.get("_allowed_hosts"))
        self.timeout = float(self.config.get("timeout_s", 5))
        self.region = self.config.get("region")      # opcional: limitar a una región del portal

    def fetch(self) -> Snapshot:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        # Token de PRUEBA (de .env vía token_env); si falta, el del laboratorio local.
        headers["Authorization"] = f"Bearer {self.config.get('_token') or 'token-prueba-portal-local'}"
        params = {"region": self.region} if self.region else None
        r = http_get(self.context, f"{self.base}/api/disponibilidad", headers, self.timeout, params=params)
        raise_for_status(r)
        try:
            horarios = r.json()["horarios"]
            agendas: dict[str, dict] = {}
            for h in horarios:
                a = agendas.setdefault(h["sucursal"], {"nombre": h["sucursal_nombre"], "region": h["region"],
                                                       "total": 0, "libres": 0, "primero": None})
                a["total"] += int(h["capacidad"])
                if int(h["libres"]) > 0:
                    a["libres"] += int(h["libres"])
                    inicio = datetime.fromisoformat(f"{h['fecha']}T{h['hora']}")
                    a["primero"] = min(a["primero"] or inicio, inicio)
        except (ValueError, KeyError, TypeError) as e:
            raise ConnectorError(f"respuesta con formato inesperado: {e.__class__.__name__}") from e
        obs = [Observation(resource_key=f"agenda:{suc}", name=f"{a['nombre']} ({suc})", resource_type="cita",
                           region=a["region"].upper(), starts_at=a["primero"], capacity=a["total"],
                           available=a["libres"], attributes={"sucursal": suc, "horarios_libres": a["libres"]})
               for suc, a in sorted(agendas.items())]
        return Snapshot(observations=obs, complete=True, meta={"horarios": len(horarios)})
