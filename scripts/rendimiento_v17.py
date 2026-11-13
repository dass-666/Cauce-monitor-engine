"""Rendimiento por escalas (v1.7) con el servidor REAL (`python -m monitoreo`) y el motor en marcha.

Cada escenario siembra una base con N regiones, sucursales, 100 fuentes simuladas, expedientes,
operadores (con usuario), asignaciones, alertas e historial; arranca el servidor y lanza a la vez
un cliente HTTP por operador y uno de administrador que repiten lo que hace el panel. Mide:

  MEDIDO     latencia de la API por ruta (p50/p95/máx), errores HTTP, refresco de cada vista del
             panel (las llamadas que hace la vista, en paralelo como el navegador), CPU y memoria
             (RSS) del proceso servidor, crecimiento de la base durante la prueba, «database is
             locked» en el registro del servidor, integridad al final y, ya con el servidor
             detenido, el tiempo de asignación del motor (allocate_slot) sobre esa misma base.
  SIMULADO   las fuentes (conector de simulación sin cambios: solo el coste de consultarlas), los
             operadores (clientes HTTP concurrentes desde 127.0.0.1) y los datos (100 % ficticios).
  NO MEDIDO  la confirmación contra el portal: se mide en el laboratorio integrado (otro script).

El límite de peticiones por IP se eleva solo para esta prueba (todos los clientes salen de la
misma IP); en uso real cada operador tiene su IP.

Uso:
  python scripts/rendimiento_v17.py --segundos 40                     # SQLite, s1..s3
  python scripts/rendimiento_v17.py --pg postgresql+psycopg://...     # además PostgreSQL
Escribe docs/resultados/v1.7/rendimiento_<motor>.json
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import random
import statistics
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("MONITOREO_INICIAR_MOTOR", "0")

from sqlalchemy import func, select, text  # noqa: E402

from monitoreo import migrate  # noqa: E402
from monitoreo.db import Database, utcnow  # noqa: E402
from monitoreo.models import (Alert, Assignment, Expediente, ExpedienteEvento, Operator, Region,  # noqa: E402
                              Request, SlotState, Source, User)
from verificar_arranque import PWD, Server, base_env  # noqa: E402

OPWD = "Operador-Rendimiento-2026"
ESCENARIOS = {
    "s1": {"regiones": 5, "sucursales": 10, "fuentes": 100, "expedientes": 1000, "operadores": 5},
    "s2": {"regiones": 10, "sucursales": 25, "fuentes": 100, "expedientes": 5000, "operadores": 10},
    "s3": {"regiones": 25, "sucursales": 25, "fuentes": 100, "expedientes": 10000, "operadores": 20},
}
LETRAS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def pct(v: list[float], p: float):
    if not v:
        return None
    v = sorted(v)
    return round(v[min(len(v) - 1, int(len(v) * p))], 1)


def resumen(v: list[float]) -> dict:
    return {"n": len(v), "p50_ms": pct(v, .5), "p95_ms": pct(v, .95), "max_ms": round(max(v), 1) if v else None}


# ---------------------------------------------------------------------------------- siembra
def sembrar(url: str, e: dict, rng: random.Random) -> dict:
    from monitoreo.security import hash_password
    migrate.upgrade(url)
    db = Database(url)
    now = utcnow()
    regiones = [f"R{LETRAS[i // 26]}{LETRAS[i % 26]}" for i in range(e["regiones"])]
    sucursales = {r: [] for r in regiones}
    for i in range(e["sucursales"]):
        sucursales[regiones[i % len(regiones)]].append(f"S{i + 1:02d}")
    t0 = time.perf_counter()
    with db.session() as s:
        for r in regiones:
            s.add(Region(code=r, name=f"Región {r}"))
        s.flush()
        fuentes = []
        for i in range(e["fuentes"]):
            r = regiones[i % len(regiones)]
            src = Source(code=f"rend-{i:03d}", name=f"Fuente ficticia {i}", region=r, availability_type="evento",
                         connector_type="simulacion", config={"feed": f"rend-{i:03d}", "error_rate": 0},
                         interval_s=15, jitter_pct=0.1, rate_limit_per_min=600)
            s.add(src)
            fuentes.append(src)
        s.flush()
        slots = []
        for i, src in enumerate(fuentes):
            for j in range(3):
                suc = (sucursales[src.region] or [None])[j % max(1, len(sucursales[src.region]))]
                st = SlotState(source_id=src.id, resource_key=f"K{i}-{j}", region=src.region, resource_type="cita_dia",
                               name=f"Día ficticio {i}-{j}", available=0, held=0, status="ACTIVO", fingerprint="f",
                               attributes={"sucursal": suc} if suc else {})
                s.add(st)
                slots.append(st)
        s.flush()
        ph = hash_password(OPWD)
        ops = []
        for i in range(e["operadores"]):
            u = User(username=f"oprend{LETRAS[i // 26].lower()}{LETRAS[i % 26].lower()}", password_hash=ph, role="operador")
            s.add(u)
            s.flush()
            regs = [regiones[(i + k) % len(regiones)] for k in range(max(1, len(regiones) // max(1, e["operadores"]) + 1))]
            o = Operator(name=f"Operador {LETRAS[i // 26]}{LETRAS[i % 26]}", user_id=u.id, regions=regs, max_concurrent=50)
            s.add(o)
            ops.append((o, u.username))
        s.flush()
        estados = (["LISTO"] * 40 + ["RESERVACION_CONFIRMADA"] * 20 + ["CERRADO"] * 15 + ["INCOMPLETO"] * 10
                   + ["ASIGNADO"] * 10 + ["REQUIERE_REVISION"] * 5)
        lote = 0
        for n in range(e["expedientes"]):
            est = rng.choice(estados)
            r = regiones[n % len(regiones)]
            suc = (sucursales[r] or [None])[n % max(1, len(sucursales[r]))]
            req = None
            if est in ("LISTO", "ASIGNADO"):
                req = Request(code=f"EXPREQ-{n:06d}", holder=f"EXP-{n + 1:06d}", region=r, resource_type="cita_dia",
                              priority=rng.randint(1, 5), status="ELEGIBLE" if est == "LISTO" else "ASIGNADO",
                              conditions={"sucursal": suc} if suc else {}, created_at=now - timedelta(hours=rng.randint(1, 72)))
                s.add(req)
                s.flush()
            x = Expediente(codigo=f"EXP-{n + 1:06d}", nombre=f"Titular Ficticio {n}", region=r, sucursal=suc,
                           tipo_tramite=rng.choice(["alta", "renovacion", "constancia"]), estado=est, modo="POR_DIA",
                           prioridad=req.priority if req else 3, request_id=req.id if req else None,
                           created_at=now - timedelta(days=rng.randint(0, 20)), updated_at=now)
            if est == "ASIGNADO":
                o = ops[n % len(ops)][0]
                x.operator_id = o.id
                req.operator_id = o.id
                sl = slots[n % len(slots)]
                a = Assignment(request_id=req.id, slot_id=sl.id, operator_id=o.id, status="ACTIVA",
                               live_request_id=req.id, created_at=now - timedelta(minutes=5),
                               expires_at=now + timedelta(hours=4),
                               explicacion={"criterio": "menor carga actual", "resumen": f"Se eligió a {o.name}."})
                s.add(a)
                o.current_load += 1
            s.add(x)
            s.flush()
            for k, (antes, despues) in enumerate([("NUEVO", "EN_VALIDACION"), ("EN_VALIDACION", est)]):
                s.add(ExpedienteEvento(expediente_id=x.id, ts=x.created_at + timedelta(minutes=k), tipo="ESTADO",
                                       actor="motor", detalle={"antes": antes, "despues": despues}))
            if n % 2 == 0:
                cat = rng.choice(["NUEVA_DISPONIBILIDAD", "DISPONIBILIDAD_AGOTADA", "EXPEDIENTE_ASIGNADO",
                                  "RESERVACION_CONFIRMADA", "EXPEDIENTE_INCOMPLETO"])
                s.add(Alert(dedup_key=f"rend:{n}", kind="CAPTURA", title=f"{x.codigo}: aviso ficticio", source_code="captura",
                            region=r, resource=suc or "-", status="ENVIADA", payload={"categoria": cat},
                            categoria=cat, nivel="MEDIA", created_at=now - timedelta(hours=rng.randint(0, 150)),
                            repeticiones=1))
            lote += 1
            if lote >= 500:
                s.flush()
                s.expunge_all()     # mantiene la memoria acotada al sembrar 10.000
                ops = [(s.get(Operator, o.id), u) for o, u in ops]
                slots = [s.get(SlotState, sl.id) for sl in slots]
                lote = 0
        usuarios = [u for _, u in ops]
    sembrado_s = round(time.perf_counter() - t0, 1)
    db.engine.dispose()
    return {"usuarios": usuarios, "regiones": regiones, "sembrado_s": sembrado_s}


def tam_base(url: str) -> int:
    if url.startswith("sqlite"):
        p = Path(url.split("///", 1)[1])
        return sum(f.stat().st_size for f in p.parent.glob(p.name + "*"))
    db = Database(url)
    with db.engine.connect() as c:
        n = c.execute(text("SELECT pg_database_size(current_database())")).scalar()
    db.engine.dispose()
    return int(n)


def limpiar_pg(url: str) -> None:
    db = Database(url)
    db.reset_for_tests()
    db.engine.dispose()


# ---------------------------------------------------------------------------------- carga
class Cliente:
    def __init__(self, base: str, usuario: str, clave: str, lat: dict, errores: dict, lock: threading.Lock):
        self.http = httpx.Client(base_url=base, timeout=30)
        r = self.http.post("/api/auth/login", json={"username": usuario, "password": clave})
        r.raise_for_status()
        self.http.headers["Authorization"] = "Bearer " + r.json()["token"]
        self.lat, self.err, self.lock = lat, errores, lock

    def get(self, ruta: str, clave: str | None = None):
        t = time.perf_counter()
        try:
            r = self.http.get(ruta)
            ok = r.status_code < 400
            code = r.status_code
        except httpx.HTTPError as e:
            ok, code, r = False, type(e).__name__, None
        ms = (time.perf_counter() - t) * 1000
        k = clave or ruta.split("?")[0]
        with self.lock:
            self.lat.setdefault(k, []).append(ms)
            if not ok:
                self.err[f"{k} {code}"] = self.err.get(f"{k} {code}", 0) + 1
        return r

    def post(self, ruta: str, body: dict, clave: str):
        t = time.perf_counter()
        try:
            r = self.http.post(ruta, json=body)
            ok, code = r.status_code < 400, r.status_code
        except httpx.HTTPError as e:
            ok, code, r = False, type(e).__name__, None
        ms = (time.perf_counter() - t) * 1000
        with self.lock:
            self.lat.setdefault(clave, []).append(ms)
            if not ok:
                self.err[f"{clave} {code}"] = self.err.get(f"{clave} {code}", 0) + 1
        return r


def vista(c: Cliente, nombre: str, rutas: list[str], refrescos: dict, lock) -> None:
    """Refresco de una vista: sus llamadas en paralelo (como Promise.all en el navegador)."""
    t = time.perf_counter()
    with ThreadPoolExecutor(len(rutas)) as ex:
        list(ex.map(c.get, rutas))
    with lock:
        refrescos.setdefault(nombre, []).append((time.perf_counter() - t) * 1000)


def correr(nombre: str, e: dict, url: str, segundos: float, out: Path) -> dict:
    rng = random.Random(17)
    tmp = Path(tempfile.mkdtemp(prefix=f"rend_{nombre}_"))
    sem = sembrar(url, e, rng)
    env = base_env(tmp, "desarrollo")
    env.update({"MONITOREO_DB_URL": url, "MONITOREO_DATOS_DEMO": "0", "MONITOREO_API_LIMITE_MIN": "1000000",
                "MONITOREO_API_ESCRITURA_MIN": "1000000"})
    tam0 = tam_base(url)
    srv = Server(f"rend-{nombre}", env, out)
    srv.start()
    arranque_s = srv.wait_ready(180)
    proc = psutil.Process(srv.proc.pid)
    lat, err, refrescos, lock = {}, {}, {}, threading.Lock()
    admin = Cliente(srv.base, "admin", PWD, lat, err, lock)
    ops = [Cliente(srv.base, u, OPWD, lat, err, lock) for u in sem["usuarios"]]
    fin = time.monotonic() + segundos
    cpu, rss = [], []
    proc.cpu_percent(None)

    def muestreo():
        while time.monotonic() < fin:
            time.sleep(0.5)
            try:
                cpu.append(proc.cpu_percent(None))
                rss.append(proc.memory_info().rss / 2**20)
            except psutil.Error:
                return

    def operador(c: Cliente, i: int):
        r = random.Random(i)
        while time.monotonic() < fin:
            vista(c, "operador · Mi trabajo", ["/api/captura/mi-trabajo"], refrescos, lock)
            vista(c, "operador · Inicio", ["/api/dashboard", "/api/centro"], refrescos, lock)
            n = c.get("/api/notificaciones?limit=50")
            if n is not None and n.status_code == 200 and r.random() < 0.3:
                ids = [a["id"] for a in n.json()["items"] if not a["acknowledged_at"]][:3]
                if ids:
                    c.post("/api/notificaciones/confirmar", {"ids": ids}, "/api/notificaciones/confirmar")
            time.sleep(r.uniform(0.5, 1.5))      # un operador real no refresca sin pausa

    def administrador():
        while time.monotonic() < fin:
            vista(admin, "admin · Inicio (centro)", ["/api/dashboard", "/api/centro", "/api/operators"], refrescos, lock)
            vista(admin, "admin · Centro filtrado", [f"/api/centro?region={sem['regiones'][0]}&periodo=7d"], refrescos, lock)
            vista(admin, "admin · Supervisión", ["/api/supervision"], refrescos, lock)
            vista(admin, "admin · Notificaciones", ["/api/notificaciones?limit=150"], refrescos, lock)
            vista(admin, "admin · Expedientes", ["/api/captura/resumen", "/api/expedientes"], refrescos, lock)
            time.sleep(1)

    hilos = [threading.Thread(target=muestreo)] + [threading.Thread(target=administrador)] + [
        threading.Thread(target=operador, args=(c, i)) for i, c in enumerate(ops)]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    tam1 = tam_base(url)
    salud = admin.get("/api/health").json() if admin.get("/api/health") is not None else {}
    parada = srv.stop()
    bloqueos = (out / f"servidor-rend-{nombre}.log").read_text(errors="replace").count("database is locked")

    # asignación del motor sobre la misma base (servidor detenido: medición en proceso, sin red)
    from monitoreo.config import ENGINE_DEFAULTS
    from monitoreo.engine import assignment as asg
    from monitoreo.engine.integrity import check
    db = Database(url)
    params = dict(ENGINE_DEFAULTS)
    with db.session() as s:
        libres = list(s.scalars(select(SlotState.id).where(SlotState.held == 0).limit(40)))
        for sid in libres:
            s.get(SlotState, sid).available = 1
    t_asg, creadas = [], 0
    for sid in libres:
        t = time.perf_counter()
        c = asg.allocate_slot(db, sid, utcnow(), params)
        t_asg.append((time.perf_counter() - t) * 1000)
        creadas += len(c)
    with db.session() as s:
        problemas = check(s)
        dobles = s.scalar(text("SELECT COUNT(*) FROM (SELECT request_id FROM assignments WHERE status IN "
                               "('ACTIVA','EN_PROCESO') GROUP BY request_id HAVING COUNT(*) > 1) x"))
    db.engine.dispose()
    tot = sum(len(v) for v in lat.values())
    return {
        "escenario": nombre, **e, "sembrado_s": sem["sembrado_s"], "arranque_s": arranque_s, "duracion_s": segundos,
        "parada": parada, "peticiones": tot, "errores_http": err,
        "peticiones_por_s": round(tot / segundos, 1),
        "latencia_api": {k: resumen(v) for k, v in sorted(lat.items())},
        "refresco_vistas": {k: resumen(v) for k, v in sorted(refrescos.items())},
        "cpu_servidor_pct": {"media": round(statistics.mean(cpu), 1) if cpu else None, "max": max(cpu) if cpu else None},
        "memoria_servidor_mb": {"max": round(max(rss), 1) if rss else None, "final": round(rss[-1], 1) if rss else None},
        "base_bytes": {"antes": tam0, "despues": tam1, "crecimiento": tam1 - tam0},
        "database_is_locked_en_registro": bloqueos,
        "asignacion_motor": {**resumen(t_asg), "recursos": len(libres), "asignaciones_creadas": creadas},
        "integridad": problemas, "asignaciones_dobles": dobles, "salud_final": salud.get("ok"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pg", help="URL de PostgreSQL (se BORRA su contenido)")
    ap.add_argument("--solo", default="s1,s2,s3")
    ap.add_argument("--segundos", type=float, default=40)
    ap.add_argument("--salida", default=str(ROOT / "docs" / "resultados" / "v1.7"))
    a = ap.parse_args()
    out = Path(a.salida)
    out.mkdir(parents=True, exist_ok=True)
    logs = Path(tempfile.mkdtemp(prefix="rend_logs_"))
    motores = [("sqlite", None)] + ([("postgresql", a.pg)] if a.pg else [])
    for motor, pg in motores:
        res = []
        for nombre in a.solo.split(","):
            e = ESCENARIOS[nombre]
            if pg:
                limpiar_pg(pg)
                url = pg
            else:
                url = f"sqlite:///{(Path(tempfile.mkdtemp(prefix='rend_db_')) / 'rend.db').as_posix()}"
            print(f"== {motor} {nombre} {e}", flush=True)
            r = correr(nombre, e, url, a.segundos, logs)
            r["motor"] = motor
            print(json.dumps({k: r[k] for k in ("escenario", "peticiones", "errores_http", "cpu_servidor_pct",
                                                 "memoria_servidor_mb", "asignacion_motor", "integridad")},
                             ensure_ascii=False), flush=True)
            res.append(r)
            if pg:
                limpiar_pg(pg)
        doc = {"generado": datetime.now().isoformat(timespec="seconds"), "motor": motor,
               "plataforma": f"{platform.system()} {platform.release()} · Python {platform.python_version()} · "
                             f"{psutil.cpu_count()} CPU · {round(psutil.virtual_memory().total / 2**30, 1)} GB",
               "etiquetas": {"MEDIDO": "latencias, CPU, memoria, crecimiento, bloqueos, integridad, asignación",
                             "SIMULADO": "fuentes, operadores (clientes HTTP), datos ficticios"},
               "escenarios": res}
        (out / f"rendimiento_{motor}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
