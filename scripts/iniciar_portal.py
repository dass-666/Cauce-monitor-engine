"""Arranca el portal de reservaciones de PRUEBA con la configuración de .env (v1.5).

Lee MONITOREO_PORTAL_URL (puerto) y MONITOREO_PORTAL_TOKEN de .env, genera un token de control
de un solo uso y lanza `python -m portal_reservas`. Solo escucha en 127.0.0.1. Datos ficticios en
data/portal/estado.json.
"""
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from monitoreo import config  # noqa: E402,F401  (carga .env)
from monitoreo.captura import portal  # noqa: E402

u = urlparse(portal.url())
if u.hostname not in ("127.0.0.1", "localhost"):
    sys.exit("MONITOREO_PORTAL_URL debe apuntar a 127.0.0.1: el portal de prueba es solo local.")
os.environ["PORTAL_TOKEN"] = portal.token()
os.environ.setdefault("PORTAL_TOKEN_CONTROL", secrets.token_urlsafe(24))
from portal_reservas.__main__ import main  # noqa: E402

zona = os.environ.get("MONITOREO_ZONA_HORARIA", "").strip()     # v1.6: misma zona que el motor
sys.exit(main(["--puerto", str(u.port or 8200), "--datos", str(ROOT / "data" / "portal" / "estado.json"),
               *(["--zona-horaria", zona] if zona else []), *sys.argv[1:]]))
