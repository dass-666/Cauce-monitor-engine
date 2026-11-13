"""LABORATORIO MULTISUCURSAL de la v1.6 (reservación POR DÍA) con procesos reales.

Qué hace (todo ficticio y local, 127.0.0.1):
  1. Arranca el portal de PRUEBA (5 regiones x 2 sucursales x 3 trámites, zona America/Mexico_City),
     amplía sus registros previos sintéticos y AGOTA todo el calendario: la disponibilidad solo
     aparece con las liberaciones de la ventana nocturna SIMULADA (22:00-07:30 del reloj del
     portal, acelerado para que la noche dure --noche-s segundos reales). La ventana es un
     PARÁMETRO DE SIMULACIÓN, no una afirmación sobre ningún proveedor.
  2. Arranca el motor completo y registra la fuente del calendario con el mecanismo indicado:
     A = eventos del servidor, B = consulta periódica cada --intervalo-b s. Con --mecanismo ambos
     se repite el MISMO escenario (misma semilla) con A y con B, cada uno con procesos nuevos.
  3. Crea --operadores operadores (repartidos por región, limitados a sus sucursales) y
     --expedientes expedientes POR_DIA (un --incompletos % incompletos, a propósito).
  4. Los operadores son HILOS que usan la API (SIMULADO: no son personas): preparar -> enviar
     datos con los campos manuales -> elegir un día DISPONIBLE -> confirmar.
  5. Durante la noche inyecta fallos: caída de la API del calendario, sesión de eventos caducada,
     eventos fuera de orden, corte del flujo de eventos, reinicio brusco del portal y del motor.
  6. Mide (MEDIDO) detección, asignación, preparación, confirmación y recuperación, y comprueba
     contra la VERDAD DEL PORTAL: sin sobreventa, folios únicos, cada confirmación con evidencia,
     ningún incompleto asignado, ningún expediente con dos operadores, sin datos personales en
     los registros del motor.

  v1.7 (--v17): además, durante la misma noche:
     - errores de captura en el portal (pantalla de datos con error 500 y con conexión cortada);
     - operadores que dejan de responder a media operación (dos se quedan "congelados" con la sesión
       abierta y uno cierra sesión) -> la SUPERVISIÓN los distingue (CONEXION_PERDIDA / DESCONECTADO)
       y el administrador REDISTRIBUYE su trabajo por la API (transaccional y auditado);
     - confirmaciones inciertas (el portal confirma y corta la respuesta) -> EN_VERIFICACION;
     - un canal de notificaciones LENTO que además falla (webhook local que tarda y responde 503):
       el motor debe seguir detectando y asignando.

Uso:  python scripts/laboratorio_dias.py --mecanismo ambos --expedientes 600 --salida resultados/laboratorio_dias
Código de salida 0 solo si todas las comprobaciones de integridad pasan (las métricas se informan).
"""
from __future__ import annotations

import argparse
import http.server
import json
import platform
import random
import secrets
import statistics
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from portal_reservas.proceso import PortalProceso  # noqa: E402
from verificar_arranque import Checks, Server, base_env  # noqa: E402

PWD = "Laboratorio-Dias-2026"
OPWD = "Operador-Lab-2026"
ZONA = "America/Mexico_City"
REGIONES = {"NORTE": "NOR", "SUR": "SUR", "ESTE": "EST", "OESTE": "OES", "CENTRO": "CEN"}
MANUALES = {"clave_identidad": "LAB482915", "acepto_privacidad": "si"}
ACTIVOS_OP = ("ASIGNADO", "CAPTURA_EN_PROCESO", "RESERVACION_PENDIENTE")


SILABAS = ["ba", "ce", "di", "fo", "gu", "la", "me", "ni", "po", "ru", "sa", "te", "vi", "zo", "ma", "lu"]


def nombre_ficticio(i: int) -> str:
    """Nombre inventado y único, solo letras (la validación no admite dígitos)."""
    a = "".join(SILABAS[(i >> s) & 15] for s in (0, 4, 8)).capitalize()
    return f"Ficticia {a} Laboratorio"


def pct(v, p):
    if not v:
        return None
    v = sorted(v)
    return round(v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))], 3)


def resumen(v) -> dict:
    return {"n": len(v), "p50": pct(v, 50), "p95": pct(v, 95), "max": round(max(v), 3) if v else None,
            "media": round(statistics.mean(v), 3) if v else None}


def iso(x: str | None) -> datetime | None:
    if not x:
        return None
    d = datetime.fromisoformat(x.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def login(base: str, u: str, p: str) -> httpx.Client:
    c = httpx.Client(base_url=base, timeout=30, trust_env=False)
    c.headers["Authorization"] = "Bearer " + c.post("/api/auth/login", json={"username": u, "password": p}).json()["token"]
    return c


def esperar(fn, timeout: float, paso: float = 0.25):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            v = fn()
        except Exception:     # noqa: BLE001  (el motor puede estar reiniciando)
            v = None
        if v:
            return v, round(time.monotonic() - t0, 2)
        time.sleep(paso)
    return None, round(time.monotonic() - t0, 2)


class WebhookLento:
    """Canal de notificaciones de laboratorio que TARDA y FALLA a propósito (127.0.0.1, puerto libre)."""

    def __init__(self, demora_s: float = 2.5):
        self.recibidas, self.fallidas, self.lock = 0, 0, threading.Lock()
        dueno = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):            # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                time.sleep(demora_s)
                with dueno.lock:
                    dueno.recibidas += 1
                    falla = dueno.recibidas % 2 == 0
                    dueno.fallidas += falla
                self.send_response(503 if falla else 200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/webhook"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def detener(self):
        self.srv.shutdown()


class Recolector(threading.Thread):
    """Lee periódicamente los eventos del portal (con su hora REAL) antes de que un reinicio los borre."""

    def __init__(self, portal):
        super().__init__(daemon=True)
        self.portal, self.eventos, self.parar, self.seq = portal, {}, threading.Event(), 0
        self.generacion = 0

    def leer(self):
        try:
            j = self.portal.ctl("GET", f"/control/eventos-dias?desde={self.seq}")
        except Exception:      # noqa: BLE001  (portal reiniciando)
            return
        for e in j.get("eventos", []):
            self.eventos[(self.generacion, e["seq"])] = e
            self.seq = max(self.seq, e["seq"])

    def reinicio_portal(self):
        self.generacion += 1          # la secuencia del portal continúa (se guarda), pero por si acaso

    def run(self):
        while not self.parar.is_set():
            self.leer()
            self.parar.wait(1.0)


def escenario(a, mecanismo: str, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f"labdias-{mecanismo}-"))
    ck = Checks()
    rng = random.Random(a.semilla)
    t_inicio = time.time()
    tz = ZoneInfo(ZONA)

    # ------------------------------------------------------------------ portal
    (tmp / "portal").mkdir(parents=True)
    portal = PortalProceso(tmp / "portal" / "estado.json", token=secrets.token_urlsafe(16),
                           token_control=secrets.token_urlsafe(16), log=out / "portal.log",
                           extra=["--zona-horaria", ZONA, "--semilla", str(a.semilla)])
    portal.iniciar()
    portal.ctl("POST", "/control/api", latido_s=1)
    regs = portal.ctl("POST", "/control/registros?total=" + str(a.expedientes + 50))
    dias = portal.ctl("GET", "/control/dias")["dias"]
    for k, x in dias.items():
        if x["estado"] == "DISPONIBLE":
            portal.ctl("POST", "/control/dias/agotar", clave=k)
    sucursales = sorted({x["sucursal"] for x in dias.values()})
    ck("portal: calendario agotado al inicio (la disponibilidad llega solo de noche)",
       all(x["estado"] != "DISPONIBLE" for x in portal.ctl("GET", "/control/dias")["dias"].values()),
       f"{len(dias)} días · {len(sucursales)} sucursales · {regs['registros']} registros sintéticos")

    # ------------------------------------------------------------------ motor
    env = base_env(tmp, "desarrollo")
    env.update({"MONITOREO_DATOS_DEMO": "0", "MONITOREO_ADMIN_PASSWORD": PWD, "MONITOREO_PORTAL_URL": portal.base,
                "MONITOREO_PORTAL_TOKEN": portal.token, "MONITOREO_ZONA_HORARIA": ZONA,
                # En el laboratorio TODOS los operadores simulados comparten 127.0.0.1 y el límite del
                # motor es por IP; con personas reales cada una tiene su equipo. Solo aquí se amplía.
                "MONITOREO_API_LIMITE_MIN": str(a.limite_api), "MONITOREO_API_ESCRITURA_MIN": str(a.limite_api // 5)})
    motor = Server("motor", env, out)
    motor.start()
    motor.wait_ready()
    adm = login(motor.base, "admin", PWD)
    adm.put("/api/settings/engine", json={"captura_modo": "POR_DIA", "ttl_asignacion_s": 900}).raise_for_status()
    cfg = {"url_base": portal.base, "token_env": "MONITOREO_PORTAL_TOKEN", "timeout_s": 5, "latido_s": 1,
           "mecanismo": "eventos" if mecanismo == "A" else "periodico"}
    r = adm.post("/api/sources", json={"code": "portal-lab-dias", "name": "Portal de prueba · calendario por día",
                                       "region": "CENTRO", "availability_type": "calendario",
                                       "connector_type": "portal_dias", "config": cfg,
                                       "interval_s": a.intervalo_b if mecanismo == "B" else 15, "jitter_pct": 0,
                                       "rate_limit_per_min": 600, "max_retries": 1})
    r.raise_for_status()
    v17: dict = {}
    hook = None
    if a.v17:
        # presencia: el panel marca la señal como mucho cada 15 s; 25 s evita falsos "conexión perdida"
        adm.put("/api/settings/engine", json={"operador_conexion_perdida_s": 25, "operador_inactivo_s": 900}
                ).raise_for_status()
        hook = WebhookLento()
        r = adm.post("/api/channels", json={"name": "Webhook lento de laboratorio", "type": "webhook",
                                            "config": {"url": hook.url, "timeout_s": 4}, "enabled": True})
        v17["canal_lento_creado"] = r.status_code
    ops = []
    for i in range(a.operadores):
        reg = list(REGIONES)[i % 5]
        u = f"op{i + 1:02d}"
        r = adm.post("/api/users", json={"username": u, "password": OPWD, "role": "operador", "regions": [reg],
                                         "sucursales": [f"{REGIONES[reg]}-1", f"{REGIONES[reg]}-2"],
                                         "operator_name": f"Operador Ficticio {i + 1:02d}"})
        r.raise_for_status()
        ops.append(u)
    ck("operadores creados y limitados a las sucursales de su región", len(ops) == a.operadores, len(ops))

    # ------------------------------------------------------------------ expedientes
    nombres, incompletos, ids = [], set(), []
    t0 = time.monotonic()
    for i in range(a.expedientes):
        ident, reg = None, None
        rr = portal.registro(i)
        reg = list(REGIONES)[i % 5]
        pref = rng.choice([1, 2])
        nombre = nombre_ficticio(i)
        d = {"nombre": nombre, "region": reg, "tipo_tramite": rr["tramite"], "modo": "POR_DIA",
             "sucursal": f"{REGIONES[reg]}-{pref}", "prioridad": rng.choice([1, 2, 2, 3]),
             "sucursales_alternativas": [f"{REGIONES[reg]}-{3 - pref}"] if rng.random() < 0.6 else []}
        if rng.random() * 100 >= a.incompletos:
            tel = f"555{i:07d}"
            d.update({"fecha_nacimiento": rr["fecha_nacimiento"], "telefono": tel, "telefono_confirmacion": tel,
                      "correo": f"lab{i}@ejemplo.test", "correo_confirmacion": f"lab{i}@ejemplo.test",
                      "id_registro_previo": rr["identificador"]})
        for _ in range(30):                      # se respeta el límite de peticiones del motor (429)
            r = adm.post("/api/expedientes", json=d)
            if r.status_code != 429:
                break
            time.sleep(min(10.0, float(r.headers.get("retry-after") or 2)))
        if r.status_code != 201:
            ck(f"expediente {i} creado", False, r.text[:200])
            continue
        e = r.json()
        ids.append(e["id"])
        nombres.append(nombre)
        if e["estado"] == "INCOMPLETO":
            incompletos.add(e["id"])
    t_creacion = round(time.monotonic() - t0, 1)
    ck("expedientes ficticios creados por la API", len(ids) == a.expedientes,
       f"{len(ids)} en {t_creacion} s · {len(incompletos)} incompletos")

    # ------------------------------------------------------------------ noche simulada
    noche_h = 9.5
    factor = noche_h * 3600 / a.noche_s
    hoy_local = datetime.now(timezone.utc).astimezone(tz).date()
    inicio_lab = datetime.combine(hoy_local, datetime.min.time(), tz).replace(hour=21, minute=59)
    portal.ctl("POST", "/control/ventana", activa=True, inicio="22:00", fin="07:30", prob=a.prob,
               max_unidades=3, vigencia_min=0)
    portal.ctl("POST", "/control/reloj", fijar=inicio_lab.isoformat(), factor=factor)
    rec = Recolector(portal)
    rec.start()

    # operadores simulados (hilos)
    parar = threading.Event()
    tiempos = {"preparar": [], "enviar": [], "seleccionar": [], "confirmar": []}
    errores_op: dict[str, int] = {}
    lock = threading.Lock()

    def anotar(clave, dur=None, err=None):
        with lock:
            if dur is not None:
                tiempos[clave].append(dur)
            if err:
                errores_op[err] = errores_op.get(err, 0) + 1

    congelados: set[str] = set()          # v1.7: operadores que dejan de responder (SIMULADO)
    cerrar_sesion: set[str] = set()       # v1.7: operadores que cierran SU sesión y se van

    def operador(u):
        hilo_usuario.u = u
        c = None
        prng = random.Random(hash(u) ^ a.semilla)
        while not parar.is_set():
            if u in cerrar_sesion:
                try:
                    if c is not None:
                        c.post("/api/auth/logout")
                except httpx.HTTPError:
                    pass
                c = None
                congelados.add(u)
                cerrar_sesion.discard(u)
            if u in congelados:
                parar.wait(1.0)                # ni una petición: la sesión sigue abierta pero sin señal
                continue
            try:
                if c is None:
                    c = login(motor.base, u, OPWD)
                mios = [e for e in c.get("/api/expedientes", params={"limit": 200}).json() if e["estado"] in ACTIVOS_OP]
                if not mios:
                    parar.wait(1.5)
                    continue
                for e in mios:
                    if parar.is_set() or u in congelados or u in cerrar_sesion:
                        break
                    atender(c, e, prng)
            except (httpx.HTTPError, ValueError, KeyError):
                anotar("x", err="motor_no_responde")
                c = None
                parar.wait(1.0)

    def llamar(c, clave, metodo, url, **kw):
        t = time.monotonic()
        r = c.request(metodo, url, **kw)
        anotar(clave, time.monotonic() - t if r.status_code < 400 else None,
               None if r.status_code < 400 else f"{clave}_{r.status_code}")
        if r.status_code == 401:
            raise httpx.HTTPError("sesión del motor perdida")
        if r.status_code == 429:                    # límite de peticiones del motor: se respeta
            time.sleep(min(10.0, float(r.headers.get("retry-after") or 2)))
        return r

    def elegir_dia(c, rid, cal, prng):
        for intento in range(2):
            libres = [d for d in (cal or {}).get("dias", []) if d.get("estado") == "DISPONIBLE"]
            if libres:
                return prng.choice(libres[:3]), cal       # compiten por los mismos días a propósito
            mes = (cal or {}).get("mes")
            if not mes or intento:
                return None, cal
            y, m = map(int, mes.split("-"))
            sig = f"{y + (m == 12)}-{(m % 12) + 1:02d}"
            r = c.get(f"/api/reservaciones/{rid}/calendario", params={"mes": sig})
            cal = r.json().get("calendario", r.json()) if r.status_code == 200 else None
        return None, cal

    hilo_usuario = threading.local()

    def u_actual():
        return getattr(hilo_usuario, "u", None)

    def atender(c, e, prng):
        time.sleep(prng.uniform(*a.pausa))                 # "lectura" del operador: SIMULADO
        r = llamar(c, "preparar", "POST", f"/api/expedientes/{e['id']}/preparar")
        if r.status_code != 200:
            return
        res = r.json()["reservacion"]
        rid, est = res["id"], res["estado"]
        cal = None
        if est == "PREPARADA":
            time.sleep(prng.uniform(*a.pausa))
            r = llamar(c, "enviar", "POST", f"/api/reservaciones/{rid}/enviar-datos",
                       json={"revisado": True, "manuales": MANUALES})
            if r.status_code != 200:
                return
            cal, est = r.json().get("calendario"), "DATOS_ENVIADOS"
        if u_actual() in congelados:
            return                               # se queda A MEDIAS: datos enviados, sin elegir día
        if est == "DATOS_ENVIADOS":
            if cal is None:
                r = c.get(f"/api/reservaciones/{rid}/calendario")
                if r.status_code != 200:
                    return
                cal = r.json().get("calendario", r.json())
            dia, cal = elegir_dia(c, rid, cal, prng)
            if dia is None:
                anotar("x", err="sin_dias_disponibles_al_preparar")
                return
            r = llamar(c, "seleccionar", "POST", f"/api/reservaciones/{rid}/seleccionar-dia",
                       json={"fecha": dia["fecha"], "sucursal": cal["sucursal"]})
            if r.status_code != 200:
                return
            est = "FECHA_ELEGIDA"
        if est == "FECHA_ELEGIDA":
            time.sleep(prng.uniform(*a.pausa))              # confirmación "humana": SIMULADA en el laboratorio
            llamar(c, "confirmar", "POST", f"/api/reservaciones/{rid}/confirmar", json={"confirmo": True})

    hilos = [threading.Thread(target=operador, args=(u,), daemon=True) for u in ops]
    for h in hilos:
        h.start()
    t_noche = time.monotonic()

    def en(s):
        return t_noche + s * a.noche_s / 300.0                # agenda escalada a la duración de la noche

    def esperar_hasta(t):
        while time.monotonic() < t:
            time.sleep(0.2)

    def fuente():
        f = adm.get("/api/captura/calendario-dias").json()["fuentes"]
        return f[0] if f else {}

    recuperacion = {}
    fallos: list[tuple[datetime, datetime]] = []        # (inicio, fin real) de cada fallo inyectado

    def marca():
        return datetime.now(timezone.utc)

    if a.v17:                      # v1.7 · confirmaciones inciertas (el portal confirma y corta la respuesta)
        esperar_hasta(en(20))
        portal.fallos(modo="cortar_tras_confirmar", restantes=5)
        esperar(lambda: (portal.fallos(latencia_ms=0).get("aplicados") or {}).get("cortar_tras_confirmar", 0) >= 5,
                max(5.0, 25 * a.noche_s / 300.0), 0.5)
        v17["confirmaciones_cortadas"] = portal.fallos(latencia_ms=0).get("aplicados")
        portal.fallos(reset=True)

    # 1) caída de la API del calendario 20 s
    esperar_hasta(en(50))
    f0 = marca()
    portal.ctl("POST", "/control/api", caida=True)
    t = time.monotonic()
    _, det = esperar(lambda: fuente().get("sin_verificar"), 60)
    esperar_hasta(t + 20 * a.noche_s / 300.0)
    portal.ctl("POST", "/control/api", caida=False)
    _, rec_t = esperar(lambda: not fuente().get("sin_verificar") and fuente().get("verificado_en"), 90)
    recuperacion["caida_api"] = {"deteccion_s": det, "recuperacion_s": rec_t}
    fallos.append((f0, marca()))
    ck("caída de la API: el motor la detecta y marca el calendario SIN VERIFICAR", det < 60, f"{det} s")

    if a.v17:                      # v1.7 · errores de captura: la pantalla de datos responde 500
        esperar_hasta(en(80))
        portal.fallos(modo="error_interno", pantallas=["datos"], restantes=12)

    # 2) sesión de eventos caducada
    esperar_hasta(en(100))
    f0 = marca()
    portal.ctl("POST", "/control/api", caducar_sesiones_eventos=True)
    _, t2 = esperar(lambda: not fuente().get("sin_verificar"), 60)
    time.sleep(2)
    fallos.append((f0, marca()))
    recuperacion["sesion_caducada"] = {"verificado_de_nuevo_s": t2}

    if a.v17:                      # v1.7 · operadores que dejan de responder
        esperar_hasta(en(120))
        v17["errores_captura_500"] = portal.fallos(latencia_ms=0).get("aplicados")
        sin_resp = ops[-2:]                       # dos "congelados" con la sesión abierta
        congelados.update(sin_resp)
        cerrar_sesion.add(ops[-3])                # y uno cierra su sesión (la del hilo) y se va
        v17["sin_responder"] = sin_resp
        v17["cerro_sesion"] = ops[-3]

    # 3) eventos fuera de orden
    esperar_hasta(en(130))
    actual = portal.ctl("GET", "/control/dias")["dias"]
    ya = [k for k, x in actual.items() if x["version"] > 2][:5]
    for k in ya:
        portal.ctl("POST", "/control/dias/reenviar-antiguo", clave=k)
    recuperacion["fuera_de_orden_inyectados"] = len(ya)

    if a.v17:                      # v1.7 · errores de captura: conexión cortada al enviar los datos
        esperar_hasta(en(140))
        portal.fallos(reset=True)
        portal.fallos(modo="cortar", pantallas=["datos"], restantes=8)

    # 4) corte del flujo de eventos
    esperar_hasta(en(160))
    f0 = marca()
    portal.ctl("POST", "/control/api", cortar_eventos=True)
    time.sleep(3)
    fallos.append((f0, marca()))

    if a.v17:                      # v1.7 · supervisión y redistribución
        # la señal de los congelados tiene que superar el umbral (25 s) + el margen de 15 s del panel
        esperar_hasta(max(en(172), time.monotonic()))
        esperar(lambda: all(o["presencia"] != "CONECTADO" for o in adm.get("/api/supervision").json()["operadores"]
                            if o["nombre"] in {f"Operador Ficticio {int(u[2:]):02d}" for u in congelados | cerrar_sesion}), 45, 1.0)
        v17["cortes_datos"] = portal.fallos(latencia_ms=0).get("aplicados")
        portal.fallos(reset=True)
        sup = adm.get("/api/supervision").json()["operadores"]
        nombre_de = {u: f"Operador Ficticio {int(u[2:]):02d}" for u in ops}
        pres = {o["nombre"]: o["presencia"] for o in sup}
        v17["presencia_antes"] = {u: pres.get(nombre_de[u]) for u in ops}
        v17["redistribuciones"] = {}
        for u in sorted(congelados | cerrar_sesion):
            oid = next(o["id"] for o in sup if o["nombre"] == nombre_de[u])
            r = adm.post(f"/api/supervision/operadores/{oid}/redistribuir",
                         json={"confirmo": True, "motivo": "laboratorio: el operador no responde",
                               "incluir_en_proceso": True, "desactivar": True})
            v17["redistribuciones"][u] = r.json() if r.status_code == 200 else {"error": r.status_code, "texto": r.text[:200]}

    # 5) reinicio brusco del portal
    esperar_hasta(en(190))
    rec.leer()
    f0 = marca()
    t = time.monotonic()
    portal.detener(forzar=True)
    _, det = esperar(lambda: fuente().get("sin_verificar"), 60)
    portal.iniciar()
    rec.reinicio_portal()
    _, rec_t = esperar(lambda: not fuente().get("sin_verificar"), 90)
    recuperacion["reinicio_portal"] = {"deteccion_s": det, "recuperacion_s": rec_t,
                                       "total_s": round(time.monotonic() - t, 2)}
    fallos.append((f0, marca()))
    ck("reinicio del portal: el motor vuelve a verificar el calendario solo", rec_t < 90, recuperacion["reinicio_portal"])

    # 6) reinicio brusco del motor
    esperar_hasta(en(235))
    f0 = marca()
    t = time.monotonic()
    motor.proc.kill()
    motor.proc.wait(10)
    motor.log.close()
    motor.start()
    arr = motor.wait_ready()
    adm = login(motor.base, "admin", PWD)
    _, rec_t = esperar(lambda: fuente().get("verificado_en") and not fuente().get("sin_verificar"), 90)
    recuperacion["reinicio_motor"] = {"arranque_s": arr, "calendario_verificado_s": rec_t,
                                      "total_s": round(time.monotonic() - t, 2)}
    fallos.append((f0, marca()))
    ck("reinicio del motor: arranca y vuelve a verificar el calendario", rec_t < 90, recuperacion["reinicio_motor"])

    esperar_hasta(en(300))
    portal.ctl("POST", "/control/ventana", activa=False)          # amanece: termina la ventana simulada
    # tiempo para terminar lo que estaba en curso
    def en_curso():
        ex = adm.get("/api/expedientes", params={"limit": 2000}).json()
        return not any(e["estado"] in ACTIVOS_OP for e in ex)
    _, t_drenado = esperar(en_curso, a.drenado_s, 1.0)
    parar.set()
    for h in hilos:
        h.join(timeout=10)
    rec.leer()
    rec.parar.set()

    # ------------------------------------------------------------------ verdad del portal y del motor
    exps = adm.get("/api/expedientes", params={"limit": 2000}).json()
    resv = adm.get("/api/reservaciones", params={"limit": 2000}).json()
    cambios = []
    desde = 0
    while True:
        lote = adm.get("/api/captura/calendario-dias/cambios", params={"desde": desde, "limite": 1000}).json()
        lote = lote.get("cambios", lote) if isinstance(lote, dict) else lote
        if not lote:
            break
        cambios += lote
        desde = lote[-1]["id"]
    p_dias = portal.ctl("GET", "/control/dias")["dias"]
    p_res = portal.ctl("GET", "/control/reservaciones")
    p_res = p_res.get("reservaciones", p_res) if isinstance(p_res, dict) else p_res
    p_res = list(p_res.values()) if isinstance(p_res, dict) else p_res
    p_dia_res = [x for x in p_res if x.get("modo") == "POR_DIA" or (x.get("fecha") and not x.get("hora"))]
    ocup = portal.ctl("GET", "/control/reservaciones").get("ocupacion_dias", {})
    integridad = adm.get("/api/captura/coherencia").json()

    por_estado: dict[str, int] = {}
    for e in exps:
        por_estado[e["estado"]] = por_estado.get(e["estado"], 0) + 1
    conf_motor = {r_["folio"]: r_ for r_ in resv if r_["estado"] == "CONFIRMADA"}
    conf_portal = {x["folio"]: x for x in p_dia_res if x.get("estado") == "CONFIRMADA"}
    sobreventa = [k for k, x in p_dias.items() if x.get("ocupados", 0) > x.get("capacidad", 0)]
    ck("sin sobreventa: ningún día del portal por encima de su capacidad", not sobreventa, sobreventa[:5])
    ck("folios únicos en el motor", len(conf_motor) == sum(1 for r_ in resv if r_["estado"] == "CONFIRMADA"))
    solo_motor = sorted(set(conf_motor) - set(conf_portal))
    ck("cada CONFIRMADA del motor existe CONFIRMADA en el portal (evidencia)", not solo_motor, solo_motor[:5])
    solo_portal = sorted(set(conf_portal) - set(conf_motor))
    ck("cada folio del portal está en el motor (o en verificación)", not solo_portal or all(
        any(r_["estado"] == "EN_VERIFICACION" for r_ in resv) for _ in solo_portal), solo_portal[:5])
    ck("el laboratorio llegó a confirmar reservaciones (no es una prueba vacía)", len(conf_motor) > 0, len(conf_motor))
    exp_conf = [e for e in exps if e["estado"] == "RESERVACION_CONFIRMADA"]
    ck("cada expediente RESERVACION_CONFIRMADA tiene folio confirmado", len(exp_conf) == len(conf_motor),
       f"{len(exp_conf)} expedientes · {len(conf_motor)} folios")
    inc_tocados = [e["codigo"] for e in exps if e["id"] in incompletos and e["estado"] not in ("INCOMPLETO",)]
    ck("ningún expediente incompleto se asignó ni avanzó", not inc_tocados, inc_tocados[:5])
    ck("coherencia del motor (sin estados contradictorios)", not integridad.get("problemas"),
       (integridad.get("problemas") or [])[:3])

    # ------------------------------------------------------------------ métricas (leídas de la BD del motor)
    motor.stop()
    portal.detener()
    from sqlalchemy import create_engine, text
    eng = create_engine(env["MONITOREO_DB_URL"])
    with eng.connect() as cx:
        dos_ops = cx.execute(text(
            "SELECT r.id FROM assignments a JOIN requests r ON r.id=a.request_id "
            "WHERE a.status IN ('ACTIVA','EN_PROCESO') GROUP BY r.id HAVING COUNT(DISTINCT a.operator_id) > 1")).fetchall()
        asg = cx.execute(text(
            "SELECT a.created_at, s.attributes FROM assignments a JOIN slot_states s ON s.id=a.slot_id")).fetchall()
        ev = cx.execute(text("SELECT expediente_id, ts, tipo, detalle FROM expediente_eventos ORDER BY id")).fetchall()
        res_rows = cx.execute(text("SELECT expediente_id, estado, created_at, updated_at FROM reservaciones")).fetchall()
        if a.v17:
            v17["_auditoria_redistribucion"] = cx.execute(text(
                "SELECT COUNT(*) FROM audit_log WHERE action='operador_redistribuido'")).scalar()
            v17["_entregas_canal_lento"] = dict(cx.execute(text(
                "SELECT d.status, COUNT(*) FROM deliveries d JOIN channels c ON c.id=d.channel_id "
                "WHERE c.name='Webhook lento de laboratorio' GROUP BY d.status")).fetchall())
            liberadas = [x["asignacion"] for r_ in v17.get("redistribuciones", {}).values()
                         for x in (r_.get("liberadas") or [])]
            v17["_liberadas_destino"] = {}
            for aid in liberadas:
                fila = cx.execute(text(
                    "SELECT e.estado, e.operator_id, a.operator_id FROM assignments a JOIN expedientes e "
                    "ON e.request_id=a.request_id WHERE a.id=:a"), {"a": aid}).fetchone()
                if fila:
                    v17["_liberadas_destino"][aid] = {"estado": fila[0], "otro_operador": fila[1] not in (None, fila[2]),
                                                      "operador_actual": fila[1], "operador_anterior": fila[2]}
            inciertas = {r_[0] for r_ in cx.execute(text(
                "SELECT DISTINCT expediente_id FROM expediente_eventos WHERE tipo='CONFIRMACION_EN_VERIFICACION'"))}
            ev_conf = cx.execute(text(
                "SELECT detalle FROM expediente_eventos WHERE tipo='RESERVACION_CONFIRMADA'")).fetchall()
            por_evid: dict[str, int] = {}
            for (det_,) in ev_conf:
                det_ = json.loads(det_) if isinstance(det_, str) else (det_ or {})
                por_evid[det_.get("evidencia") or "?"] = por_evid.get(det_.get("evidencia") or "?", 0) + 1
            v17["_inciertas"] = {"n_en_verificacion": len(inciertas), "estado_final_reservacion": {},
                                 "confirmadas_por_evidencia": por_evid}
            for eid_, est_, *_ in res_rows:
                if eid_ in inciertas:
                    d_ = v17["_inciertas"]["estado_final_reservacion"]
                    d_[est_] = d_.get(est_, 0) + 1
            ops_inact = cx.execute(text("SELECT o.name FROM operators o WHERE o.active = :f"), {"f": False}).fetchall()
            v17["_desactivados"] = sorted(x[0] for x in ops_inact)
            atascados = cx.execute(text(
                "SELECT COUNT(*) FROM reservaciones WHERE estado='CONFIRMANDO'")).scalar()
            v17["_confirmando_al_final"] = atascados
    eng.dispose()
    ck("ninguna solicitud con dos operadores a la vez", not dos_ops, [x[0] for x in dos_ops][:5])
    if a.v17:
        pres = v17.get("presencia_antes", {})
        sin_resp, cerro = v17.get("sin_responder", []), v17.get("cerro_sesion")
        ck("v1.7 supervisión: los que dejaron de responder aparecen como CONEXION_PERDIDA (sesión abierta)",
           all(pres.get(u) == "CONEXION_PERDIDA" for u in sin_resp), {u: pres.get(u) for u in sin_resp})
        ck("v1.7 supervisión: el que cerró sesión aparece como DESCONECTADO", pres.get(cerro) == "DESCONECTADO",
           pres.get(cerro))
        activos_ = [u for u in ops if u not in sin_resp and u != cerro]
        ck("v1.7 supervisión: los que siguen trabajando aparecen como CONECTADO",
           sum(pres.get(u) == "CONECTADO" for u in activos_) >= len(activos_) - 1,
           {u: pres.get(u) for u in activos_})
        reds = v17.get("redistribuciones", {})
        ck("v1.7 redistribución por la API: responde y queda en la auditoría",
           len(reds) == 3 and all("error" not in r_ for r_ in reds.values()) and v17["_auditoria_redistribucion"] == 3,
           {u: (len(r_.get("liberadas", [])), len(r_.get("no_liberadas", []))) for u, r_ in reds.items()})
        dest = v17["_liberadas_destino"]
        mal = [k for k, d_ in dest.items() if d_["estado"] in ACTIVOS_OP and not d_["otro_operador"]]
        ck("v1.7 lo redistribuido no se queda con el operador que no responde", not mal, mal[:5])
        ck("v1.7 los operadores redistribuidos quedan desactivados (no reciben trabajo nuevo)",
           len(v17["_desactivados"]) == 3, v17["_desactivados"])
        ck("v1.7 ninguna confirmación se queda a medias (CONFIRMANDO) al final", v17["_confirmando_al_final"] == 0,
           v17["_confirmando_al_final"])
        cortadas = (v17.get("confirmaciones_cortadas") or {}).get("cortar_tras_confirmar", 0)
        inc = v17["_inciertas"]
        ck("v1.7 confirmaciones inciertas (respuesta cortada tras confirmar): cada una se resolvió con evidencia del portal por la clave",
           cortadas > 0 and inc["confirmadas_por_evidencia"].get("api_por_clave", 0)
           + inc["confirmadas_por_evidencia"].get("recuperacion_por_clave", 0) >= cortadas
           and not inc["estado_final_reservacion"].get("EN_VERIFICACION"),
           {"cortadas_por_el_portal": cortadas, **inc})
        ent = v17["_entregas_canal_lento"]
        ck("v1.7 el canal lento recibió notificaciones y falló a propósito",
           hook is not None and hook.recibidas > 0 and hook.fallidas > 0,
           {"recibidas": hook.recibidas if hook else 0, "503": hook.fallidas if hook else 0, "entregas": ent})


    # detección: cada liberación del portal (hora real) contra el primer cambio del motor con esa versión o posterior
    def utc(x):
        if isinstance(x, str):
            x = datetime.fromisoformat(x.replace("Z", "+00:00"))
        return x if x.tzinfo else x.replace(tzinfo=timezone.utc)
    mot_por_clave: dict[str, list] = {}
    for cmb in cambios:
        if cmb.get("clave"):
            mot_por_clave.setdefault(cmb["clave"], []).append(cmb)
    liberaciones = [e for e in rec.eventos.values() if e.get("causa") == "LIBERACION_NOCTURNA" and not e.get("reenviado")]
    lat, lat_fallo, perdidas, transitorias = [], [], 0, 0

    def en_fallo(t_):
        return any(a_ - timedelta(seconds=1) <= t_ <= b_ + timedelta(seconds=2) for a_, b_ in fallos)
    for e in liberaciones:
        tr = utc(e["ts_real"])
        vistos = [c_ for c_ in mot_por_clave.get(e["clave"], [])
                  if (c_.get("version") or 0) >= e["version"] and utc(c_["detectado_en"]) >= tr - timedelta(seconds=1)]
        if vistos:
            (lat_fallo if en_fallo(tr) else lat).append(max(0.0, (utc(vistos[0]["detectado_en"]) - tr).total_seconds()))
        else:
            perdidas += 1
            siguiente = [x for x in rec.eventos.values() if x["clave"] == e["clave"] and x["version"] == e["version"] + 1]
            if siguiente and siguiente[0]["estado"] == "AGOTADO":
                transitorias += 1
    if a.v17:
        ck("v1.7 con el canal lento el motor siguió detectando a tiempo (p95 < 15 s fuera de fallos)",
           bool(lat) and (pct(lat, 95) or 0) < 15, resumen(lat))
    tipos: dict[str, int] = {}
    for cmb in cambios:
        tipos[cmb["tipo"]] = tipos.get(cmb["tipo"], 0) + 1

    # asignación: creación de la asignación contra el último cambio a DISPONIBLE de su sucursal y trámite
    disp_por: dict[tuple, list] = {}
    for cmb in cambios:
        if cmb.get("despues") == "DISPONIBLE":
            disp_por.setdefault((cmb["sucursal"], cmb["tramite"]), []).append(utc(cmb["detectado_en"]))
    lat_asg = []
    for creado, attrs in asg:
        attrs = json.loads(attrs) if isinstance(attrs, str) else (attrs or {})
        t_ = utc(creado)
        previos = [x for x in disp_por.get((attrs.get("sucursal"), attrs.get("tramite")), []) if x <= t_]
        if previos:
            lat_asg.append((t_ - max(previos)).total_seconds())

    # preparación y confirmación por expediente (historial del motor)
    hist: dict[int, list] = {}
    for eid, ts, tipo, det in ev:
        det = json.loads(det) if isinstance(det, str) else (det or {})
        hist.setdefault(eid, []).append((utc(ts), tipo, det))
    lat_prep, lat_conf, lat_total = [], [], []
    for eid, h in hist.items():
        asig = next((t_ for t_, tp, d in h if tp == "ESTADO" and d.get("despues") == "ASIGNADO"), None)
        datos = next((t_ for t_, tp, d in h if tp == "DATOS_ENVIADOS"), None)
        pend = next((t_ for t_, tp, d in h if tp == "ESTADO" and d.get("despues") == "RESERVACION_PENDIENTE"), None)
        conf = next((t_ for t_, tp, d in h if tp == "RESERVACION_CONFIRMADA"), None)
        if asig and datos and datos >= asig:
            lat_prep.append((datos - asig).total_seconds())
        if pend and conf and conf >= pend:
            lat_conf.append((conf - pend).total_seconds())
        if asig and conf:
            lat_total.append((conf - asig).total_seconds())

    # privacidad: ningún nombre ficticio ni teléfono en los registros del motor
    textos = "".join(p.read_text(encoding="utf-8", errors="replace") for p in out.glob("servidor-motor*.log"))
    fugas = [n for n in nombres if n in textos][:3] + [t_ for t_ in ("5550000001", "lab1@ejemplo.test") if t_ in textos]
    ck("sin datos personales en los registros del motor", not fugas, fugas)

    validos = len(ids) - len(incompletos)
    metricas = {
        "mecanismo": "A (eventos)" if mecanismo == "A" else f"B (consulta cada {a.intervalo_b} s)",
        "expedientes": len(ids), "incompletos": len(incompletos), "operadores": len(ops),
        "noche_simulada": {"real_s": a.noche_s, "factor_reloj": round(factor, 1), "ventana": "22:00-07:30 (SIMULADO)",
                           "prob_por_tic": a.prob, "liberaciones": len(liberaciones)},
        "deteccion_s": resumen(lat), "deteccion_durante_fallos_s": resumen(lat_fallo),
        "liberaciones_no_detectadas": perdidas,
        "no_detectadas_transitorias": transitorias,
        "tipos_de_cambio_detectados": tipos,
        "asignacion_s (desde la detección)": resumen(lat_asg),
        "preparacion_s (asignado -> datos enviados, operador SIMULADO)": resumen(lat_prep),
        "confirmacion_s (fecha elegida -> confirmada)": resumen(lat_conf),
        "asignado_a_folio_s": resumen(lat_total),
        "recuperacion": recuperacion,
        "expedientes_por_estado": por_estado,
        "confirmadas": len(conf_motor), "cobertura_de_validos": round(len(conf_motor) / validos, 3) if validos else None,
        "reservaciones_por_estado": {s_: sum(1 for r_ in res_rows if r_[1] == s_) for s_ in {r_[1] for r_ in res_rows}},
        "llamadas_operador_s": {k: resumen(v) for k, v in tiempos.items()},
        "errores_operador": errores_op, "drenado_s": t_drenado,
        "duracion_total_s": round(time.time() - t_inicio, 1),
    }
    if a.v17:
        metricas["v1_7"] = {k.lstrip("_"): v for k, v in v17.items()} | {
            "canal_lento": {"recibidas": hook.recibidas, "respondidas_503": hook.fallidas}}
        hook.detener()
    rep = {"ok": ck.ok, "comprobaciones": ck.items, "metricas": metricas}
    (out / "laboratorio.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return rep


def main() -> int:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mecanismo", choices=["A", "B", "ambos"], default="ambos")
    ap.add_argument("--expedientes", type=int, default=600)
    ap.add_argument("--operadores", type=int, default=10)
    ap.add_argument("--incompletos", type=float, default=8.0, help="porcentaje de expedientes incompletos a propósito")
    ap.add_argument("--noche-s", type=float, default=300, help="segundos REALES que dura la noche simulada")
    ap.add_argument("--prob", type=float, default=0.5, help="probabilidad de liberación por tic (0.5 s) del portal")
    ap.add_argument("--intervalo-b", type=float, default=10, help="segundos entre consultas del mecanismo B")
    ap.add_argument("--pausa", type=float, nargs=2, default=(0.2, 0.8), help="pausa SIMULADA del operador (s)")
    ap.add_argument("--drenado-s", type=float, default=60)
    ap.add_argument("--semilla", type=int, default=16)
    ap.add_argument("--limite-api", type=int, default=6000,
                    help="peticiones/min por IP en el motor del laboratorio (todos comparten 127.0.0.1)")
    ap.add_argument("--salida", default="resultados/laboratorio_dias")
    ap.add_argument("--v17", action="store_true", help="añade los fallos de la v1.7 (ver la descripción)")
    a = ap.parse_args()
    a.expedientes = max(1, min(a.expedientes, 5000))
    mecs = ["A", "B"] if a.mecanismo == "ambos" else [a.mecanismo]
    salida = Path(a.salida)
    reps = {}
    for m in mecs:
        print(f"===== mecanismo {m}", flush=True)
        reps[m] = escenario(a, m, salida / m)
    total = {"ok": all(r["ok"] for r in reps.values()), "plataforma": platform.platform(),
             "python": platform.python_version(), "parametros": vars(a) | {"pausa": list(a.pausa)},
             "escenarios": reps}
    salida.mkdir(parents=True, exist_ok=True)
    (salida / "laboratorio.json").write_text(json.dumps(total, ensure_ascii=False, indent=2, default=str),
                                             encoding="utf-8")
    for m, r in reps.items():
        mt = r["metricas"]
        print(f"[{m}] {mt['mecanismo']}: detección {mt['deteccion_s']} · no detectadas {mt['liberaciones_no_detectadas']}"
              f" · confirmadas {mt['confirmadas']} · asignación {mt['asignacion_s (desde la detección)']}")
    print("RESULTADO:", "TODO OK" if total["ok"] else "HAY FALLAS")
    return 0 if total["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
