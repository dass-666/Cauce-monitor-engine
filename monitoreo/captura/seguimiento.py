"""Seguimiento operativo (v1.5): enlaza los expedientes con las asignaciones del motor.

No duplica la cola ni la asignación: el motor asigna la `Request` de cada expediente con su
lógica de siempre (prioridad, antigüedad, carga del operador, UPDATE condicionales). Aquí solo
se refleja el resultado en el expediente y se emiten los eventos normalizados:

- asignación viva nueva           -> expediente ASIGNADO + DISPONIBILIDAD_DETECTADA
- asignación perdida sin tomarse  -> expediente PENDIENTE (vuelve a la cola, sin operador)
- solicitud EXPIRADA (intentos)   -> expediente FALLIDO
- expediente cerrado              -> su solicitud se cancela (nadie más la recibe)
Además ejecuta la recuperación de reservaciones (`reservaciones.recuperar`).
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select

from ..db import Database
from ..engine import assignment as asg
from ..models import Assignment, ChangeEvent, Expediente, Request, Reservacion, SlotState
from . import expedientes as ex
from . import reservaciones as rs

log = logging.getLogger("monitoreo.captura")


def sincronizar(db: Database, now: datetime) -> dict:
    out = {"asignados": 0, "devueltos": 0, "fallidos": 0, "solicitudes_cerradas": 0}
    cerrar_req: list[int] = []
    with db.session() as s:
        filas = s.execute(
            select(Expediente, Request.status).join(Request, Request.id == Expediente.request_id)
            .where(Expediente.estado.in_(ex.ACTIVOS)
                   | (Expediente.estado.in_(ex.CERRADOS) & Request.status.not_in(list(asg.TERMINAL))))).all()
        for exp, req_status in filas:
            if exp.estado in ex.CERRADOS:
                if req_status not in asg.TERMINAL:
                    cerrar_req.append(exp.request_id)
                continue
            live = s.scalar(select(Assignment).where(Assignment.request_id == exp.request_id,
                                                     Assignment.status.in_(["ACTIVA", "EN_PROCESO"])))
            if exp.estado == ex.PENDIENTE and live is not None:
                slot = s.get(SlotState, live.slot_id)
                exp.estado, exp.operator_id, exp.updated_at = ex.ASIGNADO, live.operator_id, now
                exp.version += 1
                # Evento de cambio que originó la oferta: el de la asignación o, si se asignó en la
                # reevaluación periódica (la solicitud aún no era elegible), el último del recurso.
                ce = live.change_event_id or s.scalar(
                    select(ChangeEvent.id).where(ChangeEvent.slot_id == live.slot_id)
                    .order_by(ChangeEvent.id.desc()).limit(1))
                ex.evento(s, exp.id, "DISPONIBILIDAD_DETECTADA", "motor", now, assignment_id=live.id,
                          change_event_id=ce, recurso=slot.name if slot else None,
                          libres=(slot.available - slot.held) if slot else None, operador=live.operator_id)
                out["asignados"] += 1
            elif exp.estado in (ex.ASIGNADO, ex.EN_CAPTURA) and live is None:
                viva = s.scalar(select(Reservacion.estado).where(Reservacion.viva_expediente_id == exp.id))
                if viva in (rs.RETENIDA, rs.CONFIRMANDO):
                    continue      # hay una confirmación en curso: no se le quita al operador
                if req_status == "EXPIRADO":
                    ex.cerrar(s, exp, ex.FALLIDO, "motor", now, nota="sin reservar tras los intentos permitidos")
                    out["fallidos"] += 1
                elif req_status in asg.MATCHABLE + (asg.PENDIENTE, asg.ELEGIBLE):
                    if viva in (rs.PREPARADA, rs.DATOS_ENVIADOS):
                        r = s.scalar(select(Reservacion).where(Reservacion.viva_expediente_id == exp.id))
                        r.estado, r.viva_expediente_id, r.updated_at = rs.CANCELADA, None, now
                        r.error_codigo, r.error = "ASIGNACION_LIBERADA", "la asignación volvió a la cola"
                    ex.evento(s, exp.id, "ASIGNACION_LIBERADA", "motor", now, operador_anterior=exp.operator_id)
                    exp.estado, exp.operator_id, exp.updated_at = ex.PENDIENTE, None, now
                    exp.version += 1
                    out["devueltos"] += 1
            elif exp.estado == ex.PENDIENTE and req_status == "EXPIRADO":
                ex.cerrar(s, exp, ex.FALLIDO, "motor", now, nota="sin disponibilidad tras los intentos permitidos")
                out["fallidos"] += 1
    for rid in cerrar_req:          # fuera de la transacción anterior (no se anidan sesiones)
        out["solicitudes_cerradas"] += bool(asg.cancel_request(db, rid, now, "captura"))
    return out


def mantenimiento(db: Database, now: datetime) -> dict:
    """Lo ejecuta el planificador (líder) cada pocos segundos, en su propia tarea."""
    res: dict = {}
    try:
        res["recuperacion"] = rs.recuperar(db, now)
    except Exception as e:  # noqa: BLE001 - no debe parar al motor
        log.warning("recuperación de reservaciones falló: %s", e)
        res["recuperacion_error"] = str(e)[:200]
    res["sincronizacion"] = sincronizar(db, now)
    return res
