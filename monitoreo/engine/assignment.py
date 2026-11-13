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

from sqlalchemy import and_, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from ..db import Database
from ..models import Assignment, Operator, Request, RequestEvent, SlotState, Source

# ---------------------------------------------------------------- máquina de estados
PENDIENTE, ELEGIBLE, ASIGNADO, EN_PROCESO, COMPLETADO = "PENDIENTE", "ELEGIBLE", "ASIGNADO", "EN_PROCESO", "COMPLETADO"
EXPIRADO, CANCELADO, ERROR, REINTENTO = "EXPIRADO", "CANCELADO", "ERROR", "REINTENTO"
# v2.0: la persona está siendo atendida en VENTANILLA. El motor no la asigna a nadie mientras tanto
# (no es MATCHABLE); al cerrar la atención vuelve a la cola (ELEGIBLE) o termina (COMPLETADO).
EN_VENTANILLA = "EN_VENTANILLA"

TRANSITIONS: dict[str, set[str]] = {
    PENDIENTE: {ELEGIBLE, ERROR, CANCELADO},
    ELEGIBLE: {ASIGNADO, CANCELADO, EXPIRADO, EN_VENTANILLA},
    ASIGNADO: {EN_PROCESO, REINTENTO, EXPIRADO, CANCELADO},
    EN_PROCESO: {COMPLETADO, REINTENTO, ERROR, CANCELADO, EXPIRADO},
    REINTENTO: {ASIGNADO, CANCELADO, EXPIRADO, EN_VENTANILLA},
    EN_VENTANILLA: {ELEGIBLE, COMPLETADO, CANCELADO},
    ERROR: {ELEGIBLE, CANCELADO},
    COMPLETADO: set(), CANCELADO: set(), EXPIRADO: set(),
}
MATCHABLE = (ELEGIBLE, REINTENTO)
TERMINAL = {COMPLETADO, CANCELADO, EXPIRADO}


class TransitionError(Exception):
    pass


class ConcurrentChange(TransitionError):
    """La solicitud cambió de estado en otra transacción entre la lectura y la escritura."""


def transition(session: Session, req: Request, to: str, actor: str, note: str, now: datetime) -> None:
    """Cambia el estado de una solicitud y lo anota en su historial.

    v1.4: la escritura es CONDICIONAL al estado leído (`UPDATE ... WHERE status = <leído>`). Con
    PostgreSQL (READ COMMITTED) dos transacciones pueden leer el mismo estado; antes la segunda
    sobrescribía en silencio lo que había hecho la primera (p. ej. cancelar una solicitud que otro
    proceso acababa de asignar o completar). Ahora la segunda recibe `ConcurrentChange`.
    En SQLite no cambia nada: las transacciones de escritura ya eran secuenciales."""
    if to not in TRANSITIONS.get(req.status, set()):
        raise TransitionError(f"Transición no permitida: {req.status} -> {to}")
    seen = req.status
    # UPDATE de tabla (Core) sin vaciado automático: el objeto se actualiza a mano justo debajo.
    # Con el UPDATE del ORM, SQLAlchemy recorría todos los objetos de la sesión y vaciaba la sesión
    # en CADA transición: en el mantenimiento (miles de solicitudes en una sesión) llegó a ser el
    # 57 % de la CPU del servidor (perfil v1.4) y crecía con el cuadrado del número de solicitudes.
    t = Request.__table__
    with session.no_autoflush:
        changed = session.execute(update(t).where(t.c.id == req.id, t.c.status == seen)
                                  .values(status=to, updated_at=now)).rowcount
    if changed != 1:
        raise ConcurrentChange(f"la solicitud {req.id} cambió de estado mientras tanto (se leyó {seen})")
    set_committed_value(req, "status", to)
    set_committed_value(req, "updated_at", now)
    session.add(RequestEvent(request_id=req.id, ts=now, from_status=seen, to_status=to, actor=actor, note=note))


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
    # v1.6: atributos del recurso (p. ej. {"tramite": "alta", "sucursal": ["NOR-1", "NOR-2"]})
    for k, v in (c.get("atributos") or {}).items():
        valores = v if isinstance(v, list) else [v]
        if (slot.attributes or {}).get(k) not in valores:
            return False
    return True


def _preferida(req: Request, slot: SlotState) -> int:
    """v1.6: 0 si el recurso coincide con la preferencia de la solicitud (p. ej. su sucursal
    preferida), 1 si solo coincide con una alternativa. Desempata a igual prioridad."""
    pref = (req.conditions or {}).get("preferida") or {}
    return 0 if all((slot.attributes or {}).get(k) == v for k, v in pref.items()) else 1


def candidate_requests(session: Session, slot: SlotState, source_code: str, free: int, limit: int = 50) -> list[Request]:
    """Solicitudes compatibles, ordenadas por prioridad (1 primero) y antigüedad."""
    rejected = select(Assignment.request_id).where(Assignment.slot_id == slot.id, Assignment.status == "RECHAZADA")
    q = (select(Request)
         .where(Request.status.in_(MATCHABLE), Request.region == slot.region,
                Request.resource_type == slot.resource_type, Request.quantity <= free,
                Request.id.not_in(rejected))
         .order_by(Request.priority.asc(), Request.created_at.asc(), Request.id.asc())
         .limit(limit * 4))
    ok = [r for r in session.scalars(q) if _matches(r, slot, source_code)]
    ok.sort(key=lambda r: (r.priority, _preferida(r, slot), r.created_at, r.id))   # orden estable
    return ok[:limit]


def candidate_operators(session: Session, region: str, sucursal: str | None = None) -> list[Operator]:
    ops = session.scalars(select(Operator).where(Operator.active.is_(True),
                                                 Operator.current_load < Operator.max_concurrent)).all()
    ops = [o for o in ops if not o.regions or region in o.regions]
    if sucursal:   # v1.6: operador limitado a ciertas sucursales (vacío = todas las de sus regiones)
        ops = [o for o in ops if not (o.sucursales or []) or sucursal in o.sucursales]
    return sorted(ops, key=lambda o: (o.current_load, o.last_assigned_at or datetime.min, o.id))


def elegir_operadores(session: Session, slot: SlotState, req: Request, now: datetime, params: dict,
                      vetados: set[int]) -> tuple[list[Operator], dict]:
    """v1.7: candidatos ordenados según `reparto_modo` y una EXPLICACIÓN legible de la decisión:
    quién podía recibirla, por qué cada descartado no, y con qué criterio se ordenó.

    Reglas (las de v1.0-v1.6 se conservan; lo nuevo es opcional y está apagado por defecto):
    - activo, con capacidad, de la región y con la sucursal permitida (v1.6);
    - no quien ya rechazó o declinó esta solicitud (v1.6);
    - `operador_ausente_s` > 0: no quien tiene usuario y no usa el panel hace más de N s;
    - `reparto_modo`: menor_carga (carga actual, luego quien lleva más sin recibir) o
      equitativo_dia (menos asignaciones en las últimas 24 h, luego carga)."""
    sucursal = (slot.attributes or {}).get("sucursal")
    ausente_s = float(params.get("operador_ausente_s") or 0)
    modo = params.get("reparto_modo") or "menor_carga"
    ok, descartados = [], []
    for o in session.scalars(select(Operator).order_by(Operator.id)):
        motivo = None
        if not o.active:
            motivo = "desactivado"
        elif o.regions and slot.region not in o.regions:
            motivo = f"no atiende la región {slot.region}"
        elif sucursal and (o.sucursales or []) and sucursal not in o.sucursales:
            motivo = f"no tiene permitida la sucursal {sucursal}"
        elif o.current_load >= o.max_concurrent:
            motivo = f"sin capacidad ({o.current_load}/{o.max_concurrent})"
        elif o.id in vetados:
            motivo = "ya rechazó o declinó esta solicitud"
        elif ausente_s > 0 and o.user_id is not None and (
                o.visto_en is None or (now - o.visto_en).total_seconds() > ausente_s):
            motivo = ("ausente: nunca ha entrado al panel" if o.visto_en is None else
                      f"ausente: sin actividad en el panel hace {int((now - o.visto_en).total_seconds() // 60)} min")
        if motivo:
            descartados.append({"operador": o.name, "motivo": motivo})
        else:
            ok.append(o)
    hoy: dict[int, int] = {}
    if ok and modo == "equitativo_dia":
        hoy = dict(session.execute(select(Assignment.operator_id, func.count()).where(
            Assignment.operator_id.in_([o.id for o in ok]), Assignment.created_at >= now - timedelta(hours=24))
            .group_by(Assignment.operator_id)).all())
        ok.sort(key=lambda o: (hoy.get(o.id, 0), o.current_load, o.last_assigned_at or datetime.min, o.id))
    else:
        ok.sort(key=lambda o: (o.current_load, o.last_assigned_at or datetime.min, o.id))
    criterio = ("menos asignaciones en las últimas 24 h, luego menor carga" if modo == "equitativo_dia"
                else "menor carga actual, luego quien lleva más tiempo sin recibir")
    explic = {
        "criterio": criterio, "reparto_modo": modo, "region": slot.region, "sucursal": sucursal,
        "solicitud": {"codigo": req.code, "prioridad": req.priority,
                      "antiguedad_min": int((now - req.created_at).total_seconds() // 60) if req.created_at else None},
        "candidatos": [{"operador": o.name, "carga": f"{o.current_load}/{o.max_concurrent}",
                        **({"asignaciones_24h": hoy.get(o.id, 0)} if modo == "equitativo_dia" else {})} for o in ok],
        "descartados": descartados,
    }
    return ok, explic


def resumen_explicacion(explic: dict, elegido: str) -> str:
    """Texto legible para el administrador."""
    sol = explic.get("solicitud") or {}
    partes = [f"Se eligió a {elegido} para {sol.get('codigo')} (prioridad {sol.get('prioridad')}, "
              f"en cola hace {sol.get('antiguedad_min')} min) en {explic.get('region')}"
              + (f" / {explic['sucursal']}" if explic.get("sucursal") else "") + ".",
              f"Criterio: {explic.get('criterio')}."]
    cands = explic.get("candidatos") or []
    if len(cands) > 1:
        partes.append("Otros candidatos: " + ", ".join(f"{c['operador']} ({c['carga']})" for c in cands
                                                      if c["operador"] != elegido) + ".")
    if explic.get("descartados"):
        partes.append("Descartados: " + "; ".join(f"{d['operador']} — {d['motivo']}" for d in explic["descartados"]) + ".")
    return " ".join(partes)


@dataclass
class AssignOutcome:
    assignment_id: int | None
    reason: str = ""


def try_assign(db: Database, slot_id: int, request_id: int, operator_id: int, now: datetime, ttl_s: float,
               change_event_id: int | None = None, actor: str = "motor",
               explicacion: dict | None = None) -> AssignOutcome:
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
                           created_at=now, expires_at=now + timedelta(seconds=ttl_s), explicacion=explicacion)
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
            # v1.6: quien ya rechazó o declinó esta solicitud no la vuelve a recibir
            vetados = set(s.scalars(select(Assignment.operator_id).where(
                Assignment.request_id == req.id, Assignment.status.in_(["RECHAZADA", "DECLINADA"]))))
            # v1.7: misma regla de compatibilidad + ausencias + reparto configurable, con explicación
            ops, explic = elegir_operadores(s, slot, req, now, params, vetados)
            hay_operadores = bool(ops) or any(d["motivo"] == "ya rechazó o declinó esta solicitud"
                                              for d in explic["descartados"])
            op_info = [(o.id, o.name) for o in ops]
            req_id, region = req.id, slot.region
        tried.add(req_id)
        if not hay_operadores:
            break  # nadie disponible: los recursos quedan libres y se reevalúan después
        if not ops:
            continue  # solo la rechazaron los disponibles: se prueba la siguiente solicitud
        for op_id, op_name in op_info:
            e = {**explic, "elegido": op_name, "resumen": resumen_explicacion(explic, op_name)}
            out = try_assign(db, slot_id, req_id, op_id, now, params["ttl_asignacion_s"], change_event_id,
                             explicacion=e)
            if out.assignment_id:
                created.append(out.assignment_id)
                break
            if out.reason != "operador sin capacidad":
                break
    return created


def free_slots(session: Session) -> list[int]:
    """Recursos con unidades libres SEGÚN DATOS VIGENTES (v1.6): si la última consulta de su fuente
    falló, lo guardado puede estar desactualizado y no se usa para crear asignaciones nuevas
    ("no inventar disponibilidad que no se puede verificar"). Vuelve a contar al primer éxito."""
    from ..models import Source
    return list(session.scalars(
        select(SlotState.id).join(Source, Source.id == SlotState.source_id)
        .where(SlotState.status == "ACTIVO", SlotState.available > SlotState.held, Source.consecutive_failures == 0)))


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


def record_operation(s: Session, op: dict | None, assignment_id: int, code: int, result: dict) -> None:
    """v1.4: guarda el resultado de una operación con clave (misma transacción que su efecto)."""
    if op and op.get("key"):
        from ..models import Operation
        s.add(Operation(username=op["user"], op_key=op["key"], action=op["action"], target=str(assignment_id),
                        status_code=code, result=result))


def take_assignment(db: Database, assignment_id: int, operator_id: int | None, now: datetime, actor: str,
                    op: dict | None = None, idempotent: bool = False) -> bool | str:
    """El operador toma la asignación (ASIGNADO -> EN_PROCESO). Solo uno puede lograrlo.

    v1.4: con `idempotent=True` y un operador concreto, si la asignación YA está EN_PROCESO para
    ese mismo operador se devuelve "ya_tomada" (verdadero): así repetir "tomar" tras perder la
    respuesta no deja la asignación huérfana. Nunca da por buena la toma de OTRO operador."""
    with db.session() as s:
        cond = [Assignment.id == assignment_id, Assignment.status == "ACTIVA", Assignment.expires_at > now]
        if operator_id is not None:
            cond.append(Assignment.operator_id == operator_id)
        if s.execute(update(Assignment).where(and_(*cond)).values(
                status="EN_PROCESO", taken_at=now, taken_by=actor[:64], last_activity_at=now)).rowcount != 1:
            if idempotent and operator_id is not None:
                a = s.get(Assignment, assignment_id)
                if a is not None and a.status == "EN_PROCESO" and a.operator_id == operator_id:
                    a.last_activity_at = now
                    record_operation(s, op, assignment_id, 200, {"ok": True, "ya_tomada": True})
                    return "ya_tomada"
            return False
        a = s.get(Assignment, assignment_id)
        req = s.get(Request, a.request_id)
        transition(s, req, EN_PROCESO, actor, f"asignación {assignment_id} tomada", now)
        record_operation(s, op, assignment_id, 200, {"ok": True})
        return True


def touch_assignment(db: Database, assignment_id: int, operator_id: int | None, now: datetime, actor: str) -> bool:
    """v1.4: el operador indica que sigue trabajando en una asignación EN_PROCESO."""
    with db.session() as s:
        cond = [Assignment.id == assignment_id, Assignment.status == "EN_PROCESO"]
        if operator_id is not None:
            cond.append(Assignment.operator_id == operator_id)
        return s.execute(update(Assignment).where(and_(*cond)).values(
            last_activity_at=now, inactive_alerted_at=None)).rowcount == 1


def complete_assignment(db: Database, assignment_id: int, operator_id: int | None, now: datetime, actor: str,
                        op: dict | None = None) -> bool:
    with db.session() as s:
        cond = [Assignment.id == assignment_id, Assignment.status == "EN_PROCESO"]
        if operator_id is not None:
            cond.append(Assignment.operator_id == operator_id)
        if s.execute(update(Assignment).where(and_(*cond)).values(status="COMPLETADA")).rowcount != 1:
            return False
        record_operation(s, op, assignment_id, 200, {"ok": True})
        a = s.get(Assignment, assignment_id)
        _release(s, a, "COMPLETADA", now, "completada")
        # Consumo local optimista: la unidad ya no está libre hasta que la fuente confirme.
        # v1.3: se invalida la huella para que la siguiente consulta compare de verdad. Antes, si la
        # fuente ya había reflejado la reserva (o publicaba el mismo número por otro motivo), la
        # huella coincidía, la consulta se daba por "sin cambios" y el motor se quedaba con MENOS
        # unidades de las reales indefinidamente (detectado en la prueba HTTP de la v1.3).
        s.execute(update(SlotState).where(SlotState.id == a.slot_id, SlotState.available >= a.quantity)
                  .values(available=SlotState.available - a.quantity, fingerprint=""))
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
                      params: dict, reason: str = "rechazada por el operador", op: dict | None = None,
                      only_status: tuple = ("ACTIVA", "EN_PROCESO"), status: str = "RECHAZADA") -> bool:
    """`status`: RECHAZADA (el recurso no sirve para esta solicitud: no se vuelve a ofrecer ese
    recurso a esa solicitud) o DECLINADA (v1.6: el problema es del OPERADOR -no disponible,
    conflicto de interés, carga-: la solicitud vuelve a la cola y no se le ofrece a ESE operador)."""
    if status not in ("RECHAZADA", "DECLINADA"):
        raise ValueError("estado de rechazo no válido")
    with db.session() as s:
        cond = [Assignment.id == assignment_id, Assignment.status.in_(list(only_status))]
        if operator_id is not None:
            cond.append(Assignment.operator_id == operator_id)
        if s.execute(update(Assignment).where(and_(*cond)).values(status=status)).rowcount != 1:
            return False
        record_operation(s, op, assignment_id, 200, {"ok": True})
        a = s.get(Assignment, assignment_id)
        _release(s, a, status, now, reason)
        _back_to_queue(s, s.get(Request, a.request_id), params, actor, reason, now)
        return True


def expire_assignments(db: Database, now: datetime, params: dict) -> int:
    """Asignaciones no tomadas a tiempo: se liberan y la solicitud vuelve a la cola."""
    with db.session() as s:
        ids = list(s.scalars(select(Assignment.id).where(Assignment.status == "ACTIVA", Assignment.expires_at <= now)))
    n = 0
    for aid in ids:
        try:
            with db.session() as s:
                if s.execute(update(Assignment).where(Assignment.id == aid, Assignment.status == "ACTIVA")
                             .values(status="EXPIRADA")).rowcount != 1:
                    continue
                a = s.get(Assignment, aid)
                _release(s, a, "EXPIRADA", now, "no tomada a tiempo")
                _back_to_queue(s, s.get(Request, a.request_id), params, "motor", "asignación expirada", now)
                n += 1
        except ConcurrentChange:
            continue          # la solicitud cambió a la vez (p. ej. cancelada): se deshizo todo
    return n


def invalidate_overheld(db: Database, slot_id: int, now: datetime, params: dict) -> int:
    """Si la fuente redujo unidades por debajo de lo retenido, se anulan las asignaciones
    más recientes que el operador aún no tomó, hasta recuperar la coherencia."""
    n = 0
    for _ in range(10000):
        try:
            n += _invalidate_one(db, slot_id, now)
        except _Done:
            return n
        except ConcurrentChange:
            continue           # otra transacción cambió esa solicitud a la vez: se vuelve a leer
    return n


class _Done(Exception):
    pass


def _invalidate_one(db: Database, slot_id: int, now: datetime) -> int:
    with db.session() as s:
        slot = s.get(SlotState, slot_id)
        if slot is None or slot.held <= (slot.available if slot.status == "ACTIVO" else 0):
            raise _Done
        a = s.scalar(select(Assignment).where(Assignment.slot_id == slot_id, Assignment.status == "ACTIVA")
                     .order_by(Assignment.created_at.desc(), Assignment.id.desc()).limit(1))
        if a is None:
            raise _Done  # solo quedan asignaciones EN_PROCESO: se respetan y quedan señaladas
        # v1.4: condicional; si el operador la tomó justo ahora, se respeta y se busca otra
        if s.execute(update(Assignment).where(Assignment.id == a.id, Assignment.status == "ACTIVA")
                     .values(status="CANCELADA")).rowcount != 1:
            return 0
        _release(s, a, "CANCELADA", now, "el recurso dejó de estar disponible en la fuente")
        req = s.get(Request, a.request_id)
        transition(s, req, REINTENTO, "motor", "recurso ya no disponible", now)
        req.operator_id = None
        return 1


def cancel_request(db: Database, request_id: int, now: datetime, actor: str) -> bool:
    """v1.4: resistente a cambios simultáneos (asignar, tomar o completar a la vez). Si otro
    proceso cambia la solicitud o su asignación entre la lectura y la escritura, se deshace todo
    y se vuelve a leer; una solicitud ya completada nunca se convierte en cancelada."""
    for _ in range(5):
        try:
            with db.session() as s:
                req = s.get(Request, request_id)
                if req is None or req.status in TERMINAL:
                    return False
                live = s.scalar(select(Assignment).where(Assignment.request_id == request_id,
                                                         Assignment.status.in_(["ACTIVA", "EN_PROCESO"])))
                if live:
                    if s.execute(update(Assignment).where(Assignment.id == live.id,
                                                          Assignment.status == live.status)
                                 .values(status="CANCELADA")).rowcount != 1:
                        raise ConcurrentChange("la asignación cambió mientras tanto")
                    _release(s, live, "CANCELADA", now, "solicitud cancelada")
                transition(s, req, CANCELADO, actor, "cancelada", now)
                return True
        except ConcurrentChange:
            continue
    return False


def expire_requests(db: Database, now: datetime) -> int:
    """Solicitudes con fecha de vencimiento (conditions.vence) superada."""
    n = 0
    with db.session() as s:
        for req in s.scalars(select(Request).where(Request.status.in_([ELEGIBLE, REINTENTO, PENDIENTE]))):
            v = (req.conditions or {}).get("vence")
            if v:
                try:
                    if datetime.fromisoformat(v) < now:
                        with s.begin_nested():   # si otro proceso la asignó a la vez, se deja para luego
                            if req.status == PENDIENTE:
                                transition(s, req, CANCELADO, "motor", "vencida antes de validarse", now)
                            else:
                                transition(s, req, EXPIRADO, "motor", "vigencia vencida", now)
                        n += 1
                except (ValueError, ConcurrentChange):
                    pass
    return n
