"""Verificador de integridad: comprueba invariantes que nunca deben romperse."""
from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Assignment, Operator, Request, SlotState

LIVE = ("ACTIVA", "EN_PROCESO")


def check(session: Session) -> list[str]:
    # v1.2: en PostgreSQL (READ COMMITTED) cada consulta veía un instante distinto y, con el
    # motor escribiendo, se informaban incoherencias inexistentes. Con REPEATABLE READ todas
    # las consultas ven la misma foto. Solo es posible si la sesión aún no ha consultado nada;
    # si ya lo hizo, se sigue con su nivel (el resultado puede incluir falsos positivos).
    if session.get_bind().dialect.name == "postgresql" and not session.in_transaction():
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    problems: list[str] = []
    live = list(session.scalars(select(Assignment).where(Assignment.status.in_(LIVE))))
    held_by_slot: Counter = Counter()
    per_request: Counter = Counter()
    per_operator: Counter = Counter()
    for a in live:
        held_by_slot[a.slot_id] += a.quantity
        per_request[a.request_id] += 1
        per_operator[a.operator_id] += 1
    for st in session.scalars(select(SlotState)):
        if st.held != held_by_slot.get(st.id, 0):
            problems.append(f"slot {st.id}: held={st.held} pero asignaciones vivas suman {held_by_slot.get(st.id, 0)}")
        if st.held < 0:
            problems.append(f"slot {st.id}: held negativo")
    for rid, n in per_request.items():
        if n > 1:
            problems.append(f"solicitud {rid}: {n} asignaciones vivas")
    for r in session.scalars(select(Request).where(Request.status.in_(["ASIGNADO", "EN_PROCESO"]))):
        if per_request.get(r.id, 0) != 1:
            problems.append(f"solicitud {r.id} en {r.status} sin asignación viva")
    for a in live:
        r = session.get(Request, a.request_id)
        expected = "ASIGNADO" if a.status == "ACTIVA" else "EN_PROCESO"
        if r.status != expected:
            problems.append(f"asignación {a.id} {a.status} pero solicitud {r.id} en {r.status}")
    for op in session.scalars(select(Operator)):
        if op.current_load != per_operator.get(op.id, 0):
            problems.append(f"operador {op.id}: carga={op.current_load} pero tiene {per_operator.get(op.id, 0)}")
        if op.current_load > op.max_concurrent:
            problems.append(f"operador {op.id}: supera su máximo")
    return problems
