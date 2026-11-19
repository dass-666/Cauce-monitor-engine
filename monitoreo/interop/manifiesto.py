"""Manifiesto de suites permitidas: jarvis/test-manifest.json (v2.2.1).

El manifiesto ES la lista blanca. Quien llama (Jarvis, Autonomous Worker o una persona) solo elige
el IDENTIFICADOR de una suite; el comando sale de aquí y nunca de la solicitud.

Además, cada comando del manifiesto se revisa con reglas fijas, por si alguien edita el archivo:
- `{python} -m pytest` y nada más (no hay shell, ni otros programas);
- opciones de pytest de una lista corta (-q, -x, -rs, …); nada de -p, -k, -o, --rootdir, plugins;
- rutas relativas dentro de tests/, que existan, sin `..` y con caracteres seguros;
- límites de tiempo y salida dentro de un rango.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .comun import DIR_JARVIS, PROYECTO_ID, ErrorSolicitud

FORMATO = "cauce.jarvis.test-manifest/1"
ARCHIVO = "test-manifest.json"

OPCIONES_PERMITIDAS = {"-q", "-qq", "-x", "-rs", "-ra", "--strict-markers", "--tb=short", "--tb=line",
                       "--tb=no", "--maxfail=1", "--maxfail=5"}
_RUTA = re.compile(r"^tests(/[A-Za-z0-9_.-]+)*(::[A-Za-z0-9_]+)?$")
_ID = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
TIEMPO_MAX_S = 4 * 3600
SALIDA_MAX_BYTES = 50 * 1024 * 1024
_CLAVES_SUITE = {"id", "descripcion", "argv", "tiempo_s", "salida_bytes", "postgres", "etiquetas",
                 "duracion_referencia_s", "notas"}
_CLAVES_RAIZ = {"formato", "proyecto", "descripcion", "interprete", "limites_por_defecto", "entorno_fijo",
                "suites", "notas"}
_ENTORNO_FIJO_PERMITIDO = re.compile(r"^(MONITOREO_[A-Z_]+|PYTHON[A-Z_]*)$")
_ENTORNO_PROHIBIDO = {"MONITOREO_DB_URL", "MONITOREO_TEST_PG", "MONITOREO_PORTAL_URL", "MONITOREO_PORTAL_TOKEN",
                      "MONITOREO_TELEGRAM_TOKEN", "MONITOREO_TELEGRAM_CHATS", "MONITOREO_ADMIN_PASSWORD",
                      "MONITOREO_SECRET_KEY", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME"}


@dataclass(frozen=True)
class Suite:
    id: str
    descripcion: str
    argv: tuple[str, ...]
    tiempo_s: int
    salida_bytes: int
    postgres: str  # "no" | "opcional"
    etiquetas: tuple[str, ...]
    duracion_referencia_s: float | None


@dataclass(frozen=True)
class Manifiesto:
    proyecto: str
    entorno_fijo: dict
    suites: dict  # id -> Suite
    ruta: Path

    def suite(self, sid: str) -> Suite:
        if not isinstance(sid, str) or not _ID.match(sid) or sid not in self.suites:
            raise ErrorSolicitud(f"suite no permitida: {sid!r}. Permitidas: {', '.join(sorted(self.suites))}")
        return self.suites[sid]


def _revisar_argv(argv, raiz: Path, sid: str) -> tuple[str, ...]:
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise ErrorSolicitud(f"suite {sid}: argv debe ser una lista de cadenas")
    if argv[:3] != ["{python}", "-m", "pytest"]:
        raise ErrorSolicitud(f"suite {sid}: solo se permite {{python}} -m pytest")
    rutas = 0
    for a in argv[3:]:
        if a.startswith("-"):
            if a not in OPCIONES_PERMITIDAS:
                raise ErrorSolicitud(f"suite {sid}: opción no permitida {a!r}")
            continue
        if not _RUTA.match(a) or ".." in a.split("/"):
            raise ErrorSolicitud(f"suite {sid}: ruta no permitida {a!r} (solo dentro de tests/)")
        archivo = raiz / a.split("::")[0]
        try:
            archivo.resolve().relative_to((raiz / "tests").resolve())
        except ValueError:
            raise ErrorSolicitud(f"suite {sid}: ruta fuera de tests/ {a!r}") from None
        if not archivo.exists():
            raise ErrorSolicitud(f"suite {sid}: no existe {a!r}")
        rutas += 1
    if rutas == 0:
        raise ErrorSolicitud(f"suite {sid}: debe indicar al menos una ruta de tests/")
    return tuple(argv)


def cargar(raiz: Path) -> Manifiesto:
    ruta = raiz / DIR_JARVIS / ARCHIVO
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ErrorSolicitud(f"no se pudo leer {DIR_JARVIS}/{ARCHIVO}: {e}") from None
    return desde_datos(datos, raiz, ruta)


def desde_datos(datos: dict, raiz: Path, ruta: Path | None = None) -> Manifiesto:
    if not isinstance(datos, dict) or datos.get("formato") != FORMATO:
        raise ErrorSolicitud(f"formato de manifiesto no reconocido (se esperaba {FORMATO})")
    sobra = set(datos) - _CLAVES_RAIZ
    if sobra:
        raise ErrorSolicitud(f"claves no reconocidas en el manifiesto: {sorted(sobra)}")
    if datos.get("proyecto") != PROYECTO_ID:
        raise ErrorSolicitud(f"el manifiesto es de otro proyecto: {datos.get('proyecto')!r}")
    if datos.get("interprete") != "{python}":
        raise ErrorSolicitud("interprete debe ser {python} (el mismo Python que ejecuta el ejecutor)")
    lim = datos.get("limites_por_defecto") or {}
    entorno_fijo = datos.get("entorno_fijo") or {}
    for k, v in entorno_fijo.items():
        if not _ENTORNO_FIJO_PERMITIDO.match(k) or k in _ENTORNO_PROHIBIDO or not isinstance(v, str):
            raise ErrorSolicitud(f"variable de entorno no permitida en el manifiesto: {k}")
    suites: dict = {}
    for s in datos.get("suites") or []:
        sid = s.get("id") if isinstance(s, dict) else None
        if not isinstance(sid, str) or not _ID.match(sid):
            raise ErrorSolicitud(f"identificador de suite inválido: {sid!r}")
        if sid in suites:
            raise ErrorSolicitud(f"suite repetida: {sid}")
        sobra = set(s) - _CLAVES_SUITE
        if sobra:
            raise ErrorSolicitud(f"suite {sid}: claves no reconocidas {sorted(sobra)}")
        tiempo = s.get("tiempo_s", lim.get("tiempo_s", 900))
        salida = s.get("salida_bytes", lim.get("salida_bytes", 2_000_000))
        if not (isinstance(tiempo, int) and 1 <= tiempo <= TIEMPO_MAX_S):
            raise ErrorSolicitud(f"suite {sid}: tiempo_s fuera de rango (1..{TIEMPO_MAX_S})")
        if not (isinstance(salida, int) and 1024 <= salida <= SALIDA_MAX_BYTES):
            raise ErrorSolicitud(f"suite {sid}: salida_bytes fuera de rango (1024..{SALIDA_MAX_BYTES})")
        pg = s.get("postgres", "no")
        if pg not in ("no", "opcional"):
            raise ErrorSolicitud(f"suite {sid}: postgres debe ser 'no' u 'opcional'")
        suites[sid] = Suite(sid, str(s.get("descripcion", "")), _revisar_argv(s.get("argv"), raiz, sid),
                            tiempo, salida, pg, tuple(s.get("etiquetas") or ()), s.get("duracion_referencia_s"))
    if not suites:
        raise ErrorSolicitud("el manifiesto no define suites")
    return Manifiesto(datos["proyecto"], dict(entorno_fijo), suites, ruta or raiz / DIR_JARVIS / ARCHIVO)
