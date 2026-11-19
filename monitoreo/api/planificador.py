"""v2.1 · API del planificador diario, del observador de calendario y de las coincidencias.
Solo fuera de producción (trabaja con el portal de laboratorio, como la captura)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..db import Database, utcnow
from ..planificador import coincidencias, observaciones, operacion
from ..planificador import plan as pl
from .deps import CurrentUser, current_user, get_db, require_admin

router = APIRouter(prefix="/api")


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


def _sup(request: Request):
    sup = getattr(request.app.state, "supervisor", None)
    if sup is None:
        raise HTTPException(503, "El supervisor de observadores no está en marcha en este proceso")
    return sup


@router.get("/observador")
def observadores(request: Request, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    from ..planificador import observador as ob
    sup = getattr(request.app.state, "supervisor", None)
    return {"configurados": ob.listar_config(db), "proceso": sup.estado() if sup else None,
            "observadores": sup.estado()["observadores"] if sup else []}


@router.post("/observador")
def iniciar_observador(body: ObservadorIn, request: Request, user: CurrentUser = Depends(require_admin),
                       db: Database = Depends(get_db)):
    """Guarda la configuración (persistente) y la pone en marcha. Tras reiniciar el motor se restaura sola."""
    from ..planificador import observador as ob
    sup = _sup(request)
    c = ob.configurar(db, body.sucursal, body.tramite, body.mes, body.estrategia, body.intervalo_visual_ms,
                      user.username, utcnow())
    if c["ya_existia"]:
        raise HTTPException(409, "Ya hay un observador para esa sucursal y trámite")
    r = sup.ciclo(utcnow())
    if c["clave"] in r["fallidos"]:
        cfg = next((x for x in ob.listar_config(db) if x["clave"] == c["clave"]), c)
        raise HTTPException(503, cfg.get("motivo") or "no se pudo iniciar el observador")
    o = sup.activos.get(c["clave"])
    return {**(o.estado() if o else {}), "configuracion": c,
            "en_otro_proceso": c["clave"] in r["de_otro_proceso"]}


@router.post("/observador/simular-caida-navegador")
def simular_caida(request: Request, user: CurrentUser = Depends(require_admin)):
    """Laboratorio (pruebas de resistencia): corta de golpe el navegador de los observadores de este
    proceso, como si Chromium se cerrara. El supervisor debe relanzarlo."""
    sup = _sup(request)
    n = 0
    for o in list(sup.activos.values()):
        o.matar_navegador()
        n += 1
    return {"navegadores_cortados": n}


@router.delete("/observador")
def detener_observadores(request: Request, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    from ..planificador import observador as ob
    n = ob.desactivar(db, user.username, utcnow())
    sup = getattr(request.app.state, "supervisor", None)
    if sup is not None:
        sup.ciclo(utcnow())
    return {"detenidos": n}


# ------------------------------------------------------------------ operación (operador)
@router.get("/planificador/jornada")
def jornada(user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    """v2.1 · P1: plan del día para la sección «Plan del día» de Ventanilla (respeta permisos)."""
    return operacion.jornada_para(db, user, utcnow())


@router.get("/planificador/elementos/{elemento_id}")
def detalle(elemento_id: int, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    try:
        return operacion.detalle_para(db, user, elemento_id)
    except operacion.ErrorOperacion as e:
        _err(e)


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
