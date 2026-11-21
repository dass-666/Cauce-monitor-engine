"""Confirmación humana explícita e INDEPENDIENTE en Monitor (laboratorio, en memoria; sin migración ni tabla).

Regla de fondo: lo que dice el conector (o lo que hace el navegador) NUNCA equivale a revisión humana ni a envío confirmado.
- Cerrar la ventana → PREPARADO («no se envió»), no «aprobado».
- «enviado_por_persona_observado» → INDICIO de envío, no confirmación.
- Una persona, en la interfaz de Monitor, confirma la REVISIÓN (frase exacta + huella de lo preparado + haber abierto la
  revisión un mínimo de segundos) y, aparte, confirma el ENVÍO con un comprobante (folio) que Monitor guarda como huella.

No cambia el contrato compartido ni el Registro de estados: es un registro paralelo, cuyas únicas entradas son las que
introduce una persona por el canal de Monitor. Ninguna función recibe una respuesta del conector para «aprobar».
Para uso operativo faltaría persistencia (tabla + migración revisada) y la interfaz; aquí solo se define y se prueba la regla.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from . import contrato as C
from .registro import ErrorRegistro, Registro

FRASE_REVISION = "REVISÉ Y APRUEBO"
MIN_REVISION_S = 5            # la persona debe haber tenido abierta la revisión al menos este tiempo
VIGENCIA_PREPARACION_S = 900  # pasado este tiempo desde que quedó PREPARADO ya no se puede aprobar
VIGENCIA_APROBACION_S = 600   # una aprobación se consume o caduca
_REF = re.compile(r"^rev_[a-z0-9_]{1,40}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
NO_PERSONAS = ("conector", "monitor", "simulador", "sistema")


class ErrorConfirmacion(Exception):
    def __init__(self, codigo: str):
        super().__init__(codigo)
        self.codigo = codigo


class Confirmaciones:
    def __init__(self, registro: Registro):
        self.registro = registro
        self._r: dict[str, dict] = {}
        self.diario: list[dict] = []                    # sin datos personales ni valores de campos

    def _ev(self, sid: str, evento: str, persona: str = "") -> None:
        self.diario.append({"solicitud_id": sid, "evento": evento, "persona_ref": persona})

    # ------------------------------------------------------------------ entrada: lo recibido del conector (solo registra)
    def registrar_preparacion(self, solicitud: dict, respuesta: dict, ahora: datetime) -> None:
        """Guarda LO QUE SE PREPARÓ (huella). No aprueba nada. Solo si Monitor ya la aceptó como PREPARADO."""
        sid = solicitud["solicitud_id"]
        reg = self.registro._por_id.get(sid)
        if not reg or reg["estado"] != "PREPARADO":
            raise ErrorConfirmacion("NO_PREPARADO")
        if C.validar_respuesta(respuesta, solicitud) != []:
            raise ErrorConfirmacion("RESPUESTA_INVALIDA")
        self._r[sid] = {"huella": respuesta["huella_preparacion"], "preparado_en": ahora, "abierta": None, "abierta_por": "",
                        "revision": None, "envio": None, "revocada": False}
        self._ev(sid, "PREPARACION_REGISTRADA")

    # ------------------------------------------------------------------ canal de la persona (interfaz de Monitor)
    def _persona(self, persona_ref: str) -> None:
        if not _REF.match(persona_ref or "") or persona_ref in NO_PERSONAS:
            raise ErrorConfirmacion("PERSONA_INVALIDA")

    def _reg(self, sid: str) -> dict:
        r = self._r.get(sid)
        if r is None:
            raise ErrorConfirmacion("SIN_PREPARACION")
        return r

    def abrir_revision(self, sid: str, persona_ref: str, ahora: datetime) -> str:
        """La persona abre la pantalla de revisión; devuelve la huella que debe ver y repetir al confirmar."""
        self._persona(persona_ref)
        r = self._reg(sid)
        r["abierta"], r["abierta_por"] = ahora, persona_ref
        self._ev(sid, "REVISION_ABIERTA", persona_ref)
        return r["huella"]

    def confirmar_revision(self, sid: str, persona_ref: str, frase: str, huella_vista: str, ahora: datetime) -> dict:
        self._persona(persona_ref)
        r = self._reg(sid)
        if self.registro._por_id[sid]["estado"] != "PREPARADO":
            raise ErrorConfirmacion("ESTADO_NO_ES_PREPARADO")
        if r["revocada"]:
            raise ErrorConfirmacion("REVOCADA")
        if r["revision"] is not None:
            raise ErrorConfirmacion("YA_CONFIRMADA")
        if ahora - r["preparado_en"] > timedelta(seconds=VIGENCIA_PREPARACION_S):
            raise ErrorConfirmacion("PREPARACION_VENCIDA")
        if r["abierta"] is None or r["abierta_por"] != persona_ref:
            raise ErrorConfirmacion("REVISION_NO_ABIERTA_POR_ESTA_PERSONA")
        if ahora - r["abierta"] < timedelta(seconds=MIN_REVISION_S):
            raise ErrorConfirmacion("REVISION_DEMASIADO_RAPIDA")
        if frase != FRASE_REVISION:
            raise ErrorConfirmacion("FRASE_INCORRECTA")
        if huella_vista != r["huella"]:
            raise ErrorConfirmacion("HUELLA_NO_COINCIDE")
        r["revision"] = {"persona_ref": persona_ref, "en": ahora}
        self._ev(sid, "REVISION_CONFIRMADA", persona_ref)
        return {"revision": "CONFIRMADA_POR_PERSONA", "persona_ref": persona_ref}

    def revocar(self, sid: str, persona_ref: str, ahora: datetime) -> None:
        self._persona(persona_ref)
        r = self._reg(sid)
        r["revocada"] = True
        self._ev(sid, "REVOCADA", persona_ref)

    def confirmar_envio(self, sid: str, persona_ref: str, comprobante_sha256: str, ahora: datetime) -> dict:
        """El envío solo se confirma con un comprobante (huella del folio) y tras una revisión confirmada."""
        self._persona(persona_ref)
        r = self._reg(sid)
        if r["revision"] is None or r["revocada"]:
            raise ErrorConfirmacion("SIN_REVISION_CONFIRMADA")
        if r["envio"] is not None:
            raise ErrorConfirmacion("ENVIO_YA_CONFIRMADO")
        if not _SHA.match(comprobante_sha256 or ""):
            raise ErrorConfirmacion("COMPROBANTE_INVALIDO")
        r["envio"] = {"persona_ref": persona_ref, "en": ahora, "comprobante": comprobante_sha256}
        self._ev(sid, "ENVIO_CONFIRMADO_CON_COMPROBANTE", persona_ref)
        return {"envio": "CONFIRMADO_CON_COMPROBANTE"}

    # ------------------------------------------------------------------ consulta: ¿qué se sabe realmente?
    def estado(self, sid: str, ahora: datetime) -> dict:
        reg = self.registro._por_id.get(sid)
        r = self._r.get(sid)
        revision = "SIN_CONFIRMAR"
        if r and r["revision"] and not r["revocada"]:
            vig = ahora - r["revision"]["en"] <= timedelta(seconds=VIGENCIA_APROBACION_S) or r["envio"] is not None
            revision = "CONFIRMADA_POR_PERSONA" if vig else "CADUCADA"
        elif r and r["revocada"]:
            revision = "REVOCADA"
        envio = "CONFIRMADO_CON_COMPROBANTE" if r and r["envio"] else (
            "INDICIO_DEL_CONECTOR" if reg and reg["estado"] == "ENVIADA_POR_PERSONA" else "NO_CONFIRMADO")
        return {"estado_monitor": reg["estado"] if reg else None, "revision_humana": revision, "envio": envio}

    def puede_continuar(self, sid: str, ahora: datetime) -> tuple[bool, str]:
        """Única puerta para usar una preparación: exige revisión confirmada por una persona, vigente y no revocada."""
        e = self.estado(sid, ahora)
        if e["estado_monitor"] != "PREPARADO":
            return False, "ESTADO_NO_ES_PREPARADO"
        if e["revision_humana"] != "CONFIRMADA_POR_PERSONA":
            return False, "REVISION_" + e["revision_humana"]
        return True, "OK"
