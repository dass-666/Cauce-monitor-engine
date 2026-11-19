"""v2.2.2: investigación de test_changes_wake_the_cycle (falló en CI Linux · Python 3.13 con 0,51 y 0,53 s
frente a un límite de 0,4 s).

Reproduce EXACTAMENTE el escenario de la prueba (mismas sustituciones, mismo planificador real) y mide
por partes el camino entre "la consulta trae cambios" y "se procesan los eventos":

  despertar      desde que la consulta termina hasta que el bucle sale de la espera
  ciclo_previo   lo que tarda el bucle en terminar el ciclo en curso si la consulta acabó a mitad de ciclo
  arrendamiento  lease.acquire_or_renew del ciclo siguiente (escritura en SQLite)
  latencia       lo que mide la prueba: primera llamada a process_pending_events tras polled[0] + 0,05

Escenarios:
  normal      tal cual
  carga       con N procesos que consumen CPU al 100 % (simula un ejecutor de CI cargado)
  aislado     con las tareas de fondo reales (captura, historial, envío, autodiagnóstico) sustituidas por
              nada, para ver si la contención con ellas explica la latencia

No modifica nada del motor: solo mide. Uso:
    python scripts/investigar_despertar.py --repeticiones 40 --escenarios normal,carga,aislado
"""
from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing as mp
import os
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ.setdefault("MONITOREO_INICIAR_MOTOR", "0")
os.environ.setdefault("MONITOREO_SECRET_KEY", "clave-de-pruebas")
os.environ.setdefault("MONITOREO_EXIGIR_CONSENTIMIENTO", "0")

from monitoreo.config import settings  # noqa: E402
from monitoreo.db import Database  # noqa: E402
from monitoreo.engine import scheduler as sch  # noqa: E402
from monitoreo.engine.params import set_params  # noqa: E402
from monitoreo.engine.pipeline import PollOutcome  # noqa: E402
from monitoreo.simulator.seed import seed_base  # noqa: E402

LIMITE_S = 0.4


def _quemar(fin: float) -> None:
    while time.monotonic() < fin:
        pass


def una_ronda(aislado: bool) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="despertar-"))
    settings.data_dir = tmp
    db = Database(f"sqlite:///{(tmp / 'test.db').as_posix()}")
    db.create_all()
    seed_base(db)
    with db.session() as s:
        set_params(s, {"tick_segundos": 2.0})
    calls, polled, fin_poll, salidas, arr = [], [], [], [], []
    originales = (sch.due_sources, sch.poll_source, sch.process_pending_events, sch.maintenance)
    sch.due_sources = lambda db, now: [] if polled else [1]

    def fake_poll(*a, **kw):
        polled.append(time.monotonic())
        time.sleep(0.05)
        fin_poll.append(time.monotonic())
        return PollOutcome("OK", events=3)

    sch.poll_source = fake_poll
    sch.process_pending_events = lambda *a, **kw: calls.append(time.monotonic())
    sch.maintenance = lambda *a, **kw: None

    async def scenario():
        s1 = sch.Scheduler(db, holder="uno", lease_ttl_s=10)
        espera_orig = s1._sleep_or_wake

        async def espera(delay, batch_s=0.03):
            await espera_orig(delay, batch_s)
            salidas.append(time.monotonic())
        s1._sleep_or_wake = espera
        renovar = s1.lease.acquire_or_renew

        def renovar_medido(now):
            t = time.monotonic()
            r = renovar(now)
            arr.append((t, time.monotonic() - t))
            return r
        s1.lease.acquire_or_renew = renovar_medido
        if aislado:
            async def nada(*a, **kw):
                return None
            s1._captura = s1._history = s1._selfcheck = nada
            s1._dispatch = lambda params: nada()
        s1.start()
        await asyncio.sleep(1.0)
        await s1.stop()
        return s1.wakeups

    try:
        wakeups = asyncio.run(scenario())
    finally:
        sch.due_sources, sch.poll_source, sch.process_pending_events, sch.maintenance = originales
        db.engine.dispose()
    p0 = polled[0] if polled else None
    after = [c for c in calls if p0 is not None and c > p0 + 0.05]
    lat = (after[0] - p0) if after else None
    fp = fin_poll[0] if fin_poll else None
    desp = next((x for x in salidas if fp and x >= fp), None)
    arr_sig = next(((t, d) for t, d in arr if desp and t >= desp - 0.001), None)
    return {"wakeups": wakeups, "latencia_s": lat,
            "consulta_s": (fp - p0) if fp and p0 else None,
            "espera_hasta_salir_s": (desp - fp) if desp and fp else None,
            "arrendamiento_s": arr_sig[1] if arr_sig else None,
            "ciclos_antes_de_procesar": len([c for c in calls if p0 and c <= p0 + 0.05]),
            "falla": lat is None or lat >= LIMITE_S}


def resumir(rs: list[dict]) -> dict:
    def est(clave):
        v = [r[clave] for r in rs if r[clave] is not None]
        if not v:
            return None
        v.sort()
        return {"min": round(v[0], 4), "mediana": round(statistics.median(v), 4),
                "p95": round(v[min(len(v) - 1, int(len(v) * 0.95))], 4), "max": round(v[-1], 4)}
    return {"repeticiones": len(rs), "fallos": sum(r["falla"] for r in rs),
            "latencia_s": est("latencia_s"), "espera_hasta_salir_s": est("espera_hasta_salir_s"),
            "arrendamiento_s": est("arrendamiento_s"), "consulta_s": est("consulta_s")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeticiones", type=int, default=30)
    ap.add_argument("--escenarios", default="normal,carga,aislado,carga_aislado")
    ap.add_argument("--procesos-carga", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--salida", default=str(RAIZ / "resultados" / "investigar_despertar.json"))
    a = ap.parse_args()
    res = {"python": platform.python_version(), "plataforma": platform.platform(), "cpus": os.cpu_count(),
           "limite_s": LIMITE_S, "escenarios": {}}
    for esc in a.escenarios.split(","):
        carga = esc.startswith("carga")
        procs = []
        if carga:
            fin = time.monotonic() + 3600
            procs = [mp.Process(target=_quemar, args=(fin,), daemon=True) for _ in range(a.procesos_carga)]
            for p in procs:
                p.start()
        try:
            rs = [una_ronda(aislado=esc.endswith("aislado")) for _ in range(a.repeticiones)]
        finally:
            for p in procs:
                p.terminate()
        res["escenarios"][esc] = {"resumen": resumir(rs), "rondas": rs}
        print(esc, json.dumps(res["escenarios"][esc]["resumen"], ensure_ascii=False), flush=True)
    Path(a.salida).parent.mkdir(parents=True, exist_ok=True)
    Path(a.salida).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
