"""v2.3 · Abrir Cauce en modo LABORATORIO para recorrer Ventanilla de principio a fin con la interfaz.

Arranca, en una carpeta propia (no toca `data/`):
  - el portal de laboratorio propio (127.0.0.1, datos ficticios) con su «Calendario del laboratorio»;
  - el motor con la exigencia de consentimiento ACTIVA, captura POR DÍA y la zona horaria de México;
  - un usuario administrador y un operador de prueba.

Después imprime las direcciones y las claves y se queda en marcha hasta Ctrl+C. Todo lo demás se hace
desde el navegador: no hace falta llamar a la API ni ejecutar más scripts.

Uso:
  python scripts/ventanilla_laboratorio.py                      (carpeta resultados/ventanilla_lab)
  python scripts/ventanilla_laboratorio.py --carpeta C:\\lab --nuevo   (borra la carpeta y empieza de cero)
  python scripts/ventanilla_laboratorio.py --abrir              (además abre el navegador)

Para usarlo desde otro script (la demostración automática): `Laboratorio(carpeta).iniciar()`.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import signal
import sys
import time
import webbrowser
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from portal_reservas.proceso import PortalProceso  # noqa: E402
from verificar_arranque import Server, base_env  # noqa: E402

ZONA = "America/Mexico_City"
ADMIN_PWD = "Admin-Laboratorio-2026"
OPERADOR = ("operador1", "Operador-Laboratorio-2026")


class Laboratorio:
    def __init__(self, carpeta: Path, nuevo: bool = False):
        self.carpeta = Path(carpeta)
        if nuevo and self.carpeta.exists():
            shutil.rmtree(self.carpeta)
        self.carpeta.mkdir(parents=True, exist_ok=True)
        cfg_path = self.carpeta / "laboratorio.json"
        # Los tokens se conservan entre arranques de la misma carpeta: así una atención interrumpida
        # se retoma al volver a abrir el laboratorio (misma base, mismo portal).
        self.cfg = json.loads(cfg_path.read_text("utf-8")) if cfg_path.exists() else {
            "token": secrets.token_urlsafe(16), "token_control": secrets.token_urlsafe(16),
            "secret": secrets.token_urlsafe(48)}
        cfg_path.write_text(json.dumps(self.cfg), "utf-8")
        self.portal: PortalProceso | None = None
        self.motor: Server | None = None

    def iniciar(self) -> dict:
        c = self.carpeta
        self.portal = PortalProceso(c / "portal.json", token=self.cfg["token"], token_control=self.cfg["token_control"],
                                    log=c / "portal.log", extra=["--zona-horaria", ZONA])
        self.portal.iniciar()
        env = base_env(c, "desarrollo")
        env.update({"MONITOREO_DATOS_DEMO": "0", "MONITOREO_ADMIN_PASSWORD": ADMIN_PWD,
                    "MONITOREO_SECRET_KEY": self.cfg["secret"],
                    "MONITOREO_DB_URL": f"sqlite:///{(c / 'cauce_lab.db').as_posix()}",
                    "MONITOREO_DATA_DIR": str(c / "datos"),
                    "MONITOREO_PORTAL_URL": self.portal.base, "MONITOREO_PORTAL_TOKEN": self.cfg["token"],
                    "MONITOREO_ZONA_HORARIA": ZONA, "MONITOREO_EXIGIR_CONSENTIMIENTO": "1",
                    # v2.3: el motor conoce el token de control SOLO para el portal de laboratorio en 127.0.0.1
                    "MONITOREO_PORTAL_TOKEN_CONTROL": self.cfg["token_control"]})
        if os.environ.get("MONITOREO_PUERTO_LAB"):
            env["MONITOREO_PUERTO"] = os.environ["MONITOREO_PUERTO_LAB"]
        self.motor = Server("motor-lab", env, c)
        self.motor.start()
        self.motor.wait_ready()
        adm = httpx.Client(base_url=self.motor.base, timeout=30, trust_env=False)
        r = adm.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PWD})
        r.raise_for_status()
        adm.headers["Authorization"] = "Bearer " + r.json()["token"]
        adm.put("/api/settings/engine", json={"captura_modo": "POR_DIA"}).raise_for_status()
        usuarios = {u["username"] for u in adm.get("/api/users").json()}
        if OPERADOR[0] not in usuarios:
            adm.post("/api/users", json={"username": OPERADOR[0], "password": OPERADOR[1], "role": "operador",
                                         "regions": ["NORTE"], "sucursales": ["NOR-1", "NOR-2"],
                                         "operator_name": "Operador de laboratorio"}).raise_for_status()
        adm.close()
        return self.datos()

    def datos(self) -> dict:
        return {"motor": self.motor.base, "ventanilla": self.motor.base + "/#ventanilla",
                "calendario_laboratorio": self.motor.base + "/#laboratorio-calendario",
                "portal": self.portal.base, "admin": ["admin", ADMIN_PWD], "operador": list(OPERADOR),
                "carpeta": str(self.carpeta)}

    def detener(self) -> None:
        if self.motor:
            self.motor.stop()
        if self.portal:
            self.portal.detener()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--carpeta", default=str(ROOT / "resultados" / "ventanilla_lab"))
    ap.add_argument("--nuevo", action="store_true", help="borrar la carpeta y empezar de cero")
    ap.add_argument("--abrir", action="store_true", help="abrir el navegador en Ventanilla")
    a = ap.parse_args()
    lab = Laboratorio(Path(a.carpeta), a.nuevo)
    try:
        d = lab.iniciar()
    except Exception as e:  # noqa: BLE001
        lab.detener()
        print(f"No se pudo arrancar el laboratorio: {e}. Revisa {a.carpeta}/servidor-motor-lab.log y portal.log")
        return 1
    print("\nCauce · laboratorio de Ventanilla (datos FICTICIOS, solo este equipo)\n")
    print(f"  Ventanilla:               {d['ventanilla']}")
    print(f"  Calendario del laboratorio: {d['calendario_laboratorio']}  (solo administrador)")
    print(f"  Administrador:            {d['admin'][0]} / {d['admin'][1]}")
    print(f"  Operador:                 {d['operador'][0]} / {d['operador'][1]}")
    print(f"  Carpeta:                  {d['carpeta']}")
    print("\nPulsa Ctrl+C para detenerlo. Al volver a abrirlo con la misma carpeta, todo sigue donde quedó.\n")
    if a.abrir:
        webbrowser.open(d["ventanilla"])
    parar = {"v": False}
    signal.signal(signal.SIGINT, lambda *_: parar.update(v=True))
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, lambda *_: parar.update(v=True))
    try:
        while not parar["v"]:
            if lab.motor.proc.poll() is not None:
                print("El motor terminó; revisa el registro en la carpeta.")
                break
            time.sleep(0.5)
    finally:
        lab.detener()
    return 0


if __name__ == "__main__":
    sys.exit(main())
