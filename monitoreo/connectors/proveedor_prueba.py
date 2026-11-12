"""Conector para el PROVEEDOR DE PRUEBA de la v1.3 (contrato en proveedor_prueba/CONTRATO.md).

Importante: este conector habla con NUESTRO servicio de prueba (datos ficticios, en
127.0.0.1). No es una integración con ningún proveedor real. Una integración futura con un
proveedor externo autorizado tendría su propio conector, escrito a partir del contrato y las
condiciones de uso de ese proveedor.

Lo que hace (todo dentro del conector; el motor solo recibe el `Snapshot` común):
- Recorre las páginas de `/v1/regiones/{region}/disponibilidad` con su cursor.
- Si los datos cambian entre páginas (409 cursor_obsoleto o versión distinta) reinicia una
  vez; si vuelve a pasar, entrega lo leído como INCOMPLETO (`complete=False`) para que el
  motor no deduzca desapariciones falsas.
- Envía `If-None-Match` con el ETag anterior: un 304 se entrega como `not_modified`.
- Detecta respuestas ANTIGUAS (versión y fecha de generación anteriores a lo ya procesado) y
  las rechaza con `StaleResponse`.
- Descuenta las ventanas de disponibilidad que vencieron mientras la respuesta viajaba.
- Descarta elementos mal formados (y marca la consulta como incompleta).
- Mide el tiempo de cada petición HTTP y el que informa el servidor.
- Su estado (ETag, versión, fecha) lo guarda el MOTOR en la base de datos y se lo devuelve
  en la siguiente consulta, así sobrevive a reinicios.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import httpx
from pydantic import ValidationError

from ..contracts import Observation, Snapshot
from .base import Connector, ConnectorError, StaleResponse
from .http_api import USER_AGENT, check_url, http_get, raise_for_status

_lock = threading.Lock()
_client: httpx.Client | None = None


def _shared_client() -> httpx.Client:
    """Conexiones reutilizables (keep-alive). Sin redirecciones: podrían llevar a otro host."""
    global _client
    with _lock:
        if _client is None:
            _client = httpx.Client(follow_redirects=False,
                                   limits=httpx.Limits(max_connections=20, max_keepalive_connections=10))
        return _client


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


class ProveedorPruebaConnector(Connector):
    type_name = "proveedor_prueba"
    uses_network = True

    def validate_config(self) -> None:
        self.base = str(self.config.get("url_base", "")).rstrip("/")
        check_url(self.base, self.config.get("_allowed_hosts"))
        self.region = self.config.get("region")
        if not self.region or not str(self.region).isidentifier():
            raise ConnectorError("falta 'region' (p. ej. norte)")
        self.timeout = float(self.config.get("timeout_s", 5))
        self.limite = int(self.config.get("limite", 50))
        self.max_paginas = int(self.config.get("max_paginas", 50))

    # ------------------------------------------------------------------
    def fetch(self) -> Snapshot:
        url = f"{self.base}/v1/regiones/{self.region}/disponibilidad"
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if self.config.get("_token"):
            headers["Authorization"] = f"Bearer {self.config['_token']}"
        state = dict(self.context.get("state") or {})
        client = self.context.get("http_client") or _shared_client()
        ctx = {"http_client": client}
        meta = {"paginas": 0, "http_ms": [], "servidor_ms": [], "reinicios": 0, "descartados": 0, "bytes": 0}

        for intento in range(2):
            items: list[dict] = []
            version = generado = etag = None
            cursor, coherente, terminado = None, True, False
            while True:
                h = dict(headers)
                if cursor is None and intento == 0 and state.get("etag") and not self.context.get("refresh"):
                    h["If-None-Match"] = state["etag"]
                params = {"limite": self.limite, **({"cursor": cursor} if cursor else {})}
                t0 = time.perf_counter()
                r = http_get(ctx, url, h, self.timeout, params=params)
                meta["http_ms"].append(round((time.perf_counter() - t0) * 1000, 2))
                meta["paginas"] += 1
                meta["bytes"] += len(r.content)
                try:
                    meta["servidor_ms"].append(float(r.headers.get("X-Tiempo-Servidor-ms", "nan")))
                except ValueError:
                    pass
                if r.status_code == 304 and cursor is None:
                    meta["sin_cambios"] = True
                    return Snapshot(observations=[], complete=True, not_modified=True, state=state, meta=meta)
                if r.status_code == 409 and cursor is not None:
                    coherente = False
                    break
                raise_for_status(r)
                try:
                    data = r.json()
                    pv, pg, page = int(data["version"]), _dt(data["generado_en"]), data["recursos"]
                    if data.get("region") != self.region or not isinstance(page, list):
                        raise ValueError("región o lista inesperada")
                except (ValueError, KeyError, TypeError) as e:
                    raise ConnectorError(f"respuesta con formato inesperado: {e.__class__.__name__}") from e
                if cursor is None:
                    self._check_not_stale(state, pv, pg, meta)
                    version, generado, etag = pv, pg, r.headers.get("ETag")
                elif pv != version:
                    coherente = False
                    break
                items.extend(page)
                cursor = data.get("siguiente")
                if not cursor:
                    terminado = True
                    break
                if meta["paginas"] >= self.max_paginas * (intento + 1):
                    break
            if coherente:
                break
            meta["reinicios"] += 1          # los datos cambiaron a mitad: se vuelve a empezar una vez

        received = datetime.now(timezone.utc)
        obs = []
        for it in items:
            try:
                obs.append(self._map(it, received))
            except (KeyError, ValueError, TypeError, ValidationError):
                meta["descartados"] += 1
        complete = coherente and terminado and meta["descartados"] == 0
        meta["incompleta"] = not complete
        new_state = state
        if complete:
            new_state = {"etag": etag, "version": version, "generado_en": generado.isoformat() if generado else None}
        elif version is not None:
            # Se guarda la versión vista (para detectar respuestas antiguas) pero NO el ETag: la
            # próxima consulta debe traer los datos completos en lugar de un 304.
            new_state = {**state, "version": max(version, state.get("version") or 0)}
            new_state.pop("etag", None)
        meta["version"] = version
        return Snapshot(observations=obs, complete=complete, fetched_at=received.replace(tzinfo=None),
                        state=new_state, meta=meta)

    @staticmethod
    def _check_not_stale(state: dict, version: int, generado: datetime | None, meta: dict) -> None:
        pv, pg = state.get("version"), _dt(state.get("generado_en"))
        if pv is None or version >= pv:
            return
        if pg is not None and generado is not None and generado <= pg:
            raise StaleResponse(f"respuesta antigua: versión {version} < {pv} ya procesada")
        # Versión menor pero generada después: el proveedor se reinició y perdió cambios recientes.
        meta["reinicio_proveedor"] = True

    def _map(self, it: dict, received: datetime) -> Observation:
        estado = it["estado"]
        disponibles = int(it["disponibles"])
        # Ventanas que vencieron entre que el servidor generó la respuesta y que la recibimos:
        # esas unidades ya no existen aunque la respuesta las cuente.
        vencidas = sum(int(v["unidades"]) for v in it.get("ventanas") or [] if _dt(v["hasta"]) <= received)
        disponibles = max(0, disponibles - vencidas)
        if estado == "CERRADO":
            disponibles = 0
        return Observation(
            resource_key=str(it["id"]), name=str(it["nombre"]), resource_type=str(it["tipo"]),
            region=self.config.get("region_motor") or None, starts_at=it.get("inicio"),
            capacity=it.get("capacidad"), available=disponibles,
            status="CANCELADO" if estado == "CANCELADO" else "ACTIVO", changed_at=it.get("actualizado_en"),
            attributes={"estado_proveedor": estado, "disponible_hasta": it.get("disponible_hasta"),
                        **({"vencidas_en_transito": vencidas} if vencidas else {})})
