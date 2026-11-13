"""Demostración VERIFICABLE de la v1.5: de un expediente ficticio a un folio ficticio.

Arranca, como procesos independientes y con red real en 127.0.0.1:
  1. el portal de reservaciones de PRUEBA (portal_reservas),
  2. el motor completo (`python -m monitoreo`, con su planificador),
y entonces:
  3. crea un operador y un expediente ficticio por la API (como haría un administrador),
  4. espera a que el MOTOR detecte disponibilidad y asigne el expediente (sin intervención),
  5. ejecuta el recorrido del operador en el panel con Playwright (Node) hasta el folio,
  6. comprueba el folio en la API del portal (evidencia independiente del motor),
  7. mata el motor y el portal de golpe (kill), los vuelve a arrancar sobre los mismos datos y
     comprueba que el folio sigue CONFIRMADO en ambos y que otro expediente llega a folio,
  8. deja un informe JSON y las capturas de pantalla.

Uso:  python scripts/demo_reservacion.py --salida resultados/demo --capturas docs/capturas/v1.5 [--bd URL]
Código de salida 0 solo si todas las comprobaciones pasan. Todo es ficticio y local.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
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
from verificar_arranque import Checks, Server, base_env  # noqa: E402

PWD = "Demo-Reservacion-2026"
OP_PWD = "Operador-Demo-2026"


def esperar(fn, timeout: float, paso: float = 0.5):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        v = fn()
        if v:
            return v, round(time.monotonic() - t0, 2)
        time.sleep(paso)
    return None, round(time.monotonic() - t0, 2)


def expediente_api(c: httpx.Client, reg: dict, n: int) -> dict:
    letras = "".join(chr(65 + int(d)) for d in f"{n:03d}")
    return {"nombre": f"Demostracion Ficticia {letras.capitalize()}", "fecha_nacimiento": reg["fecha_nacimiento"],
            "telefono": f"555{n:07d}", "telefono_confirmacion": f"555{n:07d}", "correo": f"demo{n}@ejemplo.test",
            "correo_confirmacion": f"demo{n}@ejemplo.test", "region": "NORTE", "tipo_tramite": reg["tramite"],
            "id_registro_previo": reg["identificador"], "prioridad": 2}


def run(a) -> dict:
    out = Path(a.salida)
    out.mkdir(parents=True, exist_ok=True)
    caps = Path(a.capturas)
    tmp = Path(tempfile.mkdtemp(prefix="demo15-"))
    ck = Checks()
    t_inicio = time.time()
    portal = PortalProceso(tmp / "portal" / "estado.json", token=secrets.token_urlsafe(16),
                           token_control=secrets.token_urlsafe(16), log=out / "portal.log")
    (tmp / "portal").mkdir(parents=True)
    portal.iniciar()
    ck("el portal de prueba arranca como proceso independiente", True, portal.base)
    env = base_env(tmp, "desarrollo")
    env.update({"MONITOREO_DATOS_DEMO": "0", "MONITOREO_ADMIN_PASSWORD": PWD, "MONITOREO_PORTAL_URL": portal.base,
                "MONITOREO_PORTAL_TOKEN": portal.token})
    if a.bd:
        env["MONITOREO_DB_URL"] = a.bd
    motor = Server("motor", env, out)
    motor.start()
    ck("el motor arranca como proceso independiente", True, f"{motor.wait_ready()} s")
    c = httpx.Client(base_url=motor.base, timeout=30, trust_env=False)
    c.headers["Authorization"] = "Bearer " + c.post("/api/auth/login", json={"username": "admin", "password": PWD}).json()["token"]
    r = c.post("/api/sources", json={"code": "portal-lab", "name": "Portal de reservaciones de prueba", "region": "CENTRO",
                                     "availability_type": "calendario", "connector_type": "portal_reservas",
                                     "config": {"url_base": portal.base, "token_env": "MONITOREO_PORTAL_TOKEN", "timeout_s": 5},
                                     "interval_s": 2, "jitter_pct": 0, "rate_limit_per_min": 120, "max_retries": 1})
    ck("fuente del portal registrada por la API", r.status_code == 201, r.status_code)
    r = c.post("/api/users", json={"username": "operador1", "password": OP_PWD, "role": "operador", "regions": ["NORTE"],
                                   "operator_name": "Operador Ficticio Norte"})
    ck("operador creado", r.status_code == 201, r.status_code)

    reg = portal.registro(7)
    r = c.post("/api/expedientes", json=expediente_api(c, reg, 7))
    ck("expediente ficticio creado", r.status_code == 201, r.json().get("codigo") if r.status_code == 201 else r.text)
    exp = r.json()
    ck("el alta devuelve los datos enmascarados", "•" in exp["telefono"] and "•" in exp["nombre"], exp["telefono"])

    def asignado():
        e = c.get(f"/api/expedientes/{exp['id']}").json()
        return e if e["estado"] == "ASIGNADO" else None
    e, t = esperar(asignado, 60)
    ck("el MOTOR detecta disponibilidad y asigna el expediente solo", bool(e), f"{t} s, operador {e and e['operador']}")

    # ---------------------------------------------------------------- recorrido con navegador real
    node = shutil.which("node")
    reg2 = portal.registro(11)
    res_ui = {}
    if not node:
        ck("Node.js disponible para Playwright", False, "no se encontró node")
    else:
        p = subprocess.run([node, str(ROOT / "scripts" / "recorrido_reservacion.js"), motor.base, portal.base,
                            "operador1", OP_PWD, "admin", PWD, exp["codigo"], str(caps), json.dumps(reg2)],
                           cwd=ROOT, capture_output=True, text=True, timeout=300)
        (out / "recorrido_playwright.log").write_text(p.stdout + "\n" + p.stderr, encoding="utf-8")
        linea = next((x for x in p.stdout.splitlines() if x.startswith("RESULTADO ")), None)
        ck("recorrido Playwright: expediente → formulario → calendario → horario → confirmación → folio",
           p.returncode == 0 and linea is not None, (p.stderr or p.stdout)[-400:] if p.returncode else "")
        if linea:
            res_ui = json.loads(linea[len("RESULTADO "):])
            ck("el panel mostró un folio", bool(res_ui.get("folio")), res_ui.get("folio"))
            ck("sin errores de JavaScript en el panel", not res_ui.get("errores_js"), res_ui.get("errores_js"))
            ck("el panel no desborda en pantalla de teléfono", not res_ui.get("desborde_horizontal_movil"))
            ck("el portal se llena directamente desde el contrato JSON (otra navegación, mismo contrato)",
               bool(res_ui.get("folio_portal_directo")), res_ui.get("folio_portal_directo"))
    folio = res_ui.get("folio")
    api_portal = httpx.Client(base_url=portal.base, timeout=10, trust_env=False,
                              headers={"Authorization": f"Bearer {portal.token}"})
    if folio:
        rp = api_portal.get(f"/api/reservaciones/{folio}").json()
        ck("evidencia independiente: el PORTAL tiene el folio CONFIRMADO", rp.get("estado") == "CONFIRMADA", rp.get("estado"))
        f = c.get(f"/api/expedientes/{exp['id']}").json()
        res = next((x for x in f["reservaciones"] if x["folio"] == folio), {})
        ck("el motor guarda el folio con evidencia y clave coincidente",
           res.get("estado") == "CONFIRMADA" and res.get("evidencia", {}).get("clave_coincide") is True,
           res.get("evidencia"))
        tipos = [x["tipo"] for x in f["historial"]]
        faltan = [t for t in ("DISPONIBILIDAD_DETECTADA", "FORMULARIO_PREPARADO", "RECURSO_SELECCIONADO",
                              "RESERVACION_PENDIENTE", "RESERVACION_CONFIRMADA") if t not in tipos]
        ck("trazabilidad: eventos normalizados en el historial del expediente", not faltan, faltan or tipos)
        ck("el expediente queda CONFIRMADO", f["estado"] == "CONFIRMADO", f["estado"])

    # ---------------------------------------------------------------- reinicio brusco de ambos procesos
    motor.proc.kill()
    motor.proc.wait(10)
    motor.log.close()
    portal.detener(forzar=True)
    ck("motor y portal detenidos de golpe (kill)", True)
    portal.iniciar()
    motor = Server("motor-reinicio", env, out)
    motor.start()
    ck("ambos vuelven a arrancar sobre los mismos datos", True, f"motor {motor.wait_ready()} s")
    c = httpx.Client(base_url=motor.base, timeout=30, trust_env=False)
    c.headers["Authorization"] = "Bearer " + c.post("/api/auth/login", json={"username": "admin", "password": PWD}).json()["token"]
    if folio:
        f = c.get(f"/api/expedientes/{exp['id']}").json()
        rp = api_portal.get(f"/api/reservaciones/{folio}").json()
        ck("tras el reinicio el folio sigue CONFIRMADO en motor y portal",
           f["estado"] == "CONFIRMADO" and rp.get("estado") == "CONFIRMADA", (f["estado"], rp.get("estado")))
    # otro expediente de principio a fin por la API después del reinicio
    r = c.post("/api/expedientes", json=expediente_api(c, portal.registro(13), 13))
    exp2 = r.json()
    e2, t2 = esperar(lambda: (lambda e: e if e["estado"] == "ASIGNADO" else None)(c.get(f"/api/expedientes/{exp2['id']}").json()), 60)
    ck("tras el reinicio el motor asigna un expediente nuevo", bool(e2), f"{t2} s")
    folio2 = None
    if e2:
        op = httpx.Client(base_url=motor.base, timeout=30, trust_env=False)
        op.headers["Authorization"] = "Bearer " + op.post("/api/auth/login", json={"username": "operador1", "password": OP_PWD}).json()["token"]
        rid = op.post(f"/api/expedientes/{exp2['id']}/preparar").json()["reservacion"]["id"]
        cal = op.post(f"/api/reservaciones/{rid}/enviar-datos", json={"revisado": True}).json()["calendario"]
        op.post(f"/api/reservaciones/{rid}/seleccionar", json={"horario": cal["horarios"][0]["id"]}).raise_for_status()
        rr = op.post(f"/api/reservaciones/{rid}/confirmar", json={"confirmo": True})
        folio2 = rr.json().get("reservacion", {}).get("folio")
        ck("segundo expediente llega a folio tras el reinicio", rr.status_code == 200 and bool(folio2), folio2)
        ck("folios únicos", folio2 != folio, (folio, folio2))
    estado_portal = portal.ctl("GET", "/control/estado")
    ocup = portal.ctl("GET", "/control/reservaciones")
    dobles = [h for h, n in ocup["ocupacion"].items() if n > 1]
    ck("ningún horario del portal quedó reservado dos veces", not dobles, dobles)
    motor.stop()
    portal.detener()
    return {"version": "1.5.0", "momento": time.strftime("%Y-%m-%d %H:%M:%S"), "plataforma": platform.platform(),
            "python": platform.python_version(), "duracion_s": round(time.time() - t_inicio, 1),
            "base_de_datos": "postgresql" if a.bd else "sqlite", "expediente": exp["codigo"], "folio": folio,
            "folio_tras_reinicio": folio2, "recorrido_panel": res_ui, "portal": {k: estado_portal[k] for k in
                                                                                 ("confirmadas", "canceladas", "retenciones")},
            "ok": ck.ok, "comprobaciones": ck.items}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--salida", default="resultados/demo_reservacion")
    ap.add_argument("--capturas", default="docs/capturas/v1.5")
    ap.add_argument("--bd", default=None, help="URL de PostgreSQL (por defecto SQLite temporal)")
    a = ap.parse_args()
    rep = run(a)
    Path(a.salida, "demo_reservacion.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'TODO CORRECTO' if rep['ok'] else 'HAY FALLOS'}: {sum(i['ok'] for i in rep['comprobaciones'])}/"
          f"{len(rep['comprobaciones'])} comprobaciones · folio {rep['folio']}")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
