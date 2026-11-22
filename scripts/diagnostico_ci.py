"""Diagnóstico COMPARTIDO por los recorridos de interfaz (v2.3 y RC1): sockets TCP por estado y la impresión de las dos líneas
que se buscan en los logs de CI. Solo observa; no cambia criterios de éxito. Ver docs/CI_RESERVACIONES_DIAGNOSTICO.md."""
from __future__ import annotations

import json
import subprocess


def estados_sockets() -> dict:
    """Cuántos sockets TCP hay en cada estado (agotamiento de puertos efímeros / TIME_WAIT). Sin direcciones ni puertos."""
    try:
        t = subprocess.run(["netstat", "-an"], capture_output=True, text=True, timeout=30).stdout
    except Exception as e:  # noqa: BLE001
        return {"error": type(e).__name__}
    n: dict = {}
    for ln in t.splitlines():
        if ln.split()[:1] in (["TCP"], ["tcp"], ["tcp6"]):
            est = ln.split()[-1]
            n[est] = n.get(est, 0) + 1
    return n


def imprimir(nav: dict) -> None:
    """Si el navegador dejó un diagnóstico (solo ocurre cuando el recorrido falló), lo imprime."""
    if nav.get("diagnostico") is not None:
        print("DIAGNOSTICO_NAVEGADOR " + json.dumps(nav["diagnostico"], ensure_ascii=False))
        print("DIAGNOSTICO_SOCKETS " + json.dumps(estados_sockets()))
