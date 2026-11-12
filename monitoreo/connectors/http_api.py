"""Conector HTTP para una API de prueba local (y plantilla para APIs autorizadas).

Comportamiento respetuoso por diseño:
- Se identifica con un User-Agent honesto, sin rotar identidades ni proxies.
- Respeta HTTP 429/503 y la cabecera Retry-After (no reintenta antes).
- No intenta sortear autenticación, captchas ni bloqueos.
"""
from __future__ import annotations

from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

from ..contracts import Observation, Snapshot
from .base import Connector, ConnectorError, RateLimited, TemporaryError

USER_AGENT = "MotorMonitoreo/1.0 (+uso autorizado; contacto en configuracion)"


def http_get(context: dict, url: str, headers: dict, timeout: float, params: dict | None = None) -> httpx.Response:
    """GET con la traducción común de errores de red (compartida por los conectores HTTP)."""
    client: httpx.Client | None = context.get("http_client")
    try:
        if client is not None:
            return client.get(url, headers=headers, timeout=timeout, params=params)
        return httpx.get(url, headers=headers, timeout=timeout, params=params)
    except httpx.TimeoutException as e:
        raise TemporaryError("tiempo de espera agotado") from e
    except httpx.TransportError as e:
        raise TemporaryError(f"error de conexión: {e.__class__.__name__}") from e


def raise_for_status(r: httpx.Response) -> None:
    """Traduce el código HTTP a los errores del motor (429/503 respetan Retry-After)."""
    if r.status_code in (429, 503):
        ra = r.headers.get("Retry-After")
        try:
            retry = float(ra) if ra is not None else None
        except ValueError:
            retry = None
        raise RateLimited(f"HTTP {r.status_code}", retry_after=retry)
    if r.status_code >= 500:
        raise TemporaryError(f"HTTP {r.status_code}")
    if r.status_code in (401, 403):
        raise ConnectorError(f"HTTP {r.status_code}: acceso denegado (no se intentará evadir)")
    if r.status_code >= 400:
        raise ConnectorError(f"HTTP {r.status_code}")


def check_url(url: str, allowed) -> None:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ConnectorError("URL inválida")
    if allowed is not None and p.hostname not in allowed:
        raise ConnectorError(f"Host no permitido: {p.hostname}")


class HttpApiConnector(Connector):
    type_name = "http"
    uses_network = True

    def validate_config(self) -> None:
        check_url(self.config.get("url", ""), self.config.get("_allowed_hosts"))
        self.timeout = float(self.config.get("timeout_s", 10))

    def fetch(self) -> Snapshot:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        token = self.config.get("_token")  # credencial resuelta desde variable de entorno, nunca en la BD
        if token:
            headers["Authorization"] = f"Bearer {token}"
        r = http_get(self.context, self.config["url"], headers, self.timeout)
        raise_for_status(r)
        try:
            data = r.json()
            items = data["items"]
            return Snapshot(observations=[Observation(**i) for i in items], complete=bool(data.get("complete", True)))
        except (ValueError, KeyError, TypeError, ValidationError) as e:
            raise ConnectorError(f"respuesta con formato inesperado: {e.__class__.__name__}") from e
