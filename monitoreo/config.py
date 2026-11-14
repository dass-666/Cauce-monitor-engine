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
    # utf-8-sig: el Bloc de notas de Windows 10 guarda con BOM y la primera clave se perdería.
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def _navegadores_de_la_instalacion() -> None:
    """Cierre v2.1: si PLAYWRIGHT_BROWSERS_PATH no está definida (ni en el entorno ni en .env) y la
    instalación tiene su propia carpeta `.browsers` con Chromium (scripts/preparar_navegador.py), se
    fija para este proceso y sus hijos (el observador, los recorridos). Ver monitoreo/navegador.py."""
    if (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip():
        return
    local = BASE_DIR / ".browsers"
    try:
        if any(x.name.startswith(("chromium-", "chromium_headless_shell-")) for x in local.iterdir()):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(local)
    except OSError:
        pass


_navegadores_de_la_instalacion()


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
    "enfriamiento_circuito_s": 60,     # modo "fijo": tiempo con el circuito abierto antes de probar
    # --- v1.4: enfriamiento adaptable (por defecto). Base = intervalo de la fuente × N (con mínimo);
    # se duplica en cada apertura seguida hasta el máximo; baja de forma gradual al recuperarse.
    "enfriamiento_modo": "adaptable",  # "adaptable" o "fijo" (comportamiento de v1.0–v1.3)
    "enfriamiento_intervalos": 5,
    "enfriamiento_min_s": 5,
    "enfriamiento_max_s": 300,
    "minutos_sin_exito_hasta_error": 15,  # modo adaptable: tiempo mínimo sin éxito antes de ERROR
    "aperturas_hasta_error": 5,        # aperturas seguidas del circuito -> fuente en ERROR (manual)
    "backoff_max_s": 600,              # tope del retroceso exponencial entre consultas
    "ausencias_para_cancelar": 2,      # consultas completas sin ver un recurso -> CANCELACION
    "ttl_asignacion_s": 900,           # tiempo para que el operador tome una asignación
    # v1.7: reparto entre operadores compatibles
    "reparto_modo": "menor_carga",     # menor_carga (v1.0-v1.6) | equitativo_dia (menos asignaciones hoy)
    "operador_ausente_s": 0,           # >0: no se asigna a quien no usa el panel hace más de N s (0 = no se mira)
    "operador_conexion_perdida_s": 90, # sin señal del panel más de N s: "conexión perdida" (supervisión)
    "operador_inactivo_s": 900,        # sin actividad más de N s: "inactivo" (supervisión)
    "alertas_silenciadas": [],         # categorías NO críticas sin envío; quedan en el historial
    "alertas_agrupar_s": 300,          # avisos iguales dentro de N s se agrupan en uno (repeticiones)
    "max_intentos_solicitud": 3,       # reasignaciones antes de marcar la solicitud EXPIRADO
    "tipos_que_alertan": ["NUEVA_DISPONIBILIDAD", "REAPARICION", "AUMENTO", "CANCELACION", "CAMBIO_HORARIO"],
    "alerta_minimo_aumento": 1,        # unidades mínimas de aumento para alertar
    "reintentos_notificacion": 5,
    "despacho_presupuesto_s": 10,       # v1.4: tiempo máximo de cada pasada de envío de alertas
    "despacho_canal_presupuesto_s": 3,  # v1.7: tiempo máximo de UN canal por pasada (un canal lento no frena a otros)
    "meta_citas_dia": 6,                # v2.0: meta diaria de citas CONFIRMADAS (panel de ventanilla)
    # --- v2.0: gestor de conexiones (salida a red del motor). Nunca se cambia de conexión por una
    # restricción del proveedor (429/403/límite): solo por fallos de RED, y solo en operaciones permitidas.
    "conexion_orden_tipos": ["ETHERNET", "WIFI", "USB", "MODEM"],
    "conexion_deshabilitadas": [],      # nombres de interfaz que el motor no debe usar
    "conexion_umbral_fallos": 2,        # fallos de red seguidos para dar una conexión por caída
    "conexion_reintento_s": 60,         # tras caer, cuánto esperar para volver a probarla
    "conexion_estable_s": 120,          # la preferida vuelve a usarse tras estar bien este tiempo
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
    # --- v1.4
    "inactividad_en_proceso_min": 30,           # aviso si una asignación EN_PROCESO no tiene actividad (0 = no)
    "retencion_operaciones_dias": 7,            # resultados guardados de operaciones con clave
    # --- v1.5: captura y reservaciones de laboratorio
    "captura_gracia_confirmando_s": 30,         # tras perder la respuesta, espera antes de reabrir el intento
    "captura_timeout_portal_s": 10,             # tiempo máximo de cada petición al portal de prueba
    # --- v1.6: modo de reservación, calendario por día y retención de datos personales
    "captura_modo": "POR_DIA",                  # modo de los expedientes NUEVOS: POR_DIA o POR_HORARIO
    "calendario_frescura_s": 60,                # sin verificación más tiempo: "pendiente de actualización"
    "retencion_confirmados_dias": 90,           # expedientes con folio: se anonimizan pasado este plazo
    "retencion_cerrados_dias": 30,              # cerrados sin folio (cancelados, fallidos)
    "retencion_incompletos_dias": 60,           # incompletos o en revisión sin cambios: se cierran y anonimizan
    "retencion_accion": "ANONIMIZAR",           # ANONIMIZAR (conserva la auditoría) o BORRAR (solo sin operaciones)
    "retencion_respaldos_dias": 30,             # respaldos más antiguos se borran (siempre quedan los 3 últimos)
    "retencion_expedientes_cada_s": 600,        # cada cuánto se aplica la política
}
