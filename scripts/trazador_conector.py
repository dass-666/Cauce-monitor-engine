"""Trazador de LABORATORIO: ejecuta `preparar` del conector (sin modificarlo) y anota, sin datos personales, en qué
orden llegan el cierre de la página y la desconexión del navegador, y qué resultado devuelve la espera de revisión.
Uso: PYTHONPATH=<conector> CAUCE_TRACE=<archivo.jsonl> python scripts/trazador_conector.py < solicitud.json"""
import json
import os
import sys
import time

import cauce_form_connector.integracion as I

_orig = I._esperar_revision


def _traza(sesion, espera, desde, gancho=None):
    t0, ev = time.monotonic(), []
    with open(os.environ["CAUCE_TRACE"], "a", encoding="utf-8") as f:     # el relleno ya terminó: empieza la revisión
        f.write(json.dumps(["inicio_revision"]) + "\n")

    def reg(nombre):
        ev.append([nombre, round(time.monotonic() - t0, 4)])
    pg = sesion.page
    pg.on("close", lambda *_: reg("page_close"))
    pg.on("crash", lambda *_: reg("page_crash"))
    sesion._browser.on("disconnected", lambda *_: reg("browser_disconnected"))
    try:
        res = _orig(sesion, espera, desde, gancho)
    except BaseException as ex:
        reg("excepcion:" + type(ex).__name__)
        raise
    reg("retorno:" + res[0])
    try:
        ev.append(["is_connected_al_retorno", bool(sesion._browser.is_connected())])
    except Exception:
        pass
    with open(os.environ["CAUCE_TRACE"], "a", encoding="utf-8") as f:
        f.write(json.dumps(ev) + "\n")
    return res


I._esperar_revision = _traza
_orig_prep = I._preparar


def _prep(*a, **k):
    try:
        return _orig_prep(*a, **k)
    except BaseException as ex:                 # solo tipo y funciones de la pila; nunca valores
        import traceback
        pila = [f"{fr.name}:{fr.lineno}" for fr in traceback.extract_tb(ex.__traceback__)][-6:]
        with open(os.environ["CAUCE_TRACE"], "a", encoding="utf-8") as f:
            f.write(json.dumps(["EXC_en_preparar", type(ex).__name__, pila]) + "\n")
        raise


I._preparar = _prep
sys.exit(I.principal())
