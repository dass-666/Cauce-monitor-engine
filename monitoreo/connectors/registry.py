"""Registro de tipos de conector. Añadir un conector = registrar una clase aquí."""
from __future__ import annotations

import os

from ..config import settings
from .base import Connector, ConnectorError
from .files import CsvFileConnector, JsonFileConnector
from .http_api import HttpApiConnector
from .portal_reservas import PortalReservasConnector
from .proveedor_prueba import ProveedorPruebaConnector
from .simulation import SimulationConnector

REGISTRY: dict[str, type[Connector]] = {
    c.type_name: c for c in (SimulationConnector, JsonFileConnector, CsvFileConnector, HttpApiConnector,
                                ProveedorPruebaConnector, PortalReservasConnector)
}


def build_connector(connector_type: str, config: dict, context: dict | None = None) -> Connector:
    cls = REGISTRY.get(connector_type)
    if cls is None:
        raise ConnectorError(f"Tipo de conector desconocido: {connector_type}")
    # Las claves internas (prefijo "_") nunca pueden venir de la configuración del usuario.
    cfg = {k: v for k, v in (config or {}).items() if not str(k).startswith("_")}
    if getattr(cls, "uses_network", False):
        cfg["_allowed_hosts"] = list(settings.webhook_allowed_hosts)
        # Las credenciales se referencian por nombre de variable de entorno, nunca se guardan en claro.
        env_name = cfg.get("token_env")
        if env_name:
            cfg["_token"] = os.environ.get(env_name)
    return cls(cfg, context)
