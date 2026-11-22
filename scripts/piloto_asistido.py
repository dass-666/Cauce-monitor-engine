"""Piloto asistido · abrir Cauce EN MODO PILOTO (producción + flujo administrativo) en una carpeta APARTE.

Pensado para la validación del domingo en Windows: no toca la instalación de la MSI (C:\\cauce-msi) ni sus
datos, ni `data/` del proyecto. Todo vive en la carpeta indicada (por omisión resultados/piloto_asistido).

Arranca el motor con:
  - MONITOREO_ENTORNO=produccion y MONITOREO_PRODUCCION_FLUJO_ADMIN=1 (lo que usaría el piloto);
  - consentimiento exigido, copia externa del diario en una subcarpeta (en el piloto real, OTRA unidad);
  - SIN portal: Cauce no se conecta a ningún portal; la cita la agenda una persona en el portal oficial.
Crea un administrador y un operador y, si se pide, un expediente FICTICIO con consentimiento ficticio.

Uso:
  python scripts/piloto_asistido.py                     queda en marcha hasta Ctrl+C (recorrido a mano)
  python scripts/piloto_asistido.py --recorrido         recorrido automático con Chromium y capturas
  python scripts/piloto_asistido.py --carpeta D:\\piloto --nuevo
Código 0 si todo pasa, 1 si algo falla, 3 si falta Node.js/Playwright (solo con --recorrido).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from verificar_arranque import Server, base_env  # noqa: E402

from monitoreo import navegador  # noqa: E402

ADMIN_PWD = "Admin-Piloto-Lab-2026"
OPERADOR = ("operador1", "Operador-Piloto-Lab-2026")
ZONA = "America/Mexico_City"


def datos_ficticios(n: int) -> dict:
    tel = f"555{n:07d}"
    letras = "".join("ABCDEFGHIJ"[int(d)] for d in str(n))       # el nombre no admite números
    return {"nombre": f"Persona Ficticia {letras.capitalize()}", "fecha_nacimiento": "1990-05-04", "telefono": tel,
            "telefono_confirmacion": tel, "correo": f"piloto{n}@ejemplo.test", "correo_confirmacion": f"piloto{n}@ejemplo.test",
            "region": "NORTE", "tipo_tramite": "alta", "id_registro_previo": f"REG{n:06d}", "sucursal": "NOR-1",
            "consentimiento": {"medio": "FIRMA_PAPEL", "aviso_privacidad": "AP-PILOTO-LAB",
                               "evidencia_sha256": hashlib.sha256(f"formato ficticio {n}".encode()).hexdigest(),
                               "evidencia_ref": "Carpeta LAB-01"}}


class Piloto:
    def __init__(self, carpeta: Path, nuevo: bool = False):
        self.c = Path(carpeta).resolve()
        if nuevo and self.c.exists():
            shutil.rmtree(self.c)
        self.c.mkdir(parents=True, exist_ok=True)
        cfg = self.c / "piloto.json"
        import secrets
        self.cfg = json.loads(cfg.read_text("utf-8")) if cfg.exists() else {"secret": secrets.token_urlsafe(48)}
        cfg.write_text(json.dumps(self.cfg), "utf-8")
        self.motor: Server | None = None

    def iniciar(self) -> None:
        env = base_env(self.c, "produccion")
        copia = self.c / "copia_diario_EN_OTRA_UNIDAD"
        copia.mkdir(exist_ok=True)
        env.update({"MONITOREO_PRODUCCION_FLUJO_ADMIN": "1", "MONITOREO_EXIGIR_CONSENTIMIENTO": "1",
                    "MONITOREO_DIARIO_COPIA": str(copia), "MONITOREO_ADMIN_PASSWORD": ADMIN_PWD,
                    "MONITOREO_SECRET_KEY": self.cfg["secret"], "MONITOREO_DATOS_DEMO": "0",
                    "MONITOREO_DB_URL": f"sqlite:///{(self.c / 'cauce_piloto.db').as_posix()}",
                    "MONITOREO_DATA_DIR": str(self.c / "datos"), "MONITOREO_ZONA_HORARIA": ZONA,
                    "MONITOREO_PORTAL_URL": "http://127.0.0.1:9"})     # nadie escucha: demuestra que no se usa
        self.motor = Server("motor-piloto", env, self.c)
        self.motor.start()
        self.motor.wait_ready()
        self.adm = httpx.Client(base_url=self.motor.base, timeout=30)
        self.adm.headers["Authorization"] = "Bearer " + self._login("admin", ADMIN_PWD)
        r = self.adm.post("/api/users", json={"username": OPERADOR[0], "password": OPERADOR[1], "role": "operador",
                                              "regions": ["NORTE"], "operator_name": "Operador del piloto"})
        if r.status_code not in (201, 409):
            raise RuntimeError(f"no se pudo crear el operador: {r.status_code} {r.text[:200]}")
        self.op = httpx.Client(base_url=self.motor.base, timeout=30)
        self.op.headers["Authorization"] = "Bearer " + self._login(*OPERADOR)

    def _login(self, u: str, p: str) -> str:
        r = httpx.post(self.motor.base + "/api/auth/login", json={"username": u, "password": p}, timeout=30)
        r.raise_for_status()
        return r.json()["token"]

    def atencion_con_expediente(self, n: int) -> tuple[dict, dict]:
        """El operador inicia una atención y vincula un expediente FICTICIO (queda tomado por él)."""
        a = self.op.post("/api/ventanilla/atenciones", json={"clave": uuid.uuid4().hex}).json()
        r = self.op.post(f"/api/ventanilla/atenciones/{a['id']}/expediente", json={"datos": datos_ficticios(n)})
        if r.status_code != 200:
            raise RuntimeError(f"no se pudo vincular el expediente ficticio: {r.status_code} {r.text[:300]}")
        return a, r.json()["expediente"]

    def detener(self) -> None:
        if self.motor:
            self.motor.stop()


def recorrido(p: Piloto, out: Path) -> dict:
    res: dict = {"version": "piloto-asistido", "comprobaciones": []}

    def ck(nombre, ok, detalle=None):
        res["comprobaciones"].append({"nombre": nombre, "ok": bool(ok), "detalle": detalle})
        print(("OK   " if ok else "FALLA") + " " + nombre + ("" if ok else f" · {detalle}"))
        return ok

    info = p.adm.get("/api/info").json()
    ck("modo piloto: producción, flujo administrativo activo, sin laboratorio",
       info.get("flujo_admin") is True and info.get("laboratorio") is False, {k: info.get(k) for k in ("flujo_admin", "laboratorio")})
    a, e = p.atencion_con_expediente(int(time.time()) % 10_000_000)
    ck("Ventanilla: el operador tomó el expediente ficticio", e["estado"] in ("ASIGNADO", "CAPTURA_EN_PROCESO"), e["estado"])
    capt = out / "capturas"
    if capt.exists():
        shutil.rmtree(capt)
    capt.mkdir(parents=True)
    nodo = shutil.which("node")
    if not ck("Node.js disponible para el navegador automatizado", bool(nodo)):
        return res
    entrada = {"op": list(OPERADOR), "admin": ["admin", ADMIN_PWD], "codigo": e["codigo"], "eid": e["id"],
               "folio": "FON-LAB-" + uuid.uuid4().hex[:6].upper(), "fecha": (date.today() + timedelta(days=5)).isoformat()}
    f_in = p.c / "entrada_ui.json"
    f_in.write_text(json.dumps(entrada), "utf-8")
    r = subprocess.run([nodo, str(ROOT / "scripts" / "recorrido_asistido_ui.js"), p.motor.base, str(capt), str(f_in)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
                       env=navegador.entorno(), cwd=str(ROOT / "scripts"))
    linea = next((x for x in r.stdout.splitlines() if x.startswith("RESULTADO ")), None)
    if not ck("el navegador terminó", linea is not None, (r.stdout + r.stderr)[-800:]):
        return res
    ui = json.loads(linea[len("RESULTADO "):])
    res["interfaz"] = ui
    for x in ui["pasos"]:
        ck("UI · " + x["paso"], x["ok"], x.get("detalle"))
    ck("UI · sin errores de JavaScript", not ui["errores_js"], ui["errores_js"][:5])
    # Lo que dijo la pantalla, comprobado por API
    st = p.op.get(f"/api/expedientes/{e['id']}/asistida").json()
    hist = st["historial"]
    ck("API · la confirmación quedó CITA_REGISTRADA por el operador, sin verificación del proveedor",
       hist and hist[0]["estado"] == "CITA_REGISTRADA" and hist[0]["persona"] == OPERADOR[0]
       and hist[0]["folio"] == entrada["folio"] and hist[0]["verificada_con_proveedor"] is False,
       [{k: x.get(k) for k in ("estado", "persona", "folio")} for x in hist])
    cerr = p.op.post(f"/api/ventanilla/atenciones/{a['id']}/cerrar", json={}).json()
    ck("API · la atención de Ventanilla se cierra CONFIRMADA con el folio", cerr.get("estado") == "CONFIRMADA"
       and cerr.get("folio") == entrada["folio"], {k: cerr.get(k) for k in ("estado", "folio")})
    rec = p.adm.get("/api/recuperacion").json()
    ck("API · recuperación operable (el diario anotó la cita sin discrepancias)", rec.get("bloqueado") is False,
       {k: rec.get(k) for k in ("bloqueado", "pendientes")})
    panel = p.adm.get("/api/admin/panel")
    ck("API · la mesa de control responde", panel.status_code == 200, panel.status_code)
    return res


def main() -> int:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--carpeta", default=str(ROOT / "resultados" / "piloto_asistido"))
    ap.add_argument("--nuevo", action="store_true", help="borra la carpeta y empieza de cero")
    ap.add_argument("--recorrido", action="store_true", help="recorrido automático con Chromium y capturas")
    a = ap.parse_args()
    if a.recorrido and not shutil.which("node"):
        print("Falta Node.js (https://nodejs.org) para el navegador automatizado.")
        return 3
    p = Piloto(Path(a.carpeta), nuevo=a.nuevo or a.recorrido)
    p.iniciar()
    try:
        if a.recorrido:
            res = recorrido(p, p.c)
            ok = res["comprobaciones"] and all(x["ok"] for x in res["comprobaciones"])
            res["aprobadas"] = sum(x["ok"] for x in res["comprobaciones"])
            res["total"] = len(res["comprobaciones"])
            (p.c / "recorrido_asistido.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), "utf-8")
            print(f"\nRESULTADO: {res['aprobadas']}/{res['total']} comprobaciones · evidencia en {p.c}")
            return 0 if ok else 1
        _, e = p.atencion_con_expediente(int(time.time()) % 10_000_000)
        print("\n" + "=" * 78)
        print("  CAUCE EN MODO PILOTO (datos FICTICIOS, carpeta aparte; la MSI no se toca)")
        print(f"  Abre:          {p.motor.base}/")
        print(f"  Operador:      {OPERADOR[0]} / {OPERADOR[1]}")
        print(f"  Administrador: admin / {ADMIN_PWD}")
        print(f"  Expediente ficticio ya tomado por el operador: {e['codigo']} (Ventanilla o Captura › Mi trabajo)")
        print(f"  Carpeta:       {p.c}")
        print("  Ctrl+C para terminar.")
        print("=" * 78)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        return 0
    finally:
        p.detener()


if __name__ == "__main__":
    sys.exit(main())
