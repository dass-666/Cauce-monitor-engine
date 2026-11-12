"""Cola de solicitudes, máquina de estados y asignación transaccional.

Cómo se evita la doble asignación (sin candados distribuidos ni Redis):
cada asignación es UNA transacción con tres UPDATE condicionales:

  1. slot_states.held += q   SOLO SI  available - held >= q    (hay unidades libres)
  2. requests.status = ASIGNADO  SOLO SI  status ∈ {ELEGIBLE, REINTENTO}
  3. operators.current_load += 1 SOLO SI  current_load < max_concurrent

Si cualquiera afecta 0 filas, alguien se adelantó y se hace ROLLBACK completo.
La base de datos evalúa cada condición de forma atómica (en PostgreSQL el UPDATE
re-evalúa el WHERE tras esperar el candado de fila; en SQLite las escrituras
son serializadas). Además, `assignments.live_request_id` es UNIQUE: aunque todo
lo anterior fallara, no pueden existir dos asignaciones vivas de una solicitud.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import Database
from ..models import Assignment, Operator, Request, RequestEvent, SlotState, Source

# ---------------------------------------------------------------- máquina de estados
PENDIENTE, ELEGIBLE, ASIGNADO, EN_PROCESO, COMPLETADO = "PENDIENTE", "ELEGIBLE", "ASIGNADO", "EN_PROCESO", "COMPLETADO"
EXPIRADO, CANCELADO, ERROR, REINTENTO = "EXPIRADO", "CANCELADO", "ERROR", "REINTENTO"

TRANSITIONS: dict[str, set[str]] = {
    PENDIENTE: {ELEGIBLE, ERROR, CANCELADO},
    ELEGIBLE: {ASIGNADO, CANCELADO, EXPIRADO},
    ASIGNADO: {EN_PROCESO, REINTENTO, EXPIRADO, CANCELADO},
    EN_PROCESO: {COMPLETADO, REINTENTO, ERROR, CANCELADO, EXPIRADO},
    REINTENTO: {ASIGNADO, CANCELADO, EXPIRADO},
    ERROR: {ELEGIBLE, CANCELADO},
    COMPLETADO: set(), CANCELADO: set(), EXPIRADO: set(),
}
MATCHABLE = (ELEGIBLE, REINTENTO)
TERMINAL = {COMPLETADO, CANCELADO, EXPIRADO}


class TransitionError(Exception):
    pass


def transition(session: Session, req: Request, to: str, actor: str, note: str, now: datetime) -> None:
    if to not in TRANSITIONS.get(req.status, set()):
        raise TransitionError(f"Transición no permitida: {req.status} -> {to}")
    session.add(RequestEvent(request_id=req.id, ts=now, from_status=req.status, to_status=to, actor=actor, note=note))
    req.status, req.updated_at = to, now


# ---------------------------------------------------------------- registro y validación
def validate_request(session: Session, req: Request, now: datetime, actor: str = "motor") -> None:
    """PENDIENTE -> ELEGIBLE si las condiciones son coherentes; si no, ERROR."""
    c = req.conditions or {}
    problems = []
    try:
        desde = datetime.fromisoformat(c["desde"]) if c.get("desde") else None
        hasta = datetime.fromisoformat(c["hasta"]) if c.get("hasta") else None
        if desde and hasta and desde > hasta:
            problems.append("'desde' es posterior a 'hasta'")
    except ValueError:
        problems.append("fechas inválidas en condiciones")
    if req.quantity < 1:
        problems.append("cantidad inválida")
    if problems:
        req.last_error = "; ".join(problems)
        transition(session, req, ERROR, actor, req.last_error, now)
    else:
        transition(session, req, ELEGIBLE, actor, "condiciones validadas", now)


def _matches(req: Request, slot: SlotState, source_code: str) -> bool:
    c = req.conditions or {}
    if c.get("fuente") and c["fuente"] != source_code:
        return False
    if c.get("nombre_contiene") and c["nombre_contiene"].lower() not in slot.name.lower():
        return False
    if slot.starts_at is not None:
        if c.get("desde") and slot.starts_at < datetime.fromisoformat(c["desde"]):
            return False
        if c.get("hasta") and slot.starts_at > datetime.fromisoformat(c["hasta"]):
            return False
    if c.get("capacidad_minima") and (slot.capacity or 0) < int(c["capacidad_minima"]):
        return False
    return True


def candidate_requests(session: Session, slot: SlotState, source_code: str, free: int, limit: int = 50) -> list[Request]:
    """Solicitudes compatibles, ordenadas por prioridad (1 primero) y antigüedad."""
    rejected = select(Assignment.request_id).where(Assignment.slot_id == slot.id, Assignment.status == "RECHAZADA")
    q = (select(Request)
         .where(Request.status.in_(MATCHABLE), Request.region == slot.region,
                Request.resource_type == slot.resource_type, Request.quantity <= free,
                Request.id.not_in(rejected))
         .order_by(Request.priority.asc(), Request.created_at.asc(), Request.id.asc())
         .limit(limit * 4))
    return [r for r in session.scalars(q) if _matches(r, slot, source_code)][:limit]


def candidate_operators(session: Session, region: str) -> list[Operator]:
    ops = session.scalars(select(Operator).where(Operator.active.is_(True),
                                                 Operator.current_load < Operator.max_concurrent)).all()
    ops = [o for o in ops if not o.regions or region in o.regions]
    return sorted(ops, key=lambda o: (o.current_load, o.last_assigned_at or datetime.min, o.id))


@dataclass
class AssignOutcome:
    assignment_id: int | None
    reason: str = ""


def try_assign(db: Database, slot_id: int, request_id: int, operator_id: int, now: datetime, ttl_s: float,
               change_event_id: int | None = None, actor: str = "motor") -> AssignOutcome:
    """Intento atómico de asignación. Devuelve assignment_id o el motivo del rechazo."""
    try:
        with db.session() as s:
            req = s.get(Request, request_id)
            if req is None:
                return AssignOutcome(None, "solicitud inexistente")
            q = req.quantity
            r1 = s.execute(update(SlotState)
                           .where(SlotState.id == slot_id, SlotState.status == "ACTIVO",
                                  SlotState.available - SlotState.held >= q)
                           .values(held=SlotState.held + q)).rowcount
            if r1 != 1:
                raise _Abort("sin unidades libres")
            prev_status = req.status
            r2 = s.execute(update(Request)
                           .where(Request.id == request_id, Request.status.in_(MATCHABLE))
                           .values(status=ASIGNADO, operator_id=operator_id, attempts=Request.attempts + 1,
                                   updated_at=now)).rowcount
            if r2 != 1:
                raise _Abort("la solicitud ya no está disponible")
            r3 = s.execute(update(Operator)
                           .where(Operator.id == operator_id, Operator.active.is_(True),
                                  Operator.current_load < Operator.max_concurrent)
                           .values(current_load=Operator.current_load + 1, last_assigned_at=now)).rowcount
            if r3 != 1:
                raise _Abort("operador sin capacidad")
            a = Assignment(request_id=request_id, slot_id=slot_id, operator_id=operator_id, quantity=q,
                           change_event_id=change_event_id, status="ACTIVA", live_request_id=request_id,
                           created_at=now, expires_at=now + timedelta(seconds=ttl_s))
            s.add(a)
            s.add(RequestEvent(request_id=request_id, ts=now, from_status=prev_status, to_status=ASIGNADO,
                               actor=actor, note=f"slot={slot_id} operador={operator_id}"))
            s.flush()
            return AssignOutcome(a.id)
    except _Abort as e:
        return AssignOutcome(None, str(e))
    except IntegrityError:
        return AssignOutcome(None, "conflicto de integridad (asignación viva duplicada)")


class _Abort(Exception):
    pass


def allocate_slot(db: Database, slot_id: int, now: datetime, params: dict, change_event_id: int | None = None,
                  max_assignments: int = 1000) -> list[int]:
    """Reparte las unidades libres de un recurso entre las solicitudes compatibles."""
    created: list[int] = []
    tried: set[int] = set()
    while len(created) < max_assignments:
        with db.session() as s:
            slot = s.get(SlotState, slot_id)
            if slot is None or slot.status != "ACTIVO":
                break
            free = slot.available - slot.held
            if free <= 0:
                break
            src = s.get(Source, slot.source_id)
            cands = [r for r in candidate_requests(s, slot, src.code, free) if r.id not in tried]
            if not cands:
                break
            req = cands[0]
            ops = candidate_operators(s, slot.region)
            req_id, region = req.id, slot.region
        tried.add(req_id)
        if not ops:
            break  # nadie disponible: los recursos quedan libres y se reevalúan después
        for op in ops:
            out = try_assign(db, slot_id, req_id, op.id, now, params["ttl_asignacion_s"], change_event_id)
            if out.assignment_id:
                created.append(out.assignment_id)
                break
            if out.reason != "operador sin capacidad":
                break
    return created


def free_slots(session: Session) -> list[int]:
    return list(session.scalars(select(SlotState.id).where(SlotState.status == "ACTIVO",
                                                           SlotState.available > SlotState.held)))


def rebalance(db: Database, now: datetime, params: dict) -> list[int]:
    """Reevalúa todos los recursos con unidades libres (tras expiraciones, rechazos o nuevas solicitudes)."""
    with db.session() as s:
        waiting = s.scalar(select(Request.id).where(Request.status.in_(MATCHABLE)).limit(1))
        ids = free_slots(s) if waiting else []
    created = []
    for sid in ids:
        created += allocate_slot(db, sid, now, params)
    return created


# ---------------------------------------------------------------- ciclo de vida de una asignación
def _release(s: Session, a: Assignment, status: str, now: datetime, reason: str) -> None:
    s.execute(update(SlotState).where(SlotState.id == a.slot_id).values(held=SlotState.held - a.quantity))
    s.execute(update(Operator).where(Operator.id == a.operator_id, Operator.current_load > 0)
              .values(current_load=Operator.current_load - 1))
    a.status, a.closed_at, a.reason, a.live_request_id = status, now, reason, None


def _back_to_queue(s: Session, req: Request, params: dict, actor: str, note: str, now: datetime) -> None:
    if req.attempts >= int(params["max_intentos_solicitud"]):
        transition(s, req, EXPIRADO, actor, f"{note}; intentos agotados", now)
    else:
        transition(s, req, REINTENTO, actor, note, now)
    req.operator_id = None


def take_assignment(db: Database, assignment_id: int, operator_id: int | None, now: datetime, actor: str) -> bool:
    """El operador toma la asignación (ASIGNADO -> EN_PROCESO). Solo uno puede lograrlo."""
    with db.session() as s:
        cond = [Assignment.id == assignment_id, Assignment.status == "ACTIVA", Assignment.expires_at > now]
        if operator_id is not None:
            cond.append(Assignment.operator_id == operator_id)
        if s.execute(update(Assignment).where(and_(*cond)).values(status="EN_PROCESO")).rowcount != 1:
            return False
        a = s.get(Assignment, assignment_id)
        req = s.get(Request, a.request_id)
        transition(s, req, EN_PROCESO, actor, f"asignación {assignment_id} tomada", now)
        return True


def complete_assignment(db: Database, assignment_id: int, operator_id: int | None, now: datetime, actor: str) -> bool:
    with db.session() as s:
        cond = [Assignment.id == assignment_id, Assignment.status == "EN_PROCESO"]
        if operator_id is not None:
            cond.append(Assignment.operator_id == operator_id)
        if s.execute(update(Assignment).where(and_(*cond)).values(status="COMPLETADA")).rowcount != 1:
            return False
        a = s.get(Assignment, assignment_id)
        _release(s, a, "COMPLETADA", now, "completada")
        # Consumo local optimista: la unidad ya no está libre hasta que la fuente confirme.
        s.execute(update(SlotState).where(SlotState.id == a.slot_id, SlotState.available >= a.quantity)
                  .values(available=SlotState.available - a.quantity))
        _consume_in_simulator(s, a)
        req = s.get(Request, a.request_id)
        transition(s, req, COMPLETADO, actor, f"asignación {assignment_id} completada", now)
        return True


def _consume_in_simulator(s: Session, a: Assignment) -> None:
    """Si el recurso viene del laboratorio, la 'reserva' consume inventario ficticio."""
    from ..models import SimItem
    slot = s.get(SlotState, a.slot_id)
    src = s.get(Source, slot.source_id)
    if src.connector_type == "simulacion":
        s.execute(update(SimItem).where(SimItem.feed == src.config.get("feed"),
                                        SimItem.resource_key == slot.resource_key,
                                        SimItem.available >= a.quantity)
                  .values(available=SimItem.available - a.quantity))


def reject_assignment(db: Database, assignment_id: int, operator_id: int | None, now: datetime, actor: str,
                      params: dict, reason: str = "rechazada por el operador") -> bool:
    with db.session() as s:
        cond = [Assignment.id == assignment_id, Assignment.status.in_(["ACTIVA", "EN_PROCESO"])]
        if operator_id is not None:
            cond.append(Assignment.operator_id == operator_id)
        if s.execute(update(Assignment).where(and_(*cond)).values(status="RECHAZADA")).rowcount != 1:
            return False
        a = s.get(Assignment, assignment_id)
        _release(s, a, "RECHAZADA", now, reason)
        _back_to_queue(s, s.get(Request, a.request_id), params, actor, reason, now)
        return True


def expire_assignments(db: Database, now: datetime, params: dict) -> int:
    """Asignaciones no tomadas a tiempo: se liberan y la solicitud vuelve a la cola."""
    with db.session() as s:
        ids = list(s.scalars(select(Assignment.id).where(Assignment.status == "ACTIVA", Assignment.expires_at <= now)))
    n = 0
    for aid in ids:
        with db.session() as s:
            if s.execute(update(Assignment).where(Assignment.id == aid, Assignment.status == "ACTIVA")
                         .values(status="EXPIRADA")).rowcount != 1:
                continue
            a = s.get(Assignment, aid)
            _release(s, a, "EXPIRADA", now, "no tomada a tiempo")
            _back_to_queue(s, s.get(Request, a.request_id), params, "motor", "asignación expirada", now)
            n += 1
    return n


def invalidate_overheld(db: Database, slot_id: int, now: datetime, params: dict) -> int:
    """Si la fuente redujo unidades por debajo de lo retenido, se anulan las asignaciones
    más recientes que el operador aún no tomó, hasta recuperar la coherencia."""
    n = 0
    while True:
        with db.session() as s:
            slot = s.get(SlotState, slot_id)
            if slot is None or slot.held <= (slot.available if slot.status == "ACTIVO" else 0):
                return n
            a = s.scalar(select(Assignment).where(Assignment.slot_id == slot_id, Assignment.status == "ACTIVA")
                         .order_by(Assignment.created_at.desc(), Assignment.id.desc()).limit(1))
            if a is None:
                return n  # solo quedan asignaciones EN_PROCESO: se respetan y quedan señaladas
            s.execute(update(Assignment).where(Assignment.id == a.id).values(status="CANCELADA"))
            _release(s, a, "CANCELADA", now, "el recurso dejó de estar disponible en la fuente")
            req = s.get(Request, a.request_id)
            transition(s, req, REINTENTO, "motor", "recurso ya no disponible", now)
            req.operator_id = None
            n += 1


def cancel_request(db: Database, request_id: int, now: datetime, actor: str) -> bool:
    with db.session() as s:
        req = s.get(Request, request_id)
        if req is None or req.status in TERMINAL:
            return False
        live = s.scalar(select(Assignment).where(Assignment.request_id == request_id,
                                                 Assignment.status.in_(["ACTIVA", "EN_PROCESO"])))
        if live:
            s.execute(update(Assignment).where(Assignment.id == live.id).values(status="CANCELADA"))
            _release(s, live, "CANCELADA", now, "solicitud cancelada")
        transition(s, req, CANCELADO, actor, "cancelada", now)
        return True


def expire_requests(db: Database, now: datetime) -> int:
    """Solicitudes con fecha de vencimiento (conditions.vence) superada."""
    n = 0
    with db.session() as s:
        for req in s.scalars(select(Request).where(Request.status.in_([ELEGIBLE, REINTENTO, PENDIENTE]))):
            v = (req.conditions or {}).get("vence")
            if v:
                try:
                    if datetime.fromisoformat(v) < now:
                        if req.status == PENDIENTE:
                            transition(s, req, CANCELADO, "motor", "vencida antes de validarse", now)
                        else:
                            transition(s, req, EXPIRADO, "motor", "vigencia vencida", now)
                        n += 1
                except ValueError:
                    pass
    return n
