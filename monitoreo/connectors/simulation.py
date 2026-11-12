"""Conector de simulación: lee el inventario ficticio del laboratorio (tabla sim_items).

Permite inyectar fallos para probar la resiliencia del motor:
  error_rate       probabilidad de error temporal en cada consulta
  rate_limit_rate  probabilidad de que la "fuente" responda 'limitado' (429)
  retry_after_s    segundos que pide esperar cuando limita
  latency_ms       latencia artificial (solo en tiempo real)
"""
from __future__ import annotations

import random
import time

from sqlalchemy import select

from ..contracts import Observation, Snapshot
from .base import Connector, ConnectorError, RateLimited, TemporaryError


class SimulationConnector(Connector):
    type_name = "simulacion"

    def validate_config(self) -> None:
        if not self.config.get("feed"):
            raise ConnectorError("La fuente simulada necesita 'feed'")
        for k in ("error_rate", "rate_limit_rate"):
            v = float(self.config.get(k, 0))
            if not 0 <= v <= 1:
                raise ConnectorError(f"{k} debe estar entre 0 y 1")

    def fetch(self) -> Snapshot:
        from ..models import SimItem
        rng: random.Random = self.context.get("rng") or random
        db = self.context["db"]
        if self.config.get("latency_ms") and not self.context.get("virtual_time"):
            time.sleep(float(self.config["latency_ms"]) / 1000)
        if rng.random() < float(self.config.get("rate_limit_rate", 0)):
            raise RateLimited("simulador: demasiadas solicitudes", retry_after=float(self.config.get("retry_after_s", 30)))
        if rng.random() < float(self.config.get("error_rate", 0)):
            raise TemporaryError("simulador: fallo de conexión simulado")
        with db.session() as s:
            items = s.scalars(select(SimItem).where(SimItem.feed == self.config["feed"])).all()
            obs = [Observation(
                resource_key=i.resource_key, name=i.name, resource_type=i.resource_type, region=i.region,
                starts_at=i.starts_at, capacity=i.capacity, available=0 if i.cancelled else i.available,
                status="CANCELADO" if i.cancelled else "ACTIVO", changed_at=i.changed_at,
            ) for i in items]
        # Duplicados intencionados: algunas fuentes reales repiten filas.
        if self.config.get("duplicate_rate") and obs and rng.random() < float(self.config["duplicate_rate"]):
            obs.append(obs[rng.randrange(len(obs))].model_copy())
        return Snapshot(observations=obs, complete=True)
