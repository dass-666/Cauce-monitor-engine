"""Qué rutas monta cada entorno (preparación del flujo administrativo para producción).

Hasta la candidata RC1 + v2.4, en producción `app.py` solo montaba la API básica y la recuperación: todo el
flujo administrativo (importar, expedientes, consentimiento, ventanilla, plan del día, mesa de control) era
de laboratorio. Este módulo separa dos grupos:

* FLUJO_ADMIN: rutas del trabajo real del administrador y sus operadores. En desarrollo y pruebas se montan
  siempre (como antes). En producción solo se montan si se activa de forma EXPLÍCITA:
      MONITOREO_PRODUCCION_FLUJO_ADMIN=1
  Por omisión está DESACTIVADO: la producción sigue como en la candidata. Los controles de acceso no cambian
  porque se montan los mismos routers (require_admin, current_user, consentimiento, recuperación, auditoría).
  Al activarlo, `entorno.review()` exige condiciones previas (ver `requisitos_produccion`); si faltan, el
  servidor NO arranca y explica qué falta.

* SOLO_LABORATORIO: herramientas de simulación y del portal de prueba propio (laboratorio, rutas de prueba,
  emulador de Telegram, «Calendario del laboratorio», gestor de conexiones, adaptadores P2, simulación de
  caída del navegador). NUNCA se montan en producción, con o sin la variable.
"""
from __future__ import annotations

import ipaddress
import os
from importlib import import_module
from urllib.parse import urlparse

VARIABLE = "MONITOREO_PRODUCCION_FLUJO_ADMIN"
_SI = ("1", "true", "si", "sí", "yes")

# (módulo, atributo del router, descripción). El orden es el de montaje (igual que en la candidata).
FLUJO_ADMIN = [
    ("monitoreo.api.captura", "router", "Expedientes y reservaciones (captura)"),
    ("monitoreo.api.consentimiento", "router", "Consentimiento verificable y trazabilidad"),
    ("monitoreo.api.ventanilla", "router", "Ventanilla (atención presencial)"),
    ("monitoreo.api.proveedor", "router", "Identidad del proveedor de esta instancia"),
    ("monitoreo.api.planificador", "router", "Plan del día, observador y coincidencias"),
    ("monitoreo.api.administracion", "router", "Mesa de control e importación por lotes"),
    ("monitoreo.api.asistida", "router", "Piloto asistido: confirmación humana de citas del portal oficial"),
]
SOLO_LABORATORIO = [
    ("monitoreo.api.routes", "lab_router", "Laboratorio de simulación (/api/lab)"),
    ("monitoreo.api.laboratorio_portal", "router", "Calendario del portal de laboratorio"),
    ("monitoreo.api.conexiones", "router", "Gestor de conexiones (redes de prueba)"),
    ("monitoreo.api.adaptadores", "router", "Adaptadores P2 y captura manual de prueba"),
    ("monitoreo.api.planificador", "router_lab", "Simular caída del navegador del observador"),
    ("monitoreo.api.test_api", "router", "Rutas de prueba (/api-prueba)"),
    ("monitoreo.emuladores.telegram", "router", "Emulador de Telegram"),
]


def flujo_admin_solicitado(env: dict | None = None) -> bool:
    env = os.environ if env is None else env
    return (env.get(VARIABLE) or "").strip().lower() in _SI


def flujo_admin_activo(env: dict | None = None) -> bool:
    """True si el flujo administrativo está disponible en este proceso (siempre fuera de producción)."""
    from . import entorno
    env = os.environ if env is None else env
    ent = (env.get("MONITOREO_ENTORNO", entorno.DESARROLLO) or "").strip().lower().replace("ó", "o")
    return ent != entorno.PRODUCCION or flujo_admin_solicitado(env)


def _loopback(url: str) -> bool:
    host = (urlparse(url).hostname or "").strip("[]")
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def requisitos_produccion(env: dict | None = None) -> list[str]:
    """Condiciones para activar el flujo administrativo en producción. Cada texto es un error que impide
    arrancar. Lista vacía = se puede activar. Solo se evalúa si se pidió la activación."""
    env = os.environ if env is None else env
    problemas: list[str] = []
    portal = (env.get("MONITOREO_PORTAL_URL") or "http://127.0.0.1:8200").strip()
    if not _loopback(portal):
        problemas.append(
            f"{VARIABLE}=1 con MONITOREO_PORTAL_URL fuera de este equipo ({urlparse(portal).hostname}). Operar un "
            "portal externo exige la autorización expresa de su proveedor y esta versión no lo implementa: usa el "
            "portal propio en 127.0.0.1 o deja la variable en 0.")
    if (env.get("MONITOREO_EXIGIR_CONSENTIMIENTO", "1") or "").strip().lower() in ("0", "no", "false"):
        problemas.append(f"{VARIABLE}=1 exige MONITOREO_EXIGIR_CONSENTIMIENTO=1: en producción no se opera sin "
                         "consentimiento verificable.")
    if not (env.get("MONITOREO_DIARIO_COPIA") or "").strip():
        problemas.append(f"{VARIABLE}=1 exige la copia externa del diario (MONITOREO_DIARIO_COPIA, en otra unidad). "
                         "Sin ella, perder el disco borra las revocaciones y citas posteriores al último respaldo. "
                         "Ver docs/RESPALDO_EXTERNO_DIARIO.md.")
    return problemas


def _router(modulo: str, attr: str):
    return getattr(import_module(modulo), attr)


def montar(app, production: bool, env: dict | None = None) -> dict:
    """Monta los routers según el entorno. Devuelve qué se montó (para /api/info y el autodiagnóstico)."""
    flujo = (not production) or flujo_admin_solicitado(env)
    montados = []
    if flujo:
        for m, a, desc in FLUJO_ADMIN:
            app.include_router(_router(m, a))
            montados.append(desc)
    if not production:
        for m, a, desc in SOLO_LABORATORIO:
            app.include_router(_router(m, a))
    return {"flujo_admin": flujo, "laboratorio": not production, "flujo_admin_modulos": montados}
