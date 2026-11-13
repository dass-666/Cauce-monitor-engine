"""v1.7 · Interacciones del operador ANTES (código v1.6) y DESPUÉS (código v1.7), medidas igual.

Para cada versión arranca, como procesos independientes y con red real en 127.0.0.1, el portal de
prueba por día y el motor DE ESA VERSIÓN (`--raiz-v16` es una copia del código de la v1.6, p. ej. un
`git worktree` de la rama fase-1.6). Crea un operador y dos expedientes ficticios POR_DIA, espera a
que el MOTOR los asigne y recorre el panel con Playwright (scripts/medir_interacciones.js) hasta los
dos folios. Cuenta clics, casillas y campos tecleados. Los folios se verifican en el portal.

Uso: python scripts/medir_interacciones.py --raiz-v16 ../base16 --salida docs/resultados/v1.7
"""
from __future__ import annotations

import argparse
import json
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from portal_reservas.proceso import PortalProceso  # noqa: E402
from verificar_arranque import base_env  # noqa: E402

PWD = "Medir-Interacciones-2026"
OP_PWD = "Operador-Medir-2026"
ZONA = "America/Mexico_City"


def esperar(fn, timeout: float):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(0.5)
    return None


def medir(version: str, raiz: Path, out: Path) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix=f"medir-{version}-"))
    (tmp / "portal").mkdir()
    portal = PortalProceso(tmp / "portal" / "estado.json", token=secrets.token_urlsafe(16),
                           token_control=secrets.token_urlsafe(16), log=out / f"portal-{version}.log",
                           extra=["--zona-horaria", ZONA])
    portal.iniciar()
    env = base_env(tmp, "desarrollo")
    env.update({"MONITOREO_DATOS_DEMO": "0", "MONITOREO_ADMIN_PASSWORD": PWD, "MONITOREO_PORTAL_URL": portal.base,
                "MONITOREO_PORTAL_TOKEN": portal.token, "MONITOREO_ZONA_HORARIA": ZONA})
    base = f"http://127.0.0.1:{env['MONITOREO_PUERTO']}"
    log = open(out / f"motor-{version}.log", "wb")
    motor = subprocess.Popen([sys.executable, "-m", "monitoreo"], cwd=raiz, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        esperar(lambda: _ok(base), 90)
        c = httpx.Client(base_url=base, timeout=30, trust_env=False)
        c.headers["Authorization"] = "Bearer " + c.post("/api/auth/login", json={"username": "admin", "password": PWD}).json()["token"]
        c.post("/api/sources", json={"code": "portal-lab-dias", "name": "Portal de prueba", "region": "CENTRO",
                                     "availability_type": "calendario", "connector_type": "portal_dias",
                                     "config": {"url_base": portal.base, "token_env": "MONITOREO_PORTAL_TOKEN", "timeout_s": 5,
                                                "mecanismo": "periodico"},
                                     "interval_s": 3, "jitter_pct": 0, "rate_limit_per_min": 120, "max_retries": 1}).raise_for_status()
        c.post("/api/users", json={"username": "operador1", "password": OP_PWD, "role": "operador", "regions": ["NORTE"],
                                   "sucursales": ["NOR-1", "NOR-2"], "operator_name": "Operador Ficticio"}).raise_for_status()
        codigos, ids = [], []
        for n, prio in ((11, 2), (12, 3)):
            reg = portal.registro(n)
            r = c.post("/api/expedientes", json={
                "nombre": "Medicion Ficticia " + "AB"[n - 11], "fecha_nacimiento": reg["fecha_nacimiento"],
                "telefono": f"55500011{n}", "telefono_confirmacion": f"55500011{n}", "correo": f"m{n}@ejemplo.test",
                "correo_confirmacion": f"m{n}@ejemplo.test", "region": "NORTE", "tipo_tramite": reg["tramite"],
                "id_registro_previo": reg["identificador"], "prioridad": prio, "modo": "POR_DIA", "sucursal": "NOR-1",
                "sucursales_alternativas": ["NOR-2"]})
            r.raise_for_status()
            codigos.append(r.json()["codigo"])
            ids.append(r.json()["id"])
        ok = esperar(lambda: all(c.get(f"/api/expedientes/{i}").json()["estado"] == "ASIGNADO" for i in ids), 120)
        if not ok:
            raise RuntimeError("el motor no asignó los dos expedientes")
        node = shutil.which("node")
        p = subprocess.run([node, str(ROOT / "scripts" / "medir_interacciones.js"), base, "operador1", OP_PWD, version,
                            codigos[0], codigos[1], str(out / "capturas_interacciones")], cwd=ROOT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=300)
        (out / f"medir_interacciones_{version}.log").write_text(p.stdout + "\n" + p.stderr, encoding="utf-8")
        linea = next((x for x in p.stdout.splitlines() if x.startswith("RESULTADO ")), None)
        if p.returncode or not linea:
            raise RuntimeError(f"recorrido {version} falló: {(p.stderr or p.stdout)[-600:]}")
        res = json.loads(linea[10:])
        res["folios_en_portal"] = [portal.http.get(portal.base + f"/api/reservaciones/{f}", headers={
            "Authorization": f"Bearer {portal.token}"}).status_code == 200 for f in res["folios"]]
        return res
    finally:
        motor.terminate()
        try:
            motor.wait(30)
        except subprocess.TimeoutExpired:
            motor.kill()
        log.close()
        portal.detener()


def _ok(base):
    try:
        return httpx.get(base + "/api/health", timeout=2).json().get("ok")
    except (httpx.HTTPError, ValueError):
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raiz-v16", required=True)
    ap.add_argument("--salida", default=str(ROOT / "docs" / "resultados" / "v1.7"))
    a = ap.parse_args()
    out = Path(a.salida)
    out.mkdir(parents=True, exist_ok=True)
    antes = medir("v1.6", Path(a.raiz_v16).resolve(), out)
    despues = medir("v1.7", ROOT, out)
    doc = {"etiqueta": "MEDIDO con navegador real (Chromium + Playwright) sobre procesos reales; datos ficticios",
           "objetivo": "dos reservaciones POR_DIA seguidas, desde el panel ya con sesión iniciada hasta el segundo folio",
           "antes_v1_6": antes, "despues_v1_7": despues,
           "reduccion": {"interacciones": antes["interacciones"] - despues["interacciones"],
                         "pct": round(100 * (antes["interacciones"] - despues["interacciones"]) / antes["interacciones"], 1)},
           "validaciones_conservadas": ["casilla «He revisado los campos»", "campos manuales tecleados por el operador",
                                        "casilla «Confirmo esta reservación»", "folio verificado por el portal"]}
    (out / "interacciones_operador.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(doc["reduccion"], ensure_ascii=False))
    print("antes", antes["interacciones"], antes["pasos"])
    print("despues", despues["interacciones"], despues["pasos"])
    return 0 if all(antes["folios_en_portal"] + despues["folios_en_portal"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
