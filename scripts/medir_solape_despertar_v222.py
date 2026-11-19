"""v2.2.2 · Mide qué tareas de fondo REALES del planificador corren a la vez que la consulta falsa de
test_changes_wake_the_cycle (sin el aislamiento de la prueba). Solo mide; no cambia nada del motor.
Uso: python scripts/medir_solape_despertar_v222.py <raíz del repo> <repeticiones> [carga]
"""
import asyncio, json, multiprocessing as mp, os, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
os.environ.setdefault("MONITOREO_INICIAR_MOTOR", "0"); os.environ.setdefault("MONITOREO_SECRET_KEY", "x")
os.environ.setdefault("MONITOREO_EXIGIR_CONSENTIMIENTO", "0")
from monitoreo.config import settings
from monitoreo.db import Database
from monitoreo.engine import scheduler as sch
from monitoreo.engine.params import set_params
from monitoreo.engine.pipeline import PollOutcome
from monitoreo.simulator.seed import seed_base

def quemar(fin):
    while time.time() < fin: pass

def ronda():
    tmp = Path(tempfile.mkdtemp()); settings.data_dir = tmp
    db = Database(f"sqlite:///{(tmp/'t.db').as_posix()}"); db.create_all(); seed_base(db)
    with db.session() as s: set_params(s, {"tick_segundos": 2.0})
    polled, fin, calls, reg = [], [], [], []
    sch.due_sources = lambda db, now: [] if polled else [1]
    def fake_poll(*a, **k):
        polled.append(time.monotonic()); time.sleep(0.05); fin.append(time.monotonic()); return PollOutcome("OK", events=3)
    sch.poll_source = fake_poll
    sch.process_pending_events = lambda *a, **k: calls.append(time.monotonic())
    sch.maintenance = lambda *a, **k: None
    def envolver(nombre, f):
        def g(*a, **k):
            t = time.monotonic(); 
            try: return f(*a, **k)
            finally: reg.append((nombre, t, time.monotonic()))
        return g
    for n in ("run_recovery", "run_history_maintenance"):
        setattr(sch, n, envolver(n, getattr(sch, n)))
    from monitoreo.engine import alerts; alerts.dispatch_pending = envolver("dispatch", alerts.dispatch_pending)
    from monitoreo.captura import seguimiento; seguimiento.mantenimiento = envolver("captura", seguimiento.mantenimiento)
    async def esc():
        s1 = sch.Scheduler(db, holder="uno", lease_ttl_s=10)
        s1._revisar_eventos = envolver("eventos", s1._revisar_eventos)
        s1._anunciar_proveedor = envolver("proveedor", s1._anunciar_proveedor)
        s1.start(); await asyncio.sleep(1.0); await s1.stop()
    asyncio.run(esc()); db.engine.dispose()
    p0, f0 = polled[0], fin[0]
    lat = next(c for c in calls if c > p0 + 0.05) - p0
    solapan = sorted({n for n, a, b in reg if a < f0 and b > p0})
    return {"latencia": round(lat, 3), "consulta": round(f0 - p0, 3), "solapan_con_consulta": solapan,
            "duraciones": {n: round(b - a, 3) for n, a, b in reg}}

if __name__ == "__main__":
    n = int(sys.argv[2]); carga = len(sys.argv) > 3
    procs = []
    if carga:
        procs = [mp.Process(target=quemar, args=(time.time() + 600,), daemon=True) for _ in range(os.cpu_count() * 2)]
        [p.start() for p in procs]
    rs = [ronda() for _ in range(n)]
    [p.terminate() for p in procs]
    from collections import Counter
    print(json.dumps({"cpu": os.cpu_count(), "carga": carga, "max_lat": max(r["latencia"] for r in rs),
        "max_consulta": max(r["consulta"] for r in rs),
        "solapes": Counter(x for r in rs for x in r["solapan_con_consulta"]),
        "ejemplo": rs[-1]}, ensure_ascii=False))
