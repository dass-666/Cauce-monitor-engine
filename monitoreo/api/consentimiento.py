"""v2.2 · API de consentimiento y trazabilidad por expediente (laboratorio).

Permisos: el administrador, o el operador que puede ver el expediente (el asignado). En Ventanilla,
el operador registra el consentimiento de la persona presente desde su atención
(`POST /api/ventanilla/atenciones/{id}/consentimiento`).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..captura import consentimiento as cs
from ..captura import expedientes as ex
from ..captura import trazabilidad as tz
from ..db import Database, utcnow
from ..models import Expediente
from .captura import ConsentimientoIn
from .deps import CurrentUser, client_ip, current_user, get_db, require_admin

router = APIRouter(prefix="/api")


class RevocarIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    medio: str = Field(max_length=24)
    motivo: str | None = Field(default=None, max_length=32)


def _err(e: cs.ErrorConsentimiento):
    raise HTTPException(e.http, {"mensaje": e.mensaje, **e.detalle})


@router.get("/consentimientos/catalogos")
def catalogos(user: CurrentUser = Depends(current_user)):
    return {"finalidades": cs.FINALIDADES, "medios": cs.MEDIOS, "medios_revocacion": cs.MEDIOS_REVOCACION,
            "motivos_revocacion": cs.MOTIVOS_REVOCACION, "exigido": cs.exigido()}


@router.get("/expedientes/{eid}/consentimientos")
def listar(eid: int, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        e = s.get(Expediente, eid)
        if e is None or not ex.puede_ver(user, e):
            raise HTTPException(404, "Expediente no encontrado")
        vig = cs.vigente(s, e, utcnow())
        return {"expediente": e.codigo, "vigente": vig.codigo if vig else None,
                "bloqueo": None if vig else cs.motivo_bloqueo(s, e, utcnow()),
                "consentimientos": cs.de_expediente(s, eid)}


@router.post("/expedientes/{eid}/consentimientos", status_code=201)
def otorgar(eid: int, body: ConsentimientoIn, request: Request, user: CurrentUser = Depends(current_user),
            db: Database = Depends(get_db)):
    try:
        return cs.otorgar_seguro(db, eid, body.model_dump(exclude_none=True), user, utcnow(), client_ip(request))
    except cs.ErrorConsentimiento as e:
        _err(e)
    except ex.ErrorExpediente as e:
        raise HTTPException(e.http, {"mensaje": e.mensaje, "errores": e.errores}) from e


@router.post("/consentimientos/{cid}/revocar")
def revocar(cid: int, body: RevocarIn, request: Request, user: CurrentUser = Depends(current_user),
            db: Database = Depends(get_db)):
    try:
        return cs.revocar(db, cid, body.model_dump(exclude_none=True), user, utcnow(), client_ip(request))
    except cs.ErrorConsentimiento as e:
        _err(e)


@router.get("/consentimientos/verificar")
def verificar(user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    return cs.verificar(db)


@router.get("/expedientes/{eid}/trazabilidad")
def trazabilidad(eid: int, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        e = s.get(Expediente, eid)
        if e is None or not ex.puede_ver(user, e):
            raise HTTPException(404, "Expediente no encontrado")
    return tz.de_expediente(db, eid)


# ======================================================================= v2.2 · posibles duplicados
class DescartarIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    otro: str = Field(max_length=32, pattern=r"^EXP-\d{6}$")
    motivo: str = Field(min_length=5, max_length=120)


@router.get("/expedientes/{eid}/posibles-duplicados")
def posibles(eid: int, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        e = s.get(Expediente, eid)
        if e is None or not ex.puede_ver(user, e):
            raise HTTPException(404, "Expediente no encontrado")
        return {"expediente": e.codigo, "posibles": ex.posibles_duplicados(s, e)}


@router.post("/expedientes/{eid}/duplicados/descartar")
def descartar(eid: int, body: DescartarIn, request: Request, user: CurrentUser = Depends(require_admin),
              db: Database = Depends(get_db)):
    """Solo un administrador: decide que dos expedientes parecidos son personas distintas."""
    from sqlalchemy import select

    from .. import security
    now = utcnow()
    try:
        with db.session() as s:
            e = s.get(Expediente, eid)
            otro = s.scalar(select(Expediente).where(Expediente.codigo == body.otro))
            if e is None or otro is None:
                raise HTTPException(404, "Expediente no encontrado")
            ex.descartar_duplicado(s, e, otro, user.username, body.motivo, now)
            security.audit(s, user.username, "duplicado_descartado", e.codigo, {"otro": otro.codigo},
                           client_ip(request))
            estado = ex.revalidar(s, e, user.username, now) if e.estado == ex.REVISION else e.estado
            return {"expediente": e.codigo, "estado": estado, "ultimo_error": e.ultimo_error}
    except ex.ErrorExpediente as err:
        raise HTTPException(err.http, {"mensaje": err.mensaje, "errores": err.errores}) from err
