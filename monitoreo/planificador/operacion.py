"""v2.1 · P4 · De la coincidencia a la operación preparada (y de vuelta a la espera si no se logra).

`preparar(elemento)`: un operador toma la coincidencia. UPDATE condicional ASIGNADO -> OPERACION_PREPARADA
(si dos operadores lo intentan a la vez, gana uno y el otro recibe 409), el expediente pasa a ese
operador por la misma vía que ventanilla (`ventanilla._tomar`, UPDATE condicional sobre la solicitud
del motor) y se abre la reservación con `reservaciones.preparar`. A partir de ahí es el flujo normal:
el operador teclea los campos manuales, elige el día asignado y confirma; CONFIRMADA solo con folio
comprobado por la API del portal.

`sincronizar()`: refleja en el plan lo que pasó con la reservación (CONFIRMADA -> CONFIRMADO; descartada
o el expediente volvió a la cola -> EN_ESPERA, conservando el expediente en la lista del día).
`devolver(elemento)`: el día ya no se pudo usar (p. ej. el portal lo dio por agotado): EN_ESPERA.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select, update

from ..captura import expedientes as ex
from ..captura import reservaciones as rs
from ..captura import ventanilla as vt
from ..models import Expediente, ObservacionDia, PlanElemento, Reservacion
from . import plan as pl

log = logging.getLogger("monitoreo.planificador")


class ErrorOperacion(Exception):
    def __init__(self, http: int, mensaje: str, detalle: dict | None = None):
        super().__init__(mensaje)
        self.http, self.mensaje, self.detalle = http, mensaje, detalle or {}


def _actor(user) -> str:
    return f"user:{user.username}"


def _a_espera(s, el: PlanElemento, now: datetime, motivo: str, desde: tuple) -> bool:
    pe = PlanElemento.__table__
    ok = s.execute(update(pe).where(pe.c.id == el.id, pe.c.estado.in_(list(desde))).values(
        estado=pl.EN_ESPERA, evento_id=None, fecha_asignada=None, sucursal_asignada=None, operador_id=None,
        reservacion_id=None, version=pe.c.version + 1, updated_at=now,
        explicacion={**(el.explicacion or {}), "devuelto": {"motivo": motivo, "en": now.isoformat() + "Z"}})).rowcount
    if ok:
        ex.evento(s, el.expediente_id, "PLAN_DEVUELTO_A_ESPERA", "motor", now, motivo=motivo[:80])
    return bool(ok)


def preparar(db, elemento_id: int, user, now: datetime) -> dict:
    if user.operator_id is None:
        raise ErrorOperacion(403, "Para preparar la operación tu usuario debe estar vinculado a un operador")
    with db.session() as s:
        el = s.get(PlanElemento, elemento_id)
        if el is None:
            raise ErrorOperacion(404, "Elemento no encontrado")
        if el.estado == pl.PREPARADO and el.operador_id == user.operator_id and el.reservacion_id:
            return {"elemento": pl.vista_elemento(el), "reservacion_id": el.reservacion_id, "retomada": True}
        if el.estado != pl.ASIGNADO:
            raise ErrorOperacion(409, f"La coincidencia ya no está disponible (está {el.estado})", {"estado": el.estado})
        dia = s.get(ObservacionDia, f"{el.sucursal_asignada}|{el.tramite}|{el.fecha_asignada}")
        if dia is None or dia.estado != "DISPONIBLE":
            _a_espera(s, el, now, "el día dejó de estar disponible antes de preparar la operación", (pl.ASIGNADO,))
            raise ErrorOperacion(409, "El día ya no está disponible: el expediente vuelve a la espera",
                                 {"codigo": "DIA_DESAPARECIO"})
        pe = PlanElemento.__table__
        if s.execute(update(pe).where(pe.c.id == el.id, pe.c.estado == pl.ASIGNADO, pe.c.version == el.version).values(
                estado=pl.PREPARADO, operador_id=user.operator_id, version=pe.c.version + 1, updated_at=now)).rowcount != 1:
            raise ErrorOperacion(409, "Otro operador acaba de tomar esta coincidencia", {"codigo": "TOMADA"})
        exp_id, fecha, sucursal = el.expediente_id, el.fecha_asignada, el.sucursal_asignada
    try:
        vt._tomar(db, user, exp_id, now)
        r = rs.preparar(db, exp_id, user, now)
    except (vt.ErrorVentanilla, rs.ErrorReservacion) as e:
        with db.session() as s:
            el = s.get(PlanElemento, elemento_id)
            pe = PlanElemento.__table__
            s.execute(update(pe).where(pe.c.id == elemento_id, pe.c.estado == pl.PREPARADO)
                      .values(estado=pl.ASIGNADO, operador_id=None, version=pe.c.version + 1, updated_at=now))
        raise ErrorOperacion(e.http, e.mensaje, getattr(e, "detalle", {}) or {}) from e
    rid = r["reservacion"]["id"]
    with db.session() as s:
        el = s.get(PlanElemento, elemento_id)
        el.reservacion_id, el.updated_at = rid, now
        ex.evento(s, exp_id, "PLAN_OPERACION_PREPARADA", _actor(user), now, reservacion_id=rid, fecha=fecha,
                  sucursal=sucursal)
        return {"elemento": pl.vista_elemento(el), "reservacion_id": rid, "revision": r["revision"],
                "fecha": fecha, "sucursal": sucursal, "retomada": False}


def devolver(db, elemento_id: int, user, now: datetime, motivo: str = "sin disponibilidad") -> dict:
    with db.session() as s:
        el = s.get(PlanElemento, elemento_id)
        if el is None:
            raise ErrorOperacion(404, "Elemento no encontrado")
        if el.estado not in (pl.ASIGNADO, pl.PREPARADO):
            raise ErrorOperacion(409, f"El elemento está {el.estado}")
        if el.estado == pl.PREPARADO and not user.is_admin and el.operador_id != user.operator_id:
            raise ErrorOperacion(403, "Esta operación la tiene otro operador")
        exp_id, estado = el.expediente_id, el.estado
    if estado == pl.PREPARADO:
        vt._devolver_a_la_cola(db, user, exp_id, now, motivo[:80])
    with db.session() as s:
        el = s.get(PlanElemento, elemento_id)
        if not _a_espera(s, el, now, motivo[:120], (pl.ASIGNADO, pl.PREPARADO)):
            raise ErrorOperacion(409, "El elemento cambió mientras tanto")
        s.flush()
        s.refresh(el)
        return pl.vista_elemento(el)


def sincronizar(db, now: datetime) -> dict:
    """La reservación manda: CONFIRMADA (con evidencia) -> CONFIRMADO; intento cerrado o expediente
    de vuelta en la cola -> EN_ESPERA. Una confirmación incierta sigue OPERACION_PREPARADA."""
    out = {"confirmados": 0, "devueltos": 0}
    with db.session() as s:
        for el in s.scalars(select(PlanElemento).where(PlanElemento.estado == pl.PREPARADO,
                                                       PlanElemento.reservacion_id.is_not(None))):
            res = s.get(Reservacion, el.reservacion_id)
            exp = s.get(Expediente, el.expediente_id)
            if res is not None and res.estado == rs.CONFIRMADA and res.folio:
                el.estado, el.activo_expediente_id, el.version, el.updated_at = pl.CONFIRMADO, None, el.version + 1, now
                el.explicacion = {**(el.explicacion or {}), "confirmado": {"folio": res.folio, "fecha": res.fecha,
                                                                            "sucursal": res.sucursal}}
                out["confirmados"] += 1
            elif (res is None or res.estado in (rs.CANCELADA, rs.EXPIRADA, rs.RECHAZADA)) and exp.estado == ex.LISTO:
                if _a_espera(s, el, now, f"la reservación terminó sin cita ({res.estado if res else 'sin intento'})",
                             (pl.PREPARADO,)):
                    out["devueltos"] += 1
    return out


def visibles_para(db, user) -> list[dict]:
    """Coincidencias que el operador puede preparar (y las suyas ya preparadas)."""
    with db.session() as s:
        out = []
        for el in s.scalars(select(PlanElemento).where(PlanElemento.estado.in_([pl.ASIGNADO, pl.PREPARADO]))
                            .order_by(PlanElemento.updated_at)):
            exp = s.get(Expediente, el.expediente_id)
            if el.estado == pl.PREPARADO and not user.is_admin and el.operador_id != user.operator_id:
                continue
            if el.estado == pl.ASIGNADO and not vt._disponible(s, exp, user)[0]:
                continue                       # otra región o sucursal que no atiende
            out.append(pl.vista_elemento(el, exp))
        return out
