"""Proceso de PRUEBA que simula un conector con hijos difíciles. Lee un JSON por stdin: {"modo": ..., "pids": "<carpeta>"}.
Cada proceso escribe su pid en <carpeta>/<rol>_<pid>.pid para que la prueba compruebe que murió."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

cfg = json.loads(sys.stdin.read() or "{}")
modo, carpeta = cfg.get("modo", "normal"), Path(cfg.get("pids", "."))
rol = cfg.get("rol", "padre")
WIN = sys.platform == "win32"


def registrar():
    (carpeta / f"{rol}_{os.getpid()}.pid").write_text(str(os.getpid()))


def ignorar_terminacion():
    for nombre in ("SIGTERM", "SIGBREAK", "SIGINT", "SIGHUP"):
        if hasattr(signal, nombre):
            try:
                signal.signal(getattr(signal, nombre), signal.SIG_IGN)
            except (OSError, ValueError):
                pass


def hijo(rol_hijo, extra=None, **kw):
    c = {"modo": "dormir", "pids": str(carpeta), "rol": rol_hijo, **(extra or {})}
    p = subprocess.Popen([sys.executable, __file__], stdin=subprocess.PIPE, **kw)
    p.stdin.write(json.dumps(c).encode())
    p.stdin.close()
    return p


registrar()
if modo == "normal":
    print("listo")
    sys.exit(0)
if modo == "dormir":
    if cfg.get("resistente"):
        ignorar_terminacion()
    time.sleep(120)
elif modo == "ignora_sigterm":
    ignorar_terminacion()
    print("padre listo", flush=True)
    time.sleep(120)
elif modo == "hijos_resistentes":
    ignorar_terminacion()
    for i in range(3):
        hijo(f"hijo{i}", {"resistente": True, "modo": "nieto"})
    print("arbol listo", flush=True)
    time.sleep(120)
elif modo == "nieto":               # hijo resistente que a su vez crea un nieto resistente
    ignorar_terminacion()
    hijo("nieto", {"resistente": True})
    time.sleep(120)
elif modo == "desertor":            # el hijo se separa del grupo/sesión y sobrevive al padre si nadie lo busca
    kw = {"creationflags": 0x00000200 | 0x00000008} if WIN else {"start_new_session": True}
    hijo("desertor", {"resistente": True}, **kw)
    print("desertor listo", flush=True)
    time.sleep(0.3)
    os._exit(0)                     # el padre termina SOLO: el desertor queda huérfano
elif modo == "spawner":             # mientras viva, sigue lanzando hijos (hasta 40)
    ignorar_terminacion()
    n = 0
    while n < 40:
        hijo(f"spawn{n}", {"resistente": True})
        n += 1
        time.sleep(0.05)
    time.sleep(120)
