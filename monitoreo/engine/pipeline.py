"""Orquestación de una consulta: fuente -> detector -> eventos -> asignación -> alertas.

Todas las funciones reciben un reloj (`clock`) para poder ejecutarse en tiempo
real (panel) o en tiempo acelerado (laboratorio, pruebas) con el MISMO código.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select

from ..connectors.base import ConnectorError, RateLimited, TemporaryError
from ..connectors.registry import build_connector
from ..db import Database
from ..models import Alert, Assignment, ChangeEvent, Operator, PollLog, Request, SlotState, Source
from . import alerts as alerts_mod
from . import assignment as asg
from .detector import AGOTADO, CANCELACION, REDUCCION, SUPPLY_TYPES, detect
from .params import get_params
from .resilience import TokenBucket, backoff_delay, circuit_check, jittered_interval

log = logging.getLogger("monitoreo.motor")


@dataclass
class RuntimeState:
    """Estado en memoria del proceso (se reconstruye solo tras un reinicio)."""
    buckets: dict[int, TokenBucket] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def bucket(self, source: Source) -> TokenBucket:
        with self.lock:
            b = self.buckets.get(source.id)
            if b is None or abs(b.rate * 60 - source.rate_limit_per_min) > 1e-9:
                b = TokenBucket(source.rate_limit_per_min)
                self.buckets[source.id] = b
            return b


@dataclass
class PollOutcome:
    outcome: str
    events: int = 0
    relevant_event_ids: list[int] = field(default_factory=list)
    duration_ms: float = 0
    error: str | None = None


def poll_source(db: Database, source_id: int, clock, runtime: RuntimeState, rng: random.Random | None = None,
                context_extra: dict | None = None) -> PollOutcome:
    rng = rng or random.Random()
    now = clock.now()
    with db.session() as s:
        src = s.get(Source, source_id)
        params = get_params(s)
        if src is None or src.status != "ACTIVA":
            return PollOutcome("OMITIDA")
        decision = circuit_check(src.circuit_state, src.circuit_opened_at, params["enfriamiento_circuito_s"], now)
        if not decision.allowed:
            src.next_poll_at = now + timedelta(seconds=decision.wait_s)
            return PollOutcome("CIRCUITO_ABIERTO")
        allowed, wait = runtime.bucket(src).try_acquire(now)
        if not allowed:
            src.next_poll_at = now + timedelta(seconds=wait)
            return PollOutcome("LIMITE_LOCAL")
        if decision.state == "SEMIABIERTO":
            src.circuit_state = "SEMIABIERTO"
        ctype, cfg, max_retries, code = src.connector_type, dict(src.config or {}), src.max_retries, src.code

    # ---- consulta a la fuente (fuera de transacción: puede tardar)
    ctx = {"db": db, "rng": rng, "virtual_time": getattr(clock, "virtual", False), **(context_extra or {})}
    t0 = time.perf_counter()
    attempts, snapshot, err, outcome, retry_after = 0, None, None, "OK", None
    try:
        connector = build_connector(ctype, cfg, ctx)
        while True:
            attempts += 1
            try:
                snapshot = connector.fetch()
                break
            except TemporaryError as e:
                if attempts > max_retries:
                    raise
                clock.sleep(backoff_delay(attempts, 0.2, 2.0, rng))  # reintento corto dentro de la consulta
                log.debug("reintento %s en %s: %s", attempts, code, e)
    except RateLimited as e:
        outcome, err, retry_after = "LIMITADO", str(e), e.retry_after
    except TemporaryError as e:
        outcome, err = "ERROR_TEMPORAL", str(e)
    except ConnectorError as e:
        outcome, err = "ERROR", str(e)
    except Exception as e:  # nunca dejar que un conector tumbe el motor
        outcome, err = "ERROR", f"{e.__class__.__name__}: {e}"
        log.exception("fallo inesperado en %s", code)
    duration_ms = (time.perf_counter() - t0) * 1000
    now = clock.now()

    # ---- persistencia atómica: estado + eventos + métricas en UNA transacción
    with db.session() as s:
        src = s.get(Source, source_id)
        params = get_params(s)
        src.last_polled_at = now
        src.total_polls += 1
        src.avg_latency_ms = src.avg_latency_ms + (duration_ms - src.avg_latency_ms) / src.total_polls
        log_row = PollLog(source_id=src.id, started_at=now, duration_ms=duration_ms, outcome=outcome,
                          attempts=attempts, error=err)
        result = PollOutcome(outcome, duration_ms=duration_ms, error=err)
        if outcome == "OK":
            det = detect(s, src, snapshot, now, params)
            log_row.items, log_row.changes = det.items, len(det.events)
            log_row.duplicates, log_row.unchanged = det.duplicates, det.unchanged
            src.total_changes += len(det.events)
            src.last_success_at, src.last_error, src.consecutive_failures = now, None, 0
            src.circuit_state, src.circuit_opened_at, src.circuit_open_count = "CERRADO", None, 0
            src.last_result = {"items": det.items, "cambios": len(det.events), "duplicados": det.duplicates,
                               "sin_cambio": det.unchanged, "ms": round(duration_ms, 2)}
            src.next_poll_at = now + timedelta(seconds=jittered_interval(src.interval_s, src.jitter_pct, rng))
            s.flush()
            result.events = len(det.events)
            result.relevant_event_ids = [e.id for e in det.events]
        else:
            src.total_errors += 1
            src.last_error = err
            src.last_result = {"resultado": outcome, "error": err}
            if outcome == "LIMITADO":
                # Respeto de la política del proveedor: esperar lo que pida (o el doble del intervalo).
                wait = max(retry_after or 0, src.interval_s * 2)
                src.next_poll_at = now + timedelta(seconds=wait)
            else:
                src.consecutive_failures += 1
                wait = backoff_delay(src.consecutive_failures, src.interval_s, params["backoff_max_s"], rng)
                src.next_poll_at = now + timedelta(seconds=max(wait, src.interval_s))
                if src.circuit_state == "SEMIABIERTO" or src.consecutive_failures >= params["umbral_circuito"]:
                    src.circuit_state, src.circuit_opened_at = "ABIERTO", now
                    src.circuit_open_count += 1
                    src.next_poll_at = now + timedelta(seconds=params["enfriamiento_circuito_s"])
                    if src.circuit_open_count >= params["aperturas_hasta_error"]:
                        src.status = "ERROR"  # pausa automática definitiva: requiere revisión manual
                if outcome == "ERROR" and "acceso denegado" in (err or ""):
                    src.status = "ERROR"  # 401/403: no insistir
        s.add(log_row)
    return result


# ---------------------------------------------------------------- procesamiento de eventos
def process_pending_events(db: Database, clock, limit: int = 500) -> dict:
    """Procesa la bandeja de eventos: asignación automática + alertas. Idempotente."""
    stats = {"eventos": 0, "asignaciones": 0, "alertas": 0}
    with db.session() as s:
        ids = list(s.scalars(select(ChangeEvent.id).where(ChangeEvent.processed.is_(False))
                             .order_by(ChangeEvent.id).limit(limit)))
        params = get_params(s)
    for eid in ids:
        now = clock.now()
        with db.session() as s:
            ev = s.get(ChangeEvent, eid)
            if ev.processed:
                continue
            ctype, slot_id, relevant = ev.change_type, ev.slot_id, ev.relevant
        # 1) coherencia: si bajaron las unidades, anular retenciones no tomadas
        if slot_id and ctype in (AGOTADO, REDUCCION, CANCELACION):
            asg.invalidate_overheld(db, slot_id, now, params)
        # 2) asignación automática si hay oferta nueva
        created: list[int] = []
        if slot_id and ctype in SUPPLY_TYPES:
            created = asg.allocate_slot(db, slot_id, now, params, change_event_id=eid)
        # 3) alertas + marcar procesado (misma transacción: si falla, se reintenta todo el evento)
        with db.session() as s:
            ev = s.get(ChangeEvent, eid)
            src = s.get(Source, ev.source_id)
            n_alerts = 0
            for aid in created:
                n_alerts += _assignment_alert(s, aid, now) is not None
            # Idempotencia: si este evento ya generó alguna alerta (p. ej. se reprocesa tras
            # un reinicio), no se crea una alerta de disponibilidad adicional.
            already = s.scalar(select(Alert.id).where(Alert.change_event_id == eid).limit(1)) is not None
            # Si la oferta nueva ya la repartió otra pasada (p. ej. la reevaluación periódica),
            # las alertas de ASIGNACION ya informan: no se emite un aviso engañoso de "sin solicitud".
            slot = s.get(SlotState, slot_id) if slot_id else None
            consumed = (ctype in SUPPLY_TYPES and slot is not None and slot.available - slot.held <= 0
                        and slot.held > 0)
            if relevant and not created and not already and not consumed:
                title = f"{ev.change_type.replace('_', ' ').title()}: {ev.resource_name}"
                if ctype in SUPPLY_TYPES:
                    title += " (sin solicitud compatible asignable)"
                a = alerts_mod.create_alert(
                    s, dedup_key=f"evt:{ev.event_key}", kind="DISPONIBILIDAD", title=title, source_code=src.code,
                    region=ev.region, resource=ev.resource_name, now=now, change_event_id=ev.id,
                    payload={"cambio": ev.change_type, "antes": ev.before, "despues": ev.after})
                n_alerts += a is not None
            ev.processed = True
        stats["eventos"] += 1
        stats["asignaciones"] += len(created)
        stats["alertas"] += n_alerts
    return stats


def _assignment_alert(s, assignment_id: int, now):
    a = s.get(Assignment, assignment_id)
    req = s.get(Request, a.request_id)
    slot = s.get(SlotState, a.slot_id)
    op = s.get(Operator, a.operator_id)
    src = s.get(Source, slot.source_id)
    return alerts_mod.create_alert(
        s, dedup_key=f"asg:{a.id}", kind="ASIGNACION", title=f"{req.code} asignada a {op.name}: {slot.name}",
        source_code=src.code, region=slot.region, resource=slot.name, now=now, change_event_id=a.change_event_id,
        assignment_id=a.id, request_id=req.id, priority=req.priority, operator_name=op.name,
        payload={"solicitud": req.code, "titular": req.holder, "cantidad": a.quantity,
                 "inicio": slot.starts_at.isoformat() if slot.starts_at else None,
                 "vence_asignacion": a.expires_at.isoformat() + "Z", "estado_solicitud": req.status})


def maintenance(db: Database, clock) -> dict:
    """Tareas periódicas: expiraciones, reevaluación de recursos libres, alertas de reasignación."""
    now = clock.now()
    with db.session() as s:
        params = get_params(s)
        pending = list(s.scalars(select(Request).where(Request.status == "PENDIENTE")))
        for r in pending:
            asg.validate_request(s, r, now)
    expired = asg.expire_assignments(db, now, params)
    vencidas = asg.expire_requests(db, now)
    created = asg.rebalance(db, now, params)
    n_alerts = 0
    with db.session() as s:
        for aid in created:
            n_alerts += _assignment_alert(s, aid, now) is not None
    return {"validadas": len(pending), "expiradas": expired, "vencidas": vencidas,
            "reasignadas": len(created), "alertas": n_alerts}


def due_sources(db: Database, now) -> list[int]:
    with db.session() as s:
        return list(s.scalars(select(Source.id).where(
            Source.status == "ACTIVA", (Source.next_poll_at.is_(None)) | (Source.next_poll_at <= now))))
