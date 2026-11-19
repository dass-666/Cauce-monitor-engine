"""Demostración VERIFICABLE de la v1.6 (reservación POR DÍA): de un expediente ficticio a un folio.

Arranca, como procesos independientes y con red real en 127.0.0.1:
  1. el portal de PRUEBA (inventario por día, zona America/Mexico_City),
  2. el motor completo (`python -m monitoreo`, con su planificador y el oyente de eventos),
y entonces:
  3. crea un operador y un expediente POR_DIA por la API; el motor lo valida (LISTO);
  4. espera a que el MOTOR detecte cupo en el calendario y lo asigne (sin intervención);
  5. recorrido del operador en el PANEL con Playwright: preparación -> campos manuales
     (tecleados) -> calendario mensual -> día -> confirmación humana -> folio;
  6. comprueba el folio en la API del portal (evidencia independiente del motor);
  7. mata el motor y el portal de golpe (kill), los vuelve a arrancar y comprueba que el folio
     se recupera en ambos y en el panel;
  8. corta la API del calendario y comprueba que el panel muestra DESCONOCIDO (no "agotado");
  9. deja un informe JSON y las capturas.

Uso:  python scripts/demo_dia.py --salida resultados/demo_dia --capturas docs/capturas/v1.6 [--bd URL]
Código de salida 0 solo si todas las comprobaciones pasan. Todo es ficticio y local.
"""
from __future__ import annotations

import argparse
import json
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
ZONA = "America/Mexico_City"


def esperar(fn, timeout: float, paso: float = 0.5):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        v = fn()
        if v:
            return v, round(time.monotonic() - t0, 2)
        time.sleep(paso)
    return None, round(time.monotonic() - t0, 2)


def login(base: str, u: str, p: str) -> httpx.Client:
    c = httpx.Client(base_url=base, timeout=30, trust_env=False)
    c.headers["Authorization"] = "Bearer " + c.post("/api/auth/login", json={"username": u, "password": p}).json()["token"]
    return c


def node_fase(node, motor, caps, out, codigo, fase) -> tuple[bool, dict, str]:
    p = subprocess.run([node, str(ROOT / "scripts" / "recorrido_dia.js"), motor.base, "operador1", OP_PWD, "admin", PWD,
                        codigo, str(caps), fase], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=300)
    with open(out / "recorrido_dia_playwright.log", "a", encoding="utf-8") as f:
        f.write(f"===== {fase}\n{p.stdout}\n{p.stderr}\n")
    linea = next((x for x in p.stdout.splitlines() if x.startswith("RESULTADO ")), None)
    return p.returncode == 0 and linea is not None, json.loads(linea[10:]) if linea else {}, (p.stderr or p.stdout)[-400:]


def run(a) -> dict:
    out = Path(a.salida)
    out.mkdir(parents=True, exist_ok=True)
    caps = Path(a.capturas)
    tmp = Path(tempfile.mkdtemp(prefix="demo16-"))
    ck = Checks()
    t0 = time.time()
    (tmp / "portal").mkdir(parents=True)
    portal = PortalProceso(tmp / "portal" / "estado.json", token=secrets.token_urlsafe(16),
                           token_control=secrets.token_urlsafe(16), log=out / "portal.log",
                           extra=["--zona-horaria", ZONA])
    portal.iniciar()
    portal.ctl("POST", "/control/api", latido_s=1)
    ck("el portal de prueba (por día) arranca como proceso independiente", True, portal.base)
    env = base_env(tmp, "desarrollo")
    env.update({"MONITOREO_DATOS_DEMO": "0", "MONITOREO_ADMIN_PASSWORD": PWD, "MONITOREO_PORTAL_URL": portal.base,
                "MONITOREO_PORTAL_TOKEN": portal.token, "MONITOREO_ZONA_HORARIA": ZONA})
    if a.bd:
        env["MONITOREO_DB_URL"] = a.bd
    motor = Server("motor", env, out)
    motor.start()
    ck("el motor arranca como proceso independiente", True, f"{motor.wait_ready()} s")
    c = login(motor.base, "admin", PWD)
    r = c.post("/api/sources", json={"code": "portal-lab-dias", "name": "Portal de prueba · calendario por día",
                                     "region": "CENTRO", "availability_type": "calendario", "connector_type": "portal_dias",
                                     "config": {"url_base": portal.base, "token_env": "MONITOREO_PORTAL_TOKEN",
                                                "timeout_s": 5, "mecanismo": "eventos", "latido_s": 1},
                                     "interval_s": 3, "jitter_pct": 0, "rate_limit_per_min": 120, "max_retries": 1})
    ck("fuente del calendario por día registrada (mecanismo A: eventos)", r.status_code == 201, r.status_code)
    r = c.post("/api/users", json={"username": "operador1", "password": OP_PWD, "role": "operador", "regions": ["NORTE"],
                                   "sucursales": ["NOR-1", "NOR-2"], "operator_name": "Operador Ficticio Norte"})
    ck("operador creado (limitado a NOR-1 y NOR-2)", r.status_code == 201, r.status_code)
    reg = portal.registro(7)
    d = {"nombre": "Demostracion Ficticia Dia", "fecha_nacimiento": reg["fecha_nacimiento"], "telefono": "5550000707",
         "telefono_confirmacion": "5550000707", "correo": "demo7@ejemplo.test", "correo_confirmacion": "demo7@ejemplo.test",
         "region": "NORTE", "tipo_tramite": reg["tramite"], "id_registro_previo": reg["identificador"], "prioridad": 2,
         "modo": "POR_DIA", "sucursal": "NOR-1", "sucursales_alternativas": ["NOR-2"]}
    r = c.post("/api/expedientes", json=d)
    exp = r.json()
    ck("expediente POR_DIA creado y validado (LISTO)", r.status_code == 201 and exp.get("estado") == "LISTO",
       exp.get("estado") if r.status_code == 201 else r.text[:200])
    inc = c.post("/api/expedientes", json={"nombre": "Incompleto Ficticio Dia", "region": "NORTE", "tipo_tramite": "alta",
                                           "modo": "POR_DIA"}).json()
    ck("un expediente incompleto queda INCOMPLETO", inc.get("estado") == "INCOMPLETO", inc.get("faltan"))

    def asignado():
        e = c.get(f"/api/expedientes/{exp['id']}").json()
        return e if e["estado"] == "ASIGNADO" else None
    e, t = esperar(asignado, 90)
    ck("el MOTOR detecta cupo en el calendario y asigna el expediente solo", bool(e), f"{t} s")
    def por_eventos():
        v = c.get("/api/captura/calendario-dias").json()
        f = v["fuentes"][0] if v["fuentes"] else {}
        return f if f.get("mecanismo") == "EVENTOS" and f.get("conectado") and not f.get("sin_verificar") else None
    f0, t = esperar(por_eventos, 30)     # la primera lectura es una consulta completa; luego conecta el oyente
    ck("calendario del motor verificado por eventos (oyente conectado)", bool(f0), f"{t} s" if f0 else
       c.get("/api/captura/calendario-dias").json()["fuentes"][:1])
    ck("un incompleto NUNCA se asigna", c.get(f"/api/expedientes/{inc['id']}").json()["estado"] == "INCOMPLETO")

    node = shutil.which("node")
    res_ui, res_ui2, res_ui3 = {}, {}, {}
    if not node:
        ck("Node.js disponible para Playwright", False, "no se encontró node")
    else:
        ok, res_ui, err = node_fase(node, motor, caps, out, exp["codigo"], "flujo")
        ck("navegador real: preparación -> manuales -> calendario diario -> día -> confirmación -> folio", ok, err if not ok else "")
        ck("el campo manual mal tecleado se marca y lo demás se conserva",
           bool(res_ui.get("error_campo_manual")) and res_ui.get("casilla_conservada") is True, res_ui.get("error_campo_manual"))
        ck("el campo que el portal no deja pegar está marcado como 'sin pegar'", res_ui.get("sin_pegar") == "1")
        ck("el calendario mensual muestra días disponibles (sin horas)", (res_ui.get("dias_disponibles") or 0) > 0,
           res_ui.get("dias_disponibles"))
        ck("el panel mostró un folio con la fecha elegida",
           bool(res_ui.get("folio")) and res_ui.get("fecha_confirmada") == res_ui.get("fecha_elegida"),
           (res_ui.get("folio"), res_ui.get("fecha_confirmada")))
    folio = res_ui.get("folio")
    api_portal = httpx.Client(base_url=portal.base, timeout=10, trust_env=False,
                              headers={"Authorization": f"Bearer {portal.token}"})
    if folio:
        rp = api_portal.get(f"/api/reservaciones/{folio}").json()
        ck("evidencia independiente: el PORTAL tiene el folio CONFIRMADO y sin hora",
           rp.get("estado") == "CONFIRMADA" and rp.get("hora") is None and rp.get("modo") == "dia", rp.get("estado"))
        f = c.get(f"/api/expedientes/{exp['id']}").json()
        ck("el expediente queda RESERVACION_CONFIRMADA", f["estado"] == "RESERVACION_CONFIRMADA", f["estado"])
        clave_tecleada = "KLM305918"
        ck("la clave de identidad tecleada NO quedó en el motor", clave_tecleada not in json.dumps(f, ensure_ascii=False))

    # ---------------------------------------------------------------- reinicio brusco de ambos procesos
    motor.proc.kill()
    motor.proc.wait(10)
    motor.log.close()
    portal.detener(forzar=True)
    ck("motor y portal detenidos de golpe (kill)", True)
    portal.iniciar()
    portal.ctl("POST", "/control/api", latido_s=1)
    motor = Server("motor-reinicio", env, out)
    motor.start()
    ck("ambos vuelven a arrancar sobre los mismos datos", True, f"motor {motor.wait_ready()} s")
    c = login(motor.base, "admin", PWD)
    if folio:
        f = c.get(f"/api/expedientes/{exp['id']}").json()
        rp = api_portal.get(f"/api/reservaciones/{folio}").json()
        ck("tras el reinicio el folio se recupera en motor y portal",
           f["estado"] == "RESERVACION_CONFIRMADA" and rp.get("estado") == "CONFIRMADA", (f["estado"], rp.get("estado")))
        if node:
            ok, res_ui2, err = node_fase(node, motor, caps, out, exp["codigo"], "tras_reinicio")
            ck("navegador real: el folio se ve en el panel tras el reinicio", ok and res_ui2.get("folio_tras_reinicio") == folio,
               err if not ok else res_ui2.get("folio_tras_reinicio"))
            ck("el panel no desborda en pantalla de teléfono",
               not res_ui2.get("desborde_movil_resumen") and not res_ui2.get("desborde_movil_calendario"))
    # ---------------------------------------------------------------- el calendario se queda sin comunicación
    _, t_v = esperar(lambda: not c.get("/api/captura/calendario-dias").json()["fuentes"][0]["sin_verificar"], 30)
    portal.ctl("POST", "/control/api", caida=True)
    v, t_d = esperar(lambda: (lambda x: x if x["fuentes"][0]["sin_verificar"] else None)(
        c.get("/api/captura/calendario-dias").json()), 40)
    ck("sin comunicación: el motor lo nota y lo marca SIN VERIFICAR", bool(v), f"{t_d} s")
    if v:
        estados = {d["estado"] for s in v["sucursales"] for ds in s["tramites"].values() for d in ds}
        ck("sin comunicación los días se muestran DESCONOCIDO (no 'agotado' ni 'disponible')",
           "DESCONOCIDO" in estados and "DISPONIBLE" not in estados, sorted(estados))
        if node:
            ok, res_ui3, err = node_fase(node, motor, caps, out, exp["codigo"], "sin_conexion")
            ck("navegador real: aviso de calendario sin comunicación", ok and res_ui3.get("dias_disponibles_mostrados") == 0,
               err if not ok else res_ui3.get("banner_sin_conexion"))
    portal.ctl("POST", "/control/api", caida=False)
    _, t_r = esperar(lambda: not c.get("/api/captura/calendario-dias").json()["fuentes"][0]["sin_verificar"], 60)
    ck("al volver la comunicación el calendario vuelve a estar verificado", t_r < 60, f"{t_r} s")
    errores_js = (res_ui.get("errores_js") or []) + (res_ui2.get("errores_js") or []) + (res_ui3.get("errores_js") or [])
    # El 422 del primer envío es INTENCIONAL (clave mal tecleada a propósito) y lo registra el navegador
    # como recurso fallido; se cuenta aparte para no ocultarlo. Cualquier otro error sí es falla.
    esperados = [x for x in errores_js if "status of 422" in x]
    otros = [x for x in errores_js if "status of 422" not in x]
    ck("sin errores de JavaScript en el panel (aparte del 422 provocado a propósito)", not otros and len(esperados) <= 1,
       {"otros": otros[:5], "422_esperados": len(esperados)})
    motor.stop()
    portal.detener()
    return {"version": "1.6.0", "momento": time.strftime("%Y-%m-%d %H:%M:%S"), "plataforma": platform.platform(),
            "python": platform.python_version(), "duracion_s": round(time.time() - t0, 1),
            "base_de_datos": "postgresql" if a.bd else "sqlite", "expediente": exp.get("codigo"), "folio": folio,
            "recorrido_panel": res_ui, "tras_reinicio": res_ui2, "sin_conexion": res_ui3,
            "segundos_hasta_detectar_caida": t_d, "segundos_hasta_reverificar": t_r,
            "ok": ck.ok, "comprobaciones": ck.items}


def main() -> int:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--salida", default="resultados/demo_dia")
    ap.add_argument("--capturas", default="docs/capturas/v1.6")
    ap.add_argument("--bd", default=None, help="URL de PostgreSQL (por defecto SQLite temporal)")
    a = ap.parse_args()
    rep = run(a)
    Path(a.salida).mkdir(parents=True, exist_ok=True)
    Path(a.salida, "demo_dia.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    for it in rep["comprobaciones"]:
        print(("OK   " if it["ok"] else "FALLA") + " " + it["comprobacion"] + (f" — {it['detalle']}" if it.get("detalle") else ""))
    print("RESULTADO:", "TODO OK" if rep["ok"] else "HAY FALLAS")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
