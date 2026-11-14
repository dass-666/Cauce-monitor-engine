"""v2.1 · Investigación REPRODUCIBLE del bloqueo del observador tras una sesión caducada.

Cada ciclo: el laboratorio caduca la sesión de la vista, el observador la ve, se le ordena reabrir y se mide
cuánto tarda en confirmar (REABIERTA). Un ciclo sin confirmación en 20 s cuenta como bloqueo. Con
DEBUG=pw:api queda en stderr.log el registro de Playwright (ahí se ve la captura en curso durante la
navegación). Para comparar con la versión SIN candado de página:
  git show 0d0d755:scripts/observador_calendario.js > scripts/_obs_sin_candado.js   (dentro de scripts/:
  así Node encuentra Playwright igual que el observador)
  python scripts/investigar_bloqueo_observador.py scripts/_obs_sin_candado.js 60 salida_sin
  python scripts/investigar_bloqueo_observador.py scripts/observador_calendario.js 600 salida_con
(en la investigación se saturó la CPU con 6 bucles ocupados en paralelo).
Uso: python scripts/investigar_bloqueo_observador.py <script.js> <ciclos> <salida_dir>"""
import json, os, subprocess, sys, tempfile, threading, time
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from pathlib import Path
from portal_reservas.proceso import PortalProceso

script, ciclos, out = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)
tmp = Path(tempfile.mkdtemp())
p = PortalProceso(tmp / "portal.json", extra=["--zona-horaria", "America/Mexico_City"]); p.iniciar()
d = p.ctl("GET", "/control/dias"); suc = "NOR-1"
cfg = {"url": p.base + f"/observar/calendario?sucursal={suc}&tramite=alta", "estrategia": "ambas",
       "intervalo_visual_ms": 250, "congelado_s": 5, "evidencia_dir": None}
cf = tmp / "cfg.json"; cf.write_text(json.dumps(cfg))
env = {**os.environ, "DEBUG": "pw:api"}
err = open(out / "stderr.log", "w")
proc = subprocess.Popen(["node", script, str(cf)], cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]), stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=err, text=True, bufsize=1, env=env)
lineas = []; ev = threading.Condition()
def lector():
    for l in proc.stdout:
        if l.startswith("OBS "):
            o = json.loads(l[4:]); o["_t"] = time.monotonic()
            with ev: lineas.append(o); ev.notify_all()
threading.Thread(target=lector, daemon=True).start()
def esperar(pred, t):
    fin = time.monotonic() + t
    with ev:
        while time.monotonic() < fin:
            for o in lineas:
                if pred(o): return o
            ev.wait(0.2)
    return None
assert esperar(lambda o: o["tipo"] == "LISTO", 60), "no arrancó"
res = []
for i in range(ciclos):
    n0 = len(lineas)
    p.ctl("POST", "/control/vista", caducar_todas=True)
    c = esperar(lambda o: lineas.index(o) >= n0 and o.get("estado") == "SESION_CADUCADA", 20)
    if not c: res.append({"ciclo": i, "fallo": "no vio caducada"}); continue
    time.sleep(0.05 * (i % 10))   # desfase variable respecto al ciclo de captura
    t0 = time.monotonic(); n1 = len(lineas)
    proc.stdin.write('{"cmd":"reabrir"}\n'); proc.stdin.flush()
    r = esperar(lambda o: lineas.index(o) >= n1 and o["tipo"] == "REABIERTA", 20)
    dt = None if r is None else round(r["_t"] - t0, 2)
    res.append({"ciclo": i, "reabrir_s": dt}); print(i, dt, flush=True)
    if r is None:
        err.write(f"\n===== BLOQUEO en ciclo {i} =====\n"); err.flush()
        if not esperar(lambda o: lineas.index(o) >= n1 and o["tipo"] == "REABIERTA", 60): break
fallos = [x for x in res if x.get("reabrir_s") is None]
(out / "resultado.json").write_text(json.dumps({"script": script, "ciclos": len(res), "fallos": len(fallos), "res": res}, indent=1))
try:
    proc.stdin.write('{"cmd":"salir"}\n'); proc.stdin.flush(); proc.wait(10)
except Exception: proc.kill()
p.detener()
