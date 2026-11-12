"""Modelo de datos persistente (tablas)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (JSON, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, utcnow


# ---------------------------------------------------------------- seguridad
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16))  # admin | operador
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ApiToken(Base):
    __tablename__ = "api_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256: nunca se guarda el token
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    username: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(128), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[str] = mapped_column(String(64), default="")


# ---------------------------------------------------------------- configuración
class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | list | int | float | str | None] = mapped_column(JSON)


class Region(Base):
    __tablename__ = "regions"
    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))


# ---------------------------------------------------------------- fuentes
class Source(Base):
    __tablename__ = "sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    region: Mapped[str] = mapped_column(ForeignKey("regions.code"))
    category: Mapped[str] = mapped_column(String(64), default="general")
    availability_type: Mapped[str] = mapped_column(String(32))  # evento | reservacion | inventario | calendario
    connector_type: Mapped[str] = mapped_column(String(32))     # simulacion | json | csv | http
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    # Estado operativo: ACTIVA | SUSPENDIDA (manual) | ERROR (requiere revisión manual)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVA")
    interval_s: Mapped[float] = mapped_column(Float, default=30)
    jitter_pct: Mapped[float] = mapped_column(Float, default=0.1)
    rate_limit_per_min: Mapped[int] = mapped_column(Integer, default=30)
    max_retries: Mapped[int] = mapped_column(Integer, default=2)
    # Circuit breaker: CERRADO | ABIERTO | SEMIABIERTO
    circuit_state: Mapped[str] = mapped_column(String(16), default="CERRADO")
    circuit_opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    circuit_open_count: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_result: Mapped[dict] = mapped_column(JSON, default=dict)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Métricas acumuladas
    total_polls: Mapped[int] = mapped_column(Integer, default=0)
    total_errors: Mapped[int] = mapped_column(Integer, default=0)
    total_changes: Mapped[int] = mapped_column(Integer, default=0)
    avg_latency_ms: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SlotState(Base):
    """Último estado conocido de cada recurso observado (para comparar)."""
    __tablename__ = "slot_states"
    __table_args__ = (
        UniqueConstraint("source_id", "resource_key", name="uq_slot_source_key"),
        CheckConstraint("held >= 0", name="ck_slot_held_nonneg"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    resource_key: Mapped[str] = mapped_column(String(128))
    region: Mapped[str] = mapped_column(String(32), index=True)
    resource_type: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(256))
    starts_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    available: Mapped[int] = mapped_column(Integer, default=0)
    held: Mapped[int] = mapped_column(Integer, default=0)  # unidades retenidas por asignaciones activas
    status: Mapped[str] = mapped_column(String(16), default="ACTIVO")  # ACTIVO | CANCELADO | DESAPARECIDO
    fingerprint: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    ever_available: Mapped[bool] = mapped_column(Boolean, default=False)
    missing_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)


class PollLog(Base):
    __tablename__ = "poll_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    duration_ms: Mapped[float] = mapped_column(Float, default=0)
    # OK | ERROR_TEMPORAL | LIMITADO | ERROR | CIRCUITO_ABIERTO
    outcome: Mapped[str] = mapped_column(String(24))
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    items: Mapped[int] = mapped_column(Integer, default=0)
    changes: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    unchanged: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ChangeEvent(Base):
    __tablename__ = "change_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_key: Mapped[str] = mapped_column(String(64), unique=True)  # identificador estable (idempotencia)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    slot_id: Mapped[int | None] = mapped_column(ForeignKey("slot_states.id"), nullable=True)
    region: Mapped[str] = mapped_column(String(32), index=True)
    resource_key: Mapped[str] = mapped_column(String(128))
    resource_type: Mapped[str] = mapped_column(String(64))
    resource_name: Mapped[str] = mapped_column(String(256))
    change_type: Mapped[str] = mapped_column(String(32), index=True)
    before: Mapped[dict] = mapped_column(JSON, default=dict)
    after: Mapped[dict] = mapped_column(JSON, default=dict)
    relevant: Mapped[bool] = mapped_column(Boolean, default=False)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # cuándo cambió en la fuente
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    detection_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Patrón outbox: el evento se guarda en la misma transacción que el estado y
    # luego se procesa (asignación + alerta). Si el proceso muere entre medio,
    # al reiniciar se retoman los eventos con processed=False.
    processed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


# ---------------------------------------------------------------- operadores y solicitudes
class Operator(Base):
    __tablename__ = "operators"
    __table_args__ = (CheckConstraint("current_load >= 0", name="ck_operator_load_nonneg"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    regions: Mapped[list] = mapped_column(JSON, default=list)  # vacío = todas
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    max_concurrent: Mapped[int] = mapped_column(Integer, default=3)
    current_load: Mapped[int] = mapped_column(Integer, default=0)
    last_assigned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Request(Base):
    __tablename__ = "requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    holder: Mapped[str] = mapped_column(String(128))  # titular ficticio del expediente
    region: Mapped[str] = mapped_column(ForeignKey("regions.code"), index=True)
    resource_type: Mapped[str] = mapped_column(String(64))
    priority: Mapped[int] = mapped_column(Integer, default=3)  # 1 = máxima, 5 = mínima
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    conditions: Mapped[dict] = mapped_column(JSON, default=dict)  # condiciones de elegibilidad
    status: Mapped[str] = mapped_column(String(16), default="PENDIENTE", index=True)
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("operators.id"), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


Index("ix_requests_matching", Request.status, Request.region, Request.priority, Request.created_at)


class RequestEvent(Base):
    """Historial de estados de una solicitud."""
    __tablename__ = "request_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    from_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_status: Mapped[str] = mapped_column(String(16))
    actor: Mapped[str] = mapped_column(String(64), default="motor")
    note: Mapped[str] = mapped_column(Text, default="")


class Assignment(Base):
    __tablename__ = "assignments"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"), index=True)
    slot_id: Mapped[int] = mapped_column(ForeignKey("slot_states.id"), index=True)
    operator_id: Mapped[int] = mapped_column(ForeignKey("operators.id"), index=True)
    change_event_id: Mapped[int | None] = mapped_column(ForeignKey("change_events.id"), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    # ACTIVA | EN_PROCESO | COMPLETADA | RECHAZADA | EXPIRADA | CANCELADA
    status: Mapped[str] = mapped_column(String(16), default="ACTIVA", index=True)
    # Columna que solo vale 1 mientras la asignación está viva; con UNIQUE impide
    # dos asignaciones vivas para la misma solicitud aunque fallen otras capas.
    live_request_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")


# ---------------------------------------------------------------- alertas
class Channel(Base):
    __tablename__ = "channels"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    type: Mapped[str] = mapped_column(String(16))  # panel | webhook | simulado | registro | telegram | correo
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    dedup_key: Mapped[str] = mapped_column(String(96), unique=True)
    kind: Mapped[str] = mapped_column(String(16))  # ASIGNACION | DISPONIBILIDAD
    change_event_id: Mapped[int | None] = mapped_column(ForeignKey("change_events.id"), nullable=True)
    assignment_id: Mapped[int | None] = mapped_column(ForeignKey("assignments.id"), nullable=True)
    request_id: Mapped[int | None] = mapped_column(ForeignKey("requests.id"), nullable=True)
    source_code: Mapped[str] = mapped_column(String(64))
    region: Mapped[str] = mapped_column(String(32), index=True)
    resource: Mapped[str] = mapped_column(String(256))
    priority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    operator_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    title: Mapped[str] = mapped_column(String(256))
    # PENDIENTE | ENVIADA | PARCIAL | FALLIDA | CONFIRMADA
    status: Mapped[str] = mapped_column(String(16), default="PENDIENTE", index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Delivery(Base):
    """Bandeja de salida (patrón outbox): un registro por alerta y canal."""
    __tablename__ = "deliveries"
    __table_args__ = (UniqueConstraint("alert_id", "channel_id", name="uq_delivery_alert_channel"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id"), index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"))
    status: Mapped[str] = mapped_column(String(16), default="PENDIENTE", index=True)  # PENDIENTE|ENVIADA|FALLIDA
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)


class NotificationLog(Base):
    __tablename__ = "notification_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    alert_id: Mapped[int] = mapped_column(Integer, index=True)
    channel: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(16))
    detail: Mapped[str] = mapped_column(Text, default="")


# ---------------------------------------------------------------- laboratorio
class SimItem(Base):
    """Mundo simulado: inventario ficticio que leen los conectores de prueba."""
    __tablename__ = "sim_items"
    __table_args__ = (
        UniqueConstraint("feed", "resource_key", name="uq_sim_feed_key"),
        CheckConstraint("available >= 0", name="ck_sim_available_nonneg"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    feed: Mapped[str] = mapped_column(String(64), index=True)  # a qué fuente simulada pertenece
    resource_key: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(256))
    region: Mapped[str] = mapped_column(String(32))
    resource_type: Mapped[str] = mapped_column(String(64))
    starts_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    capacity: Mapped[int] = mapped_column(Integer, default=10)
    available: Mapped[int] = mapped_column(Integer, default=0)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SimRun(Base):
    __tablename__ = "sim_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    results: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="EN_CURSO")
