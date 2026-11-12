"""Parámetros del motor guardados en la tabla settings (editables desde el panel)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..config import ENGINE_DEFAULTS
from ..models import Setting


def get_params(session: Session) -> dict:
    row = session.get(Setting, "engine")
    params = dict(ENGINE_DEFAULTS)
    if row and isinstance(row.value, dict):
        params.update({k: v for k, v in row.value.items() if k in ENGINE_DEFAULTS})
    return params


def set_params(session: Session, changes: dict) -> dict:
    unknown = set(changes) - set(ENGINE_DEFAULTS)
    if unknown:
        raise ValueError(f"Parámetros desconocidos: {sorted(unknown)}")
    current = get_params(session)
    for k, v in changes.items():
        default = ENGINE_DEFAULTS[k]
        if isinstance(default, list):
            if not isinstance(v, list):
                raise ValueError(f"{k} debe ser una lista")
        elif isinstance(default, (int, float)):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                raise ValueError(f"{k} debe ser un número >= 0")
        current[k] = v
    row = session.get(Setting, "engine")
    if row is None:
        session.add(Setting(key="engine", value=current))
    else:
        row.value = current
    return current
