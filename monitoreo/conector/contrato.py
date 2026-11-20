"""Contrato cauce.conector/1: validación de solicitud y respuesta, huellas, caducidad y filtro de datos personales.

El contrato lo define Monitor (PROPUESTO). El contrato del Conector Universal no estaba disponible al escribirlo:
ver docs/CONECTOR_CONTRATO_v1.md. Nada de lo que declara este módulo está implementado en el conector real.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from ..interop.esquema import validar as _validar_esquema

VERSION = "cauce.conector/1"
ESQUEMAS = Path(__file__).parent / "esquemas"
ESTADOS_RESPUESTA = ("PREPARADO", "RECHAZADO", "ERROR", "CADUCADO", "INCIERTO")
# Estados que Monitor lleva por solicitud (el conector solo informa los de arriba).
ESTADOS_SOLICITUD = ("CREADA", "AUTORIZADA", "LANZADA", "PREPARADO", "RECHAZADA", "ERROR", "CADUCADA",
                     "CANCELADA", "INCIERTA")
TERMINALES = ("PREPARADO", "RECHAZADA", "ERROR", "CADUCADA", "CANCELADA")
TRANSICIONES = {
    "CREADA": ("AUTORIZADA", "CANCELADA", "CADUCADA"),
    "AUTORIZADA": ("LANZADA", "CANCELADA", "CADUCADA"),
    "LANZADA": ("PREPARADO", "RECHAZADA", "ERROR", "CADUCADA", "INCIERTA", "CANCELADA"),
    "INCIERTA": ("PREPARADO", "RECHAZADA", "ERROR", "CANCELADA"),   # solo por conciliación humana
}
# Respuesta del conector -> estado de Monitor
MAPA_ESTADO = {"PREPARADO": "PREPARADO", "RECHAZADO": "RECHAZADA", "ERROR": "ERROR", "CADUCADO": "CADUCADA",
               "INCIERTO": "INCIERTA"}
MAX_CADUCIDAD_S = 120

_RE_CAMPO = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
# Patrones de datos personales. Sirven de red de seguridad: la respuesta ya no tiene texto libre.
_PII = (
    ("correo", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("telefono", re.compile(r"(?<!\d)(?:\+?\d[\s().-]?){10,15}(?!\d)")),
    ("fecha", re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{2}/\d{2}/\d{4}\b")),
    ("curp", re.compile(r"\b[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d\b", re.I)),
)


_RE_OPACO = re.compile(r"^(?:[0-9a-f]{64}|[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$")


def cargar_esquema(nombre: str) -> dict:
    return json.loads((ESQUEMAS / f"{nombre}.schema.json").read_text(encoding="utf-8"))


def huella_campos(campos: dict) -> str:
    """SHA-256 del JSON canónico (claves ordenadas, UTF-8, sin espacios)."""
    canon = json.dumps(campos, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _ts(texto: str) -> datetime:
    return datetime.strptime(texto, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def validar_solicitud(sol, ahora: datetime | None = None) -> list[str]:
    """Errores de la solicitud (lista vacía = válida y vigente). `ahora` debe ser UTC con zona."""
    if not isinstance(sol, dict):
        return ["la solicitud no es un objeto JSON"]
    errores = _validar_esquema(sol, cargar_esquema("solicitud"))
    if errores:
        return errores
    campos = sol["campos"]
    for k, v in campos.items():
        if not _RE_CAMPO.match(k):
            errores.append(f"nombre de campo inválido: {k!r}")
        if not isinstance(v, str) or len(v) > 200:
            errores.append(f"valor de {k!r} no es texto de hasta 200 caracteres")
    if len(campos) > 64:
        errores.append("demasiados campos (máximo 64)")
    if not errores and sol["huella_campos"] != huella_campos(campos):
        errores.append("HUELLA_NO_COINCIDE: la huella no corresponde a los campos")
    emitida, caduca, aprob = _ts(sol["emitida"]), _ts(sol["caduca"]), _ts(sol["autorizacion"]["aprobada_en"])
    if not emitida < caduca or (caduca - emitida).total_seconds() > MAX_CADUCIDAD_S:
        errores.append(f"caducidad fuera de rango (máximo {MAX_CADUCIDAD_S} s desde la emisión)")
    if aprob > emitida:
        errores.append("la aprobación humana es posterior a la emisión")
    if ahora is not None and ahora >= caduca:
        errores.append("SOLICITUD_CADUCADA")
    return errores


def hallazgos_pii(obj, valores_prohibidos=()) -> list[str]:
    """Qué parece dato personal dentro de `obj` (cualquier cadena anidada, claves incluidas)."""
    out: list[str] = []
    prohibidos = [v.lower() for v in valores_prohibidos if isinstance(v, str) and len(v) >= 3]

    def rec(x, ruta):
        if isinstance(x, dict):
            for k, v in x.items():
                rec(k, f"{ruta}.<clave>")
                rec(v, f"{ruta}.{k}")
        elif isinstance(x, (list, tuple)):
            for i, v in enumerate(x):
                rec(v, f"{ruta}[{i}]")
        elif isinstance(x, str):
            if _RE_OPACO.match(x):          # huellas e identificadores: ya los fija el esquema
                return
            bajo = x.lower()
            for nombre, rx in _PII:
                if rx.search(x):
                    out.append(f"{ruta}: parece {nombre}")
            for p in prohibidos:
                if p in bajo:
                    out.append(f"{ruta}: repite un valor enviado en la solicitud")
                    break
    rec(obj, "$")
    return out


def validar_respuesta(resp, solicitud: dict) -> list[str]:
    """Errores de la respuesta frente a su solicitud (vacía = aceptable). Nunca imprime valores de la respuesta."""
    if not isinstance(resp, dict):
        return ["la respuesta no es un objeto JSON"]
    errores = _validar_esquema(resp, cargar_esquema("respuesta"))
    # Los hallazgos de PII se reportan SIN el valor encontrado.
    errores += hallazgos_pii(resp, solicitud.get("campos", {}).values())
    if errores:
        return errores
    if resp["id_solicitud"] != solicitud["id_solicitud"]:
        errores.append("id_solicitud no coincide con la solicitud")
    if resp["evidencia"]["huella_campos"] != solicitud["huella_campos"]:
        errores.append("HUELLA_NO_COINCIDE: la respuesta no confirma los mismos campos")
    if (resp["estado"] == "PREPARADO") != (resp["codigo"] == "OK"):
        errores.append("estado y código incoherentes (PREPARADO exige OK y solo él)")
    if resp["estado"] == "PREPARADO":
        if sorted(resp["campos_llenados"]) != sorted(solicitud["campos"]):
            errores.append("campos_llenados no coincide con los campos solicitados")
        if not resp["evidencia"]["huella_pagina"]:
            errores.append("PREPARADO sin huella de página")
    return errores


def resumen_error_seguro(errores: list[str]) -> str:
    """Texto apto para auditoría: solo la clase del error, sin valores."""
    return "; ".join(sorted({e.split(":")[0][:60] for e in errores}))[:300]
