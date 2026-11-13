"""v2.0 · API del modo ventanilla (atención presencial). Lógica en `captura/ventanilla.py`.

El resto del flujo (preparar, enviar datos, calendario, elegir día, confirmar) usa las rutas de
siempre de /api/expedientes y /api/reservaciones: no hay un segundo camino para confirmar.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..captura import ventanilla as vt
from ..db import Database, utcnow
from .captura import ExpedienteIn
from .deps import CurrentUser, current_user, get_db

router = APIRouter(prefix="/api/ventanilla")


def _err(e: vt.ErrorVentanilla):
    raise HTTPException(e.http, {"mensaje": e.mensaje, **e.detalle})


class IniciarIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clave: str = Field(min_length=8, max_length=64)      # la genera el navegador: repetir = misma atención
    conexion: str | None = Field(default=None, max_length=64)


class BuscarIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    codigo: str | None = Field(default=None, max_length=32)
    nombre: str | None = Field(default=None, max_length=128)
    fecha_nacimiento: str | None = Field(default=None, max_length=10)
    tipo_tramite: str | None = Field(default=None, max_length=32)


class VincularIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expediente_id: int | None = None
    datos: ExpedienteIn | None = None


class CerrarIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resultado: Literal["SIN_DISPONIBILIDAD", "ERROR", "CANCELADA"] | None = None
    motivo: str = Field(default="", max_length=32)
    cerrar_expediente: bool = False


@router.get("")
def estado(user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    """Meta del día, progreso, atenciones abiertas (para retomar) y las últimas del operador."""
    return vt.resumen_hoy(db, user, utcnow())


@router.post("/atenciones")
def iniciar(body: IniciarIn, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    try:
        creada, out = vt.iniciar(db, user, body.clave, utcnow(), body.conexion)
    except vt.ErrorVentanilla as e:
        _err(e)
    return JSONResponse(out, status_code=201 if creada else 200)


@router.get("/atenciones/{aid}")
def ver(aid: int, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    try:
        with db.session() as s:
            return vt.vista(s, vt._visible(s, aid, user))
    except vt.ErrorVentanilla as e:
        _err(e)


@router.post("/atenciones/{aid}/buscar")
def buscar(aid: int, body: BuscarIn, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    try:
        return {"expedientes": vt.buscar(db, user, aid, **body.model_dump())}
    except vt.ErrorVentanilla as e:
        _err(e)


@router.post("/atenciones/{aid}/expediente")
def vincular(aid: int, body: VincularIn, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    try:
        return vt.vincular(db, user, aid, utcnow(), expediente_id=body.expediente_id,
                           datos=body.datos.model_dump(exclude_none=True) if body.datos else None)
    except vt.ErrorVentanilla as e:
        _err(e)


@router.post("/atenciones/{aid}/cerrar")
def cerrar(aid: int, body: CerrarIn, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    try:
        return vt.cerrar(db, user, aid, utcnow(), body.resultado, body.motivo, body.cerrar_expediente)
    except vt.ErrorVentanilla as e:
        _err(e)
