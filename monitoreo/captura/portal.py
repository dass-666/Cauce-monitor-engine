"""Conexión con el portal de reservaciones de PRUEBA (configurable, sin valores reales).

  MONITOREO_PORTAL_URL     dirección del portal de prueba (por defecto http://127.0.0.1:8200)
  MONITOREO_PORTAL_TOKEN   token Bearer de PRUEBA de su API de verificación
  MONITOREO_PORTAL_CONTRATO  contrato de campos (por defecto portal_laboratorio)

El host debe estar en MONITOREO_WEBHOOK_HOSTS (por defecto solo localhost/127.0.0.1): el motor
no puede apuntarse por configuración a un servicio externo.
"""
from __future__ import annotations

import os

from ..config import settings
from .formularios.contrato import cargar
from .formularios.navegador import NavegadorHTTP


TOKEN_LOCAL = "token-prueba-portal-local"   # solo laboratorio en 127.0.0.1 (si .env no trae uno)


def token() -> str:
    return os.environ.get("MONITOREO_PORTAL_TOKEN") or TOKEN_LOCAL


def url() -> str:
    return os.environ.get("MONITOREO_PORTAL_URL", "http://127.0.0.1:8200").rstrip("/")


def contrato():
    return cargar(os.environ.get("MONITOREO_PORTAL_CONTRATO", "portal_laboratorio"))


def navegador(sesion: str | None = None, timeout: float = 10) -> NavegadorHTTP:
    return NavegadorHTTP(url(), contrato(), token_api=token(), sesion=sesion,
                         timeout=timeout, allowed_hosts=list(settings.webhook_allowed_hosts))
