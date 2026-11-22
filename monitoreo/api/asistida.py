"""Piloto asistido · API de la confirmación humana persistente (ver monitoreo/captura/asistida.py).

Forma parte del flujo administrativo: fuera de producción siempre; en producción solo con
MONITOREO_PRODUCCION_FLUJO_ADMIN=1. Permisos: el operador asignado al expediente o un administrador.
Ninguna ruta llama a un portal externo.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..captura import asistida as asi
from ..db import Database, utcnow
from .deps import CurrentUser, client_ip, current_user, get_db

router = APIRouter(prefix="/api")


def _err(e: asi.ErrorAsistida):
    raise HTTPException(e.http, {"mensaje": e.mensaje, **e.detalle}) from e


class RevisarIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frase: str = Field(max_length=64)
    huella: str = Field(max_length=64)


class RegistrarIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    folio: str = Field(max_length=64)
    folio_confirmacion: str = Field(max_length=64)
    fecha: str = Field(max_length=10)
    hora: str | None = Field(default=None, max_length=5)
    sucursal: str | None = Field(default=None, max_length=32)
    comprobante_sha256: str | None = Field(default=None, max_length=64)
    comprobante_ref: str | None = Field(default=None, max_length=64)
    declaro: bool = False


class RevocarIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    motivo: str = Field(max_length=200)


@router.get("/expedientes/{eid}/asistida")
def estado(eid: int, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    try:
        return asi.estado(db, eid, user, utcnow())
    except asi.ErrorAsistida as e:
        _err(e)


@router.post("/expedientes/{eid}/asistida/abrir")
def abrir(eid: int, request: Request, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    try:
        return asi.abrir(db, eid, user, utcnow(), client_ip(request))
    except asi.ErrorAsistida as e:
        _err(e)


@router.post("/expedientes/{eid}/asistida/revisar")
def revisar(eid: int, body: RevisarIn, request: Request, user: CurrentUser = Depends(current_user),
            db: Database = Depends(get_db)):
    try:
        return asi.revisar(db, eid, user, body.frase, body.huella, utcnow(), client_ip(request))
    except asi.ErrorAsistida as e:
        _err(e)


@router.post("/expedientes/{eid}/asistida/registrar")
def registrar(eid: int, body: RegistrarIn, request: Request, user: CurrentUser = Depends(current_user),
              db: Database = Depends(get_db)):
    try:
        return asi.registrar(db, eid, user, utcnow(), body.folio, body.folio_confirmacion, body.fecha, body.hora,
                             body.sucursal, body.comprobante_sha256, body.comprobante_ref, body.declaro,
                             client_ip(request))
    except asi.ErrorAsistida as e:
        _err(e)


@router.post("/expedientes/{eid}/asistida/revocar")
def revocar(eid: int, body: RevocarIn, request: Request, user: CurrentUser = Depends(current_user),
            db: Database = Depends(get_db)):
    try:
        return asi.revocar(db, eid, user, body.motivo, utcnow(), client_ip(request))
    except asi.ErrorAsistida as e:
        _err(e)
