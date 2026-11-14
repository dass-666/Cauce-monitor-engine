"""v2.1 · API del planificador diario, del observador de calendario y de las coincidencias.
Solo fuera de producción (trabaja con el portal de laboratorio, como la captura)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..db import Database, utcnow
from ..planificador import coincidencias, observaciones, operacion
from ..planificador import plan as pl
from .deps import CurrentUser, current_user, get_db, require_admin

router = APIRouter(prefix="/api")
_observadores: dict[str, object] = {}


def _err(e):
    raise HTTPException(e.http, {"mensaje": e.mensaje, **(e.detalle or {})} if e.detalle else e.mensaje) from e


class PlanIn(BaseModel):
    fecha_jornada: str
    capacidad: int = pl.CAPACIDAD_INICIAL


class SeleccionIn(BaseModel):
    n: int | None = None
    fechas_desde: str | None = None
    fechas_hasta: str | None = None


class ElementosIn(BaseModel):
    elementos: list[dict] = Field(default_factory=list, max_length=pl.CAPACIDAD_MAXIMA)


class AmpliarIn(BaseModel):
    capacidad: int


@router.get("/planes")
def planes(user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    return {"planes": pl.listar(db)}


@router.post("/planes", status_code=201)
def crear_plan(body: PlanIn, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    try:
        return pl.crear(db, body.fecha_jornada, user.username, utcnow(), body.capacidad)
    except pl.ErrorPlan as e:
        _err(e)


@router.get("/planes/{plan_id}")
def ver_plan(plan_id: int, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    try:
        return pl.obtener(db, plan_id)
    except pl.ErrorPlan as e:
        _err(e)


@router.post("/planes/{plan_id}/seleccionar")
def seleccionar(plan_id: int, body: SeleccionIn, user: CurrentUser = Depends(require_admin),
                db: Database = Depends(get_db)):
    try:
        return pl.seleccionar(db, plan_id, user.username, utcnow(), body.n, body.fechas_desde, body.fechas_hasta)
    except pl.ErrorPlan as e:
        _err(e)


@router.post("/planes/{plan_id}/elementos")
def agregar(plan_id: int, body: ElementosIn, user: CurrentUser = Depends(require_admin),
            db: Database = Depends(get_db)):
    try:
        return pl.agregar(db, plan_id, body.elementos, user.username, utcnow())
    except pl.ErrorPlan as e:
        _err(e)


@router.post("/planes/{plan_id}/ampliar")
def ampliar(plan_id: int, body: AmpliarIn, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    try:
        return pl.ampliar(db, plan_id, body.capacidad, user.username, utcnow())
    except pl.ErrorPlan as e:
        _err(e)


@router.delete("/planes/elementos/{elemento_id}")
def retirar(elemento_id: int, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    try:
        return pl.retirar(db, elemento_id, user.username, utcnow())
    except pl.ErrorPlan as e:
        _err(e)


# ------------------------------------------------------------------ observaciones y coincidencias
@router.get("/observaciones")
def ver_observaciones(desde: int = 0, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    return {"eventos": observaciones.listar(db, desde)}


@router.post("/observaciones/{evento_id}/atender")
def atender(evento_id: int, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    return coincidencias.atender(db, evento_id, utcnow())


class ObservadorIn(BaseModel):
    sucursal: str = Field(min_length=1, max_length=32)
    tramite: str = Field(min_length=1, max_length=32)
    mes: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    estrategia: str = Field(default="ambas", pattern="^(dom|visual|ambas)$")
    intervalo_visual_ms: int = Field(default=250, ge=50, le=10_000)


@router.get("/observador")
def observadores(user: CurrentUser = Depends(require_admin)):
    return {"observadores": [o.estado() for o in _observadores.values()]}


@router.post("/observador")
def iniciar_observador(body: ObservadorIn, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    from ..planificador.observador import Observador
    clave = f"{body.sucursal}|{body.tramite}"
    if clave in _observadores:
        raise HTTPException(409, "Ya hay un observador para esa sucursal y trámite")
    o = Observador(db, body.sucursal, body.tramite, body.mes, body.estrategia, body.intervalo_visual_ms)
    try:
        o.iniciar()
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from None
    _observadores[clave] = o
    return o.estado()


@router.delete("/observador")
def detener_observadores(user: CurrentUser = Depends(require_admin)):
    n = len(_observadores)
    for o in list(_observadores.values()):
        o.detener()
    _observadores.clear()
    return {"detenidos": n}


# ------------------------------------------------------------------ operación (operador)
@router.get("/planificador/coincidencias")
def mis_coincidencias(user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    return {"coincidencias": operacion.visibles_para(db, user)}


@router.post("/planificador/elementos/{elemento_id}/preparar")
def preparar(elemento_id: int, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    try:
        return operacion.preparar(db, elemento_id, user, utcnow())
    except operacion.ErrorOperacion as e:
        _err(e)


class DevolverIn(BaseModel):
    motivo: str = Field(default="sin disponibilidad", max_length=120)


@router.post("/planificador/elementos/{elemento_id}/devolver")
def devolver(elemento_id: int, body: DevolverIn, user: CurrentUser = Depends(current_user),
             db: Database = Depends(get_db)):
    try:
        return operacion.devolver(db, elemento_id, user, utcnow(), body.motivo)
    except operacion.ErrorOperacion as e:
        _err(e)


@router.post("/planificador/sincronizar")
def sincronizar(user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    return operacion.sincronizar(db, utcnow())
