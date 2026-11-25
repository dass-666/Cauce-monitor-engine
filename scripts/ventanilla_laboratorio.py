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
import socket
import sqlite3
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

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
    def __init__(self, carpeta: Path, nuevo: bool = False, rc1: bool = False, forms: Path | None = None,
                 perfil_externo: Path | None = None, host_externo: str | None = None,
                 carpeta_forms: Path | None = None):
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
        self.rc1 = rc1                  # RC1: el administrador también opera (y registros de la lista demo)
        self.portal: PortalProceso | None = None
        self.motor: Server | None = None
        self.env_extra: dict[str, str] = {}   # p. ej. MONITOREO_DIARIO_COPIA en el recorrido con medio externo
        self.forms = Path(forms).resolve() if forms else None   # Forms (Conector Universal) para «Autorrellenar»
        self.perfil_forms: Path | None = None
        # Perfil EXTERNO autorizado (docs/PERFIL_FORMULARIO_EXTERNO.md): en lugar del /cita del portal propio.
        # El host lo escribe quien abre el laboratorio (segunda llave), no sale del perfil.
        self.perfil_externo = Path(perfil_externo).resolve() if perfil_externo else None
        self.host_externo = host_externo
        # Laboratorio AISLADO: el perfil aprobado y el estado de Forms van a una carpeta que la cuenta del conector
        # puede leer (no a la del laboratorio, que es privada de Monitor). Por omisión, la del laboratorio.
        self.carpeta_forms = Path(carpeta_forms) if carpeta_forms else None

    @staticmethod
    def _puerto_disponible(p: int) -> bool:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
            except OSError:
                return False
        return True

    def _puerto_registrado(self) -> int | None:
        """Puerto del portal que la base de esta carpeta ya tiene registrado como su proveedor (si lo hay)."""
        db = self.carpeta / "cauce_lab.db"
        if not db.exists():
            return None
        try:
            con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5)
            try:
                fila = con.execute("select value from settings where key = 'proveedor.identidad'").fetchone()
            finally:
                con.close()
            u = urlparse(json.loads(fila[0])["destino"]) if fila else None
            return u.port if u and u.hostname == "127.0.0.1" else None
        except Exception:  # noqa: BLE001 - sin base legible simplemente no hay preferencia
            return None

    def _puerto_portal_estable(self) -> int | None:
        """El motor registra en la base la identidad del portal (host y PUERTO). Si el portal del laboratorio
        tomara un puerto libre distinto en cada arranque, al reabrir la misma carpeta el motor vería «otro
        portal» y se negaría a preparar o confirmar citas. Por eso se reutiliza el puerto ya registrado (o el
        último usado) mientras esté libre."""
        for p in (self._puerto_registrado(), self.cfg.get("puerto_portal")):
            if isinstance(p, int) and 1024 <= p <= 65535 and self._puerto_disponible(p):
                return p
        return None

    def iniciar(self) -> dict:
        c = self.carpeta
        self.portal = PortalProceso(c / "portal.json", puerto=self._puerto_portal_estable(), token=self.cfg["token"],
                                    token_control=self.cfg["token_control"], log=c / "portal.log",
                                    extra=["--zona-horaria", ZONA])
        self.cfg["puerto_portal"] = self.portal.puerto
        (c / "laboratorio.json").write_text(json.dumps(self.cfg), "utf-8")
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
        if self.forms is not None:
            # Laboratorio · autorrellenado con Forms: se aprueba el perfil del formulario /cita de ESTE portal
            # (huella fijada por Monitor) y el motor recibe dónde está Forms. Ver docs/AUTORRELLENO_FORMS_LAB.md.
            from autorrelleno_forms import aprobar, aprobar_externo, variables_motor
            if self.perfil_externo is not None:
                self.perfil_forms = aprobar_externo(self.forms, self.perfil_externo, self.carpeta_forms or c)
                env.update(variables_motor(self.forms, self.perfil_forms, host_externo=self.host_externo))
            else:
                self.perfil_forms = aprobar(self.forms, self.portal.base, self.carpeta_forms or c)
                env.update(variables_motor(self.forms, self.perfil_forms))
            for k in ("MONITOREO_FORMS_BROWSER_PATH", "MONITOREO_FORMS_HEADLESS", "MONITOREO_AUTORRELLENO_ESPERA_S"):
                if os.environ.get(k):            # base_env() quita MONITOREO_*: se pasan solo estas, si se definieron
                    env[k] = os.environ[k]
        env.update(self.env_extra)
        self.motor = Server("motor-lab", env, c)
        self.motor.start()
        self.motor.wait_ready()
        adm = httpx.Client(base_url=self.motor.base, timeout=30, trust_env=False)
        r = adm.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PWD})
        r.raise_for_status()
        adm.headers["Authorization"] = "Bearer " + r.json()["token"]
        if not adm.get("/api/proveedor").json().get("compatible", True):
            # El puerto registrado ya lo usa otro programa: se adopta el portal actual (explícito y auditado).
            # El motor lo niega solo si quedan reservaciones a medias del portal anterior.
            a = adm.post("/api/proveedor/adoptar", json={"confirmo": True})
            if a.status_code != 200:
                print(f"AVISO: el portal del laboratorio cambió de puerto y no se pudo adoptar: {a.text[:200]}")
        adm.put("/api/settings/engine", json={"captura_modo": "POR_DIA"}).raise_for_status()
        usuarios = {u["username"] for u in adm.get("/api/users").json()}
        if OPERADOR[0] not in usuarios:
            adm.post("/api/users", json={"username": OPERADOR[0], "password": OPERADOR[1], "role": "operador",
                                         "regions": ["NORTE"], "sucursales": ["NOR-1", "NOR-2"],
                                         "operator_name": "Operador de laboratorio"}).raise_for_status()
        adm.close()
        if self.rc1:
            self.portal.ctl("POST", "/control/registros/demo-rc1")
            self._admin_operador(env["MONITOREO_DB_URL"])
        return self.datos()

    @staticmethod
    def _admin_operador(url: str) -> None:
        """RC1: una persona administrativa opera el sistema. Su usuario se vincula a un operador (todas las
        regiones y sucursales) para poder preparar y confirmar la operación de una coincidencia con el mismo
        flujo de Ventanilla. Solo en este laboratorio; no crea cuentas nuevas."""
        from sqlalchemy import select

        from monitoreo.db import Database
        from monitoreo.models import Operator, Region, User
        d = Database(url)
        try:
            with d.session() as s:
                u = s.scalar(select(User).where(User.username == "admin"))
                if u is not None and not s.scalar(select(Operator.id).where(Operator.user_id == u.id)):
                    s.add(Operator(name="Administración (laboratorio)", user_id=u.id,
                                   regions=list(s.scalars(select(Region.code))), sucursales=[], max_concurrent=10))
        finally:
            d.engine.dispose()

    def datos(self) -> dict:
        return {"motor": self.motor.base, "ventanilla": self.motor.base + "/#ventanilla",
                "calendario_laboratorio": self.motor.base + "/#laboratorio-calendario",
                "portal": self.portal.base, "admin": ["admin", ADMIN_PWD], "operador": list(OPERADOR),
                "carpeta": str(self.carpeta), "forms": str(self.forms) if self.forms else None}

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
    ap.add_argument("--forms", help="carpeta de Forms (Conector Universal) instalado: activa «Autorrellenar formulario»")
    ap.add_argument("--perfil-externo", help="perfil de un formulario EXTERNO autorizado (requiere --forms y "
                                             "--host-autorizado); ver docs/PERFIL_FORMULARIO_EXTERNO.md")
    ap.add_argument("--host-autorizado", help="host del sitio externo, escrito por quien abre el laboratorio")
    a = ap.parse_args()
    if a.perfil_externo and not (a.forms and a.host_autorizado):
        print("--perfil-externo necesita --forms y --host-autorizado")
        return 2
    lab = Laboratorio(Path(a.carpeta), a.nuevo, forms=Path(a.forms) if a.forms else None,
                      perfil_externo=Path(a.perfil_externo) if a.perfil_externo else None,
                      host_externo=a.host_autorizado)
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
    if d.get("forms"):
        print(f"  Autorrellenar con Forms:  ACTIVO ({d['forms']}); perfil aprobado en {lab.perfil_forms}")
        if lab.perfil_externo is not None:
            print(f"  Formulario EXTERNO:       {lab.host_externo} (solo datos FICTICIOS; Forms no envía nada)")
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
