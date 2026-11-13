"""v2.0 · API del gestor de conexiones (solo administradores; solo fuera de producción, como la captura)."""
from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Depends
from sqlalchemy import select

from .. import conexiones, security
from ..captura import portal
from ..conexiones.gestor import es_local
from ..db import Database
from ..engine.params import get_params
from ..models import ConexionEvento
from . import serializers as ser
from .deps import CurrentUser, get_db, require_admin

router = APIRouter(prefix="/api/conexiones")


def _destino() -> tuple[str, int]:
    u = urlparse(portal.url())
    return u.hostname or "", u.port or (443 if u.scheme == "https" else 80)


@router.get("")
def estado(user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    """Conexiones del equipo, cuál usa el motor, sus cifras y los últimos eventos (cambios, caídas,
    restricciones del proveedor y uso por operación)."""
    try:
        conexiones.volcar(db)
    except Exception:  # noqa: BLE001
        pass
    host, puerto = _destino()
    with db.session() as s:
        params = get_params(s)
        eventos = [{"ts": ser.iso(e.ts), "evento": e.evento, "conexion": e.conexion, "tipo": e.tipo,
                    "operacion": e.operacion, "detalle": e.detalle}
                   for e in s.scalars(select(ConexionEvento).order_by(ConexionEvento.id.desc()).limit(50))]
    return {**conexiones.gestor().resumen(params), "destino": {"host": host, "puerto": puerto, "local": es_local(host)},
            "politica": {k: params[k] for k in ("conexion_orden_tipos", "conexion_deshabilitadas", "conexion_umbral_fallos",
                                                 "conexion_reintento_s", "conexion_estable_s")},
            "eventos": eventos}


@router.post("/sondear")
def sondear(user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    """Mide ahora cada conexión abriendo (y cerrando) una conexión TCP con el portal CONFIGURADO.
    No envía nada al portal. Con el portal en este mismo equipo no hay nada que medir."""
    host, puerto = _destino()
    with db.session() as s:
        params = get_params(s)
        security.audit(s, user.username, "conexiones_sondear", host, {})
    r = conexiones.gestor().sondear(params, host, puerto)
    try:
        conexiones.volcar(db)
    except Exception:  # noqa: BLE001
        pass
    return {"resultados": r, "nota": None if r else "El portal está en este mismo equipo: no hay conexiones que medir."}
