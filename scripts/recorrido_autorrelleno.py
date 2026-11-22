"""Laboratorio · recorrido reproducible de «Autorrellenar formulario» (Ventanilla -> adaptador -> Forms REAL).

1. Arranca el laboratorio de Ventanilla con Forms (`ventanilla_laboratorio.Laboratorio(..., forms=...)`) en una
   carpeta temporal nueva: portal propio en 127.0.0.1, motor con consentimiento exigido, perfil aprobado en
   Forms. No toca `data/`, ni otras instalaciones, ni bases o respaldos existentes.
2. Lanza `recorrido_autorrelleno.js` (Chromium, solo clics): el operador crea un expediente ficticio con su
   consentimiento, pulsa «Autorrellenar formulario», espera el resultado, revoca el consentimiento y comprueba
   que una persona sin consentimiento no tiene botón.
3. Comprueba POR SU CUENTA (API y base) que: Forms rellenó 8 campos y 2 confirmaciones, la autorización usada
   fue el consentimiento real del expediente, tras revocar el servidor NIEGA el autorrellenado, sin
   consentimiento también, y el historial/auditoría no contienen los datos personales ficticios tecleados.

Uso: python scripts/recorrido_autorrelleno.py --forms <carpeta de Forms instalado> [--salida DIR] [--espera-s 30]
Para VER la ventana de Forms durante el recorrido: añade --con-ventana (por omisión Forms corre sin ventana).
Código 0 si todo pasa, 1 si algo falla, 3 si falta Node.js/Playwright o Forms.
"""
from __future__ import annotations

import argparse
import json
import os
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
from autorrelleno_forms import FormsNoListo, buscar_datos  # noqa: E402
from ventanilla_laboratorio import ADMIN_PWD, OPERADOR, Laboratorio  # noqa: E402

from monitoreo import navegador  # noqa: E402

PERSONA = {"nombre": "Rosa Ficticia Autorrelleno", "telefono": "5550004321", "correo": "rosa.ficticia@ejemplo.test",
           "tramite": "alta"}
PERSONA_SIN = {"nombre": "Tomas Ficticio Sinconsentimiento", "telefono": "5550009876",
               "correo": "tomas.ficticio@ejemplo.test", "tramite": "renovacion"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--forms", required=True)
    ap.add_argument("--salida", default=str(ROOT / "resultados" / "recorrido_autorrelleno"))
    ap.add_argument("--espera-s", type=int, default=30, help="tiempo de revisión de la ventana de Forms")
    ap.add_argument("--con-ventana", action="store_true", help="Forms con ventana visible")
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
    t_inicio = time.time() - 1
    tmp = Path(tempfile.mkdtemp(prefix="autorrelleno-"))
    lab = Laboratorio(tmp / "lab", nuevo=True, forms=Path(a.forms))
    lab.env_extra.update({"MONITOREO_AUTORRELLENO_ESPERA_S": str(a.espera_s),
                          "MONITOREO_FORMS_HEADLESS": "0" if a.con_ventana else "1"})
    ck: list[dict] = []
    res: dict = {"recorrido": "autorrelleno_forms_laboratorio", "plataforma": platform.platform(),
                 "python": platform.python_version(), "inicio": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "datos": "ficticios; portal de laboratorio propio en 127.0.0.1", "forms_con_ventana": a.con_ventana}

    def check(nombre, ok, detalle=None):
        ck.append({"comprobacion": nombre, "ok": bool(ok), "detalle": detalle})
        print(("OK   " if ok else "FALLA") + " " + nombre + ("" if ok else f"  {detalle}"))
        return ok
    try:
        try:
            d = lab.iniciar()
        except FormsNoListo as e:
            print(e)
            return 3
        pin = json.loads(lab.perfil_forms.read_text(encoding="utf-8"))
        res["forms"] = {"commit": pin.get("forms_commit"), "perfil_sha256": pin["perfil_sha256"],
                        "firma": pin["firma_estructura_esperada"], "url": pin["url"]}
        check("Forms aprobó el perfil del formulario /cita del portal de laboratorio", bool(pin["perfil_sha256"]))
        adm = httpx.Client(base_url=d["motor"], timeout=30, trust_env=False)
        adm.headers["Authorization"] = "Bearer " + adm.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PWD}).json()["token"]
        ctl = httpx.Client(base_url=d["portal"], timeout=10, trust_env=False, headers={"X-Control-Token": lab.cfg["token_control"]})
        reg = [ctl.get("/control/registro-ejemplo", params={"n": n}).json() for n in (1, 2)]
        persona = {**PERSONA, "fecha_nacimiento": reg[0]["fecha_nacimiento"], "identificador": reg[0]["identificador"],
                   "tramite": reg[0]["tramite"]}
        sin = {**PERSONA_SIN, "fecha_nacimiento": reg[1]["fecha_nacimiento"], "identificador": reg[1]["identificador"],
               "tramite": reg[1]["tramite"]}
        entrada = {"operador": list(OPERADOR), "persona": persona, "persona_sin": sin, "espera_s": a.espera_s}
        (tmp / "entrada.json").write_text(json.dumps(entrada), "utf-8")
        env = navegador.entorno()
        p = subprocess.run([nodo, str(ROOT / "scripts" / "recorrido_autorrelleno.js"), d["motor"], str(capt), str(tmp / "entrada.json")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900, env=env,
                           cwd=str(ROOT / "scripts"))
        linea = next((x for x in p.stdout.splitlines() if x.startswith("RESULTADO ")), None)
        if linea is None:
            (out / "recorrido_autorrelleno_node.log").write_text(p.stdout + "\n" + p.stderr, "utf-8")
            check("el navegador automatizado se ejecutó", False, (p.stderr or p.stdout)[-400:])
            return 3 if "Cannot find module" in p.stderr else 1
        nav = json.loads(linea[len("RESULTADO "):])
        res["navegador"] = nav
        for x in nav["pasos"]:
            check(f"interfaz · {x['paso']}", x["ok"], x["detalle"])
        check("interfaz · recorrido completo sin interrupciones", not nav.get("fallo"), nav.get("fallo"))
        check("interfaz · sin errores de JavaScript", not nav["errores_js"], nav["errores_js"])
        check("interfaz · sin respuestas HTTP de error", not nav["http_errores"], nav["http_errores"])
        if nav.get("atencion"):
            op = httpx.Client(base_url=d["motor"], timeout=30, trust_env=False)
            op.headers["Authorization"] = "Bearer " + op.post("/api/auth/login", json={"username": OPERADOR[0], "password": OPERADOR[1]}).json()["token"]
            r = op.post(f"/api/autorrelleno/atenciones/{nav['atencion']}")
            check("servidor · tras revocar, el autorrellenado se NIEGA (409 CONSENTIMIENTO_REVOCADO)",
                  r.status_code == 409 and r.json()["detail"]["codigo"] == "CONSENTIMIENTO_REVOCADO", r.text[:200])
            if nav.get("atencion_sin"):
                r = op.post(f"/api/autorrelleno/atenciones/{nav['atencion_sin']}")
                check("servidor · sin consentimiento, el autorrellenado se NIEGA (409 SIN_CONSENTIMIENTO)",
                      r.status_code == 409 and r.json()["detail"]["codigo"] == "SIN_CONSENTIMIENTO", r.text[:200])
            exps = adm.get("/api/expedientes").json()
            e = next((x for x in exps if x.get("codigo") and x.get("estado") == "CERRADO"), None)
            if e:
                t = adm.get(f"/api/expedientes/{e['id']}/trazabilidad").json()
                linea = t.get("linea_de_tiempo", [])
                tipos = [x.get("tipo") for x in linea]
                fin = next((x for x in linea if x.get("tipo") == "AUTORRELLENO_TERMINADO"), {})
                det = fin.get("detalle") or {}
                check("historial · AUTORRELLENO_SOLICITADO y AUTORRELLENO_TERMINADO en el expediente",
                      "AUTORRELLENO_SOLICITADO" in tipos and "AUTORRELLENO_TERMINADO" in tipos, tipos[-8:])
                check("historial · 8 campos correctos, 2 confirmaciones, sin envío por Forms, 0 procesos restantes",
                      det.get("campos_correctos") == 8 and det.get("confirmaciones_coinciden") == 2
                      and det.get("envio_por_conector") is False and det.get("procesos_restantes") == 0, det)
                sol = next((x for x in linea if x.get("tipo") == "AUTORRELLENO_SOLICITADO"), {})
                check("historial · la solicitud usó el consentimiento real del expediente",
                      (sol.get("detalle") or {}).get("consentimiento") == nav.get("consentimiento"), sol.get("detalle"))
            import sqlite3
            con = sqlite3.connect(str(tmp / "lab" / "cauce_lab.db"))
            try:
                textos = " ".join(str(r) for r in con.execute("select detail, target from audit_log"))
                textos += " ".join(str(r) for r in con.execute("select detalle from expediente_eventos"))
            finally:
                con.close()
            fugas = [k for k in ("telefono", "correo", "fecha_nacimiento", "identificador") if persona[k] in textos]
            check("registros · auditoría e historial sin los datos personales ficticios", not fugas, fugas)
        # todos los archivos de texto del laboratorio (registros del motor y del portal, estado y perfiles de Forms)
        # y los registros de Forms escritos durante esta corrida. Se excluye solo el registro ficticio del portal
        # (portal.json), que guarda a propósito la fecha y el identificador de sus personas de ejemplo.
        valores = {k: persona[k] for k in ("nombre", "telefono", "correo", "fecha_nacimiento", "identificador")}
        forms = Path(a.forms)
        hall = buscar_datos([tmp / "lab", forms / "logs", forms / "state", out], valores, desde=t_inicio,
                            excluir=(tmp / "lab" / "portal.json",))
        check("registros · ningún archivo de texto (motor, portal, Forms, evidencia) contiene los datos ficticios",
              not hall, hall)
    finally:
        lab.detener()
        res["comprobaciones"] = ck
        res["ok"] = bool(ck) and all(x["ok"] for x in ck)
        res["fin"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        out.mkdir(parents=True, exist_ok=True)
        (out / "recorrido_autorrelleno.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), "utf-8")
        print(f"\n{sum(x['ok'] for x in ck)}/{len(ck)} comprobaciones. Evidencia: {out}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
