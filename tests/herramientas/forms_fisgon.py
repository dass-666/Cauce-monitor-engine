"""Conector de PRUEBA «fisgón»: hace de Forms, pero antes intenta leer lo privado de Monitor.

Uso (solo pruebas): python forms_fisgon.py <carpeta> <rutas.json>
- <rutas.json>: lista de {"ref", "ruta", "tipo": "archivo"|"carpeta"|"escribir"} que el fisgón intenta abrir.
- <carpeta>/fisgon.json: usuario con el que corrió, resultado de cada intento (DENEGADO/PERMITIDO/...), nombres de
  las variables de entorno, HOME/TEMP. NUNCA el contenido de lo que haya podido leer.
Después responde como el espía (simulador del contrato), para que el recorrido termine normal.
"""
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from monitoreo.conector import simulador  # noqa: E402


def usuario():
    if sys.platform == "win32":
        import ctypes
        b = ctypes.create_unicode_buffer(256)
        n = ctypes.c_ulong(256)
        ctypes.windll.advapi32.GetUserNameW(b, ctypes.byref(n))
        return b.value
    import pwd
    return pwd.getpwuid(os.getuid()).pw_name


def intento(fn):
    try:
        fn()
        return "PERMITIDO"
    except PermissionError:
        return "DENEGADO"
    except FileNotFoundError:
        return "NO_EXISTE"
    except Exception as e:  # noqa: BLE001
        return "ERROR_" + type(e).__name__


def leer(p):
    with open(p, "rb") as f:
        f.read(16)


def escribir(c):
    q = Path(c) / f".fisgon_{uuid.uuid4().hex}"
    q.write_bytes(b"x")
    q.unlink()


carpeta = Path(sys.argv[1])
rutas = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
res = []
for r in rutas:
    fn = {"archivo": lambda p=r["ruta"]: leer(p), "carpeta": lambda p=r["ruta"]: os.listdir(p),
          "escribir": lambda p=r["ruta"]: escribir(p)}[r["tipo"]]
    res.append({"ref": r["ref"], "tipo": r["tipo"], "resultado": intento(fn)})
carpeta.mkdir(parents=True, exist_ok=True)
texto = sys.stdin.read()
(carpeta / "fisgon.json").write_text(json.dumps({
    "usuario": usuario(), "intentos": res, "entorno": sorted(os.environ),
    "home": os.environ.get("HOME", ""), "temp": os.environ.get("TEMP", ""), "pid": os.getpid()}), encoding="utf-8")
resp, salida = simulador.responder(json.loads(texto), "ok")
sys.stdout.write(json.dumps(resp, ensure_ascii=False))
sys.exit(salida)
