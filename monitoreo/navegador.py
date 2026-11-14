"""Dónde está el Chromium de Playwright (cierre v2.1).

Defecto que motivó este módulo (validación de Diego en su servidor Linux): Playwright buscaba el
navegador en su carpeta por omisión (`~/.cache/ms-playwright`) pero se había instalado en
`<instalación>/.browsers`. El observador no arrancaba, el motor no lo explicaba y el recorrido solo
pasó al exportar a mano `PLAYWRIGHT_BROWSERS_PATH`.

Regla de búsqueda, en orden (la primera que exista gana):

1. `PLAYWRIGHT_BROWSERS_PATH` en el entorno del proceso **o en `.env`** (la configuración del motor
   carga `.env` en el entorno al importarse, sin pisar lo ya definido).
2. La carpeta `.browsers` dentro de la instalación (la que usa `scripts/preparar_navegador.py`).
3. La carpeta por omisión de Playwright (`~/.cache/ms-playwright` en Linux,
   `%LOCALAPPDATA%\\ms-playwright` en Windows).

`entorno()` devuelve el entorno para lanzar Node con esa ruta ya resuelta; lo usan el observador y
los scripts de recorrido. Así basta instalar el navegador una vez con `preparar_navegador.py` (o
fijar la variable en `.env`) para que funcione en cada arranque, sin exportar nada a mano.

Diagnóstico en consola (no abre ningún navegador ni se conecta a nada):

    python -m monitoreo.navegador          (texto; código 0 = listo, 1 = falta algo)
    python -m monitoreo.navegador --json
    python -m monitoreo.navegador --probar  (además arranca Chromium sin ventana en about:blank y lo cierra)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import config  # noqa: F401 - carga .env en el entorno (PLAYWRIGHT_BROWSERS_PATH incluido)
from .texto_seguro import sanitizar

ROOT = Path(__file__).resolve().parents[1]
VARIABLE = "PLAYWRIGHT_BROWSERS_PATH"


def carpeta_local() -> Path:
    return ROOT / ".browsers"


def carpeta_por_omision() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "ms-playwright"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "ms-playwright"


def _tiene_chromium(p: Path) -> bool:
    try:
        return p.is_dir() and any(x.is_dir() and x.name.startswith(("chromium-", "chromium_headless_shell-"))
                                  for x in p.iterdir())
    except OSError:
        return False


def resolver(env: dict | None = None) -> dict:
    """Qué carpeta de navegadores usará Playwright y por qué. No ejecuta nada."""
    env = os.environ if env is None else env
    valor = (env.get(VARIABLE) or "").strip()
    if valor:
        if valor == "0":       # convención de Playwright: navegadores dentro de node_modules
            return {"ruta": None, "origen": f"{VARIABLE}=0 (dentro de node_modules)", "chromium": None}
        p = Path(valor).expanduser()
        if not p.is_absolute():
            p = (ROOT / p).resolve()
        origen = ("carpeta .browsers de la instalación" if p == carpeta_local()
                  else f"variable {VARIABLE} (entorno o .env)")
        return {"ruta": str(p), "origen": origen, "chromium": _tiene_chromium(p)}
    local = carpeta_local()
    if _tiene_chromium(local):
        return {"ruta": str(local), "origen": "carpeta .browsers de la instalación", "chromium": True}
    omision = carpeta_por_omision()
    return {"ruta": str(omision), "origen": "carpeta por omisión de Playwright", "chromium": _tiene_chromium(omision)}


def entorno(base: dict | None = None) -> dict:
    """Entorno para lanzar Node/Playwright: si la variable no está definida y la instalación tiene
    su propia carpeta `.browsers` con Chromium, la fija. Nunca pisa un valor ya definido."""
    env = dict(os.environ if base is None else base)
    if not (env.get(VARIABLE) or "").strip() and _tiene_chromium(carpeta_local()):
        env[VARIABLE] = str(carpeta_local())
    return env


def diagnosticar(timeout: float = 20.0, probar: bool = False) -> dict:
    """Comprueba Node, el módulo playwright y el ejecutable de Chromium que Playwright usaría.
    Sin `probar` solo pregunta a Playwright la ruta del ejecutable; con `probar` además arranca
    Chromium sin ventana sobre una página en blanco y lo cierra (no navega a ningún sitio)."""
    r = {"node": None, "playwright": None, "ejecutable": None, "existe": False, **resolver(entorno()), "problemas": []}
    node = shutil.which("node")
    if not node:
        r["problemas"].append("Node.js no está instalado o no está en el PATH")
        return _cerrar(r)
    env = entorno()
    sonda = ("let p;try{p=require('playwright')}catch(e){console.log(JSON.stringify({error:'modulo',m:String(e.message)}));"
             "process.exit(0)}console.log(JSON.stringify({version:require('playwright/package.json').version,"
             "ejecutable:p.chromium.executablePath()}))")
    try:
        v = subprocess.run([node, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        r["node"] = v.stdout.strip() or None
        s = subprocess.run([node, "-e", sonda], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=str(ROOT / "scripts"),
                           env=env)
        datos = json.loads((s.stdout.strip().splitlines() or ["{}"])[-1])
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        r["problemas"].append("no se pudo consultar a Node: " + sanitizar(f"{type(e).__name__}: {e}", 200))
        return _cerrar(r)
    if datos.get("error") == "modulo":
        r["problemas"].append("Node no encuentra el módulo «playwright»: ejecuta `python scripts/preparar_navegador.py` "
                              "en la carpeta de la instalación (" + sanitizar(datos.get("m"), 120) + ")")
        return _cerrar(r)
    r["playwright"] = datos.get("version")
    r["ejecutable"] = datos.get("ejecutable")
    r["existe"] = bool(r["ejecutable"]) and Path(r["ejecutable"]).is_file()
    # El modo sin ventana (el que usa el observador) lanza «chromium_headless_shell-<rev>», hermana
    # de la carpeta «chromium-<rev>» del ejecutable. Se comprueba también.
    r["sin_ventana"] = _sin_ventana(r["ejecutable"])
    arreglo = ("Instálalo con `python scripts/preparar_navegador.py`, o fija en .env "
               f"{VARIABLE}=<carpeta donde ya está instalado>")
    if not r["existe"]:
        r["problemas"].append(f"Playwright busca Chromium en {r['ejecutable']} y no está ({r['origen']}). " + arreglo)
    elif r["sin_ventana"] is False:
        r["problemas"].append("falta el Chromium sin ventana (chromium_headless_shell) junto a "
                              f"{Path(r['ejecutable']).parents[1]}. " + arreglo)
    if probar and not r["problemas"]:
        r["prueba"] = _probar(node, env, timeout)
        if not r["prueba"]["ok"]:
            r["problemas"].append("Chromium no arrancó: " + r["prueba"]["error"])
    return _cerrar(r)


def _sin_ventana(ejecutable: str | None) -> bool | None:
    """True/False si se puede saber; None si la estructura de carpetas no es la esperada."""
    if not ejecutable:
        return None
    for parte in Path(ejecutable).parents:
        if parte.name.startswith("chromium-"):
            hermana = parte.with_name("chromium_headless_shell-" + parte.name.split("-", 1)[1])
            try:
                return hermana.is_dir() and any(hermana.iterdir())
            except OSError:
                return False
    return None


def _probar(node: str, env: dict, timeout: float) -> dict:
    """Arranca Chromium sin ventana con una página en blanco y lo cierra (sin red)."""
    js = ("require('playwright').chromium.launch().then(async b=>{const p=await b.newPage();await p.goto('about:blank');"
          "console.log(JSON.stringify({ok:true,version:b.version()}));await b.close()})"
          ".catch(e=>{console.log(JSON.stringify({ok:false,error:String(e.message)}))})")
    try:
        s = subprocess.run([node, "-e", js], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=max(timeout, 60),
                           cwd=str(ROOT / "scripts"), env=env)
        d = json.loads((s.stdout.strip().splitlines() or ["{}"])[-1])
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        d = {"ok": False, "error": f"{type(e).__name__}"}
    if not d.get("ok"):
        d = {"ok": False, "error": sanitizar(d.get("error") or "sin respuesta", 240)}
    return d


def _cerrar(r: dict) -> dict:
    r["listo"] = not r["problemas"]
    return r


def main(argv: list[str]) -> int:
    for flujo in (sys.stdout, sys.stderr):   # consolas de Windows sin UTF-8: no fallar por un acento
        try:
            flujo.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    r = diagnosticar(probar="--probar" in argv)
    if "--json" in argv:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print("Navegador del observador (Playwright/Chromium)")
        print(f"  Node.js:        {r['node'] or 'NO'}")
        print(f"  playwright:     {r['playwright'] or 'NO'}")
        print(f"  carpeta:        {r['ruta'] or '(node_modules)'}  [{r['origen']}]")
        print(f"  ejecutable:     {r['ejecutable'] or '-'}  ({'existe' if r['existe'] else 'NO existe'})")
        if "prueba" in r:
            print("  arranque real:  " + (f"OK (Chromium {r['prueba'].get('version')})" if r["prueba"]["ok"] else "FALLÓ"))
        for p in r["problemas"]:
            print("  PROBLEMA: " + p)
        print("  Estado: " + ("LISTO" if r["listo"] else "FALTA ALGO"))
    return 0 if r["listo"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
