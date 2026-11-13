"""v2.0 · Identidad del proveedor (portal) de esta instancia y adopción explícita (solo administrador)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import proveedor
from ..db import Database, utcnow
from .deps import CurrentUser, get_db, require_admin

router = APIRouter(prefix="/api")


@router.get("/proveedor")
def estado(user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    return proveedor.estado(db)


class AdoptarIn(BaseModel):
    confirmo: bool = False


@router.post("/proveedor/adoptar")
def adoptar(body: AdoptarIn, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    if not body.confirmo:
        raise HTTPException(422, "Cambiar el proveedor de la base exige confirmarlo expresamente")
    try:
        return proveedor.adoptar(db, user.username, utcnow())
    except proveedor.Incompatible as e:
        raise HTTPException(409, {"mensaje": "Hay reservaciones a medias del proveedor anterior: resuélvelas desde "
                                             "la instancia que usa ese portal antes de cambiarlo",
                                  "pendientes": int(e.args[0])}) from None
