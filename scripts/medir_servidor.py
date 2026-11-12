"""Mide CPU, memoria y latencia de la API de un servidor en marcha.

Uso: python scripts/medir_servidor.py --pid <PID> --usuario admin --clave ... --segundos 60
"""
from __future__ import annotations

import argparse
import json
import statistics
import time

import httpx
import psutil

ap = argparse.ArgumentParser()
ap.add_argument("--pid", type=int, required=True)
ap.add_argument("--url", default="http://127.0.0.1:8000")
ap.add_argument("--usuario", default="admin")
ap.add_argument("--clave", required=True)
ap.add_argument("--segundos", type=int, default=60)
a = ap.parse_args()

proc = psutil.Process(a.pid)
tok = httpx.post(f"{a.url}/api/auth/login", json={"username": a.usuario, "password": a.clave}).json()["token"]
H = {"Authorization": f"Bearer {tok}"}
cpu, mem, lat = [], [], {"/api/dashboard": [], "/api/requests?limit=200": [], "/api/alerts?limit=100": []}
proc.cpu_percent(None)
end = time.time() + a.segundos
while time.time() < end:
    time.sleep(1)
    cpu.append(proc.cpu_percent(None))
    mem.append(proc.memory_info().rss / 1048576)
    for path in lat:
        t = time.perf_counter()
        httpx.get(a.url + path, headers=H).raise_for_status()
        lat[path].append((time.perf_counter() - t) * 1000)
health = httpx.get(f"{a.url}/api/health").json()


def p(v, q):
    v = sorted(v)
    return round(v[min(len(v) - 1, int(q * (len(v) - 1)))], 1)


print(json.dumps({
    "segundos": a.segundos, "cpu_pct_media": round(statistics.fmean(cpu), 1), "cpu_pct_max": max(cpu),
    "memoria_mb_media": round(statistics.fmean(mem), 1), "memoria_mb_max": round(max(mem), 1),
    "latencia_api_ms": {k: {"p50": p(v, .5), "p95": p(v, .95), "max": round(max(v), 1)} for k, v in lat.items()},
    "ciclos_planificador": health["ciclos"]}, indent=2, ensure_ascii=False))
