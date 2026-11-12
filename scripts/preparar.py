"""Comprobaciones y copias seguras para instalar y actualizar (v1.2).

Solo usa la biblioteca estándar de Python: así funciona ANTES de instalar las dependencias.
Lo llaman instalar.ps1 y actualizar.ps1, pero también se puede usar a mano:

    python scripts/preparar.py requisitos            ¿se puede instalar aquí?
    python scripts/preparar.py respaldar             copia data/ y .env a respaldos-instalacion/<fecha>/
    python scripts/preparar.py copiar-datos --desde C:\\motor-anterior
                                                      trae data/ y .env de otra instalación (actualizar)

Reglas:
- Nunca se borra ni se sobrescribe nada: si el destino ya tiene datos, se detiene.
- Las bases SQLite se copian con la API de respaldo de SQLite (copia coherente aunque haya
  un archivo -wal) y se verifican: integridad y mismo número de filas por tabla.
- Si el motor parece estar en marcha, se detiene: copiar una base en uso es arriesgado.

Código de salida: 0 = correcto, 1 = correcto con avisos, 2 = no se puede continuar.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import sqlite3
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIN_PY, MAX_PY = (3, 11), (3, 14)
OK, AVISO, CRITICO = "OK", "AVISO", "CRITICO"
_MARK = {OK: "[ OK ]", AVISO: "[AVISO]", CRITICO: "[CRÍTICO]"}


class Informe:
    def __init__(self):
        self.items: list[tuple[str, str, str]] = []

    def add(self, estado: str, texto: str, accion: str = "") -> None:
        self.items.append((estado, texto, accion))
        print(f"{_MARK[estado]:<10} {texto}")
        if accion:
            print(f"           -> {accion}")
        sys.stdout.flush()

    @property
    def code(self) -> int:
        estados = {e for e, _, _ in self.items}
        return 2 if CRITICO in estados else 1 if AVISO in estados else 0


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def engine_running(port: int) -> str | None:
    """None si el puerto está libre; 'motor' si responde el motor; 'otro' si lo usa otro programa."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return None
        except OSError:
            pass
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # nunca a través de un proxy
        with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=3) as r:
            if json.loads(r.read().decode()).get("ok") is not None:
                return "motor"
    except Exception:  # noqa: BLE001
        pass
    return "otro"


def sqlite_in_use(path: Path) -> bool:
    """True si otro proceso tiene la base bloqueada para escribir (el motor en marcha)."""
    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=rw", uri=True, timeout=1)
        try:
            con.execute("BEGIN IMMEDIATE")
            con.rollback()
            return False
        finally:
            con.close()
    except sqlite3.OperationalError:
        return True


def _sqlite_counts(con: sqlite3.Connection) -> dict[str, int]:
    names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    return {n: con.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}


def copy_sqlite(src: Path, dst: Path) -> dict[str, int]:
    """Copia coherente + verificación. Devuelve las filas por tabla."""
    s = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
        if d.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError(f"la copia de {src.name} no supera la comprobación de integridad")
        a, b = _sqlite_counts(s), _sqlite_counts(d)
        if a != b:
            raise RuntimeError(f"la copia de {src.name} no tiene las mismas filas que el original")
        return b
    finally:
        s.close()
        d.close()


def copy_tree(src: Path, dst: Path, skip_dirs: set[str] = frozenset()) -> tuple[int, int]:
    """Copia src -> dst sin sobrescribir. Devuelve (archivos, bytes)."""
    files = size = 0
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if rel.parts and rel.parts[0] in skip_dirs:
            continue
        if p.name.endswith(("-wal", "-shm", "-journal")):
            continue  # incorporados por la API de respaldo de SQLite
        target = dst / rel
        if p.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if target.exists():
            raise FileExistsError(f"ya existe {target}; no se sobrescribe")
        target.parent.mkdir(parents=True, exist_ok=True)
        if p.suffix == ".db":
            copy_sqlite(p, target)
        else:
            shutil.copy2(p, target)
            if target.stat().st_size != p.stat().st_size:
                raise RuntimeError(f"la copia de {p.name} tiene un tamaño distinto")
        files += 1
        size += target.stat().st_size
    return files, size


# --------------------------------------------------------------------------- requisitos

def cmd_requisitos(root: Path) -> int:
    inf = Informe()
    print(f"Comprobación previa de la instalación en {root}\n")
    v = sys.version_info[:2]
    if MIN_PY <= v <= MAX_PY:
        inf.add(OK, f"Python {platform.python_version()} ({platform.architecture()[0]})")
    else:
        inf.add(CRITICO, f"Python {platform.python_version()} no es compatible",
                "Instala Python 3.13 desde https://www.python.org/downloads/ (marca 'Add python.exe to PATH').")
    if platform.architecture()[0] != "64bit":
        inf.add(CRITICO, "Python de 32 bits", "Instala la versión de 64 bits de Python.")
    env = read_env(root / ".env")
    is_pg = env.get("MONITOREO_DB_URL", "").startswith("postgresql")
    if os.name == "nt" and platform.machine().upper() in ("ARM64", "AARCH64") and is_pg:
        inf.add(AVISO, "Windows ARM64 con PostgreSQL: el controlador psycopg no ofrece paquete precompilado",
                "Usa SQLite en este equipo o un Python x64 (emulado).")
    free = shutil.disk_usage(root).free
    if free < 500 * 2**20:
        inf.add(CRITICO, f"Poco espacio libre en disco: {free / 2**20:.0f} MB", "Libera al menos 1 GB.")
    elif free < 2 * 2**30:
        inf.add(AVISO, f"Espacio libre justo: {free / 2**30:.1f} GB",
                "La instalación ocupa unos 300 MB; la base y sus respaldos crecen con el uso.")
    else:
        inf.add(OK, f"Espacio libre: {free / 2**30:.1f} GB")
    probe = root / ".prueba_escritura.tmp"
    try:
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        inf.add(OK, "Se puede escribir en la carpeta")
    except OSError as e:
        inf.add(CRITICO, f"No se puede escribir en la carpeta ({e.__class__.__name__})",
                "Mueve la carpeta a un lugar tuyo, por ejemplo C:\\motor-monitoreo (no dentro de Archivos de programa).")
    if "onedrive" in str(root).lower():
        inf.add(AVISO, "La carpeta está dentro de OneDrive",
                "OneDrive puede bloquear o dañar la base SQLite al sincronizarla. Recomendado: C:\\motor-monitoreo")
    if len(str(root)) > 120:
        inf.add(AVISO, f"La ruta de la carpeta es larga ({len(str(root))} caracteres)",
                "Windows limita las rutas a 260 caracteres y las dependencias crean rutas profundas. Usa una ruta corta.")
    port = int(env.get("MONITOREO_PUERTO") or os.environ.get("MONITOREO_PUERTO") or 8000)
    state = engine_running(port)
    if state == "motor":
        inf.add(CRITICO, f"El motor está en marcha en el puerto {port}",
                "Ciérralo (Ctrl+C en la ventana de iniciar.bat) y vuelve a ejecutar.")
    elif state == "otro":
        inf.add(AVISO, f"El puerto {port} lo usa otro programa",
                "Cambia MONITOREO_PUERTO en .env (por ejemplo 8010) o cierra ese programa.")
    else:
        inf.add(OK, f"Puerto {port} libre")
    db = root / "data" / "monitoreo.db"
    if db.exists():
        inf.add(OK if not sqlite_in_use(db) else CRITICO,
                f"Base existente data/monitoreo.db ({db.stat().st_size / 2**20:.1f} MB): se conserva y se respalda antes de migrar"
                if not sqlite_in_use(db) else "La base data/monitoreo.db está en uso por otro proceso",
                "" if not sqlite_in_use(db) else "Cierra el motor antes de continuar.")
    if (root / ".env").exists():
        inf.add(OK, "Ya existe .env: se conserva tal cual")
    print()
    print({0: "Se puede continuar.", 1: "Se puede continuar; revisa los avisos.",
           2: "NO se puede continuar hasta resolver lo marcado como CRÍTICO."}[inf.code])
    return inf.code


# --------------------------------------------------------------------------- respaldar

def cmd_respaldar(root: Path) -> int:
    """Copia data/ (sin logs ni respaldos anteriores) y .env antes de reinstalar en la misma carpeta."""
    data = root / "data"
    has_data = data.exists() and any(p.is_file() and p.name != ".gitkeep" for p in data.rglob("*"))
    if not has_data and not (root / ".env").exists():
        print("No hay datos que respaldar (instalación nueva).")
        return 0
    db = data / "monitoreo.db"
    if db.exists() and sqlite_in_use(db):
        print("ERROR: la base está en uso. Cierra el motor (Ctrl+C en la ventana de iniciar.bat) y repite.")
        return 2
    dest = root / "respaldos-instalacion" / datetime.now().strftime("%Y%m%d-%H%M%S")
    dest.mkdir(parents=True)
    try:
        n, size = copy_tree(data, dest / "data", skip_dirs={"logs", "respaldos"}) if data.exists() else (0, 0)
        if (root / ".env").exists():
            shutil.copy2(root / ".env", dest / ".env")
            n += 1
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: el respaldo falló ({e}). No se ha cambiado nada; no continúes.")
        return 2
    print(f"Respaldo verificado: {n} archivo(s), {size / 2**20:.1f} MB en {dest}")
    print("Para volver atrás: cierra el motor y copia el contenido de esa carpeta sobre esta.")
    return 0


# --------------------------------------------------------------------------- copiar-datos

def cmd_copiar_datos(root: Path, desde: Path) -> int:
    desde = desde.resolve()
    inf = Informe()
    print(f"Traer datos de {desde}\n      a       {root}\n")
    if desde == root:
        inf.add(CRITICO, "La carpeta anterior es la misma que la nueva",
                "Descomprime la nueva versión en OTRA carpeta, o usa instalar.ps1 sobre esta.")
        return inf.code
    if not (desde / "monitoreo").is_dir() or not ((desde / "data").exists() or (desde / ".env").exists()):
        inf.add(CRITICO, "Esa carpeta no parece una instalación del motor (falta monitoreo\\, data\\ o .env)",
                "Indica la carpeta donde estaba instalado (la que tiene iniciar.bat y la carpeta data).")
        return inf.code
    existing = [p for p in (root / "data").rglob("*") if p.is_file() and p.name != ".gitkeep"] if (root / "data").exists() else []
    if existing or (root / ".env").exists():
        inf.add(CRITICO, "La carpeta nueva ya tiene datos o .env: no se sobrescriben",
                "Usa una carpeta nueva recién descomprimida, o mueve a otro sitio su .env y su carpeta data.")
        return inf.code
    env = read_env(desde / ".env")
    url = env.get("MONITOREO_DB_URL", "")
    if url.startswith("sqlite") and url.replace("sqlite:///", "") and desde.as_posix().lower() in url.lower():
        inf.add(CRITICO, "El .env anterior apunta a una base SQLite con ruta fija dentro de la carpeta anterior",
                "Quita la línea MONITOREO_DB_URL del .env anterior (se usará data\\monitoreo.db) y repite.")
        return inf.code
    port = int(env.get("MONITOREO_PUERTO") or 8000)
    if engine_running(port) == "motor":
        inf.add(CRITICO, f"El motor está en marcha (puerto {port})",
                "Ciérralo (Ctrl+C en la ventana de iniciar.bat de la instalación anterior) y repite.")
        return inf.code
    old_db = desde / "data" / "monitoreo.db"
    if old_db.exists() and sqlite_in_use(old_db):
        inf.add(CRITICO, "La base anterior está en uso por otro proceso", "Cierra el motor anterior y repite.")
        return inf.code
    try:
        n, size = copy_tree(desde / "data", root / "data") if (desde / "data").exists() else (0, 0)
        if (desde / ".env").exists():
            shutil.copy2(desde / ".env", root / ".env")
            n += 1
    except Exception as e:  # noqa: BLE001
        inf.add(CRITICO, f"La copia falló: {e}",
                "La instalación anterior NO se ha tocado. Borra la carpeta nueva, descomprime de nuevo y repite.")
        return inf.code
    (root / "data").mkdir(exist_ok=True)
    (root / "data" / "COPIADO_DESDE.txt").write_text(
        f"Datos copiados el {datetime.now():%Y-%m-%d %H:%M:%S} desde {desde}\n", encoding="utf-8")
    inf.add(OK, f"Copiados y verificados {n} archivo(s), {size / 2**20:.1f} MB")
    if url.startswith("postgresql"):
        inf.add(AVISO, "La base es PostgreSQL: es compartida por ambas carpetas",
                "La migración hará un respaldo con pg_dump antes de cambiar el esquema.")
    inf.add(OK, f"La instalación anterior en {desde} no se ha modificado (sirve para volver atrás)")
    return inf.code


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="preparar.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("requisitos")
    sub.add_parser("respaldar")
    c = sub.add_parser("copiar-datos")
    c.add_argument("--desde", required=True, type=Path)
    ap.add_argument("--carpeta", type=Path, default=ROOT, help=argparse.SUPPRESS)  # para las pruebas
    a = ap.parse_args(argv)
    root = a.carpeta.resolve()
    if a.cmd == "requisitos":
        return cmd_requisitos(root)
    if a.cmd == "respaldar":
        return cmd_respaldar(root)
    return cmd_copiar_datos(root, a.desde)


if __name__ == "__main__":
    try:
        # Se ejecuta con -I (modo aislado), que ignora PYTHONIOENCODING. Con la salida redirigida
        # (instalar.ps1 la pasa por PowerShell, que la lee en UTF-8) Windows usaría cp1252 y los
        # acentos se verían dañados ("atr�s"): visto en la CI de Windows.
        if os.name == "nt" and not sys.stdout.isatty():
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        else:
            sys.stdout.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main(sys.argv[1:]))
