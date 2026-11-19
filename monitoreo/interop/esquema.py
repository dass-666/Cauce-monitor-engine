"""Validador mínimo de JSON Schema (v2.2.1), sin dependencias.

Cubre exactamente las palabras clave que usa jarvis/report.schema.json: type, properties, required,
additionalProperties (booleano), items, enum, const, pattern, minimum, maximum, minLength, maxLength,
minItems, maxItems, $ref locales (#/$defs/...), allOf, if/then. Cualquier otra palabra clave hace FALLAR la
validación del esquema (para no aceptar en silencio algo que no se comprobó).

Jarvis puede validar el mismo informe con `jsonschema` (Draft 2020-12): una prueba compara ambos
validadores cuando `jsonschema` está instalado.
"""
from __future__ import annotations

import re

_SOPORTADAS = {"$schema", "$id", "$defs", "$comment", "title", "description", "type", "properties",
               "required", "additionalProperties", "items", "enum", "const", "pattern", "minimum",
               "maximum", "minLength", "maxLength", "minItems", "maxItems", "$ref", "allOf", "if", "then",
               "examples", "default"}

_TIPOS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


class EsquemaNoSoportado(ValueError):
    pass


def validar(instancia, esquema: dict) -> list[str]:
    """Devuelve la lista de errores (vacía = válido)."""
    errores: list[str] = []
    _validar(instancia, esquema, esquema, "$", errores)
    return errores


def _resolver(ref: str, raiz: dict) -> dict:
    if not ref.startswith("#/"):
        raise EsquemaNoSoportado(f"$ref no local: {ref}")
    nodo = raiz
    for parte in ref[2:].split("/"):
        nodo = nodo[parte]
    return nodo


def _validar(v, esq, raiz, ruta, errores):
    if esq is True or esq == {}:
        return
    if esq is False:
        errores.append(f"{ruta}: no se permite ningún valor")
        return
    desconocidas = set(esq) - _SOPORTADAS
    if desconocidas:
        raise EsquemaNoSoportado(f"palabras clave no soportadas en {ruta}: {sorted(desconocidas)}")
    if "$ref" in esq:
        _validar(v, _resolver(esq["$ref"], raiz), raiz, ruta, errores)
    if "type" in esq:
        tipos = esq["type"] if isinstance(esq["type"], list) else [esq["type"]]
        if not any(_TIPOS[t](v) for t in tipos):
            errores.append(f"{ruta}: se esperaba {'/'.join(tipos)}")
            return
    if "const" in esq and v != esq["const"]:
        errores.append(f"{ruta}: debe ser {esq['const']!r}")
    if "enum" in esq and v not in esq["enum"]:
        errores.append(f"{ruta}: {v!r} no está en {esq['enum']}")
    if isinstance(v, str):
        if "pattern" in esq and not re.search(esq["pattern"], v):
            errores.append(f"{ruta}: no cumple el patrón {esq['pattern']}")
        if "minLength" in esq and len(v) < esq["minLength"]:
            errores.append(f"{ruta}: más corto que {esq['minLength']}")
        if "maxLength" in esq and len(v) > esq["maxLength"]:
            errores.append(f"{ruta}: más largo que {esq['maxLength']}")
    if _TIPOS["number"](v):
        if "minimum" in esq and v < esq["minimum"]:
            errores.append(f"{ruta}: menor que {esq['minimum']}")
        if "maximum" in esq and v > esq["maximum"]:
            errores.append(f"{ruta}: mayor que {esq['maximum']}")
    if isinstance(v, dict):
        for r in esq.get("required", []):
            if r not in v:
                errores.append(f"{ruta}: falta '{r}'")
        props = esq.get("properties", {})
        for k, sub in v.items():
            if k in props:
                _validar(sub, props[k], raiz, f"{ruta}.{k}", errores)
            elif esq.get("additionalProperties", True) is False:
                errores.append(f"{ruta}: propiedad no permitida '{k}'")
            elif isinstance(esq.get("additionalProperties"), dict):
                _validar(sub, esq["additionalProperties"], raiz, f"{ruta}.{k}", errores)
    if isinstance(v, list):
        if "minItems" in esq and len(v) < esq["minItems"]:
            errores.append(f"{ruta}: menos de {esq['minItems']} elementos")
        if "maxItems" in esq and len(v) > esq["maxItems"]:
            errores.append(f"{ruta}: más de {esq['maxItems']} elementos")
        if "items" in esq:
            for i, x in enumerate(v):
                _validar(x, esq["items"], raiz, f"{ruta}[{i}]", errores)
    for sub in esq.get("allOf", []):
        _validar(v, sub, raiz, ruta, errores)
    if "if" in esq:
        prueba: list[str] = []
        _validar(v, esq["if"], raiz, ruta, prueba)
        if not prueba and "then" in esq:
            _validar(v, esq["then"], raiz, ruta, errores)
