"""v2.0 · Identidad persistente del proveedor (portal) por instancia del motor.

Problema que resuelve: dos instancias comparten la base de datos pero tienen configurados portales
DISTINTOS. La que no creó una reservación puede preguntar por su clave a SU portal, que no la
conoce, y reabrir como «no confirmada» una cita que el portal correcto sí confirmó.

Reglas:
- La identidad es una huella determinista del destino configurado (esquema, host normalizado,
  puerto) y de los contratos de formulario. `localhost` y `127.0.0.1` son el mismo destino.
- La primera instancia que usa la base REGISTRA su identidad (`settings.proveedor.identidad`).
- Cada reservación guarda la identidad con la que se creó (`reservaciones.proveedor`; las antiguas,
  nulas, se consideran del proveedor registrado en la base).
- Una instancia solo recupera, confirma o continúa reservaciones de SU proveedor. Las demás se
  dejan intactas («bloqueadas_otro_proveedor») y el autodiagnóstico lo marca como CRÍTICO.
- Cambiar de proveedor a propósito es una acción explícita y auditada (`adoptar`), que se niega
  mientras haya reservaciones a medias del proveedor anterior.
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import select

log = logging.getLogger("monitoreo.proveedor")
CLAVE = "proveedor.identidad"
ACTIVA_S = 120          # una instancia que no se ha reportado en 2 minutos ya no cuenta como activa
_LOCALES = {"localhost", "127.0.0.1", "::1", "ip6-localhost"}


def identidad_local() -> dict:
    from .captura import portal
    u = urlparse(portal.url())
    host = (u.hostname or "").lower()
    host = "127.0.0.1" if host in _LOCALES else host
    puerto = u.port or (443 if u.scheme == "https" else 80)
    contratos = sorted({os.environ.get("MONITOREO_PORTAL_CONTRATO", "portal_laboratorio"),
                        os.environ.get("MONITOREO_PORTAL_CONTRATO_DIA", "portal_laboratorio_dia")})
    destino = f"{u.scheme or 'http'}://{host}:{puerto}"
    huella = hashlib.sha256((destino + "|" + ",".join(contratos)).encode()).hexdigest()[:16]
    return {"id": huella, "destino": destino, "contratos": contratos}


def identidad_base(s) -> dict | None:
    from .models import Setting
    row = s.get(Setting, CLAVE)
    return row.value if row is not None and isinstance(row.value, dict) else None


def asegurar_base(s, local: dict | None = None) -> dict:
    """Devuelve la identidad registrada en la base; si no hay ninguna, registra la local."""
    from .models import Setting
    actual = identidad_base(s)
    if actual is None:
        actual = dict(local or identidad_local())
        s.add(Setting(key=CLAVE, value=actual))
        s.flush()
    return actual


def es_compatible(s, local: dict | None = None) -> bool:
    local = local or identidad_local()
    return asegurar_base(s, local)["id"] == local["id"]


def de_reservacion(s, res) -> str:
    """Identidad del proveedor de una reservación (las antiguas, sin dato, son de la base)."""
    return res.proveedor or asegurar_base(s)["id"]


def registrar_instancia(db, instancia: str, now: datetime) -> None:
    from .models import InstanciaMotor
    local = identidad_local()
    with db.session() as s:
        asegurar_base(s, local)
        row = s.get(InstanciaMotor, instancia[:96])
        if row is None:
            s.add(InstanciaMotor(instancia=instancia[:96], proveedor=local["id"], destino=local["destino"][:160], visto_en=now))
        else:
            row.proveedor, row.destino, row.visto_en = local["id"], local["destino"][:160], now


def estado(db, now: datetime | None = None) -> dict:
    from .db import utcnow
    from .models import InstanciaMotor
    now = now or utcnow()
    local = identidad_local()
    with db.session() as s:
        base = asegurar_base(s, local)
        activas = [{"instancia": i.instancia, "proveedor": i.proveedor, "destino": i.destino,
                    "visto_en": i.visto_en.isoformat() + "Z"}
                   for i in s.scalars(select(InstanciaMotor).where(InstanciaMotor.visto_en >= now - timedelta(seconds=ACTIVA_S))
                                      .order_by(InstanciaMotor.instancia))]
    return {"identidad_local": local, "identidad_base": base, "compatible": base["id"] == local["id"],
            "instancias_activas": activas,
            "instancias_discrepantes": [a for a in activas if a["proveedor"] != base["id"]]}


def pendientes_de(s, proveedor_id: str, base_id: str) -> int:
    """Reservaciones a medias (que dependen de lo que diga el portal) de ese proveedor."""
    from sqlalchemy import func, or_

    from .captura.reservaciones import BLOQUEANTES, VIVAS
    from .models import Reservacion
    cond = Reservacion.proveedor == proveedor_id
    if proveedor_id == base_id:
        cond = or_(cond, Reservacion.proveedor.is_(None))
    return s.scalar(select(func.count()).select_from(Reservacion).where(
        cond, Reservacion.estado.in_(list(set(BLOQUEANTES) | set(VIVAS))))) or 0


class Incompatible(Exception):
    pass


def adoptar(db, usuario: str, now: datetime) -> dict:
    """Registra como identidad de la base la de ESTA instancia. Se niega si quedan reservaciones a
    medias del proveedor anterior (se perderían o se recuperarían contra el portal equivocado)."""
    from . import security
    from .models import Setting
    local = identidad_local()
    with db.session() as s:
        base = asegurar_base(s, local)
        if base["id"] == local["id"]:
            return {"cambiado": False, "identidad_base": base}
        n = pendientes_de(s, base["id"], base["id"])
        if n:
            raise Incompatible(n)
        s.get(Setting, CLAVE).value = dict(local)
        security.audit(s, usuario, "proveedor_adoptado", local["id"],
                       {"anterior": base["id"], "destino_anterior": base.get("destino"), "destino": local["destino"]})
    log.warning("identidad del proveedor cambiada por %s: %s -> %s", usuario, base["id"], local["id"])
    return {"cambiado": True, "identidad_base": local, "anterior": base}
