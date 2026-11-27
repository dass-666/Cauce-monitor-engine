"""Estado de la red según Network Engine (solo lectura), para cualquier usuario con sesión.

No importa nada de la red al cargarse: el servicio (monitoreo/red_lectura/servicio.py) lo crea app.py solo si hay
configuración. Esta ruta devuelve la copia en memoria y nunca espera a Network Engine.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import security
from .deps import CurrentUser, client_ip, current_user, get_db, require_admin

router = APIRouter(prefix="/api/red")


@router.get("")
def estado(request: Request, user: CurrentUser = Depends(current_user)):
    svc = getattr(request.app.state, "red", None)
    if svc is None:
        razon = getattr(request.app.state, "red_razon", "no_configurado") or "no_configurado"
        from ..red_lectura.servicio import estado_no_configurado     # módulo sin dependencias de la red
        return estado_no_configurado(razon)
    return svc.estado()


class ResolverIn(BaseModel):
    nota: str
    confirmar: str


@router.post("/revisiones/{pid}/resolver")
def resolver_revision(pid: str, body: ResolverIn, request: Request, user: CurrentUser = Depends(require_admin),
                      db=Depends(get_db)):
    """E8: resolver una marca «revisar a mano» de Network Engine. Solo administradores de Monitor y solo si hay una
    credencial «review» de Network Engine DISTINTA de la de lectura (ver red_lectura/revisiones.py)."""
    from ..red_lectura import revisiones as R
    error = R.validar(pid, body.nota, body.confirmar)
    if error:
        raise HTTPException(400, {"codigo": error, "mensaje": R.MENSAJES[error]})
    ip = client_ip(request)

    def anotar(resultado: str, extra: dict | None = None):
        with db.session() as s:
            security.audit(s, user.username, "red_revision_resolver", pid,
                           {"resultado": resultado, "nota": body.nota.strip()[:500], **(extra or {})}, ip)

    try:
        r = R.resolver(pid, body.nota, body.confirmar)
    except R.NoHabilitado as e:
        anotar("no_habilitado", {"razon": e.razon})
        orden = R.orden_cli(pid, body.confirmar.strip()[:40])
        raise HTTPException(409, {"codigo": "no_habilitado", "razon": e.razon, "orden": orden,
                                  "mensaje": f"Resolver desde Monitor no está habilitado ({e.razon}). En el equipo del "
                                             f"motor: {orden}"}) from None
    except Exception as e:  # noqa: BLE001  error del motor: se informa su código, nunca la credencial
        estado = getattr(e, "status", None)
        codigo = getattr(e, "code", None) or type(e).__name__
        anotar("rechazado", {"estado": estado, "codigo": str(codigo)[:40]})
        raise HTTPException(estado if isinstance(estado, int) and 400 <= estado < 500 else 502,
                            {"codigo": str(codigo)[:40], "mensaje": R.MENSAJES.get(str(codigo),
                                                                                    f"Network Engine respondió {codigo}")}) from None
    anotar("resuelta")
    svc = getattr(request.app.state, "red", None)
    if svc is not None:
        svc.quitar_revision(pid)                     # la pantalla deja de mostrarla sin esperar a la próxima lectura
    return {"ok": True, "resuelta": pid, "motor": {k: r.get(k) for k in ("resolved", "reason") if k in r}}
