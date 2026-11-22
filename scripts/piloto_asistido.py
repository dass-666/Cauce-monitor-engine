"""DEMOSTRACIÓN del piloto asistido · Cauce en modo piloto (producción + flujo administrativo), datos FICTICIOS.

NO es la forma de operar el piloto. El piloto real es una instalación normal con su `.env`, iniciada con
`iniciar.bat` (ver docs/INICIAR_PILOTO_WINDOWS.md y preparar_piloto.bat). Este script solo sirve para
demostraciones y pruebas: usa su propia carpeta, su propia base y contraseñas aleatorias que genera él mismo;
los respaldos (`respaldar_externo.bat`) NO cubren esta carpeta.

Separación de entornos (auditoría del PR #10):
  - cada carpeta de demostración lleva la marca `DEMOSTRACION_FICTICIA.json`;
  - el script SOLO borra carpetas que tengan esa marca; si la carpeta tiene contenido sin la marca (por ejemplo
    una base con datos), se NIEGA y no toca nada;
  - `--recorrido` usa por omisión OTRA carpeta que el uso a mano, y solo reinicia carpetas creadas por un
    recorrido: nunca borra lo que alguien hizo a mano;
  - se niega a trabajar dentro de una instalación (carpeta con `.env`) o de su carpeta `data`.

Arranca el motor con:
  - MONITOREO_ENTORNO=produccion y MONITOREO_PRODUCCION_FLUJO_ADMIN=1;
  - consentimiento exigido, copia del diario en una subcarpeta (solo para que arranque: NO es otra unidad);
  - SIN portal: Cauce no se conecta a ningún portal; la cita la agenda una persona en el portal oficial.
Crea un administrador y un operador (contraseñas aleatorias, en `demostracion.json` de la carpeta y en la
consola) y un expediente FICTICIO con consentimiento ficticio.

Uso:
  python scripts/piloto_asistido.py                     demostración a mano, hasta Ctrl+C (resultados/demo_asistido)
  python scripts/piloto_asistido.py --recorrido         recorrido automático (resultados/demo_asistido_recorrido)
  python scripts/piloto_asistido.py --carpeta D:\\demo --nuevo     borra SOLO una carpeta de demostración marcada
Código 0 si todo pasa, 1 si algo falla, 2 si se niega por seguridad, 3 si falta Node.js (solo con --recorrido).
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

OPERADOR_USUARIO = "operador1"
ZONA = "America/Mexico_City"
MARCA = "DEMOSTRACION_FICTICIA.json"
CARPETA_A_MANO = ROOT / "resultados" / "demo_asistido"
CARPETA_RECORRIDO = ROOT / "resultados" / "demo_asistido_recorrido"


class Negado(Exception):
    """El script se niega a continuar para no tocar datos que no son de una demostración."""


def datos_ficticios(n: int) -> dict:
    tel = f"555{n:07d}"
    letras = "".join("ABCDEFGHIJ"[int(d)] for d in str(n))       # el nombre no admite números
    return {"nombre": f"Persona Ficticia {letras.capitalize()}", "fecha_nacimiento": "1990-05-04", "telefono": tel,
            "telefono_confirmacion": tel, "correo": f"piloto{n}@ejemplo.test", "correo_confirmacion": f"piloto{n}@ejemplo.test",
            "region": "NORTE", "tipo_tramite": "alta", "id_registro_previo": f"REG{n:06d}", "sucursal": "NOR-1",
            "consentimiento": {"medio": "FIRMA_PAPEL", "aviso_privacidad": "AP-PILOTO-LAB",
                               "evidencia_sha256": hashlib.sha256(f"formato ficticio {n}".encode()).hexdigest(),
                               "evidencia_ref": "Carpeta LAB-01"}}


def _marca(carpeta: Path) -> dict | None:
    try:
        return json.loads((carpeta / MARCA).read_text("utf-8"))
    except (OSError, ValueError):
        return None


def comprobar_carpeta(carpeta: Path, modo: str, nuevo: bool) -> bool:
    """Devuelve True si hay que vaciar la carpeta antes de empezar. Lanza Negado si la carpeta no es de una
    demostración: este script jamás borra ni reutiliza datos que no creó él."""
    c = carpeta.resolve()
    for padre in (c, *c.parents):
        if (padre / ".env").is_file() and padre != ROOT:
            raise Negado(f"{padre} es una instalación de Cauce (tiene .env): la demostración no se ejecuta ahí.")
    datos_instalacion = (ROOT / "data").resolve()
    if c == ROOT or c == datos_instalacion or datos_instalacion in c.parents:
        raise Negado("La carpeta de la demostración no puede ser la instalación ni su carpeta data.")
    if not c.exists() or not any(c.iterdir()):
        return False
    m = _marca(c)
    if m is None:
        raise Negado(f"{c} ya tiene contenido y NO es una carpeta de demostración (falta {MARCA}). No se toca nada. "
                     "Usa otra carpeta con --carpeta, o bórrala tú si de verdad no la necesitas.")
    if modo == "recorrido":
        if m.get("modo") != "recorrido":
            raise Negado(f"{c} es una demostración hecha A MANO: el recorrido automático no la borra. "
                         "Usa otra carpeta con --carpeta (por omisión el recorrido usa la suya).")
        return True
    return nuevo


class Piloto:
    def __init__(self, carpeta: Path, nuevo: bool = False, modo: str = "a_mano"):
        self.c = Path(carpeta).resolve()
        if comprobar_carpeta(self.c, modo, nuevo):
            shutil.rmtree(self.c)
        self.c.mkdir(parents=True, exist_ok=True)
        import secrets
        if _marca(self.c) is None:
            (self.c / MARCA).write_text(json.dumps({
                "modo": modo, "aviso": "Carpeta de DEMOSTRACIÓN con datos ficticios. No es el piloto. El script "
                                       "piloto_asistido.py puede borrarla."}, ensure_ascii=False), "utf-8")
        cfg = self.c / "demostracion.json"
        if cfg.exists():
            self.cfg = json.loads(cfg.read_text("utf-8"))
        else:       # contraseñas ALEATORIAS por carpeta: ninguna credencial fija en el código
            self.cfg = {"secret": secrets.token_urlsafe(48), "admin": "Adm-" + secrets.token_urlsafe(12),
                        "operador": "Ope-" + secrets.token_urlsafe(12)}
            cfg.write_text(json.dumps(self.cfg), "utf-8")
        self.admin_pwd, self.operador = self.cfg["admin"], (OPERADOR_USUARIO, self.cfg["operador"])
        self.motor: Server | None = None

    def iniciar(self) -> None:
        env = base_env(self.c, "produccion")
        copia = self.c / "copia_diario_DEMOSTRACION"
        copia.mkdir(exist_ok=True)
        env.update({"MONITOREO_PRODUCCION_FLUJO_ADMIN": "1", "MONITOREO_EXIGIR_CONSENTIMIENTO": "1",
                    "MONITOREO_DIARIO_COPIA": str(copia), "MONITOREO_ADMIN_PASSWORD": self.admin_pwd,
                    "MONITOREO_SECRET_KEY": self.cfg["secret"], "MONITOREO_DATOS_DEMO": "0",
                    "MONITOREO_DB_URL": f"sqlite:///{(self.c / 'cauce_piloto.db').as_posix()}",
                    "MONITOREO_DATA_DIR": str(self.c / "datos"), "MONITOREO_ZONA_HORARIA": ZONA,
                    "MONITOREO_PORTAL_URL": "http://127.0.0.1:9"})     # nadie escucha: demuestra que no se usa
        self.motor = Server("motor-piloto", env, self.c)
        self.motor.start()
        self.motor.wait_ready()
        self.adm = httpx.Client(base_url=self.motor.base, timeout=30)
        self.adm.headers["Authorization"] = "Bearer " + self._login("admin", self.admin_pwd)
        r = self.adm.post("/api/users", json={"username": self.operador[0], "password": self.operador[1], "role": "operador",
                                              "regions": ["NORTE"], "operator_name": "Operador de demostración"})
        if r.status_code not in (201, 409):
            raise RuntimeError(f"no se pudo crear el operador: {r.status_code} {r.text[:200]}")
        self.op = httpx.Client(base_url=self.motor.base, timeout=30)
        self.op.headers["Authorization"] = "Bearer " + self._login(*self.operador)

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
    entrada = {"op": list(p.operador), "admin": ["admin", p.admin_pwd], "codigo": e["codigo"], "eid": e["id"],
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
       hist and hist[0]["estado"] == "CITA_REGISTRADA" and hist[0]["persona"] == p.operador[0]
       and hist[0]["folio"] == entrada["folio"] and hist[0]["verificada_con_proveedor"] is False,
       [{k: x.get(k) for k in ("estado", "persona", "folio")} for x in hist])
    cerr = p.op.post(f"/api/ventanilla/atenciones/{a['id']}/cerrar", json={}).json()
    ck("API · la atención de Ventanilla se cierra CONFIRMADA con el folio", cerr.get("estado") == "CONFIRMADA"
       and cerr.get("folio") == entrada["folio"], {k: cerr.get(k) for k in ("estado", "folio")})
    rec = p.adm.get("/api/recuperacion").json()
    ck("API · recuperación operable (el diario anotó la cita sin discrepancias)", rec.get("bloqueado") is False,
       {k: rec.get(k) for k in ("bloqueado", "pendientes")})
    panel = p.adm.get("/api/admin/panel")
    conf = [x for x in (panel.json().get("confirmadas") or []) if x.get("folio") == entrada["folio"]] if panel.status_code == 200 else []
    ck("API · la mesa de control la muestra como «declarada por persona», no como comprobada en el portal",
       bool(conf) and conf[0].get("origen") == "PERSONA", conf)
    # La persona cancela: el administrador anula la cita desde la ficha (segunda fase del navegador)
    f_in.write_text(json.dumps({**entrada, "fase": "anular"}), "utf-8")
    r = subprocess.run([nodo, str(ROOT / "scripts" / "recorrido_asistido_ui.js"), p.motor.base, str(capt), str(f_in)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
                       env=navegador.entorno(), cwd=str(ROOT / "scripts"))
    linea = next((x for x in r.stdout.splitlines() if x.startswith("RESULTADO ")), None)
    if not ck("el navegador terminó (anulación)", linea is not None, (r.stdout + r.stderr)[-800:]):
        return res
    ui2 = json.loads(linea[len("RESULTADO "):])
    for x in ui2["pasos"]:
        ck("UI · " + x["paso"], x["ok"], x.get("detalle"))
    ck("UI · sin errores de JavaScript (anulación)", not ui2["errores_js"], ui2["errores_js"][:5])
    st = p.adm.get(f"/api/expedientes/{e['id']}/asistida").json()
    exp = p.adm.get(f"/api/expedientes/{e['id']}").json()
    ck("API · la cita quedó ANULADA por el administrador y el expediente CERRADO (CANCELADO)",
       st["historial"][0]["estado"] == "ANULADA" and st["historial"][0]["revocada_por"] == "admin"
       and exp.get("estado") == "CERRADO", {"conf": st["historial"][0]["estado"], "exp": exp.get("estado")})
    panel = p.adm.get("/api/admin/panel").json()
    rec = p.adm.get("/api/recuperacion").json()
    ck("API · la mesa de control ya no la cuenta y la recuperación sigue operable",
       not [x for x in (panel.get("confirmadas") or []) if x.get("folio") == entrada["folio"]] and rec.get("bloqueado") is False,
       {"bloqueado": rec.get("bloqueado")})
    return res


def main() -> int:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--carpeta", default=None, help="carpeta de la demostración (por omisión, una para el uso a "
                    "mano y OTRA para --recorrido, dentro de resultados/)")
    ap.add_argument("--nuevo", action="store_true", help="borra la carpeta (solo si es de demostración) y empieza de cero")
    ap.add_argument("--recorrido", action="store_true", help="recorrido automático con Chromium y capturas")
    a = ap.parse_args()
    if a.recorrido and not shutil.which("node"):
        print("Falta Node.js (https://nodejs.org) para el navegador automatizado.")
        return 3
    carpeta = Path(a.carpeta) if a.carpeta else (CARPETA_RECORRIDO if a.recorrido else CARPETA_A_MANO)
    try:
        p = Piloto(carpeta, nuevo=a.nuevo, modo="recorrido" if a.recorrido else "a_mano")
    except Negado as e:
        print(f"\nNO SE HIZO NADA: {e}")
        return 2
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
        print("  DEMOSTRACIÓN de Cauce en modo piloto (datos FICTICIOS). NO es el piloto:")
        print("  el piloto real se prepara con preparar_piloto.bat y se abre con iniciar.bat.")
        print(f"  Abre:          {p.motor.base}/")
        print(f"  Operador:      {p.operador[0]} / {p.operador[1]}")
        print(f"  Administrador: admin / {p.admin_pwd}")
        print(f"  Expediente ficticio ya tomado por el operador: {e['codigo']} (Ventanilla o Captura › Mi trabajo)")
        print(f"  Carpeta:       {p.c}   (los respaldos de Cauce NO cubren esta carpeta)")
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
