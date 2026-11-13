"""Contratos de campos (v1.5, ampliado en v1.6).

Un contrato describe QUÉ hay que llenar en cada pantalla (nombre del campo, tipo, de qué dato del
expediente sale, si es obligatorio) y CÓMO se reconoce el éxito o el error. No dice cómo se navega:
el mismo contrato lo usa el navegador HTTP del motor (`navegador.py`) y el recorrido con
Playwright de las pruebas. Cambiar de portal = escribir otro JSON, no otro código.

v1.6 — cada campo puede declarar:
  tipo           texto | fecha | opcion (menú desplegable) | casilla
  modo           AUTOMATICO (sale del expediente) | MANUAL_REQUIRED (lo teclea el operador; el motor
                 nunca lo escribe solo ni lo guarda) | SOLO_LECTURA (el portal lo muestra; se comprueba)
  obligatorio    true/false (opcional = false)
  confirmacion_de  campo de confirmación (se compara con el original; no se guarda copia)
  requerido_si   {"campo": X, "igual": V}: obligatorio solo si otro campo vale V (validación dependiente)
  validacion     {"patron": regex, "max_fecha": "hoy", "min_len": n, "max_len": n, "valor_requerido": V}
  instrucciones  texto para el operador (campos manuales)
  sin_pegar      el portal no permite pegar en ese campo (se respeta: se teclea)
  guardar        false = el valor nunca se guarda (p. ej. la clave de identidad)

Contratos disponibles: `contratos/*.json` (solo los del portal de PRUEBA propio).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path

DIR = Path(__file__).parent / "contratos"
TIPOS = {"texto", "fecha", "opcion", "casilla"}
MODOS = {"AUTOMATICO", "MANUAL_REQUIRED", "SOLO_LECTURA"}
PANTALLAS = {"POR_HORARIO": ("datos", "disponibilidad", "confirmacion", "resultado"),
             "POR_DIA": ("datos", "calendario", "resultado")}


class ContratoInvalido(Exception):
    pass


@dataclass(frozen=True)
class Campo:
    nombre: str
    etiqueta: str
    tipo: str
    origen: str | None = None
    obligatorio: bool = True
    personal: bool = False
    confirmacion_de: str | None = None
    modo: str = "AUTOMATICO"
    requerido_si: dict | None = None
    validacion: dict | None = None
    instrucciones: str | None = None
    sin_pegar: bool = False
    guardar: bool = True

    @property
    def manual(self) -> bool:
        return self.modo == "MANUAL_REQUIRED"


@dataclass(frozen=True)
class Contrato:
    nombre: str
    version: int
    datos: dict = field(hash=False, compare=False)

    @property
    def modo(self) -> str:
        return self.datos.get("modo", "POR_HORARIO")

    def pantalla(self, p: str) -> dict:
        return self.datos["pantallas"][p]

    @property
    def campos(self) -> list[Campo]:
        return [Campo(**c) for c in self.pantalla("datos")["campos"]]

    @property
    def automaticos(self) -> list[Campo]:
        return [c for c in self.campos if c.modo == "AUTOMATICO"]

    @property
    def manuales(self) -> list[Campo]:
        return [c for c in self.campos if c.manual]

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
        modo = d.get("modo", "POR_HORARIO")
        if modo not in PANTALLAS:
            raise ContratoInvalido(f"modo desconocido: {modo}")
        for p in PANTALLAS[modo]:
            if p not in d["pantallas"]:
                raise ContratoInvalido(f"falta la pantalla '{p}'")
        vistos = set()
        for c in d["pantallas"]["datos"]["campos"]:
            campo = Campo(**c)
            if campo.tipo not in TIPOS:
                raise ContratoInvalido(f"tipo desconocido en {campo.nombre}: {campo.tipo}")
            if campo.modo not in MODOS:
                raise ContratoInvalido(f"modo desconocido en {campo.nombre}: {campo.modo}")
            if campo.modo == "AUTOMATICO" and not campo.origen:
                raise ContratoInvalido(f"el campo automático {campo.nombre} necesita 'origen'")
            if campo.manual and campo.origen:
                raise ContratoInvalido(f"el campo manual {campo.nombre} no puede salir del expediente")
            if campo.nombre in vistos:
                raise ContratoInvalido(f"campo repetido: {campo.nombre}")
            if campo.validacion and campo.validacion.get("patron"):
                re.compile(campo.validacion["patron"])
            vistos.add(campo.nombre)
        for c in d["pantallas"]["datos"]["campos"]:
            r = c.get("requerido_si")
            if r and r.get("campo") not in vistos:
                raise ContratoInvalido(f"requerido_si de {c['nombre']} apunta a un campo inexistente")
            if c.get("confirmacion_de") and c["confirmacion_de"] not in vistos:
                raise ContratoInvalido(f"confirmacion_de de {c['nombre']} apunta a un campo inexistente")
    except (KeyError, TypeError, re.error) as e:
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


# ---------------------------------------------------------------- valores y reglas
def valores_desde(contrato: Contrato, exp) -> dict[str, str | None]:
    """Valor de cada campo AUTOMÁTICO a partir del expediente (sin tocar el portal). Las
    confirmaciones se derivan del dato original: no existen copias guardadas. Los campos
    manuales NO aparecen aquí: el motor nunca los escribe por su cuenta."""
    out: dict[str, str | None] = {}
    for c in contrato.automaticos:
        v = getattr(exp, c.origen, None)
        if isinstance(v, date):
            v = v.isoformat()
        out[c.nombre] = None if v in (None, "") else str(v)
    return out


def requerido(c: Campo, valores: dict) -> bool:
    if c.requerido_si:
        return str(valores.get(c.requerido_si["campo"]) or "") == str(c.requerido_si["igual"])
    return c.obligatorio


def _valida(c: Campo, v: str, hoy: date) -> str | None:
    regla = c.validacion or {}
    if regla.get("valor_requerido") is not None and v != str(regla["valor_requerido"]):
        return "Este campo debe marcarse para continuar"
    if regla.get("patron") and not re.fullmatch(regla["patron"], v):
        return regla.get("mensaje") or "Formato no válido"
    if regla.get("min_len") and len(v) < int(regla["min_len"]):
        return f"Mínimo {regla['min_len']} caracteres"
    if regla.get("max_len") and len(v) > int(regla["max_len"]):
        return f"Máximo {regla['max_len']} caracteres"
    if c.tipo == "fecha":
        try:
            f = date.fromisoformat(v)
        except ValueError:
            return "Fecha no válida (AAAA-MM-DD)"
        if regla.get("max_fecha") == "hoy" and f > hoy:
            return "La fecha no puede ser futura"
    return None


def validar(contrato: Contrato, valores: dict[str, str | None], hoy: date | None = None) -> dict[str, str]:
    """Reglas del contrato sobre el conjunto completo de valores (automáticos + manuales):
    obligatorios, dependientes (requerido_si), confirmaciones y formato. Devuelve {campo: error}."""
    hoy = hoy or date.today()
    errores: dict[str, str] = {}
    for c in contrato.campos:
        if c.modo == "SOLO_LECTURA":
            continue
        v = valores.get(c.nombre)
        v = None if v is None or str(v).strip() == "" else str(v).strip()
        if v is None:
            if requerido(c, valores):
                errores[c.nombre] = ("Captura manual pendiente" if c.manual else "Falta el dato en el expediente") \
                    if not c.requerido_si else f"Obligatorio cuando {c.requerido_si['campo']} es {c.requerido_si['igual']}"
            continue
        e = _valida(c, v, hoy)
        if e:
            errores[c.nombre] = e
        elif c.confirmacion_de and v != str(valores.get(c.confirmacion_de) or "").strip():
            errores[c.nombre] = "No coincide con el dato original"
    return errores


def validar_manuales(contrato: Contrato, manuales: dict | None, valores_auto: dict, hoy: date | None = None
                     ) -> tuple[dict[str, str], dict[str, str]]:
    """Valida lo que tecleó el operador. Devuelve (valores_limpios, errores). Rechaza nombres que
    no son campos manuales del contrato (no se puede "colar" un campo automático)."""
    manuales = dict(manuales or {})
    nombres = {c.nombre: c for c in contrato.manuales}
    errores = {k: "No es un campo de captura manual" for k in manuales if k not in nombres}
    limpios = {}
    for k, c in nombres.items():
        v = manuales.get(k)
        if v is None or str(v).strip() == "":
            continue
        v = str(v).strip()
        if c.validacion and c.validacion.get("mayusculas"):
            v = v.upper()
        limpios[k] = v
    errs = validar(contrato, {**valores_auto, **limpios}, hoy)
    errores.update({k: v for k, v in errs.items() if k in nombres})
    return limpios, errores


def faltantes_expediente(contrato: Contrato, exp) -> list[str]:
    """Datos del EXPEDIENTE que faltan para poder capturar (no cuenta los campos manuales)."""
    valores = valores_desde(contrato, exp)
    faltan = []
    for c in contrato.automaticos:
        if valores.get(c.nombre) is None and requerido(c, valores) and c.origen not in faltan:
            faltan.append(c.origen)
    return faltan


def preparar(contrato: Contrato, exp) -> dict:
    """Prepara el formulario para REVISIÓN HUMANA: qué se va a escribir, qué falta y qué debe
    teclear el operador. Los datos personales se muestran enmascarados."""
    from .. import validacion as val
    valores = valores_desde(contrato, exp)
    campos, faltan = [], []
    for c in contrato.campos:
        if c.modo == "SOLO_LECTURA":
            continue
        req = requerido(c, valores)
        if c.manual:
            campos.append({"campo": c.nombre, "etiqueta": c.etiqueta, "tipo": c.tipo, "modo": c.modo, "origen": None,
                           "obligatorio": req, "valor": None, "listo": False, "manual": True,
                           "instrucciones": c.instrucciones, "sin_pegar": c.sin_pegar, "validacion": c.validacion or {},
                           "confirmacion_de": None})
            continue
        v = valores[c.nombre]
        if v is None and req:
            faltan.append(c.nombre)
        mostrado = v
        if v is not None and c.personal:
            mostrado = val.enmascarar_telefono(v) if c.origen == "telefono" else val.enmascarar_correo(v)
        campos.append({"campo": c.nombre, "etiqueta": c.etiqueta, "tipo": c.tipo, "modo": c.modo, "origen": c.origen,
                       "obligatorio": req, "valor": mostrado, "listo": v is not None or not req, "manual": False,
                       "confirmacion_de": c.confirmacion_de, "requerido_si": c.requerido_si})
    errores = {k: v for k, v in validar(contrato, valores).items() if k not in {m.nombre for m in contrato.manuales}}
    return {"contrato": contrato.nombre, "version": contrato.version, "modo": contrato.modo, "campos": campos,
            "faltan": faltan, "errores": errores, "manuales": [m.nombre for m in contrato.manuales],
            "listo": not faltan and not errores}
