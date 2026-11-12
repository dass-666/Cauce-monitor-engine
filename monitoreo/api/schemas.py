"""Esquemas de entrada: validación estricta de todo lo que llega a la API."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Code = Field(pattern=r"^[A-Za-z0-9_.\-]{2,64}$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginIn(Strict):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserIn(Strict):
    username: str = Field(pattern=r"^[a-zA-Z0-9_.\-]{3,64}$")
    password: str = Field(min_length=10, max_length=256)
    role: Literal["admin", "operador"]
    operator_name: str | None = Field(default=None, max_length=128)
    regions: list[str] = Field(default_factory=list, max_length=10)


class PasswordIn(Strict):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=10, max_length=256)


class SourceIn(Strict):
    code: str = Code
    name: str = Field(min_length=1, max_length=128)
    region: str = Field(max_length=32)
    category: str = Field(default="general", max_length=64)
    availability_type: Literal["evento", "reservacion", "inventario", "calendario"]
    # v1.3: cualquier tipo registrado (antes era una lista fija: añadir un conector obligaba a
    # tocar también la API).
    connector_type: str = Field(min_length=1, max_length=32)
    config: dict = Field(default_factory=dict)
    interval_s: float = Field(default=30, ge=1, le=86400)
    jitter_pct: float = Field(default=0.1, ge=0, le=0.9)
    rate_limit_per_min: int = Field(default=30, ge=1, le=600)
    max_retries: int = Field(default=2, ge=0, le=10)

    @field_validator("connector_type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        from ..connectors.registry import REGISTRY
        if v not in REGISTRY:
            raise ValueError(f"tipo de conector desconocido; válidos: {sorted(REGISTRY)}")
        return v


class SourcePatch(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    config: dict | None = None
    interval_s: float | None = Field(default=None, ge=1, le=86400)
    jitter_pct: float | None = Field(default=None, ge=0, le=0.9)
    rate_limit_per_min: int | None = Field(default=None, ge=1, le=600)
    max_retries: int | None = Field(default=None, ge=0, le=10)


class Conditions(Strict):
    desde: datetime | None = None
    hasta: datetime | None = None
    vence: datetime | None = None
    fuente: str | None = Field(default=None, max_length=64)
    nombre_contiene: str | None = Field(default=None, max_length=128)
    capacidad_minima: int | None = Field(default=None, ge=0)


class RequestIn(Strict):
    code: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.\-]{2,64}$")
    holder: str = Field(min_length=1, max_length=128)
    region: str = Field(max_length=32)
    resource_type: str = Field(min_length=1, max_length=64)
    priority: int = Field(default=3, ge=1, le=5)
    quantity: int = Field(default=1, ge=1, le=100)
    conditions: Conditions = Field(default_factory=Conditions)


class RequestPatch(Strict):
    priority: int | None = Field(default=None, ge=1, le=5)
    holder: str | None = Field(default=None, min_length=1, max_length=128)


class OperatorIn(Strict):
    name: str = Field(min_length=1, max_length=128)
    regions: list[str] = Field(default_factory=list, max_length=10)
    max_concurrent: int = Field(default=3, ge=1, le=100)
    active: bool = True


class OperatorPatch(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    regions: list[str] | None = Field(default=None, max_length=10)
    max_concurrent: int | None = Field(default=None, ge=1, le=100)
    active: bool | None = None


class ChannelIn(Strict):
    name: str = Field(min_length=1, max_length=64)
    type: Literal["panel", "webhook", "simulado", "registro", "telegram", "correo"]
    config: dict = Field(default_factory=dict)
    enabled: bool = True


class ChannelPatch(Strict):
    config: dict | None = None
    enabled: bool | None = None


class SimItemIn(Strict):
    feed: str = Code
    resource_key: str = Field(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")
    name: str = Field(min_length=1, max_length=256)
    region: str = Field(max_length=32)
    resource_type: str = Field(min_length=1, max_length=64)
    capacity: int = Field(default=5, ge=1, le=100000)
    available: int = Field(default=0, ge=0, le=100000)
    starts_at: datetime | None = None


class UnitsIn(Strict):
    units: int = Field(default=1, ge=1, le=10000)


class RescheduleIn(Strict):
    hours: float = Field(ge=-720, le=720)


class RandomStepIn(Strict):
    steps: int = Field(default=1, ge=1, le=100)
    burst: bool = False


class CompetitionIn(Strict):
    item_id: int
    requests: int = Field(default=10, ge=2, le=500)
    units: int = Field(default=1, ge=1, le=100)


class AcceleratedIn(Strict):
    hours: float = Field(default=12, gt=0, le=168)
    step_s: float = Field(default=30, ge=5, le=3600)
    seed: int = 7
    requests_per_region: int = Field(default=40, ge=1, le=2000)
    error_rate: float = Field(default=0.03, ge=0, le=1)
    notify_fail_rate: float = Field(default=0.1, ge=0, le=1)
    intensity: float = Field(default=1.0, gt=0, le=20)
