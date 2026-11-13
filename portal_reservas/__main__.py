"""Arranque:  python -m portal_reservas --puerto 8200 --datos data/portal/estado.json

Credenciales de PRUEBA por variables de entorno (nunca credenciales reales):
  PORTAL_TOKEN           token Bearer de la API de consulta que usa el motor
  PORTAL_TOKEN_CONTROL   token de las rutas /control (laboratorio)
Solo escucha en 127.0.0.1/localhost: no se publica en la red.
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
    ap = argparse.ArgumentParser(prog="portal_reservas", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--puerto", type=int, default=8200)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--datos", type=Path, default=Path("data/portal/estado.json"))
    ap.add_argument("--semilla", type=int, default=5)
    ap.add_argument("--dias", type=int, default=10)
    ap.add_argument("--retencion-s", type=float, default=300, help="segundos que dura un horario apartado")
    ap.add_argument("--sesion-s", type=float, default=900, help="segundos que dura una sesión")
    ap.add_argument("--zona-horaria", default=os.environ.get("PORTAL_ZONA_HORARIA") or None,
                    help="zona IANA del calendario (p. ej. America/Mexico_City); por defecto la del sistema")
    ap.add_argument("--horizonte-dias", type=int, default=30, help="días hacia adelante del inventario diario")
    ap.add_argument("--dias-habilitados", type=int, default=15, help="días hábiles habilitados al sembrar")
    a = ap.parse_args(argv)
    try:
        loopback = ipaddress.ip_address(a.host).is_loopback
    except ValueError:
        loopback = a.host == "localhost"
    if not loopback:
        print("Por seguridad el portal de prueba solo escucha en 127.0.0.1/localhost.", file=sys.stderr)
        return 2
    token, control = os.environ.get("PORTAL_TOKEN"), os.environ.get("PORTAL_TOKEN_CONTROL")
    if not token or not control:
        print("Faltan PORTAL_TOKEN y PORTAL_TOKEN_CONTROL (valores de prueba inventados).", file=sys.stderr)
        return 2
    a.datos.parent.mkdir(parents=True, exist_ok=True)
    app = crear_app(a.datos, token, control, semilla=a.semilla, dias=a.dias, retencion_s=a.retencion_s,
                    sesion_s=a.sesion_s, zona=a.zona_horaria, horizonte_dias=a.horizonte_dias,
                    dias_habilitados=a.dias_habilitados)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print(f"Portal de reservaciones de PRUEBA en http://{a.host}:{a.puerto}/reservar (datos ficticios en {a.datos})",
          flush=True)
    uvicorn.run(app, host=a.host, port=a.puerto, log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
