"""Crea .env a partir de .env.ejemplo. La contraseña llega por la variable de entorno MON_PWD.

MON_ENTORNO=produccion (instalar.ps1 -Produccion) deja el archivo listo para uso real:
entorno produccion, sin datos de demostración, contraseña de 12+ caracteres y clave larga.
"""
import os
import pathlib
import re
import secrets
import sys

root = pathlib.Path(__file__).resolve().parent.parent
pwd = os.environ.get("MON_PWD", "")
prod = os.environ.get("MON_ENTORNO", "").strip().lower() == "produccion"
minimo = 12 if prod else 10
if len(pwd) < minimo or pwd.isalpha() or pwd.isdigit():
    sys.exit(f"La contraseña debe tener al menos {minimo} caracteres y combinar letras y números.")
if pwd != pwd.strip().strip('"').strip("'"):
    sys.exit("La contraseña no puede empezar ni terminar con espacios o comillas.")
if (root / ".env").exists():
    sys.exit("Ya existe .env: no se sobrescribe.")


def put(text: str, key: str, value: str) -> str:
    pat = re.compile(rf"(?m)^#?\s*{key}=.*$")
    if pat.search(text):
        return pat.sub(lambda m: f"{key}={value}", text, count=1)
    return text.rstrip("\n") + f"\n{key}={value}\n"


t = (root / ".env.ejemplo").read_text(encoding="utf-8")
t = put(t, "MONITOREO_ADMIN_PASSWORD", pwd)
t = put(t, "MONITOREO_SECRET_KEY", secrets.token_urlsafe(48 if prod else 32))
if prod:
    t = put(t, "MONITOREO_ENTORNO", "produccion")
    t = put(t, "MONITOREO_DATOS_DEMO", "0")
(root / ".env").write_text(t, encoding="utf-8")
print("Archivo .env creado" + (" para PRODUCCIÓN." if prod else "."))
