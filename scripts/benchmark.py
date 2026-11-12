"""Pruebas de rendimiento y confiabilidad con cifras reales.

Uso:
  python scripts/benchmark.py                       # SQLite temporal
  python scripts/benchmark.py --pg postgresql+psycopg://usuario@localhost/monitoreo_bench
Escribe los resultados en docs/resultados/benchmark_<motor>.json
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import random
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MONITOREO_INICIAR_MOTOR", "0")

from sqlalchemy import func, select  # noqa: E402

from monitoreo.db import Database  # noqa: E402
from monitoreo.engine import assignment as asg  # noqa: E402
from monitoreo.engine.integrity import check  # noqa: E402
from monitoreo.models import Assignment, Operator, Request, SlotState, Source  # noqa: E402
from monitoreo.config import ENGINE_DEFAULTS  # noqa: E402
from monitoreo.simulator.accelerated import run_accelerated  # noqa: E402


def concurrency_stress(db_url: str, threads: int = 32, requests: int = 500, slots: int = 10, units: int = 3) -> dict:
    """Muchos hilos compiten a la vez por pocos recursos. Nada debe asignarse dos veces."""
    db = Database(db_url)
    db.reset_for_tests()
    db.create_all()
    from monitoreo.simulator.seed import seed_base
    seed_base(db)
    with db.session() as s:
        src = Source(code="stress", name="stress", region="NORTE", availability_type="evento",
                     connector_type="simulacion", config={"feed": "stress"})
        s.add(src)
        s.flush()
        for i in range(slots):
            s.add(SlotState(source_id=src.id, resource_key=f"S{i}", region="NORTE", resource_type="entrada",
                            name=f"S{i}", available=units, held=0, status="ACTIVO", fingerprint="x", version=1))
        for i in range(threads):
            s.add(Operator(name=f"Op{i}", regions=[], max_concurrent=1000))
        for i in range(requests):
            s.add(Request(code=f"R{i}", holder="Ficticio", region="NORTE", resource_type="entrada",
                          priority=1 + i % 5, quantity=1, status="ELEGIBLE", conditions={}))
    with db.session() as s:
        slot_ids = list(s.scalars(select(SlotState.id)))
        req_ids = list(s.scalars(select(Request.id)))
        op_ids = list(s.scalars(select(Operator.id)))
    barrier = threading.Barrier(threads)
    ok, fail = [0], [0]
    lock = threading.Lock()
    now = datetime(2026, 6, 1)

    def worker(k):
        rng = random.Random(k)
        barrier.wait()
        for _ in range(60):
            out = asg.try_assign(db, rng.choice(slot_ids), rng.choice(req_ids), op_ids[k], now, 600)
            with lock:
                (ok if out.assignment_id else fail)[0] += 1

    t0 = time.perf_counter()
    ts = [threading.Thread(target=worker, args=(k,)) for k in range(threads)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    wall = time.perf_counter() - t0
    with db.session() as s:
        res = {"hilos": threads, "intentos": ok[0] + fail[0], "asignaciones_exitosas": ok[0],
               "rechazadas_por_conflicto": fail[0], "unidades_totales": slots * units,
               "asignaciones_en_bd": s.scalar(select(func.count()).select_from(Assignment)),
               "unidades_retenidas": s.scalar(select(func.sum(SlotState.held))),
               "violaciones_integridad": check(s), "segundos": round(wall, 2),
               "intentos_por_segundo": round((ok[0] + fail[0]) / wall, 1)}
    db.engine.dispose()
    res["correcto"] = (res["asignaciones_exitosas"] == res["asignaciones_en_bd"] == res["unidades_retenidas"]
                       <= res["unidades_totales"] and not res["violaciones_integridad"])
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pg", help="URL de PostgreSQL para las pruebas")
    ap.add_argument("--horas", type=float, default=24)
    ap.add_argument("--solicitudes", type=int, default=100, help="solicitudes por región")
    args = ap.parse_args()
    engine = "postgresql" if args.pg else "sqlite"
    tmp = tempfile.mkdtemp()
    url = args.pg or f"sqlite:///{tmp}/bench.db"
    out = {"fecha": datetime.now().isoformat(timespec="seconds"), "motor_bd": engine,
           "python": platform.python_version(), "sistema": platform.platform(), "cpus": os.cpu_count()}
    print(f"[{engine}] simulación acelerada de {args.horas} h con {args.solicitudes * 5} solicitudes...", flush=True)
    out["simulacion_acelerada"] = run_accelerated(hours=args.horas, step_s=30, requests_per_region=args.solicitudes,
                                                  db_url=url, seed=11)
    print(f"[{engine}] estrés de concurrencia...", flush=True)
    out["concurrencia"] = concurrency_stress(url)
    dest = ROOT / "docs" / "resultados" / f"benchmark_{engine}.json"
    dest.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    sa = out["simulacion_acelerada"]
    print(json.dumps({k: sa[k] for k in ("tiempo_real_s", "aceleracion_x", "cambios_simulados", "consultas",
                                         "eventos_detectados", "asignaciones", "alertas", "cpu_s", "memoria_pico_mb",
                                         "total_violaciones")}, ensure_ascii=False, default=str))
    print(json.dumps(out["concurrencia"], ensure_ascii=False))
    print("Resultados en", dest)


if __name__ == "__main__":
    main()
