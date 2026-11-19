"""v2.0 · P2 · API de adaptadores y de captura manual (solo fuera de producción, como la captura)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import adaptadores
from ..adaptadores import capturas
from ..adaptadores.base import NoAutorizado
from ..db import Database, utcnow
from .deps import CurrentUser, current_user, get_db, require_admin

router = APIRouter(prefix="/api")


def _ad(nombre: str):
    try:
        return adaptadores.obtener(nombre)
    except KeyError:
        raise HTTPException(404, "Adaptador desconocido") from None


@router.get("/adaptadores")
def listar(user: CurrentUser = Depends(current_user)):
    return {"adaptadores": adaptadores.listar()}


class PrepararIn(BaseModel):
    expediente_id: int | None = None
    datos: dict[str, str | None] = Field(default_factory=dict)


@router.post("/adaptadores/{nombre}/preparar")
def preparar(nombre: str, body: PrepararIn, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    """Qué pedirá el trámite y qué falta. Con `expediente_id` usa sus datos (sin devolverlos)."""
    datos = dict(body.datos)
    if body.expediente_id is not None:
        from ..captura import expedientes as ex
        from ..models import Expediente
        with db.session() as s:
            e = s.get(Expediente, body.expediente_id)
            if e is None or not ex.puede_ver(user, e):
                raise HTTPException(404, "Expediente no encontrado")
            # Los datos del expediente solo se usan aquí para saber qué falta: la respuesta no los incluye.
            datos = {col.name: getattr(e, col.name) for col in Expediente.__table__.columns}
            datos = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in datos.items()}
    return _ad(nombre).preparar(datos).como_dict()


class ObservarIn(BaseModel):
    region: str | None = Field(None, max_length=32)
    sucursal: str | None = Field(None, max_length=32)
    desde: str | None = Field(None, max_length=10)
    hasta: str | None = Field(None, max_length=10)


@router.post("/adaptadores/{nombre}/observar")
def observar(nombre: str, body: ObservarIn, user: CurrentUser = Depends(require_admin)):
    try:
        return _ad(nombre).observar(body.model_dump()).como_dict()
    except NoAutorizado as e:
        raise HTTPException(409, str(e)) from e


class CapturaIn(BaseModel):
    adaptador: str = Field(max_length=32)
    folio: str = Field(min_length=1, max_length=64)
    fecha: str = Field(min_length=6, max_length=20)
    sucursal: str | None = Field(None, max_length=32)
    expediente_id: int | None = None
    atencion_id: int | None = None
    transaccion_id: str | None = Field(None, max_length=64)
    folio_visto: bool = False


def _err(e: capturas.ErrorCaptura):
    raise HTTPException(e.status, {"mensaje": e.mensaje, **e.extra}) from e


@router.post("/capturas-manuales", status_code=201)
def registrar(body: CapturaIn, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    try:
        return capturas.registrar(db, user, body.adaptador, body.folio, body.fecha, body.sucursal, utcnow(),
                                  body.expediente_id, body.atencion_id, body.transaccion_id, body.folio_visto)
    except capturas.ErrorCaptura as e:
        _err(e)


@router.post("/capturas-manuales/{cid}/verificar")
def reverificar(cid: int, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    try:
        return capturas.verificar(db, user, cid, utcnow())
    except capturas.ErrorCaptura as e:
        _err(e)


@router.get("/capturas-manuales")
def lista(user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    return {"capturas": capturas.listar(db, user)}
