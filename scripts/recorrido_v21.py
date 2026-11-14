"""v2.1 · Recorrido INTEGRADO y VERIFICABLE del planificador diario con procesos y navegador reales.

Procesos: portal de laboratorio (`python -m portal_reservas`) y motor (`python -m monitoreo`, con su
planificador en marcha). El observador (Node + Playwright/Chromium) lo lanza el propio motor por su
API y mantiene ABIERTA la vista en vivo del calendario del laboratorio.

  1. Plan del día: 6 expedientes ficticios elegidos por prioridad; un incompleto rechazado; ampliar a 8.
  2. Sin disponibilidad: calendario abierto, ningún día disponible -> los 8 siguen EN ESPERA.
  3. El laboratorio habilita fechas nuevas (5 rondas): evento detectado por DOM y por imagen,
     coincidencia determinista, explicación. Se mide la latencia de cada paso.
  4. Operación preparada -> datos manuales -> día -> CONFIRMACIÓN HUMANA DE LABORATORIO (en esta
     corrida automática la «persona» es este script: está SIMULADA) -> folio comprobado en el portal.
  5. Cambio que desaparece tras asignarse: el expediente vuelve a EN ESPERA.
  6. Parpadeos (un día que aparece y desaparece en 150 ms y en 1,5 s): qué estrategia los ve.
  7. Motor matado (kill) y vuelto a arrancar: el plan sigue igual y no se duplican eventos.
  8. (Opcional, --pg-parar/--pg-arrancar) PostgreSQL caído mientras se habilita un día.

Uso: python scripts/recorrido_v21.py --salida docs/resultados/v2.1 [--bd URL --pg-parar CMD --pg-arrancar CMD]
Salida: <salida>/recorrido_v21.json (código 0 si todas las comprobaciones pasan).
"""
from __future__ import annotations

import argparse
import calendar
import json
import platform
import secrets
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from portal_reservas.proceso import PortalProceso  # noqa: E402
from verificar_arranque import Checks, Server, base_env  # noqa: E402

PWD = "Recorrido-Planificador-2026"
OP_PWD = "Operador-Planificador-2026"
ZONA = "America/Mexico_City"
MANUALES = {"clave_identidad": "QXZ482915", "acepto_privacidad": "si"}     # ficticios
LETRAS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def login(base: str, u: str, p: str) -> httpx.Client:
    c = httpx.Client(base_url=base, timeout=30, trust_env=False)
    c.headers["Authorization"] = "Bearer " + c.post("/api/auth/login", json={"username": u, "password": p}).json()["token"]
    return c


def esperar(pred, t=30.0, paso=0.05):
    t0 = time.monotonic()
    while time.monotonic() - t0 < t:
        try:
            v = pred()
        except (httpx.HTTPError, ValueError, KeyError, IndexError):
            v = None
        if v:
            return v
        time.sleep(paso)
    return None


def iso_a_epoch(s: str) -> float:
    return datetime.fromisoformat(s.rstrip("Z")).replace(tzinfo=__import__("datetime").timezone.utc).timestamp()


def resumen(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    xs = sorted(xs)
    return {"n": len(xs), "mediana_ms": round(statistics.median(xs)), "p95_ms": round(xs[min(len(xs) - 1, int(0.95 * len(xs)))]),
            "max_ms": round(xs[-1]), "min_ms": round(xs[0])}


def run(a) -> dict:
    out = Path(a.salida)
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="rec21-"))
    ck = Checks()
    t_inicio = time.time()
    portal = PortalProceso(tmp / "portal.json", token=secrets.token_urlsafe(16), token_control=secrets.token_urlsafe(16),
                           log=out / "portal_v21.log", extra=["--zona-horaria", ZONA])
    portal.iniciar()
    env = base_env(tmp, "desarrollo")
    env.update({"MONITOREO_DATOS_DEMO": "0", "MONITOREO_ADMIN_PASSWORD": PWD, "MONITOREO_PORTAL_URL": portal.base,
                "MONITOREO_PORTAL_TOKEN": portal.token, "MONITOREO_ZONA_HORARIA": ZONA})
    if a.bd:
        env["MONITOREO_DB_URL"] = a.bd
    motor = Server("motor-v21", env, out)
    motor.start()
    metricas: dict = {"rondas": [], "parpadeos": []}
    try:
        ck("motor y portal arrancan como procesos independientes", True, f"{motor.wait_ready()} s")
        c = login(motor.base, "admin", PWD)
        c.put("/api/settings/engine", json={"captura_modo": "POR_DIA"}).raise_for_status()
        r = c.post("/api/users", json={"username": "operador1", "password": OP_PWD, "role": "operador", "regions": ["NORTE"],
                                       "sucursales": ["NOR-1", "NOR-2"], "operator_name": "Operador Planificador"})
        ck("operador con usuario", r.status_code == 201, r.status_code)
        op = login(motor.base, "operador1", OP_PWD)
        ctl = lambda m, ruta, **kw: portal.ctl(m, ruta, **kw)  # noqa: E731
        dias = ctl("GET", "/control/dias")
        hoy = date.fromisoformat(dias["hoy"])
        ocupadas = {x["fecha"] for x in dias["dias"].values() if x["sucursal"] == "NOR-1" and x["tramite"] == "alta"}
        # mes observado: el primero (a partir de +20 días) sin ningún día de NOR-1/alta en el laboratorio
        m0 = hoy + timedelta(days=20)
        mes = None
        for k in range(0, 8):
            y, m = m0.year + (m0.month - 1 + k) // 12, (m0.month - 1 + k) % 12 + 1
            if not any(f.startswith(f"{y:04d}-{m:02d}") for f in ocupadas):
                mes = (y, m)
                break
        ck("hay un mes sin días habilitados para observar", mes is not None, mes)
        y, m = mes
        ult = calendar.monthrange(y, m)[1]
        libres = [date(y, m, d).isoformat() for d in range(1, ult + 1)]

        # ---------------------------------------------------------------- 1. plan del día
        prios = [3, 1, 2, 5, 1, 4, 2, 3]
        exps, n = [], 40
        while len(exps) < 8 and n < 600:
            reg = portal.registro(n)
            if reg["tramite"] == "alta":
                d = {"nombre": f"Persona Plan {LETRAS[len(exps)]}{LETRAS[n % 26]}", "fecha_nacimiento": reg["fecha_nacimiento"],
                     "telefono": f"55500041{len(exps):02d}", "telefono_confirmacion": f"55500041{len(exps):02d}",
                     "correo": f"plan{len(exps)}@ejemplo.test", "correo_confirmacion": f"plan{len(exps)}@ejemplo.test",
                     "region": "NORTE", "sucursal": "NOR-1", "sucursales_alternativas": ["NOR-2"], "tipo_tramite": "alta",
                     "id_registro_previo": reg["identificador"], "prioridad": prios[len(exps)]}
                r = c.post("/api/expedientes", json=d)
                if r.status_code == 201 and r.json()["estado"] == "LISTO":
                    exps.append({**r.json(), "_nombre": d["nombre"], "_tel": d["telefono"]})
            n += 1
        ck("8 expedientes ficticios LISTOS (trámite alta, NOR-1)", len(exps) == 8, len(exps))
        inc = c.post("/api/expedientes", json={"nombre": "Persona Plan Incompleta", "region": "NORTE", "sucursal": "NOR-1",
                                               "tipo_tramite": "alta", "correo": "inc@ejemplo.test",
                                               "correo_confirmacion": "inc@ejemplo.test"}).json()
        ck("un expediente incompleto (para comprobar que se rechaza)", inc.get("estado") == "INCOMPLETO", inc.get("estado"))
        jornada = datetime.now(__import__("zoneinfo").ZoneInfo(ZONA)).date().isoformat()
        r = c.post("/api/planes", json={"fecha_jornada": jornada, "capacidad": 6})
        ck("plan de la jornada creado (capacidad 6)", r.status_code == 201, r.text[:200])
        pid = r.json()["id"]
        t = time.perf_counter()
        r = c.post(f"/api/planes/{pid}/seleccionar", json={"fechas_desde": libres[0], "fechas_hasta": libres[-1]}).json()
        metricas["seleccion_ms"] = round((time.perf_counter() - t) * 1000, 1)
        orden_esperado = [e["id"] for _, i, e in sorted((e["prioridad"], i, e) for i, e in enumerate(exps))][:6]
        ck("selección automática: 6 por prioridad y antigüedad", [x["expediente_id"] for x in r["plan"]["elementos"]] == orden_esperado,
           [x["prioridad"] for x in r["plan"]["elementos"]])
        r = c.post(f"/api/planes/{pid}/elementos", json={"elementos": [{"expediente_id": inc["id"]}]}).json()
        ck("el incompleto se rechaza con su motivo", not r["aceptados"] and "incompleto" in r["rechazados"][0]["motivo"],
           r["rechazados"][:1])
        fuera = [e["id"] for e in exps if e["id"] not in orden_esperado]
        r = c.post(f"/api/planes/{pid}/elementos", json={"elementos": [{"expediente_id": fuera[0]}]}).json()
        ck("plan lleno: el 7.º se rechaza", not r["aceptados"] and "completo" in r["rechazados"][0]["motivo"], r["rechazados"][:1])
        c.post(f"/api/planes/{pid}/ampliar", json={"capacidad": 8}).raise_for_status()
        r = c.post(f"/api/planes/{pid}/elementos", json={"elementos": [
            {"expediente_id": x, "fechas_desde": libres[0], "fechas_hasta": libres[-1]} for x in fuera]}).json()
        ck("ampliado a 8: entran los otros dos", len(r["aceptados"]) == 2, r["rechazados"])
        r = c.post(f"/api/planes/{pid}/elementos", json={"elementos": [{"expediente_id": exps[0]["id"]}]}).json()
        ck("duplicado rechazado", not r["aceptados"] and "ya está en este plan" in r["rechazados"][0]["motivo"])

        # ---------------------------------------------------------------- 2. observador abierto, sin disponibilidad
        portal.ctl("POST", "/control/vista", refresco_ms=a.refresco_ms)
        r = c.post("/api/observador", json={"sucursal": "NOR-1", "tramite": "alta", "mes": f"{y:04d}-{m:02d}",
                                            "estrategia": "ambas", "intervalo_visual_ms": a.intervalo_visual_ms})
        ck("observador (Chromium) lanzado por el motor", r.status_code == 200, r.text[:200])
        activo = esperar(lambda: (c.get("/api/observador").json()["observadores"][0]["vista"] or {}).get("dom") == "ACTIVA", 60)
        ck("calendario abierto y ACTIVO", bool(activo), c.get("/api/observador").json())
        time.sleep(4)
        evs = c.get("/api/observaciones").json()["eventos"]
        plan = c.get(f"/api/planes/{pid}").json()
        ck("sin disponibilidad: ningún evento de día y los 8 siguen EN ESPERA",
           not [e for e in evs if e["tipo"] != "VISTA"] and all(x["estado"] == "EN_ESPERA" for x in plan["elementos"]),
           {"eventos": [e["tipo"] for e in evs], "estados": [x["estado"] for x in plan["elementos"]]})
        ck("la página vacía NO se confunde con desconexión ni sesión caducada",
           {e["estado"] for e in evs if e["tipo"] == "VISTA"} == {"ACTIVA"}, [e["estado"] for e in evs])

        # ---------------------------------------------------------------- 3. fechas nuevas
        candidatas = [f for f in libres if date.fromisoformat(f) > hoy]
        usadas: list[str] = []

        def ronda(fecha: str) -> dict:
            plan = c.get(f"/api/planes/{pid}").json()
            espera = sorted((x["prioridad"], x["orden"], x["expediente_id"], x["id"]) for x in plan["elementos"]
                            if x["estado"] == "EN_ESPERA" and fecha in x["fechas_aceptables"])
            esperado = espera[0][3] if espera else None
            ultimo = max([e["id"] for e in c.get("/api/observaciones").json()["eventos"]] or [0])
            t0 = time.time()
            ctl("POST", "/control/dias/habilitar", sucursal="NOR-1", tramite="alta", fecha=fecha, capacidad=1)
            t1 = time.time()

            def listo():
                for e in c.get("/api/observaciones", params={"desde": ultimo}).json()["eventos"]:
                    if e["tipo"] == "DIA_DISPONIBLE" and e["fecha"] == fecha and e["consumo"] not in ("NUEVO", "EN_PROCESO") \
                            and {"dom", "visual"} <= set(e["detecciones"]):
                        return e
            ev = esperar(listo, 20)
            if not ev:
                return {"fecha": fecha, "ok": False}
            dom, vis = iso_a_epoch(ev["detecciones"]["dom"]), iso_a_epoch(ev["detecciones"]["visual"])
            at = iso_a_epoch(ev["explicacion"]["atendido_en"]) if ev["explicacion"].get("atendido_en") else None
            usadas.append(fecha)
            return {"fecha": fecha, "ok": True, "evento_id": ev["id"], "elemento": ev["plan_elemento_id"], "esperado": esperado,
                    "consumo": ev["consumo"], "por_que": ev["explicacion"].get("por_que"),
                    "control_ms": round((t1 - t0) * 1000, 1),
                    "deteccion_dom_ms": round((dom - t0) * 1000, 1), "deteccion_visual_ms": round((vis - t0) * 1000, 1),
                    "asignacion_ms": round((at - t0) * 1000, 1) if at else None,
                    "evidencia_dom": bool(ev["evidencia"].get("html")) or ev["estrategia"] == "visual",
                    "evidencia": sorted(ev["evidencia"])}
        for i in range(a.rondas):
            rr = ronda(candidatas[i * 2])
            metricas["rondas"].append(rr)
        oks = [x for x in metricas["rondas"] if x["ok"]]
        ck(f"{a.rondas} fechas nuevas detectadas por DOM y por imagen", len(oks) == a.rondas,
           [x["fecha"] for x in metricas["rondas"] if not x["ok"]])
        ck("cada fecha se asignó al expediente que dicta la regla (prioridad, orden de alta)",
           all(x["consumo"] == "ASIGNADO" and x["elemento"] == x["esperado"] for x in oks),
           [(x["elemento"], x["esperado"]) for x in oks])
        ck("cada asignación trae su explicación", all(x["por_que"] for x in oks), oks[0].get("por_que") if oks else None)

        # ---------------------------------------------------------------- 4. operación -> confirmación humana -> folio
        primero = oks[0]
        coinc = op.get("/api/planificador/coincidencias").json()["coincidencias"]
        ck("el operador ve las coincidencias asignadas", any(x["id"] == primero["elemento"] for x in coinc), len(coinc))
        t = time.perf_counter()
        r = op.post(f"/api/planificador/elementos/{primero['elemento']}/preparar")
        ck("operación preparada (expediente tomado, reservación abierta)", r.status_code == 200, r.text[:200])
        rid = r.json()["reservacion_id"]
        r = op.post(f"/api/reservaciones/{rid}/enviar-datos", json={"revisado": True, "manuales": MANUALES, "sucursal": "NOR-1"})
        ck("datos enviados con los campos MANUALES tecleados", r.status_code == 200, r.text[:200])
        r = op.post(f"/api/reservaciones/{rid}/seleccionar-dia", json={"fecha": primero["fecha"], "sucursal": "NOR-1"})
        ck("día asignado elegido en el portal", r.status_code == 200, r.text[:200])
        plan = c.get(f"/api/planes/{pid}").json()
        el = next(x for x in plan["elementos"] if x["id"] == primero["elemento"])
        ck("sin confirmación humana NO hay cita (sigue OPERACION_PREPARADA)", el["estado"] == "OPERACION_PREPARADA", el["estado"])
        r = op.post(f"/api/reservaciones/{rid}/confirmar", json={"confirmo": True})       # SIMULADA: la «persona» es el script
        metricas["operacion_hasta_folio_ms"] = round((time.perf_counter() - t) * 1000, 1)
        folio = r.json().get("reservacion", {}).get("folio") if r.status_code == 200 else None
        ck("confirmación humana de laboratorio -> CONFIRMADA con folio", bool(folio), r.text[:200])
        rp = httpx.get(portal.base + f"/api/reservaciones/{folio}", headers={"Authorization": f"Bearer {portal.token}"},
                       trust_env=False)
        ck("el folio existe CONFIRMADO en el portal (comprobación independiente)",
           rp.status_code == 200 and rp.json().get("estado") == "CONFIRMADA", rp.status_code)
        conf = esperar(lambda: next(x for x in c.get(f"/api/planes/{pid}").json()["elementos"]
                                    if x["id"] == primero["elemento"])["estado"] == "CONFIRMADO", 30, 0.5)
        ck("el plan lo refleja: CONFIRMADO (mantenimiento del motor)", bool(conf))

        # ---------------------------------------------------------------- 5. cambio que desaparece tras asignarse
        seg = oks[1]
        ctl("POST", "/control/dias/agotar", sucursal="NOR-1", tramite="alta", fecha=seg["fecha"])
        vuelto = esperar(lambda: next(x for x in c.get(f"/api/planes/{pid}").json()["elementos"]
                                      if x["id"] == seg["elemento"])["estado"] == "EN_ESPERA", 30, 0.3)
        ck("día que desaparece tras asignarse: el expediente vuelve a EN ESPERA", bool(vuelto))
        r = op.post(f"/api/planificador/elementos/{seg['elemento']}/preparar")
        ck("ya no se puede preparar esa operación", r.status_code == 409, r.status_code)

        # ---------------------------------------------------------------- 6. parpadeos
        for dur in (0.15, 1.5):
            fecha = candidatas[a.rondas * 2 + len(metricas["parpadeos"]) * 2 + 1]
            ultimo = max([e["id"] for e in c.get("/api/observaciones").json()["eventos"]] or [0])
            ctl("POST", "/control/dias/habilitar", sucursal="NOR-1", tramite="alta", fecha=fecha, capacidad=1)
            time.sleep(dur)
            ctl("POST", "/control/dias/agotar", sucursal="NOR-1", tramite="alta", fecha=fecha)
            time.sleep(3)
            evs = [e for e in c.get("/api/observaciones", params={"desde": ultimo}).json()["eventos"] if e["fecha"] == fecha]
            disp = [e for e in evs if e["tipo"] == "DIA_DISPONIBLE"]
            metricas["parpadeos"].append({"duracion_s": dur, "visto": bool(disp),
                                          "estrategias": sorted(disp[0]["detecciones"]) if disp else [],
                                          "consumo": disp[0]["consumo"] if disp else None})
        p15 = metricas["parpadeos"][1]
        el15 = [x for x in c.get(f"/api/planes/{pid}").json()["elementos"] if x["estado"] == "ASIGNADO"
                and x["fecha_asignada"] == candidatas[a.rondas * 2 + 3]]
        ck("parpadeo de 1,5 s: visto por ambas estrategias y, al desaparecer, nada queda asignado a ese día",
           p15["visto"] and p15["estrategias"] == ["dom", "visual"] and not el15, metricas["parpadeos"])
        metricas["parpadeos_nota"] = ("Un cambio más corto que el refresco de la propia página no llega al DOM: ninguna "
                                      "estrategia puede verlo (MEDIDO con 150 ms y refresco de " f"{a.refresco_ms} ms).")

        # ---------------------------------------------------------------- 7. motor matado y reiniciado
        antes = c.get(f"/api/planes/{pid}").json()
        n_ev = len(c.get("/api/observaciones").json()["eventos"])
        motor.proc.kill()
        motor.proc.wait(20)
        motor.log.close()
        t = time.perf_counter()
        motor.start()
        motor.wait_ready()
        metricas["reinicio_motor_s"] = round(time.perf_counter() - t, 2)
        c = login(motor.base, "admin", PWD)
        op = login(motor.base, "operador1", OP_PWD)
        despues = c.get(f"/api/planes/{pid}").json()
        ck("tras matar y reiniciar el motor, el plan está intacto",
           [(x["id"], x["estado"]) for x in antes["elementos"]] == [(x["id"], x["estado"]) for x in despues["elementos"]])
        # v2.1: la configuración del observador es persistente: se restaura sola (sin volver a pedirla)
        t = time.perf_counter()
        restaurado = esperar(lambda: (c.get("/api/observador").json()["observadores"][0]["vista"] or {}).get("dom") == "ACTIVA",
                             90, 0.5)
        metricas["observador_restaurado_s"] = round(time.perf_counter() - t + metricas["reinicio_motor_s"], 1)
        ck("el observador se restaura solo tras el reinicio", bool(restaurado), metricas["observador_restaurado_s"])
        time.sleep(2)
        evs = c.get("/api/observaciones").json()["eventos"]
        nuevos_dia = [e for e in evs[n_ev:] if e["tipo"] != "VISTA"]
        ck("al reabrir el calendario no se duplican los eventos ya vistos", not nuevos_dia, [e["fecha"] for e in nuevos_dia])
        rr = ronda(candidatas[a.rondas * 2 + 6])
        ck("tras el reinicio se sigue detectando y asignando", rr["ok"] and rr["consumo"] in ("ASIGNADO", "SIN_CANDIDATOS"), rr)
        metricas["ronda_tras_reinicio"] = rr

        # ---------------------------------------------------------------- 8. PostgreSQL caído
        if a.pg_parar and a.pg_arrancar:
            fecha = candidatas[a.rondas * 2 + 8]
            subprocess.run(a.pg_parar, shell=True, check=False, timeout=60)
            t0 = time.time()
            ctl("POST", "/control/dias/habilitar", sucursal="NOR-1", tramite="alta", fecha=fecha, capacidad=1)
            time.sleep(4)
            salud = httpx.get(motor.base + "/api/health", timeout=10, trust_env=False).status_code
            subprocess.run(a.pg_arrancar, shell=True, check=False, timeout=60)
            ev = esperar(lambda: [e for e in c.get("/api/observaciones").json()["eventos"]
                                  if e["fecha"] == fecha and e["tipo"] == "DIA_DISPONIBLE"], 60, 0.5)
            ck("PostgreSQL caído: el motor responde 503 (no inventa nada)", salud == 503, salud)
            ck("al volver PostgreSQL, el cambio visto durante la caída se registra (una vez)", ev and len(ev) == 1,
               len(ev or []))
            metricas["pg_caido"] = {"salud_durante": salud, "registrado_s_desde_habilitar":
                                    round(iso_a_epoch(ev[0]["registrado_en"]) - t0, 1) if ev else None}
        c.delete("/api/observador")
        obs_estado = None
    finally:
        try:
            motor.stop()
        except Exception:  # noqa: BLE001
            pass
        portal.detener()
    textos = "".join(p.read_text(encoding="utf-8", errors="replace") for p in out.glob("servidor-motor-v21*.log"))
    fugas = [e["_nombre"] for e in exps if e["_nombre"] in textos] + [e["_tel"] for e in exps if e["_tel"] in textos]
    ck("sin datos personales en los registros del motor", not fugas, fugas[:3])
    ok_r = [x for x in metricas["rondas"] if x.get("ok")]
    return {"version": "2.1.0-dev", "momento": time.strftime("%Y-%m-%d %H:%M:%S"), "plataforma": platform.platform(),
            "python": platform.python_version(), "duracion_s": round(time.time() - t_inicio, 1),
            "base_de_datos": "postgresql" if a.bd else "sqlite",
            "parametros": {"refresco_pagina_ms": a.refresco_ms, "intervalo_visual_ms": a.intervalo_visual_ms,
                           "rondas": a.rondas},
            "latencias": {"deteccion_dom": resumen([x["deteccion_dom_ms"] for x in ok_r]),
                          "deteccion_visual": resumen([x["deteccion_visual_ms"] for x in ok_r]),
                          "hasta_asignacion": resumen([x["asignacion_ms"] for x in ok_r]),
                          "nota": "MEDIDO desde que el laboratorio habilita el día (antes de la llamada de control) "
                                  "hasta el instante de detección en la página y hasta que el motor asigna. Incluye el "
                                  "refresco propio de la página (refresco_pagina_ms): con un portal real ese intervalo lo "
                                  "decide el portal."},
            "metricas": metricas, "ok": ck.ok, "comprobaciones": ck.items, "observador": obs_estado}


def main() -> int:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--salida", default="resultados/recorrido_v21")
    ap.add_argument("--bd", default=None)
    ap.add_argument("--rondas", type=int, default=5)
    ap.add_argument("--refresco-ms", type=int, default=500)
    ap.add_argument("--intervalo-visual-ms", type=int, default=250)
    ap.add_argument("--pg-parar", default=None)
    ap.add_argument("--pg-arrancar", default=None)
    a = ap.parse_args()
    if not shutil.which("node"):
        print("FALLA Node.js no está disponible (el observador necesita Node + Playwright)")
        return 3
    rep = run(a)
    nombre = "recorrido_v21" + ("_postgresql" if a.bd else "_sqlite") + ".json"
    Path(a.salida, nombre).write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    for it in rep["comprobaciones"]:
        print(("OK   " if it["ok"] else "FALLA") + " " + it["comprobacion"] + (f" — {it['detalle']}" if it.get("detalle") else ""))
    print(json.dumps(rep["latencias"], ensure_ascii=False))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
