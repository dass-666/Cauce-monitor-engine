"""Sonda de aislamiento: se ejecuta COMO la cuenta restringida (la lanza scripts/aislamiento_windows_lanzador.py).

Solo usa la biblioteca estándar y no escribe fuera de su carpeta de trabajo. Imprime UN JSON en stdout.
Modos:
  pruebas <raiz> <puerto_local>  A1-A9 + barrido de todo <raiz> + eco de stdin + identidad del token
  arbol                          lanza un hijo y un nieto que duermen (para comprobar que el Job no deja huérfanos)
  desertor                       el padre sale enseguida y deja un hijo vivo
  dormir                         duerme sin fin (para comprobar plazo y cancelación)
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


def usuario_del_token() -> str:
    if sys.platform != "win32":
        import getpass
        return getpass.getuser()
    import ctypes
    b = ctypes.create_unicode_buffer(256)
    n = ctypes.c_ulong(256)
    ctypes.windll.advapi32.GetUserNameW(b, ctypes.byref(n))   # identidad real del token, no variables de entorno
    return b.value


def prueba(nombre, fn, debe_fallar):
    try:
        fn()
        ok, obs = not debe_fallar, "permitido"
    except PermissionError:
        ok, obs = debe_fallar, "PermissionError"
    except Exception as e:  # noqa: BLE001
        ok, obs = False, type(e).__name__
    return {"prueba": nombre, "esperado": "PermissionError" if debe_fallar else "permitido", "observado": obs, "ok": ok}


def red(nombre, destino, debe_fallar):
    try:
        socket.create_connection(destino, timeout=5).close()
        ok, obs = not debe_fallar, "conectado"
    except OSError as e:
        ok, obs = debe_fallar, f"{type(e).__name__}"
    return {"prueba": nombre, "esperado": "sin conexión" if debe_fallar else "conectado", "observado": obs, "ok": ok}


def barrido(raiz: Path) -> dict:
    """Recorre TODO <raiz> e intenta leer cada archivo. Lo legible fuera de conector/ es una fuga."""
    permitidas = (raiz / "conector",)
    legibles_fuera, carpetas_listables, archivos_vistos = [], [], 0
    for actual, dirs, files in os.walk(raiz, onerror=lambda e: None):
        p = Path(actual)
        if not any(p == q or q in p.parents for q in permitidas) and p != raiz:
            carpetas_listables.append(str(p.relative_to(raiz)))
        for f in files:
            ruta = p / f
            archivos_vistos += 1
            try:
                with open(ruta, "rb") as fh:
                    fh.read(1)
            except OSError:
                continue
            if not any(q in ruta.parents for q in permitidas):
                legibles_fuera.append(str(ruta.relative_to(raiz)))
    return {"legibles_fuera_de_conector": legibles_fuera, "carpetas_listables_fuera": carpetas_listables,
            "archivos_vistos": archivos_vistos}


def modo_pruebas(raiz: Path, puerto: int) -> dict:
    trabajo = raiz / "conector" / "trabajo"
    codigo = raiz / "conector" / "codigo"

    def escribe(p):
        Path(p).write_text("x")
    r = [
        prueba("A1 leer data/monitoreo.db", lambda: (raiz / "data" / "monitoreo.db").read_bytes(), True),
        prueba("A2 listar data", lambda: list((raiz / "data").iterdir()), True),
        prueba("A2 escribir en data", lambda: escribe(raiz / "data" / "nuevo.txt"), True),
        prueba("A3 leer .env", lambda: (raiz / ".env").read_text(), True),
        prueba("A4 listar respaldos", lambda: list((raiz / "respaldos").iterdir()), True),
        prueba("A4 leer respaldos/copia.db", lambda: (raiz / "respaldos" / "copia.db").read_bytes(), True),
        prueba("A4b leer la clave del diario", lambda: (raiz / "diario" / "clave_diario.key").read_bytes(), True),
        prueba("A5 escribir en el código del conector", lambda: escribe(codigo / "x.txt"), True),
        prueba("A5 modificar la sonda (código)", lambda: (codigo / "aislamiento_sonda.py").write_text("x"), True),
        prueba("A5 crear archivos en la raíz", lambda: escribe(raiz / "nuevo.txt"), True),
        prueba("A6 leer el código del conector", lambda: (codigo / "leeme.txt").read_text(), False),
        prueba("A7 escribir en su carpeta de trabajo", lambda: escribe(trabajo / "salida.txt"), False),
        prueba("A7 leer lo que escribió", lambda: (trabajo / "salida.txt").read_text(), False),
        red("A9 conexión a 127.0.0.1 (portal de laboratorio)", ("127.0.0.1", puerto), False),
        red("A9 conexión a una IP pública (1.1.1.1:443)", ("1.1.1.1", 443), True),
    ]
    entrada = sys.stdin.buffer.read()
    return {"usuario": usuario_del_token(), "pruebas": r, "barrido": barrido(raiz),
            "stdin_sha256": hashlib.sha256(entrada).hexdigest(), "temp": os.environ.get("TEMP", "")}


def main() -> int:
    modo = sys.argv[1] if len(sys.argv) > 1 else "pruebas"
    if modo == "pruebas":
        print(json.dumps(modo_pruebas(Path(sys.argv[2]), int(sys.argv[3]))))
        return 0
    if modo == "arbol":
        nieto = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(600)']); time.sleep(600)"
        subprocess.Popen([sys.executable, "-c", nieto])
        print(json.dumps({"usuario": usuario_del_token(), "arbol": "lanzado"}), flush=True)
        time.sleep(600)
        return 0
    if modo == "desertor":       # el padre termina enseguida y deja un hijo vivo: el Job debe eliminarlo
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        print(json.dumps({"usuario": usuario_del_token(), "desertor": "lanzado"}), flush=True)
        return 0
    if modo == "dormir":
        time.sleep(600)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
