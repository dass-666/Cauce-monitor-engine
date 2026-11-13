"""Contratos de campos (v1.5).

Un contrato describe QUÉ hay que llenar en cada pantalla (nombre de campo, tipo, de qué dato del
expediente sale, si es obligatorio) y CÓMO se reconoce el éxito o el error. No dice nada de cómo
se navega: el mismo contrato lo usa el navegador HTTP del motor (`navegador.py`) y el recorrido
con Playwright de las pruebas. Cambiar de portal = escribir otro JSON, no otro código.

Contratos disponibles: archivos `contratos/*.json`. Solo existe el del portal de PRUEBA propio.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

DIR = Path(__file__).parent / "contratos"
TIPOS = {"texto", "fecha", "opcion"}


class ContratoInvalido(Exception):
    pass


@dataclass(frozen=True)
class Campo:
    nombre: str
    etiqueta: str
    tipo: str
    origen: str
    obligatorio: bool = True
    personal: bool = False
    confirmacion_de: str | None = None


@dataclass(frozen=True)
class Contrato:
    nombre: str
    version: int
    datos: dict

    def pantalla(self, p: str) -> dict:
        return self.datos["pantallas"][p]

    @property
    def campos(self) -> list[Campo]:
        return [Campo(**c) for c in self.pantalla("datos")["campos"]]

    @property
    def marcadores(self) -> dict:
        return self.datos["marcadores"]

    @property
    def verificacion(self) -> dict:
        return self.datos["verificacion"]


def validar_contrato(d: dict) -> None:
    try:
        for k in ("nombre", "version", "pantallas", "marcadores", "verificacion"):
            if k not in d:
                raise ContratoInvalido(f"falta '{k}'")
        for p in ("datos", "disponibilidad", "confirmacion", "resultado"):
            if p not in d["pantallas"]:
                raise ContratoInvalido(f"falta la pantalla '{p}'")
        vistos = set()
        for c in d["pantallas"]["datos"]["campos"]:
            if c["tipo"] not in TIPOS:
                raise ContratoInvalido(f"tipo desconocido en {c['nombre']}: {c['tipo']}")
            if c["nombre"] in vistos:
                raise ContratoInvalido(f"campo repetido: {c['nombre']}")
            vistos.add(c["nombre"])
    except (KeyError, TypeError) as e:
        raise ContratoInvalido(f"estructura inválida: {e}") from e


@lru_cache(maxsize=8)
def cargar(nombre: str = "portal_laboratorio") -> Contrato:
    if not nombre.replace("_", "").isalnum():
        raise ContratoInvalido("nombre de contrato no válido")
    ruta = DIR / f"{nombre}.json"
    if not ruta.exists():
        raise ContratoInvalido(f"no existe el contrato {nombre}")
    d = json.loads(ruta.read_text(encoding="utf-8"))
    validar_contrato(d)
    return Contrato(d["nombre"], int(d["version"]), d)


def valores_desde(contrato: Contrato, exp) -> dict[str, str | None]:
    """Valor de cada campo del contrato a partir del expediente (sin tocar el portal).
    Las confirmaciones se derivan del dato original: no existen copias guardadas."""
    out: dict[str, str | None] = {}
    for c in contrato.campos:
        v = getattr(exp, c.origen, None)
        if isinstance(v, date):
            v = v.isoformat()
        out[c.nombre] = None if v in (None, "") else str(v)
    return out


def preparar(contrato: Contrato, exp) -> dict:
    """Prepara el formulario para REVISIÓN HUMANA: qué se va a escribir y qué falta.
    Los datos personales se muestran enmascarados; el operador asignado puede verlos completos
    desde la ficha del expediente."""
    from .. import validacion as val
    valores = valores_desde(contrato, exp)
    campos, faltan = [], []
    for c in contrato.campos:
        v = valores[c.nombre]
        if v is None and c.obligatorio:
            faltan.append(c.nombre)
        mostrado = v
        if v is not None and c.personal:
            mostrado = val.enmascarar_telefono(v) if c.origen == "telefono" else val.enmascarar_correo(v)
        campos.append({"campo": c.nombre, "etiqueta": c.etiqueta, "tipo": c.tipo, "origen": c.origen,
                       "obligatorio": c.obligatorio, "valor": mostrado, "listo": v is not None or not c.obligatorio,
                       "confirmacion_de": c.confirmacion_de})
    return {"contrato": contrato.nombre, "version": contrato.version, "campos": campos, "faltan": faltan,
            "listo": not faltan}
