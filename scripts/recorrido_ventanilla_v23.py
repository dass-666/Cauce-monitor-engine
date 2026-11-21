"""v2.3 · Demostración reproducible: un operador recorre Ventanilla de principio a fin CON LA INTERFAZ.

1. Arranca el laboratorio (`ventanilla_laboratorio.Laboratorio`) en una carpeta temporal: portal propio en
   127.0.0.1, motor con consentimiento exigido, administrador y operador. No toca `data/`.
2. Lanza `recorrido_ventanilla_v23.js` (Chromium): dos navegadores, administrador y operador, solo clics y
   teclas. Los 10 pasos pedidos: abrir, plan del día, expediente, consentimiento, validación y duplicados,
   plan, estado, calendario del laboratorio + operación, folio, cierre e historial.
3. Comprueba POR SU CUENTA (fuera del navegador) que lo que la pantalla dijo es verdad: el folio existe y está
   CONFIRMADO en el portal de laboratorio, la trazabilidad del expediente lo registra con su cadena de
   consentimiento íntegra, y los registros del motor no contienen los datos personales ficticios tecleados.

La «confirmación humana» de la cita la hace el script: es una SIMULACIÓN y la evidencia lo dice.

Uso: python scripts/recorrido_ventanilla_v23.py [--salida resultados/recorrido_v23]
Salida: <salida>/recorrido_v23.json y <salida>/capturas/*.png. Código 0 si todo pasa, 1 si algo falla,
3 si falta Node.js/Playwright.
"""
from __future__ import annotations

import argparse
import calendar
import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from ventanilla_laboratorio import ADMIN_PWD, OPERADOR, Laboratorio  # noqa: E402

from monitoreo import navegador  # noqa: E402

PERSONA = {"nombre": "Lucía Ficticia Laboratorio", "parecido": "Lucia Fictisia Laboratorio",
           "telefono": "5550001234", "correo": "lucia.ficticia@ejemplo.test"}


def mes_vacio(c: httpx.Client) -> str:
    """El primer mes (desde dos meses adelante) sin ningún día abierto para NOR-1/alta: así el único
    día disponible es el que abre el administrador en la demostración."""
    hoy = date.fromisoformat(c.get("/api/laboratorio/portal/calendario", params={"sucursal": "NOR-1", "tramite": "alta"}).json()["hoy"])
    for k in range(2, 4):
        y, m = hoy.year + (hoy.month - 1 + k) // 12, (hoy.month - 1 + k) % 12 + 1
        mes = f"{y:04d}-{m:02d}"
        d = c.get("/api/laboratorio/portal/calendario", params={"sucursal": "NOR-1", "tramite": "alta", "mes": mes}).json()
        if all(x["estado"] == "NO_HABILITADO" for x in d["dias"]):
            return mes
    raise RuntimeError("no hay un mes vacío en el horizonte del plan")


def estados_sockets() -> dict:
    """Diagnóstico: cuántos sockets TCP hay en cada estado (agotamiento de puertos efímeros / TIME_WAIT)."""
    try:
        t = subprocess.run(["netstat", "-an"], capture_output=True, text=True, timeout=30).stdout
    except Exception as e:  # noqa: BLE001
        return {"error": type(e).__name__}
    n: dict = {}
    for ln in t.splitlines():
        if ln.split()[:1] in (["TCP"], ["tcp"], ["tcp6"]):
            est = ln.split()[-1]
            n[est] = n.get(est, 0) + 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida", default=str(ROOT / "resultados" / "recorrido_v23"))
    a = ap.parse_args()
    out = Path(a.salida)
    capt = out / "capturas"
    if capt.exists():
        shutil.rmtree(capt)
    capt.mkdir(parents=True, exist_ok=True)
    nodo = shutil.which("node")
    if not nodo:
        print("Falta Node.js (https://nodejs.org) para el navegador automatizado.")
        return 3
    tmp = Path(tempfile.mkdtemp(prefix="recorrido23-"))
    lab = Laboratorio(tmp / "lab", nuevo=True)
    ck: list[dict] = []
    res: dict = {"version": "2.3", "plataforma": platform.platform(), "python": platform.python_version(),
                 "inicio": time.strftime("%Y-%m-%dT%H:%M:%S"), "confirmacion_humana": "SIMULADA por el script",
                 "datos": "ficticios; portal de laboratorio propio en 127.0.0.1"}

    def check(nombre, ok, detalle=None):
        ck.append({"comprobacion": nombre, "ok": bool(ok), "detalle": detalle})
        print(("OK   " if ok else "FALLA") + " " + nombre + ("" if ok else f"  {detalle}"))
        return ok
    try:
        d = lab.iniciar()
        adm = httpx.Client(base_url=d["motor"], timeout=30, trust_env=False)
        adm.headers["Authorization"] = "Bearer " + adm.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PWD}).json()["token"]
        mes = mes_vacio(adm)
        y, m = (int(x) for x in mes.split("-"))
        entrada = {"admin": ["admin", ADMIN_PWD], "operador": list(OPERADOR), "persona": PERSONA, "mes": mes,
                   "fin_mes": f"{mes}-{calendar.monthrange(y, m)[1]:02d}", "dia": f"{mes}-15"}
        (tmp / "entrada.json").write_text(json.dumps(entrada), "utf-8")
        p = subprocess.run([nodo, str(ROOT / "scripts" / "recorrido_ventanilla_v23.js"), d["motor"], str(capt), str(tmp / "entrada.json")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
                           env=navegador.entorno(), cwd=str(ROOT / "scripts"))
        linea = next((x for x in p.stdout.splitlines() if x.startswith("RESULTADO ")), None)
        if linea is None:
            (out / "recorrido_v23_node.log").write_text(p.stdout + "\n" + p.stderr, "utf-8")
            check("el navegador automatizado se ejecutó", False, (p.stderr or p.stdout)[-400:])
            return 3 if "Cannot find module" in p.stderr else 1
        nav = json.loads(linea[len("RESULTADO "):])
        res["navegador"] = nav
        if nav.get("diagnostico") is not None:
            print("DIAGNOSTICO_NAVEGADOR " + json.dumps(nav["diagnostico"], ensure_ascii=False))
            print("DIAGNOSTICO_SOCKETS " + json.dumps(estados_sockets()))
        for x in nav["pasos"]:
            check(f"interfaz · {x['paso']}", x["ok"], x["detalle"])
        check("interfaz · recorrido completo sin interrupciones", not nav.get("fallo"), nav.get("fallo"))
        check("interfaz · sin errores de JavaScript", not nav["errores_js"], nav["errores_js"])
        # el único error HTTP esperado es el 409 del duplicado exacto (el motor lo detiene a propósito)
        otros = [x for x in nav["http_errores"] if not re.fullmatch(r"operador 409 POST /api/ventanilla/atenciones/\d+/expediente", x)]
        check("interfaz · sin respuestas HTTP de error inesperadas (solo el 409 del duplicado)", not otros, otros)
        folio = nav.get("folio")
        # --- comprobación independiente: el portal tiene la cita
        if folio:
            rp = httpx.get(f"{d['portal']}/api/reservaciones/{folio}", headers={"Authorization": f"Bearer {lab.cfg['token']}"},
                           trust_env=False, timeout=10)
            check("portal · el folio existe y está CONFIRMADO", rp.status_code == 200 and rp.json().get("estado") == "CONFIRMADA",
                  rp.text[:200])
            check("portal · la cita es del día abierto por el administrador", rp.status_code == 200 and rp.json().get("fecha") == entrada["dia"],
                  rp.json().get("fecha") if rp.status_code == 200 else None)
            # --- trazabilidad del expediente (lo que ve el administrador)
            exps = adm.get("/api/expedientes", params={"estado": "RESERVACION_CONFIRMADA"}).json()
            e = exps[0] if exps else None
            t = adm.get(f"/api/expedientes/{e['id']}/trazabilidad").json() if e else {}
            tipos = {x["tipo"] for x in t.get("linea_de_tiempo", [])}
            check("historial · folio, consentimiento, plan y atenciones en una sola línea de tiempo",
                  bool(e) and any(r.get("folio") == folio for r in t.get("reservaciones", []))
                  and {"CONSENTIMIENTO_REGISTRADO", "PLAN_AGREGADO", "PLAN_COINCIDENCIA", "RESERVACION_CONFIRMADA"} <= tipos
                  and [x["resultado"] for x in t.get("atenciones", [])] == ["EN_PLAN_DEL_DIA", "CITA_CONFIRMADA"],
                  {"tipos_faltantes": sorted({"CONSENTIMIENTO_REGISTRADO", "PLAN_AGREGADO", "PLAN_COINCIDENCIA",
                                              "RESERVACION_CONFIRMADA"} - tipos),
                   "atenciones": [x["resultado"] for x in t.get("atenciones", [])]})
            # --- v2.3.1: el plan se sincroniza solo (mantenimiento del planificador) y el historial lo refleja
            def plan_confirmado():
                tt = adm.get(f"/api/expedientes/{e['id']}/trazabilidad").json()
                return tt if any(x["estado"] == "CONFIRMADO" for x in tt.get("planes", [])) else None
            t_plan = None
            if e:
                limite = time.time() + 30
                while t_plan is None and time.time() < limite:
                    t_plan = plan_confirmado()
                    if t_plan is None:
                        time.sleep(0.5)
            check("plan · el elemento del plan pasa a CONFIRMADO tras el folio y el historial lo refleja",
                  bool(t_plan) and [x["estado"] for x in t_plan["planes"]] == ["CONFIRMADO"]
                  and any(r.get("folio") == folio and r.get("estado") == "CONFIRMADA" for r in t_plan["reservaciones"])
                  and t_plan["atenciones"][-1]["folio"] == folio,
                  {"planes": [x["estado"] for x in (t_plan or t).get("planes", [])]})
            check("historial · cadena de sellos del consentimiento íntegra", t.get("verificacion_consentimientos", {}).get("ok"),
                  t.get("verificacion_consentimientos"))
            dup = [x for x in adm.get("/api/expedientes", params={"estado": "CERRADO"}).json()
                   if (x.get("motivo_cierre") or "").upper() == "CANCELADO"]
            check("duplicados · el expediente parecido quedó cerrado (cancelado) por el administrador", len(dup) == 1,
                  [x.get("motivo_cierre") for x in dup])
            res["expediente"] = e and e["codigo"]
        # --- sin datos personales en los registros del motor
        logs = "".join(f.read_text("utf-8", errors="replace") for f in lab.carpeta.glob("*.log"))
        fugas = [k for k in ("telefono", "correo", "nombre") if PERSONA[k] in logs]
        check("registros del motor y del portal sin los datos personales tecleados", not fugas, fugas)
        res["folio"], res["fecha"] = folio, nav.get("fecha")
    except Exception as e:  # noqa: BLE001
        check("la demostración se detuvo por un error", False, f"{type(e).__name__}: {str(e)[:300]}")
    finally:
        lab.detener()
        res["comprobaciones"] = ck
        res["aprobadas"] = sum(x["ok"] for x in ck)
        res["total"] = len(ck)
        res["capturas"] = sorted(x.name for x in capt.glob("*.png"))
        out.mkdir(parents=True, exist_ok=True)
        (out / "recorrido_v23.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), "utf-8")
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nRESULTADO: {res['aprobadas']}/{res['total']} comprobaciones · evidencia en {out / 'recorrido_v23.json'}")
    return 0 if ck and all(x["ok"] for x in ck) else 1


if __name__ == "__main__":
    sys.exit(main())
