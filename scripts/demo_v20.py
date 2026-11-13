"""Demostración VERIFICABLE del modo ventanilla (v2.0) con procesos reales y navegador real.

Arranca el portal de prueba (por día) y el motor (`python -m monitoreo`), crea un operador con
usuario y, con Playwright (scripts/recorrido_v20.js), atiende en la pantalla «Ventanilla»:
  1. SEIS personas seguidas: registrar -> datos -> día -> confirmar -> cerrar (meta diaria = 6);
  2. una persona SIN disponibilidad (la atención termina sin cita y el expediente vuelve a la cola);
  3. una persona que YA tenía expediente (duplicado detectado: se recupera el existente);
  4. una atención A MEDIAS; el motor se mata (kill) y se vuelve a arrancar; se RETOMA hasta el folio;
  5. la misma pantalla en un TELÉFONO (sin desborde horizontal).
Cada folio se comprueba en el portal (evidencia independiente). Cuenta interacciones y tiempos.
Uso: python scripts/demo_v20.py --salida docs/resultados/v2.0 --capturas docs/capturas/v2.0 [--bd URL]
"""
from __future__ import annotations

import argparse
import json
import platform
import secrets
import shutil
import statistics
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

PWD = "Demo-Ventanilla-2026"
OP_PWD = "Operador-Ventanilla-2026"
ZONA = "America/Mexico_City"
LETRAS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def login(base: str, u: str, p: str) -> httpx.Client:
    c = httpx.Client(base_url=base, timeout=30, trust_env=False)
    c.headers["Authorization"] = "Bearer " + c.post("/api/auth/login", json={"username": u, "password": p}).json()["token"]
    return c


def persona(portal, n: int) -> dict:
    reg = portal.registro(n)
    nombre = f"Persona Ventanilla {LETRAS[n % 26]}{LETRAS[(n // 26) % 26]}"
    return {"nombre": nombre, "fecha_nacimiento": reg["fecha_nacimiento"], "id_registro_previo": reg["identificador"],
            "telefono": f"55500030{n:02d}", "correo": f"v{n}@ejemplo.test", "tipo_tramite": reg["tramite"],
            "region": "NORTE", "sucursal": "NOR-1"}


def fase(node, motor, caps, out, nombre, personas, usuario="operador1", clave=None) -> tuple[bool, dict, str]:
    pj = out / f"personas_{nombre}.json"
    pj.write_text(json.dumps(personas, ensure_ascii=False), encoding="utf-8")
    p = subprocess.run([node, str(ROOT / "scripts" / "recorrido_v20.js"), motor.base, usuario, clave or OP_PWD, str(caps),
                        nombre, str(pj)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600)
    pj.unlink()                     # datos ficticios, pero no se dejan en los resultados
    with open(out / "recorrido_v20_playwright.log", "a", encoding="utf-8") as f:
        f.write(f"===== {nombre}\n{p.stdout}\n{p.stderr}\n")
    linea = next((x for x in p.stdout.splitlines() if x.startswith("RESULTADO ")), None)
    return p.returncode == 0 and linea is not None, json.loads(linea[10:]) if linea else {}, (p.stderr or p.stdout)[-600:]


def en_portal(portal, folio: str) -> str | None:
    r = httpx.get(portal.base + f"/api/reservaciones/{folio}", headers={"Authorization": f"Bearer {portal.token}"},
                  trust_env=False)
    return r.json().get("estado") if r.status_code == 200 else None


def run(a) -> dict:
    out = Path(a.salida)
    out.mkdir(parents=True, exist_ok=True)
    caps = Path(a.capturas)
    tmp = Path(tempfile.mkdtemp(prefix="demo20-"))
    ck = Checks()
    t0 = time.time()
    (tmp / "portal").mkdir(parents=True)
    portal = PortalProceso(tmp / "portal" / "estado.json", token=secrets.token_urlsafe(16),
                           token_control=secrets.token_urlsafe(16), log=out / "portal.log", extra=["--zona-horaria", ZONA])
    portal.iniciar()
    env = base_env(tmp, "desarrollo")
    env.update({"MONITOREO_DATOS_DEMO": "0", "MONITOREO_ADMIN_PASSWORD": PWD, "MONITOREO_PORTAL_URL": portal.base,
                "MONITOREO_PORTAL_TOKEN": portal.token, "MONITOREO_ZONA_HORARIA": ZONA})
    if a.bd:
        env["MONITOREO_DB_URL"] = a.bd
    motor = Server("motor", env, out)
    motor.start()
    ck("motor y portal arrancan como procesos independientes", True, f"{motor.wait_ready()} s")
    c = login(motor.base, "admin", PWD)
    c.put("/api/settings/engine", json={"captura_modo": "POR_DIA"}).raise_for_status()
    r = c.post("/api/sources", json={"code": "portal-lab-dias", "name": "Portal de prueba · por día", "region": "CENTRO",
                                     "availability_type": "calendario", "connector_type": "portal_dias",
                                     "config": {"url_base": portal.base, "token_env": "MONITOREO_PORTAL_TOKEN",
                                                "timeout_s": 5, "mecanismo": "periodico"},
                                     "interval_s": 5, "jitter_pct": 0, "rate_limit_per_min": 120, "max_retries": 1})
    ck("fuente del calendario registrada", r.status_code == 201, r.status_code)
    r = c.post("/api/users", json={"username": "operador1", "password": OP_PWD, "role": "operador", "regions": ["NORTE"],
                                   "sucursales": ["NOR-1", "NOR-2"], "operator_name": "Operador Ventanilla"})
    ck("operador de ventanilla con usuario", r.status_code == 201, r.status_code)
    node = shutil.which("node")
    if not node:
        ck("Node.js disponible para Playwright", False)
        return {"ok": False, "comprobaciones": ck.items}
    personas = [persona(portal, 40 + i) for i in range(12)]

    ok, seis, err = fase(node, motor, caps, out, "seis", personas[:6])
    ats = seis.get("atenciones", [])
    ck("navegador real: SEIS atenciones seguidas hasta el folio", ok and len(ats) == 6, err if not ok else len(ats))
    ck("con el servidor lento, la pantalla no se repinta y no borra lo que el operador ya tecleó",
       seis.get("repintado_borra") is False, seis.get("repintado_borra"))
    ck("las seis terminan como CITA CONFIRMADA en pantalla", ats and all(x["estado"] == "CONFIRMADA" for x in ats),
       [x.get("estado") for x in ats])
    estados = [en_portal(portal, x["folio"]) for x in ats]
    ck("evidencia independiente: los seis folios están CONFIRMADOS en el portal", estados == ["CONFIRMADA"] * 6, estados)
    ck("seis folios distintos (ninguna cita duplicada)", len({x["folio"] for x in ats}) == 6)
    ck("la meta del día (6) aparece cumplida", seis.get("citas_hoy") == 6, seis.get("citas_hoy"))

    ok, sc, err = fase(node, motor, caps, out, "sin_cita", personas[6:7])
    ck("atención SIN disponibilidad: termina sin cita y sin folio", ok and sc.get("estado") == "SIN_DISPONIBILIDAD"
       and not sc.get("folio_visible"), err if not ok else sc.get("estado"))
    exps = {e["codigo"]: e for e in c.get("/api/expedientes", params={"limit": 200}).json()}
    sin = [e for e in exps.values() if e["estado"] == "LISTO"]
    ck("su expediente vuelve a la cola del motor (LISTO, sin operador)", len(sin) == 1 and not sin[0].get("operador"),
       [(e["codigo"], e["estado"]) for e in sin])

    p8 = personas[7]
    antes = len(c.get("/api/expedientes", params={"limit": 500}).json())
    r = c.post("/api/expedientes", json={**{k: v for k, v in p8.items()}, "telefono_confirmacion": p8["telefono"],
                                         "correo_confirmacion": p8["correo"], "modo": "POR_DIA"})
    ck("alta previa de una persona (para el caso duplicado)", r.status_code == 201, r.status_code)
    previo = r.json().get("codigo")
    ok, du, err = fase(node, motor, caps, out, "duplicado", [p8])
    ck("duplicado detectado en ventanilla y se atiende el expediente EXISTENTE", ok and previo in (du.get("recuperado") or ""),
       err if not ok else (du.get("aviso") or "")[:100])
    ck("el expediente recuperado llega a su cita y el folio está CONFIRMADO en el portal",
       bool(du.get("folio")) and en_portal(portal, du["folio"]) == "CONFIRMADA", du.get("folio"))
    despues = len(c.get("/api/expedientes", params={"limit": 500}).json())
    ck("no se creó un segundo expediente para esa persona (solo el alta previa)", despues == antes + 1,
       f"{antes} -> {despues}, previo {previo}")

    ok, me, err = fase(node, motor, caps, out, "medias", personas[8:9])
    ck("atención a medias (datos enviados, calendario en pantalla) y navegador cerrado", ok, err if not ok else me.get("atencion"))
    motor.proc.kill()
    motor.proc.wait(10)
    motor.log.close()
    ck("el motor se detiene de golpe (kill) con la atención a medias", True)
    motor = Server("motor-reinicio", env, out)
    motor.start()
    ck("el motor vuelve a arrancar sobre los mismos datos", True, f"{motor.wait_ready()} s")
    ok, rt, err = fase(node, motor, caps, out, "retomar", [{"atencion": me.get("atencion")}])
    ck("«Retomar» vuelve al paso exacto (elegir día) y llega al folio", ok and rt.get("siguiente") == "fecha" and bool(rt.get("folio")),
       err if not ok else (rt.get("siguiente"), rt.get("folio")))
    if rt.get("folio"):
        ck("evidencia independiente: el folio retomado está CONFIRMADO en el portal", en_portal(portal, rt["folio"]) == "CONFIRMADA")

    ok, mv, err = fase(node, motor, caps, out, "movil", personas[9:10])
    ck("ventanilla en teléfono: atención completa y sin desborde horizontal", ok and mv.get("desborde") is False
       and len(mv.get("atenciones", [])) == 1, err if not ok else mv.get("desborde"))

    ok, cx, err = fase(node, motor, caps, out, "conexiones", [], usuario="admin", clave=PWD)
    ck("pantalla «Conexiones»: el portal de prueba es local y no se aparenta probar redes físicas",
       ok and cx.get("local") is True and cx.get("usos", 0) >= 1, err if not ok else cx)
    c = login(motor.base, "admin", PWD)
    with httpx.Client(base_url=motor.base, timeout=30, trust_env=False) as co:
        co.headers["Authorization"] = "Bearer " + co.post("/api/auth/login", json={"username": "operador1", "password": OP_PWD}).json()["token"]
        dia = co.get("/api/ventanilla").json()
    ck("resumen del día: 9 con cita, 1 sin cita, ninguna abierta", dia["mias"]["con_cita"] == 9 and dia["mias"]["sin_cita"] == 1
       and not dia["abiertas"], (dia["mias"]["con_cita"], dia["mias"]["sin_cita"], len(dia["abiertas"])))
    integ = c.get("/api/captura/coherencia").json()
    ck("coherencia del motor (sin estados contradictorios)", not integ.get("problemas"), (integ.get("problemas") or [])[:3])
    errores = sum((x.get("errores_js", []) for x in (seis, sc, du, me, rt, mv, cx)), [])
    ck("sin errores de JavaScript en el panel", not errores, errores[:5])
    http_err = {k: x.get("http_errores", []) for k, x in (("seis", seis), ("sin_cita", sc), ("duplicado", du), ("medias", me),
                                                           ("retomar", rt), ("movil", mv))}
    esperados = [e for e in http_err["duplicado"] if e.startswith("409 POST /api/ventanilla/atenciones/") and e.endswith("/expediente")]
    otros = [e for k, v in http_err.items() for e in v if not (k == "duplicado" and e in esperados)]
    ck("respuestas HTTP de error: solo UN 409, el esperado del duplicado (sin envíos dobles)", len(esperados) == 1 and not otros,
       {k: x.get("http_mensajes", []) for k, x in (("duplicado", du),)} if otros or len(esperados) != 1 else http_err["duplicado"])
    motor.stop()
    portal.detener()
    textos = "".join(p.read_text(encoding="utf-8", errors="replace") for p in out.glob("servidor-motor*.log"))
    fugas = [p["nombre"] for p in personas[:10] if p["nombre"] in textos] + [p["telefono"] for p in personas[:10] if p["telefono"] in textos]
    ck("sin datos personales en los registros del motor", not fugas, fugas[:3])
    inter = [x["interacciones"] for x in ats]
    segs = [x["segundos"] for x in ats]
    return {"version": "2.0.0-dev", "momento": time.strftime("%Y-%m-%d %H:%M:%S"), "plataforma": platform.platform(),
            "python": platform.python_version(), "duracion_s": round(time.time() - t0, 1),
            "base_de_datos": "postgresql" if a.bd else "sqlite",
            "por_atencion": {"interacciones": {"media": statistics.mean(inter) if inter else None, "min": min(inter, default=None),
                                               "max": max(inter, default=None)},
                             "segundos_navegador_automatizado": {"media": round(statistics.mean(segs), 1) if segs else None,
                                                                 "max": round(max(segs), 1) if segs else None},
                             "nota": "MEDIDO con un navegador automatizado: el tiempo de una persona real será mayor "
                                     "(teclear, hablar con el solicitante)."},
            "seis": seis, "sin_cita": sc, "duplicado": du, "retomar": rt, "movil": mv, "dia": dia,
            "ok": ck.ok, "comprobaciones": ck.items}


def main() -> int:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--salida", default="resultados/demo_v20")
    ap.add_argument("--capturas", default="docs/capturas/v2.0")
    ap.add_argument("--bd", default=None)
    a = ap.parse_args()
    rep = run(a)
    Path(a.salida, "demo_v20.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    for it in rep["comprobaciones"]:
        print(("OK   " if it["ok"] else "FALLA") + " " + it["comprobacion"] + (f" — {it['detalle']}" if it.get("detalle") else ""))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
