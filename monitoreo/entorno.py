"""Entorno de ejecución (v1.2): DESARROLLO, PRUEBAS o PRODUCCIÓN.

Se elige con MONITOREO_ENTORNO = desarrollo | pruebas | produccion (por defecto desarrollo,
que conserva el comportamiento de la v1.0/v1.1).

| Función                                   | desarrollo | pruebas | produccion |
|-------------------------------------------|------------|---------|------------|
| Laboratorio (/api/lab/*)                  | sí         | sí      | NO         |
| Rutas de prueba (/api-prueba/*) y emulador Telegram | sí | sí      | NO         |
| Datos de demostración                     | según .env | según .env | NUNCA   |
| Documentación interactiva (/api/docs)     | sí         | sí      | NO         |
| Detector de sesiones anidadas             | opcional   | sí      | no         |
| Comprobación estricta de configuración    | avisos     | avisos  | ERRORES: no arranca |
| Registro en archivo con rotación          | no         | no      | data/logs/  |

En producción el servidor se NIEGA a arrancar si la configuración es insegura y explica qué
cambiar. Así un error de configuración se ve antes de exponer el sistema, no después.
"""
from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

DESARROLLO, PRUEBAS, PRODUCCION = "desarrollo", "pruebas", "produccion"
VALIDOS = (DESARROLLO, PRUEBAS, PRODUCCION)
# Valores de ejemplo que nunca deben llegar a producción.
CLAVES_DE_EJEMPLO = {"", "cambiame", "changeme", "secret", "secreto", "clave", "x", "resistencia", "migracion",
                     "clave-de-pruebas", "caida"}


class ConfigError(Exception):
    """Configuración insegura o inválida: el servidor no debe arrancar."""

    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


def current() -> str:
    value = os.environ.get("MONITOREO_ENTORNO", DESARROLLO).strip().lower()
    value = value.replace("ó", "o")
    if value not in VALIDOS:
        raise ConfigError([f"MONITOREO_ENTORNO='{value}' no es válido. Usa: {', '.join(VALIDOS)}"])
    return value


def is_production() -> bool:
    return current() == PRODUCCION


def lab_enabled() -> bool:
    return current() != PRODUCCION


@dataclass
class Revision:
    entorno: str
    errores: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"entorno": self.entorno, "errores": self.errores, "avisos": self.avisos}


def _is_loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def review(env: dict | None = None) -> Revision:
    """Revisa la configuración. En producción los `errores` impiden arrancar."""
    env = os.environ if env is None else env
    ent = env.get("MONITOREO_ENTORNO", DESARROLLO).strip().lower().replace("ó", "o")
    r = Revision(entorno=ent)
    if ent not in VALIDOS:
        r.errores.append(f"MONITOREO_ENTORNO='{ent}' no es válido. Usa: {', '.join(VALIDOS)}")
        return r
    prod = ent == PRODUCCION
    bad = r.errores.append if prod else r.avisos.append

    key = env.get("MONITOREO_SECRET_KEY", "")
    if key.strip().lower() in CLAVES_DE_EJEMPLO or len(key) < 32:
        bad("MONITOREO_SECRET_KEY falta, es de ejemplo o tiene menos de 32 caracteres. Genera una con: "
            "python -c \"import secrets; print(secrets.token_urlsafe(32))\"")
    pwd = env.get("MONITOREO_ADMIN_PASSWORD")
    if pwd is not None and pwd != "":
        from .security import password_problems
        problems = password_problems(pwd)
        if prod and len(pwd) < 12:
            problems.append("en producción, mínimo 12 caracteres")
        if problems:
            bad("MONITOREO_ADMIN_PASSWORD es débil: " + ", ".join(problems))
    if prod and env.get("MONITOREO_DATOS_DEMO", "").strip().lower() in ("1", "true", "si", "sí", "yes"):
        r.errores.append("MONITOREO_DATOS_DEMO=1 no está permitido en producción (ponlo en 0).")
    tg_api = env.get("MONITOREO_TELEGRAM_API", "")
    if tg_api and urlparse(tg_api).scheme != "https":
        bad(f"MONITOREO_TELEGRAM_API apunta a '{urlparse(tg_api).hostname}' sin HTTPS (¿el emulador?). "
            "En producción quítala para usar https://api.telegram.org.")
    host = env.get("MONITOREO_HOST", "127.0.0.1")
    if not _is_loopback(host) and not env.get("MONITOREO_PROXY_CONFIABLE"):
        bad(f"MONITOREO_HOST={host} expone el panel en la red sin HTTPS. Publica detrás de un proxy inverso "
            "con HTTPS (ver docs/PRODUCCION.md) y define MONITOREO_PROXY_CONFIABLE con la IP del proxy, "
            "o deja MONITOREO_HOST=127.0.0.1.")
    try:
        ttl = float(env.get("MONITOREO_TOKEN_TTL_HORAS", "12"))
        if ttl > 24:
            r.avisos.append(f"Sesiones de {ttl:.0f} h: se recomienda 12 h o menos.")
    except ValueError:
        r.errores.append("MONITOREO_TOKEN_TTL_HORAS no es un número.")
    if env.get("MONITOREO_RESPALDO_PG", "").lower() == "omitir":
        r.avisos.append("MONITOREO_RESPALDO_PG=omitir: las migraciones de PostgreSQL se harán SIN respaldo.")
    db_url = env.get("MONITOREO_DB_URL", "")
    if prod and (not db_url or db_url.startswith("sqlite")):
        r.avisos.append("Producción con SQLite: válido para una sola máquina. Para varias instancias en "
                        "máquinas distintas usa PostgreSQL.")
    if env.get("MONITOREO_DETECTAR_ANIDADAS") == "1" and prod:
        r.avisos.append("MONITOREO_DETECTAR_ANIDADAS=1 es una herramienta de pruebas.")
    return r


def enforce(env: dict | None = None) -> Revision:
    """Lanza ConfigError si hay errores (solo pueden existir en producción)."""
    r = review(env)
    if r.errores:
        raise ConfigError(r.errores)
    return r
