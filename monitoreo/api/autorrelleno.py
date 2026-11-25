"""Laboratorio · «Autorrellenar formulario» en Ventanilla con Forms (ver monitoreo/autorrelleno/adaptador.py).

Solo se monta fuera de producción (modulos.SOLO_LABORATORIO) y cada petición vuelve a comprobar la
configuración de laboratorio y todas las reglas de Monitor. Nada de esto envía formularios.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..autorrelleno import adaptador as ad
from ..autorrelleno import coincidencia as cz
from ..db import Database, utcnow
from ..models import Atencion
from .deps import CurrentUser, current_user, get_db

router = APIRouter(prefix="/api/autorrelleno")


def _negado(n: ad.Negado):
    raise HTTPException(n.http, {"mensaje": n.mensaje, "codigo": n.codigo})


@router.get("/estado")
def estado(user: CurrentUser = Depends(current_user)):
    """¿Está disponible en esta instancia? (sin evaluar ningún expediente)."""
    return ad.estado_configuracion()


@router.get("/atenciones/{aid}")
def ver(aid: int, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    """Si el botón procede ahora para ESTA atención y la última corrida de su expediente (sin valores)."""
    conf = ad.estado_configuracion()
    with db.session() as s:
        a = s.get(Atencion, aid)
        if a is None or not (user.is_admin or a.username == user.username):
            raise HTTPException(404, "Atención no encontrada")
        exp_id = a.expediente_id
        coin = cz.vista(s, exp_id, user.operator_id) if exp_id else None
    corrida = ad.corrida_de(exp_id) if exp_id else None
    if coin is not None and coin.get("lista_para_confirmacion"):
        coin["reintentos"] = len((coin.get("forms") or {}).get("reintentos") or [])
        coin["max_reintentos"] = cz.MAX_REINTENTOS
        coin["motivos_reintento"] = cz.MOTIVOS_REINTENTO
    out = {"habilitado": conf["habilitado"], "disponible": False, "codigo": conf["codigo"], "motivo": conf["motivo"],
           "corrida": corrida.vista() if corrida else None,
           "externo": conf.get("externo", False), "sitio": conf.get("sitio", ""), "coincidencia": coin,
           "aislamiento": conf.get("aislamiento", "ninguno")}
    if not conf["habilitado"]:
        return out
    try:
        ad.evaluar(db, user, aid, utcnow(), ad.configuracion())
    except ad.Negado as n:
        out.update(codigo=n.codigo, motivo=n.mensaje)
        return out
    out.update(disponible=True, codigo="", motivo="")
    return out


class LanzarIn(BaseModel):
    reintento: str | None = Field(default=None, max_length=32)   # motivo: cz.MOTIVOS_REINTENTO


@router.post("/atenciones/{aid}", status_code=202)
def lanzar(aid: int, body: LanzarIn | None = None, user: CurrentUser = Depends(current_user),
           db: Database = Depends(get_db)):
    try:
        return ad.iniciar(db, user, aid, reintento=(body.reintento if body else None))
    except ad.Negado as n:
        _negado(n)


class FolioIn(BaseModel):
    folio: str = Field(min_length=4, max_length=40)
    folio_confirmacion: str = Field(min_length=4, max_length=40)


@router.post("/reservaciones/{rid}/folio")
def registrar_folio(rid: int, body: FolioIn, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    """La persona confirmó en el portal la operación que Forms preparó: Monitor comprueba el folio con el portal
    (vía autorizada del adaptador) y solo entonces marca la cita CONFIRMADA. Si no coincide, no cambia nada."""
    try:
        return cz.verificar_folio(db, user, rid, body.folio, body.folio_confirmacion, utcnow())
    except cz.ErrorFolio as e:
        raise HTTPException(e.http, {"mensaje": e.mensaje, "codigo": e.codigo, **e.detalle})
