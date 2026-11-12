"""Escenario operativo v1.3: motor y proveedor de prueba como SERVICIOS INDEPENDIENTES por HTTP real.

Procesos (todos en 127.0.0.1, nada se publica ni se contacta fuera de la máquina):
  1. proveedor de prueba   python -m proveedor_prueba        (datos ficticios, su propio JSON)
  2. motor                 python -m monitoreo               (su propia base de datos)
  3. emulador de Telegram  uvicorn con monitoreo.emuladores.telegram (sin token real)
  y este script, que hace de "mundo": provoca cambios en el proveedor, simula operadores que
  toman/completan asignaciones por la API del motor y reservan en el proveedor, inyecta fallos y
  reinicia procesos. Al final audita: compara el historial del proveedor (la verdad) con lo que
  el motor registró y calcula los tiempos de punta a punta.

Uso:
  python scripts/integracion_http.py --salida docs/resultados/integracion_http
  python scripts/integracion_http.py --rapido           (versión corta para CI)
Código de salida 0 solo si pasan todas las comprobaciones críticas.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import random
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("MONITOREO_INICIAR_MOTOR", "0")

from sqlalchemy import func, select  # noqa: E402

from monitoreo.db import Database  # noqa: E402
from monitoreo.engine.integrity import check  # noqa: E402
from monitoreo.engine.metricas import percentiles, service_metrics, trace_event  # noqa: E402
from monitoreo.models import (Alert, Assignment, ChangeEvent, Channel, Delivery, PollLog, Request,  # noqa: E402
                              RequestEvent, SlotState)
from proveedor_prueba.proceso import ProveedorProceso, puerto_libre  # noqa: E402
from verificar_arranque import PWD, Server, base_env  # noqa: E402

REGIONES = {"norte": "entrada", "sur": "mesa", "este": "unidad", "oeste": "turno", "centro": "entrada"}
TG_TOKEN = "000000:EMULADOR"
TOKEN_ENV = "PROVEEDOR_TOKEN_MOTOR"


def utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class Sampler(threading.Thread):
    """CPU y memoria REALES de cada servicio (sigue al proceso nuevo tras un reinicio)."""

    def __init__(self):
        super().__init__(daemon=True)
        self.targets: dict[str, psutil.Process] = {}
        self.cpu: dict[str, list] = {}
        self.rss: dict[str, list] = {}
        self.stop_ev = threading.Event()
        self.lock = threading.Lock()

    def track(self, name: str, pid: int) -> None:
        with self.lock:
            p = psutil.Process(pid)
            p.cpu_percent(None)
            self.targets[name] = p
            self.cpu.setdefault(name, [])
            self.rss.setdefault(name, [])

    def run(self):
        while not self.stop_ev.wait(1):
            with self.lock:
                for name, p in self.targets.items():
                    try:
                        self.cpu[name].append(p.cpu_percent(None))
                        self.rss[name].append(p.memory_info().rss / 2**20)
                    except psutil.Error:
                        pass

    def summary(self) -> dict:
        out = {}
        for name in self.cpu:
            c, r = self.cpu[name], self.rss[name]
            out[name] = {"muestras": len(c), "cpu_pct_media": round(statistics.fmean(c), 1) if c else None,
                         "cpu_pct_max": round(max(c), 1) if c else None,
                         "memoria_mb_media": round(statistics.fmean(r), 1) if r else None,
                         "memoria_mb_max": round(max(r), 1) if r else None}
        return out


class Emulador:
    def __init__(self, out: Path):
        self.port = puerto_libre()
        self.base = f"http://127.0.0.1:{self.port}/api-prueba/telegram"
        self.out = out

    def start(self):
        code = ("import uvicorn; from fastapi import FastAPI; from monitoreo.emuladores.telegram import router; "
                "app = FastAPI(); app.include_router(router); "
                f"uvicorn.run(app, host='127.0.0.1', port={self.port}, log_level='warning')")
        self._log = open(self.out / "emulador-telegram.log", "ab")
        self.proc = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, stdout=self._log, stderr=subprocess.STDOUT)
        for _ in range(200):
            try:
                if httpx.get(self.base + "/mensajes", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        raise RuntimeError("el emulador de Telegram no arrancó")

    def total(self) -> int:
        return httpx.get(self.base + "/mensajes", timeout=10).json()["total"]

    def stop(self):
        self.proc.terminate()
        self.proc.wait(10)
        self._log.close()


# --------------------------------------------------------------------------- operadores
class Operadores(threading.Thread):
    """Operadores simulados: cada uno toma SUS asignaciones, reserva en el proveedor y completa.
    Un "intruso" intenta a la vez tomar la misma asignación que su dueño (competencia real)."""

    def __init__(self, api: httpx.Client, prov: ProveedorProceso, rng: random.Random, stats: dict):
        super().__init__(daemon=True)
        self.api, self.prov, self.rng, self.stats = api, prov, rng, stats
        self.stop_ev = threading.Event()
        self.pool = ThreadPoolExecutor(8)
        # Las dos "tomas" simultáneas van en OTRO grupo de hilos: si fueran al mismo, 8 operadores
        # esperando a sus tomas ocuparían los 8 hilos y nadie ejecutaría las tomas (bloqueo mutuo
        # del propio banco de pruebas; ocurrió en GitHub Actions con PostgreSQL).
        self.take_pool = ThreadPoolExecutor(16)
        self.taken: dict[int, int] = {}
        self.res_ids: set[str] = set()
        self.lock = threading.Lock()

    def _act(self, aid: int, action: str) -> int:
        try:
            return self.api.post(f"/api/assignments/{aid}/{action}").status_code
        except httpx.HTTPError:
            self.stats["errores_api"] += 1
            return 0

    def handle(self, a: dict, compete: bool) -> None:
        aid = a["id"]
        if compete:   # dos operadores pulsan "tomar" a la vez sobre la misma asignación
            barrier = threading.Barrier(2, timeout=10)

            def take():
                try:
                    barrier.wait()
                except threading.BrokenBarrierError:
                    pass                            # sin pareja a tiempo: toma igualmente
                return self._act(aid, "tomar")
            f1, f2 = self.take_pool.submit(take), self.take_pool.submit(take)
            codes = sorted([f1.result(), f2.result()])
            self.stats["competencias"] += 1
            self.stats["competencias_resultado"][str(codes)] = self.stats["competencias_resultado"].get(str(codes), 0) + 1
            ok = codes.count(200)
            if ok > 1:
                self.stats["dobles_tomas"] += 1
            if ok == 0:
                return
        else:
            if self._act(aid, "tomar") != 200:
                self.stats["tomas_fallidas"] += 1
                return
        with self.lock:
            self.taken[aid] = self.taken.get(aid, 0) + 1
        time.sleep(self.rng.uniform(0.2, 1.5))
        # Reserva REAL en el proveedor (como lo haría el operador en el sistema del proveedor)
        rkey = a.get("resource_key")
        try:
            r = self.prov.http.post(self.prov.base + "/v1/reservas", headers={"Authorization": f"Bearer {self.prov.token}"},
                                    json={"recurso": rkey, "unidades": a["quantity"],
                                          "titular": f"Titular Ficticio {a['request_id']:05d}"}, timeout=5)
            code = r.status_code
        except httpx.HTTPError:
            code = 0
        if code == 201:
            self.stats["reservas_ok"] += 1
            self.res_ids.add(r.json()["id"])
            self.stats["completadas"] += self._persist(aid, "completar")
        else:
            # la unidad ya no estaba (se la llevó otro o venció): se rechaza y la solicitud vuelve a la cola
            self.stats["reservas_rechazadas"] += 1
            self._persist(aid, "rechazar")

    def _persist(self, aid: int, action: str) -> bool:
        """Como una persona: si el motor no responde (p. ej. se está reiniciando), vuelve a intentarlo."""
        for i in range(40):
            code = self._act(aid, action)
            if code == 200:
                return True
            if code == 409:
                return False
            self.stats["reintentos_operador"] += 1
            time.sleep(min(2.0, 0.25 * (i + 1)))
        return False

    def run(self):
        slots: dict[int, str] = {}
        while not self.stop_ev.wait(0.5):
            try:
                act = self.api.get("/api/assignments", params={"status": "ACTIVA", "limit": 100}).json()
                if any(a["slot_id"] not in slots for a in act):
                    slots = {x["id"]: x["resource_key"] for x in self.api.get("/api/slots").json()}
            except (httpx.HTTPError, ValueError):
                self.stats["errores_api"] += 1
                continue
            for a in act:
                if a["id"] in self.taken:
                    continue
                if self.rng.random() < 0.1:
                    continue                        # algunos operadores no atienden: la asignación vencerá
                a["resource_key"] = slots.get(a["slot_id"])
                self.taken.setdefault(a["id"], 0)
                self.pool.submit(self.handle, a, self.rng.random() < 0.15)

    def stop(self):
        self.stop_ev.set()
        self.join(5)
        self.pool.shutdown(wait=True)
        self.take_pool.shutdown(wait=True)


# --------------------------------------------------------------------------- escenario
def run(args) -> dict:
    rng = random.Random(args.semilla)
    out = Path(args.salida)
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="integracion-http-"))
    timeline: list[dict] = []
    checks: list[dict] = []

    def phase(name: str):
        timeline.append({"fase": name, "inicio": utc().isoformat() + "Z"})
        log(f"=== fase: {name}")

    def ck(name: str, ok: bool, detail=None, critical: bool = True):
        checks.append({"comprobacion": name, "ok": bool(ok), "critica": critical, "detalle": detail})
        log(f"{'OK ' if ok else 'FALLO'} {name}  {detail if detail is not None else ''}")
        return ok

    sampler = Sampler()
    sampler.start()
    prov = ProveedorProceso(tmp / "proveedor" / "estado.json", log=out / "proveedor.log",
                            extra=["--recursos-por-region", str(args.recursos_por_region), "--cuota-por-min", "6000"])
    (tmp / "proveedor").mkdir(parents=True, exist_ok=True)
    prov.iniciar()
    sampler.track("proveedor", prov.proc.pid)
    emu = Emulador(out)
    emu.start()

    env = base_env(tmp, "desarrollo")
    env.update({"MONITOREO_DATOS_DEMO": "0", TOKEN_ENV: prov.token, "MONITOREO_TELEGRAM_TOKEN": TG_TOKEN,
                "MONITOREO_TELEGRAM_CHATS": "1001,1002", "MONITOREO_TELEGRAM_API": emu.base,
                # límite de la API del motor ampliado SOLO en este laboratorio: los operadores simulados
                # y la carga inicial de solicitudes hacen muchas más peticiones que una persona
                "MONITOREO_API_LIMITE_MIN": "100000", "MONITOREO_API_ESCRITURA_MIN": "100000"})
    if args.bd:
        env["MONITOREO_DB_URL"] = args.bd
    db_url = env["MONITOREO_DB_URL"]
    engine = Server("motor", env, out)
    engine.start()
    ck("el motor arranca como proceso independiente", True, f"{engine.wait_ready()} s")
    sampler.track("motor", engine.proc.pid)
    api = httpx.Client(base_url=engine.base, timeout=30, trust_env=False)
    tok = api.post("/api/auth/login", json={"username": "admin", "password": PWD}).json()["token"]
    api.headers["Authorization"] = f"Bearer {tok}"

    # ---- configuración por la API del motor (lo mismo que haría un administrador)
    eng_params = {"ttl_asignacion_s": 20, "concurrencia_maxima": 5}
    if args.enfriamiento_circuito is not None:
        eng_params["enfriamiento_circuito_s"] = args.enfriamiento_circuito
    api.put("/api/settings/engine", json=eng_params).raise_for_status()
    for ch in api.get("/api/channels").json():
        if ch["type"] == "telegram":
            api.patch(f"/api/channels/{ch['id']}", json={"enabled": True, "config": {"tipos": ["ASIGNACION", "SISTEMA"]}}
                      ).raise_for_status()
        elif ch["type"] == "simulado":
            api.patch(f"/api/channels/{ch['id']}", json={"enabled": False}).raise_for_status()
    sids = {}
    for reg in REGIONES:
        r = api.post("/api/sources", json={
            "code": f"http-{reg}", "name": f"Proveedor de PRUEBA · {reg}", "region": reg.upper(),
            "availability_type": "reservacion", "connector_type": "proveedor_prueba",
            "config": {"url_base": prov.base, "region": reg, "token_env": TOKEN_ENV, "limite": 25, "timeout_s": 3},
            "interval_s": args.intervalo, "jitter_pct": 0.1, "rate_limit_per_min": 300, "max_retries": 1})
        r.raise_for_status()
        sids[reg] = r.json()["id"]
    ops = []
    for i, reg in enumerate(list(REGIONES) * 2 + [None, None]):
        r = api.post("/api/operators", json={"name": f"Operador Ficticio {i + 1:02d}",
                                             "regions": [reg.upper()] if reg else [], "max_concurrent": 4})
        r.raise_for_status()
        ops.append(r.json()["id"])

    def mk(i):
        reg = rng.choice(list(REGIONES))
        body = {"code": f"SOL-{i:05d}", "holder": f"Titular Ficticio {i:05d}", "region": reg.upper(),
                "resource_type": REGIONES[reg], "priority": rng.choice([1, 2, 3, 3, 4, 5]),
                "quantity": 2 if rng.random() < 0.1 else 1}
        return api.post("/api/requests", json=body).status_code
    t0 = time.monotonic()
    with ThreadPoolExecutor(8) as ex:
        codes = list(ex.map(mk, range(1, args.solicitudes + 1)))
    ck(f"{args.solicitudes} solicitudes creadas por la API", codes.count(201) == args.solicitudes,
       f"{round(time.monotonic() - t0, 1)} s")
    size_before = _db_size(db_url)
    run_start = utc()

    stats = {"competencias": 0, "competencias_resultado": {}, "dobles_tomas": 0, "tomas_fallidas": 0,
             "reservas_ok": 0, "reservas_rechazadas": 0, "completadas": 0, "errores_api": 0, "reintentos_operador": 0}
    operadores = Operadores(api, prov, rng, stats)
    operadores.start()

    # ---- fase 1: línea base
    phase("linea_base")
    time.sleep(args.intervalo * 4)

    # ---- fase 2: liberaciones controladas (tiempos de punta a punta exactos)
    phase("liberaciones_controladas")
    controlled = []
    end = time.monotonic() + args.duracion_controlada
    recent: dict[str, float] = {}
    while time.monotonic() < end:
        reg = rng.choice(list(REGIONES))
        rid = f"{reg[:3].upper()}-{rng.randint(1, args.recursos_por_region):03d}"
        # Cada liberación controlada es una MEDICIÓN aislada: no se repite un recurso hasta que su
        # liberación anterior (y su posible ventana de 6 s) ya no pueda mezclarse con la nueva.
        if time.monotonic() - recent.get(rid, -1e9) < 10:
            continue
        recent[rid] = time.monotonic()
        ventana = rng.choice([None, None, 6.0])
        try:
            r = prov.liberar(rid, rng.randint(1, 2), ventana)
            if r["liberadas"]:
                controlled.append({"recurso": rid, "t": r["t"], "ventana_s": ventana, "unidades": r["liberadas"]})
        except httpx.HTTPError:
            pass
        time.sleep(rng.uniform(0.8, 1.6))

    # ---- fase 3: carga con latencia variable, ráfagas y ventanas cortas
    phase("carga")
    prov.ctl("POST", "/control/simulacion", activa=True, cada_ms=120, ventana_s=3, p_rafaga=0.05,
             p_cancelar_recurso=0.1)
    prov.fallos(latencia_ms=[5, 120], modo="500", prob_fallo=0.03)
    time.sleep(args.duracion_carga)

    # ---- fase 4: fallos del proveedor
    phase("fallos")
    fault_windows = []
    for nombre, cfg in [("429", {"modo": "429", "retry_after": 3, "regiones": ["sur"]}),
                        ("conexion_cortada", {"modo": "cortar", "prob_fallo": 0.4}),
                        ("proveedor_no_responde", {"modo": "colgar", "colgar_s": 6, "regiones": ["este"]}),
                        ("respuestas_antiguas", {"repetir_antigua": 6}),
                        ("cambios_entre_paginas", {"cambiar_entre_paginas": 8})]:
        t_ini = utc()
        prov.fallos(reset=True)
        prov.fallos(latencia_ms=[5, 120], **cfg)
        time.sleep(args.duracion_fallo)
        fault_windows.append({"fallo": nombre, "inicio": t_ini.isoformat() + "Z", "fin": utc().isoformat() + "Z",
                              "regiones": cfg.get("regiones")})
    prov.fallos(reset=True)
    prov.fallos(latencia_ms=[5, 120])

    restarts = {}
    if not args.sin_reinicios:
        # ---- fase 5: corte brusco del motor
        phase("reinicio_motor")
        t_kill = utc()
        engine.proc.kill()
        engine.proc.wait(10)
        time.sleep(4)
        engine.start()
        ready = engine.wait_ready()
        sampler.track("motor", engine.proc.pid)
        t_first_ok = _wait_first_ok(api, after=utc(), timeout=60)
        restarts["motor"] = {"corte": t_kill.isoformat() + "Z", "listo_tras_arrancar_s": ready,
                             "primera_consulta_ok_s": t_first_ok}
        ck("el motor se recupera tras un corte brusco (kill)", t_first_ok is not None, restarts["motor"])
        fault_windows.append({"fallo": "motor_caido", "inicio": t_kill.isoformat() + "Z", "fin": utc().isoformat() + "Z"})
        # ---- fase 6: corte brusco del proveedor
        phase("reinicio_proveedor")
        hist_before_kill = prov.historial()["cambios"]
        t_kill = utc()
        prov.detener(forzar=True)
        time.sleep(3)
        prov.iniciar()
        sampler.track("proveedor", prov.proc.pid)
        prov.ctl("POST", "/control/simulacion", activa=True, cada_ms=120, ventana_s=3, p_rafaga=0.05)
        prov.fallos(latencia_ms=[5, 120])
        t_first_ok = _wait_first_ok(api, after=utc(), timeout=60)
        restarts["proveedor"] = {"corte": t_kill.isoformat() + "Z", "primera_consulta_ok_s": t_first_ok}
        ck("el motor se recupera tras un corte brusco del proveedor", t_first_ok is not None, restarts["proveedor"])
        fault_windows.append({"fallo": "proveedor_caido", "inicio": t_kill.isoformat() + "Z",
                              "fin": utc().isoformat() + "Z"})
        time.sleep(args.duracion_fallo)
    else:
        hist_before_kill = []

    # ---- fase 7: drenaje y convergencia
    phase("drenaje")
    prov.ctl("POST", "/control/simulacion", activa=False)
    prov.fallos(reset=True)
    # Esperar a que TODAS las fuentes vuelvan a estar al día (un circuito abierto por los fallos
    # tarda `enfriamiento_circuito_s` en volver a probar). Se mide cuánto tarda.
    t_drain = time.monotonic()
    params = api.get("/api/settings/engine").json()
    limit_s = float(params.get("enfriamiento_circuito_s", 60)) + 30
    recovered = None
    while time.monotonic() - t_drain < limit_s:
        srcs = api.get("/api/sources").json()
        if all(x["circuit_state"] == "CERRADO" and x["last_outcome"] == "OK" for x in srcs):
            recovered = round(time.monotonic() - t_drain, 1)
            break
        time.sleep(0.5)
    ck("todas las fuentes vuelven a estar al día tras los fallos", recovered is not None,
       {"segundos": recovered, "limite_s": limit_s,
        "circuito_enfriamiento_s": params.get("enfriamiento_circuito_s")})
    time.sleep(max(5, args.intervalo * 5))
    operadores.stop()
    # un recorrido por la API del motor (lo que ve el panel)
    m_api = api.get("/api/metricas/servicio", params={"minutos": 120}).json()
    some_event = next((a.get("change_event_id") for a in api.get("/api/alerts", params={"limit": 500}).json()
                       if a.get("kind") == "ASIGNACION" and a.get("change_event_id")), None)
    trace_api = api.get(f"/api/eventos/{some_event}/recorrido").json() if some_event else None
    ck("la API devuelve el recorrido de un evento", bool(trace_api and trace_api.get("pasos")),
       len(trace_api["pasos"]) if trace_api else None)
    # convergencia: lo que cree el motor == lo que publica el proveedor
    slots = {x["resource_key"]: x for x in api.get("/api/slots").json()}
    mismatches = []
    for reg in REGIONES:
        for it in prov.disponibilidad(reg):
            st = slots.get(it["id"])
            expected = 0 if it["estado"] != "ABIERTO" else it["disponibles"]
            if st is None or (st["available"] if st["status"] == "ACTIVO" else 0) != expected:
                mismatches.append({"recurso": it["id"], "proveedor": expected,
                                   "motor": None if st is None else (st["available"], st["status"])})
    ck("convergencia: el motor ve exactamente lo que publica el proveedor", not mismatches,
       {"distintos": len(mismatches), "ejemplos": mismatches[:5]})
    hist_after = prov.historial()["cambios"]
    prov_state = prov.estado()
    tg_messages = emu.total()

    # ---- parada ordenada
    engine.stop()
    prov.detener()
    emu.stop()
    sampler.stop_ev.set()
    size_after = _db_size(db_url)
    run_end = utc()

    # ---- auditoría sobre la base del motor (ya detenido)
    history = _merge_history(hist_before_kill, hist_after)
    audit = _audit(db_url, history, controlled, fault_windows, run_start, run_end, args.intervalo,
                   operadores.res_ids)
    with _db(db_url) as s:
        problems = check(s)
        metrics = service_metrics(s, since=run_start, now=run_end)
        trace_db = trace_event(s, some_event) if some_event else None
        tg_sent = s.scalar(select(func.count()).select_from(Delivery).join(Channel, Channel.id == Delivery.channel_id)
                           .where(Channel.type == "telegram", Delivery.status == "ENVIADA"))
        tg_alerts = s.scalar(select(func.count()).select_from(Delivery).join(Channel, Channel.id == Delivery.channel_id)
                             .where(Channel.type == "telegram"))
        double_live = s.scalar(select(func.count()).select_from(
            select(Assignment.request_id).where(Assignment.status.in_(["ACTIVA", "EN_PROCESO"]))
            .group_by(Assignment.request_id).having(func.count() > 1).subquery()))
        # Retenido por asignaciones AÚN NO TOMADAS no puede superar lo disponible. Las EN_PROCESO se
        # respetan (el operador ya está trabajando y probablemente ya reservó en el proveedor).
        in_progress = dict(s.execute(select(Assignment.slot_id, func.sum(Assignment.quantity))
                                     .where(Assignment.status == "EN_PROCESO").group_by(Assignment.slot_id)).all())
        over_held = sum(1 for st in s.scalars(select(SlotState).where(SlotState.status == "ACTIVO"))
                        if st.held - (in_progress.get(st.id) or 0) > st.available)
        stuck = s.scalar(select(func.count()).select_from(Assignment).where(Assignment.status == "EN_PROCESO"))
        hist_rows = s.scalar(select(func.count()).select_from(RequestEvent))
        n_polls = s.scalar(select(func.count()).select_from(PollLog).where(PollLog.started_at >= run_start))
        assignments_total = s.scalar(select(func.count()).select_from(Assignment))
        completed = s.scalar(select(func.count()).select_from(Request).where(Request.status == "COMPLETADO"))
    ck("integridad de la base del motor (sin asignaciones incompatibles)", not problems, problems[:5])
    ck("ninguna solicitud con dos asignaciones vivas", double_live == 0, double_live)
    ck("ningún recurso con más unidades retenidas (sin tomar) que disponibles", over_held == 0, over_held)
    ck("ninguna asignación quedó a medias (EN_PROCESO) al terminar", stuck == 0, stuck, critical=False)
    ck("dos operadores nunca toman la misma asignación", stats["dobles_tomas"] == 0,
       {"competencias": stats["competencias"], "resultados": stats["competencias_resultado"]})
    ck("Telegram (emulador): cada entrega ENVIADA llegó a los 2 chats", tg_messages == 2 * tg_sent and tg_sent > 0,
       {"entregas_enviadas": tg_sent, "entregas_totales": tg_alerts, "mensajes_en_emulador": tg_messages})
    ck("cambios duraderos del proveedor no detectados FUERA de ventanas de fallo",
       audit["no_detectados_fuera_de_fallos"] == 0,
       {k: audit[k] for k in ("total_cambios", "detectados", "coalescidos", "no_detectados_fuera_de_fallos",
                              "no_detectados_en_fallos", "no_detectados_fuente_sin_datos",
                              "anticipados_por_el_motor", "coincide_con_descuento_local", "sin_cambio_neto")})
    cc = audit["controladas"]
    ck("todas las liberaciones controladas se detectaron (salvo las tapadas por otro cambio del recurso antes "
       "de la siguiente consulta, que se cuentan aparte)",
       cc["detectadas"] + cc["mezcladas_con_otro_cambio"] == cc["total"], cc)

    return {
        "version": "1.3", "fecha": run_start.isoformat() + "Z", "rapido": args.rapido,
        "naturaleza": ("MEDIDO con HTTP real entre procesos independientes en 127.0.0.1. Datos ficticios. "
                       "No es una prueba contra ningún proveedor externo."),
        "maquina": {"sistema": platform.platform(), "python": platform.python_version(), "cpus": os.cpu_count()},
        "base_de_datos": "PostgreSQL" if db_url.startswith("postgresql") else "SQLite",
        "parametros": {"solicitudes": args.solicitudes, "regiones": len(REGIONES), "operadores": len(ops),
                       "recursos_por_region": args.recursos_por_region, "intervalo_consulta_s": args.intervalo,
                       "duracion_controlada_s": args.duracion_controlada, "duracion_carga_s": args.duracion_carga,
                       "duracion_por_fallo_s": args.duracion_fallo, "semilla": args.semilla,
                       "parametros_motor_cambiados": eng_params},
        "fases": timeline, "ventanas_de_fallo": fault_windows, "reinicios": restarts,
        "comprobaciones": checks,
        "metricas_servicio": metrics, "metricas_api_durante_la_prueba": m_api,
        "auditoria_cambios": audit, "operadores": stats,
        "totales": {"consultas": n_polls, "asignaciones": assignments_total, "solicitudes_completadas": completed,
                    "filas_historial_solicitudes": hist_rows},
        "telegram_emulador": {"entregas_enviadas": tg_sent, "mensajes_recibidos": tg_messages},
        "proveedor": {"arranques": prov_state["arranques"], "metricas": prov_state["metricas"],
                      "fallos_aplicados": prov_state["fallos"]["aplicados"]},
        "recursos_sistema": sampler.summary(),
        "almacenamiento": {"bytes_antes": size_before, "bytes_despues": size_after,
                           "crecimiento_bytes": size_after - size_before,
                           "duracion_s": round((run_end - run_start).total_seconds(), 1),
                           "bytes_por_consulta": round((size_after - size_before) / n_polls, 1) if n_polls else None},
        "recorrido_ejemplo": trace_db,
    }


class _db:
    def __init__(self, url):
        self.d = Database(url)

    def __enter__(self):
        self.cm = self.d.session()
        return self.cm.__enter__()

    def __exit__(self, *a):
        r = self.cm.__exit__(*a)
        self.d.engine.dispose()
        return r


def _db_size(url: str) -> int:
    from capacidad import db_size
    return db_size(url)


def _wait_first_ok(api: httpx.Client, after: datetime, timeout: float) -> float | None:
    """Segundos hasta la primera consulta correcta posterior a `after` (vista por la API del motor)."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            polls = api.get("/api/polls", params={"outcome": "OK", "limit": 5}).json()
            if any(parse(p["started_at"]) >= after for p in polls):
                return round(time.monotonic() - t0, 2)
        except (httpx.HTTPError, ValueError, TypeError):
            pass
        time.sleep(0.25)
    return None


def _merge_history(before: list[dict], after: list[dict]) -> list[dict]:
    """Tras un corte brusco el proveedor puede perder sus últimos cambios y reutilizar números:
    se unen por (hora, recurso) para no perder ninguno de los que existieron."""
    seen, out = set(), []
    for h in before + after:
        k = (h["t"], h["recurso"], h["motivo"])
        if k not in seen:
            seen.add(k)
            out.append(h)
    return sorted(out, key=lambda h: h["t"])


def _audit(url, history, controlled, fault_windows, start, end, interval, own_reservations=frozenset()) -> dict:
    """Compara la verdad del proveedor con los eventos del motor.

    - detectado: el motor tiene un evento de ese recurso con observed_at == hora del cambio.
    - coalescido: el estado duró menos de 2 intervalos de consulta y lo sustituyó otro cambio antes
      de poder verse; una consulta periódica no puede verlo (no es una pérdida del motor).
    - anticipado: reserva hecha por un operador del motor; al completar la asignación el motor ya
      descontó la unidad, así que la fuente solo confirma lo que el motor ya sabía (sin evento).
    - no detectado: duró lo suficiente y el motor nunca lo registró. Se separa si ocurrió
      durante una ventana de fallo o reinicio (esperable) o fuera de ellas (defecto).
    """
    with _db(url) as s:
        evs = s.execute(select(ChangeEvent.resource_key, ChangeEvent.observed_at, ChangeEvent.detected_at,
                               ChangeEvent.id)).all()
        asg = {a.change_event_id: a.created_at for a in s.scalars(select(Assignment))}
        # Descuentos locales al completar (el motor resta la unidad sin esperar a la fuente)
        local: dict[str, list] = {}
        for rk, closed in s.execute(select(SlotState.resource_key, Assignment.closed_at)
                                    .join(SlotState, SlotState.id == Assignment.slot_id)
                                    .where(Assignment.status == "COMPLETADA")):
            local.setdefault(rk, []).append(closed)
        tg = {}
        for d, al_ in s.execute(select(Delivery, Alert).join(Alert, Alert.id == Delivery.alert_id)
                                .join(Channel, Channel.id == Delivery.channel_id)
                                .where(Channel.type == "telegram", Delivery.status == "ENVIADA")):
            if al_.change_event_id:
                tg[al_.change_event_id] = d.delivered_at
        # Periodos "a ciegas": huecos entre consultas correctas de una fuente mayores que 3 intervalos
        # (circuito abierto, proveedor caído, motor caído...). Un cambio que vive solo dentro de un
        # hueco no podía verse; se informa aparte y se suma el tiempo a ciegas de cada fuente.
        from monitoreo.models import Source
        region_of = {x.id: x.config.get("region") for x in s.scalars(select(Source))}
        ok_times: dict[str, list] = {}
        for sid, t in s.execute(select(PollLog.source_id, PollLog.started_at).where(
                PollLog.outcome == "OK", PollLog.started_at >= start).order_by(PollLog.started_at)):
            ok_times.setdefault(region_of.get(sid), []).append(t)
    blind: dict[str, list] = {}
    for reg, ts in ok_times.items():
        ts = [start] + ts + [end]
        blind[reg] = [(a_, b_) for a_, b_ in zip(ts, ts[1:]) if (b_ - a_).total_seconds() > 3 * interval]
    detected_at_by_res: dict[str, list] = {}
    for rk, obs, det, eid in evs:
        detected_at_by_res.setdefault(rk, []).append(det)
    seen = {}
    for rk, obs, det, eid in evs:
        if obs is not None:
            seen.setdefault((rk, obs.replace(microsecond=obs.microsecond)), (det, eid))
    windows = [(parse(w["inicio"]), parse(w["fin"])) for w in fault_windows]
    hist = [h for h in history if parse(h["t"]) >= start and (h["antes"] != h["despues"] or h["motivo"] == "recurso cancelado")]
    by_res: dict[str, list] = {}
    for h in hist:
        by_res.setdefault(h["recurso"], []).append(h)
    res = {"total_cambios": len(hist), "detectados": 0, "coalescidos": 0, "no_detectados_fuera_de_fallos": 0,
           "no_detectados_en_fallos": 0, "no_detectados_fuente_sin_datos": 0, "anticipados_por_el_motor": 0,
           "coincide_con_descuento_local": 0, "sin_cambio_neto": 0, "vencimientos_detectados_por_el_conector": 0, "ejemplos_no_detectados": [],
           "tiempo_a_ciegas_s": {reg: round(sum((b_ - a_).total_seconds() for a_, b_ in g), 1)
                                 for reg, g in blind.items()}}
    region_by_res = {h["recurso"]: h["region"] for h in hist}
    threshold = 2 * interval + 1.5
    for rk, lst in by_res.items():
        known = lst[0]["antes"]          # último valor que el motor llegó a conocer
        for i, h in enumerate(lst):
            t = parse(h["t"])
            nxt = parse(lst[i + 1]["t"]) if i + 1 < len(lst) else end
            own = h["motivo"].startswith("reserva ") and h["motivo"].split()[1] in own_reservations
            # Un descuento local del motor solo explica una BAJADA de unidades. (Antes también se
            # aplicaba a subidas: una cancelación justo después de completar dejaba "known" mal y
            # la auditoría contaba como perdido un cambio que el motor sí tenía bien.)
            near_local = h["despues"] < h["antes"] and any(
                abs((c - t).total_seconds()) <= 2 * interval + 1 for c in local.get(rk, []))
            if (rk, t) in seen:
                res["detectados"] += 1
                known = h["despues"]
            elif h["motivo"] == "ventana vencida" and any(
                    abs((d - t).total_seconds()) <= interval + 1 for d in detected_at_by_res.get(rk, [])):
                # el conector descuenta la ventana en cuanto vence (aunque la respuesta aún la contara):
                # el motor registra el cambio por su cuenta, a veces antes de que el proveedor lo anote
                res["detectados"] += 1
                res["vencimientos_detectados_por_el_conector"] += 1
                known = h["despues"]
            elif (nxt - t).total_seconds() < threshold:
                res["coalescidos"] += 1
                if own or near_local:
                    known = h["despues"]   # el motor ya lo había descontado por su cuenta
            elif h["despues"] == known:
                # "ida y vuelta": el cambio anterior duró demasiado poco para verse y este devuelve
                # el recurso al valor que el motor ya tenía; no hay diferencia que registrar
                res["sin_cambio_neto"] += 1
            elif own:
                res["anticipados_por_el_motor"] += 1
                known = h["despues"]
            elif near_local:
                # el motor acababa de descontar localmente una unidad de ese recurso: su estado ya
                # coincidía con el nuevo valor y no había diferencia que registrar
                res["coincide_con_descuento_local"] += 1
                known = h["despues"]
            elif any(a <= t <= b or a <= nxt <= b for a, b in windows):
                res["no_detectados_en_fallos"] += 1
            elif any(a_ <= t <= b_ for a_, b_ in blind.get(region_by_res[rk], [])):
                # ocurrió mientras el motor no recibía datos de esa fuente: solo puede ver el estado neto
                # al recuperarse (la comprobación de convergencia verifica que ese estado final es correcto)
                res["no_detectados_fuente_sin_datos"] += 1
            else:
                known = h["despues"]  # (el motor lo verá en el siguiente cambio)
                res["no_detectados_fuera_de_fallos"] += 1
                if len(res["ejemplos_no_detectados"]) < 5:
                    res["ejemplos_no_detectados"].append(h)
    # liberaciones controladas: tiempos exactos de punta a punta
    det, to_asg, to_tg = [], [], []
    found = 0
    mixed = 0
    for c in controlled:
        t = parse(c["t"])
        hit = seen.get((c["recurso"], t))
        if not hit:
            # Otro cambio del MISMO recurso (p. ej. la reserva de un operador por una asignación
            # anterior) antes de la siguiente consulta: la consulta solo puede ver el estado neto.
            later = [parse(h["t"]) for h in by_res.get(c["recurso"], []) if parse(h["t"]) > t]
            if later and (min(later) - t).total_seconds() < threshold:
                mixed += 1
                found += 1
                continue
        if hit:
            found += 1
            det.append((hit[0] - t).total_seconds() * 1000)
            if hit[1] in asg:
                to_asg.append((asg[hit[1]] - t).total_seconds() * 1000)
            if hit[1] in tg:
                to_tg.append((tg[hit[1]] - t).total_seconds() * 1000)
    res["controladas"] = {"total": len(controlled), "detectadas": found - mixed, "mezcladas_con_otro_cambio": mixed, "deteccion_ms": percentiles(det),
                          "hasta_asignacion_ms": percentiles(to_asg), "hasta_telegram_ms": percentiles(to_tg)}
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--salida", default=str(ROOT / "resultados" / "integracion_http"))
    ap.add_argument("--solicitudes", type=int, default=1000)
    ap.add_argument("--recursos-por-region", type=int, default=40)
    ap.add_argument("--intervalo", type=float, default=1.0, help="segundos entre consultas por fuente")
    ap.add_argument("--duracion-controlada", type=float, default=60)
    ap.add_argument("--duracion-carga", type=float, default=120)
    ap.add_argument("--duracion-fallo", type=float, default=12)
    ap.add_argument("--semilla", type=int, default=13)
    ap.add_argument("--bd", help="URL de la base del motor (por defecto SQLite temporal)")
    ap.add_argument("--sin-reinicios", action="store_true")
    ap.add_argument("--enfriamiento-circuito", type=float, help="segundos (por defecto, el valor del motor: 60)")
    ap.add_argument("--rapido", action="store_true", help="versión corta (CI)")
    args = ap.parse_args()
    if args.rapido:
        args.solicitudes = min(args.solicitudes, 500)
        args.duracion_controlada, args.duracion_carga, args.duracion_fallo = 25, 40, 6
    res = run(args)
    out = Path(args.salida)
    name = (f"integracion_http_{res['base_de_datos'].lower()}{'_rapido' if args.rapido else ''}"
            f"{'_enfriamiento' + str(int(args.enfriamiento_circuito)) if args.enfriamiento_circuito is not None else ''}.json")
    (out / name).write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    failed = [c for c in res["comprobaciones"] if c["critica"] and not c["ok"]]
    log(f"resultado en {out / name}: {len(res['comprobaciones']) - len(failed)}/{len(res['comprobaciones'])} "
        "comprobaciones correctas")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
