"""v2.1 · Una sola autoridad de asignación por expediente (cola general del motor o plan diario).

Regla de propiedad (explícita y transaccional):

  - La PROPIEDAD la marca el estado de la solicitud del motor (`requests.status`), que ya es la
    pieza que el motor consulta con un UPDATE condicional antes de asignar (`status IN (ELEGIBLE,
    REINTENTO)`). Un expediente del plan tiene su solicitud en `EN_PLAN`: el motor no la puede
    asignar porque no es MATCHABLE, sin tocar el código de asignación.
  - Entrar al plan = UPDATE condicional `ELEGIBLE|REINTENTO -> EN_PLAN` en LA MISMA transacción que
    crea el elemento. Si el motor la asignó un instante antes (ASIGNADO), el UPDATE no afecta filas y
    el expediente NO entra al plan: gana quien llegó primero, nunca los dos.
  - Mientras está en el plan, solo el planificador la saca: `EN_PLAN -> EN_VENTANILLA` cuando un
    operador prepara la operación de una coincidencia; `EN_PLAN -> ELEGIBLE` al retirarla del plan,
    al terminar la jornada o si el expediente deja de ser elegible.
  - Una operación del plan que termina sin cita vuelve AL PLAN (`EN_VENTANILLA -> EN_PLAN`,
    elemento `EN_ESPERA`), no a la cola general, en la misma transacción.

`reconciliar()` corrige lo que pudo quedar de antes de esta regla (planes creados con v2.1 inicial)
y los cierres de jornada; es idempotente y lo llama el mantenimiento del motor.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select, update

from ..captura import expedientes as ex
from ..captura import zona
from ..engine import assignment as asg
from ..models import Expediente, PlanDiario, PlanElemento, Request

log = logging.getLogger("monitoreo.planificador")
ACTOR = "planificador"
FIN_JORNADA = "FIN_JORNADA"


class SinPropiedad(Exception):
    def __init__(self, mensaje: str, estado: str | None = None):
        super().__init__(mensaje)
        self.mensaje, self.estado = mensaje, estado


def tomar_para_plan(s, exp: Expediente, now: datetime) -> None:
    """Pasa la solicitud del expediente al plan (misma transacción que el alta del elemento)."""
    req = s.get(Request, exp.request_id) if exp.request_id else None
    if req is None:
        raise SinPropiedad("el expediente no tiene solicitud en la cola del motor")
    try:
        if req.status == asg.PENDIENTE:
            asg.validate_request(s, req, now, actor=ACTOR)
        if req.status not in asg.MATCHABLE:
            raise SinPropiedad(_motivo_ocupada(req.status), req.status)
        asg.transition(s, req, asg.EN_PLAN, ACTOR, "incluido en el plan diario", now)
    except asg.TransitionError as e:      # incluye ConcurrentChange: el motor se adelantó
        raise SinPropiedad("la cola general lo tomó en ese instante; vuelve a intentarlo", req.status) from e


def _motivo_ocupada(estado: str) -> str:
    if estado in (asg.ASIGNADO, asg.EN_PROCESO):
        return f"la cola general ya lo asignó a un operador ({estado})"
    if estado == asg.EN_VENTANILLA:
        return "se está atendiendo en ventanilla"
    if estado == asg.EN_PLAN:
        return "El expediente ya está en otro plan vivo"
    return f"su solicitud no está en la cola ({estado})"


def devolver_a_cola(s, expediente_id: int, now: datetime, motivo: str) -> bool:
    """EN_PLAN -> ELEGIBLE (el expediente sale del plan y vuelve a la cola general)."""
    exp = s.get(Expediente, expediente_id)
    req = s.get(Request, exp.request_id) if exp and exp.request_id else None
    if req is None or req.status != asg.EN_PLAN:
        return False
    try:
        asg.transition(s, req, asg.ELEGIBLE, ACTOR, f"sale del plan diario: {motivo}"[:200], now)
    except asg.TransitionError:
        return False
    return True


def regresar_al_plan(s, expediente_id: int, req: Request, actor: str, now: datetime, motivo: str) -> bool:
    """Una operación de un elemento del plan terminó sin cita: la solicitud vuelve a EN_PLAN y el
    elemento a EN_ESPERA en la misma transacción. Devuelve False si el expediente no está en un plan."""
    el = s.scalar(select(PlanElemento).where(PlanElemento.activo_expediente_id == expediente_id,
                                             PlanElemento.estado.in_([_pl().ASIGNADO, _pl().PREPARADO])))
    if el is None or req.status != asg.EN_VENTANILLA:
        return False
    asg.transition(s, req, asg.EN_PLAN, actor, f"operación del plan sin cita: {motivo}"[:200], now)
    pe = PlanElemento.__table__
    s.execute(update(pe).where(pe.c.id == el.id).values(
        estado=_pl().EN_ESPERA, evento_id=None, fecha_asignada=None, sucursal_asignada=None, operador_id=None,
        reservacion_id=None, version=pe.c.version + 1, updated_at=now,
        explicacion={**(el.explicacion or {}), "devuelto": {"motivo": f"la operación terminó sin cita ({motivo})"[:160],
                                                           "en": now.isoformat() + "Z"}}))
    ex.evento(s, expediente_id, "PLAN_DEVUELTO_A_ESPERA", actor, now, motivo=motivo[:80])
    return True


def _pl():
    from . import plan
    return plan


def reconciliar(db, now: datetime) -> dict:
    """Idempotente. (1) elementos vivos cuya solicitud sigue en la cola -> EN_PLAN; (2) elementos en
    espera de expedientes que ya no son elegibles -> retirados; (3) jornadas pasadas -> cierre y los
    expedientes no atendidos vuelven a la cola; (4) solicitudes EN_PLAN sin elemento vivo -> cola."""
    pl = _pl()
    out = {"a_plan": 0, "conflictos": 0, "retirados": 0, "fin_jornada": 0, "huerfanas": 0}
    hoy = zona.hoy(now)
    with db.session() as s:
        for p in s.scalars(select(PlanDiario).where(PlanDiario.estado == "ABIERTO", PlanDiario.fecha_jornada < hoy)):
            for el in s.scalars(select(PlanElemento).where(PlanElemento.plan_id == p.id,
                                                           PlanElemento.estado.in_([pl.EN_ESPERA, pl.ASIGNADO]))):
                el.estado, el.activo_expediente_id, el.version, el.updated_at = FIN_JORNADA, None, el.version + 1, now
                el.explicacion = {**(el.explicacion or {}), "fin_jornada": {"en": now.isoformat() + "Z"}}
                devolver_a_cola(s, el.expediente_id, now, "terminó la jornada")
                ex.evento(s, el.expediente_id, "PLAN_FIN_JORNADA", ACTOR, now)
                out["fin_jornada"] += 1
            if not s.scalar(select(PlanElemento.id).where(PlanElemento.plan_id == p.id,
                                                          PlanElemento.estado == pl.PREPARADO).limit(1)):
                p.estado, p.updated_at = "CERRADO", now
    with db.session() as s:
        filas = s.execute(select(PlanElemento, Expediente, Request)
                          .join(Expediente, Expediente.id == PlanElemento.expediente_id)
                          .outerjoin(Request, Request.id == Expediente.request_id)
                          .where(PlanElemento.estado.in_([pl.EN_ESPERA, pl.ASIGNADO]))).all()
        for el, exp, req in filas:
            if pl.motivo_inelegible(exp) and not (exp.estado == ex.LISTO):
                el.estado, el.activo_expediente_id, el.version, el.updated_at = pl.RETIRADO, None, el.version + 1, now
                el.explicacion = {**(el.explicacion or {}), "retirado": {
                    "motivo": f"el expediente dejó de ser elegible ({exp.estado})", "en": now.isoformat() + "Z"}}
                devolver_a_cola(s, exp.id, now, "expediente no elegible")
                out["retirados"] += 1
                continue
            if req is None or req.status == asg.EN_PLAN:
                continue
            if req.status in asg.MATCHABLE or req.status == asg.PENDIENTE:
                try:
                    with s.begin_nested():
                        tomar_para_plan(s, exp, now)
                    out["a_plan"] += 1
                except SinPropiedad:
                    pass
            elif req.status in (asg.ASIGNADO, asg.EN_PROCESO):
                out["conflictos"] += 1          # de antes de la regla: la conserva la cola (ya la asignó)
    with db.session() as s:
        vivos = set(s.scalars(select(PlanElemento.activo_expediente_id).where(PlanElemento.activo_expediente_id.is_not(None))))
        for exp_id, in s.execute(select(Expediente.id).join(Request, Request.id == Expediente.request_id)
                                 .where(Request.status == asg.EN_PLAN)).all():
            if exp_id not in vivos and devolver_a_cola(s, exp_id, now, "sin elemento vivo en un plan"):
                out["huerfanas"] += 1
    if any(out.values()):
        log.info("propiedad del plan reconciliada: %s", out)
    return out
