"""E8 · Centro de revisiones pendientes: resolver desde Monitor una marca «revisar a mano» de Network Engine.

Separación de credenciales (decisión de diseño):
- La LECTURA usa la credencial `read` de siempre (MONITOREO_NETWORK_TOKEN_FILE). Con ella Monitor ve las revisiones
  pendientes, pero Network Engine le responde 403 si intenta resolver.
- RESOLVER exige una credencial DISTINTA con el permiso `review` de Network Engine (MONITOREO_NETWORK_REVISIONES_TOKEN_FILE,
  creada con `cauce token create --name monitor-revisiones --scope review --out ARCHIVO`). Ese permiso solo ve y resuelve
  revisiones pendientes: no crea credenciales, no lee la red, no toca rutas ni apaga el motor. Nunca la credencial
  maestra del motor. Por omisión no está configurada y Monitor muestra la orden para el equipo del motor.
- Si el archivo de revisiones es el mismo, o contiene la misma credencial que el de lectura, se rechaza.
- Solo un usuario de Monitor con rol administrador puede usarlo; nota obligatoria y confirmación escrita; se audita en
  Monitor y en Network Engine (que valida de nuevo nota y confirmación).
"""
from __future__ import annotations

import os
import re
import sys
from typing import Mapping, Optional

from .lector import ARCHIVO_TOKEN_ENV, DIRECTORIO_ENV, PUERTO_DEFECTO, PUERTO_ENV, _ruta_red

ARCHIVO_REVISIONES_ENV = "MONITOREO_NETWORK_REVISIONES_TOKEN_FILE"
ID_RE = re.compile(r"^pm_[0-9a-f]{12}$")
NOTA_MIN, NOTA_MAX = 5, 500
MENSAJES = {"id_invalido": "Identificador de revisión no válido.",
            "nota_obligatoria": "La nota es obligatoria (5 a 500 caracteres).",
            "confirmacion_obligatoria": "Falta la confirmación escrita.",
            "confirmation_mismatch": "La confirmación no coincide: no se resolvió nada.",
            "unknown_pending": "Esa revisión ya no está pendiente (quizá la resolvió otra persona).",
            "note_required": "Network Engine exige una nota.", "insufficient_scope": "La credencial configurada no tiene el permiso «review».",
            "invalid_token": "La credencial de revisiones no es válida o fue revocada."}


class NoHabilitado(Exception):
    """La resolución desde Monitor no está configurada (o lo está de forma insegura)."""

    def __init__(self, razon: str):
        super().__init__(razon)
        self.razon = razon


def orden_cli(pid: str, confirmar: str) -> str:
    return f'cauce revisiones resolver {pid} --nota "qué revisaste y qué hiciste" --confirmar "{confirmar}"'


def _leer(ruta: str) -> str:
    from cauce.secrets_store import read_secret       # DPAPI en Windows; nunca se registra el valor
    return read_secret(ruta).strip()


def cliente_revisiones(entorno: Optional[Mapping[str, str]] = None):
    env = os.environ if entorno is None else entorno
    ruta = (env.get(ARCHIVO_REVISIONES_ENV) or "").strip()
    if not ruta:
        raise NoHabilitado("no_configurado")
    lectura = (env.get(ARCHIVO_TOKEN_ENV) or "").strip()
    if lectura and os.path.abspath(lectura) == os.path.abspath(ruta):
        raise NoHabilitado("misma_credencial")
    try:
        puerto = int(env.get(PUERTO_ENV, str(PUERTO_DEFECTO)))
    except ValueError:
        raise NoHabilitado("puerto_invalido") from None
    ruta_red = _ruta_red(env)
    if env.get(DIRECTORIO_ENV) and ruta_red is None:
        raise NoHabilitado("directorio_invalido")
    if ruta_red and ruta_red not in sys.path:
        sys.path.append(ruta_red)
    try:
        from cauce.client import CauceClient
    except ImportError:
        raise NoHabilitado("paquete_no_instalado") from None
    try:
        token = _leer(ruta)
        if lectura and os.path.isfile(lectura) and _leer(lectura) == token:
            raise NoHabilitado("misma_credencial")
    except NoHabilitado:
        raise
    except Exception:                                 # noqa: BLE001  ni la ruta ni el motivo salen de aquí
        raise NoHabilitado("credencial_ilegible") from None
    return CauceClient(token, port=puerto, timeout=10)


def validar(pid: str, nota: str, confirmar: str) -> Optional[str]:
    """Código de error, o None si la petición es válida (Network Engine vuelve a validar)."""
    if not isinstance(pid, str) or not ID_RE.match(pid):
        return "id_invalido"
    if not isinstance(nota, str) or not NOTA_MIN <= len(nota.strip()) <= NOTA_MAX:
        return "nota_obligatoria"
    if not isinstance(confirmar, str) or not confirmar.strip():
        return "confirmacion_obligatoria"
    return None


def resolver(pid: str, nota: str, confirmar: str, entorno: Optional[Mapping[str, str]] = None) -> dict:
    c = cliente_revisiones(entorno)
    return c.resolve_pending(pid, nota.strip(), confirmar.strip())
