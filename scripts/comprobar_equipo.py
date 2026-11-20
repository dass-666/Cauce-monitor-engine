"""Comprobar el equipo antes de instalar o abrir Cauce (SOLO LECTURA).

Responde a una pregunta: «¿puedo instalar y abrir Cauce en esta carpeta?». No instala, no descarga, no
borra y no cambia ningún dato ni configuración. Lo único que escribe es este mismo informe en
`resultados/comprobacion_equipo.txt` (para poder compartirlo si algo falla); con `--sin-informe` ni eso.

Comprueba:
  - qué rama y commit hay en la carpeta;
  - Python (3.11 a 3.14, 64 bits) y, si ya existe, el entorno .venv con sus dependencias;
  - Node.js y npm (los usa el observador del calendario);
  - Playwright y Chromium (con `--probar`, además abre Chromium sin ventana en about:blank y lo cierra);
  - que el equipo puede abrir puertos locales en 127.0.0.1 y si el 8000 está ocupado;
  - permisos de escritura, espacio libre, OneDrive y longitud de la ruta;
  - que producción y la copia externa del diario siguen desactivadas en .env;
  - qué datos de laboratorio existen ya (se conservan).

Uso:
  python scripts/comprobar_equipo.py              (todo; sin abrir Chromium)
  python scripts/comprobar_equipo.py --probar     (además arranca Chromium sin ventana)
  python scripts/comprobar_equipo.py --antes      (antes de instalar: lo que falte de .venv no es un fallo)

Funciona con la biblioteca estándar (sirve antes de instalar nada). Código de salida: 0 = listo,
1 = se puede seguir pero hay avisos o falta un paso de la instalación, 2 = hay algo que impide seguir.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))   # con -I no se añade solo
from preparar import AVISO, CRITICO, OK, Informe, engine_running, read_env  # noqa: E402

MIN_NODE = 18
DEPENDENCIAS = ["fastapi", "uvicorn", "sqlalchemy", "httpx", "pydantic"]


def _venv_python() -> Path:
    return ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _run(cmd: list[str], timeout: int = 60, env: dict | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout, env=env,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return 127, f"{e.__class__.__name__}: {e}"


def _git() -> tuple[str, str]:
    """Rama y commit leyendo .git directamente (no necesita tener git instalado)."""
    g = ROOT / ".git"
    try:
        head = (g / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            ref = head[5:]
            rama = ref.rsplit("refs/heads/", 1)[-1]
            f = g / ref
            if f.exists():
                return rama, f.read_text(encoding="utf-8").strip()[:7]
            packed = g / "packed-refs"
            if packed.exists():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    if line.endswith(" " + ref):
                        return rama, line.split(" ", 1)[0][:7]
            return rama, "?"
        return "(sin rama: commit suelto)", head[:7]
    except OSError:
        return "", ""


def comprobar(a: argparse.Namespace, inf: Informe) -> None:
    print(f"Comprobación del equipo para Cauce · {datetime.now():%Y-%m-%d %H:%M} · solo lectura\n")
    print(f"Carpeta: {ROOT}")
    rama, commit = _git()
    if rama:
        inf.add(OK, f"Rama {rama} · commit {commit}")
    else:
        inf.add(AVISO, "La carpeta no es un clon de git (¿descargada como ZIP?)",
                "Anota de qué rama descargaste el ZIP para indicarlo si compartes este informe.")
    version = ROOT / "VERSION"
    if version.exists():
        print(f"           versión declarada: {version.read_text(encoding='utf-8').strip()}")

    # --- Python
    v = sys.version_info[:2]
    if (3, 11) <= v <= (3, 14) and platform.architecture()[0] == "64bit":
        inf.add(OK, f"Python {platform.python_version()} de 64 bits ({sys.executable})")
    else:
        inf.add(CRITICO, f"Python {platform.python_version()} ({platform.architecture()[0]}) no es compatible",
                "Instala Python 3.13 de 64 bits desde https://www.python.org/downloads/ "
                "(marca 'Add python.exe to PATH').")

    vpy = _venv_python()
    venv_ok = False
    if vpy.exists():
        code, out = _run([str(vpy), "-c", "import importlib.util,sys;"
                          f"f=[m for m in {DEPENDENCIAS!r} if importlib.util.find_spec(m) is None];"
                          "print('%d.%d' % sys.version_info[:2], ','.join(f))"])
        partes = out.strip().split(" ", 1) if code == 0 else []
        if code != 0:
            inf.add(CRITICO, "El entorno .venv existe pero no arranca",
                    "Vuelve a ejecutar instalar_cauce.bat (no borra datos).")
        elif len(partes) > 1 and partes[1]:
            inf.add(AVISO, f"Al entorno .venv le faltan dependencias: {partes[1]}",
                    "Ejecuta instalar_cauce.bat para completarlas.")
        else:
            venv_ok = True
            inf.add(OK, f"Entorno .venv preparado (Python {partes[0] if partes else '?'}) con sus dependencias")
    else:
        inf.add(AVISO if not a.antes else OK,
                "Todavía no hay entorno .venv" + (" (lo crea el instalador)" if a.antes else ""),
                "" if a.antes else "Ejecuta instalar_cauce.bat.")

    # --- Node.js / npm
    node = shutil.which("node")
    if not node:
        inf.add(CRITICO, "No se encontró Node.js",
                "Instala la versión LTS desde https://nodejs.org (siguiente, siguiente) y abre una ventana nueva.")
    else:
        code, out = _run([node, "--version"], 20)
        txt = out.strip()
        try:
            mayor = int(txt.lstrip("v").split(".", 1)[0])
        except ValueError:
            mayor = 0
        if code == 0 and mayor >= MIN_NODE:
            inf.add(OK, f"Node.js {txt}")
        else:
            inf.add(CRITICO, f"Node.js {txt or '?'} es antiguo o no responde",
                    f"Instala Node.js LTS ({MIN_NODE} o superior) desde https://nodejs.org.")
        if not (shutil.which("npm") or shutil.which("npm.cmd")):
            inf.add(CRITICO, "Node.js está, pero falta npm", "Reinstala Node.js LTS con las opciones por defecto.")

    # --- Playwright / Chromium (con el mismo módulo que usa el motor; necesita el .venv)
    if venv_ok:
        cmd = [str(vpy), "-m", "monitoreo.navegador", "--json"] + (["--probar"] if a.probar else [])
        code, out = _run(cmd, 120)
        try:
            r = json.loads(out[out.index("{"):])
        except ValueError:
            r = None
        if r and r.get("listo"):
            inf.add(OK, f"Playwright {r.get('playwright')} y Chromium listos"
                    + (" (probado sin ventana)" if a.probar else "") + f" · {r.get('ruta')}")
        elif r:
            inf.add(AVISO if a.antes else CRITICO, "El navegador del observador no está listo: "
                    + "; ".join(r.get("problemas") or ["sin detalle"]),
                    "Ejecuta instalar_cauce.bat (instala Playwright y Chromium dentro de la carpeta, en .browsers).")
        else:
            inf.add(AVISO, "No se pudo consultar el estado del navegador", out.strip()[-300:])
    elif not a.antes:
        inf.add(AVISO, "Chromium sin comprobar: antes hay que preparar .venv", "Ejecuta instalar_cauce.bat.")

    # --- Puertos
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            puerto = s.getsockname()[1]
            with socket.create_connection(("127.0.0.1", puerto), timeout=3):
                pass
        inf.add(OK, "Se pueden abrir puertos locales en 127.0.0.1 (el laboratorio elige puertos libres solo)")
    except OSError as e:
        inf.add(CRITICO, f"No se puede abrir un puerto local en 127.0.0.1 ({e.__class__.__name__})",
                "Un antivirus o cortafuegos bloquea conexiones locales: permite Python en 127.0.0.1.")
    env = read_env(ROOT / ".env")
    port = int(env.get("MONITOREO_PUERTO") or 8000)
    estado = engine_running(port)
    if estado is None:
        inf.add(OK, f"Puerto {port} libre (lo usa iniciar.bat, no el laboratorio)")
    elif estado == "motor":
        inf.add(AVISO, f"Ya hay un motor Cauce en marcha en el puerto {port}",
                "No impide abrir el laboratorio (usa otros puertos), pero ciérralo antes de instalar o actualizar.")
    else:
        inf.add(AVISO, f"El puerto {port} lo usa otro programa",
                "No afecta al laboratorio. Para iniciar.bat, cambia MONITOREO_PUERTO en .env o cierra ese programa.")

    # --- Permisos y disco
    if os.access(ROOT, os.W_OK):
        inf.add(OK, "La carpeta admite escritura")
    else:
        inf.add(CRITICO, "No tienes permiso de escritura en esta carpeta",
                "Usa una carpeta tuya, por ejemplo C:\\cauce-msi (no dentro de Archivos de programa).")
    free = shutil.disk_usage(ROOT).free
    inf.add(OK if free >= 2 * 2**30 else AVISO if free >= 500 * 2**20 else CRITICO,
            f"Espacio libre: {free / 2**30:.1f} GB", "" if free >= 2 * 2**30 else "Libera espacio (Chromium ocupa ~300 MB).")
    if "onedrive" in str(ROOT).lower():
        inf.add(AVISO, "La carpeta está dentro de OneDrive", "Recomendado: C:\\cauce-msi, fuera de OneDrive.")
    if len(str(ROOT)) > 120:
        inf.add(AVISO, f"Ruta larga ({len(str(ROOT))} caracteres)", "Usa una ruta corta como C:\\cauce-msi.")

    # --- Seguridad de la configuración (solo se lee .env)
    if (ROOT / ".env").exists():
        entorno = (env.get("MONITOREO_ENTORNO") or "desarrollo").lower()
        flujo = env.get("MONITOREO_PRODUCCION_FLUJO_ADMIN", "").strip()
        copia = env.get("MONITOREO_DIARIO_COPIA", "").strip()
        if entorno == "produccion" or flujo not in ("", "0"):
            inf.add(AVISO, f"Esta carpeta está configurada para PRODUCCIÓN (entorno={entorno}, flujo={flujo or 'no'})",
                    "Para la validación usa una carpeta nueva; esta no es una instalación de laboratorio.")
        else:
            inf.add(OK, "Producción desactivada en .env")
        inf.add(OK if not copia else AVISO,
                "Copia externa del diario desactivada (lo previsto en esta validación)" if not copia
                else f"Copia externa del diario ACTIVADA en {copia}",
                "" if not copia else "En esta validación no debe activarse; ver docs/RESPALDO_EXTERNO_DIARIO.md.")
    else:
        inf.add(OK if a.antes else AVISO, "Aún no hay .env" + (" (lo crea el instalador)" if a.antes else ""),
                "" if a.antes else "Ejecuta instalar_cauce.bat.")

    # --- Datos existentes (solo se informan; nunca se tocan)
    lab = ROOT / "resultados" / "cauce_rc1"
    if (lab / "cauce_lab.db").exists():
        inf.add(OK, f"Hay una sesión de laboratorio anterior en {lab.relative_to(ROOT)} (se conserva; "
                    "para empezar de cero: cauce_rc1.bat --nuevo)")
    if (ROOT / "data" / "monitoreo.db").exists():
        inf.add(OK, "Hay una base data\\monitoreo.db: el laboratorio no la usa ni la modifica")


def main() -> int:
    for flujo in (sys.stdout, sys.stderr):   # consolas de Windows sin UTF-8: no fallar por un acento
        try:
            flujo.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--probar", action="store_true", help="arrancar Chromium sin ventana para comprobarlo")
    ap.add_argument("--antes", action="store_true", help="antes de instalar: lo que crea el instalador no es fallo")
    ap.add_argument("--sin-informe", action="store_true", help="no guardar resultados/comprobacion_equipo.txt")
    a = ap.parse_args()

    copia = io.StringIO()
    real = sys.stdout

    class _Tee:
        def write(self, s):
            copia.write(s)
            return real.write(s)

        def flush(self):
            real.flush()

    sys.stdout = _Tee()
    inf = Informe()
    try:
        comprobar(a, inf)
        print()
        print({0: "RESULTADO: equipo listo.",
               1: "RESULTADO: se puede seguir; revisa los avisos.",
               2: "RESULTADO: hay algo que impide seguir (marcado como CRÍTICO)."}[inf.code])
    finally:
        sys.stdout = real
    if not a.sin_informe:
        try:
            dest = ROOT / "resultados" / "comprobacion_equipo.txt"
            dest.parent.mkdir(exist_ok=True)
            dest.write_text(copia.getvalue(), encoding="utf-8")
            print(f"Informe guardado en {dest}")
        except OSError as e:
            print(f"(No se pudo guardar el informe: {e.__class__.__name__})")
    return inf.code


if __name__ == "__main__":
    raise SystemExit(main())
