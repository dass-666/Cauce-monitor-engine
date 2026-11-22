"""Prepara ESTA carpeta como instalación del piloto asistido (producción + flujo administrativo, sin portal).

El piloto es una instalación normal: su configuración está en `.env`, su base en `data\\monitoreo.db` y se abre
con `iniciar.bat`. Así el motor, `respaldar_externo.bat`, `diagnostico.bat` y `migrate restaurar` usan LA MISMA
base. (`piloto_asistido.bat` es solo una demostración con datos ficticios y no sirve para el piloto.)

Qué hace:
  1. Si no hay `.env`, lo crea para producción con TU contraseña de administrador (se pide dos veces, no se
     muestra; o llega por la variable MON_PWD) y una clave secreta nueva. Si ya hay `.env`, lo conserva y guarda
     antes una copia `.env.antes-de-piloto-<fecha>`.
  2. Comprueba la carpeta externa (otra unidad que la base): crea en ella `diario\\` y `respaldos\\`.
  3. Deja en `.env`: producción, sin datos de demostración, flujo administrativo activo, consentimiento exigido,
     copia externa del diario, respaldo externo, 127.0.0.1 y el puerto indicado.
  4. Revisa la configuración con las mismas reglas que el arranque y rechaza contraseñas de laboratorio.
  5. Crea o actualiza la base (`python -m monitoreo.migrate actualizar`).
No borra nada, no toca otras carpetas y no arranca el motor.

Uso:
  python scripts/preparar_piloto.py --externo D:\\CaucePiloto [--puerto 8100]
  python scripts/preparar_piloto.py --comprobar        solo revisa la configuración actual (no cambia nada)
Código 0 = listo; 1 = no se pudo (el mensaje dice qué falta).
"""
from __future__ import annotations

import argparse
import getpass
import os
import re
import secrets
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
# Contraseñas escritas en el repositorio (laboratorio, demostraciones, CI): nunca en el piloto.
PROHIBIDAS = {"admin-laboratorio-2026", "admin-piloto-lab-2026", "operador-piloto-lab-2026", "operador-laboratorio-2026",
              "verificacion-arranque-2026"}
PILOTO = {"MONITOREO_ENTORNO": "produccion", "MONITOREO_DATOS_DEMO": "0", "MONITOREO_PRODUCCION_FLUJO_ADMIN": "1",
          "MONITOREO_EXIGIR_CONSENTIMIENTO": "1", "MONITOREO_HOST": "127.0.0.1", "MONITOREO_INICIAR_MOTOR": "1"}
# Variables que desviarían la base, el diario o la clave fuera de esta carpeta: en el piloto no se usan.
NO_PERMITIDAS = ("MONITOREO_DATA_DIR", "MONITOREO_DIARIO_DIR", "MONITOREO_DIARIO_CLAVE_ARCHIVO")


def leer_env(ruta: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not ruta.exists():
        return out
    for raw in ruta.read_text(encoding="utf-8-sig").splitlines():
        linea = raw.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, v = linea.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def poner(texto: str, clave: str, valor: str) -> str:
    pat = re.compile(rf"(?m)^#?\s*{re.escape(clave)}=.*$")
    if pat.search(texto):
        return pat.sub(lambda m: f"{clave}={valor}", texto, count=1)
    return texto.rstrip("\n") + f"\n{clave}={valor}\n"


def problemas_contrasena(pwd: str) -> list[str]:
    p = []
    if len(pwd) < 12:
        p.append("mínimo 12 caracteres")
    if pwd.isalpha() or pwd.isdigit():
        p.append("combina letras y números o símbolos")
    if pwd != pwd.strip().strip('"').strip("'"):
        p.append("no puede empezar ni terminar con espacios o comillas")
    if pwd.strip().lower() in PROHIBIDAS:
        p.append("es una contraseña de laboratorio publicada en el código: elige una propia")
    return p


def _volumen(p: Path):
    q = p
    while not q.exists() and q != q.parent:
        q = q.parent
    try:
        return os.stat(q).st_dev
    except OSError:
        return None


def revisar_externo(externo: Path, permitir_misma_unidad: bool) -> list[str]:
    problemas = []
    if not externo.is_dir():
        return [f"La carpeta externa {externo} no existe o la unidad no está conectada."]
    res, raiz = externo.resolve(), ROOT.resolve()
    if res == raiz or raiz in res.parents:
        problemas.append("La carpeta externa está dentro de la instalación: se perdería con ella.")
    if _volumen(res) == _volumen(raiz) and not permitir_misma_unidad:
        problemas.append("La carpeta externa está en la MISMA unidad que la instalación: no protege contra la pérdida "
                         "del disco. Usa otra unidad (segundo disco o USB cifrada).")
    return problemas


def revisar_configuracion(env: dict[str, str]) -> tuple[list[str], list[str]]:
    """(errores, avisos) de un `.env` para el piloto. Sin efectos."""
    sys.path.insert(0, str(ROOT))
    from monitoreo import entorno
    errores = list(entorno.review({**env}).errores)
    avisos: list[str] = []
    for k, v in PILOTO.items():
        if k == "MONITOREO_INICIAR_MOTOR":
            continue
        if (env.get(k) or "").strip().lower() != v:
            errores.append(f"{k} debe ser {v} en el piloto (ahora: {env.get(k) or 'sin definir'}).")
    pwd = env.get("MONITOREO_ADMIN_PASSWORD") or ""
    if pwd:
        errores += ["MONITOREO_ADMIN_PASSWORD: " + x for x in problemas_contrasena(pwd)]
    for k in NO_PERMITIDAS:
        if (env.get(k) or "").strip():
            errores.append(f"{k} no debe definirse en el piloto: la base, el diario y su clave viven en data\\ de "
                           "esta carpeta, que es lo que respaldan los .bat.")
    url = (env.get("MONITOREO_DB_URL") or "").strip()
    if url and not url.startswith("sqlite"):
        errores.append("El piloto usa SQLite: el respaldo externo verificado no existe para PostgreSQL.")
    elif url:
        avisos.append(f"MONITOREO_DB_URL está definida ({url}): el motor y los respaldos usarán ESA base.")
    for k in ("MONITOREO_DIARIO_COPIA", "MONITOREO_RESPALDO_EXTERNO"):
        v = (env.get(k) or "").strip()
        if not v:
            errores.append(f"Falta {k}.")
        elif not Path(v).is_dir():
            errores.append(f"{k}={v}: la carpeta no existe o la unidad no está conectada.")
    if (env.get("MONITOREO_CAPTURA_SOLO_SINTETICOS", "1") or "1").strip() == "1":
        avisos.append("MONITOREO_CAPTURA_SOLO_SINTETICOS=1: solo se aceptan datos FICTICIOS (correo .test/.example, "
                      "teléfono 555…). Es lo correcto hasta que se autoricen datos reales.")
    else:
        avisos.append("MONITOREO_CAPTURA_SOLO_SINTETICOS=0: se aceptan datos REALES. Solo con base legal, "
                      "consentimiento y medios cifrados confirmados.")
    return errores, avisos


def comprobar() -> int:
    if not ENV.exists():
        print("No hay .env en esta carpeta: ejecuta preparar_piloto.bat <carpeta externa>.")
        return 1
    env = leer_env(ENV)
    errores, avisos = revisar_configuracion(env)
    base = (env.get("MONITOREO_DB_URL") or f"sqlite:///{(ROOT / 'data' / 'monitoreo.db').as_posix()}")
    print(f"Instalación:        {ROOT}")
    print(f"Base de datos:      {base}")
    print(f"Copia del diario:   {env.get('MONITOREO_DIARIO_COPIA') or '—'}")
    print(f"Respaldo externo:   {env.get('MONITOREO_RESPALDO_EXTERNO') or '—'}")
    print(f"Dirección:          http://127.0.0.1:{env.get('MONITOREO_PUERTO', '8000')}/")
    for a in avisos:
        print("AVISO:  " + a)
    for e in errores:
        print("ERROR:  " + e)
    print("RESULTADO: " + ("configuración del piloto correcta" if not errores else f"{len(errores)} problema(s)"))
    return 0 if not errores else 1


def main() -> int:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--externo", help="carpeta en OTRA unidad para la copia del diario y los respaldos")
    ap.add_argument("--puerto", type=int, default=8100, help="puerto fijo del panel (por omisión 8100)")
    ap.add_argument("--comprobar", action="store_true", help="solo revisa la configuración actual")
    ap.add_argument("--permitir-misma-unidad", action="store_true",
                    help="SOLO pruebas automatizadas: acepta la carpeta externa en la misma unidad")
    ap.add_argument("--sin-base", action="store_true", help="no crear/actualizar la base (pruebas)")
    a = ap.parse_args()
    if a.comprobar:
        return comprobar()
    if not a.externo:
        print("Indica la carpeta externa, por ejemplo: preparar_piloto.bat D:\\CaucePiloto")
        return 1
    if not (1024 <= a.puerto <= 65535):
        print("Puerto no válido (1024 a 65535).")
        return 1
    externo = Path(a.externo).expanduser()
    problemas = revisar_externo(externo, a.permitir_misma_unidad)
    if problemas:
        print("NO SE CAMBIÓ NADA:\n  - " + "\n  - ".join(problemas))
        return 1
    creado = False
    if ENV.exists():
        actual = leer_env(ENV)
        if (actual.get("MONITOREO_ENTORNO") or "desarrollo").strip().lower() != "produccion" and (
                ROOT / "data" / "monitoreo.db").exists():
            print("NO SE CAMBIÓ NADA: esta carpeta ya tiene una instalación de LABORATORIO con base de datos "
                  "(.env sin producción y data\\monitoreo.db). El piloto va en una carpeta NUEVA e independiente.")
            return 1
        pwd = actual.get("MONITOREO_ADMIN_PASSWORD") or ""
        if pwd and problemas_contrasena(pwd):
            print("NO SE CAMBIÓ NADA: la contraseña del administrador de .env no sirve para el piloto ("
                  + ", ".join(problemas_contrasena(pwd)) + "). Cámbiala en .env (MONITOREO_ADMIN_PASSWORD) y repite.")
            return 1
        copia = ROOT / f".env.antes-de-piloto-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(ENV, copia)
        print(f"Ya existe .env: se conserva (copia en {copia.name}) y se completa para el piloto.")
        texto = ENV.read_text(encoding="utf-8-sig")
    else:
        pwd = os.environ.get("MON_PWD", "")
        if not pwd:
            for _ in range(3):
                p1 = getpass.getpass("Contraseña para el administrador del piloto (mín. 12, letras y números): ")
                p2 = getpass.getpass("Repite la contraseña: ")
                if p1 != p2:
                    print("No coinciden.")
                    continue
                if problemas_contrasena(p1):
                    print("No sirve: " + ", ".join(problemas_contrasena(p1)))
                    continue
                pwd = p1
                break
        if not pwd or problemas_contrasena(pwd):
            print("NO SE CAMBIÓ NADA: contraseña no válida"
                  + (" (" + ", ".join(problemas_contrasena(pwd)) + ")" if pwd else "") + ".")
            return 1
        texto = (ROOT / ".env.ejemplo").read_text(encoding="utf-8")
        texto = poner(texto, "MONITOREO_ADMIN_PASSWORD", pwd)
        texto = poner(texto, "MONITOREO_SECRET_KEY", secrets.token_urlsafe(48))
        texto = poner(texto, "MONITOREO_PORTAL_TOKEN", secrets.token_urlsafe(24))
        creado = True
    diario, respaldos = externo / "diario", externo / "respaldos"
    diario.mkdir(exist_ok=True)
    respaldos.mkdir(exist_ok=True)
    for k, v in PILOTO.items():
        texto = poner(texto, k, v)
    texto = poner(texto, "MONITOREO_PUERTO", str(a.puerto))
    texto = poner(texto, "MONITOREO_DIARIO_COPIA", str(diario))
    texto = poner(texto, "MONITOREO_RESPALDO_EXTERNO", str(respaldos))
    nuevo = {}
    for raw in texto.splitlines():
        s = raw.strip()
        if s and not s.startswith("#") and "=" in s:
            k, v = s.split("=", 1)
            nuevo[k.strip()] = v.strip().strip('"').strip("'")
    if len(nuevo.get("MONITOREO_SECRET_KEY", "")) < 32:
        texto = poner(texto, "MONITOREO_SECRET_KEY", secrets.token_urlsafe(48))
        nuevo["MONITOREO_SECRET_KEY"] = "x" * 48
    errores, avisos = revisar_configuracion(nuevo)
    if errores:
        print("NO SE CAMBIÓ NADA: la configuración resultante no es válida para el piloto:\n  - " + "\n  - ".join(errores))
        return 1
    ENV.write_text(texto, encoding="utf-8")
    try:
        os.chmod(ENV, 0o600)
    except OSError:
        pass
    print(("Archivo .env creado" if creado else "Archivo .env completado") + " para el PILOTO.")
    for x in avisos:
        print("AVISO:  " + x)
    if not a.sin_base:
        (ROOT / "data").mkdir(exist_ok=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith("MONITOREO_") and k != "MON_PWD"}
        r = subprocess.run([sys.executable, "-m", "monitoreo.migrate", "actualizar"], cwd=ROOT, env=env)
        if r.returncode != 0:
            print("La base no se pudo crear o actualizar (ver el mensaje de arriba). El .env queda escrito.")
            return 1
    print("\n" + "=" * 78)
    print("  PILOTO PREPARADO en " + str(ROOT))
    print(f"  Base:              {ROOT / 'data' / 'monitoreo.db'}")
    print(f"  Copia del diario:  {diario}")
    print(f"  Respaldo externo:  {respaldos}")
    print(f"  Abrir:             iniciar.bat   ->  http://127.0.0.1:{a.puerto}/   (usuario admin, TU contraseña)")
    print("  Después:           crea el operador en Usuarios, con su propia contraseña.")
    print("  Al cerrar el día:  respaldar_externo.bat")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
