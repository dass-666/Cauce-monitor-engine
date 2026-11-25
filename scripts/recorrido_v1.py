"""Recorrido de la VERSIÓN CANDIDATA 1.0 con los componentes reales (Windows o Linux), datos ficticios.

Motor real + portal de laboratorio propio (127.0.0.1) + observador universal por URL + plan/coincidencias + Ventanilla
+ Forms REAL en ventana visible. La única pieza simulada es la PERSONA: actúa con clics y teclas reales dentro de la
MISMA ventana que Forms dejó rellena (tests/herramientas/forms_persona.py) y nunca vuelve a teclear los 8 campos.

Comprueba además: caso sin disponibilidad, duplicados (preparar dos veces, autorrellenar dos veces, folio repetido),
folio falso rechazado, y que tras REINICIAR el motor todo sigue igual (persistencia).
  python scripts/recorrido_v1.py [--forms ..\\cauce-forms] [--sin-ventana]
Resultado: resultados/recorrido_v1/recorrido_v1.json
"""
from __future__ import annotations

import argparse
import json
import platform
import secrets
import shutil
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from demo_integral import FUENTE, sembrar  # noqa: E402
from ventanilla_laboratorio import ADMIN_PWD, OPERADOR, Laboratorio  # noqa: E402

ck: list[dict] = []


def check(nombre, ok, detalle=None):
    ck.append({"comprobacion": nombre, "ok": bool(ok), "detalle": None if ok else detalle})
    print(("OK    " if ok else "FALLA ") + nombre + ("" if ok else f"  {str(detalle)[:300]}"), flush=True)
    return ok


def esperar(f, t=60.0, paso=0.5):
    fin = time.monotonic() + t
    while time.monotonic() < fin:
        v = f()
        if v:
            return v
        time.sleep(paso)
    return None


def cliente(base, u, p):
    c = httpx.Client(base_url=base, timeout=60, trust_env=False)
    c.headers["Authorization"] = "Bearer " + c.post("/api/auth/login", json={"username": u, "password": p}).json()["token"]
    return c


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--forms", default=str(ROOT.parent / "cauce-forms"))
    ap.add_argument("--salida", default=str(ROOT / "resultados" / "recorrido_v1"))
    ap.add_argument("--sin-ventana", action="store_true")
    a = ap.parse_args()
    forms = Path(a.forms).resolve()
    out = Path(a.salida)
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="recorrido-v1-"))
    folio_arch = tmp / "folio.txt"
    lab = Laboratorio(tmp / "lab", nuevo=True, forms=forms)
    lab.env_extra.update({"MONITOREO_FORMS_HEADLESS": "1" if a.sin_ventana else "0",
                          "MONITOREO_AUTORRELLENO_ESPERA_S": "240"})
    res = {"recorrido": "v1_candidata", "plataforma": platform.platform(), "inicio": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "datos": "FICTICIOS; portal de laboratorio 127.0.0.1; la persona es simulada DENTRO de la ventana de Forms"}
    try:
        d = lab.iniciar()
        demo = sembrar(lab, d)
        fecha, e1, e2 = demo["fecha_coincidencia"], demo["expedientes"][0], demo["expedientes"][1]
        py_forms = forms / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        # Forms real, envuelto solo para que una persona simulada actúe en su ventana
        lab.motor.stop()
        lab.motor.env["MONITOREO_FORMS_COMANDO"] = json.dumps(
            [str(py_forms), str(ROOT / "tests" / "herramientas" / "forms_persona.py"), str(forms), fecha, "NOR-1",
             str(folio_arch)])
        lab.motor.start()
        lab.motor.wait_ready()
        adm = cliente(d["motor"], "admin", ADMIN_PWD)
        op = cliente(d["motor"], *OPERADOR)

        def plan():
            return {e["expediente_id"]: e for p in adm.get("/api/planes").json()["planes"]
                    for e in adm.get(f"/api/planes/{p['id']}").json()["elementos"]}

        # A. expedientes
        check("A · 2 expedientes ficticios con consentimiento en el plan", set(plan()) == {e1["id"], e2["id"]}, plan())
        # B. fuente por URL: la plantilla se copia, se prueba y se guarda (lo mismo que hace la pantalla)
        f0 = next(f for f in adm.get("/api/fuentes-web").json()["fuentes"] if f["nombre"] == FUENTE)
        cuerpo = {k: f0[k] for k in ("url", "tramite", "sucursal", "estrategia", "perfil")} | {"nombre": FUENTE + "-2"}
        pr = adm.post("/api/fuentes-web/probar", json=cuerpo).json()
        check("B · «Probar» lee la página sin guardar", (pr.get("vista") or {}).get("estado") == "ACTIVA", pr)
        r = adm.post("/api/fuentes-web", json=cuerpo)
        check("B · «Guardar y observar» lanza el observador", r.status_code == 201, r.text[:200])
        r2 = adm.post("/api/fuentes-web", json=cuerpo)
        check("B · la misma fuente no se duplica (409)", r2.status_code == 409, r2.status_code)
        mirando = esperar(lambda: any(((f.get("observador") or {}).get("vista") or {}) and f["activo"]
                                      for f in adm.get("/api/fuentes-web").json()["fuentes"]), 90)
        check("B · la página queda «Mirando»", mirando)
        time.sleep(4)
        p0 = plan()
        check("C · sin disponibilidad: ambos expedientes EN_ESPERA",
              p0[e1["id"]]["estado"] == p0[e2["id"]]["estado"] == "EN_ESPERA", {k: v["estado"] for k, v in p0.items()})
        # C. provocar disponibilidad (Calendario lab) -> detección -> coincidencia
        t = time.perf_counter()
        adm.post("/api/laboratorio/portal/calendario/habilitar",
                 json={"sucursal": "NOR-1", "tramite": demo["tramite"], "fecha": fecha, "capacidad": 1}).raise_for_status()
        el = esperar(lambda: (lambda e: e if e["estado"] == "ASIGNADO" else None)(plan()[e1["id"]]), 60)
        res["deteccion_s"] = round(time.perf_counter() - t, 1)
        check("C · coincidencia: fecha, sucursal y expediente correctos",
              el and el["fecha_asignada"] == fecha and el.get("sucursal_asignada", "NOR-1") == "NOR-1", el)
        check("C · el otro expediente sigue EN_ESPERA", plan()[e2["id"]]["estado"] == "EN_ESPERA")
        # D. Ventanilla: operación preparada (y no se duplica)
        r = op.post(f"/api/planificador/elementos/{el['id']}/preparar")
        check("D · operación preparada desde el plan", r.status_code == 200, r.text[:200])
        rid = r.json()["reservacion_id"]
        r2 = op.post(f"/api/planificador/elementos/{el['id']}/preparar")
        check("D · preparar otra vez no crea otra reservación", r2.status_code != 200 or r2.json().get("reservacion_id") == rid,
              r2.text[:200])
        at = op.post("/api/ventanilla/atenciones", json={"clave": secrets.token_hex(16)}).json()
        op.post(f"/api/ventanilla/atenciones/{at['id']}/expediente", json={"expediente_id": e1["id"]}).raise_for_status()
        st = op.get(f"/api/autorrelleno/atenciones/{at['id']}").json()
        check("D · Ventanilla ve la coincidencia ligada (fecha/sucursal) y puede autorrellenar",
              st["disponible"] and (st["coincidencia"] or {}).get("fecha") == fecha, st)
        # E. Forms real; la persona sigue en la misma ventana hasta el folio
        r = op.post(f"/api/autorrelleno/atenciones/{at['id']}")
        check("E · «Autorrellenar» lanza Forms", r.status_code == 202, r.text[:200])
        r2 = op.post(f"/api/autorrelleno/atenciones/{at['id']}")
        check("E · un segundo clic no lanza otro Forms", r2.status_code == 409, r2.text[:200])
        fin = esperar(lambda: (lambda c: c if c and not c["en_curso"] else None)(
            op.get(f"/api/autorrelleno/atenciones/{at['id']}").json()["corrida"]), 400, 1)
        inf = (fin or {}).get("informe") or {}
        res["forms"] = {k: (fin or {}).get(k) for k in ("estado", "resultado_operacion")} | {
            "revision": inf.get("revision_humana"), "campos_ok": sum(c["correcto"] for c in inf.get("campos", []))}
        check("E · Forms rellenó y verificó 8 campos + 2 confirmaciones, sin enviar por su cuenta",
              res["forms"]["campos_ok"] == 8 and all(c["coinciden"] for c in inf.get("confirmaciones", []))
              and not inf.get("envio_realizado_por_conector"), res["forms"])
        folio = folio_arch.read_text("utf-8").strip() if folio_arch.exists() else ""
        res["folio"] = folio
        check("F · la persona terminó en la MISMA ventana (sin repetir datos) y el portal le dio folio",
              bool(folio) and inf.get("revision_humana") == "envio_humano" and fin["estado"] == "ENVIADA_POR_PERSONA",
              {"folio": folio, **res["forms"]})
        check("F · operación lista para el folio (no es cita todavía)",
              fin and fin.get("resultado_operacion") == "LISTA_PARA_CONFIRMACION", res["forms"])

        def mandar(f1, f2=None):
            return op.post(f"/api/autorrelleno/reservaciones/{rid}/folio", json={"folio": f1, "folio_confirmacion": f2 or f1})
        r = mandar("DIA-20991231-99999Z")
        check("F · folio inexistente rechazado", r.status_code >= 400, r.text[:200])
        r = mandar(folio)
        check("F · folio verificado con el portal", r.status_code == 200, r.text[:300])
        r = mandar(folio)
        check("F · registrar el folio otra vez no duplica nada", r.status_code in (200, 409), r.text[:200])

        def estado_final():
            resv = [x for x in adm.get("/api/reservaciones").json() if x.get("expediente_id") == e1["id"]]
            return {"reservaciones": [(x["estado"], x["fecha"], x["sucursal"], x["folio"]) for x in resv],
                    "expediente": adm.get(f"/api/expedientes/{e1['id']}").json()["estado"],
                    "plan": plan()[e1["id"]]["estado"], "otro": plan()[e2["id"]]["estado"]}
        antes = estado_final()
        res["estado_final"] = antes
        check("F · reservación CONFIRMADA (única), expediente y plan actualizados",
              antes["reservaciones"] == [("CONFIRMADA", fecha, "NOR-1", folio)]
              and antes["expediente"] == "RESERVACION_CONFIRMADA" and antes["plan"] == "CONFIRMADO", antes)
        tl = [x.get("tipo") for x in adm.get(f"/api/expedientes/{e1['id']}/trazabilidad").json().get("linea_de_tiempo", [])]
        check("G · historial: coincidencia -> Forms -> lista -> confirmada",
              all(k in tl for k in ("PLAN_COINCIDENCIA", "COINCIDENCIA_ENTREGADA_A_FORMS", "AUTORRELLENO_TERMINADO",
                                    "OPERACION_LISTA_CONFIRMACION", "RESERVACION_CONFIRMADA")), tl)
        # H. reinicio: el motor se detiene y vuelve a arrancar con la misma carpeta
        lab.motor.stop()
        lab.motor.start()
        lab.motor.wait_ready()
        adm = cliente(d["motor"], "admin", ADMIN_PWD)
        despues = estado_final()
        check("H · tras reiniciar, todo sigue igual (persistencia)", despues == antes, {"antes": antes, "despues": despues})
        fuentes = adm.get("/api/fuentes-web").json()["fuentes"]
        check("H · tras reiniciar, la página sigue configurada y activa",
              any(f["nombre"] == FUENTE + "-2" and f["activo"] for f in fuentes), fuentes)
    except Exception as e:  # noqa: BLE001
        check("el recorrido terminó sin excepción", False, f"{type(e).__name__}: {e}")
    finally:
        lab.detener()
        shutil.rmtree(tmp, ignore_errors=True)
    res["comprobaciones"] = ck
    res["resumen"] = f"{sum(c['ok'] for c in ck)}/{len(ck)}"
    (out / "recorrido_v1.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n{res['resumen']} comprobaciones. Evidencia: {out / 'recorrido_v1.json'}")
    return 0 if all(c["ok"] for c in ck) else 1


if __name__ == "__main__":
    sys.exit(main())
