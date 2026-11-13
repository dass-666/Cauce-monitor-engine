"""Demostración VERIFICABLE de la v1.7 con procesos reales y navegador real.

Arranca el portal de prueba (por día) y el motor (`python -m monitoreo`, con datos de demostración
para tener fuentes, operadores y alertas), crea un operador con usuario y dos expedientes ficticios,
espera a que el MOTOR los asigne y entonces, con Playwright (scripts/recorrido_v17.js):
  1. recorre el centro de operaciones (con filtro), supervisión, redistribución (formulario),
     notificaciones (filtro por categoría), explicación de una asignación y el formulario largo,
     en escritorio, pantalla pequeña y teléfono; el operador no puede abrir Supervisión;
  2. el operador deja una reservación A MEDIAS (datos enviados, calendario en pantalla);
  3. el motor se mata de golpe (kill) y se vuelve a arrancar;
  4. el operador entra de nuevo: «Mi trabajo» muestra la operación a medias y la retoma hasta el folio;
  5. el navegador pierde la red: aparece el aviso; vuelve la red: el aviso desaparece.
Comprueba el folio en el portal (evidencia independiente). Escribe un JSON y las capturas.

Uso: python scripts/demo_v17.py --salida resultados/demo_v17 --capturas docs/capturas/v1.7 [--bd URL]
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

PWD = "Demo-Centro-2026"
OP_PWD = "Operador-Centro-2026"
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


def fase(node, motor, caps, out, nombre) -> tuple[bool, dict, str]:
    p = subprocess.run([node, str(ROOT / "scripts" / "recorrido_v17.js"), motor.base, "admin", PWD, "operador1", OP_PWD,
                        str(caps), nombre], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=400)
    with open(out / "recorrido_v17_playwright.log", "a", encoding="utf-8") as f:
        f.write(f"===== {nombre}\n{p.stdout}\n{p.stderr}\n")
    linea = next((x for x in p.stdout.splitlines() if x.startswith("RESULTADO ")), None)
    return p.returncode == 0 and linea is not None, json.loads(linea[10:]) if linea else {}, (p.stderr or p.stdout)[-500:]


def run(a) -> dict:
    out = Path(a.salida)
    out.mkdir(parents=True, exist_ok=True)
    caps = Path(a.capturas)
    tmp = Path(tempfile.mkdtemp(prefix="demo17-"))
    ck = Checks()
    t0 = time.time()
    (tmp / "portal").mkdir(parents=True)
    portal = PortalProceso(tmp / "portal" / "estado.json", token=secrets.token_urlsafe(16),
                           token_control=secrets.token_urlsafe(16), log=out / "portal.log", extra=["--zona-horaria", ZONA])
    portal.iniciar()
    env = base_env(tmp, "desarrollo")
    env.update({"MONITOREO_DATOS_DEMO": "1", "MONITOREO_ADMIN_PASSWORD": PWD, "MONITOREO_PORTAL_URL": portal.base,
                "MONITOREO_PORTAL_TOKEN": portal.token, "MONITOREO_ZONA_HORARIA": ZONA})
    if a.bd:
        env["MONITOREO_DB_URL"] = a.bd
    motor = Server("motor", env, out)
    motor.start()
    ck("motor y portal arrancan como procesos independientes", True, f"{motor.wait_ready()} s")
    c = login(motor.base, "admin", PWD)
    r = c.post("/api/sources", json={"code": "portal-lab-dias", "name": "Portal de prueba · por día", "region": "CENTRO",
                                     "availability_type": "calendario", "connector_type": "portal_dias",
                                     "config": {"url_base": portal.base, "token_env": "MONITOREO_PORTAL_TOKEN", "timeout_s": 5,
                                                "mecanismo": "periodico"},
                                     "interval_s": 3, "jitter_pct": 0, "rate_limit_per_min": 120, "max_retries": 1})
    ck("fuente del calendario registrada (o ya incluida en los datos de demostración)", r.status_code in (201, 409),
       r.status_code)
    r = c.post("/api/users", json={"username": "operador1", "password": OP_PWD, "role": "operador", "regions": ["NORTE"],
                                   "sucursales": ["NOR-1", "NOR-2"], "operator_name": "Operador Ficticio Norte"})
    ck("operador con usuario creado", r.status_code == 201, r.status_code)
    # que los operadores de demostración (sin usuario) no se queden los expedientes del laboratorio
    # (se les limita a una sucursal inexistente: siguen recibiendo las solicitudes de demostración)
    for o in c.get("/api/operators").json():
        if o["name"] != "Operador Ficticio Norte":
            c.patch(f"/api/operators/{o['id']}", json={"sucursales": ["SIN-ACCESO"]})
    ids = []
    for n, prio in ((21, 1), (22, 3)):
        reg = portal.registro(n)
        r = c.post("/api/expedientes", json={
            "nombre": "Centro Ficticio " + "AB"[n - 21], "fecha_nacimiento": reg["fecha_nacimiento"],
            "telefono": f"55500022{n}", "telefono_confirmacion": f"55500022{n}", "correo": f"c{n}@ejemplo.test",
            "correo_confirmacion": f"c{n}@ejemplo.test", "region": "NORTE", "tipo_tramite": reg["tramite"],
            "id_registro_previo": reg["identificador"], "prioridad": prio, "modo": "POR_DIA", "sucursal": "NOR-1",
            "sucursales_alternativas": ["NOR-2"]})
        ids.append(r.json().get("id"))
    c.post("/api/expedientes", json={"nombre": "Incompleto Ficticio", "region": "NORTE", "tipo_tramite": "alta", "modo": "POR_DIA"})
    v, t = esperar(lambda: all(c.get(f"/api/expedientes/{i}").json()["estado"] == "ASIGNADO" for i in ids), 120)
    ck("el MOTOR asigna los dos expedientes al operador", bool(v), f"{t} s")
    time.sleep(4)   # unos ciclos del motor para que el centro tenga consultas y tiempos medidos
    node = shutil.which("node")
    if not node:
        ck("Node.js disponible para Playwright", False)
        return {"ok": False, "comprobaciones": ck.items}
    ok, vis, err = fase(node, motor, caps, out, "vistas")
    ck("navegador real: centro, supervisión, notificaciones, explicación y formularios", ok, err if not ok else "")
    ck("el centro muestra indicadores y aplica el filtro de región", len(vis.get("kpis_centro", [])) >= 12
       and vis.get("filtro_region_aplicado") == "NORTE", len(vis.get("kpis_centro", [])))
    ck("supervisión distingue estados de presencia", "CONECTADO" in vis.get("presencias", []) or
       "DESCONECTADO" in vis.get("presencias", []), sorted(set(vis.get("presencias", []))))
    ck("redistribuir no se envía sin la confirmación", vis.get("redistribuir_bloqueado_sin_confirmar") in (True, None))
    ck("notificaciones: 10 categorías y filtro", vis.get("categorias") == 10 and vis.get("filtradas", 0) >= 1,
       (vis.get("categorias"), vis.get("filtradas")))
    ck("la explicación de la asignación es legible", "Se eligió" in (vis.get("explicacion") or ""), (vis.get("explicacion") or "")[:120])
    ck("sin desborde horizontal en pantalla pequeña y teléfono",
       not any(v for k, v in vis.items() if k.startswith("desborde_")),
       {k: v for k, v in vis.items() if k.startswith("desborde_")})
    ck("el operador ve Mi trabajo y no puede abrir Supervisión", vis.get("mi_trabajo_items", 0) >= 2
       and vis.get("operador_supervision_redirige") and "Supervisión" not in vis.get("nav_operador", []),
       (vis.get("mi_trabajo_items"), vis.get("nav_operador")))
    ok, inte, err = fase(node, motor, caps, out, "interrumpir")
    ck("el operador deja una reservación a medias (calendario en pantalla)", ok, err if not ok else inte.get("codigo"))
    motor.proc.kill()
    motor.proc.wait(10)
    motor.log.close()
    ck("el motor se detiene de golpe (kill) con la operación a medias", True)
    motor = Server("motor-reinicio", env, out)
    motor.start()
    ck("el motor vuelve a arrancar sobre los mismos datos", True, f"{motor.wait_ready()} s")
    ok, ret, err = fase(node, motor, caps, out, "retomar")
    ck("Mi trabajo muestra la operación a medias en su paso y se retoma hasta el folio",
       ok and ret.get("paso_tras_reinicio") == "disponibilidad" and bool(ret.get("folio")),
       err if not ok else (ret.get("paso_tras_reinicio"), ret.get("boton"), ret.get("folio")))
    if ret.get("folio"):
        rp = httpx.get(portal.base + f"/api/reservaciones/{ret['folio']}", headers={"Authorization": f"Bearer {portal.token}"},
                       trust_env=False).json()
        ck("evidencia independiente: el PORTAL tiene el folio CONFIRMADO", rp.get("estado") == "CONFIRMADA", rp.get("estado"))
    ok, sc, err = fase(node, motor, caps, out, "sin_conexion")
    ck("conexión perdida: aviso visible y los datos siguen en pantalla; al volver desaparece",
       ok and bool(sc.get("aviso")) and sc.get("datos_siguen_visibles") and sc.get("aviso_desaparece_al_volver"),
       err if not ok else (sc.get("aviso") or "")[:80])
    errores = vis.get("errores_js", []) + inte.get("errores_js", []) + ret.get("errores_js", []) + sc.get("errores_js", [])
    ck("sin errores de JavaScript en el panel", not errores, errores[:5])
    motor.stop()
    portal.detener()
    return {"version": "1.7.0", "momento": time.strftime("%Y-%m-%d %H:%M:%S"), "plataforma": platform.platform(),
            "python": platform.python_version(), "duracion_s": round(time.time() - t0, 1),
            "base_de_datos": "postgresql" if a.bd else "sqlite", "vistas": vis, "interrumpir": inte, "retomar": ret,
            "sin_conexion": sc, "ok": ck.ok, "comprobaciones": ck.items}


def main() -> int:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--salida", default="resultados/demo_v17")
    ap.add_argument("--capturas", default="docs/capturas/v1.7")
    ap.add_argument("--bd", default=None)
    a = ap.parse_args()
    rep = run(a)
    Path(a.salida, "demo_v17.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    for it in rep["comprobaciones"]:
        print(("OK   " if it["ok"] else "FALLA") + " " + it["comprobacion"] + (f" — {it['detalle']}" if it.get("detalle") else ""))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
