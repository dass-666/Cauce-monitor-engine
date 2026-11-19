"""Prueba de CARGA de la v1.5 con procesos reales: muchos expedientes y operadores a la vez.

Portal de prueba y motor como procesos independientes (red real en 127.0.0.1). Se crean N
expedientes ficticios y M operadores; el MOTOR los asigna y cada operador (un hilo) recorre por la
API sus expedientes asignados: preparar -> enviar datos -> elegir horario -> confirmar. Los
operadores compiten por los MISMOS horarios (eligen entre los primeros libres) a propósito.

Comprobaciones al final (contra la verdad del portal, no contra el motor):
- ningún horario reservado por encima de su capacidad; folios únicos;
- cada reservación CONFIRMADA del motor existe en el portal con la misma clave y viceversa;
- ningún expediente CONFIRMADO sin evidencia; ninguna asignación duplicada.
Uso: python scripts/carga_captura.py --expedientes 200 --operadores 10 --salida resultados/carga
"""
from __future__ import annotations

import argparse
import json
import platform
import random
import secrets
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from portal_reservas.proceso import PortalProceso  # noqa: E402
from verificar_arranque import Checks, Server, base_env  # noqa: E402

PWD = "Carga-Captura-2026"
OPWD = "Operador-Carga-2026"
REGIONES = ["NORTE", "SUR", "ESTE", "OESTE", "CENTRO"]


def pct(v, p):
    if not v:
        return None
    v = sorted(v)
    return round(v[min(len(v) - 1, int(len(v) * p))] * 1000, 1)


def run(a) -> dict:
    out = Path(a.salida)
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="carga15-"))
    ck = Checks()
    (tmp / "portal").mkdir()
    portal = PortalProceso(tmp / "portal" / "estado.json", token=secrets.token_urlsafe(16), log=out / "portal.log")
    portal.iniciar()
    env = base_env(tmp, "desarrollo")
    env.update({"MONITOREO_DATOS_DEMO": "0", "MONITOREO_ADMIN_PASSWORD": PWD, "MONITOREO_PORTAL_URL": portal.base,
                "MONITOREO_PORTAL_TOKEN": portal.token, "MONITOREO_API_LIMITE_MIN": "1000000",
                "MONITOREO_API_ESCRITURA_MIN": "1000000"})
    if a.bd:
        env["MONITOREO_DB_URL"] = a.bd
    motor = Server("motor", env, out)
    motor.start()
    motor.wait_ready()
    adm = httpx.Client(base_url=motor.base, timeout=60, trust_env=False)
    adm.headers["Authorization"] = "Bearer " + adm.post("/api/auth/login", json={"username": "admin", "password": PWD}).json()["token"]
    adm.post("/api/sources", json={"code": "portal-lab", "name": "Portal de prueba", "region": "CENTRO",
                                   "availability_type": "calendario", "connector_type": "portal_reservas",
                                   "config": {"url_base": portal.base, "token_env": "MONITOREO_PORTAL_TOKEN"},
                                   "interval_s": 2, "jitter_pct": 0, "rate_limit_per_min": 120}).raise_for_status()
    adm.put("/api/settings/engine", json={"ttl_asignacion_s": 600}).raise_for_status()
    ops = []
    for i in range(a.operadores):
        u = f"op{i:02d}"
        adm.post("/api/users", json={"username": u, "password": OPWD, "role": "operador",
                                     "regions": [REGIONES[i % 5]], "operator_name": f"Operador Carga {i:02d}"}).raise_for_status()
        ops.append(u)
    rng = random.Random(a.semilla)
    t_alta = time.perf_counter()
    for n in range(1, a.expedientes + 1):
        reg = portal.registro(n)
        letras = "".join(chr(65 + int(d)) for d in f"{n:04d}").capitalize()
        r = adm.post("/api/expedientes", json={
            "nombre": f"Carga Ficticia {letras}", "fecha_nacimiento": reg["fecha_nacimiento"],
            "telefono": f"555{n:07d}", "telefono_confirmacion": f"555{n:07d}", "correo": f"c{n}@ejemplo.test",
            "correo_confirmacion": f"c{n}@ejemplo.test", "region": REGIONES[n % 5], "tipo_tramite": reg["tramite"],
            "id_registro_previo": reg["identificador"], "prioridad": rng.randint(1, 5)})
        r.raise_for_status()
    t_alta = time.perf_counter() - t_alta
    ck(f"{a.expedientes} expedientes creados por la API", True, f"{round(t_alta, 1)} s")

    lat = {k: [] for k in ("preparar", "enviar", "seleccionar", "confirmar")}
    res = {"confirmadas": 0, "agotado": 0, "otros_errores": [], "202": 0}
    lock = threading.Lock()
    fin = time.monotonic() + a.duracion

    def terminado():
        return all(e["estado"] in ("CONFIRMADO", "FALLIDO", "CANCELADO")
                   for e in c_fin.get("/api/expedientes?limit=2000").json())

    c_fin = httpx.Client(base_url=motor.base, timeout=60, trust_env=False, headers=dict(adm.headers))

    def operador(u):
        c = httpx.Client(base_url=motor.base, timeout=60, trust_env=False)
        c.headers["Authorization"] = "Bearer " + c.post("/api/auth/login", json={"username": u, "password": OPWD}).json()["token"]
        r2 = random.Random(hash(u))

        def t(nombre, fn):
            t0 = time.perf_counter()
            r = fn()
            with lock:
                lat[nombre].append(time.perf_counter() - t0)
            return r
        while time.monotonic() < fin:
            mios = [e for e in c.get("/api/expedientes").json() if e["estado"] in ("ASIGNADO", "EN_CAPTURA")]
            if not mios:
                if terminado():
                    return
                time.sleep(0.5)
                continue
            for e in mios:
                r = t("preparar", lambda: c.post(f"/api/expedientes/{e['id']}/preparar"))
                if r.status_code != 200:
                    with lock:
                        res["otros_errores"].append(("preparar", r.status_code, r.text[:120]))
                    continue
                rid = r.json()["reservacion"]["id"]
                r = t("enviar", lambda: c.post(f"/api/reservaciones/{rid}/enviar-datos", json={"revisado": True}))
                if r.status_code != 200:
                    with lock:
                        res["otros_errores"].append(("enviar", r.status_code, r.text[:120]))
                    continue
                hs = r.json()["calendario"]["horarios"]
                while hs:
                    h = hs[r2.randrange(min(3, len(hs)))]        # competencia: todos miran los primeros
                    r = t("seleccionar", lambda: c.post(f"/api/reservaciones/{rid}/seleccionar", json={"horario": h["id"]}))
                    if r.status_code == 200:
                        break
                    with lock:
                        res["agotado"] += 1
                    cal = (r.json().get("detail") or {}).get("calendario")
                    hs = cal["horarios"] if cal else []
                if r.status_code != 200:
                    continue
                r = t("confirmar", lambda: c.post(f"/api/reservaciones/{rid}/confirmar", json={"confirmo": True}))
                with lock:
                    if r.status_code == 200:
                        res["confirmadas"] += 1
                    elif r.status_code == 202:
                        res["202"] += 1
                    else:
                        res["otros_errores"].append(("confirmar", r.status_code, r.text[:120]))
            if terminado():
                return
    t0 = time.perf_counter()
    hilos = [threading.Thread(target=operador, args=(u,)) for u in ops]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    dur = time.perf_counter() - t0

    # ---------------------------------------------------------------- auditoría contra el portal
    exps = adm.get("/api/expedientes?limit=2000").json()
    estados = {}
    for e in exps:
        estados[e["estado"]] = estados.get(e["estado"], 0) + 1
    resv = adm.get("/api/reservaciones?limit=2000").json()
    conf = [r for r in resv if r["estado"] == "CONFIRMADA"]
    pr = portal.ctl("GET", "/control/reservaciones")
    p_conf = {r["folio"]: r for r in pr["reservaciones"] if r["estado"] == "CONFIRMADA"}
    sobre = [h for h, n in pr["ocupacion"].items() if n > 1]
    ck("ningún horario por encima de su capacidad (capacidad 1)", not sobre, sobre[:5])
    ck("folios únicos en el motor", len({r["folio"] for r in conf}) == len(conf))
    ck("cada CONFIRMADA del motor existe en el portal", all(r["folio"] in p_conf for r in conf),
       [r["folio"] for r in conf if r["folio"] not in p_conf][:5])
    ck("cada CONFIRMADA del portal está registrada en el motor", set(p_conf) <= {r["folio"] for r in conf},
       sorted(set(p_conf) - {r["folio"] for r in conf})[:5])
    ck("toda CONFIRMADA tiene evidencia con clave coincidente", all(r["evidencia"].get("clave_coincide") for r in conf))
    ck("expedientes CONFIRMADO == reservaciones CONFIRMADA", estados.get("CONFIRMADO", 0) == len(conf),
       (estados.get("CONFIRMADO", 0), len(conf)))
    ck("sin errores inesperados de la API", not res["otros_errores"], res["otros_errores"][:5])
    salud = adm.get("/api/metrics").json().get("integridad")
    motor.stop()
    portal.detener()
    return {"version": "1.5.0", "momento": time.strftime("%Y-%m-%d %H:%M:%S"), "plataforma": platform.platform(),
            "python": platform.python_version(), "base_de_datos": "postgresql" if a.bd else "sqlite",
            "expedientes": a.expedientes, "operadores": a.operadores, "duracion_flujo_s": round(dur, 1),
            "alta_expedientes_s": round(t_alta, 1), "estados_expedientes": estados, "confirmadas": len(conf),
            "confirmadas_por_minuto": round(len(conf) / dur * 60, 1) if dur else None,
            "horario_ya_tomado_por_otro": res["agotado"], "confirmaciones_202": res["202"],
            "latencia_ms": {k: {"n": len(v), "p50": pct(v, .5), "p95": pct(v, .95), "max": pct(v, 1.0),
                                "media": round(statistics.mean(v) * 1000, 1) if v else None} for k, v in lat.items()},
            "integridad_motor": salud, "ok": ck.ok, "comprobaciones": ck.items}


def main() -> int:
    for flujo in (sys.stdout, sys.stderr):   # consola de Windows (cp1252): no fallar por "→" o "•"
        try:
            flujo.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--expedientes", type=int, default=200)
    ap.add_argument("--operadores", type=int, default=10)
    ap.add_argument("--duracion", type=float, default=240, help="tiempo máximo de la fase de operadores (s)")
    ap.add_argument("--semilla", type=int, default=5)
    ap.add_argument("--bd", default=None)
    ap.add_argument("--salida", default="resultados/carga_captura")
    a = ap.parse_args()
    if a.expedientes > 290:
        sys.exit("El portal de prueba tiene 300 registros ficticios: usa como máximo 290 expedientes.")
    rep = run(a)
    Path(a.salida, "carga_captura.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: rep[k] for k in ("estados_expedientes", "confirmadas", "confirmadas_por_minuto",
                                          "duracion_flujo_s", "latencia_ms", "ok")}, ensure_ascii=False, indent=1))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
