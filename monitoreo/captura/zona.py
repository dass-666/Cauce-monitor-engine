"""Zona horaria del calendario (v1.6).

`MONITOREO_ZONA_HORARIA` (nombre IANA, p. ej. America/Mexico_City). Si no se define se usa la del
sistema. Las fechas de los calendarios ("hoy", días pasados, ventana nocturna) se calculan en
esta zona; en la base de datos los instantes se guardan en UTC sin zona, como en el resto del motor.
En Windows las zonas IANA vienen del paquete `tzdata` (requirements.txt).
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def nombre() -> str:
    return os.environ.get("MONITOREO_ZONA_HORARIA", "").strip() or "local"


def zona(n: str | None = None):
    """Zona del motor (variable de entorno) o, si se da `n`, esa zona IANA (p. ej. la que declara
    el portal en cada respuesta: los "hoy" de un calendario se calculan en la zona del portal)."""
    n = n or nombre()
    if n == "local":
        return datetime.now().astimezone().tzinfo
    try:
        return ZoneInfo(n)
    except ZoneInfoNotFoundError as e:
        raise ValueError(f"Zona horaria desconocida en MONITOREO_ZONA_HORARIA: {n}") from e


def a_local(dt_utc_naive: datetime | None) -> datetime | None:
    if dt_utc_naive is None:
        return None
    return dt_utc_naive.replace(tzinfo=timezone.utc).astimezone(zona())


def hoy(ahora_utc_naive: datetime | None = None, zona_nombre: str | None = None) -> date:
    base = (ahora_utc_naive.replace(tzinfo=timezone.utc) if ahora_utc_naive
            else datetime.now(timezone.utc))
    try:
        z = zona(zona_nombre)
    except ValueError:
        z = zona()
    return base.astimezone(z).date()


def utc_naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    return utc_naive(datetime.fromisoformat(str(s).replace("Z", "+00:00")))
