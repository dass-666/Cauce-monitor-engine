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

from sqlalchemy import func, select

from ..connectors.base import ConnectorError, RateLimited, StaleResponse, TemporaryError
from ..connectors.registry import build_connector
from ..db import Database
from ..models import Alert, Assignment, AuditLog, ChangeEvent, Operator, PollLog, Request, SlotState, Source
from . import alerts as alerts_mod
from . import assignment as asg
from .detector import AGOTADO, CANCELACION, REDUCCION, SUPPLY_TYPES, detect
from .params import get_params
from .resilience import TokenBucket, backoff_delay, circuit_check, circuit_cooldown, jittered_interval

log = logging.getLogger("monitoreo.motor")


@dataclass
class RuntimeState:
    """Estado en memoria del proceso (se reconstruye solo tras un reinicio)."""
    buckets: dict[int, TokenBucket] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def bucket(self, source: Source, now=None) -> TokenBucket:
        with self.lock:
            b = self.buckets.get(source.id)
            if b is None or abs(b.rate * 60 - source.rate_limit_per_min) > 1e-9:
                b = TokenBucket(source.rate_limit_per_min)
                # v1.1: tras un reinicio la cubeta NO arranca llena si la fuente se consultó hace
                # poco; así un reinicio rápido (o varios) no provoca una ráfaga sobre el proveedor.
                if now is not None and source.last_polled_at is not None:
                    elapsed = max(0.0, (now - source.last_polled_at).total_seconds())
                    b.tokens = min(b.capacity, elapsed * b.rate)
                    b.updated = now
                self.buckets[source.id] = b
            return b


@dataclass
class PollOutcome:
    outcome: str
    events: int = 0
    relevant_event_ids: list[int] = field(default_factory=list)
    duration_ms: float = 0
    error: str | None = None


def _set_status(src: Source, status: str, reason: str, now) -> None:
    if src.status != status:
        src.status, src.status_changed_at = status, now
    src.status_reason = reason


def _system_alert(s, src: Source, what: str, detail: str, now) -> None:
    """Aviso de fallo crítico (canal Telegram/webhook/panel). Deduplicado por episodio."""
    stamp = (src.circuit_opened_at or now).isoformat()
    alerts_mod.create_alert(
        s, dedup_key=f"sys:{src.id}:{what}:{stamp}", kind="SISTEMA", title=f"{src.code}: {detail}",
        source_code=src.code, region=src.region, resource=src.name, now=now,
        payload={"evento_sistema": what, "ultimo_error": (src.last_error or "")[:200],
                 "fallos_consecutivos": src.consecutive_failures})


def _close_circuit(src: Source, params: dict) -> None:
    """Cierra el circuito tras una respuesta correcta. v1.4 (modo adaptable): el contador de
    aperturas baja de uno en uno, así una fuente que acaba de recuperarse y vuelve a fallar no
    empieza desde el enfriamiento más corto (restablecimiento gradual)."""
    src.circuit_state, src.circuit_opened_at = "CERRADO", None
    if params.get("enfriamiento_modo", "adaptable") == "fijo":
        src.circuit_open_count = 0
    else:
        src.circuit_open_count = max(0, (src.circuit_open_count or 0) - 1)


def _should_stop(src: Source, params: dict, now) -> bool:
    """¿Pasar la fuente a ERROR (revisión manual)? Modo fijo: tras N aperturas (como v1.0–v1.3).
    Modo adaptable: además debe llevar `minutos_sin_exito_hasta_error` sin una consulta correcta;
    los enfriamientos cortos no deben convertir un corte de 2 minutos en una parada manual."""
    if src.circuit_open_count < params["aperturas_hasta_error"]:
        return False
    if params.get("enfriamiento_modo", "adaptable") == "fijo" or src.last_success_at is None:
        return True
    return (now - src.last_success_at).total_seconds() >= 60 * float(params.get("minutos_sin_exito_hasta_error", 15))


def poll_source(db: Database, source_id: int, clock, runtime: RuntimeState, rng: random.Random | None = None,
                context_extra: dict | None = None, guard=None) -> PollOutcome:
    """`guard`: función opcional que devuelve False si esta instancia perdió el liderazgo;
    en ese caso el resultado de la consulta no se guarda (lo hará el nuevo líder)."""
    rng = rng or random.Random()
    now = clock.now()
    with db.session() as s:
        src = s.get(Source, source_id)
        params = get_params(s)
        if src is None or src.status != "ACTIVA":
            return PollOutcome("OMITIDA")
        cooldown = circuit_cooldown(max(1, src.circuit_open_count or 0), src.interval_s, params)
        decision = circuit_check(src.circuit_state, src.circuit_opened_at, cooldown, now)
        if not decision.allowed:
            src.next_poll_at = now + timedelta(seconds=decision.wait_s)
            src.last_outcome = "CIRCUITO_ABIERTO"
            return PollOutcome("CIRCUITO_ABIERTO")
        allowed, wait = runtime.bucket(src, now).try_acquire(now)
        if not allowed:
            src.next_poll_at = now + timedelta(seconds=wait)
            src.last_outcome = "LIMITE_LOCAL"
            return PollOutcome("LIMITE_LOCAL")
        if decision.state == "SEMIABIERTO":
            # v1.4: UNA sola consulta de prueba. Se anota cuándo empezó y se aparta la siguiente
            # consulta: si otro hilo o un reinicio llegan antes de que termine, no lanzan otra.
            src.circuit_state, src.circuit_opened_at = "SEMIABIERTO", now
            src.next_poll_at = now + timedelta(seconds=cooldown)
        ctype, cfg, max_retries, code = src.connector_type, dict(src.config or {}), src.max_retries, src.code
        cstate = dict(src.connector_state or {})
        # v1.3: si el motor modificó localmente algún recurso de esta fuente (consumo optimista al
        # completar), se pide la lista completa: una respuesta condicional "sin cambios" (304)
        # impediría corregir ese valor local con lo que publica la fuente.
        refresh = s.scalar(select(SlotState.id).where(SlotState.source_id == source_id,
                                                      SlotState.fingerprint == "").limit(1)) is not None

    # ---- consulta a la fuente (fuera de transacción: puede tardar)
    ctx = {"db": db, "rng": rng, "virtual_time": getattr(clock, "virtual", False), "state": cstate,
           "refresh": refresh, "source_id": source_id, "now": now, **(context_extra or {})}
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
    except StaleResponse as e:
        outcome, err = "OBSOLETA", str(e)
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
    if guard is not None and not guard():
        return PollOutcome("SIN_LIDERAZGO", duration_ms=duration_ms)
    with db.session() as s:
        src = s.get(Source, source_id)
        src.last_outcome = outcome
        params = get_params(s)
        src.last_polled_at = now
        src.total_polls += 1
        src.avg_latency_ms = src.avg_latency_ms + (duration_ms - src.avg_latency_ms) / src.total_polls
        log_row = PollLog(source_id=src.id, started_at=now, duration_ms=duration_ms, outcome=outcome,
                          attempts=attempts, error=err)
        result = PollOutcome(outcome, duration_ms=duration_ms, error=err)
        if outcome == "OK":
            if snapshot.not_modified:
                # v1.3: la fuente confirmó que nada cambió (p. ej. HTTP 304): no hay nada que comparar.
                src.last_result = {"items": 0, "cambios": 0, "sin_modificar": True, "ms": round(duration_ms, 2)}
                events = []
            else:
                det = detect(s, src, snapshot, now, params)
                log_row.items, log_row.changes = det.items, len(det.events)
                log_row.duplicates, log_row.unchanged = det.duplicates, det.unchanged
                src.last_result = {"items": det.items, "cambios": len(det.events), "duplicados": det.duplicates,
                                   "sin_cambio": det.unchanged, "ms": round(duration_ms, 2)}
                if not snapshot.complete:
                    src.last_result["incompleta"] = True
                events = det.events
            if snapshot.meta:
                src.last_result["conector"] = _compact_meta(snapshot.meta)
            if snapshot.state is not None:
                src.connector_state = snapshot.state
            src.total_changes += len(events)
            src.last_success_at, src.last_error, src.consecutive_failures = now, None, 0
            _close_circuit(src, params)
            src.status_reason = None
            src.next_poll_at = now + timedelta(seconds=jittered_interval(src.interval_s, src.jitter_pct, rng))
            s.flush()
            result.events = len(events)
            result.relevant_event_ids = [e.id for e in events]
        elif outcome == "OBSOLETA":
            # v1.3: la fuente respondió, pero con datos más antiguos que los ya procesados. Se descarta
            # sin compararla (no genera cambios falsos) y no cuenta como fallo de la fuente.
            src.last_error = err
            src.last_result = {"resultado": outcome, "error": err, "ms": round(duration_ms, 2)}
            src.consecutive_failures = 0
            if src.circuit_state != "CERRADO":
                _close_circuit(src, params)      # la fuente responde: la conexión está sana
            src.next_poll_at = now + timedelta(seconds=jittered_interval(src.interval_s, src.jitter_pct, rng))
        else:
            src.total_errors += 1
            src.last_error = err
            src.last_result = {"resultado": outcome, "error": err}
            if outcome == "LIMITADO":
                # Respeto de la política del proveedor: esperar lo que pida (o el doble del intervalo).
                wait = max(retry_after or 0, src.interval_s * 2)
                src.next_poll_at = now + timedelta(seconds=wait)
                src.status_reason = f"la fuente pidió esperar; próxima consulta en {wait:.0f} s"
            else:
                src.consecutive_failures += 1
                wait = backoff_delay(src.consecutive_failures, src.interval_s, params["backoff_max_s"], rng)
                src.next_poll_at = now + timedelta(seconds=max(wait, src.interval_s))
                if src.circuit_state == "SEMIABIERTO" or src.consecutive_failures >= params["umbral_circuito"]:
                    src.circuit_state, src.circuit_opened_at = "ABIERTO", now
                    src.circuit_open_count += 1
                    cooldown = circuit_cooldown(src.circuit_open_count, src.interval_s, params)
                    src.next_poll_at = now + timedelta(seconds=cooldown)
                    src.status_reason = (f"circuito abierto tras {src.consecutive_failures} fallos seguidos "
                                         f"(apertura {src.circuit_open_count}); prueba en {cooldown:g} s")
                    if _should_stop(src, params, now):
                        # pausa automática definitiva: requiere revisión manual
                        _set_status(src, "ERROR", f"circuito abierto {src.circuit_open_count} veces seguidas; "
                                    "requiere revisión manual (Reanudar)", now)
                        _system_alert(s, src, "error", "fuente detenida por errores repetidos", now)
                    elif src.circuit_open_count == 1:
                        _system_alert(s, src, "circuito", "circuito abierto por fallos consecutivos", now)
                if outcome == "ERROR" and "acceso denegado" in (err or ""):
                    _set_status(src, "ERROR", "la fuente respondió acceso denegado (401/403); no se insiste", now)
                    _system_alert(s, src, "acceso", "acceso denegado por la fuente", now)
        s.add(log_row)
    return result


# ---------------------------------------------------------------- procesamiento de eventos
def process_pending_events(db: Database, clock, limit: int = 500, guard=None) -> dict:
    """Procesa la bandeja de eventos: asignación automática + alertas. Idempotente."""
    stats = {"eventos": 0, "asignaciones": 0, "alertas": 0}
    with db.session() as s:
        ids = list(s.scalars(select(ChangeEvent.id).where(ChangeEvent.processed.is_(False))
                             .order_by(ChangeEvent.id).limit(limit)))
        params = get_params(s)
    for eid in ids:
        if guard is not None and not guard():
            break
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
            # v1.3: también las asignaciones que este evento creó en un intento anterior que falló
            # antes de avisar (la asignación se confirma en su propia transacción). Sin esto, un
            # fallo de la BD en ese instante dejaba una asignación viva de la que nadie se enteraba.
            # La alerta se deduplica por asignación, así que nunca se avisa dos veces.
            earlier = s.scalars(select(Assignment.id).where(Assignment.change_event_id == eid,
                                                            Assignment.status == "ACTIVA"))
            for aid in sorted(set(created) | set(earlier)):
                n_alerts += _assignment_alert(s, aid, now) is not None
            # Idempotencia: si este evento ya generó alguna alerta (p. ej. se reprocesa tras
            # un reinicio), no se crea una alerta de disponibilidad adicional.
            already = s.scalar(select(Alert.id).where(Alert.change_event_id == eid).limit(1)) is not None
            # Si la oferta nueva ya la repartió otra pasada (p. ej. la reevaluación periódica),
            # las alertas de ASIGNACION ya informan: no se emite un aviso engañoso de "sin solicitud".
            slot = s.get(SlotState, slot_id) if slot_id else None
            consumed = (ctype in SUPPLY_TYPES and slot is not None and slot.available - slot.held <= 0
                        and slot.held > 0)
            # v1.3: si la oferta ya desapareció cuando se procesa el evento (p. ej. una ventana corta
            # que venció), avisar de "disponibilidad" sería engañoso: nadie podría usarla. El
            # historial conserva ambos eventos (la aparición y el agotamiento posterior).
            gone = (ctype in SUPPLY_TYPES and slot is not None
                    and (slot.status != "ACTIVO" or slot.available <= 0))
            if relevant and not created and not already and not consumed and not gone:
                title = f"{ev.change_type.replace('_', ' ').title()}: {ev.resource_name}"
                if ctype in SUPPLY_TYPES:
                    title += " (sin solicitud compatible asignable)"
                a = alerts_mod.create_alert(
                    s, dedup_key=f"evt:{ev.event_key}", kind="DISPONIBILIDAD", title=title, source_code=src.code,
                    region=ev.region, resource=ev.resource_name, now=now, change_event_id=ev.id,
                    payload={"cambio": ev.change_type, "antes": ev.before, "despues": ev.after})
                n_alerts += a is not None
            ev.processed = True
            ev.processed_at = clock.now()
        stats["eventos"] += 1
        stats["asignaciones"] += len(created)
        stats["alertas"] += n_alerts
    return stats


def _compact_meta(meta: dict) -> dict:
    """Resumen de los datos de la consulta para `last_result` (sin listas largas)."""
    out = {}
    for k, v in meta.items():
        if isinstance(v, list):
            nums = [x for x in v if isinstance(x, (int, float)) and x == x]
            out[k] = round(sum(nums), 2) if k.endswith("_ms") else len(v)
        else:
            out[k] = v
    return out


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
        payload={"solicitud": req.code, "titular": alerts_mod.enmascarar_nombre(req.holder), "cantidad": a.quantity,
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
        # v1.3: incluye asignaciones vivas que se quedaron sin aviso (fallo entre asignar y avisar).
        orphans = s.scalars(select(Assignment.id).where(
            Assignment.status == "ACTIVA", ~select(Alert.id).where(Alert.assignment_id == Assignment.id).exists()))
        for aid in sorted(set(created) | set(orphans)):
            n_alerts += _assignment_alert(s, aid, now) is not None
    inactive = flag_inactive_assignments(db, now, params)
    return {"validadas": len(pending), "expiradas": expired, "vencidas": vencidas,
            "reasignadas": len(created), "alertas": n_alerts, "inactivas_avisadas": inactive}


def flag_inactive_assignments(db: Database, now, params: dict) -> int:
    """v1.4: avisa (al operador y al administrador) de asignaciones EN_PROCESO sin actividad
    durante `inactividad_en_proceso_min`. NO las libera: puede que el operador sí esté
    reservando y solo se perdió la comunicación. La recuperación es manual y queda auditada."""
    minutes = float(params.get("inactividad_en_proceso_min", 30))
    if minutes <= 0:
        return 0
    limit = now - timedelta(minutes=minutes)
    n = 0
    with db.session() as s:
        last = func.coalesce(Assignment.last_activity_at, Assignment.taken_at, Assignment.created_at)
        for a in s.scalars(select(Assignment).where(Assignment.status == "EN_PROCESO", last <= limit,
                                                    Assignment.inactive_alerted_at.is_(None))):
            req, slot, op = s.get(Request, a.request_id), s.get(SlotState, a.slot_id), s.get(Operator, a.operator_id)
            src = s.get(Source, slot.source_id)
            since = a.last_activity_at or a.taken_at or a.created_at
            idle = int((now - since).total_seconds() // 60)
            a.inactive_alerted_at = now
            alerts_mod.create_alert(
                s, dedup_key=f"inactiva:{a.id}:{since.isoformat()}", kind="SISTEMA",
                title=f"{req.code}: asignación EN_PROCESO sin actividad desde hace {idle} min ({op.name})",
                source_code=src.code, region=slot.region, resource=slot.name, now=now, assignment_id=a.id,
                request_id=req.id, priority=req.priority, operator_name=op.name,
                payload={"evento_sistema": "asignacion_inactiva", "asignacion": a.id, "solicitud": req.code,
                         "operador": op.name, "tomada_por": a.taken_by, "minutos_sin_actividad": idle,
                         "que_hacer": "Confirmar con el operador. Si ya reservó: Recuperar > completada. "
                                      "Si no: Recuperar > devolver a la cola."})
            s.add(AuditLog(ts=now, username="motor", action="asignacion_inactiva", target=str(a.id),
                           detail={"minutos": idle, "operador": op.name}))
            n += 1
    return n


def due_sources(db: Database, now) -> list[int]:
    with db.session() as s:
        return list(s.scalars(select(Source.id).where(
            Source.status == "ACTIVA", (Source.next_poll_at.is_(None)) | (Source.next_poll_at <= now))))
