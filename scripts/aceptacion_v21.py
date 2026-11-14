"""v2.1 · Recorrido de ACEPTACIÓN con procesos y navegador reales (datos ficticios, solo 127.0.0.1).

  1. Ocho expedientes ficticios listos. El administrador crea el plan DESDE VENTANILLA: entran 6 por
     prioridad; amplía a 8 y lo completa (Chromium, capturas).
  2. El observador se configura una vez (persistente) y abre el calendario del laboratorio.
     Sin días disponibles: nada se asigna.
  3. Aparecen VARIAS unidades: día A con 3, día B con 2, día C con 1 -> 6 coincidencias, en orden de
     prioridad, sin pasar de las unidades de cada día; las otras 2 siguen pendientes.
  4. Día D con 2 unidades -> las 2 pendientes. Disminución inesperada (D se agota) -> vuelven a la
     espera; D vuelve con 1 -> la de mayor prioridad.
  5. Operador 1 en Ventanilla: prepara 2 coincidencias desde «Plan del día» y las lleva al folio
     (confirmación humana SIMULADA por el script). Operador 2: una operación que termina SIN cita
     vuelve AL PLAN (no a la cola general) y la unidad se reofrece.
  6. Recuperación: el motor se mata (kill) con una operación a medias; al arrancar, el observador
     se RESTAURA solo (sin volver a pedirlo), el plan sigue igual y la operación se termina. Luego
     se mata el navegador del observador: el supervisor lo relanza y se sigue detectando.
  7. Las operaciones restantes hasta el folio. Cada folio se comprueba en la API del portal
     (evidencia independiente). Ninguna solicitud de un expediente del plan pasó por la cola general.

Uso: python scripts/aceptacion_v21.py --salida docs/resultados/v2.1 [--bd URL]
Salida: <salida>/aceptacion_v21_<bd>.json y capturas en <salida>/capturas_v21/ (código 0 si todo pasa).
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
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from portal_reservas.proceso import PortalProceso  # noqa: E402
from verificar_arranque import Checks, Server, base_env  # noqa: E402

PWD = "Aceptacion-Planificador-2026"
OP_PWD = "Operador-Aceptacion-2026"
ZONA = "America/Mexico_City"
MANUALES = {"clave_identidad": "QXZ482915", "acepto_privacidad": "si"}     # ficticios
LETRAS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def login(base: str, u: str, p: str) -> httpx.Client:
    c = httpx.Client(base_url=base, timeout=30, trust_env=False)
    c.headers["Authorization"] = "Bearer " + c.post("/api/auth/login", json={"username": u, "password": p}).json()["token"]
    return c


def esperar(pred, t=30.0, paso=0.1):
    t0 = time.monotonic()
    while time.monotonic() - t0 < t:
        try:
            v = pred()
        except (httpx.HTTPError, ValueError, KeyError, StopIteration, IndexError):
            v = None
        if v:
            return v
        time.sleep(paso)
    return None


def navegador(base, usuario, clave, caps, fase, n=None, log=None) -> dict:
    p = subprocess.run(["node", str(ROOT / "scripts" / "aceptacion_v21.js"), base, usuario, clave, str(caps), fase,
                        *( [str(n)] if n is not None else [])], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=900)
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"===== {fase} {usuario}\n{p.stdout}\n{p.stderr[-3000:]}\n")
    linea = next((x for x in p.stdout.splitlines() if x.startswith("RESULTADO ")), None)
    return json.loads(linea[10:]) if linea else {"error": f"sin resultado (código {p.returncode})"}


def run(a) -> dict:
    out = Path(a.salida)
    caps = out / "capturas_v21"
    out.mkdir(parents=True, exist_ok=True)
    caps.mkdir(parents=True, exist_ok=True)
    navlog = out / f"aceptacion_v21_navegador_{'postgresql' if a.bd else 'sqlite'}.log"
    navlog.write_text("", encoding="utf-8")
    tmp = Path(tempfile.mkdtemp(prefix="acep21-"))
    ck = Checks()
    t_inicio = time.time()
    portal = PortalProceso(tmp / "portal.json", token=secrets.token_urlsafe(16), token_control=secrets.token_urlsafe(16),
                           log=out / "portal_aceptacion_v21.log", extra=["--zona-horaria", ZONA])
    portal.iniciar()
    env = base_env(tmp, "desarrollo")
    env.update({"MONITOREO_DATOS_DEMO": "0", "MONITOREO_ADMIN_PASSWORD": PWD, "MONITOREO_PORTAL_URL": portal.base,
                "MONITOREO_PORTAL_TOKEN": portal.token, "MONITOREO_ZONA_HORARIA": ZONA})
    if a.bd:
        env["MONITOREO_DB_URL"] = a.bd
    motor = Server("motor-acep21", env, out)
    motor.start()
    m: dict = {}
    exps: list[dict] = []
    try:
        ck("motor y portal arrancan como procesos independientes", True, f"{motor.wait_ready()} s")
        c = login(motor.base, "admin", PWD)
        c.put("/api/settings/engine", json={"captura_modo": "POR_DIA"}).raise_for_status()
        for u in ("operador1", "operador2"):
            r = c.post("/api/users", json={"username": u, "password": OP_PWD, "role": "operador", "regions": ["NORTE"],
                                           "sucursales": ["NOR-1", "NOR-2"], "operator_name": f"Operador {u[-1]}"})
            ck(f"{u} con usuario", r.status_code == 201, r.status_code)
        op1, op2 = login(motor.base, "operador1", OP_PWD), login(motor.base, "operador2", OP_PWD)
        ctl = portal.ctl
        dias = ctl("GET", "/control/dias")
        hoy = date.fromisoformat(dias["hoy"])
        jornada = datetime.now(ZoneInfo(ZONA)).date()
        # mes observado: el de mañana si le quedan >= 6 días dentro de la ventana del plan (+30); si no, el siguiente
        ventana = [hoy + timedelta(days=i) for i in range(3, 30)]
        por_mes: dict = {}
        for f in ventana:
            por_mes.setdefault((f.year, f.month), []).append(f.isoformat())
        usadas = {x["fecha"] for x in dias["dias"].values() if x["sucursal"] == "NOR-1" and x["tramite"] == "alta"}
        y, mm = max(por_mes, key=lambda k: len([f for f in por_mes[k] if f not in usadas]))
        mes = f"{y:04d}-{mm:02d}"
        # el calendario empieza SIN disponibilidad para NOR-1/alta en ese mes (se deshabilita lo que traiga el laboratorio)
        for x in dias["dias"].values():
            if x["sucursal"] == "NOR-1" and x["tramite"] == "alta" and x["fecha"].startswith(mes):
                ctl("POST", "/control/dias/deshabilitar", sucursal="NOR-1", tramite="alta", fecha=x["fecha"])
        libres = [f for f in por_mes[(y, mm)] if f not in usadas]
        ck("hay al menos 5 fechas nuevas posibles en el mes observado", len(libres) >= 5, {"mes": mes, "n": len(libres)})
        A, B, C, D, E = libres[0], libres[1], libres[2], libres[3], libres[4]

        # ---------------------------------------------------------------- 1. expedientes y plan (Ventanilla)
        prios = [3, 1, 2, 5, 1, 4, 2, 3]
        n = 40
        while len(exps) < 8 and n < 600:
            reg = portal.registro(n)
            if reg["tramite"] == "alta":
                k = len(exps)
                d = {"nombre": f"Persona Acept {LETRAS[k]}{LETRAS[n % 26]}", "fecha_nacimiento": reg["fecha_nacimiento"],
                     "telefono": f"55500052{k:02d}", "telefono_confirmacion": f"55500052{k:02d}",
                     "correo": f"acep{k}@ejemplo.test", "correo_confirmacion": f"acep{k}@ejemplo.test",
                     "region": "NORTE", "sucursal": "NOR-1", "sucursales_alternativas": ["NOR-2"], "tipo_tramite": "alta",
                     "id_registro_previo": reg["identificador"], "prioridad": prios[k]}
                r = c.post("/api/expedientes", json=d)
                if r.status_code == 201 and r.json()["estado"] == "LISTO":
                    exps.append({**r.json(), "_nombre": d["nombre"], "_tel": d["telefono"]})
            n += 1
        ck("8 expedientes ficticios LISTOS", len(exps) == 8, len(exps))
        nav = navegador(motor.base, "admin", PWD, caps, "plan", log=navlog)
        orden6 = [e["codigo"] for _, i, e in sorted((e["prioridad"], i, e) for i, e in enumerate(exps))][:6]
        ck("Ventanilla: el administrador crea el plan y entran 6 por prioridad",
           not nav.get("error") and nav.get("codigos_inicial") == orden6 and (nav.get("vivos_inicial") or "").startswith("6 de 6"),
           {k: nav.get(k) for k in ("error", "vivos_inicial", "codigos_inicial")})
        ck("Ventanilla: capacidad ampliada a 8 y completada", (nav.get("vivos_final") or "").startswith("8 de 8")
           and len(nav.get("codigos_final") or []) == 8, nav.get("vivos_final"))
        ck("Ventanilla: el detalle muestra sucursales y fechas aceptables",
           bool(nav.get("detalle")) and "NOR-1" in nav["detalle"]["sucursales"] and nav["detalle"]["fechas"] >= 20, nav.get("detalle"))
        ck("sin errores de JavaScript en la sección", not nav.get("errores_js"), nav.get("errores_js"))
        pid = c.get("/api/planes").json()["planes"][0]["id"]
        plan = c.get(f"/api/planes/{pid}").json()

        def reqs_plan():
            out_ = {}
            for x in c.get(f"/api/planes/{pid}").json()["elementos"]:
                e = c.get(f"/api/expedientes/{x['expediente_id']}").json()
                out_[x["expediente_id"]] = c.get(f"/api/requests/{e['request_id']}").json()
            return out_
        ck("propiedad: las 8 solicitudes del motor quedaron EN_PLAN", all(r["status"] == "EN_PLAN" for r in reqs_plan().values()))

        # ---------------------------------------------------------------- 2. observador persistente, sin disponibilidad
        portal.ctl("POST", "/control/vista", refresco_ms=500)
        r = c.post("/api/observador", json={"sucursal": "NOR-1", "tramite": "alta", "mes": mes, "estrategia": "ambas",
                                            "intervalo_visual_ms": 250})
        ck("observador configurado (persistente) y lanzado", r.status_code == 200, r.text[:200])

        def vista_dom():
            o = c.get("/api/observador").json()["observadores"]
            return o and (o[0]["vista"] or {}).get("dom") == "ACTIVA"
        ck("calendario abierto y ACTIVO", bool(esperar(vista_dom, 60)))
        time.sleep(3)
        ck("sin disponibilidad: nada asignado", all(x["estado"] == "EN_ESPERA" for x in c.get(f"/api/planes/{pid}").json()["elementos"]))

        def elementos():
            return c.get(f"/api/planes/{pid}").json()["elementos"]

        def asignados(fecha=None):
            return [x for x in elementos() if x["estado"] in ("ASIGNADO", "OPERACION_PREPARADA", "CONFIRMADO")
                    and (fecha is None or x["fecha_asignada"] == fecha)]

        def esperado_orden(k):
            return [x["id"] for x in sorted((x for x in elementos() if x["estado"] == "EN_ESPERA"),
                                            key=lambda x: (x["prioridad"], x["orden"], x["expediente_id"]))][:k]

        # ---------------------------------------------------------------- 3. varias unidades
        lat = {}
        for fecha, unidades in ((A, 3), (B, 2), (C, 1)):
            esperados = esperado_orden(unidades)
            t0 = time.time()
            ctl("POST", "/control/dias/habilitar", sucursal="NOR-1", tramite="alta", fecha=fecha, capacidad=unidades)
            ok = esperar(lambda: len(asignados(fecha)) >= unidades, 30)
            lat[fecha] = round((time.time() - t0) * 1000)
            time.sleep(1.5)                      # por si llegara algo de más
            got = asignados(fecha)
            ck(f"día con {unidades} unidad(es): {unidades} coincidencia(s), las de mayor prioridad",
               bool(ok) and sorted(x["id"] for x in got) == sorted(esperados), {"esperados": esperados, "obtenidos": [x["id"] for x in got]})
        m["latencia_hasta_asignar_ms"] = lat
        evs = c.get("/api/observaciones").json()["eventos"]
        por_dia = {f: [e for e in evs if e["fecha"] == f and e["tipo"] == "DIA_DISPONIBLE"] for f in (A, B, C)}
        ck("un evento por día (las dos estrategias lo ven; no se duplica)",
           all(len(v) == 1 and {"dom", "visual"} <= set(v[0]["detecciones"]) for v in por_dia.values()),
           {f: [(e["unidades"], sorted(e["detecciones"])) for e in v] for f, v in por_dia.items()})
        ck("las unidades vistas son las del portal (3, 2, 1)", [por_dia[f][0]["unidades"] for f in (A, B, C)] == [3, 2, 1])
        ck("6 coincidencias y 2 pendientes", len(asignados()) == 6 and sum(1 for x in elementos() if x["estado"] == "EN_ESPERA") == 2)

        # ---------------------------------------------------------------- 4. más unidades, baja inesperada y vuelta
        pend = esperado_orden(2)
        ctl("POST", "/control/dias/habilitar", sucursal="NOR-1", tramite="alta", fecha=D, capacidad=2)
        ck("día D con 2 unidades -> las 2 pendientes", bool(esperar(lambda: sorted(x["id"] for x in asignados(D)) == sorted(pend), 30)))
        ctl("POST", "/control/dias/agotar", sucursal="NOR-1", tramite="alta", fecha=D)
        ck("baja inesperada (D se agota): las 2 vuelven a la espera",
           bool(esperar(lambda: not asignados(D) and sum(1 for x in elementos() if x["estado"] == "EN_ESPERA") == 2, 30)))
        ctl("POST", "/control/dias/liberar", sucursal="NOR-1", tramite="alta", fecha=D, unidades=1)
        ck("D vuelve con 1 unidad: la de mayor prioridad de las dos", bool(esperar(lambda: [x["id"] for x in asignados(D)] == pend[:1], 30)),
           [x["id"] for x in asignados(D)])

        # ---------------------------------------------------------------- 5. operaciones desde Ventanilla
        nav = navegador(motor.base, "operador1", OP_PWD, caps, "operar", 2, log=navlog)
        ops1 = nav.get("operaciones") or []
        ck("operador 1: 2 operaciones desde «Plan del día» hasta el folio (Ventanilla)",
           not nav.get("error") and len(ops1) == 2 and all(o.get("folio") and o["folio"] == o["folio_atencion"] for o in ops1),
           {"error": nav.get("error"), "ops": ops1})
        nav = navegador(motor.base, "operador2", OP_PWD, caps, "sin_cita", log=navlog)
        sc = (nav.get("operaciones") or [{}])[0]
        el_sc = next((x for x in elementos() if str(x["id"]) == str(sc.get("elemento"))), None)
        ck("operación sin cita: la atención termina SIN_DISPONIBILIDAD", sc.get("estado") == "SIN_DISPONIBILIDAD", nav.get("error") or sc)
        if el_sc:
            e_sc = c.get(f"/api/expedientes/{el_sc['expediente_id']}").json()
            req = c.get(f"/api/requests/{e_sc['request_id']}").json()
            ck("…y el expediente vuelve AL PLAN (solicitud EN_PLAN), no a la cola general",
               req["status"] == "EN_PLAN" and e_sc["estado"] == "LISTO", (req["status"], e_sc["estado"]))
            ck("…y su unidad (que sigue libre en el portal) se le vuelve a ofrecer (REEVALUACION)",
               bool(esperar(lambda: next(x for x in elementos() if x["id"] == el_sc["id"])["estado"] == "ASIGNADO", 30, 0.5)))

        # ---------------------------------------------------------------- 6. recuperación: motor y navegador
        coinc = [x for x in elementos() if x["estado"] == "ASIGNADO"]
        medias = coinc[0]
        r = op2.post(f"/api/planificador/elementos/{medias['id']}/preparar")
        rid = r.json().get("reservacion_id")
        r = op2.post(f"/api/reservaciones/{rid}/enviar-datos", json={"revisado": True, "manuales": MANUALES, "sucursal": "NOR-1"})
        ck("operación a medias (datos enviados) antes de matar el motor", r.status_code == 200, r.text[:200])
        antes = [(x["id"], x["estado"], x["fecha_asignada"]) for x in elementos()]
        motor.proc.kill()
        motor.proc.wait(20)
        motor.log.close()
        t = time.perf_counter()
        motor.start()
        motor.wait_ready()
        c = login(motor.base, "admin", PWD)
        op1, op2 = login(motor.base, "operador1", OP_PWD), login(motor.base, "operador2", OP_PWD)
        m["reinicio_motor_s"] = round(time.perf_counter() - t, 2)
        despues = [(x["id"], x["estado"], x["fecha_asignada"]) for x in elementos()]
        ck("tras matar y reiniciar el motor, el plan está intacto", antes == despues)
        t = time.perf_counter()
        ok = esperar(vista_dom, 90, 0.5)
        m["observador_restaurado_s"] = round(time.perf_counter() - t + m["reinicio_motor_s"], 1)
        ck("el observador se RESTAURA solo tras el reinicio (sin volver a pedirlo)", bool(ok), m["observador_restaurado_s"])
        ck("no hay dos observadores para lo mismo", len(c.get("/api/observador").json()["observadores"]) == 1)
        r = op2.post(f"/api/reservaciones/{rid}/seleccionar-dia", json={"fecha": medias["fecha_asignada"], "sucursal": "NOR-1"})
        r2 = op2.post(f"/api/reservaciones/{rid}/confirmar", json={"confirmo": True}) if r.status_code == 200 else r
        folio_medias = (r2.json().get("reservacion") or {}).get("folio") if r2.status_code == 200 else None
        ck("la operación a medias se termina tras el reinicio (folio)", bool(folio_medias), r2.text[:200])
        # navegador del observador matado
        n_arr = c.get("/api/observador").json()["observadores"][0]["arranques"]
        c.post("/api/observador/simular-caida-navegador").raise_for_status()      # corte brusco de Chromium
        ck("el supervisor relanza el navegador del observador",
           bool(esperar(lambda: c.get("/api/observador").json()["observadores"][0]["arranques"] > n_arr and vista_dom(), 60, 0.5)))
        pend = esperado_orden(1)
        ctl("POST", "/control/dias/habilitar", sucursal="NOR-1", tramite="alta", fecha=E, capacidad=1)
        ck("tras relanzarlo se sigue detectando: día E -> la pendiente", bool(esperar(lambda: [x["id"] for x in asignados(E)] == pend, 30)),
           pend)

        # ---------------------------------------------------------------- 7. el resto hasta el folio
        quedan = sum(1 for x in elementos() if x["estado"] == "ASIGNADO")
        nav = navegador(motor.base, "operador1", OP_PWD, caps, "operar", quedan, log=navlog)
        ck(f"operador 1: las {quedan} restantes hasta el folio (Ventanilla)",
           not nav.get("error") and len(nav.get("operaciones") or []) == quedan, {"error": nav.get("error")})
        ck("el plan refleja 8 CONFIRMADOS (mantenimiento del motor)",
           bool(esperar(lambda: all(x["estado"] == "CONFIRMADO" for x in elementos()), 40, 0.5)),
           [x["estado"] for x in elementos()])
        navegador(motor.base, "admin", PWD, caps, "ver", "83", log=navlog)
        mv = navegador(motor.base, "operador1", OP_PWD, caps, "movil", log=navlog)
        ck("la sección en un teléfono no desborda", mv.get("desborde") is False, mv.get("error"))
        els = elementos()
        folios = [x["explicacion"]["confirmado"]["folio"] for x in els]
        ck("8 folios distintos", len(set(folios)) == 8, folios)
        malos = []
        for x in els:
            f = x["explicacion"]["confirmado"]["folio"]
            rp = httpx.get(portal.base + f"/api/reservaciones/{f}", headers={"Authorization": f"Bearer {portal.token}"},
                           trust_env=False)
            if rp.status_code != 200 or rp.json().get("estado") != "CONFIRMADA" or rp.json().get("fecha") != x["fecha_asignada"]:
                malos.append((f, rp.status_code))
        ck("cada folio existe CONFIRMADO en el portal con su día (comprobación independiente)", not malos, malos)
        por_fecha = {}
        for x in els:
            por_fecha[x["fecha_asignada"]] = por_fecha.get(x["fecha_asignada"], 0) + 1
        cap = {A: 3, B: 2, C: 1, D: 1, E: 1}
        ck("ningún día con más citas que unidades", all(por_fecha.get(f, 0) <= u for f, u in cap.items()), por_fecha)
        rq = reqs_plan()
        cola = [k for k, r in rq.items() if r["asignaciones"] or any(h["to"] == "ASIGNADO" for h in r["historial"])]
        ck("ninguna solicitud del plan pasó por la cola general (sin asignaciones del motor)", not cola, cola)
        ck("todas las solicitudes del plan terminaron COMPLETADO", all(r["status"] == "COMPLETADO" for r in rq.values()),
           sorted({r["status"] for r in rq.values()}))
        c.delete("/api/observador")
    finally:
        try:
            motor.stop()
        except Exception:  # noqa: BLE001
            pass
        portal.detener()
    textos = "".join(p.read_text(encoding="utf-8", errors="replace") for p in out.glob("servidor-motor-acep21*.log"))
    textos += navlog.read_text(encoding="utf-8", errors="replace")
    fugas = [e["_nombre"] for e in exps if e["_nombre"] in textos] + [e["_tel"] for e in exps if e["_tel"] in textos]
    ck("sin datos personales en los registros del motor ni del navegador", not fugas, fugas[:3])
    return {"version": "2.1.0-dev", "momento": time.strftime("%Y-%m-%d %H:%M:%S"), "plataforma": platform.platform(),
            "python": platform.python_version(), "duracion_s": round(time.time() - t_inicio, 1),
            "base_de_datos": "postgresql" if a.bd else "sqlite",
            "nota": "La confirmación humana la hace el script (SIMULADA). Portal de laboratorio propio; datos ficticios.",
            "metricas": m, "ok": ck.ok, "comprobaciones": ck.items}


def main() -> int:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--salida", default="resultados/aceptacion_v21")
    ap.add_argument("--bd", default=None)
    a = ap.parse_args()
    if not shutil.which("node"):
        print("FALLA Node.js no está disponible (el observador y el recorrido necesitan Node + Playwright)")
        return 3
    rep = run(a)
    nombre = "aceptacion_v21_" + ("postgresql" if a.bd else "sqlite") + ".json"
    Path(a.salida, nombre).write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    for it in rep["comprobaciones"]:
        print(("OK   " if it["ok"] else "FALLA") + " " + it["comprobacion"] + (f" — {it['detalle']}" if it.get("detalle") else ""))
    print(json.dumps(rep["metricas"], ensure_ascii=False))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
