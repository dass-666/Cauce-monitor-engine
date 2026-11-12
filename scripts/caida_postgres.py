"""Caídas REALES de PostgreSQL con el servidor en marcha (Fase 1.1). Medición, no simulación.

Arranca el servidor real contra una base PostgreSQL DE PRUEBAS, genera actividad, detiene
PostgreSQL varias veces (comando configurable) y mide:
- cuánto tarda /api/health en volver a "OK" tras recuperar la base;
- si el proceso sobrevive, si el planificador sigue siendo líder y si sigue haciendo ciclos;
- integridad de datos al final (asignaciones, cargas, retenciones).

ATENCIÓN: borra por completo la base indicada. Úsalo solo con una base de pruebas.
Ejemplo (Linux):
  python scripts/caida_postgres.py --db postgresql+psycopg://postgres@/monitoreo_bench?host=/tmp/pg \
     --parar "pg_ctl -D /tmp/pg/data stop -m fast" --arrancar "pg_ctl -D /tmp/pg/data start -l /tmp/pg/log -o '-k /tmp/pg'"
En Windows (servicio):  --parar "net stop postgresql-x64-16"  --arrancar "net start postgresql-x64-16"
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

PWD = "Caida-Postgres-123"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--parar", required=True)
    ap.add_argument("--arrancar", required=True)
    ap.add_argument("--puerto", type=int, default=8131)
    ap.add_argument("--caidas", type=int, default=3)
    ap.add_argument("--duracion", type=float, default=10, help="segundos con la base detenida")
    ap.add_argument("--salida")
    a = ap.parse_args()

    from monitoreo.db import Database
    d = Database(a.db)
    d.reset_for_tests()
    d.engine.dispose()

    base = f"http://127.0.0.1:{a.puerto}"
    env = {**os.environ, "MONITOREO_DB_URL": a.db, "MONITOREO_ADMIN_PASSWORD": PWD, "MONITOREO_PUERTO": str(a.puerto),
           "MONITOREO_SECRET_KEY": "caida", "MONITOREO_DATOS_DEMO": "1", "MONITOREO_INICIAR_MOTOR": "1",
           "MONITOREO_LOG": "warning"}
    log = open(Path(os.environ.get("TMPDIR", "/tmp")) / f"caida_postgres_{a.puerto}.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "monitoreo"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    cli = httpx.Client(timeout=5, trust_env=False)
    try:
        for _ in range(120):
            try:
                if cli.get(base + "/api/health").json().get("rol") == "LIDER":
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)
        H = {"Authorization": "Bearer " + cli.post(base + "/api/auth/login",
                                                   json={"username": "admin", "password": PWD}).json()["token"]}
        samples: list[tuple[float, str, str]] = []
        stop = threading.Event()
        t_zero = time.monotonic()

        def probe():
            while not stop.is_set():
                try:
                    r = cli.get(base + "/api/health").json()
                    samples.append((time.monotonic() - t_zero, r["base_de_datos"], r["rol"]))
                except Exception as e:  # noqa: BLE001
                    samples.append((time.monotonic() - t_zero, f"SIN_RESPUESTA({type(e).__name__})", "?"))
                time.sleep(0.25)
        threading.Thread(target=probe, daemon=True).start()

        api_ok = api_fail = 0
        load_stop = threading.Event()

        def load():  # actividad continua: solicitudes nuevas y liberaciones en el laboratorio
            nonlocal api_ok, api_fail
            i = 0
            items = [x["id"] for x in cli.get(base + "/api/lab/items", headers=H).json()][:20]
            while not load_stop.is_set():
                i += 1
                try:
                    r1 = cli.post(base + "/api/requests", headers=H, json={
                        "code": f"PG-{i:05d}", "holder": "Expediente ficticio", "region": "NORTE",
                        "resource_type": "entrada", "priority": 2, "quantity": 1})
                    r2 = cli.post(f"{base}/api/lab/items/{items[i % len(items)]}/liberar", headers=H, json={"units": 1})
                    ok = r1.status_code < 300 and r2.status_code < 300
                except Exception:  # noqa: BLE001
                    ok = False
                api_ok += ok
                api_fail += not ok
                time.sleep(0.3)
        lt = threading.Thread(target=load, daemon=True)
        lt.start()
        time.sleep(10)
        outages = []
        for k in range(a.caidas):
            t_down = time.monotonic() - t_zero
            subprocess.run(shlex.split(a.parar), check=True, capture_output=True)
            time.sleep(a.duracion)
            subprocess.run(shlex.split(a.arrancar), check=True, capture_output=True)
            t_up = time.monotonic() - t_zero
            # tiempo hasta que /api/health vuelve a OK
            t_ok = None
            for _ in range(240):
                last = samples[-1] if samples else None
                if last and last[0] > t_up and last[1] == "OK":
                    t_ok = last[0]
                    break
                time.sleep(0.25)
            seen = {s[1] for s in samples if t_down <= s[0] <= t_up}
            outages.append({"caida": k + 1, "base_detenida_s": round(t_up - t_down, 1),
                            "estados_vistos_durante_la_caida": sorted(seen),
                            "health_ok_tras_arrancar_la_base_s": round(t_ok - t_up, 2) if t_ok else None})
            time.sleep(15)
        load_stop.set()
        lt.join()
        time.sleep(5)
        stop.set()
        obs = cli.get(base + "/api/observabilidad", headers=H).json()
        metrics = cli.get(base + "/api/metrics", headers=H).json()
        c1 = cli.get(base + "/api/health").json()["ciclos"]
        time.sleep(3)
        c2 = cli.get(base + "/api/health").json()["ciclos"]
        res = {
            "aviso": "Medición real: servidor y PostgreSQL reales en esta máquina; las caídas son paradas reales de PostgreSQL.",
            "caidas": outages,
            "proceso_vivo_al_final": proc.poll() is None,
            "rol_final": obs["motor"]["rol"], "ciclos_siguen_avanzando": c2 > c1,
            "errores_ciclo_seguidos_al_final": obs["motor"].get("errores_ciclo_seguidos"),
            "peticiones_api_ok": api_ok, "peticiones_api_fallidas": api_fail,
            "integridad_final": metrics["integridad"],
            "memoria_proceso_mb": obs["proceso"]["memoria_mb"],
        }
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
    text = json.dumps(res, ensure_ascii=False, indent=2)
    print(text)
    if a.salida:
        Path(a.salida).write_text(text, encoding="utf-8")
    ok = res["proceso_vivo_al_final"] and res["ciclos_siguen_avanzando"] and not res["integridad_final"] \
        and all(o["health_ok_tras_arrancar_la_base_s"] is not None for o in outages)
    print("\nRESULTADO:", "SE RECUPERÓ EN TODAS LAS CAÍDAS" if ok else "HAY PROBLEMAS")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
