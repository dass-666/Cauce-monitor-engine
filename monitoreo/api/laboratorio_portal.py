"""v2.3 · «Calendario del laboratorio»: manejar el portal de laboratorio PROPIO desde el panel.

Antes, abrir un día en el calendario del laboratorio (para que el observador lo vea y el plan lo asigne)
solo se podía con las rutas `/control/...` del portal y su token de control, es decir, con un script.
Estas rutas hacen de intermediario para el administrador, con tres condiciones que se comprueban en cada
petición:

1. el motor NO está en producción (el enrutador solo se monta fuera de producción);
2. `MONITOREO_PORTAL_TOKEN_CONTROL` está definido (lo pone `scripts/ventanilla_laboratorio.py`);
3. `MONITOREO_PORTAL_URL` apunta a 127.0.0.1/localhost: nunca se envía el token a otro equipo.

El protocolo de control (cabecera y token) vive en `portal_reservas/control.py`, no en el motor.
Solo se permiten las acciones de calendario (habilitar, liberar, agotar, deshabilitar, rehabilitar) y
leer registros ficticios de ejemplo. No hay vía para portales externos: el portal es el de laboratorio.
"""
from __future__ import annotations

import calendar
import os
from datetime import date
from typing import Literal
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from portal_reservas import control

from .. import security
from ..captura import portal
from ..db import Database
from .deps import CurrentUser, get_db, require_admin

router = APIRouter(prefix="/api/laboratorio/portal")
VARIABLE = "MONITOREO_PORTAL_TOKEN_CONTROL"


def _estado() -> tuple[bool, str]:
    if not os.environ.get(VARIABLE):
        return False, ("El calendario del laboratorio se maneja desde aquí cuando el motor se abre con "
                       "scripts/ventanilla_laboratorio.py (o ventanilla_laboratorio.bat).")
    host = urlparse(portal.url()).hostname
    if host not in ("127.0.0.1", "localhost"):
        return False, "El portal configurado no es el de laboratorio en este equipo (127.0.0.1)."
    return True, ""


def _ctl(metodo: str, ruta: str, **kw):
    ok, motivo = _estado()
    if not ok:
        raise HTTPException(409, motivo)
    try:
        r = control.peticion(portal.url(), os.environ[VARIABLE], metodo, ruta, **kw)
    except httpx.HTTPError as e:
        raise HTTPException(503, f"El portal de laboratorio no responde ({type(e).__name__})") from None
    if r.status_code >= 400:
        try:
            msg = r.json().get("detail")
        except ValueError:
            msg = None
        raise HTTPException(r.status_code if r.status_code in (404, 409, 422) else 502,
                            f"El portal de laboratorio rechazó la acción: {msg or r.status_code}")
    return r.json()


@router.get("")
def estado(user: CurrentUser = Depends(require_admin)):
    ok, motivo = _estado()
    return {"disponible": ok, "motivo": motivo, "portal": portal.url() if ok else None}


@router.get("/calendario")
def calendario_mes(sucursal: str = Query(max_length=32), tramite: str = Query(max_length=32),
                   mes: str | None = Query(default=None, max_length=7), user: CurrentUser = Depends(require_admin)):
    """Los días del mes para una sucursal y trámite, con su estado en el portal de laboratorio."""
    d = _ctl("GET", "/control/dias")
    hoy = date.fromisoformat(d["hoy"])
    try:
        y, m = (int(x) for x in (mes or f"{hoy.year:04d}-{hoy.month:02d}").split("-"))
        date(y, m, 1)
    except ValueError:
        raise HTTPException(422, "Mes no válido (AAAA-MM)") from None
    por_fecha = {x["fecha"]: x for x in d["dias"].values() if x["sucursal"] == sucursal and x["tramite"] == tramite}
    dias = []
    for k in range(1, calendar.monthrange(y, m)[1] + 1):
        f = date(y, m, k).isoformat()
        x = por_fecha.get(f)
        dias.append({"fecha": f, "pasado": f <= hoy.isoformat(), "estado": x["estado"] if x else "NO_HABILITADO",
                     "libres": x["libres"] if x else 0, "capacidad": x["capacidad"] if x else 0,
                     "ocupados": x.get("ocupados", 0) if x else 0})
    return {"hoy": hoy.isoformat(), "mes": f"{y:04d}-{m:02d}", "sucursal": sucursal, "tramite": tramite, "dias": dias,
            "vista_observador": f"{portal.url()}/observar/calendario?sucursal={sucursal}&tramite={tramite}&mes={y:04d}-{m:02d}"}


class AccionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sucursal: str = Field(min_length=1, max_length=32)
    tramite: str = Field(min_length=1, max_length=32)
    fecha: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    capacidad: int = Field(default=1, ge=1, le=20)


@router.post("/calendario/{accion}")
def accion(accion: Literal["habilitar", "liberar", "agotar", "deshabilitar", "rehabilitar"], body: AccionIn,
           user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    datos = {"sucursal": body.sucursal, "tramite": body.tramite, "fecha": body.fecha}
    if accion == "habilitar":
        datos["capacidad"] = body.capacidad
    elif accion == "liberar":
        datos["unidades"] = body.capacidad
    r = _ctl("POST", f"/control/dias/{accion}", json=datos)
    with db.session() as s:
        security.audit(s, user.username, "laboratorio_calendario", f"{body.sucursal}/{body.tramite}/{body.fecha}",
                       {"accion": accion, "capacidad": body.capacidad})
    return {"accion": accion, "resultado": r}


@router.get("/registros")
def registros(desde: int = 0, cuantos: int = 8, tramite: str | None = None,
              user: CurrentUser = Depends(require_admin)):
    """Registros previos FICTICIOS que el portal de laboratorio reconoce (para dar de alta personas de prueba)."""
    out, n = [], max(0, desde)
    while len(out) < min(max(1, cuantos), 20) and n < desde + 400:
        x = _ctl("GET", "/control/registro-ejemplo", params={"n": n})
        if not tramite or x.get("tramite") == tramite:
            out.append({"identificador": x["identificador"], "fecha_nacimiento": x["fecha_nacimiento"],
                        "tramite": x["tramite"]})
        n += 1
    return {"registros": out, "siguiente": n, "nota": "Registros sintéticos del portal de laboratorio: no son personas reales."}
