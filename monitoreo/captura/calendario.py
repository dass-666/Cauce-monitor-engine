"""Calendario de laboratorio (v1.5): disponibilidad de horarios del portal de PRUEBA, por
sucursal y fecha, leída de su API de consulta. Solo lectura: no aparta ni reserva nada."""
from __future__ import annotations

import httpx

from ..config import settings
from ..connectors.http_api import USER_AGENT, check_url
from . import portal


class CalendarioNoDisponible(Exception):
    pass


def leer(region: str | None = None, sucursal: str | None = None, tramite: str | None = None,
         timeout: float = 5) -> dict:
    base = portal.url()
    check_url(base, list(settings.webhook_allowed_hosts))
    params = {k: v for k, v in {"region": region.lower() if region else None, "sucursal": sucursal,
                                "tramite": tramite}.items() if v}
    h = {"User-Agent": USER_AGENT}
    h["Authorization"] = f"Bearer {portal.token()}"
    try:
        r = httpx.get(base + "/api/disponibilidad", params=params, headers=h, timeout=timeout, trust_env=False)
    except httpx.HTTPError as e:
        raise CalendarioNoDisponible(f"el portal de prueba no responde ({e.__class__.__name__})") from e
    if r.status_code != 200:
        raise CalendarioNoDisponible(f"el portal de prueba respondió HTTP {r.status_code}")
    horarios = r.json()["horarios"]
    sucursales: dict[str, dict] = {}
    for h in horarios:
        s = sucursales.setdefault(h["sucursal"], {"sucursal": h["sucursal"], "nombre": h["sucursal_nombre"],
                                                  "region": h["region"], "dias": {}})
        s["dias"].setdefault(h["fecha"], []).append({"id": h["id"], "hora": h["hora"], "libres": h["libres"],
                                                     "capacidad": h["capacidad"]})
    out = []
    for s in sorted(sucursales.values(), key=lambda x: x["sucursal"]):
        dias = [{"fecha": f, "horarios": sorted(hs, key=lambda x: x["hora"]),
                 "libres": sum(x["libres"] for x in hs)} for f, hs in sorted(s["dias"].items())]
        out.append({**s, "dias": dias, "libres": sum(d["libres"] for d in dias)})
    return {"portal": base, "sucursales": out, "total_libres": sum(s["libres"] for s in out),
            "total_horarios": len(horarios)}
