"""Estado de la red según Network Engine (solo lectura), para cualquier usuario con sesión.

No importa nada de la red al cargarse: el servicio (monitoreo/red_lectura/servicio.py) lo crea app.py solo si hay
configuración. Esta ruta devuelve la copia en memoria y nunca espera a Network Engine.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from .deps import CurrentUser, current_user

router = APIRouter(prefix="/api/red")


@router.get("")
def estado(request: Request, user: CurrentUser = Depends(current_user)):
    svc = getattr(request.app.state, "red", None)
    if svc is None:
        razon = getattr(request.app.state, "red_razon", "no_configurado") or "no_configurado"
        from ..red_lectura.servicio import estado_no_configurado     # módulo sin dependencias de la red
        return estado_no_configurado(razon)
    return svc.estado()
