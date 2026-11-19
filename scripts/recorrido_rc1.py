"""RC1 · Demostración reproducible del flujo ADMINISTRATIVO completo con la interfaz.

Importar -> validar -> preparar expedientes -> planificar -> observar disponibilidad autorizada (laboratorio) ->
revisar la operación -> registrar resultado y folio -> reimportar sin duplicar.

1. Arranca Cauce en modo laboratorio RC1 (`ventanilla_laboratorio.Laboratorio(rc1=True)`) en una carpeta temporal:
   portal propio en 127.0.0.1, consentimiento exigido. No toca `data/`.
2. Genera la lista de demostración (6 clientes FICTICIOS, `demo_rc1_datos.py`) con fechas vigentes.
3. Lanza `recorrido_rc1.js` (Chromium): una persona administrativa, solo clics y teclas.
4. Comprueba POR SU CUENTA (fuera del navegador) que lo que la pantalla dijo es verdad: el folio está CONFIRMADO en
   el portal y es del día abierto, solo hay 6 expedientes, ningún expediente importado lleva la contraseña del
   archivo, los bloqueados no entraron al plan, y los registros del motor no contienen los datos personales.

La «confirmación humana» de la cita la hace el script: es una SIMULACIÓN y la evidencia lo dice. La idempotencia
del portal de LABORATORIO no demuestra la de un proveedor externo.

Uso: python scripts/recorrido_rc1.py [--salida resultados/recorrido_rc1]
Código 0 si todo pasa, 1 si algo falla, 3 si falta Node.js/Playwright.
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from demo_rc1_datos import filas as filas_demo  # noqa: E402
from demo_rc1_datos import generar  # noqa: E402
from ventanilla_laboratorio import ADMIN_PWD, Laboratorio  # noqa: E402

from monitoreo import navegador  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida", default=str(ROOT / "resultados" / "recorrido_rc1"))
    a = ap.parse_args()
    out = Path(a.salida).resolve()  # el navegador corre con cwd=scripts: la ruta debe ser absoluta
    capt = out / "capturas"
    if capt.exists():
        shutil.rmtree(capt)
    capt.mkdir(parents=True, exist_ok=True)
    nodo = shutil.which("node")
    if not nodo:
        print("Falta Node.js (https://nodejs.org) para el navegador automatizado.")
        return 3
    tmp = Path(tempfile.mkdtemp(prefix="recorrido-rc1-"))
    lab = Laboratorio(tmp / "lab", nuevo=True, rc1=True)
    ck: list[dict] = []
    res: dict = {"version": "RC1", "plataforma": platform.platform(), "python": platform.python_version(),
                 "inicio": time.strftime("%Y-%m-%dT%H:%M:%S"), "confirmacion_humana": "SIMULADA por el script",
                 "datos": "ficticios; portal de laboratorio propio en 127.0.0.1"}

    def check(nombre, ok, detalle=None):
        ck.append({"comprobacion": nombre, "ok": bool(ok), "detalle": detalle})
        print(("OK   " if ok else "FALLA") + " " + nombre + ("" if ok else f"  {detalle}"))
        return ok
    try:
        d = lab.iniciar()
        demo = generar(out / "lista_demostracion")
        res["lista"] = demo
        entrada = {"admin": ["admin", ADMIN_PWD], "archivo": demo["xlsx"], "mes": demo["mes"], "dia": demo["dia"],
                   "con_consentimiento": [0, 1, 5], "esperado_coincidencia": "EXP-000001"}
        (tmp / "entrada.json").write_text(json.dumps(entrada), "utf-8")
        p = subprocess.run([nodo, str(ROOT / "scripts" / "recorrido_rc1.js"), d["motor"], str(capt), str(tmp / "entrada.json")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
                           env=navegador.entorno(), cwd=str(ROOT / "scripts"))
        linea = next((x for x in p.stdout.splitlines() if x.startswith("RESULTADO ")), None)
        if linea is None:
            (out / "recorrido_rc1_node.log").write_text(p.stdout + "\n" + p.stderr, "utf-8")
            check("el navegador automatizado se ejecutó", False, (p.stderr or p.stdout)[-400:])
            return 3 if "Cannot find module" in p.stderr else 1
        nav = json.loads(linea[len("RESULTADO "):])
        res["navegador"] = nav
        for x in nav["pasos"]:
            check(f"interfaz · {x['paso']}", x["ok"], x["detalle"])
        check("interfaz · recorrido completo sin interrupciones", not nav.get("fallo"), nav.get("fallo"))
        check("interfaz · sin errores de JavaScript", not nav["errores_js"], nav["errores_js"])
        check("interfaz · sin respuestas HTTP de error", not nav["http_errores"], nav["http_errores"])
        adm = httpx.Client(base_url=d["motor"], timeout=30, trust_env=False)
        adm.headers["Authorization"] = "Bearer " + adm.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PWD}).json()["token"]
        folio = nav.get("folio")
        # --- comprobaciones independientes
        if folio:
            rp = httpx.get(f"{d['portal']}/api/reservaciones/{folio}", headers={"Authorization": f"Bearer {lab.cfg['token']}"},
                           trust_env=False, timeout=10)
            check("portal · el folio existe y está CONFIRMADO", rp.status_code == 200 and rp.json().get("estado") == "CONFIRMADA",
                  rp.text[:200])
            check("portal · la cita es del día abierto en el calendario del laboratorio",
                  rp.status_code == 200 and rp.json().get("fecha") == demo["dia"], rp.json().get("fecha") if rp.status_code == 200 else None)
        exps = adm.get("/api/admin/expedientes").json()["expedientes"]
        sit = {e["codigo"]: e["situacion"] for e in exps}
        res["situacion_final"] = sit
        check("base · exactamente 6 expedientes tras dos cargas del mismo archivo", len(exps) == 6, len(exps))
        check("base · la cita confirmada es solo la del expediente que coincidió",
              [c for c, s in sit.items() if s == "CONFIRMADA"] == ["EXP-000001"], sit)
        check("base · los bloqueados (sin consentimiento o incompletos) no están en el plan",
              all(sit.get(c) in ("SIN_CONSENTIMIENTO", "INCOMPLETO") for c in ("EXP-000003", "EXP-000004", "EXP-000005")), sit)
        check("base · los otros dos elegibles siguen en el plan esperando (sin cita)",
              sit.get("EXP-000002") == "EN_PLAN" and sit.get("EXP-000006") == "EN_PLAN", sit)
        lotes = adm.get("/api/admin/importaciones").json()["lotes"]
        check("lotes · dos cargas registradas: 6 nuevos y luego 0 nuevos / 6 ya existentes",
              sorted((x["creadas"], x["existentes"]) for x in lotes) == [(0, 6), (6, 0)], [(x["codigo"], x["creadas"], x["existentes"]) for x in lotes])
        t = adm.get("/api/expedientes/1/trazabilidad").json()
        tipos = {x["tipo"] for x in t.get("linea_de_tiempo", [])}
        esperados = {"IMPORTADO", "CONSENTIMIENTO_REGISTRADO", "PLAN_AGREGADO", "RESERVACION_CONFIRMADA"}
        check("historial · importación, consentimiento, plan y folio en una sola línea de tiempo",
              esperados <= tipos and any(r.get("folio") == folio for r in t.get("reservaciones", [])),
              {"faltan": sorted(esperados - tipos)})
        check("historial · cadena de sellos del consentimiento íntegra", t.get("verificacion_consentimientos", {}).get("ok"),
              t.get("verificacion_consentimientos"))
        # --- minimización: ni la contraseña del archivo ni los datos personales en registros
        datos = filas_demo(__import__("datetime").date.fromisoformat(demo["mes"] + "-01"))
        logs = "".join(f.read_text("utf-8", errors="replace") for f in lab.carpeta.glob("*.log"))
        sensibles = ["no-usar-1234"] + [x[0] for x in datos] + [x[3] for x in datos if x[3]] + [x[4] for x in datos if x[4]]
        fugas = [s for s in sensibles if s in logs]
        check("registros del motor y del portal sin datos personales ni la contraseña del archivo", not fugas, fugas)
        import sqlite3
        con = sqlite3.connect(lab.carpeta / "cauce_lab.db")
        try:
            volcado = "\n".join(con.iterdump())
        finally:
            con.close()
        check("base de datos · la contraseña del archivo no se guardó en ninguna tabla", "no-usar-1234" not in volcado)
        res["folio"] = folio
    except Exception as e:  # noqa: BLE001
        check("la demostración se detuvo por un error", False, f"{type(e).__name__}: {str(e)[:300]}")
    finally:
        lab.detener()
        res["comprobaciones"] = ck
        res["aprobadas"] = sum(x["ok"] for x in ck)
        res["total"] = len(ck)
        res["capturas"] = sorted(x.name for x in capt.glob("*.png"))
        out.mkdir(parents=True, exist_ok=True)
        (out / "recorrido_rc1.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), "utf-8")
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nRESULTADO: {res['aprobadas']}/{res['total']} comprobaciones · evidencia en {out / 'recorrido_rc1.json'}")
    return 0 if ck and all(x["ok"] for x in ck) else 1


if __name__ == "__main__":
    sys.exit(main())
