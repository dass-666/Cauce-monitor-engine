"""Saneado de todo lo que sale hacia Jarvis (v2.2.1).

Dos capas:
1. `sanear_texto` / `sanear` reemplazan lo que no debe salir del equipo: la ruta de la instalación,
   la carpeta del usuario, correos, credenciales en URLs, tokens de Telegram, CURP y números largos
   (teléfonos, cuentas).
2. `buscar_sensibles` revisa el resultado final; si algo quedó, el exportador se NIEGA a escribir.

Límite: es un filtro por patrones. Reduce el riesgo; no garantiza que un texto libre no contenga un
dato personal escrito de forma inesperada. Por eso los informes solo llevan resúmenes, no salidas
completas, y las salidas completas se quedan en el equipo (resultados/jarvis/).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_CORREO = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_URL_CRED = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s:@]+:[^/\s@]*@")
_TOKEN_BOT = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b")
_CURP = re.compile(r"\b[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d\b")
_DIGITOS = re.compile(r"(?<![0-9A-Za-z])\d{10,}(?![0-9A-Za-z])")
_CLAVE_VALOR = re.compile(r"(?i)\b(password|passwd|contrase(?:ñ|n)a|secret|secreto|token|api[_-]?key)"
                          r"(\s*[=:]\s*)([^\s,;\"']{4,})")
_HOME_POSIX = re.compile(r"/(?:home|Users)/[^/\s\"']+")
_HOME_WIN = re.compile(r"(?i)[A-Z]:\\(?:Users|Documents and Settings)\\[^\\\s\"']+")


def _variantes(ruta: Path) -> list[str]:
    s = str(ruta)
    vs = {s, s.replace("\\", "/"), ruta.as_posix()}
    return sorted((v for v in vs if len(v) > 3), key=len, reverse=True)


def sanear_texto(texto: str, raiz: Path | None = None) -> str:
    if not texto:
        return texto
    if raiz is not None:
        for v in _variantes(raiz):
            texto = re.sub(re.escape(v), "<proyecto>", texto, flags=re.IGNORECASE)
    home = os.path.expanduser("~")
    if home and home not in ("~", "/", "/root"):
        for v in _variantes(Path(home)):
            texto = re.sub(re.escape(v), "~", texto, flags=re.IGNORECASE)
    texto = _URL_CRED.sub(r"\1<credenciales>@", texto)
    texto = _CORREO.sub("<correo>", texto)
    texto = _TOKEN_BOT.sub("<token>", texto)
    texto = _CLAVE_VALOR.sub(r"\1\2<oculto>", texto)
    texto = _CURP.sub("<curp>", texto)
    texto = _DIGITOS.sub("<numero>", texto)
    texto = _HOME_WIN.sub("~", texto)
    texto = _HOME_POSIX.sub("~", texto)
    return texto


def sanear(obj, raiz: Path | None = None):
    """Sanea recursivamente todas las cadenas de un objeto JSON (claves incluidas)."""
    if isinstance(obj, str):
        return sanear_texto(obj, raiz)
    if isinstance(obj, list):
        return [sanear(x, raiz) for x in obj]
    if isinstance(obj, dict):
        return {sanear_texto(str(k), raiz): sanear(v, raiz) for k, v in obj.items()}
    return obj


_HEX40 = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")


def buscar_sensibles(obj, ruta: str = "$", raiz: Path | None = None) -> list[str]:
    """Lista de hallazgos (ruta JSON y tipo) que NO deberían estar en un informe ya saneado."""
    hallazgos: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            hallazgos += buscar_sensibles(v, f"{ruta}.{k}", raiz)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hallazgos += buscar_sensibles(v, f"{ruta}[{i}]", raiz)
    elif isinstance(obj, str) and not _HEX40.match(obj):
        pruebas = [("correo", _CORREO), ("credencial en URL", _URL_CRED), ("token", _TOKEN_BOT),
                   ("CURP", _CURP), ("número largo", _DIGITOS), ("clave=valor", _CLAVE_VALOR),
                   ("carpeta de usuario", _HOME_POSIX), ("carpeta de usuario", _HOME_WIN)]
        for nombre, rx in pruebas:
            m = rx.search(obj)
            if m and "<oculto>" not in m.group(0):
                hallazgos.append(f"{ruta}: {nombre}")
        if raiz is not None and any(v.lower() in obj.lower() for v in _variantes(raiz)):
            hallazgos.append(f"{ruta}: ruta absoluta del proyecto")
    return hallazgos
