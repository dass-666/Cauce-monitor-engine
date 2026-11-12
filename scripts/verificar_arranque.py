"""Verificación de arranque REAL del servidor (v1.2), pensada para GitHub Actions en Windows y Linux.

Arranca `python -m monitoreo` como lo haría iniciar.bat, espera a que responda, hace
peticiones reales por HTTP, lo detiene, lo vuelve a arrancar sobre la misma base y comprueba
que los datos siguen ahí. Repite en desarrollo y en producción, y comprueba que una
configuración insegura en producción impide arrancar.

Uso:  python scripts/verificar_arranque.py --salida resultados/arranque
Código de salida 0 solo si TODAS las comprobaciones pasan. Deja un informe JSON y el
registro de cada servidor en la carpeta de salida (para adjuntarlos si algo falla).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PWD = "Verificacion-Arranque-2026"
WIN = os.name == "nt"
E2E = False


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, name: str, env: dict, out: Path):
        self.name, self.env, self.out = name, env, out
        self.port = int(env["MONITOREO_PUERTO"])
        self.base = f"http://127.0.0.1:{self.port}"
        self.log_path = out / f"servidor-{name}.log"
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        self.log = open(self.log_path, "ab")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if WIN else 0  # para poder enviar Ctrl+Break
        self.proc = subprocess.Popen([sys.executable, "-m", "monitoreo"], cwd=ROOT, env=self.env,
                                     stdout=self.log, stderr=subprocess.STDOUT, creationflags=flags)

    def wait_ready(self, timeout: float = 90) -> float:
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if self.proc.poll() is not None:
                raise RuntimeError(f"el servidor terminó al arrancar (código {self.proc.returncode})")
            try:
                if httpx.get(self.base + "/api/health", timeout=2).json().get("ok"):
                    return round(time.monotonic() - t0, 2)
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(0.5)
        raise RuntimeError(f"no respondió en {timeout} s")

    def stop(self) -> str:
        """Parada ordenada (Ctrl+Break en Windows, SIGTERM en Linux); si no, se fuerza."""
        if self.proc.poll() is None:
            self.proc.send_signal(signal.CTRL_BREAK_EVENT if WIN else signal.SIGTERM)
            try:
                self.proc.wait(30)
                how = "ordenada"
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(10)
                how = "forzada"
        else:
            how = "ya terminado"
        self.log.close()
        return how


class Checks:
    def __init__(self):
        self.items: list[dict] = []

    def __call__(self, name: str, ok: bool, detail="") -> bool:
        self.items.append({"comprobacion": name, "ok": bool(ok), "detalle": detail})
        print(f"[{'OK' if ok else 'FALLO'}] {name}" + (f" - {detail}" if detail else ""), flush=True)
        return ok

    @property
    def ok(self) -> bool:
        return all(i["ok"] for i in self.items)


def base_env(tmp: Path, entorno: str) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("MONITOREO_")}
    env.update({
        "MONITOREO_ENTORNO": entorno, "MONITOREO_PUERTO": str(free_port()), "MONITOREO_HOST": "127.0.0.1",
        "MONITOREO_DB_URL": f"sqlite:///{(tmp / f'{entorno}.db').as_posix()}",
        "MONITOREO_ADMIN_PASSWORD": PWD, "MONITOREO_SECRET_KEY": secrets.token_urlsafe(48),
        "MONITOREO_DATOS_DEMO": "1" if entorno != "produccion" else "0", "MONITOREO_INICIAR_MOTOR": "1",
        "MONITOREO_ABRIR_NAVEGADOR": "0", "MONITOREO_LEASE_S": "5", "PYTHONUNBUFFERED": "1",
    })
    return env


def verify(entorno: str, tmp: Path, out: Path, ck: Checks) -> None:
    prefix = f"[{entorno}]"
    srv = Server(entorno, base_env(tmp, entorno), out)
    srv.start()
    try:
        ck(f"{prefix} arranca y responde /api/health", True, f"{srv.wait_ready()} s")
        c = httpx.Client(base_url=srv.base, timeout=20)
        r = c.post("/api/auth/login", json={"username": "admin", "password": PWD})
        if not ck(f"{prefix} inicio de sesión de admin", r.status_code == 200, r.status_code):
            return
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        info = c.get("/api/info", headers=h).json()
        ck(f"{prefix} /api/info indica el entorno", info.get("entorno") == entorno, info)
        ck(f"{prefix} panel (página principal)", c.get("/").status_code == 200)
        for path in ("/api/dashboard", "/api/requests", "/api/alerts", "/api/observabilidad", "/api/autodiagnostico"):
            st = c.get(path, headers=h).status_code
            ck(f"{prefix} GET {path}", st == 200, st)
        lab = c.get("/api/lab/items", headers=h).status_code
        docs = c.get("/api/docs").status_code
        if entorno == "produccion":
            ck(f"{prefix} laboratorio inaccesible", lab == 404, lab)
            ck(f"{prefix} documentación interactiva oculta", docs == 404, docs)
            ck(f"{prefix} rutas de prueba inaccesibles", c.get("/api-prueba/telegram/mensajes").status_code == 404)
        else:
            ck(f"{prefix} laboratorio disponible", lab == 200, lab)
        r = c.post("/api/requests", headers=h, json={"code": f"ARR-{entorno[:4].upper()}", "holder": "Titular Ficticio",
                                                    "region": "NORTE", "resource_type": "entrada", "priority": 2})
        ck(f"{prefix} registrar una solicitud", r.status_code == 201, r.status_code)
        t0, role = time.monotonic(), None
        while time.monotonic() - t0 < 30:
            hl = c.get("/api/health").json()
            role = hl.get("rol")
            if role == "LIDER" and hl.get("ciclos", 0) > 0:
                break
            time.sleep(0.5)
        ck(f"{prefix} el planificador toma el liderazgo y ejecuta ciclos", role == "LIDER", hl)
        d = c.get("/api/autodiagnostico", headers=h).json()
        crit = [i["titulo"] for i in d["comprobaciones"] if i["estado"] == "CRITICO"]
        ck(f"{prefix} autodiagnóstico sin problemas críticos", not crit, crit or d["estado"])
        if E2E:
            js = "e2e_produccion.js" if entorno == "produccion" else "e2e_panel.js"
            with open(out / f"e2e-{entorno}.log", "w", encoding="utf-8") as f:
                node = shutil.which("node") or "node"
                r = subprocess.run([node, str(ROOT / "scripts" / js), srv.base, "admin", PWD, str(out / "capturas")],
                                   stdout=f, stderr=subprocess.STDOUT, timeout=300)
            ck(f"{prefix} panel con navegador real ({js})", r.returncode == 0,
               (out / f"e2e-{entorno}.log").read_text(encoding="utf-8")[-400:])
    finally:
        how = srv.stop()
    ck(f"{prefix} parada", how != "ya terminado", how)

    # Reinicio sobre la misma base: los datos siguen y el esquema no se rehace.
    srv2 = Server(entorno + "-reinicio", {**srv.env, "MONITOREO_PUERTO": str(free_port())}, out)
    srv2.start()
    try:
        ck(f"{prefix} reinicia sobre la misma base", True, f"{srv2.wait_ready()} s")
        c = httpx.Client(base_url=srv2.base, timeout=20)
        h = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": "admin", "password": PWD}).json()["token"]}
        code = f"ARR-{entorno[:4].upper()}"
        codes = [x.get("code") for x in c.get("/api/requests", headers=h, params={"q": code}).json()["items"]]
        ck(f"{prefix} la solicitud registrada sigue tras reiniciar", code in codes, codes)
    finally:
        srv2.stop()


def verify_insecure_production(tmp: Path, out: Path, ck: Checks) -> None:
    env = base_env(tmp, "produccion")
    env["MONITOREO_SECRET_KEY"] = "corta"
    env["MONITOREO_DB_URL"] = f"sqlite:///{(tmp / 'insegura.db').as_posix()}"
    r = subprocess.run([sys.executable, "-m", "monitoreo"], cwd=ROOT, env=env, capture_output=True, text=True,
                       timeout=60, errors="replace")
    (out / "servidor-produccion-insegura.log").write_text(r.stdout + r.stderr, encoding="utf-8")
    ck("[produccion] configuración insegura: NO arranca y explica por qué",
       r.returncode == 2 and "EL SERVIDOR NO ARRANCA" in r.stderr and "MONITOREO_SECRET_KEY" in r.stderr,
       f"código {r.returncode}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida", type=Path, default=ROOT / "resultados" / "arranque")
    ap.add_argument("--entornos", default="desarrollo,produccion")
    ap.add_argument("--e2e", action="store_true", help="además, prueba el panel con Playwright (requiere node)")
    a = ap.parse_args()
    global E2E
    E2E = a.e2e
    a.salida.mkdir(parents=True, exist_ok=True)
    ck = Checks()
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for e in a.entornos.split(","):
            try:
                verify(e, tmp, a.salida, ck)
            except Exception as ex:  # noqa: BLE001
                ck(f"[{e}] verificación completa", False, f"{type(ex).__name__}: {ex}")
        try:
            verify_insecure_production(tmp, a.salida, ck)
        except Exception as ex:  # noqa: BLE001
            ck("[produccion] configuración insegura", False, f"{type(ex).__name__}: {ex}")
    report = {"sistema": platform.platform(), "python": platform.python_version(), "duracion_s":
              round(time.monotonic() - t0, 1), "todo_correcto": ck.ok, "comprobaciones": ck.items}
    (a.salida / "verificacion_arranque.json").write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                                                     default=str), encoding="utf-8")
    print(f"\n{'TODAS LAS COMPROBACIONES PASARON' if ck.ok else 'HAY COMPROBACIONES FALLIDAS'} "
          f"({sum(i['ok'] for i in ck.items)}/{len(ck.items)}) en {report['duracion_s']} s")
    return 0 if ck.ok else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
