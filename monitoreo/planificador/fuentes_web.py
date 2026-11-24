"""Observador universal · fuentes web configurables por URL (sin código por proveedor).

Una fuente web es una fila de `observadores_config` con `url` y `perfil` (migración 0018). El supervisor de
observadores la arranca, la mantiene viva (arrendamiento entre procesos, relanzamiento, límites) y la restaura tras
reiniciar, exactamente como al observador del calendario del laboratorio. El navegador lo pone
`scripts/observador_web.js`, que habla el MISMO protocolo de líneas (INICIAL / CELDA / VISTA / LATIDO): lo que emite
pasa por `observaciones.registrar` (deduplicación por sucursal|trámite|fecha) y `coincidencias` como siempre.

Perfil (configuración, no código; lo escribe y valida una persona con `probar()` antes de activarlo):

  zona            selector CSS de la parte de la página que se observa (obligatorio)
  elemento        selector de cada turno/día/celda dentro de la zona (obligatorio)
  fecha           regla de extracción + "formato": ISO | DD/MM/AAAA | DD-MM-AAAA | AAAA/MM/DD   (obligatoria)
  estado          regla + listas "disponible" y "no_disponible" (textos exactos, sin mayúsculas/acentos de diferencia).
                  Sin regla de estado: un elemento presente = DISPONIBLE. Un texto que no está en ninguna lista NO se
                  interpreta: el elemento se ignora y se cuenta como «no reconocido».
  libres, hora, ubicacion   reglas opcionales
  ubicaciones     tabla EXPLÍCITA texto de ubicación -> código de sucursal. Lo que no esté en la tabla se ignora (no se
                  adivinan equivalencias). Sin regla de ubicación, todo va a la sucursal fija de la fuente.
  ignorar         selectores de ruido (relojes, anuncios, animaciones): sus cambios no cuentan y se tapan en la captura
  estabilidad_ms  un cambio tiene que mantenerse este tiempo antes de registrarse (filtra parpadeos y animaciones)
  sesion_perdida  selector cuya presencia indica que la sesión de consulta caducó (se reabre la página)

  Regla de extracción: {"selector": opcional (si falta, el propio elemento), "atributo": opcional (si falta, el texto),
                        "patron": expresión regular opcional (se toma el primer grupo, o todo lo que coincide)}

Controles de acceso: si la página muestra un CAPTCHA, pide iniciar sesión o responde 401/403/429, el observador se
DETIENE con ese motivo (no se reintenta ni se intenta sortear). Las navegaciones que no son GET (envío de formularios)
se bloquean siempre: observar no envía nada.

Sitios: 127.0.0.1/localhost (laboratorio) se admiten siempre fuera de producción. Cualquier otro host exige a la vez:
https, que el host esté en MONITOREO_OBSERVADOR_HOSTS (lo escribe quien instala, no la fuente) y una autorización
escrita vigente en la fuente (ref, huella SHA-256 del documento y vigente_hasta).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select

from .. import navegador

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_WEB = ROOT / "scripts" / "observador_web.js"
VARIABLE_HOSTS = "MONITOREO_OBSERVADOR_HOSTS"
PREFIJO = "web|"
FORMATOS = ("ISO", "DD/MM/AAAA", "DD-MM-AAAA", "AAAA/MM/DD")
_NOMBRE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")
_SUCURSAL = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,31}$")
_TRAMITE = re.compile(r"^[a-z0-9_]{1,32}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HOST = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
LOCALES = ("127.0.0.1", "localhost")


class FuenteInvalida(ValueError):
    def __init__(self, campo: str, mensaje: str):
        super().__init__(f"{campo}: {mensaje}")
        self.campo, self.mensaje = campo, mensaje


def clave(nombre: str) -> str:
    return PREFIJO + nombre


def es_web(c) -> bool:
    return bool(getattr(c, "url", None))


# ---------------------------------------------------------------- validación
def _texto(v, campo: str, n: int = 200, obligatorio: bool = True) -> str | None:
    if v is None or v == "":
        if obligatorio:
            raise FuenteInvalida(campo, "es obligatorio")
        return None
    if not isinstance(v, str) or len(v) > n or any(ord(ch) < 32 for ch in v):
        raise FuenteInvalida(campo, f"debe ser un texto de hasta {n} caracteres")
    return v


def _regla(r, campo: str, obligatoria: bool = False) -> dict | None:
    if r is None:
        if obligatoria:
            raise FuenteInvalida(campo, "falta la regla de extracción")
        return None
    if not isinstance(r, dict) or set(r) - {"selector", "atributo", "patron", "formato", "disponible", "no_disponible"}:
        raise FuenteInvalida(campo, "regla con claves desconocidas")
    out = {"selector": _texto(r.get("selector"), f"{campo}.selector", obligatorio=False),
           "atributo": _texto(r.get("atributo"), f"{campo}.atributo", 60, obligatorio=False),
           "patron": _texto(r.get("patron"), f"{campo}.patron", 200, obligatorio=False)}
    if out["atributo"] and not re.fullmatch(r"[A-Za-z_:][A-Za-z0-9_:.-]*", out["atributo"]):
        raise FuenteInvalida(f"{campo}.atributo", "no es un nombre de atributo")
    if out["patron"]:
        try:
            re.compile(out["patron"])
        except re.error as e:
            raise FuenteInvalida(f"{campo}.patron", f"expresión regular inválida ({e})") from e
        if re.search(r"\(\?[<P=!]", out["patron"]):
            raise FuenteInvalida(f"{campo}.patron", "use solo grupos simples (la misma expresión corre en el navegador)")
    return {k: v for k, v in out.items() if v is not None}


def _lista(v, campo: str) -> list[str]:
    if v is None:
        return []
    if not isinstance(v, list) or len(v) > 30:
        raise FuenteInvalida(campo, "debe ser una lista (máximo 30)")
    return [_texto(x, campo, 80) for x in v]


def validar_perfil(p, sucursal_fija: str | None) -> dict:
    if not isinstance(p, dict):
        raise FuenteInvalida("perfil", "debe ser un objeto")
    desconocidas = set(p) - {"zona", "elemento", "fecha", "estado", "libres", "hora", "ubicacion", "ubicaciones",
                             "ignorar", "estabilidad_ms", "sesion_perdida", "nota"}
    if desconocidas:
        raise FuenteInvalida("perfil", f"claves desconocidas: {', '.join(sorted(desconocidas))}")
    out = {"zona": _texto(p.get("zona"), "perfil.zona"), "elemento": _texto(p.get("elemento"), "perfil.elemento")}
    out["fecha"] = _regla(p.get("fecha"), "perfil.fecha", obligatoria=True)
    fmt = (p.get("fecha") or {}).get("formato", "ISO")
    if fmt not in FORMATOS:
        raise FuenteInvalida("perfil.fecha.formato", f"use uno de {', '.join(FORMATOS)}")
    out["fecha"]["formato"] = fmt
    if p.get("estado") is not None:
        e = _regla(p["estado"], "perfil.estado")
        e["disponible"] = _lista(p["estado"].get("disponible"), "perfil.estado.disponible")
        e["no_disponible"] = _lista(p["estado"].get("no_disponible"), "perfil.estado.no_disponible")
        if not e["disponible"]:
            raise FuenteInvalida("perfil.estado.disponible", "indique qué textos significan «disponible»")
        out["estado"] = e
    for k in ("libres", "hora", "ubicacion"):
        if p.get(k) is not None:
            out[k] = _regla(p[k], f"perfil.{k}")
    ub = p.get("ubicaciones")
    if out.get("ubicacion"):
        if not isinstance(ub, dict) or not ub or len(ub) > 100:
            raise FuenteInvalida("perfil.ubicaciones", "con regla de ubicación hace falta la tabla texto -> sucursal")
        for texto, suc in ub.items():
            _texto(texto, "perfil.ubicaciones", 120)
            if not isinstance(suc, str) or not _SUCURSAL.match(suc):
                raise FuenteInvalida("perfil.ubicaciones", f"«{suc}» no es un código de sucursal")
        out["ubicaciones"] = dict(ub)
    elif ub:
        raise FuenteInvalida("perfil.ubicaciones", "hay tabla de ubicaciones pero no regla de ubicación")
    elif not sucursal_fija:
        raise FuenteInvalida("sucursal", "sin regla de ubicación, la fuente necesita una sucursal fija")
    out["ignorar"] = _lista(p.get("ignorar"), "perfil.ignorar")
    est = p.get("estabilidad_ms", 800)
    if not isinstance(est, int) or not 0 <= est <= 60_000:
        raise FuenteInvalida("perfil.estabilidad_ms", "entre 0 y 60000")
    out["estabilidad_ms"] = est
    if p.get("sesion_perdida"):
        out["sesion_perdida"] = _texto(p["sesion_perdida"], "perfil.sesion_perdida")
    return out


def _hosts_permitidos(env=None) -> set[str]:
    raw = (env if env is not None else os.environ).get(VARIABLE_HOSTS, "")
    return {h.strip().lower() for h in raw.split(",") if _HOST.match(h.strip().lower())}


def validar_url(url, autorizacion, hoy: date | None = None, produccion: bool | None = None, env=None) -> dict | None:
    _texto(url, "url", 300)
    u = urlsplit(url)
    host = (u.hostname or "").lower()
    if u.scheme not in ("http", "https") or not host or u.username or u.password:
        raise FuenteInvalida("url", "debe ser http(s)://host/ruta, sin usuario ni contraseña")
    if produccion is None:
        from .. import entorno
        produccion = entorno.is_production()
    if host in LOCALES:
        if produccion:
            raise FuenteInvalida("url", "una fuente local (laboratorio) no se usa en producción")
        return None
    if u.scheme != "https":
        raise FuenteInvalida("url", "un sitio que no es local debe usar https")
    if host not in _hosts_permitidos(env):
        raise FuenteInvalida("url", f"el host {host} no está en {VARIABLE_HOSTS} (lo escribe quien instala Cauce)")
    a = autorizacion
    if not isinstance(a, dict):
        raise FuenteInvalida("autorizacion", "observar un sitio que no es local exige una autorización escrita")
    ref = _texto(a.get("ref"), "autorizacion.ref", 64)
    if not _HEX64.match(str(a.get("documento_sha256", ""))):
        raise FuenteInvalida("autorizacion.documento_sha256", "huella SHA-256 del documento de autorización")
    try:
        hasta = date.fromisoformat(str(a.get("vigente_hasta")))
    except ValueError as e:
        raise FuenteInvalida("autorizacion.vigente_hasta", "fecha AAAA-MM-DD") from e
    if hasta < (hoy or date.today()):
        raise FuenteInvalida("autorizacion.vigente_hasta", "la autorización está vencida")
    return {"ref": ref, "documento_sha256": a["documento_sha256"], "vigente_hasta": hasta.isoformat(), "host": host}


def validar(d: dict, hoy: date | None = None, produccion: bool | None = None, env=None) -> dict:
    nombre = d.get("nombre")
    if not isinstance(nombre, str) or not _NOMBRE.match(nombre):
        raise FuenteInvalida("nombre", "minúsculas, números, - y _ (2 a 41)")
    tramite = d.get("tramite")
    if not isinstance(tramite, str) or not _TRAMITE.match(tramite):
        raise FuenteInvalida("tramite", "código de trámite de Cauce (p. ej. alta)")
    suc = d.get("sucursal") or None
    if suc is not None and not (isinstance(suc, str) and _SUCURSAL.match(suc)):
        raise FuenteInvalida("sucursal", "código de sucursal")
    estrategia = d.get("estrategia", "dom")
    if estrategia not in ("dom", "visual", "ambas"):
        raise FuenteInvalida("estrategia", "dom, visual o ambas")
    iv = d.get("intervalo_visual_ms", 1000)
    if not isinstance(iv, int) or not 250 <= iv <= 60_000:
        raise FuenteInvalida("intervalo_visual_ms", "entre 250 y 60000")
    perfil = validar_perfil(d.get("perfil"), suc)
    aut = validar_url(d.get("url"), d.get("autorizacion"), hoy, produccion, env)
    return {"nombre": nombre, "url": d["url"], "tramite": tramite, "sucursal": suc, "estrategia": estrategia,
            "intervalo_visual_ms": iv, "perfil": perfil, "autorizacion": aut}


def sucursales_de(c) -> set[str]:
    """Sucursales que esta fuente puede reportar (la python vuelve a comprobar lo que emite el navegador)."""
    p = c.perfil or {}
    out = set((p.get("ubicaciones") or {}).values())
    if c.sucursal and c.sucursal != "*":
        out.add(c.sucursal)
    return out


# ---------------------------------------------------------------- persistencia
def configurar(db, d: dict, usuario: str, now: datetime) -> dict:
    from ..models import ObservadorConfig
    from .observador import ACTIVO
    v = validar(d)
    k = clave(v["nombre"])
    with db.session() as s:
        c = s.get(ObservadorConfig, k)
        if c is not None and c.activo:
            return {**vista(c), "ya_existia": True}
        if c is None:
            c = ObservadorConfig(clave=k, created_at=now, creado_por=usuario[:64])
            s.add(c)
        c.sucursal, c.tramite, c.mes = v["sucursal"] or "*", v["tramite"], None
        c.estrategia, c.intervalo_visual_ms = v["estrategia"], v["intervalo_visual_ms"]
        c.url, c.perfil, c.autorizacion = v["url"], v["perfil"], v["autorizacion"]
        c.activo, c.estado, c.motivo, c.reinicios, c.updated_at = True, ACTIVO, "", 0, now
        s.flush()
        return {**vista(c), "ya_existia": False}


def vista(c) -> dict:
    from .observador import vista_config
    return {**vista_config(c), "nombre": c.clave[len(PREFIJO):], "url": c.url, "perfil": c.perfil,
            "autorizacion": c.autorizacion}


def listar(db) -> list[dict]:
    from ..models import ObservadorConfig
    with db.session() as s:
        return [vista(c) for c in s.scalars(select(ObservadorConfig).where(ObservadorConfig.clave.like(PREFIJO + "%"))
                                             .order_by(ObservadorConfig.clave))]


# ---------------------------------------------------------------- vista previa (una sola lectura, sin guardar nada)
def probar(d: dict, timeout_s: float = 40.0) -> dict:
    """Abre la página UNA vez, aplica el perfil y devuelve lo que reconocería (días por sucursal, no reconocidos,
    controles detectados). No registra nada ni lanza coincidencias: sirve para que una persona valide el perfil."""
    v = validar(d)
    if shutil.which("node") is None:
        raise RuntimeError("Node.js no está disponible: el observador necesita Node + Playwright")
    cfg = {"url": v["url"], "perfil": v["perfil"], "sucursal": v["sucursal"], "estrategia": "dom", "una_vez": True}
    fd, ruta = tempfile.mkstemp(prefix="fuente-web-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    try:
        p = subprocess.run(["node", str(SCRIPT_WEB), ruta], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout_s, env=navegador.entorno())
    finally:
        os.unlink(ruta)
    lineas = [json.loads(x[4:]) for x in p.stdout.splitlines() if x.startswith("OBS ")]
    ini = next((x for x in lineas if x.get("tipo") == "INICIAL"), None)
    vistas = [x for x in lineas if x.get("tipo") == "VISTA"]
    errores = [x.get("mensaje") for x in lineas if x.get("tipo") == "ERROR"]
    return {"fuente": v["nombre"], "url": v["url"], "dias": (ini or {}).get("celdas", []),
            "elementos": (ini or {}).get("elementos"), "no_reconocidos": (ini or {}).get("no_reconocidos", []),
            "vista": vistas[-1] if vistas else None, "errores": errores[:3]}
