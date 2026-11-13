"""API de captura y reservaciones de laboratorio (v1.5).

Permisos: el administrador ve y actúa sobre todo; un operador solo sobre los expedientes que el
motor le asignó (los demás responden 404, igual que si no existieran). Los listados siempre
enmascaran los datos personales; la ficha completa solo la ve el operador asignado o un
administrador, y cada consulta de la ficha completa queda auditada (sin valores).
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from .. import security
from ..captura import calendario as cal
from ..captura import expedientes as ex
from ..captura import reservaciones as rs
from ..captura.portal import contrato as contrato_actual
from ..db import Database, utcnow
from ..models import Expediente, ExpedienteEvento, Reservacion
from .deps import CurrentUser, current_user, get_db

router = APIRouter(prefix="/api")

_s = Field(default=None, max_length=128)


class ExpedienteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nombre: str | None = _s
    fecha_nacimiento: str | None = Field(default=None, max_length=10)
    telefono: str | None = Field(default=None, max_length=20)
    telefono_confirmacion: str | None = Field(default=None, max_length=20)
    correo: str | None = _s
    correo_confirmacion: str | None = _s
    region: str | None = Field(default=None, max_length=32)
    sucursal: str | None = Field(default=None, max_length=32)
    tipo_tramite: str | None = Field(default=None, max_length=32)
    operacion: str | None = Field(default=None, max_length=16)
    prioridad: int | None = None
    id_externo: str | None = Field(default=None, max_length=64)
    id_registro_previo: str | None = Field(default=None, max_length=32)


class ExpedientePatch(ExpedienteIn):
    version: int = Field(ge=1)


class EnviarIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revisado: bool = False


class SeleccionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    horario: str = Field(min_length=3, max_length=64)
    region: str | None = Field(default=None, max_length=32)
    sucursal: str | None = Field(default=None, max_length=32)


class ConfirmarIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmo: bool = False


def _err(e):
    if isinstance(e, ex.ErrorExpediente):
        raise HTTPException(e.http, {"mensaje": e.mensaje, "errores": e.errores})
    raise HTTPException(e.http, {"mensaje": e.mensaje, **e.detalle})


def _puede_capturar(user: CurrentUser) -> None:
    if not user.is_admin and user.operator_id is None:
        raise HTTPException(403, "Tu usuario no está vinculado a un operador")


# ======================================================================= resumen
@router.get("/captura/resumen")
def resumen(user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        q = ex.consulta_visible(user).subquery()
        por_estado = dict(s.execute(select(q.c.estado, func.count()).group_by(q.c.estado)).all())
        rq = select(Reservacion.estado, func.count()).group_by(Reservacion.estado)
        if not user.is_admin:
            rq = rq.where(Reservacion.operator_id == (user.operator_id or -1))
        res = dict(s.execute(rq).all())
    grupos = {"pendiente": 0, "en_proceso": 0, "confirmado": 0, "fallido": 0}
    for k, n in por_estado.items():
        grupos[ex.GRUPO.get(k, "pendiente")] += n
    return {"expedientes": {"por_estado": por_estado, "por_grupo": grupos},
            "reservaciones": {"por_estado": res}}


# ======================================================================= expedientes
@router.get("/expedientes")
def listar(estado: str | None = Query(None, max_length=16), grupo: str | None = Query(None, max_length=16),
           limit: int = Query(200, ge=1, le=2000), user: CurrentUser = Depends(current_user),
           db: Database = Depends(get_db)):
    with db.session() as s:
        q = ex.consulta_visible(user)
        if estado:
            q = q.where(Expediente.estado == estado)
        if grupo:
            q = q.where(Expediente.estado.in_([k for k, v in ex.GRUPO.items() if v == grupo]))
        q = q.order_by(Expediente.prioridad.asc(), Expediente.created_at.desc()).limit(limit)
        return [ex.vista(s, e) for e in s.scalars(q)]


@router.post("/expedientes", status_code=201)
def crear(body: ExpedienteIn, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    _puede_capturar(user)
    now = utcnow()
    try:
        out = ex.crear_seguro(db, body.model_dump(exclude_none=True), user.username, now)
    except ex.ErrorExpediente as e:
        _err(e)
    with db.session() as s:
        security.audit(s, user.username, "expediente_creado", out["codigo"],
                       {"campos": sorted(k for k, v in body.model_dump(exclude_none=True).items()
                                         if not k.endswith("_confirmacion"))})
    # Quien lo crea no necesariamente lo atenderá: se devuelve enmascarado.
    with db.session() as s:
        return ex.vista(s, s.get(Expediente, out["id"]))


@router.get("/expedientes/{eid}")
def ficha(eid: int, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        e = s.get(Expediente, eid)
        if e is None or not ex.puede_ver(user, e):
            raise HTTPException(404, "Expediente no encontrado")
        out = ex.vista(s, e, completa=True)
        out["reservaciones"] = [rs.vista(r) for r in s.scalars(
            select(Reservacion).where(Reservacion.expediente_id == eid).order_by(Reservacion.id.desc()))]
        out["historial"] = ex.historial(s, eid)
        security.audit(s, user.username, "expediente_consultado", e.codigo, {"datos_completos": True})
        return out


@router.patch("/expedientes/{eid}")
def modificar(eid: int, body: ExpedientePatch, user: CurrentUser = Depends(current_user),
              db: Database = Depends(get_db)):
    now = utcnow()
    try:
        with db.session() as s:
            e = s.get(Expediente, eid)
            if e is None or not ex.puede_ver(user, e):
                raise HTTPException(404, "Expediente no encontrado")
            d = body.model_dump(exclude_none=True)
            version = d.pop("version")
            campos = ex.modificar(s, e, d, version, user.username, now)
            security.audit(s, user.username, "expediente_modificado", e.codigo, {"campos": campos})
            return {**ex.vista(s, e, completa=True), "campos_modificados": campos}
    except ex.ErrorExpediente as err:
        _err(err)


@router.post("/expedientes/{eid}/cancelar")
def cancelar_expediente(eid: int, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    now = utcnow()
    with db.session() as s:
        e = s.get(Expediente, eid)
        if e is None or not ex.puede_ver(user, e):
            raise HTTPException(404, "Expediente no encontrado")
        if e.estado in ex.CERRADOS:
            raise HTTPException(409, {"mensaje": f"El expediente ya está {e.estado}"})
        viva = s.scalar(select(Reservacion).where(Reservacion.viva_expediente_id == eid))
        if viva is not None and viva.estado == rs.CONFIRMANDO:
            raise HTTPException(409, {"mensaje": "Hay una confirmación en verificación: espera su resultado"})
        if viva is not None:
            viva.estado, viva.viva_expediente_id, viva.updated_at = rs.CANCELADA, None, now
            viva.error_codigo, viva.error = "EXPEDIENTE_CANCELADO", "expediente cancelado"
        ex.cerrar(s, e, ex.CANCELADO, user.username, now, nota="cancelado por el usuario")
        security.audit(s, user.username, "expediente_cancelado", e.codigo)
        rid = e.request_id
    if rid:
        from ..engine import assignment as asg
        asg.cancel_request(db, rid, now, user.username)
    return {"ok": True}


@router.post("/expedientes/{eid}/preparar")
def preparar(eid: int, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    _puede_capturar(user)
    try:
        return rs.preparar(db, eid, user, utcnow())
    except rs.ErrorReservacion as e:
        _err(e)


# ======================================================================= reservaciones
@router.get("/reservaciones")
def listar_reservaciones(estado: str | None = Query(None, max_length=16), limit: int = Query(200, ge=1, le=2000),
                         user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        q = select(Reservacion, Expediente.codigo).join(Expediente, Expediente.id == Reservacion.expediente_id)
        if not user.is_admin:
            q = q.where(Reservacion.operator_id == (user.operator_id or -1),
                        Expediente.operator_id == (user.operator_id or -1))
        if estado:
            q = q.where(Reservacion.estado == estado)
        q = q.order_by(Reservacion.updated_at.desc()).limit(limit)
        return [{**rs.vista(r), "expediente": codigo} for r, codigo in s.execute(q)]


@router.post("/reservaciones/{rid}/enviar-datos")
def enviar_datos(rid: int, body: EnviarIn, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    try:
        return rs.enviar_datos(db, rid, user, utcnow(), body.revisado)
    except rs.ErrorReservacion as e:
        _err(e)


@router.get("/reservaciones/{rid}/calendario")
def calendario_reservacion(rid: int, region: str | None = Query(None, max_length=32),
                           sucursal: str | None = Query(None, max_length=32),
                           user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    try:
        return rs.calendario(db, rid, user, utcnow(), region, sucursal)
    except rs.ErrorReservacion as e:
        _err(e)


@router.post("/reservaciones/{rid}/seleccionar")
def seleccionar(rid: int, body: SeleccionIn, user: CurrentUser = Depends(current_user),
                db: Database = Depends(get_db)):
    try:
        return rs.seleccionar(db, rid, user, utcnow(), body.horario, body.region, body.sucursal)
    except rs.ErrorReservacion as e:
        _err(e)


@router.post("/reservaciones/{rid}/confirmar")
def confirmar(rid: int, body: ConfirmarIn, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    try:
        code, out = rs.confirmar(db, rid, user, utcnow(), body.confirmo)
    except rs.ErrorReservacion as e:
        _err(e)
    with db.session() as s:
        security.audit(s, user.username, "reservacion_confirmar", str(rid),
                       {"resultado": out["reservacion"]["estado"], "folio": out["reservacion"].get("folio")})
    return JSONResponse(out, status_code=code)


@router.post("/reservaciones/{rid}/cancelar")
def cancelar_reservacion(rid: int, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    try:
        out = rs.cancelar(db, rid, user, utcnow())
    except rs.ErrorReservacion as e:
        _err(e)
    with db.session() as s:
        security.audit(s, user.username, "reservacion_cancelada", str(rid), {"estado": out["estado"]})
    return out


# ======================================================================= calendario, historial, contrato
@router.get("/captura/calendario")
def calendario_laboratorio(region: str | None = Query(None, max_length=32),
                           sucursal: str | None = Query(None, max_length=32),
                           tramite: Literal["alta", "renovacion", "constancia"] | None = None,
                           user: CurrentUser = Depends(current_user)):
    try:
        return cal.leer(region, sucursal, tramite)
    except cal.CalendarioNoDisponible as e:
        raise HTTPException(503, {"mensaje": str(e)})


@router.get("/captura/historial")
def historial(limit: int = Query(200, ge=1, le=2000), tipo: str | None = Query(None, max_length=32),
              user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        q = (select(ExpedienteEvento, Expediente.codigo)
             .join(Expediente, Expediente.id == ExpedienteEvento.expediente_id))
        if not user.is_admin:
            q = q.where(Expediente.operator_id == (user.operator_id or -1))
        if tipo:
            q = q.where(ExpedienteEvento.tipo == tipo)
        q = q.order_by(ExpedienteEvento.id.desc()).limit(limit)
        from .serializers import iso
        return [{"id": e.id, "ts": iso(e.ts), "expediente": codigo, "expediente_id": e.expediente_id, "tipo": e.tipo,
                 "actor": e.actor, "asignacion": e.assignment_id, "reservacion": e.reservacion_id,
                 "evento_cambio": e.change_event_id, "detalle": e.detalle,
                 "normalizado": e.tipo in ex.EVENTOS_NORMALIZADOS} for e, codigo in s.execute(q)]


@router.get("/captura/contrato")
def contrato(user: CurrentUser = Depends(current_user)):
    c = contrato_actual()
    return {"nombre": c.nombre, "version": c.version,
            "campos": [{"campo": x.nombre, "etiqueta": x.etiqueta, "tipo": x.tipo, "origen": x.origen,
                        "obligatorio": x.obligatorio} for x in c.campos]}
