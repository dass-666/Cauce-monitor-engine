"""Simulador del conector en Python puro: lee UNA solicitud por stdin, escribe UNA respuesta por stdout.

No abre red, no abre archivos, no importa Monitor salvo el contrato. Escenarios por la variable de entorno
CONECTOR_SIM_MODO (solo pruebas): ok (por omisión), rechazar, error, incierto, tardio, filtra_pii, malformado,
huella_mala, sin_salida. Códigos de salida: 0 respuesta emitida, 2 entrada inválida.
Uso: python -m monitoreo.conector.simulador < solicitud.json
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

from . import contrato as C


def responder(sol: dict, modo: str) -> dict:
    base = {"contrato": C.VERSION, "id_solicitud": sol["id_solicitud"], "enviado": False,
            "campos_llenados": [], "evidencia": {"huella_campos": sol["huella_campos"], "huella_pagina": "", "pasos": 0},
            "duracion_ms": 1}
    if modo == "rechazar":
        return {**base, "estado": "RECHAZADO", "codigo": "CAMPO_NO_RECONOCIDO"}
    if modo == "error":
        return {**base, "estado": "ERROR", "codigo": "ERROR_INTERNO"}
    if modo == "incierto":
        return {**base, "estado": "INCIERTO", "codigo": "TIEMPO_AGOTADO"}
    pagina = hashlib.sha256(("pagina-simulada|" + sol["portal"]["proveedor_id"]).encode()).hexdigest()
    ok = {**base, "estado": "PREPARADO", "codigo": "OK", "campos_llenados": sorted(sol["campos"]),
          "evidencia": {"huella_campos": sol["huella_campos"], "huella_pagina": pagina, "pasos": 2 + len(sol["campos"])}}
    if modo == "filtra_pii":      # defecto deliberado: repite un valor de la solicitud
        ok["campos_llenados"] = list(sol["campos"].values())[:1] + ok["campos_llenados"]
    if modo == "huella_mala":
        ok["evidencia"] = {**ok["evidencia"], "huella_campos": "0" * 64}
    return ok


def main() -> int:
    modo = os.environ.get("CONECTOR_SIM_MODO", "ok")
    try:
        sol = json.loads(sys.stdin.read(1_000_000))
    except ValueError:
        return 2
    if C.validar_solicitud(sol):         # entrada inválida: el simulador no responde
        return 2
    if modo == "tardio":
        time.sleep(float(os.environ.get("CONECTOR_SIM_DEMORA_S", "5")))
    if modo == "sin_salida":
        return 0
    if modo == "malformado":
        sys.stdout.write("{no es json")
        return 0
    sys.stdout.write(json.dumps(responder(sol, modo), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
