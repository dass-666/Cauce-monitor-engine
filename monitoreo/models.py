"""Modelo de datos persistente (tablas)."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (JSON, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer,
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
    # v1.1: diagnóstico. Por qué está en su estado actual y desde cuándo.
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_outcome: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # v1.3: estado opaco del conector (p. ej. ETag/versión) que se le devuelve en cada consulta.
    connector_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)


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
    # OK | ERROR_TEMPORAL | LIMITADO | ERROR | CIRCUITO_ABIERTO | OBSOLETA (v1.3)
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
    # v1.3: cuándo terminó su procesamiento (asignación + alertas); mide el tramo "procesamiento".
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ---------------------------------------------------------------- operadores y solicitudes
class Operator(Base):
    __tablename__ = "operators"
    __table_args__ = (CheckConstraint("current_load >= 0", name="ck_operator_load_nonneg"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    regions: Mapped[list] = mapped_column(JSON, default=list)  # vacío = todas
    sucursales: Mapped[list] = mapped_column(JSON, default=list)  # v1.6: vacío = todas las de sus regiones
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    max_concurrent: Mapped[int] = mapped_column(Integer, default=3)
    current_load: Mapped[int] = mapped_column(Integer, default=0)
    last_assigned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    visto_en: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)   # v1.7: presencia en el panel


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
Index("ix_change_events_purge", ChangeEvent.relevant, ChangeEvent.processed, ChangeEvent.detected_at)


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
    # v1.4: quién tomó la asignación y cuándo hubo actividad por última vez (para detectar
    # asignaciones EN_PROCESO abandonadas sin liberarlas automáticamente).
    taken_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    taken_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    inactive_alerted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    explicacion: Mapped[dict | None] = mapped_column(JSON, nullable=True)   # v1.7: por qué este operador


class Operation(Base):
    """v1.4: resultado guardado de una operación de un usuario identificada por su clave
    (cabecera `Idempotency-Key`). Se escribe en la MISMA transacción que la operación: si existe,
    la operación se hizo. Repetir la petición devuelve el mismo resultado sin repetir el efecto, y
    `GET /api/operaciones/{clave}` permite saber qué pasó tras perder la conexión."""
    __tablename__ = "operations"
    __table_args__ = (UniqueConstraint("username", "op_key", name="uq_operations_user_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64))
    op_key: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(32))
    target: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(Integer)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


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
    # v1.7: centro de notificaciones
    categoria: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    nivel: Mapped[str | None] = mapped_column(String(8), nullable=True)          # CRITICA | ALTA | MEDIA | BAJA
    grupo: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    repeticiones: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    ultima_en: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


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
    # v1.1: progreso por destinatario (p. ej. chats de Telegram ya entregados) para no reenviar
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # v1.2: testigo del reclamo vigente. Solo quien lo posee puede registrar el resultado.
    claim_token: Mapped[str | None] = mapped_column(String(32), nullable=True)


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


# ---------------------------------------------------------------- v1.1: estadísticas, coordinación, mantenimiento
class PollStatHourly(Base):
    """Agregado horario de consultas (sobrevive a la limpieza de poll_log)."""
    __tablename__ = "poll_stats_hourly"
    __table_args__ = (UniqueConstraint("source_id", "hour", name="uq_pstat_h_source_hour"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    region: Mapped[str] = mapped_column(String(32), index=True)
    hour: Mapped[datetime] = mapped_column(DateTime, index=True)
    polls: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    rate_limited: Mapped[int] = mapped_column(Integer, default=0)
    items: Mapped[int] = mapped_column(Integer, default=0)
    changes: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    sum_ms: Mapped[float] = mapped_column(Float, default=0)
    max_ms: Mapped[float] = mapped_column(Float, default=0)


class PollStatDaily(Base):
    __tablename__ = "poll_stats_daily"
    __table_args__ = (UniqueConstraint("source_id", "day", name="uq_pstat_d_source_day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    region: Mapped[str] = mapped_column(String(32), index=True)
    day: Mapped[datetime] = mapped_column(DateTime, index=True)
    polls: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    rate_limited: Mapped[int] = mapped_column(Integer, default=0)
    items: Mapped[int] = mapped_column(Integer, default=0)
    changes: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    sum_ms: Mapped[float] = mapped_column(Float, default=0)
    max_ms: Mapped[float] = mapped_column(Float, default=0)


class SchedulerLease(Base):
    """Arrendamiento ("lease") del planificador: solo la instancia que lo tiene vigente trabaja."""
    __tablename__ = "scheduler_leases"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    holder: Mapped[str] = mapped_column(String(128))
    acquired_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    renewed_at: Mapped[datetime] = mapped_column(DateTime)
    fencing_token: Mapped[int] = mapped_column(Integer, default=0)


class MaintenanceLog(Base):
    __tablename__ = "maintenance_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    task: Mapped[str] = mapped_column(String(32), index=True)
    holder: Mapped[str] = mapped_column(String(128), default="")
    duration_ms: Mapped[float] = mapped_column(Float, default=0)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


# ---------------------------------------------------------------- v1.5: captura y reservaciones (laboratorio)
class Expediente(Base):
    """Expediente de un titular FICTICIO. Las confirmaciones de teléfono y correo solo existen en
    la validación de entrada: aquí no se guardan copias. Los datos personales nunca salen en
    listados sin enmascarar ni en auditoría, alertas o eventos."""
    __tablename__ = "expedientes"
    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(32), unique=True)
    id_externo: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    id_registro_previo: Mapped[str | None] = mapped_column(String(32), nullable=True)
    nombre: Mapped[str] = mapped_column(String(128))
    # v1.6: un expediente puede crearse INCOMPLETO (preparación anticipada): estos tres pueden faltar
    fecha_nacimiento: Mapped[date | None] = mapped_column(Date, nullable=True)
    telefono: Mapped[str | None] = mapped_column(String(16), nullable=True)
    correo: Mapped[str | None] = mapped_column(String(128), nullable=True)
    region: Mapped[str] = mapped_column(ForeignKey("regions.code"), index=True)
    sucursal: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tipo_tramite: Mapped[str] = mapped_column(String(32))
    operacion: Mapped[str] = mapped_column(String(16), default="nueva")
    # v1.6: NUEVO | INCOMPLETO | EN_VALIDACION | LISTO | ASIGNADO | CAPTURA_EN_PROCESO |
    # RESERVACION_PENDIENTE | RESERVACION_CONFIRMADA | REQUIERE_REVISION | CERRADO
    estado: Mapped[str] = mapped_column(String(24), default="NUEVO", index=True)
    motivo_cierre: Mapped[str | None] = mapped_column(String(24), nullable=True)
    folio_anterior: Mapped[str | None] = mapped_column(String(32), nullable=True)   # solo al reprogramar
    modo: Mapped[str] = mapped_column(String(12), default="POR_DIA")       # POR_DIA | POR_HORARIO
    sucursales_alternativas: Mapped[list] = mapped_column(JSON, default=list)
    ronda: Mapped[int] = mapped_column(Integer, default=0)                 # solicitudes del motor creadas
    validado_en: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    anonimizado_en: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    prioridad: Mapped[int] = mapped_column(Integer, default=3)
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("operators.id"), nullable=True, index=True)
    request_id: Mapped[int | None] = mapped_column(ForeignKey("requests.id"), nullable=True, unique=True)
    # Huella (sha256) de nombre+fecha+trámite normalizados; UNIQUE mientras el expediente está
    # activo (NULL al cerrarse): impide expedientes duplicados aunque lleguen a la vez.
    dedup_activo: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    creado_por: Mapped[str] = mapped_column(String(64), default="")
    ultimo_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ConexionEvento(Base):
    """v2.0: cambios de conexión, caídas, recuperaciones, RESTRICCIONES del proveedor y el uso de
    cada operación con el portal (qué conexión, qué resultado). Sin datos de personas."""
    __tablename__ = "conexion_eventos"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    evento: Mapped[str] = mapped_column(String(16), index=True)      # CAMBIO|CAIDA|RECUPERADA|RESTRICCION|USO
    conexion: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tipo: Mapped[str | None] = mapped_column(String(16), nullable=True)
    operacion: Mapped[str | None] = mapped_column(String(24), nullable=True)
    detalle: Mapped[dict] = mapped_column(JSON, default=dict)


class Atencion(Base):
    """v2.0 · Atención PRESENCIAL (modo ventanilla): una persona frente al operador, de principio a fin.

    PENDIENTE (iniciada, sin expediente) -> EN_PROCESO (expediente tomado) -> CONFIRMADA | SIN_DISPONIBILIDAD
    | ERROR | CANCELADA. Si al cerrar hay una confirmación sin resultado claro, queda PENDIENTE con
    `resultado = VERIFICACION` y el motor la resuelve contra el portal. CONFIRMADA solo si la
    reservación vinculada está CONFIRMADA (con evidencia del portal): nunca por una solicitud o simulación.
    - `clave` (única por usuario) hace idempotente el inicio: repetir la petición devuelve la misma atención.
    - `viva_expediente_id` (UNIQUE) impide dos atenciones abiertas sobre el mismo expediente.
    No guarda datos personales: solo identificadores, estados y tiempos."""
    __tablename__ = "atenciones"
    __table_args__ = (UniqueConstraint("username", "clave", name="uq_atenciones_usuario_clave"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(32), unique=True)
    clave: Mapped[str] = mapped_column(String(64))
    username: Mapped[str] = mapped_column(String(64))
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("operators.id"), nullable=True, index=True)
    expediente_id: Mapped[int | None] = mapped_column(ForeignKey("expedientes.id"), nullable=True, index=True)
    viva_expediente_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    reservacion_id: Mapped[int | None] = mapped_column(ForeignKey("reservaciones.id"), nullable=True)
    estado: Mapped[str] = mapped_column(String(20), default="PENDIENTE", index=True)
    resultado: Mapped[str | None] = mapped_column(String(32), nullable=True)      # motivo del cierre
    folio: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sucursal: Mapped[str | None] = mapped_column(String(32), nullable=True)
    region: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    conexion: Mapped[str | None] = mapped_column(String(64), nullable=True)       # v2.0 P1: red usada
    pasos: Mapped[int] = mapped_column(Integer, default=0)
    iniciada_en: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    cerrada_en: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ExpedienteEvento(Base):
    """Historial y trazabilidad de un expediente: modificaciones (solo NOMBRES de campos, nunca
    valores) y eventos normalizados (DISPONIBILIDAD_DETECTADA, RESERVACION_CONFIRMADA...)."""
    __tablename__ = "expediente_eventos"
    id: Mapped[int] = mapped_column(primary_key=True)
    expediente_id: Mapped[int] = mapped_column(ForeignKey("expedientes.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    tipo: Mapped[str] = mapped_column(String(32), index=True)
    actor: Mapped[str] = mapped_column(String(64), default="motor")
    assignment_id: Mapped[int | None] = mapped_column(ForeignKey("assignments.id"), nullable=True)
    reservacion_id: Mapped[int | None] = mapped_column(ForeignKey("reservaciones.id"), nullable=True)
    change_event_id: Mapped[int | None] = mapped_column(ForeignKey("change_events.id"), nullable=True)
    detalle: Mapped[dict] = mapped_column(JSON, default=dict)


class Reservacion(Base):
    """Intento de reservación de un expediente en el portal de PRUEBA.

    PREPARADA -> DATOS_ENVIADOS -> RETENIDA -> CONFIRMANDO -> CONFIRMADA
    y en cualquier punto vivo: RECHAZADA | EXPIRADA | CANCELADA.
    CONFIRMADA solo se alcanza con evidencia: el folio leído de la API del portal con la misma
    clave de confirmación. `viva_expediente_id` (UNIQUE) impide dos intentos vivos por expediente."""
    __tablename__ = "reservaciones"
    id: Mapped[int] = mapped_column(primary_key=True)
    expediente_id: Mapped[int] = mapped_column(ForeignKey("expedientes.id"), index=True)
    viva_expediente_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    assignment_id: Mapped[int | None] = mapped_column(ForeignKey("assignments.id"), nullable=True)
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("operators.id"), nullable=True, index=True)
    estado: Mapped[str] = mapped_column(String(16), default="PREPARADA", index=True)
    contrato: Mapped[str] = mapped_column(String(64), default="portal_laboratorio")
    modo: Mapped[str] = mapped_column(String(12), default="POR_HORARIO")   # v1.6
    sesion_portal: Mapped[str | None] = mapped_column(String(128), nullable=True)
    horario_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sucursal: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fecha: Mapped[str | None] = mapped_column(String(10), nullable=True)
    hora: Mapped[str | None] = mapped_column(String(5), nullable=True)
    clave: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    folio: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    vence_en: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confirmando_desde: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    evidencia: Mapped[dict] = mapped_column(JSON, default=dict)
    error_codigo: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    intentos_confirmacion: Mapped[int] = mapped_column(Integer, default=0)
    confirmada_por: Mapped[str | None] = mapped_column(String(64), nullable=True)
    conexion: Mapped[str | None] = mapped_column(String(64), nullable=True)    # v2.0: conexión de red de su sesión
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ---------------------------------------------------------------- v1.6: calendario POR DÍA
class CalendarioDia(Base):
    """Último estado CONOCIDO de un día (sucursal, trámite, fecha) de una fuente de calendario.
    `version_portal` permite descartar actualizaciones que llegan fuera de orden; `verificado_en`
    es cuándo el portal lo confirmó por última vez (no cuándo lo miró el motor)."""
    __tablename__ = "calendario_dias"
    __table_args__ = (UniqueConstraint("source_id", "clave", name="uq_calendario_dia"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    clave: Mapped[str] = mapped_column(String(96))
    region: Mapped[str] = mapped_column(String(32), index=True)
    sucursal: Mapped[str] = mapped_column(String(32), index=True)
    sucursal_nombre: Mapped[str] = mapped_column(String(128), default="")
    tramite: Mapped[str] = mapped_column(String(32))
    fecha: Mapped[str] = mapped_column(String(10), index=True)
    estado: Mapped[str] = mapped_column(String(16))      # DISPONIBLE | AGOTADO | DESHABILITADO | NO_HABILITADO
    libres: Mapped[int] = mapped_column(Integer, default=0)
    capacidad: Mapped[int] = mapped_column(Integer, default=0)
    version_portal: Mapped[int] = mapped_column(Integer, default=0)
    disponible_hasta: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verificado_en: Mapped[datetime] = mapped_column(DateTime)
    cambiado_en: Mapped[datetime] = mapped_column(DateTime)
    ultima_causa: Mapped[str | None] = mapped_column(String(32), nullable=True)


class CalendarioCambio(Base):
    """Cambio detectado en el calendario (o en su conexión). Sin datos personales."""
    __tablename__ = "calendario_cambios"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    tipo: Mapped[str] = mapped_column(String(32), index=True)
    clave: Mapped[str | None] = mapped_column(String(96), nullable=True)
    sucursal: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tramite: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fecha: Mapped[str | None] = mapped_column(String(10), nullable=True)
    antes: Mapped[str | None] = mapped_column(String(16), nullable=True)
    despues: Mapped[str | None] = mapped_column(String(16), nullable=True)
    libres: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version_portal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mecanismo: Mapped[str] = mapped_column(String(12), default="PERIODICO")   # EVENTOS | PERIODICO
    ts_portal: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    detectado_en: Mapped[datetime] = mapped_column(DateTime, index=True)
    detalle: Mapped[dict] = mapped_column(JSON, default=dict)


class CalendarioFuente(Base):
    """Estado de la conexión con el calendario de una fuente: cuándo se verificó de verdad."""
    __tablename__ = "calendario_fuentes"
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), primary_key=True)
    mecanismo: Mapped[str] = mapped_column(String(12), default="PERIODICO")
    conectado: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmado_en: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)   # todo el calendario al día
    ultimo_intento_en: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ultimo_error_en: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ultimo_error_codigo: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ultimo_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    ultima_secuencia: Mapped[int] = mapped_column(Integer, default=0)
    eventos_recibidos: Mapped[int] = mapped_column(Integer, default=0)
    fuera_de_orden: Mapped[int] = mapped_column(Integer, default=0)
    resincronizaciones: Mapped[int] = mapped_column(Integer, default=0)
    reconexiones: Mapped[int] = mapped_column(Integer, default=0)
    zona_horaria: Mapped[str | None] = mapped_column(String(64), nullable=True)   # la que declara el portal
