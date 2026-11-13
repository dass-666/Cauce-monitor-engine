"""v2.0 · Gestor de conexiones de red del motor (ver `gestor.py` para las reglas).

Un gestor por proceso. Sus eventos (cambio, caída, recuperación, restricción, uso) se acumulan en
memoria y se escriben en `conexion_eventos` con `volcar(db)`, que llaman las operaciones con el
portal al terminar y el mantenimiento del motor.
"""
from __future__ import annotations

import threading
from collections import deque

from . import gestor as gestor_mod  # noqa: F401
from .gestor import LOCAL, OPERACIONES, Eleccion, Gestor, SinConexion  # noqa: F401

_lock = threading.Lock()
_pendientes: deque = deque(maxlen=5000)
_gestor: Gestor | None = None


def _anotar(evento: str, detalle: dict) -> None:
    from ..db import utcnow
    _pendientes.append((utcnow(), evento, dict(detalle)))


def gestor() -> Gestor:
    global _gestor
    with _lock:
        if _gestor is None:
            _gestor = Gestor(al_evento=_anotar)
        return _gestor


def usar(g: Gestor | None) -> None:
    """Pruebas y laboratorio: sustituir el gestor (p. ej. por uno con interfaces simuladas)."""
    global _gestor
    with _lock:
        _gestor = g
        if g is not None:
            g.al_evento = _anotar


def anotar(evento: str, **detalle) -> None:
    _anotar(evento, detalle)


def volcar(db) -> int:
    """Escribe los eventos pendientes. Si la base falla, se conservan para la próxima vez."""
    from ..models import ConexionEvento
    lote = []
    while _pendientes and len(lote) < 1000:
        lote.append(_pendientes.popleft())
    if not lote:
        return 0
    try:
        with db.session() as s:
            for ts, evento, d in lote:
                d = dict(d)
                s.add(ConexionEvento(ts=ts, evento=evento, conexion=(d.pop("conexion", None) or None),
                                     tipo=d.pop("tipo", None), operacion=d.pop("operacion", None), detalle=d))
    except Exception:
        for x in reversed(lote):
            _pendientes.appendleft(x)
        raise
    return len(lote)
