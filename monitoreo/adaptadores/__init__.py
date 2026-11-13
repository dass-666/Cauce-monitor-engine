"""v2.0 · P2 · Adaptadores de proveedor (contrato en base.py y docs/CONTRATO_ADAPTADORES.md)."""
from __future__ import annotations

import threading

from .base import Adaptador, Capacidades, NoAutorizado  # noqa: F401

_lock = threading.Lock()
_instancias: dict[str, Adaptador] = {}


def _crear(nombre: str) -> Adaptador:
    if nombre == "simulado":
        from .simulado import AdaptadorSimulado
        return AdaptadorSimulado()
    if nombre == "laboratorio":
        from .laboratorio import AdaptadorLaboratorio
        return AdaptadorLaboratorio()
    if nombre == "oficial_manual":
        from .sin_integracion import AdaptadorSinIntegracion
        return AdaptadorSinIntegracion()
    raise KeyError(nombre)


NOMBRES = ("laboratorio", "simulado", "oficial_manual")


def obtener(nombre: str) -> Adaptador:
    if nombre not in NOMBRES:
        raise KeyError(nombre)
    with _lock:
        if nombre not in _instancias:
            _instancias[nombre] = _crear(nombre)
        return _instancias[nombre]


def registrar(nombre: str, adaptador: Adaptador) -> None:
    """Para pruebas: sustituye una instancia."""
    with _lock:
        _instancias[nombre] = adaptador


def listar() -> list[dict]:
    return [obtener(n).capacidades().como_dict() for n in NOMBRES]
