"""v1.7 · Reparaciones del diagnóstico.

Dos clases, y la diferencia importa:
- SEGURAS: recalculan datos DERIVADOS de otros (la carga de un operador sale de sus asignaciones
  vivas; lo retenido de un recurso, de las asignaciones que lo usan) o devuelven a la cola trabajo
  que el propio motor ya reintenta. No borran nada ni deciden por nadie. Las aplica el
  autodiagnóstico periódico y cualquier administrador; SIEMPRE quedan en la auditoría con el antes
  y el después.
- CON CONFIRMACIÓN: borran o deciden algo que no se puede deducir con certeza. Solo un
  administrador, con `confirmo=true` y motivo; también quedan auditadas.

Lo que NUNCA se repara solo: asignaciones en proceso (alguien puede estar trabajando), reservaciones
con confirmación incierta (las resuelve el motor preguntando al portal) y cualquier dato personal.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime

from sqlalchemy import delete, func, select, update

from .db import Database
from .models import Alert, Assignment, AuditLog, Delivery, Operator, SlotState

LIVE = ("ACTIVA", "EN_PROCESO")


def _cargas(db: Database, now: datetime, aplicar: bool) -> dict:
    """Detecta con una foto y corrige fila a fila SIN carreras: se bloquea la fila (PostgreSQL: FOR
    UPDATE espera a que termine una asignación en curso del motor) y se recalcula con UNA sentencia
    que cuenta en ese momento (no con la foto). Así nunca se escribe un número viejo."""
    cambios = []
    with db.session() as s:
        vivas = list(s.execute(select(Assignment.operator_id, Assignment.slot_id, Assignment.quantity)
                               .where(Assignment.status.in_(LIVE))))
        por_op, por_slot = Counter(), Counter()
        for oid, sid, q in vivas:
            por_op[oid] += 1
            por_slot[sid] += q
        ops = [(o.id, o.current_load) for o in s.scalars(select(Operator)) if o.current_load != por_op.get(o.id, 0)]
        sls = [(x.id, x.held) for x in s.scalars(select(SlotState)) if x.held != por_slot.get(x.id, 0)]
    for oid, antes in ops:
        if aplicar:
            with db.session() as s:
                s.execute(select(Operator.id).where(Operator.id == oid).with_for_update())
                cuenta = (select(func.count()).select_from(Assignment)
                          .where(Assignment.operator_id == oid, Assignment.status.in_(LIVE)).scalar_subquery())
                s.execute(update(Operator).where(Operator.id == oid).values(current_load=cuenta))
                despues = s.scalar(select(Operator.current_load).where(Operator.id == oid))
        else:
            despues = por_op.get(oid, 0)
        cambios.append({"operador": oid, "antes": antes, "despues": despues})
    for sid, antes in sls:
        if aplicar:
            with db.session() as s:
                s.execute(select(SlotState.id).where(SlotState.id == sid).with_for_update())
                suma = (select(func.coalesce(func.sum(Assignment.quantity), 0)).where(
                    Assignment.slot_id == sid, Assignment.status.in_(LIVE)).scalar_subquery())
                s.execute(update(SlotState).where(SlotState.id == sid).values(held=suma))
                despues = s.scalar(select(SlotState.held).where(SlotState.id == sid))
        else:
            despues = por_slot.get(sid, 0)
        cambios.append({"recurso": sid, "retenido_antes": antes, "retenido_despues": despues})
    if aplicar and cambios:
        with db.session() as s:
            s.add(AuditLog(ts=now, username="motor", action="reparacion_cargas", target="operadores/recursos",
                           detail={"cambios": cambios[:50], "total": len(cambios)}))
    return {"pendientes": len(cambios), "detalle": cambios[:20]}


def _entregas_atascadas(db: Database, now: datetime, aplicar: bool) -> dict:
    from datetime import timedelta
    with db.session() as s:
        n = s.scalar(select(func.count()).select_from(Delivery).where(
            Delivery.status == "ENVIANDO", (Delivery.claimed_at.is_(None)) | (Delivery.claimed_at < now - timedelta(seconds=600))))
    if aplicar and n:
        from .engine.alerts import requeue_stale_claims
        n = requeue_stale_claims(db, now, 600)
        with db.session() as s:
            s.add(AuditLog(ts=now, username="motor", action="reparacion_entregas", target="entregas", detail={"reencoladas": n}))
    return {"pendientes": n}


def _entregas_huerfanas(db: Database, now: datetime, aplicar: bool) -> dict:
    with db.session() as s:
        ids = list(s.scalars(select(Delivery.id).outerjoin(Alert, Alert.id == Delivery.alert_id).where(Alert.id.is_(None))))
        if aplicar and ids:
            s.execute(delete(Delivery).where(Delivery.id.in_(ids)))
    return {"pendientes": len(ids), "detalle": ids[:20]}


REPARACIONES = {
    "cargas": {"titulo": "Recalcular la carga de operadores y lo retenido en cada recurso", "segura": True,
               "explicacion": "Son contadores que se deducen de las asignaciones vivas; si no cuadran se corrigen.",
               "fn": _cargas},
    "entregas_atascadas": {"titulo": "Devolver a la cola notificaciones atascadas en «enviando» (más de 10 min)",
                           "segura": True, "explicacion": "El despachador las vuelve a intentar; el receptor "
                           "descarta duplicados con X-Id-Alerta.", "fn": _entregas_atascadas},
    "entregas_huerfanas": {"titulo": "Borrar entregas de notificaciones cuya alerta ya no existe", "segura": False,
                           "explicacion": "Borra filas. Solo con confirmación de un administrador.", "fn": _entregas_huerfanas},
}


def revisar(db: Database, now: datetime) -> list[dict]:
    out = []
    for clave, r in REPARACIONES.items():
        res = r["fn"](db, now, False)
        out.append({"clave": clave, "titulo": r["titulo"], "segura": r["segura"], "explicacion": r["explicacion"], **res})
    return out


def aplicar(db: Database, clave: str, now: datetime, usuario: str, confirmo: bool = False, motivo: str = "") -> dict:
    r = REPARACIONES.get(clave)
    if r is None:
        raise KeyError(clave)
    if not r["segura"] and not (confirmo and len(motivo.strip()) >= 5):
        raise PermissionError("Esta reparación borra o decide datos: requiere confirmación y motivo de un administrador")
    res = r["fn"](db, now, True)
    from . import security
    with db.session() as s:
        security.audit(s, usuario, "reparacion_aplicada", clave, {"segura": r["segura"], "motivo": motivo[:200],
                                                                   "pendientes_antes": res["pendientes"]})
    return {"clave": clave, "aplicada": True, **res}


def aplicar_seguras(db: Database, now: datetime) -> dict:
    """La llama el autodiagnóstico periódico: solo las seguras y solo si hace falta."""
    hechas = {}
    for clave, r in REPARACIONES.items():
        if r["segura"] and r["fn"](db, now, False)["pendientes"]:
            hechas[clave] = aplicar(db, clave, now, "motor (autodiagnóstico)")["pendientes"]
    return hechas
