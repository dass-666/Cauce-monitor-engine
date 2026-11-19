"""Texto seguro para registros y diagnósticos (cierre v2.1).

`sanitizar()` deja un mensaje de error (de Node, de Playwright o de una respuesta HTTP) apto para
escribirse en un registro o en un informe:

- una sola línea, sin caracteres de control (evita que un mensaje falsee líneas del registro);
- sin cadenas de consulta en las URL (`?token=…` podría llevar un secreto);
- sin pares `clave=valor` de secretos (token, password, contraseña, clave, secret, authorization,
  bearer, cookie);
- sin correos ni teléfonos, sin CURP/RFC y sin secuencias largas de dígitos;
- con longitud máxima.

No intenta ser un filtro perfecto de datos personales: es una red de seguridad. La regla principal
sigue siendo no meter datos personales en los mensajes desde el origen.
"""
from __future__ import annotations

import re

_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_ESPACIOS = re.compile(r"\s{2,}")
_MARCO = re.compile(r"[╔╗╚╝║═─│┌┐└┘]+")
_URL_CONSULTA = re.compile(r"(https?://[^\s?#\"'<>]+)[?#][^\s\"'<>]*", re.I)
_SECRETO = re.compile(r"(?i)\b(token|access_token|password|passwd|contrase(?:ñ|n)a|clave|secret|secreto|api[_-]?key|"
                      r"authorization|cookie)\b(\s*[=:]\s*|\s+)(\"[^\"]*\"|'[^']*'|[^\s,;&]+)")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_CORREO = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_CURP = re.compile(r"\b[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d\b")
_RFC = re.compile(r"\b[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}\b")
_TELEFONO = re.compile(r"(?<![\w-])\+?\d(?: ?\d){9,}(?![\w-])")
_DIGITOS = re.compile(r"(?<![\w-])\d{8,}(?![\w-])")


def sanitizar(texto, maximo: int = 300) -> str:
    """Devuelve `texto` en una sola línea, sin secretos ni datos personales evidentes."""
    if texto is None:
        return ""
    t = str(texto)
    t = _MARCO.sub(" ", t)
    t = _CONTROL.sub(" ", t)
    t = _URL_CONSULTA.sub(r"\1?…", t)
    t = _BEARER.sub("Bearer ***", t)
    t = _SECRETO.sub(lambda m: f"{m.group(1)}{m.group(2)}***", t)
    t = _CORREO.sub("***@***", t)
    t = _CURP.sub("***CURP***", t)
    t = _RFC.sub("***RFC***", t)
    t = _TELEFONO.sub("***", t)
    t = _DIGITOS.sub("***", t)
    t = _ESPACIOS.sub(" ", t).strip()
    if len(t) > maximo:
        t = t[: maximo - 1].rstrip() + "…"
    return t


class RespuestaInesperada(RuntimeError):
    """Una respuesta HTTP que no trae lo que el script esperaba (código o clave)."""

    def __init__(self, diagnostico: str, codigo: int | None = None):
        super().__init__(diagnostico)
        self.codigo = codigo


def cuerpo_esperado(r, clave: str | None = None):
    """Devuelve el JSON de la respuesta `r` (httpx), o `r.json()[clave]` si se indica `clave`.

    Si el código HTTP es de error, si el cuerpo no es un objeto JSON o si falta la clave, lanza
    `RespuestaInesperada` con un diagnóstico seguro: método, ruta (sin consulta), código HTTP, la
    clave esperada, las claves que sí trae y el `detail` del motor saneado. Nunca copia el cuerpo
    completo (podría traer datos de expedientes)."""
    try:
        cuerpo = r.json()
    except ValueError:
        cuerpo = None
    falta = clave is not None and (not isinstance(cuerpo, dict) or clave not in cuerpo)
    if r.status_code < 400 and not falta and (clave is not None or cuerpo is not None):
        return cuerpo[clave] if clave is not None else cuerpo
    try:
        metodo, ruta = r.request.method, r.request.url.path
    except Exception:  # noqa: BLE001 - respuesta construida a mano en pruebas
        metodo, ruta = "?", "?"
    partes = [f"{metodo} {ruta} respondió HTTP {r.status_code}"]
    if clave is not None:
        partes.append(f"se esperaba la clave «{clave}»")
    if isinstance(cuerpo, dict):
        partes.append("claves recibidas: " + (", ".join(sorted(map(str, cuerpo))[:12]) or "ninguna"))
        if "detail" in cuerpo:
            partes.append("detalle: " + sanitizar(cuerpo["detail"], 160))
    elif cuerpo is None:
        tipo = (r.headers.get("content-type") or "desconocido").split(";")[0]
        partes.append(f"el cuerpo no es JSON (tipo {tipo}, {len(r.content)} bytes)")
    else:
        partes.append(f"el cuerpo es {type(cuerpo).__name__}, no un objeto")
    raise RespuestaInesperada("; ".join(partes), r.status_code)
