"""Contrato unificado `cauce.conector.v1` (Monitor <-> Conector Universal): validación y reglas.

Nace de conciliar el contrato que Monitor implementó (`cauce.conector/1`) con la propuesta de la auditoría del
Conector Universal (`cauce.conector.v1`); ver docs/CONECTOR_CONTRATO_v1.md. Sigue siendo PROPUESTO: el conector real
todavía no lo implementa (su auditoría lista el trabajo pendiente).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from ..interop.esquema import validar as _validar_esquema

VERSION = "cauce.conector.v1"
ESQUEMAS = Path(__file__).parent / "esquemas"
MAX_VENTANA_S = 900            # caduca_en - emitida_en: plazo máximo para EMPEZAR a atender la solicitud
MARGEN_PROCESO_S = 180         # tras espera_revision_max_s, Monitor mata el proceso y deja la solicitud INCIERTA

# estado del conector (cable) -> código de salida del proceso (tabla del Conector Universal, §5.5)
SALIDA = {"preparado_revisado_sin_envio": 0, "enviado_por_persona_observado": 0, "preparado_con_incidencias": 2,
          "rechazado_no_autorizado": 3, "detenido_intervencion_humana": 4, "detenido_estructura_cambiada": 5,
          "rechazado_solicitud_invalida": 6, "revision_caducada": 7, "error_interno": 1}
# estado del conector -> estado de Monitor (INCIERTA no viene del conector: la deriva Monitor)
MAPA_ESTADO = {"preparado_revisado_sin_envio": "PREPARADO", "enviado_por_persona_observado": "ENVIADA_POR_PERSONA",
               "preparado_con_incidencias": "CON_INCIDENCIAS", "rechazado_no_autorizado": "RECHAZADA",
               "rechazado_solicitud_invalida": "RECHAZADA", "detenido_intervencion_humana": "DETENIDA",
               "detenido_estructura_cambiada": "DETENIDA", "revision_caducada": "CADUCADA", "error_interno": "ERROR"}
ESTADOS_SOLICITUD = ("CREADA", "AUTORIZADA", "LANZADA", "PREPARADO", "ENVIADA_POR_PERSONA", "CON_INCIDENCIAS", "DETENIDA",
                     "RECHAZADA", "CADUCADA", "ERROR", "CANCELADA", "INCIERTA")
TERMINALES = ("PREPARADO", "ENVIADA_POR_PERSONA", "CON_INCIDENCIAS", "DETENIDA", "RECHAZADA", "CADUCADA", "ERROR", "CANCELADA")
_DESDE_LANZADA = ("PREPARADO", "ENVIADA_POR_PERSONA", "CON_INCIDENCIAS", "DETENIDA", "RECHAZADA", "CADUCADA", "ERROR",
                  "INCIERTA", "CANCELADA")
TRANSICIONES = {"CREADA": ("AUTORIZADA", "CANCELADA", "CADUCADA"),
                "AUTORIZADA": ("LANZADA", "CANCELADA", "CADUCADA"),
                "LANZADA": _DESDE_LANZADA,
                "INCIERTA": ("PREPARADO", "ENVIADA_POR_PERSONA", "RECHAZADA", "ERROR", "CANCELADA")}   # solo conciliando
# se detienen antes de teclear: no puede haber campos ni huella (0 teclas)
_SIN_CAMPOS = ("rechazado_no_autorizado", "rechazado_solicitud_invalida", "detenido_intervencion_humana",
               "detenido_estructura_cambiada")
_PREPARADOS = ("preparado_revisado_sin_envio", "enviado_por_persona_observado", "preparado_con_incidencias",
               "revision_caducada")   # revision_caducada: se preparó y verificó, pero nadie revisó a tiempo

_RE_CAMPO = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_RE_OPACO = re.compile(r"^(?:[0-9a-f]{64}|sol_[0-9a-f]{16,32}|v2:[0-9a-f]{16}|sha256:[0-9a-f]{8,64}|(?:frm|aut|rev)_[a-z0-9_]{1,40})$")
_PII = (
    ("correo", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("telefono", re.compile(r"(?<!\d)(?:\+?\d[\s().-]?){10,15}(?!\d)")),
    ("fecha", re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{2}/\d{2}/\d{4}\b")),
    ("curp", re.compile(r"\b[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d\b", re.I)),
)


def cargar_esquema(nombre: str) -> dict:
    return json.loads((ESQUEMAS / f"{nombre}.schema.json").read_text(encoding="utf-8"))


def _canon(campos: dict) -> str:
    return json.dumps(campos, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def huella_preparacion(solicitud_id: str, firma_estructura: str, campos: dict, largo: int = 16) -> str:
    """sha256(solicitud_id | firma | valores canónicos), salada con el id de la solicitud. Monitor la recalcula con los
    valores que él mismo envió; los valores no viajan. El prefijo corto no protege valores de poca entropía por
    sí solo: Monitor no debe publicarla."""
    h = hashlib.sha256(f"{solicitud_id}|{firma_estructura}|{_canon(campos)}".encode("utf-8")).hexdigest()
    return "sha256:" + h[:largo]


def ts(texto: str) -> datetime:
    """ISO 8601 con zona (Z o ±hh:mm) -> UTC. Sin zona no se acepta."""
    d = datetime.fromisoformat(texto)
    if d.tzinfo is None:
        raise ValueError("fecha sin zona horaria")
    return d.astimezone(timezone.utc)


def validar_solicitud(sol, ahora: datetime | None = None, solo_local: bool = True) -> list[str]:
    """Errores (vacía = válida y vigente). `ahora` en UTC con zona. `solo_local`: en la fase de laboratorio solo se
    admiten URLs 127.0.0.1 (política de Monitor, no del contrato)."""
    if not isinstance(sol, dict):
        return ["la solicitud no es un objeto JSON"]
    errores = _validar_esquema(sol, cargar_esquema("solicitud"))
    if errores:
        return errores
    campos = sol["campos"]
    if not campos or len(campos) > 64:
        errores.append("CAMPOS_FUERA_DE_RANGO: de 1 a 64 campos")
    for k, v in campos.items():
        if not _RE_CAMPO.match(k):
            errores.append(f"CAMPO_NO_DECLARADO: nombre inválido {k!r}")
        if not isinstance(v, str) or len(v) > 200:
            errores.append(f"valor de {k!r} no es texto de hasta 200 caracteres")
    if sol["autorizacion"]["formulario_id"] != sol["formulario"]["formulario_id"]:
        errores.append("FORMULARIO_NO_AUTORIZADO: la autorización es de otro formulario")
    try:
        emitida, caduca = ts(sol["emitida_en"]), ts(sol["caduca_en"])
        vigente = date.fromisoformat(sol["autorizacion"]["vigente_hasta"])
    except ValueError as e:
        return errores + [f"fecha inválida: {e}"]
    if not emitida < caduca or (caduca - emitida).total_seconds() > MAX_VENTANA_S:
        errores.append(f"caducidad fuera de rango (máximo {MAX_VENTANA_S} s desde la emisión)")
    if solo_local and urlsplit(sol["formulario"]["url"]).hostname != "127.0.0.1":
        errores.append("PORTAL_NO_LOCAL: en la fase de laboratorio solo se admite 127.0.0.1")
    if ahora is not None:
        if ahora >= caduca:
            errores.append("SOLICITUD_CADUCADA")
        if vigente < ahora.date():
            errores.append("AUTORIZACION_CADUCADA")
    return errores


def _opaco(s: str) -> bool:
    return bool(_RE_OPACO.match(s))


def hallazgos_pii(obj, valores_prohibidos=()) -> list[str]:
    """Qué parece dato personal en `obj` (cadenas anidadas, claves incluidas). Nunca devuelve el valor hallado.
    Un valor enviado se considera repetido si es igual a la cadena (3-5 caracteres) o está contenido en ella (6 o más)."""
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
        elif isinstance(x, str) and not _opaco(x):
            bajo = x.lower()
            for nombre, rx in _PII:
                if rx.search(x):
                    out.append(f"{ruta}: parece {nombre}")
            for p in prohibidos:
                if bajo == p or (len(p) >= 6 and p in bajo):
                    out.append(f"{ruta}: repite un valor enviado en la solicitud")
                    break
    rec(obj, "$")
    return out


def validar_respuesta(resp, sol: dict, codigo_salida: int | None = None) -> list[str]:
    """Errores de la respuesta frente a su solicitud (vacía = aceptable). Si se conoce, `codigo_salida` debe
    corresponder al estado. Los mensajes no reproducen valores de la respuesta."""
    if not isinstance(resp, dict):
        return ["la respuesta no es un objeto JSON"]
    errores = _validar_esquema(resp, cargar_esquema("respuesta"))
    errores += hallazgos_pii(resp, sol.get("campos", {}).values())
    if errores:
        return errores
    est = resp["estado"]
    if resp["solicitud_id"] != sol["solicitud_id"]:
        errores.append("solicitud_id no coincide con la solicitud")
    if resp["formulario"]["origen"] != sol["formulario"]["url"]:
        errores.append("origen distinto de la URL solicitada")
    if codigo_salida is not None and codigo_salida != SALIDA[est]:
        errores.append("CODIGO_SALIDA_INCOHERENTE: el código de salida no corresponde al estado")
    campos = resp["campos"]
    nombres = [c["campo"] for c in campos]
    if len(set(nombres)) != len(nombres) or not set(nombres) <= set(sol["campos"]):
        errores.append("CAMPO_NO_DECLARADO: la respuesta menciona campos no solicitados o repetidos")
    if resp["envio"]["envio_humano_observado"] != (est == "enviado_por_persona_observado"):
        errores.append("envio_humano_observado incoherente con el estado")
    if est in _SIN_CAMPOS and (campos or resp["huella_preparacion"]):
        errores.append(f"{est} no debe haber tecleado ni preparado nada")
    if est in _PREPARADOS:
        if sorted(nombres) != sorted(sol["campos"]):
            errores.append("campos_llenados no coincide con los campos solicitados")
        firma = resp["formulario"]["firma_estructura"]
        if not firma or not resp["formulario"]["estructura_coincide"] or firma != sol["formulario"]["firma_estructura_esperada"]:
            errores.append("PERFIL_NO_COINCIDE: la estructura no coincide con la esperada")
        esperada = huella_preparacion(sol["solicitud_id"], sol["formulario"]["firma_estructura_esperada"], sol["campos"], 64)
        if not resp["huella_preparacion"] or not esperada.startswith(resp["huella_preparacion"]):
            errores.append("HUELLA_NO_COINCIDE: lo preparado no corresponde a lo solicitado")
    if est in ("preparado_revisado_sin_envio", "enviado_por_persona_observado"):
        if any(c["estado"] not in ("ok", "ok_normalizado") for c in campos) or resp["errores"] \
                or not all(c["coinciden"] for c in resp["confirmaciones"]):
            errores.append("estado exitoso con campos, confirmaciones o errores no válidos")
    if est == "preparado_revisado_sin_envio" and resp["revision_humana"]["resultado"] != "cerrada_sin_envio":
        errores.append("revision_humana incoherente con preparado_revisado_sin_envio")
    if est == "enviado_por_persona_observado" and resp["revision_humana"]["resultado"] != "envio_humano":
        errores.append("revision_humana incoherente con enviado_por_persona_observado")
    if est == "revision_caducada" and resp["revision_humana"]["resultado"] != "caducada":
        errores.append("revision_humana incoherente con revision_caducada")
    if est == "preparado_con_incidencias" and not resp["errores"] \
            and all(c["estado"] in ("ok", "ok_normalizado") for c in campos) and all(c["coinciden"] for c in resp["confirmaciones"]):
        errores.append("preparado_con_incidencias sin ninguna incidencia")
    if est == "detenido_estructura_cambiada" and resp["formulario"]["estructura_coincide"]:
        errores.append("detenido_estructura_cambiada con estructura_coincide=true")
    if est != "detenido_estructura_cambiada" and not resp["formulario"]["estructura_coincide"] and est in (
            "preparado_revisado_sin_envio", "enviado_por_persona_observado"):
        errores.append("estructura distinta con estado exitoso")
    return errores


def resumen_error_seguro(errores: list[str]) -> str:
    """Texto apto para auditoría: solo la clase del error, sin valores."""
    return "; ".join(sorted({e.split(":")[0][:60] for e in errores}))[:300]
