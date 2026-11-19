"""Conexión con el portal de reservaciones de PRUEBA (configurable, sin valores reales).

  MONITOREO_PORTAL_URL     dirección del portal de prueba (por defecto http://127.0.0.1:8200)
  MONITOREO_PORTAL_TOKEN   token Bearer de PRUEBA de su API de verificación
  MONITOREO_PORTAL_CONTRATO  contrato de campos POR_HORARIO (por defecto portal_laboratorio)
  MONITOREO_PORTAL_CONTRATO_DIA  contrato POR_DIA (v1.6; por defecto portal_laboratorio_dia)

El host debe estar en MONITOREO_WEBHOOK_HOSTS (por defecto solo localhost/127.0.0.1): el motor
no puede apuntarse por configuración a un servicio externo.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from ..config import settings
from .formularios.contrato import cargar
from .formularios.navegador import NavegadorHTTP


TOKEN_LOCAL = "token-prueba-portal-local"   # solo laboratorio en 127.0.0.1 (si .env no trae uno)


def token() -> str:
    return os.environ.get("MONITOREO_PORTAL_TOKEN") or TOKEN_LOCAL


def url() -> str:
    return os.environ.get("MONITOREO_PORTAL_URL", "http://127.0.0.1:8200").rstrip("/")


def contrato(modo: str | None = None):
    """Contrato de la modalidad (sin modalidad: el de la v1.5, POR_HORARIO)."""
    if modo == "POR_DIA":
        return cargar(os.environ.get("MONITOREO_PORTAL_CONTRATO_DIA", "portal_laboratorio_dia"))
    return cargar(os.environ.get("MONITOREO_PORTAL_CONTRATO", "portal_laboratorio"))


def navegador(sesion: str | None = None, timeout: float = 10, modo: str | None = None,
              operacion: str | None = None, conexion: str | None = None, params: dict | None = None,
              ref: dict | None = None) -> NavegadorHTTP:
    """v2.0: la conexión de red la elige el gestor de conexiones. Sin `operacion` se deduce: con
    sesión, `continuar_sesion` (misma conexión que la sesión, `conexion`); sin sesión, `nueva_sesion`.
    El navegador devuelto lleva `.conexion` (para guardarla con la reservación) y, al cerrarse, deja
    un evento USO con lo que pasó (sin datos de personas)."""
    from .. import conexiones
    from ..config import ENGINE_DEFAULTS
    params = params or ENGINE_DEFAULTS
    if operacion is None:
        operacion = "continuar_sesion" if sesion else "nueva_sesion"
    base = url()
    g = conexiones.gestor()
    fija = (conexion or conexiones.LOCAL) if operacion in ("continuar_sesion", "confirmar") else None
    el = g.elegir(operacion, params, host=urlparse(base).hostname, fija=fija)
    cuenta = {"OK": 0, "RED": 0, "RESTRICCION": 0, "PORTAL": 0}

    usadas = [el]

    def informar(resultado: str, ms: float, detalle: str) -> None:
        cuenta[resultado] += 1
        g.registrar(usadas[-1].nombre, operacion, resultado, params, ms, detalle)

    def cambiar_conexion(motivo: str) -> str | None:
        """Respaldo: solo operaciones que lo admiten y solo si hay OTRA conexión utilizable.
        Devuelve la nueva dirección de origen ("" = sin fijar) o None si no hay cambio."""
        actual = usadas[-1]
        if operacion not in conexiones.gestor_mod.OPS_CON_RESPALDO or actual.nombre == conexiones.LOCAL:
            return None
        g.marcar_caida(actual.nombre, operacion, f"no se pudo establecer la conexión ({motivo})")
        try:
            otra = g.elegir(operacion, params, host=urlparse(base).hostname)
        except conexiones.SinConexion:
            return None
        if otra.nombre == actual.nombre:
            return None
        usadas.append(otra)
        nav.conexion = otra.nombre
        return otra.ip_origen or ""

    nav = NavegadorHTTP(base, contrato(modo), token_api=token(), sesion=sesion, timeout=timeout,
                        allowed_hosts=list(settings.webhook_allowed_hosts), local_address=el.ip_origen,
                        informar=informar, cambiar_conexion=cambiar_conexion)
    nav.conexion = el.nombre
    cerrar = nav.cerrar

    def cerrar_y_anotar() -> None:
        cerrar()
        if any(cuenta.values()):
            fin = usadas[-1]
            conexiones.anotar("USO", conexion=fin.nombre, tipo=fin.tipo, operacion=operacion, peticiones=dict(cuenta),
                              motivo=fin.motivo, respaldo_desde=usadas[0].nombre if len(usadas) > 1 else None,
                              **(ref or {}))
    nav.cerrar = cerrar_y_anotar
    return nav
