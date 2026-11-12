"""Contrato interno común.

Todo conector, sin importar de dónde lea (simulador, JSON, CSV, API), debe
entregar un `Snapshot` con una lista de `Observation`. El resto del motor
(detector, asignación, alertas) solo conoce este contrato, nunca el formato
original de la fuente. Así se puede añadir una API externa autorizada
escribiendo únicamente un conector nuevo.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Observation(BaseModel):
    """Un recurso observado en una consulta (función, mesa, cupo, turno, artículo...)."""
    model_config = ConfigDict(extra="forbid")

    resource_key: str = Field(min_length=1, max_length=128, description="Identificador estable del recurso en la fuente")
    name: str = Field(min_length=1, max_length=256)
    resource_type: str = Field(min_length=1, max_length=64)
    region: str | None = Field(default=None, max_length=32)
    starts_at: datetime | None = None
    capacity: int | None = Field(default=None, ge=0)
    available: int = Field(ge=0)
    status: str = Field(default="ACTIVO", pattern="^(ACTIVO|CANCELADO)$")
    changed_at: datetime | None = Field(default=None, description="Cuándo cambió en la fuente, si la fuente lo informa")
    attributes: dict = Field(default_factory=dict)

    @field_validator("starts_at", "changed_at")
    @classmethod
    def _naive_utc(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is not None:
            from datetime import timezone
            v = v.astimezone(timezone.utc).replace(tzinfo=None)
        return v

    def fingerprint(self) -> str:
        """Huella del contenido relevante. Si no cambia, la observación es un duplicado."""
        data = {
            "n": self.name, "t": self.resource_type, "s": self.starts_at.isoformat() if self.starts_at else None,
            "c": self.capacity, "a": self.available, "st": self.status,
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


class Snapshot(BaseModel):
    """Resultado de una consulta a una fuente."""
    observations: list[Observation]
    # complete=True significa "esta es la lista completa": lo que no aparece ya no existe.
    # Una fuente paginada o parcial debe enviar False para no inferir cancelaciones falsas.
    complete: bool = True
    fetched_at: datetime | None = None
    # v1.3 (genérico, sirve para cualquier fuente que lo soporte):
    # - not_modified: la fuente confirmó que nada cambió desde la consulta anterior (HTTP 304);
    #   el detector no compara nada y no se infieren desapariciones.
    # - state: estado opaco del conector que el motor guarda y le devuelve en la siguiente
    #   consulta (p. ej. ETag o versión); así sobrevive a reinicios del motor.
    # - meta: datos de la consulta para diagnóstico (páginas, tiempos HTTP, elementos descartados).
    not_modified: bool = False
    state: dict | None = None
    meta: dict = Field(default_factory=dict)
