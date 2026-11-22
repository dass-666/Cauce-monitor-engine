"""Laboratorio · «Autorrellenar formulario» en Ventanilla con Forms (ver monitoreo/autorrelleno/adaptador.py).

Solo se monta fuera de producción (modulos.SOLO_LABORATORIO) y cada petición vuelve a comprobar la
configuración de laboratorio y todas las reglas de Monitor. Nada de esto envía formularios.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..autorrelleno import adaptador as ad
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
    corrida = ad.corrida_de(exp_id) if exp_id else None
    out = {"habilitado": conf["habilitado"], "disponible": False, "codigo": conf["codigo"], "motivo": conf["motivo"],
           "corrida": corrida.vista() if corrida else None}
    if not conf["habilitado"]:
        return out
    try:
        ad.evaluar(db, user, aid, utcnow(), ad.configuracion())
    except ad.Negado as n:
        out.update(codigo=n.codigo, motivo=n.mensaje)
        return out
    out.update(disponible=True, codigo="", motivo="")
    return out


@router.post("/atenciones/{aid}", status_code=202)
def lanzar(aid: int, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    try:
        return ad.iniciar(db, user, aid)
    except ad.Negado as n:
        _negado(n)
