"""Crea .env a partir de .env.ejemplo. La contraseña llega por la variable de entorno MON_PWD."""
import os
import pathlib
import re
import secrets
import sys

root = pathlib.Path(__file__).resolve().parent.parent
pwd = os.environ.get("MON_PWD", "")
if len(pwd) < 10 or pwd.isalpha() or pwd.isdigit():
    sys.exit("La contraseña debe tener al menos 10 caracteres y combinar letras y números.")
if pwd != pwd.strip().strip('"').strip("'"):
    sys.exit("La contraseña no puede empezar ni terminar con espacios o comillas.")
t = (root / ".env.ejemplo").read_text(encoding="utf-8")
t = re.sub(r"(?m)^MONITOREO_ADMIN_PASSWORD=.*$", lambda m: "MONITOREO_ADMIN_PASSWORD=" + pwd, t)
t = re.sub(r"(?m)^MONITOREO_SECRET_KEY=.*$", lambda m: "MONITOREO_SECRET_KEY=" + secrets.token_urlsafe(32), t)
(root / ".env").write_text(t, encoding="utf-8")
print("Archivo .env creado.")
