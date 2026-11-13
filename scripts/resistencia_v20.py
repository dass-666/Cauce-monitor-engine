"""v2.0 · Resistencia REAL de la ventanilla: caídas de PostgreSQL y reinicio completo.

Medición, no simulación: motor y portal de prueba como procesos reales, PostgreSQL de PRUEBAS que se
DETIENE de verdad (comando configurable). Datos ficticios; solo 127.0.0.1.

Escenarios
  A. PostgreSQL cae EN MITAD de una confirmación: el portal ya la procesó, pero el motor no puede
     guardar el resultado. Tras volver la base: la confirmación se recupera por su clave, sin
     duplicar la cita, y la atención llega a CONFIRMADA. Un reintento del operador no duplica.
  B. PostgreSQL cae con una atención a medias: durante la caída las operaciones fallan sin
     corromper nada; el motor sigue vivo y, al volver la base, la atención continúa hasta el folio.
  C. Reinicio completo: a la vez se matan motor y portal y se detiene PostgreSQL de golpe (como un
     corte de luz), con una atención a medias y otra confirmándose. Al volver todo: la atención a
     medias se retoma hasta su folio; la que se confirmaba se reabre (el portal no llegó a
     procesarla) o se recupera (si la procesó), y nunca queda duplicada; los folios anteriores siguen.

ATENCIÓN: borra la base indicada. Úsalo solo con una base de pruebas.
  python scripts/resistencia_v20.py --bd "postgresql+psycopg://postgres@/monitoreo_res?host=/tmp/pg" \
     --parar "pg_ctl -D /tmp/pg/data stop -m fast" --parar-golpe "pg_ctl -D /tmp/pg/data stop -m immediate" \
     --arrancar "pg_ctl -D /tmp/pg/data start -w -l /tmp/pg/log -o '-k /tmp/pg'"
"""
from __future__ import annotations

import argparse
import json
import platform
import secrets
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from monitoreo.db import Database  # noqa: E402
from portal_reservas.proceso import PortalProceso  # noqa: E402
from verificar_arranque import Checks, Server, base_env  # noqa: E402

PWD = "Resistencia-V20-2026"
OP_PWD = "Operador-Resistencia-2026"
ZONA = "America/Mexico_City"
MANUALES = {"clave_identidad": "KLM305918", "acepto_privacidad": "si"}     # ficticios


def sh(cmd: str) -> tuple[int, str]:
    p = subprocess.run(cmd if sys.platform.startswith("win") else shlex.split(cmd), capture_output=True, text=True,
                       timeout=120, shell=sys.platform.startswith("win"))
    return p.returncode, (p.stdout + p.stderr).strip()[-300:]


def login(base: str, u: str, p: str) -> httpx.Client:
    c = httpx.Client(base_url=base, timeout=60, trust_env=False)
    c.headers["Authorization"] = "Bearer " + c.post("/api/auth/login", json={"username": u, "password": p}).json()["token"]
    return c


def persona(portal, n: int) -> dict:
    reg = portal.registro(n)
    return {"nombre": f"Persona Resistencia {chr(65 + n % 26)}{chr(65 + (n // 26) % 26)}",
            "fecha_nacimiento": reg["fecha_nacimiento"], "id_registro_previo": reg["identificador"],
            "telefono": f"55500040{n:02d}", "telefono_confirmacion": f"55500040{n:02d}",
            "correo": f"r{n}@ejemplo.test", "correo_confirmacion": f"r{n}@ejemplo.test",
            "tipo_tramite": reg["tramite"], "region": "NORTE", "sucursal": "NOR-1"}


def hasta_fecha(c: httpx.Client, p: dict) -> tuple[int, int, int]:
    """Atención de ventanilla: persona -> datos -> día elegido. Devuelve (atención, expediente, reservación)."""
    a = c.post("/api/ventanilla/atenciones", json={"clave": secrets.token_hex(12)})
    a.raise_for_status()
    aid = a.json()["id"]
    r = c.post(f"/api/ventanilla/atenciones/{aid}/expediente", json={"datos": p})
    r.raise_for_status()
    eid = r.json()["expediente"]["id"]
    r = c.post(f"/api/expedientes/{eid}/preparar")
    r.raise_for_status()
    rid = r.json()["reservacion"]["id"]
    r = c.post(f"/api/reservaciones/{rid}/enviar-datos", json={"revisado": True, "manuales": dict(MANUALES)})
    r.raise_for_status()
    cal = r.json()["calendario"]
    for _ in range(3):
        libres = [d for d in cal["dias"] if d["estado"] == "DISPONIBLE"]
        if libres:
            break
        y, m = (int(x) for x in cal["mes"].split("-"))
        r = c.get(f"/api/reservaciones/{rid}/calendario", params={"mes": f"{y + m // 12:04d}-{m % 12 + 1:02d}",
                                                                 "sucursal": cal["sucursal"]})
        r.raise_for_status()
        cal = r.json().get("calendario", r.json())
    r = c.post(f"/api/reservaciones/{rid}/seleccionar-dia", json={"fecha": libres[0]["fecha"], "sucursal": cal["sucursal"]})
    r.raise_for_status()
    return aid, eid, rid


def esperar(cond, timeout: float, paso: float = 0.5):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            v = cond()
            if v:
                return v, round(time.monotonic() - t0, 2)
        except (httpx.HTTPError, ValueError, KeyError):
            pass
        time.sleep(paso)
    return None, round(time.monotonic() - t0, 2)


def salud_ok(base: str) -> bool:
    r = httpx.get(base + "/api/health", timeout=3, trust_env=False)
    return r.status_code == 200 and r.json().get("ok") and r.json().get("base_de_datos") == "OK"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bd", required=True, help="URL de PostgreSQL DE PRUEBAS (se borra)")
    ap.add_argument("--parar", required=True, help="comando que detiene PostgreSQL (ordenado)")
    ap.add_argument("--parar-golpe", help="comando que lo detiene de golpe (sin vaciar): corte de luz")
    ap.add_argument("--arrancar", required=True)
    ap.add_argument("--caida-s", type=float, default=8)
    ap.add_argument("--salida", default="resultados/resistencia_v20")
    a = ap.parse_args()
    out = Path(a.salida)
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="res20-"))
    ck = Checks()
    m: dict = {}
    t_ini = time.time()

    Database(a.bd).reset_for_tests()
    (tmp / "portal").mkdir()
    portal = PortalProceso(tmp / "portal" / "estado.json", token=secrets.token_urlsafe(16),
                           token_control=secrets.token_urlsafe(16), log=out / "portal.log", extra=["--zona-horaria", ZONA])
    portal.iniciar()
    _VIVOS.append(portal.detener)
    env = base_env(tmp, "desarrollo")
    env.update({"MONITOREO_DATOS_DEMO": "0", "MONITOREO_ADMIN_PASSWORD": PWD, "MONITOREO_PORTAL_URL": portal.base,
                "MONITOREO_PORTAL_TOKEN": portal.token, "MONITOREO_ZONA_HORARIA": ZONA, "MONITOREO_DB_URL": a.bd})
    motor = Server("motor", env, out)
    _VIVOS.append(lambda: motor.stop())
    motor.start()
    motor.wait_ready()
    adm = login(motor.base, "admin", PWD)
    adm.put("/api/settings/engine", json={"captura_modo": "POR_DIA", "captura_gracia_confirmando_s": 5}).raise_for_status()
    adm.post("/api/users", json={"username": "operador1", "password": OP_PWD, "role": "operador", "regions": ["NORTE"],
                                 "sucursales": ["NOR-1", "NOR-2"], "operator_name": "Operador Resistencia"}).raise_for_status()
    op = login(motor.base, "operador1", OP_PWD)
    gente = [persona(portal, 70 + i) for i in range(6)]
    confirmadas0 = portal.ctl("GET", "/control/estado")["confirmadas"]

    def en_portal(folio):
        r = httpx.get(portal.base + f"/api/reservaciones/{folio}", headers={"Authorization": f"Bearer {portal.token}"},
                      trust_env=False, timeout=10)
        return r.json().get("estado") if r.status_code == 200 else None

    def res_estado(c, rid):
        return next((r for r in c.get("/api/reservaciones").json() if r["id"] == rid), None)

    # ------------------------------------------------------------------ A
    aidA, eidA, ridA = hasta_fecha(op, gente[0])
    portal.fallos(latencia_ms=4000)                     # el portal tarda: la base cae mientras tanto
    resp = {}
    th = threading.Thread(target=lambda: resp.setdefault("r", op.post(f"/api/reservaciones/{ridA}/confirmar",
                                                                       json={"confirmo": True})))
    th.start()
    time.sleep(1.5)
    rc, txt = sh(a.parar)
    ck("A · PostgreSQL detenido a mitad de la confirmación", rc == 0, txt[-120:])
    th.join(60)
    portal.fallos(reset=True)
    rA = resp.get("r")
    ck("A · el operador recibe un error (no un éxito falso) mientras la base está caída",
       rA is not None and rA.status_code >= 500, rA.status_code if rA is not None else None)
    h = httpx.get(motor.base + "/api/health", timeout=5, trust_env=False)
    ck("A · /api/health avisa de la base caída", h.status_code != 200 or not h.json().get("ok"), h.status_code)
    ck("A · el portal sí procesó la confirmación (una)", portal.ctl("GET", "/control/estado")["confirmadas"] == confirmadas0 + 1)
    time.sleep(max(0.0, a.caida_s - 3))
    rc, txt = sh(a.arrancar)
    ck("A · PostgreSQL vuelve", rc == 0, txt[-120:])
    _, t_rec = esperar(lambda: salud_ok(motor.base), 90)
    m["A_recuperacion_salud_s"] = t_rec
    ck("A · el motor sigue vivo y vuelve a OK sin reiniciarlo", motor.proc.poll() is None and t_rec < 90, f"{t_rec} s")
    r2 = op.post(f"/api/reservaciones/{ridA}/confirmar", json={"confirmo": True})
    ck("A · el reintento del operador NO vuelve a confirmar en el portal", r2.status_code in (200, 409)
       and portal.ctl("GET", "/control/estado")["confirmadas"] == confirmadas0 + 1, r2.status_code)
    est, t_res = esperar(lambda: (lambda r: r if r["estado"] == "CONFIRMADA" else None)(res_estado(op, ridA)), 90)
    m["A_confirmacion_recuperada_s"] = t_res
    ck("A · la confirmación se recupera por su clave (CONFIRMADA con evidencia del portal)",
       est is not None and en_portal(est["folio"]) == "CONFIRMADA", (est or {}).get("folio"))
    v = op.post(f"/api/ventanilla/atenciones/{aidA}/cerrar", json={}).json()
    ck("A · la atención termina CONFIRMADA con ese folio", v.get("estado") == "CONFIRMADA" and v.get("folio") == (est or {}).get("folio"),
       (v.get("estado"), v.get("folio")))
    folios = [v.get("folio")]

    # ------------------------------------------------------------------ B
    aidB, eidB, ridB = hasta_fecha(op, gente[1])
    rc, txt = sh(a.parar)
    ck("B · PostgreSQL detenido con una atención a medias", rc == 0, txt[-120:])
    fallos_b = [op.get(f"/api/ventanilla/atenciones/{aidB}").status_code,
                op.post(f"/api/reservaciones/{ridB}/confirmar", json={"confirmo": True}).status_code]
    ck("B · durante la caída las operaciones fallan (sin éxito falso)", all(x >= 500 for x in fallos_b), fallos_b)
    ck("B · nada llegó al portal durante la caída", portal.ctl("GET", "/control/estado")["confirmadas"] == confirmadas0 + 1)
    time.sleep(a.caida_s)
    sh(a.arrancar)
    _, t_rec = esperar(lambda: salud_ok(motor.base), 90)
    m["B_recuperacion_salud_s"] = t_rec
    ck("B · el motor sigue vivo y vuelve a OK", motor.proc.poll() is None and t_rec < 90, f"{t_rec} s")
    vb = op.get(f"/api/ventanilla/atenciones/{aidB}").json()
    ck("B · la atención sigue en el paso exacto (confirmar)", vb["siguiente"]["clave"] == "confirmar", vb["siguiente"]["clave"])
    r = op.post(f"/api/reservaciones/{ridB}/confirmar", json={"confirmo": True})
    vb = op.post(f"/api/ventanilla/atenciones/{aidB}/cerrar", json={}).json()
    ck("B · llega a su folio", r.status_code == 200 and vb.get("estado") == "CONFIRMADA" and en_portal(vb.get("folio")) == "CONFIRMADA",
       (r.status_code, vb.get("folio")))
    folios.append(vb.get("folio"))

    # ------------------------------------------------------------------ C
    aidC1, eidC1, ridC1 = hasta_fecha(op, gente[2])     # a medias (día elegido)
    aidC2, eidC2, ridC2 = hasta_fecha(op, gente[3])     # se estará confirmando
    antes_c = portal.ctl("GET", "/control/estado")["confirmadas"]
    portal.fallos(latencia_ms=5000)
    th = threading.Thread(target=lambda: _ignorar(lambda: op.post(f"/api/reservaciones/{ridC2}/confirmar",
                                                                  json={"confirmo": True}, timeout=30)))
    th.start()
    time.sleep(1.5)
    motor.proc.kill()
    portal.detener(forzar=True)
    rc, txt = sh(a.parar_golpe or a.parar)
    ck("C · corte simultáneo: motor y portal matados, PostgreSQL detenido de golpe",
       motor.proc.poll() is not None and portal.proc.poll() is not None and rc == 0, txt[-120:])
    motor.proc.wait(10)
    motor.log.close()
    th.join(40)
    t0 = time.monotonic()
    sh(a.arrancar)
    portal.iniciar()
    motor = Server("motor-reinicio", env, out)
    motor.start()
    m["C_arranque_total_s"] = round(motor.wait_ready() + (time.monotonic() - t0), 2)
    ck("C · todo vuelve a arrancar sobre los mismos datos", salud_ok(motor.base), f"{m['C_arranque_total_s']} s")
    op = login(motor.base, "operador1", OP_PWD)
    ck("C · los folios anteriores siguen CONFIRMADOS en el portal tras el corte",
       all(en_portal(f) == "CONFIRMADA" for f in folios), folios)
    abiertas = {x["id"] for x in op.get("/api/ventanilla").json()["abiertas"]}
    ck("C · las dos atenciones siguen abiertas para retomarlas", {aidC1, aidC2} <= abiertas, sorted(abiertas))
    # C2: ¿la procesó el portal? Con el retraso inyectado, el portal murió antes de procesarla.
    procesada = portal.ctl("GET", "/control/estado")["confirmadas"] > antes_c
    m["C_confirmacion_en_curso_procesada_por_el_portal"] = procesada
    est, t_c2 = esperar(lambda: (lambda r: r if r["estado"] in ("CONFIRMADA", "FECHA_ELEGIDA") else None)(res_estado(op, ridC2)), 120)
    m["C_confirmacion_en_curso_resuelta_s"] = t_c2
    ck("C · la confirmación que estaba en curso se resuelve sola (reabierta si el portal no la procesó, "
       "recuperada si sí)", est is not None and (est["estado"] == ("CONFIRMADA" if procesada else "FECHA_ELEGIDA")),
       (est or {}).get("estado"))
    for aid, rid in ((aidC1, ridC1), (aidC2, ridC2)):
        if res_estado(op, rid)["estado"] != "CONFIRMADA":
            r = op.post(f"/api/reservaciones/{rid}/confirmar", json={"confirmo": True})
            if r.status_code != 200:
                ck(f"C · confirmar la atención {aid} tras el reinicio", False, r.text[:200])
        v = op.post(f"/api/ventanilla/atenciones/{aid}/cerrar", json={}).json()
        ck(f"C · la atención {aid} se retoma hasta su folio", v.get("estado") == "CONFIRMADA"
           and en_portal(v.get("folio")) == "CONFIRMADA", (v.get("estado"), v.get("folio")))
        folios.append(v.get("folio"))

    # ------------------------------------------------------------------ integridad final
    total = portal.ctl("GET", "/control/estado")["confirmadas"] - confirmadas0
    ck("FINAL · exactamente una cita por persona: 4 folios distintos, 4 confirmadas en el portal",
       len(set(folios)) == len(folios) == 4 and total == 4, {"folios": folios, "confirmadas_en_portal": total})
    coh = adm_login(motor.base).get("/api/captura/coherencia").json()
    ck("FINAL · coherencia del motor (sin estados contradictorios)", not coh.get("problemas"), (coh.get("problemas") or [])[:3])
    dia = op.get("/api/ventanilla").json()
    ck("FINAL · resumen del día: 4 con cita, ninguna abierta", dia["mias"]["con_cita"] == 4 and not dia["abiertas"],
       (dia["mias"]["con_cita"], len(dia["abiertas"])))
    textos = "".join(p.read_text(encoding="utf-8", errors="replace") for p in out.glob("servidor-motor*.log"))
    fugas = [p["nombre"] for p in gente if p["nombre"] in textos] + [p["telefono"] for p in gente if p["telefono"] in textos]
    ck("FINAL · sin datos personales en los registros del motor", not fugas, fugas[:3])
    motor.stop()
    portal.detener()
    res = {"version": "2.0.0-dev", "momento": time.strftime("%Y-%m-%d %H:%M:%S"), "plataforma": platform.platform(),
           "python": platform.python_version(), "duracion_s": round(time.time() - t_ini, 1), "base_de_datos": "postgresql",
           "etiqueta": "MEDIDO: procesos reales y PostgreSQL detenido de verdad; datos ficticios",
           "metricas": m, "ok": ck.ok, "comprobaciones": ck.items}
    (out / "resistencia_v20.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print("TODO OK" if ck.ok else "HAY FALLOS", f"({sum(i['ok'] for i in ck.items)}/{len(ck.items)})")
    return 0 if ck.ok else 1


# Procesos lanzados por este script: se detienen SIEMPRE al salir, también si algo falla a mitad.
# (Hallado en la primera corrida: un motor huérfano de una corrida caída siguió conectado a la misma
# base, apuntando a OTRO portal, y "recuperó" reabriendo una confirmación que su portal no conocía.)
_VIVOS: list = []


def _ignorar(fn):
    try:
        fn()
    except httpx.HTTPError:
        pass


def adm_login(base: str) -> httpx.Client:
    return login(base, "admin", PWD)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt):
        # Si algo explota a mitad, no dejar PostgreSQL detenido (el comando de arranque es idempotente).
        cmd = next((sys.argv[i + 1] for i, x in enumerate(sys.argv) if x == "--arrancar"), None)
        if cmd:
            sh(cmd)
        raise
    finally:
        for parar in reversed(_VIVOS):
            try:
                parar()
            except Exception:  # noqa: BLE001
                pass
