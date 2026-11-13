"""Pruebas de capacidad (v1.2) con el servidor REAL en marcha.

Cada escenario prepara una base con N regiones, fuentes simuladas, solicitudes y operadores,
arranca `python -m monitoreo` (una o dos instancias), genera cambios de disponibilidad
(simulados) y carga de varios operadores consultando el panel a la vez, y mide:

  MEDIDO     memoria (RSS) y CPU del proceso servidor, latencia de la API (p50/p95/máx),
             consultas a fuentes por minuto, ciclos del planificador, tiempo de relevo entre
             instancias, crecimiento de la base durante la prueba, integridad al final.
  SIMULADO   las fuentes (conector de simulación) y los cambios de disponibilidad; los
             "operadores" son clientes HTTP concurrentes autenticados.
  ESTIMADO   crecimiento diario de la base, extrapolado de lo medido y de la retención.

Uso:
  python scripts/capacidad.py                                  # SQLite, todos los escenarios
  python scripts/capacidad.py --pg postgresql+psycopg://...    # además, en PostgreSQL
  python scripts/capacidad.py --segundos 30 --solo e1,e2
Escribe docs/resultados/capacidad_<motor>.json
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import random
import statistics
import sys
import subprocess
import tempfile
import threading
import time
from datetime import timedelta
from pathlib import Path

import httpx
import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("MONITOREO_INICIAR_MOTOR", "0")

from sqlalchemy import func, select, text  # noqa: E402

from monitoreo.db import Database, utcnow  # noqa: E402
from monitoreo.models import (Alert, Assignment, ChangeEvent, Operator, PollLog, Region, Request,  # noqa: E402
                              SimItem, Source)
from verificar_arranque import PWD, Server, base_env  # noqa: E402

TYPES = ["entrada", "mesa", "unidad", "turno"]
SCENARIOS = {
    "e0": {"titulo": "Reposo: 25 fuentes consultándose sin cambios ni usuarios (crecimiento base)", "regiones": 5,
           "fuentes": 25, "pendientes": 100, "historicas": 0, "clientes": 0, "instancias": 1, "cambios": False},
    "e1": {"titulo": "5 regiones · 25 fuentes · 500 solicitudes · 5 operadores", "regiones": 5, "fuentes": 25,
           "pendientes": 500, "historicas": 0, "clientes": 5, "instancias": 1},
    "e2": {"titulo": "10 regiones · 100 fuentes · 2.000 solicitudes · 10 operadores", "regiones": 10,
           "fuentes": 100, "pendientes": 2000, "historicas": 0, "clientes": 10, "instancias": 1},
    "e3": {"titulo": "10 regiones · 100 fuentes · 10.000 solicitudes almacenadas · 10 operadores", "regiones": 10,
           "fuentes": 100, "pendientes": 1000, "historicas": 9000, "clientes": 10, "instancias": 1},
    "e4": {"titulo": "2 instancias · 10 regiones · 100 fuentes · 2.000 solicitudes · relevo por caída",
           "regiones": 10, "fuentes": 100, "pendientes": 2000, "historicas": 0, "clientes": 6, "instancias": 2},
}


def pct(v: list[float], q: float) -> float | None:
    if not v:
        return None
    v = sorted(v)
    return round(v[min(len(v) - 1, int(q * (len(v) - 1)))], 1)


# --------------------------------------------------------------------------- preparación

def prepare(url: str, sc: dict, rng: random.Random) -> dict:
    db = Database(url)
    db.reset_for_tests()
    db.create_all()
    from monitoreo.simulator.seed import seed_base
    seed_base(db)
    t0 = time.perf_counter()
    with db.session() as s:
        regions = [r.code for r in s.scalars(select(Region))]
        for k in range(len(regions), sc["regiones"]):
            code = f"REG{k + 1:02d}"
            s.add(Region(code=code, name=f"Región {k + 1:02d} (ficticia)"))
            regions.append(code)
    regions = regions[: sc["regiones"]]
    now = utcnow()
    with db.session() as s:
        for i in range(sc["fuentes"]):
            region, rtype = regions[i % len(regions)], TYPES[i % len(TYPES)]
            feed = f"cap-{i:03d}"
            s.add(Source(code=feed, name=f"Fuente simulada {i:03d}", region=region, category=rtype,
                         availability_type="evento", connector_type="simulacion",
                         config={"feed": feed, "error_rate": 0.01, "rate_limit_rate": 0.0},
                         interval_s=10, jitter_pct=0.15, rate_limit_per_min=30, max_retries=1))
            for k in range(8):
                cap = rng.choice([2, 4, 6])
                s.add(SimItem(feed=feed, resource_key=f"{feed}-{k}", name=f"Recurso ficticio {i}-{k}", region=region,
                              resource_type=rtype, capacity=cap, available=0, changed_at=now,
                              starts_at=now + timedelta(days=1 + k)))
        for region in regions:
            for j in range(2):
                s.add(Operator(name=f"Operador {region}-{j}", regions=[region], max_concurrent=5, active=True))
        s.add(Operator(name="Operador global", regions=[], max_concurrent=10, active=True))
    batch = []
    total = sc["pendientes"] + sc["historicas"]
    for n in range(total):
        hist = n < sc["historicas"]
        batch.append({"code": f"CAP-{n:05d}", "holder": f"Titular Ficticio {n:05d}",
                      "region": regions[n % len(regions)], "resource_type": TYPES[n % len(TYPES)],
                      "priority": rng.choice([1, 2, 3, 3, 4, 5]), "quantity": 1, "conditions": {},
                      "status": rng.choice(["COMPLETADA", "CANCELADA", "EXPIRADA"]) if hist else "PENDIENTE",
                      "created_at": now - timedelta(minutes=rng.randint(1, 60 * 24 * 30 if hist else 600)),
                      "updated_at": now})
        if len(batch) == 2000:
            with db.session() as s:
                s.execute(Request.__table__.insert(), batch)
            batch = []
    if batch:
        with db.session() as s:
            s.execute(Request.__table__.insert(), batch)
    prep_s = round(time.perf_counter() - t0, 2)
    db.engine.dispose()
    return {"preparacion_s": prep_s, "regiones": regions}


def db_size(url: str) -> int:
    """Tamaño real de los datos. En SQLite se vuelca antes el diario -wal (si no, se mediría el
    diario temporal, que crece mientras hay escrituras y se recicla, no el crecimiento real)."""
    if url.startswith("sqlite"):
        import sqlite3
        p = Path(url.replace("sqlite:///", ""))
        con = sqlite3.connect(p, timeout=30)
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            # páginas en uso (sin las libres que deja el vaciado de la base entre escenarios)
            used = con.execute("PRAGMA page_count").fetchone()[0] - con.execute("PRAGMA freelist_count").fetchone()[0]
            return used * con.execute("PRAGMA page_size").fetchone()[0]
        finally:
            con.close()
    db = Database(url)
    try:
        with db.engine.connect() as c:
            return int(c.execute(text("SELECT pg_database_size(current_database())")).scalar())
    finally:
        db.engine.dispose()


def counts(url: str) -> dict:
    db = Database(url)
    try:
        with db.session() as s:
            return {"consultas": s.scalar(select(func.count()).select_from(PollLog)),
                    "eventos": s.scalar(select(func.count()).select_from(ChangeEvent)),
                    "asignaciones": s.scalar(select(func.count()).select_from(Assignment)),
                    "alertas": s.scalar(select(func.count()).select_from(Alert)),
                    "solicitudes": s.scalar(select(func.count()).select_from(Request))}
    finally:
        db.engine.dispose()


def integrity(url: str) -> list:
    from monitoreo.engine.integrity import check
    db = Database(url)
    try:
        with db.session() as s:
            return check(s)
    finally:
        db.engine.dispose()


# --------------------------------------------------------------------------- carga

class Sampler(threading.Thread):
    def __init__(self, pids: list[int]):
        super().__init__(daemon=True)
        self.procs = {p: psutil.Process(p) for p in pids}
        self.rss = {p: [] for p in pids}
        self.cpu = {p: [] for p in pids}
        self.stop_ev = threading.Event()
        for pr in self.procs.values():
            pr.cpu_percent(None)

    def run(self):
        while not self.stop_ev.wait(1):
            for p, pr in self.procs.items():
                try:
                    self.cpu[p].append(pr.cpu_percent(None))
                    self.rss[p].append(pr.memory_info().rss / 2**20)
                except psutil.Error:
                    pass

    def summary(self, pid: int) -> dict:
        r, c = self.rss[pid], self.cpu[pid]
        return {"memoria_mb_media": round(statistics.fmean(r), 1) if r else None, "memoria_mb_max": round(max(r), 1) if r else None,
                "cpu_pct_media": round(statistics.fmean(c), 1) if c else None, "cpu_pct_p95": pct(c, .95)}


def world_driver(url: str, stop: threading.Event, stats: dict, rng: random.Random) -> None:
    """Cambios de disponibilidad SIMULADOS: ~1 paso por segundo, con un pico cada 15 s."""
    from monitoreo.simulator.world import World
    db = Database(url)
    w = World(db, rng=rng)
    k = 0
    try:
        while not stop.wait(1):
            k += 1
            stats["cambios"] += len(w.random_step(intensity=2, burst=(k % 15 == 0)))
    except Exception as e:  # noqa: BLE001
        stats["error"] = f"{type(e).__name__}: {e}"
    finally:
        db.engine.dispose()


def operator_client(bases: list[str], token_by_base: dict, stop: threading.Event, lat: dict, errors: list,
                    rng: random.Random, idx: int) -> None:
    """Un operador: mira el inicio, la lista de solicitudes y las alertas; a veces registra una solicitud."""
    clients = {b: httpx.Client(base_url=b, timeout=30, headers={"Authorization": f"Bearer {token_by_base[b]}"})
               for b in bases}
    n = 0
    while not stop.is_set():
        b = bases[(idx + n) % len(bases)]
        c = clients[b]
        n += 1
        path = rng.choice(["/api/dashboard", "/api/requests?limit=200", "/api/alerts?limit=100", "/api/dashboard"])
        method = "GET"
        if n % 10 == 0:
            method, path = "POST", "/api/requests"
        t = time.perf_counter()
        try:
            if method == "GET":
                r = c.get(path)
            else:
                r = c.post(path, json={"holder": "Titular Ficticio Carga", "region": "NORTE",
                                       "resource_type": rng.choice(TYPES), "priority": rng.randint(1, 5)})
            ms = (time.perf_counter() - t) * 1000
            key = f"{method} {path.split('?')[0]}"
            lat.setdefault(key, []).append(ms)
            if r.status_code >= 400:
                errors.append(f"{key}: {r.status_code}")
        except httpx.HTTPError as e:
            errors.append(f"{method} {path}: {type(e).__name__}")
            time.sleep(0.5)
        time.sleep(rng.uniform(0.2, 0.6))     # pausa humana/navegador entre acciones
    for c in clients.values():
        c.close()


# --------------------------------------------------------------------------- escenario

def run_scenario(key: str, url: str, seconds: int, out: Path) -> dict:
    sc = SCENARIOS[key]
    print(f"\n=== {key}: {sc['titulo']} ({'PostgreSQL' if url.startswith('postgresql') else 'SQLite'})", flush=True)
    rng = random.Random(7)
    prep = prepare(url, sc, rng)
    size0 = db_size(url)
    servers = []
    for i in range(sc["instancias"]):
        env = base_env(out, "desarrollo")
        env.update({"MONITOREO_DB_URL": url, "MONITOREO_DATOS_DEMO": "0", "MONITOREO_API_LIMITE_MIN": "0",
                    "MONITOREO_API_ESCRITURA_MIN": "0", "MONITOREO_LEASE_S": "5", "MONITOREO_LOG": "warning"})
        srv = Server(f"{key}-inst{i + 1}", env, out)
        srv.start()
        servers.append(srv)
    res: dict = {"escenario": sc, "preparacion": prep}
    prof = None
    try:
        res["arranque_s"] = [s.wait_ready(120) for s in servers]
        tokens = {}
        for s in servers:
            tokens[s.base] = httpx.post(s.base + "/api/auth/login", json={"username": "admin", "password": PWD},
                                        timeout=30).json()["token"]
        before = counts(url)
        if os.environ.get("CAPACIDAD_PERFIL"):  # v1.4: perfil del servidor con py-spy (herramienta opcional)
            pdir = Path(os.environ["CAPACIDAD_PERFIL"])
            pdir.mkdir(parents=True, exist_ok=True)
            prof = subprocess.Popen(["py-spy", "record", "-p", str(servers[0].proc.pid), "-d", str(max(5, seconds - 5)),
                                     "-r", "50", "--format", "raw", "-o", str(pdir / f"{key}.txt"), "--nonblocking"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        sampler = Sampler([s.proc.pid for s in servers])
        sampler.start()
        stop = threading.Event()
        wstats = {"cambios": 0}
        lat: dict = {}
        errors: list = []
        threads = ([threading.Thread(target=world_driver, args=(url, stop, wstats, random.Random(11)), daemon=True)]
                   if sc.get("cambios", True) else [])
        threads += [threading.Thread(target=operator_client, args=([s.base for s in servers], tokens, stop, lat, errors,
                                                                  random.Random(100 + i), i), daemon=True)
                    for i in range(sc["clientes"])]
        t0 = time.monotonic()
        for t in threads:
            t.start()
        failover = None
        if sc["instancias"] == 2:
            time.sleep(seconds / 2)
            roles = {s.base: httpx.get(s.base + "/api/health", timeout=10).json()["rol"] for s in servers}
            leaders = [s for s in servers if roles[s.base] == "LIDER"]
            res["roles_antes_de_la_caida"] = roles
            res["lideres_simultaneos"] = len(leaders)
            if len(leaders) == 1:
                victim = leaders[0]
                other = [s for s in servers if s is not victim][0]
                tk = time.monotonic()
                res["errores_api_antes_de_la_caida"] = len(errors)
                victim.proc.kill()                   # caída brusca, sin parada ordenada
                victim.proc.wait(10)
                while time.monotonic() - tk < 60:
                    try:
                        if httpx.get(other.base + "/api/health", timeout=5).json()["rol"] == "LIDER":
                            failover = round(time.monotonic() - tk, 2)
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.25)
                res["relevo_tras_caida_s"] = failover
            time.sleep(max(0, seconds - (time.monotonic() - t0)))
        else:
            time.sleep(seconds)
        stop.set()
        for t in threads:
            t.join(30)
        sampler.stop_ev.set()
        elapsed = time.monotonic() - t0
        after = counts(url)
        alive = [s for s in servers if s.proc.poll() is None]
        health = [httpx.get(s.base + "/api/health", timeout=10).json() for s in alive]
        obs = httpx.get(alive[0].base + "/api/observabilidad", timeout=60,
                        headers={"Authorization": f"Bearer {tokens[alive[0].base]}"}).json()
        delta = {k: after[k] - before[k] for k in after}
        res["_delta"] = delta
        res.update({
            "duracion_s": round(elapsed, 1),
            "medido": {
                "procesos": {s.name: sampler.summary(s.proc.pid) for s in servers},
                "latencia_api_ms": {k: {"n": len(v), "p50": pct(v, .5), "p95": pct(v, .95), "max": round(max(v), 1)}
                                    for k, v in sorted(lat.items())},
                "peticiones_api_por_s": round(sum(len(v) for v in lat.values()) / elapsed, 1),
                "errores_api": len(errors), "ejemplos_error": errors[:5],
                "consultas_fuentes_por_min": round(delta["consultas"] / elapsed * 60, 1),
                "consultas_esperadas_por_min": round(sc["fuentes"] * 60 / 10, 1),
                "eventos_detectados": delta["eventos"], "asignaciones": delta["asignaciones"],
                "alertas": delta["alertas"], "ciclos_planificador": [h["ciclos"] for h in health],
                "errores_ciclo_seguidos": obs["motor"].get("errores_ciclo_seguidos"),
                "latencia_deteccion_ms_por_region": {r["name"]: r.get("latencia_deteccion_ms") for r in obs["regiones"]},
            },
            "simulado": {"cambios_de_disponibilidad": wstats["cambios"], "error_simulador": wstats.get("error"),
                         "clientes_operador": sc["clientes"]},
        })
        problems = integrity(url)            # con el motor en marcha (foto coherente: REPEATABLE READ)
        res["medido"]["integridad_en_marcha"] = "correcta" if not problems else problems[:10]
        print(json.dumps(res["medido"]["procesos"], ensure_ascii=False), flush=True)
    finally:
        if prof is not None:
            try:
                prof.wait(timeout=60)
            except subprocess.TimeoutExpired:
                prof.kill()
        for srv_ in servers:
            srv_.stop()
    if "medido" in res:
        problems = integrity(url)            # con todo parado: foto definitiva
        res["medido"]["integridad_tras_parada"] = "correcta" if not problems else problems[:10]
        size1 = db_size(url)                 # con los servidores parados: medida limpia
        m = res["medido"]
        m.update({"base_bytes_inicio": size0, "base_bytes_fin": size1,
                  "crecimiento_bytes_por_hora": round((size1 - size0) / res["duracion_s"] * 3600)})
        delta = res.pop("_delta")
        # ESTIMACIÓN: con la retención por defecto (consultas 14 días) el volumen se estabiliza.
        if not delta["consultas"]:
            return res
        polls_day = res["medido"]["consultas_fuentes_por_min"] * 60 * 24
        per_poll = (size1 - size0) / max(1, delta["consultas"])
        res["estimado"] = {
            "consultas_por_dia": round(polls_day),
            "bytes_por_consulta_aprox": round(per_poll),
            "crecimiento_dia_mb": round(polls_day * per_poll / 2**20, 1),
            "techo_con_retencion_14d_mb": round(polls_day * per_poll * 14 / 2**20, 1),
            "nota": "Extrapolación lineal de lo medido en la prueba; incluye eventos y asignaciones. "
                    "La retención borra consultas de más de 14 días (ya resumidas en agregados).",
        }
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pg")
    ap.add_argument("--segundos", type=int, default=60)
    ap.add_argument("--solo", default="e0,e1,e2,e3,e4")
    ap.add_argument("--sin-sqlite", action="store_true")
    a = ap.parse_args()
    keys = a.solo.split(",")
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        targets = ([] if a.sin_sqlite else [("sqlite", f"sqlite:///{(out / 'capacidad.db').as_posix()}")])
        if a.pg:
            targets.append(("postgresql", a.pg))
        for name, url in targets:
            report = {"motor": name, "sistema": platform.platform(), "python": platform.python_version(),
                      "cpus": psutil.cpu_count(), "memoria_total_gb": round(psutil.virtual_memory().total / 2**30, 1),
                      "segundos_por_escenario": a.segundos, "escenarios": {}}
            for k in keys:
                try:
                    report["escenarios"][k] = run_scenario(k, url, a.segundos, out)
                except Exception as e:  # noqa: BLE001
                    report["escenarios"][k] = {"error": f"{type(e).__name__}: {e}"}
                    print("ERROR", k, e, flush=True)
                for log in out.glob("servidor-*.log"):
                    txt = log.read_text(encoding="utf-8", errors="replace")
                    if "Traceback" in txt or "ERROR" in txt:
                        report["escenarios"][k].setdefault("registro_servidor_con_errores", {})[log.name] = txt[-1500:]
                    log.unlink()
            dest = ROOT / "docs" / "resultados" / f"capacidad_{name}.json"
            dest.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            print(f"\nResultados en {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
