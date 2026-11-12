"""Configuración del sistema.

Toda la configuración sensible (clave secreta, contraseña inicial, URL de base de
datos) se lee de variables de entorno o de un archivo `.env` local que NUNCA se
sube al repositorio. Los parámetros operativos del motor (frecuencias, umbrales)
se guardan en la base de datos para poder cambiarlos desde el panel.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Carga un .env sencillo (CLAVE=valor) sin dependencias externas.

    Las variables ya definidas en el entorno tienen prioridad.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "si", "sí", "yes"}


@dataclass
class Settings:
    db_url: str = field(default_factory=lambda: os.environ.get(
        "MONITOREO_DB_URL", f"sqlite:///{(BASE_DIR / 'data' / 'monitoreo.db').as_posix()}"))
    # Clave para firmar webhooks. Si no se define se genera una por proceso
    # (válida para desarrollo; en producción debe fijarse en .env).
    secret_key: str = field(default_factory=lambda: os.environ.get("MONITOREO_SECRET_KEY") or secrets.token_urlsafe(32))
    admin_user: str = field(default_factory=lambda: os.environ.get("MONITOREO_ADMIN_USER", "admin"))
    admin_password: str | None = field(default_factory=lambda: os.environ.get("MONITOREO_ADMIN_PASSWORD"))
    token_ttl_hours: int = field(default_factory=lambda: int(os.environ.get("MONITOREO_TOKEN_TTL_HORAS", "12")))
    start_engine: bool = field(default_factory=lambda: _bool("MONITOREO_INICIAR_MOTOR", True))
    seed_demo: bool = field(default_factory=lambda: _bool("MONITOREO_DATOS_DEMO", True))
    # Lista de hosts permitidos para webhooks (mitiga SSRF). Vacío = solo localhost.
    webhook_allowed_hosts: list[str] = field(default_factory=lambda: [
        h.strip() for h in os.environ.get("MONITOREO_WEBHOOK_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()])
    data_dir: Path = BASE_DIR / "data"


settings = Settings()

# Parámetros del motor editables desde el panel (valores por defecto).
ENGINE_DEFAULTS: dict = {
    "tick_segundos": 0.5,              # cada cuánto el planificador revisa qué fuentes tocan
    "concurrencia_maxima": 5,          # consultas simultáneas como máximo
    "umbral_circuito": 3,              # fallos consecutivos para abrir el circuito
    "enfriamiento_circuito_s": 60,     # tiempo con el circuito abierto antes de probar
    "aperturas_hasta_error": 5,        # aperturas seguidas del circuito -> fuente en ERROR (manual)
    "backoff_max_s": 600,              # tope del retroceso exponencial entre consultas
    "ausencias_para_cancelar": 2,      # consultas completas sin ver un recurso -> CANCELACION
    "ttl_asignacion_s": 900,           # tiempo para que el operador tome una asignación
    "max_intentos_solicitud": 3,       # reasignaciones antes de marcar la solicitud EXPIRADO
    "tipos_que_alertan": ["NUEVA_DISPONIBILIDAD", "REAPARICION", "AUMENTO", "CANCELACION", "CAMBIO_HORARIO"],
    "alerta_minimo_aumento": 1,        # unidades mínimas de aumento para alertar
    "reintentos_notificacion": 5,
    "backoff_notificacion_s": 2,
    # --- v1.1: retención del historial (valores conservadores; 0 = no borrar nunca)
    "retencion_consultas_dias": 14,             # poll_log detallado (ya resumido en agregados)
    "retencion_eventos_sin_alerta_dias": 30,    # solo eventos NO relevantes y sin referencias
    "retencion_estadisticas_horarias_dias": 90, # los agregados diarios no se borran
    "retencion_registro_notificaciones_dias": 0,
    "retencion_lote": 1000,                     # filas por lote de borrado
    "retencion_lotes_por_pasada": 50,           # tope por pasada: limpieza gradual
    "retencion_pausa_lote_s": 0.05,             # respiro entre lotes para no bloquear al motor
    "mantenimiento_historial_cada_s": 300,
    # --- v1.1: coordinación entre instancias
    "lease_duracion_s": 15,
    # --- v1.2
    "autodiagnostico_cada_s": 600,              # 0 = desactivado
    "entrega_reclamo_vence_s": 300,             # una entrega en ENVIANDO más tiempo vuelve a la cola
}
