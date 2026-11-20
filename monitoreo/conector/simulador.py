"""Simulador del conector (Python puro): lee UNA solicitud por stdin, escribe UNA respuesta por stdout.

No abre red ni archivos y no importa nada de Monitor salvo el contrato. No lee expedientes ni bases: solo lo que
llega por stdin. Escenarios por CONECTOR_SIM_MODO (solo pruebas): ok (por omisión), envio_humano, incidencias,
estructura, no_autorizado, intervencion, invalida, revision_caducada, error, filtra_pii, huella_mala,
codigo_mal, malformado, sin_salida, tardio. Salida del proceso = tabla del contrato (contrato.SALIDA); 6 si la
entrada no es válida (sin respuesta JSON si ni siquiera hay solicitud_id válido).
Uso: python -m monitoreo.conector.simulador < solicitud.json
"""
from __future__ import annotations

import json
import os
import sys
import time

from . import contrato as C

_VACIO = {"faltan": [], "nuevos": [], "modificados": []}


def responder(sol: dict, modo: str) -> tuple[dict, int]:
    form = sol["formulario"]
    firma = form["firma_estructura_esperada"]
    base = {"contrato": C.VERSION, "solicitud_id": sol["solicitud_id"],
            "formulario": {"origen": form["url"], "firma_estructura": firma, "estructura_coincide": True, "cambios": dict(_VACIO)},
            "campos": [], "confirmaciones": [], "errores": [],
            "revision_humana": {"requerida": True, "resultado": "no_realizada", "duracion_s": 0},
            "envio": {"realizado_por_conector": False, "envio_humano_observado": False},
            "huella_preparacion": "", "duracion_ms": 1}

    def parar(estado, codigo, **cambios):
        r = {**base, "estado": estado, "errores": [{"codigo": codigo}], **cambios}
        return r, C.SALIDA[estado]

    if modo == "no_autorizado":
        return parar("rechazado_no_autorizado", "FORMULARIO_NO_AUTORIZADO")
    if modo == "invalida":
        return parar("rechazado_solicitud_invalida", "SOLICITUD_MAL_FORMADA")
    if modo == "intervencion":
        return parar("detenido_intervencion_humana", "INTERVENCION_HUMANA_REQUERIDA")
    if modo == "estructura":
        f = {**base["formulario"], "firma_estructura": "v2:0000000000000000", "estructura_coincide": False,
             "cambios": {"faltan": [], "nuevos": ["alta::documento"], "modificados": [{"campo": "email", "cambio": "etiqueta"}]}}
        return parar("detenido_estructura_cambiada", "ESTRUCTURA_CAMBIADA", formulario=f)
    if modo == "error":
        r = {**base, "estado": "error_interno", "errores": [{"codigo": "NAVEGADOR_NO_DISPONIBLE"}]}
        return r, C.SALIDA["error_interno"]
    campos = [{"campo": k, "estado": "ok", "metodo": "teclado_secuencial", "caracteres": len(v)} for k, v in sorted(sol["campos"].items())]
    huella = C.huella_preparacion(sol["solicitud_id"], firma, sol["campos"])
    prep = {**base, "campos": campos, "huella_preparacion": huella,
            "revision_humana": {"requerida": True, "resultado": "cerrada_sin_envio", "duracion_s": 1}}
    if modo == "envio_humano":
        r = {**prep, "estado": "enviado_por_persona_observado",
             "revision_humana": {"requerida": True, "resultado": "envio_humano", "duracion_s": 1},
             "envio": {"realizado_por_conector": False, "envio_humano_observado": True}}
        return r, 0
    if modo == "incidencias":
        r = {**prep, "estado": "preparado_con_incidencias", "campos": [{**campos[0], "estado": "diferente"}] + campos[1:],
             "errores": [{"codigo": "CAMPO_NO_VERIFICADO"}]}
        return r, 2
    if modo == "revision_caducada":
        r = {**prep, "estado": "revision_caducada", "errores": [{"codigo": "REVISION_CADUCADA"}],
             "revision_humana": {"requerida": True, "resultado": "caducada", "duracion_s": sol["opciones"]["espera_revision_max_s"]}}
        return r, 7
    r = {**prep, "estado": "preparado_revisado_sin_envio"}
    if modo == "filtra_pii":      # defecto deliberado: repite un valor de la solicitud en un texto libre
        r["errores"] = [{"codigo": "ERROR_INTERNO", "detalle": "fallo con " + next(iter(sol["campos"].values()))}]
    if modo == "huella_mala":
        r["huella_preparacion"] = "sha256:" + "0" * 16
    return r, (3 if modo == "codigo_mal" else 0)


def main() -> int:
    modo = os.environ.get("CONECTOR_SIM_MODO", "ok")
    try:
        sol = json.loads(sys.stdin.read(1_000_000))
    except ValueError:
        return 6
    if C.validar_solicitud(sol):            # entrada inválida: no hay respuesta con valores; solo el código
        return 6
    if modo == "tardio":
        time.sleep(float(os.environ.get("CONECTOR_SIM_DEMORA_S", "5")))
    if modo == "sin_salida":
        return 0
    if modo == "malformado":
        sys.stdout.write("{no es json")
        return 0
    resp, salida = responder(sol, modo)
    sys.stdout.write(json.dumps(resp, ensure_ascii=False))
    return salida


if __name__ == "__main__":
    sys.exit(main())
