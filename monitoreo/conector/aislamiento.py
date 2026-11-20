"""Capacidad de lanzar el conector: BLOQUEADA hasta demostrar aislamiento real del sistema operativo.

No bastan entorno limpio, Job Object ni ausencia de credenciales: un proceso del mismo usuario puede abrir
data/*.db. Se desbloquea solo con una prueba positiva (ver docs/CONECTOR_CONTRATO_v1.md, sección 4), que hoy
NO existe. Mientras tanto `puede_lanzar()` es siempre falso.
"""
from __future__ import annotations


def puede_lanzar() -> tuple[bool, str]:
    return False, ("BLOQUEADO: no hay aislamiento de sistema operativo demostrado (usuario local distinto con ACL "
                   "que niega la lectura de la base de Monitor y prueba negativa que lo confirme)")
