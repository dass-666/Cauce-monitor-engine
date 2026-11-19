"""v2.4 · API de recuperación segura (solo administrador). Ver monitoreo/recuperacion.py.

GET  /api/recuperacion                                   estado, motivos del bloqueo y discrepancias
POST /api/recuperacion/discrepancias/{ref}/resolver      una acción explícita, con motivo
POST /api/recuperacion/levantar                          solo con 0 discrepancias pendientes
POST /api/recuperacion/reiniciar-diario                  solo si el diario no supera la verificación
Ninguna de estas rutas llama al portal.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import recuperacion as rc
from ..captura import consentimiento as cs
from ..captura import reservaciones as rs
from ..db import Database
from .deps import CurrentUser, client_ip, get_db, require_admin

router = APIRouter(prefix="/api/recuperacion")


class ResolverIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tipo: str = Field(max_length=40)
    accion: str = Field(max_length=40)
    motivo: str = Field(max_length=300)
    folio: str | None = Field(default=None, max_length=32)


class MotivoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    motivo: str = Field(max_length=300)


def _err(e: Exception):
    raise HTTPException(e.http, {"mensaje": e.mensaje, **e.detalle})


@router.get("")
def estado(user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    with db.session() as s:
        return rc.publico(rc.evaluar(s, usar_cache=False))


@router.post("/discrepancias/{ref}/resolver")
def resolver(ref: int, body: ResolverIn, request: Request, user: CurrentUser = Depends(require_admin),
             db: Database = Depends(get_db)):
    try:
        return rc.resolver(db, user, ref, body.tipo, body.accion, body.motivo, body.folio, client_ip(request))
    except (rc.ErrorRecuperacion, cs.ErrorConsentimiento, rs.ErrorReservacion) as e:
        _err(e)


@router.post("/levantar")
def levantar(body: MotivoIn, request: Request, user: CurrentUser = Depends(require_admin),
             db: Database = Depends(get_db)):
    try:
        return rc.levantar(db, user, body.motivo, client_ip(request))
    except rc.ErrorRecuperacion as e:
        _err(e)


@router.post("/reiniciar-diario")
def reiniciar(body: MotivoIn, request: Request, user: CurrentUser = Depends(require_admin),
              db: Database = Depends(get_db)):
    try:
        return rc.reiniciar_diario(db, user, body.motivo, client_ip(request))
    except rc.ErrorRecuperacion as e:
        _err(e)
