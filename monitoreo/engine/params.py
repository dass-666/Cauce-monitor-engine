"""Parámetros del motor guardados en la tabla settings (editables desde el panel)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..config import ENGINE_DEFAULTS
from ..models import Setting


CHOICES = {"enfriamiento_modo": ["adaptable", "fijo"]}

# v1.4 (auditoría): límites de los parámetros cuyo valor extremo deja el motor inservible o
# consume la CPU (p. ej. tick_segundos = 0 hacía girar el planificador sin pausa).
LIMITS = {
    "tick_segundos": (0.05, 60), "concurrencia_maxima": (1, 200), "umbral_circuito": (1, 100),
    "aperturas_hasta_error": (1, 1000), "enfriamiento_circuito_s": (1, 86400), "enfriamiento_intervalos": (1, 1000),
    "enfriamiento_min_s": (1, 86400), "enfriamiento_max_s": (1, 86400), "minutos_sin_exito_hasta_error": (0, 100000),
    "backoff_max_s": (1, 86400), "ausencias_para_cancelar": (1, 1000), "ttl_asignacion_s": (10, 7 * 86400),
    "max_intentos_solicitud": (1, 1000), "reintentos_notificacion": (0, 100), "despacho_presupuesto_s": (0.5, 600), "backoff_notificacion_s": (0.1, 3600),
    "retencion_lote": (1, 100000), "retencion_lotes_por_pasada": (1, 100000), "retencion_pausa_lote_s": (0, 10),
    "mantenimiento_historial_cada_s": (10, 7 * 86400), "lease_duracion_s": (3, 3600),
    "entrega_reclamo_vence_s": (10, 86400), "inactividad_en_proceso_min": (0, 7 * 24 * 60),
    "captura_gracia_confirmando_s": (1, 3600), "captura_timeout_portal_s": (1, 120),
}


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
        elif k in CHOICES:
            if v not in CHOICES[k]:
                raise ValueError(f"{k} debe ser uno de {CHOICES[k]}")
        elif isinstance(default, (int, float)):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                raise ValueError(f"{k} debe ser un número >= 0")
            lo, hi = LIMITS.get(k, (0, float("inf")))
            if not lo <= v <= hi:
                raise ValueError(f"{k} debe estar entre {lo:g} y {hi:g}")
        current[k] = v
    if current["enfriamiento_max_s"] < current["enfriamiento_min_s"]:
        raise ValueError("enfriamiento_max_s no puede ser menor que enfriamiento_min_s")
    row = session.get(Setting, "engine")
    if row is None:
        session.add(Setting(key="engine", value=current))
    else:
        row.value = current
    return current
