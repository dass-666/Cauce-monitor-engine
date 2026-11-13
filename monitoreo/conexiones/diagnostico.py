"""v2.0 · Diagnóstico y observabilidad de la conectividad hacia el portal.

- `ultimas_24h(s, now)`: cifras persistentes (tabla conexion_eventos) que sobreviven a reinicios:
  restricciones del proveedor con su motivo, caídas/recuperaciones, cambios de conexión y resultado
  de las peticiones por operación.
- `revisar(...)`: lo usa el autodiagnóstico (consola y panel). Interfaces encontradas, cuáles sirven,
  resolución del destino y, en modo profundo, una conexión TCP de prueba al portal CONFIGURADO
  (no envía nada; con el portal en este equipo no se mide nada).

Nada de esto cambia el comportamiento ante 403/429: una restricción se informa y la operación queda
detenida; el diagnóstico solo aconseja revisar la cuota o el permiso con el proveedor.
"""
from __future__ import annotations

import socket
from collections import Counter
from datetime import datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import select

from ..models import ConexionEvento


def ultimas_24h(s, now: datetime, horas: int = 24) -> dict:
    desde = now - timedelta(hours=horas)
    evs = list(s.scalars(select(ConexionEvento).where(ConexionEvento.ts >= desde).order_by(ConexionEvento.id)))
    por_evento = Counter(e.evento for e in evs)
    peticiones: Counter = Counter()
    por_operacion: dict[str, Counter] = {}
    for e in evs:
        if e.evento == "USO":
            p = (e.detalle or {}).get("peticiones") or {}
            peticiones.update({k: int(v) for k, v in p.items() if v})
            por_operacion.setdefault(e.operacion or "—", Counter()).update({k: int(v) for k, v in p.items() if v})
    restr = [e for e in evs if e.evento == "RESTRICCION"]
    total = sum(peticiones.values())
    return {"horas": horas,
            "eventos": {k: por_evento.get(k, 0) for k in ("CAMBIO", "CAIDA", "RECUPERADA", "RESTRICCION", "USO")},
            "peticiones": dict(peticiones), "peticiones_total": total,
            "tasa_error_red": round(peticiones.get("RED", 0) / total, 4) if total else None,
            "por_operacion": {k: dict(v) for k, v in sorted(por_operacion.items())},
            "restricciones": [{"ts": e.ts.isoformat() + "Z", "conexion": e.conexion, "operacion": e.operacion,
                               "motivo": (e.detalle or {}).get("motivo", "")[:200]} for e in restr[-10:]][::-1]}


def destino(url: str) -> tuple[str, int]:
    u = urlparse(url)
    return u.hostname or "", u.port or (443 if u.scheme == "https" else 80)


def revisar(db, now: datetime, url_portal: str, profundo: bool = False) -> list[dict]:
    """Devuelve elementos {estado, titulo, detalle, accion, clave} (estados OK|INFO|AVISO|CRITICO)."""
    from . import gestor
    from .gestor import es_local, sonda_tcp
    from ..engine.params import get_params
    out: list[dict] = []
    host, puerto = destino(url_portal)
    local = es_local(host)
    g = gestor()
    ifs = g.actualizar(forzar=profundo)
    with db.session() as s:
        params = get_params(s)
        u24 = ultimas_24h(s, now)
    usables = [i for i in ifs if i.usable]
    fuera = set(params.get("conexion_deshabilitadas") or [])
    cand = [i for i in usables if i.nombre not in fuera]
    tipos = ", ".join(f"{i.nombre} ({i.tipo})" for i in cand) or "ninguna"
    if local:
        out.append({"estado": "OK", "titulo": "El portal configurado está en este mismo equipo",
                    "detalle": f"{host}:{puerto}. Interfaces utilizables: {tipos}.", "accion": "", "clave": ""})
    elif not cand:
        out.append({"estado": "CRITICO", "titulo": "No hay ninguna conexión de red utilizable hacia el portal",
                    "detalle": f"Interfaces vistas: {len(ifs)}; utilizables: {len(usables)}; deshabilitadas por "
                               f"configuración: {', '.join(sorted(fuera)) or 'ninguna'}.",
                    "accion": "Conecta el cable o el wifi, o revisa «conexion_deshabilitadas» en Configuración.",
                    "clave": "red:sin_conexion"})
    else:
        out.append({"estado": "OK" if len(cand) > 1 else "INFO",
                    "titulo": f"{len(cand)} conexión(es) utilizable(s) hacia el portal",
                    "detalle": f"{tipos}. En uso: {g.activa or 'aún ninguna'}."
                               + ("" if len(cand) > 1 else " Sin conexión de respaldo."),
                    "accion": "" if len(cand) > 1 else "Opcional: una segunda conexión (otro cable o wifi) da respaldo "
                                                       "si la principal no conecta.", "clave": ""})
    if host and not local:
        try:
            socket.getaddrinfo(host, puerto, type=socket.SOCK_STREAM)
        except OSError as e:
            out.append({"estado": "CRITICO", "titulo": "No se puede resolver el nombre del portal",
                        "detalle": f"{host}: {e.__class__.__name__}", "accion": "Revisa el DNS o la dirección configurada.",
                        "clave": "red:dns"})
        if profundo and cand:
            for i in cand[:4]:
                try:
                    ms = sonda_tcp(i.ipv4[0], host, puerto, 3.0)
                    out.append({"estado": "OK", "titulo": f"{i.nombre}: el portal acepta conexiones",
                                "detalle": f"conexión TCP en {ms:.0f} ms (no se envió nada)", "accion": "", "clave": ""})
                except OSError as e:
                    out.append({"estado": "AVISO", "titulo": f"{i.nombre}: el portal no acepta conexiones por aquí",
                                "detalle": e.__class__.__name__, "accion": "Si es la conexión principal, revisa la red.",
                                "clave": f"red:sonda:{i.nombre}"})
    r = u24["eventos"]["RESTRICCION"]
    if r:
        ult = u24["restricciones"][0]
        out.append({"estado": "AVISO", "titulo": f"El proveedor restringió {r} operación(es) en 24 h",
                    "detalle": f"Última: {ult['ts']} · {ult['operacion'] or '—'} · {ult['motivo']}. "
                               "Cada una se detuvo sin reintentar ni cambiar de conexión.",
                    "accion": "No insistas por otra vía: revisa con el proveedor la cuota o el permiso de acceso.",
                    "clave": "red:restriccion"})
    caidas = u24["eventos"]["CAIDA"]
    if caidas:
        out.append({"estado": "AVISO" if caidas >= 3 else "INFO", "titulo": f"{caidas} caída(s) de conexión en 24 h",
                    "detalle": f"Recuperadas: {u24['eventos']['RECUPERADA']}; cambios de conexión: {u24['eventos']['CAMBIO']}; "
                               f"tasa de error de red: {u24['tasa_error_red'] if u24['tasa_error_red'] is not None else '—'}.",
                    "accion": "Si se repite, revisa el cable/wifi de la conexión que cae." if caidas >= 3 else "",
                    "clave": "red:caidas" if caidas >= 3 else ""})
    return out
