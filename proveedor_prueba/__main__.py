"""Arranque:  python -m proveedor_prueba --puerto 8100 --datos data/proveedor/estado.json

Credenciales de PRUEBA por variables de entorno (nunca credenciales reales):
  PROVEEDOR_TOKEN           token Bearer que usará el motor
  PROVEEDOR_TOKEN_CONTROL   token de las rutas /control (laboratorio)
Por defecto escucha solo en 127.0.0.1: no se publica en la red.
"""
from __future__ import annotations

import argparse
import ipaddress
import logging
import os
import sys
from pathlib import Path

import uvicorn

from .servidor import crear_app


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="proveedor_prueba", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--puerto", type=int, default=8100)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--datos", type=Path, default=Path("data/proveedor/estado.json"))
    ap.add_argument("--semilla", type=int, default=7)
    ap.add_argument("--recursos-por-region", type=int, default=12)
    ap.add_argument("--cuota-por-min", type=int, default=1200, help="0 = sin cuota")
    a = ap.parse_args(argv)
    try:
        loopback = ipaddress.ip_address(a.host).is_loopback
    except ValueError:
        loopback = a.host == "localhost"
    if not loopback:
        print("Por seguridad el proveedor de prueba solo escucha en 127.0.0.1/localhost.", file=sys.stderr)
        return 2
    token, control = os.environ.get("PROVEEDOR_TOKEN"), os.environ.get("PROVEEDOR_TOKEN_CONTROL")
    if not token or not control:
        print("Faltan PROVEEDOR_TOKEN y PROVEEDOR_TOKEN_CONTROL (valores de prueba inventados).", file=sys.stderr)
        return 2
    a.datos.parent.mkdir(parents=True, exist_ok=True)
    app = crear_app(a.datos, token, control, semilla=a.semilla, recursos_por_region=a.recursos_por_region,
                    cuota_por_min=a.cuota_por_min)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print(f"Proveedor de PRUEBA en http://{a.host}:{a.puerto} (datos ficticios en {a.datos})", flush=True)
    uvicorn.run(app, host=a.host, port=a.puerto, log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
