"""Seguimiento operativo (v1.5, estados v1.6): enlaza los expedientes con las asignaciones del motor.

No duplica la cola ni la asignación: el motor asigna la `Request` de cada expediente LISTO con su
lógica de siempre (prioridad, antigüedad, preferencia de sucursal, carga y sucursales del
operador, UPDATE condicionales). Aquí solo se refleja el resultado en el expediente:

- LISTO + asignación viva nueva            -> ASIGNADO + DISPONIBILIDAD_DETECTADA
- ASIGNADO + asignación viva de OTRO operador (cancelada y reasignada entre dos pasadas)
                                           -> el expediente pasa al operador nuevo
- asignación perdida sin confirmar          -> LISTO (vuelve a la cola, sin operador)
- solicitud EXPIRADA (intentos agotados)    -> REQUIERE_REVISION (lo decide una persona)
- expediente que ya no espera (confirmado, cerrado, en revisión) -> su solicitud se cancela
- operador que rechaza con motivo           -> `rechazar()` (reglas de recuperación del motor)
Además ejecuta la validación de expedientes NUEVO, la recuperación de reservaciones y la
política de retención (con su propio intervalo).
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime

from sqlalchemy import select

from ..db import Database
from ..engine import assignment as asg
from ..engine.params import get_params
from ..models import Assignment, ChangeEvent, Expediente, Request, Reservacion, SlotState
from . import expedientes as ex
from . import reservaciones as rs

log = logging.getLogger("monitoreo.captura")
_ultima_retencion = {"t": 0.0}
SIN_OPERACION = (rs.PREPARADA, rs.DATOS_ENVIADOS, rs.FECHA_ELEGIDA)


def _descartar_viva(s, exp_id: int, now: datetime, codigo: str, mensaje: str) -> None:
    r = s.scalar(select(Reservacion).where(Reservacion.viva_expediente_id == exp_id))
    if r is not None and r.estado in SIN_OPERACION:
        r.estado, r.viva_expediente_id, r.updated_at = rs.CANCELADA, None, now
        r.error_codigo, r.error = codigo, mensaje


def sincronizar(db: Database, now: datetime) -> dict:
    out = {"asignados": 0, "devueltos": 0, "revision": 0, "solicitudes_cerradas": 0}
    cerrar_req: list[int] = []
    with db.session() as s:
        filas = s.execute(
            select(Expediente, Request.status).join(Request, Request.id == Expediente.request_id)
            .where(Expediente.estado.in_([ex.LISTO, *ex.OPERACION_ACTIVA])
                   | (Expediente.estado.not_in([ex.LISTO, *ex.OPERACION_ACTIVA])
                      & Request.status.not_in(list(asg.TERMINAL))))).all()
        for exp, req_status in filas:
            if exp.estado not in (ex.LISTO, *ex.OPERACION_ACTIVA):
                if req_status not in asg.TERMINAL and not rs.viva_bloquea(s, exp.id):
                    cerrar_req.append(exp.request_id)
                continue
            live = s.scalar(select(Assignment).where(Assignment.request_id == exp.request_id,
                                                     Assignment.status.in_(["ACTIVA", "EN_PROCESO"])))
            if exp.estado == ex.LISTO and live is not None:
                slot = s.get(SlotState, live.slot_id)
                ex.cambiar_estado(s, exp, ex.ASIGNADO, "motor", now, operador=live.operator_id)
                exp.operator_id = live.operator_id
                # Evento de cambio que originó la oferta: el de la asignación o, si se asignó en la
                # reevaluación periódica (la solicitud aún no era elegible), el último del recurso.
                ce = live.change_event_id or s.scalar(
                    select(ChangeEvent.id).where(ChangeEvent.slot_id == live.slot_id)
                    .order_by(ChangeEvent.id.desc()).limit(1))
                ex.evento(s, exp.id, "DISPONIBILIDAD_DETECTADA", "motor", now, assignment_id=live.id,
                          change_event_id=ce, recurso=slot.name if slot else None,
                          sucursal=(slot.attributes or {}).get("sucursal") if slot else None,
                          libres=(slot.available - slot.held) if slot else None, operador=live.operator_id)
                out["asignados"] += 1
            elif exp.estado == ex.ASIGNADO and live is not None and live.operator_id != exp.operator_id:
                # v1.6 (defecto del laboratorio): entre dos pasadas el motor canceló la asignación
                # (el recurso se agotó) y volvió a asignar la solicitud a OTRO operador. El expediente
                # debe seguir a la asignación viva; si no, el anterior lo ve sin poder tomarlo y el
                # nuevo no lo ve. Solo en ASIGNADO: nadie ha empezado a capturarlo todavía.
                if rs.viva_bloquea(s, exp.id):
                    continue
                _descartar_viva(s, exp.id, now, "ASIGNACION_CAMBIADA", "la asignación pasó a otro operador")
                ex.evento(s, exp.id, "ASIGNACION_CAMBIADA", "motor", now, assignment_id=live.id,
                          operador_anterior=exp.operator_id, operador=live.operator_id)
                exp.operator_id, exp.updated_at = live.operator_id, now
                out["reasignados"] = out.get("reasignados", 0) + 1
            elif exp.estado in ex.OPERACION_ACTIVA and live is None:
                if rs.viva_bloquea(s, exp.id):
                    continue      # hay una confirmación en curso o en verificación: no se le quita al operador
                if req_status == "EXPIRADO":
                    _descartar_viva(s, exp.id, now, "SOLICITUD_EXPIRADA", "intentos agotados")
                    ex.a_revision(s, exp, "motor", now, "INTENTOS_AGOTADOS")
                    out["revision"] += 1
                elif req_status in asg.MATCHABLE + (asg.PENDIENTE, asg.ELEGIBLE):
                    _descartar_viva(s, exp.id, now, "ASIGNACION_LIBERADA", "la asignación volvió a la cola")
                    ex.evento(s, exp.id, "ASIGNACION_LIBERADA", "motor", now, operador_anterior=exp.operator_id)
                    ex.cambiar_estado(s, exp, ex.LISTO, "motor", now, motivo="asignacion_liberada")
                    exp.operator_id = None
                    out["devueltos"] += 1
                elif req_status in asg.TERMINAL:
                    _descartar_viva(s, exp.id, now, "SOLICITUD_CERRADA", "la solicitud del motor se cerró")
                    ex.a_revision(s, exp, "motor", now, f"SOLICITUD_{req_status}")
                    out["revision"] += 1
            elif exp.estado == ex.LISTO and req_status == "EXPIRADO":
                ex.a_revision(s, exp, "motor", now, "SIN_DISPONIBILIDAD")
                out["revision"] += 1
    for rid in cerrar_req:          # fuera de la transacción anterior (no se anidan sesiones)
        out["solicitudes_cerradas"] += bool(asg.cancel_request(db, rid, now, "captura"))
    return out


# ---------------------------------------------------------------- rechazo de una asignación con motivo
_PII = re.compile(r"(\d[\d\s-]{6,}\d)|@")


def rechazar(db: Database, exp_id: int, user, motivo: str, nota: str, now: datetime) -> dict:
    """El operador rechaza la asignación de un expediente. Se aplican las reglas del motor:
    - DATOS_DUDOSOS: el expediente pasa a REQUIERE_REVISION y su solicitud se cancela.
    - cualquier otro: la solicitud vuelve a la cola (o se agota si llegó al máximo de intentos)
      y NO se vuelve a ofrecer a ese operador (asignación DECLINADA)."""
    if motivo not in ex.MOTIVOS_RECHAZO:
        raise ex.ErrorExpediente(422, "Motivo de rechazo no válido", {"motivo": "elige un motivo de la lista"})
    nota = " ".join(str(nota or "").split())[:200]
    if motivo == "OTRO" and not nota:
        raise ex.ErrorExpediente(422, "Explica brevemente el motivo", {"nota": "obligatoria con 'OTRO'"})
    if _PII.search(nota):
        raise ex.ErrorExpediente(422, "La nota no debe incluir teléfonos, correos ni identificadores",
                                 {"nota": "sin datos personales"})
    actor = user.username[:64]
    with db.session() as s:
        exp = s.get(Expediente, exp_id)
        if exp is None or not ex.puede_ver(user, exp):
            raise ex.ErrorExpediente(404, "Expediente no encontrado")
        if exp.estado not in (ex.ASIGNADO, ex.CAPTURA):
            raise ex.ErrorExpediente(409, f"Solo se rechaza una asignación ASIGNADA o en captura (está {exp.estado})")
        if rs.viva_bloquea(s, exp_id):
            raise ex.ErrorExpediente(409, "Hay una reservación apartada o en verificación: termínala o descártala")
        live = s.scalar(select(Assignment).where(Assignment.request_id == exp.request_id,
                                                 Assignment.status.in_(["ACTIVA", "EN_PROCESO"])))
        if live is None:
            raise ex.ErrorExpediente(409, "La asignación ya no está viva")
        aid, op_id, rid = live.id, live.operator_id, exp.request_id
        params = get_params(s)
    estado_asg = "RECHAZADA" if motivo == "DATOS_DUDOSOS" else "DECLINADA"
    if not asg.reject_assignment(db, aid, None if user.is_admin else op_id, now, actor, params,
                                 reason=f"{motivo}: {ex.MOTIVOS_RECHAZO[motivo]}", status=estado_asg):
        raise ex.ErrorExpediente(409, "La asignación cambió mientras tanto")
    with db.session() as s:
        exp = s.get(Expediente, exp_id)
        _descartar_viva(s, exp_id, now, "ASIGNACION_RECHAZADA", motivo)
        ex.evento(s, exp_id, "ASIGNACION_RECHAZADA", actor, now, assignment_id=aid, motivo=motivo, nota=nota,
                  operador=op_id)
        if motivo == "DATOS_DUDOSOS":
            ex.a_revision(s, exp, actor, now, "DATOS_DUDOSOS")
        elif exp.estado in ex.OPERACION_ACTIVA:
            req = s.get(Request, rid)
            if req.status == "EXPIRADO":
                ex.a_revision(s, exp, actor, now, "INTENTOS_AGOTADOS")
            else:
                ex.cambiar_estado(s, exp, ex.LISTO, actor, now, motivo="rechazada")
        exp.operator_id = None
        estado = exp.estado
    if motivo == "DATOS_DUDOSOS":
        asg.cancel_request(db, rid, now, actor)
    return {"ok": True, "estado": estado, "motivo": motivo, "asignacion": estado_asg}


def mantenimiento(db: Database, now: datetime) -> dict:
    """Lo ejecuta el planificador (líder) cada pocos segundos, en su propia tarea."""
    res: dict = {}
    try:
        res["validacion"] = ex.validar_pendientes(db, now)
    except Exception as e:  # noqa: BLE001 - no debe parar al motor
        log.warning("validación de expedientes falló: %s", e)
    try:
        res["recuperacion"] = rs.recuperar(db, now)
    except Exception as e:  # noqa: BLE001 - no debe parar al motor
        log.warning("recuperación de reservaciones falló: %s", e)
        res["recuperacion_error"] = str(e)[:200]
    res["sincronizacion"] = sincronizar(db, now)
    try:
        with db.session() as s:
            cada = float(get_params(s)["retencion_expedientes_cada_s"])
        if cada > 0 and time.monotonic() - _ultima_retencion["t"] >= cada:
            from . import retencion
            _ultima_retencion["t"] = time.monotonic()
            res["retencion"] = retencion.aplicar(db, now, actor="motor")
    except ImportError:
        pass
    except Exception as e:  # noqa: BLE001
        log.warning("política de retención falló: %s", e)
    return res
