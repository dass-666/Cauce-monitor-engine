"""Interfaz de conectores (desacoplada del motor)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..contracts import Snapshot


class ConnectorError(Exception):
    """Error definitivo (configuración inválida, datos corruptos). No se reintenta en la misma consulta."""


class TemporaryError(ConnectorError):
    """Error transitorio (red, timeout, 5xx). Se reintenta con retroceso exponencial."""


class RateLimited(ConnectorError):
    """La fuente pidió bajar el ritmo (HTTP 429 / 503 con Retry-After).

    El motor NO reintenta: respeta `retry_after` antes de volver a consultar.
    """

    def __init__(self, message: str = "limitado por la fuente", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class StaleResponse(ConnectorError):
    """La fuente devolvió datos MÁS ANTIGUOS que los ya procesados (p. ej. una caché intermedia).

    v1.3: el motor descarta la respuesta sin compararla (no genera falsos cambios) y no la
    cuenta como fallo de la fuente; queda registrada con resultado OBSOLETA.
    """


class Connector(ABC):
    """Contrato de un conector.

    Un conector solo tiene una responsabilidad: leer la fuente y traducir su
    formato al `Snapshot` interno. No decide alertas, ni asignaciones, ni ritmo
    de consulta: eso lo hace el motor.
    """

    type_name: str = "base"
    # v1.3: un conector de red recibe la lista de hosts permitidos y la credencial resuelta
    # desde la variable de entorno indicada en `token_env` (nunca se guarda en la BD).
    uses_network: bool = False

    def __init__(self, config: dict[str, Any], context: dict[str, Any] | None = None):
        self.config = config or {}
        self.context = context or {}
        self.validate_config()

    def validate_config(self) -> None:  # pragma: no cover - por defecto no valida nada
        pass

    @abstractmethod
    def fetch(self) -> Snapshot:
        """Consulta la fuente y devuelve el estado completo (o parcial) normalizado."""
