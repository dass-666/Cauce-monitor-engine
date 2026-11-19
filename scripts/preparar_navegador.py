"""Prepara (una sola vez) el navegador que usan el observador y los recorridos: Playwright + Chromium.

Cierre v2.1. Deja el navegador DENTRO de la instalación, en `.browsers/`, que el motor encuentra
solo en cada arranque (`monitoreo/navegador.py`): no hace falta exportar PLAYWRIGHT_BROWSERS_PATH.
Si en el entorno o en `.env` ya está definida PLAYWRIGHT_BROWSERS_PATH, instala ahí y la respeta.

Uso (desde la carpeta de la instalación, Linux o Windows):

    python scripts/preparar_navegador.py               instala lo que falte y prueba un arranque real
    python scripts/preparar_navegador.py --comprobar   solo diagnostica (no descarga nada)
    python scripts/preparar_navegador.py --dependencias-linux
                                                       además instala las bibliotecas del sistema que
                                                       Chromium necesita (apt; requiere root)

Descarga desde el registro de npm y desde el servidor de descargas de Playwright (unos 150 MB).
No toca ningún otro proyecto: todo queda en `node_modules/` y `.browsers/` de esta carpeta.
Código de salida: 0 = listo, 1 = algo falla (se explica qué), 3 = falta Node.js/npm.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from monitoreo import navegador  # noqa: E402  (carga .env)

VERSION = "1.63.0"        # la misma que fija la integración continua


def _npm() -> str | None:
    return shutil.which("npm.cmd" if os.name == "nt" else "npm") or shutil.which("npm")


def _npx() -> str | None:
    return shutil.which("npx.cmd" if os.name == "nt" else "npx") or shutil.which("npx")


def _en_env() -> bool:
    try:
        lineas = (ROOT / ".env").read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    return any(x.strip().startswith(navegador.VARIABLE + "=") for x in lineas)


def _imprimir(r: dict) -> None:
    print(f"  Node.js: {r['node'] or 'NO'} · playwright: {r['playwright'] or 'NO'}")
    print(f"  carpeta de navegadores: {r['ruta'] or '(node_modules)'} [{r['origen']}]")
    for p in r["problemas"]:
        print("  PROBLEMA: " + p)


def main() -> int:
    for flujo in (sys.stdout, sys.stderr):   # consolas de Windows sin UTF-8: no fallar por un acento
        try:
            flujo.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--comprobar", action="store_true", help="solo diagnosticar")
    ap.add_argument("--dependencias-linux", action="store_true", help="instalar también las bibliotecas del sistema")
    a = ap.parse_args()

    if a.comprobar:
        r = navegador.diagnosticar(probar=True)
        _imprimir(r)
        print("LISTO" if r["listo"] else "FALTA ALGO")
        return 0 if r["listo"] else 1

    npm, npx = _npm(), _npx()
    if not shutil.which("node") or not npm or not npx:
        print("Falta Node.js (con npm). Instala la versión LTS desde https://nodejs.org y vuelve a ejecutar.")
        return 3
    env = navegador.entorno()
    definida = (env.get(navegador.VARIABLE) or "").strip()
    if not definida:
        env[navegador.VARIABLE] = str(navegador.carpeta_local())
    destino = env[navegador.VARIABLE]
    print(f"Carpeta de navegadores: {destino}" + ("  (definida por PLAYWRIGHT_BROWSERS_PATH)" if definida else
                                                   "  (.browsers de esta instalación)"))
    env.pop("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", None)

    previo = navegador.diagnosticar()
    if previo["playwright"] != VERSION:
        print(f"Instalando playwright@{VERSION} en {ROOT / 'node_modules'} …")
        p = subprocess.run([npm, "install", "--no-save", "--no-audit", "--no-fund", f"playwright@{VERSION}"], cwd=str(ROOT),
                           env=env)
        if p.returncode:
            print("No se pudo instalar playwright con npm (¿sin internet o sin permisos?).")
            return 1
    orden = [npx, "playwright", "install"] + (["--with-deps"] if a.dependencias_linux and os.name != "nt" else []) + ["chromium"]
    print("Instalando Chromium (y su versión sin ventana) …")
    p = subprocess.run(orden, cwd=str(ROOT), env=env)
    if p.returncode:
        print("No se pudo descargar Chromium (¿sin internet o sin permisos?).")
        return 1

    # La comprobación final usa el entorno que tendrá el motor (sin la variable añadida aquí):
    # así se demuestra que la encontrará solo en el próximo arranque.
    r = navegador.diagnosticar(probar=True)
    _imprimir(r)
    if r["listo"] and definida and not _en_env():
        print(f"AVISO: {navegador.VARIABLE} viene solo de esta consola. Para que el motor la tenga en cada arranque,\n"
              f"       añade a .env la línea  {navegador.VARIABLE}={destino}")
        return 0
    if r["listo"]:
        print("LISTO: el motor encontrará el navegador en cada arranque sin exportar nada.")
        return 0
    print("FALTA ALGO: revisa los problemas de arriba.")
    if os.name != "nt" and not a.dependencias_linux:
        print("En Linux suele faltar alguna biblioteca del sistema: repite con --dependencias-linux (como root).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
