"""Estabilidad de la VERSIÓN CANDIDATA 1.0 en Windows (o Linux), con los componentes reales y datos ficticios.

Escenarios, sobre UNA misma operación (coincidencia -> Ventanilla -> Forms real -> folio):
  1. Forms muere de golpe con la ventana abierta (se termina su proceso): Monitor lo registra, no deja procesos y la
     operación se puede volver a lanzar sin duplicar nada.
  2. Monitor muere de golpe (kill) con Forms abierto: el árbol de Forms (y su Chromium) termina con él; al arrancar,
     la operación queda INTERRUMPIDA, reabrir exige motivo y el folio está bloqueado hasta que Forms la deje lista.
  3. Conexión con el portal interrumpida al registrar el folio: no se marca nada (503); con el portal de vuelta, el
     mismo folio se confirma. El observador vuelve a mirar la página cuando el portal regresa.
  4. «Reinicio de Windows» SIMULADO: se matan de golpe motor y portal y se vuelve a abrir el laboratorio sobre la
     misma carpeta: todo sigue (no se reinicia el equipo de verdad).
  python scripts/recorrido_estabilidad.py [--forms ..\\cauce-forms]
Resultado: resultados/recorrido_estabilidad/recorrido_estabilidad.json
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

import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from demo_integral import FUENTE, sembrar  # noqa: E402
from recorrido_v1 import check, ck, cliente, esperar  # noqa: E402
from ventanilla_laboratorio import ADMIN_PWD, OPERADOR, Laboratorio  # noqa: E402


def procesos_forms(forms: Path) -> list[psutil.Process]:
    out = []
    for p in psutil.process_iter(["cmdline", "exe"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
            if "forms_persona.py" in cmd or str(forms / ".pw-browsers") in (p.info["exe"] or "") \
                    or str(forms / ".pw-browsers") in cmd:
                out.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--forms", default=str(ROOT.parent / "cauce-forms"))
    ap.add_argument("--salida", default=str(ROOT / "resultados" / "recorrido_estabilidad"))
    a = ap.parse_args()
    forms = Path(a.forms).resolve()
    out = Path(a.salida)
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="estabilidad-"))
    folio_arch = tmp / "folio.txt"
    py_forms = forms / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    lab = Laboratorio(tmp / "lab", nuevo=True, forms=forms)
    lab.env_extra.update({"MONITOREO_FORMS_HEADLESS": "0", "MONITOREO_AUTORRELLENO_ESPERA_S": "240"})
    res = {"recorrido": "estabilidad_v1", "plataforma": platform.platform(), "inicio": time.strftime("%Y-%m-%dT%H:%M:%S")}

    def comando(fecha, quieta):
        return json.dumps([str(py_forms), str(ROOT / "tests" / "herramientas" / "forms_persona.py"), str(forms), fecha,
                           "NOR-1", str(folio_arch), "QXZ482915", "quieta" if quieta else "activa"])

    def arrancar_motor(fecha, quieta):
        lab.motor.env["MONITOREO_FORMS_COMANDO"] = comando(fecha, quieta)
        lab.motor.start()
        lab.motor.wait_ready()

    try:
        d = lab.iniciar()
        demo = sembrar(lab, d)
        fecha, e1 = demo["fecha_coincidencia"], demo["expedientes"][0]
        lab.motor.stop()
        arrancar_motor(fecha, quieta=True)
        adm, op = cliente(d["motor"], "admin", ADMIN_PWD), cliente(d["motor"], *OPERADOR)
        f0 = next(f for f in adm.get("/api/fuentes-web").json()["fuentes"] if f["nombre"] == FUENTE)
        cuerpo = {k: f0[k] for k in ("url", "tramite", "sucursal", "estrategia", "perfil")} | {"nombre": FUENTE + "-2"}
        adm.post("/api/fuentes-web", json=cuerpo).raise_for_status()
        time.sleep(5)
        adm.post("/api/laboratorio/portal/calendario/habilitar",
                 json={"sucursal": "NOR-1", "tramite": demo["tramite"], "fecha": fecha, "capacidad": 1}).raise_for_status()

        def plan():
            return {e["expediente_id"]: e for p in adm.get("/api/planes").json()["planes"]
                    for e in adm.get(f"/api/planes/{p['id']}").json()["elementos"]}
        el = esperar(lambda: (lambda e: e if e["estado"] == "ASIGNADO" else None)(plan()[e1["id"]]), 60)
        check("0 · coincidencia asignada", bool(el), plan())
        rid = op.post(f"/api/planificador/elementos/{el['id']}/preparar").json()["reservacion_id"]
        at = op.post("/api/ventanilla/atenciones", json={"clave": secrets.token_hex(16)}).json()
        op.post(f"/api/ventanilla/atenciones/{at['id']}/expediente", json={"expediente_id": e1["id"]}).raise_for_status()
        url_at = f"/api/autorrelleno/atenciones/{at['id']}"

        def corrida():
            return op.get(url_at).json()

        # ---- 1. Forms muere de golpe con la ventana abierta
        check("1 · autorrellenar lanzado", op.post(url_at).status_code == 202)
        vivos = esperar(lambda: [p for p in procesos_forms(forms) if "chrom" in (p.name() or "").lower()], 90)
        check("1 · Forms y su Chromium abiertos", bool(vivos), [p.name() for p in procesos_forms(forms)])
        time.sleep(12)                                   # que termine de rellenar y quede en revisión
        for p in procesos_forms(forms):
            if "forms_persona.py" in " ".join(p.cmdline()):
                p.kill()                                 # TerminateProcess: el Python de Forms muere sin responder
        fin = esperar(lambda: (lambda c: c if c and not c["en_curso"] else None)(corrida()["corrida"]), 60)
        res["forms_muerto"] = {k: (fin or {}).get(k) for k in ("estado", "codigo", "resultado_operacion")}
        check("1 · Monitor registra la corrida terminada sin respuesta válida (no la da por buena)",
              fin and fin["estado"] in ("INCIERTA", "ERROR") and fin.get("resultado_operacion") != "LISTA_PARA_CONFIRMACION",
              res["forms_muerto"])
        restos = esperar(lambda: not procesos_forms(forms), 30)
        check("1 · no quedan procesos de Forms ni Chromium", restos, [p.name() for p in procesos_forms(forms)])
        k = corrida()["coincidencia"] or {}
        check("1 · la operación sigue PREPARADA, sin folio, misma reservación",
              k.get("reservacion_id") == rid and k.get("estado_reservacion") == "PREPARADA" and not k.get("folio"), k)

        # ---- 2. Monitor muere de golpe con Forms abierto
        r = op.post(url_at)
        check("2 · se puede volver a lanzar Forms tras el fallo", r.status_code == 202, r.text[:200])
        esperar(lambda: [p for p in procesos_forms(forms) if "chrom" in (p.name() or "").lower()], 90)
        time.sleep(12)
        antes = len(procesos_forms(forms))
        lab.motor.proc.kill()
        lab.motor.proc.wait(20)
        lab.motor.log.close()
        restos = esperar(lambda: not procesos_forms(forms), 30)
        res["monitor_muerto"] = {"procesos_forms_antes": antes, "procesos_forms_despues": len(procesos_forms(forms))}
        check("2 · al morir Monitor, Forms y su Chromium terminan con él (Job Object)", restos and antes > 0,
              res["monitor_muerto"])
        arrancar_motor(fecha, quieta=False)
        adm, op = cliente(d["motor"], "admin", ADMIN_PWD), cliente(d["motor"], *OPERADOR)
        k = corrida()["coincidencia"] or {}
        check("2 · al arrancar, la operación queda INTERRUMPIDA (misma reservación, sin folio)",
              k.get("interrumpida") is True and k.get("reservacion_id") == rid and not k.get("folio"), k)
        r = op.post(url_at)
        check("2 · reabrir sin motivo se niega", r.status_code == 409 and "OPERACION_INTERRUMPIDA" in r.text, r.text[:200])
        r = op.post(f"/api/autorrelleno/reservaciones/{rid}/folio", json={"folio": "XXXX-0000", "folio_confirmacion": "XXXX-0000"})
        check("2 · el folio está bloqueado mientras la operación esté interrumpida", r.status_code == 409, r.text[:200])
        r = op.post(url_at, json={"reintento": "VENTANA_CERRADA"})
        check("2 · reintento con motivo: la persona termina en la misma ventana", r.status_code == 202, r.text[:200])
        fin = esperar(lambda: (lambda c: c if c and not c["en_curso"] else None)(corrida()["corrida"]), 300, 1)
        folio = folio_arch.read_text("utf-8").strip() if folio_arch.exists() else ""
        check("2 · Forms 8/8 y la persona obtuvo folio tras la recuperación",
              fin and fin["estado"] == "ENVIADA_POR_PERSONA" and bool(folio), {"fin": (fin or {}).get("estado"), "folio": folio})

        # ---- 3. conexión con el portal interrumpida al registrar el folio
        lab.portal.detener(forzar=True)
        r = op.post(f"/api/autorrelleno/reservaciones/{rid}/folio", json={"folio": folio, "folio_confirmacion": folio})
        check("3 · sin portal: el folio no se marca (503) y no cambia nada",
              r.status_code == 503 and (corrida()["coincidencia"] or {}).get("estado_reservacion") == "PREPARADA", r.text[:200])
        lab.portal.iniciar()
        r = op.post(f"/api/autorrelleno/reservaciones/{rid}/folio", json={"folio": folio, "folio_confirmacion": folio})
        check("3 · con el portal de vuelta, el mismo folio se confirma", r.status_code == 200, r.text[:300])
        mirando = esperar(lambda: any(f["nombre"] == FUENTE + "-2" and ((f.get("observador") or {}).get("vista") or {})
                                      and "ACTIVA" in json.dumps((f.get("observador") or {}).get("vista"))
                                      for f in adm.get("/api/fuentes-web").json()["fuentes"]), 120, 2)
        check("3 · el observador vuelve a mirar la página tras el corte del portal", mirando,
              [(f["nombre"], (f.get("observador") or {}).get("vista")) for f in adm.get("/api/fuentes-web").json()["fuentes"]])

        def estado():
            resv = [x for x in adm.get("/api/reservaciones").json() if x.get("expediente_id") == e1["id"]]
            return {"reservaciones": [(x["estado"], x["fecha"], x["folio"]) for x in resv],
                    "expediente": adm.get(f"/api/expedientes/{e1['id']}").json()["estado"], "plan": plan()[e1["id"]]["estado"]}
        antes = estado()
        check("3 · una sola reservación CONFIRMADA; expediente y plan actualizados",
              antes["reservaciones"] == [("CONFIRMADA", fecha, folio)] and antes["plan"] == "CONFIRMADO", antes)

        # ---- 4. «reinicio de Windows» simulado: todo muere de golpe y se vuelve a abrir
        lab.motor.proc.kill()
        lab.motor.proc.wait(20)
        lab.motor.log.close()
        lab.portal.detener(forzar=True)
        lab2 = Laboratorio(tmp / "lab", nuevo=False, forms=forms)
        lab2.env_extra.update(lab.env_extra)
        lab = lab2
        d = lab.iniciar()
        adm = cliente(d["motor"], "admin", ADMIN_PWD)
        despues = estado()
        check("4 · tras matar todo y volver a abrir, nada se perdió", despues == antes, {"antes": antes, "despues": despues})
        check("4 · la página observada sigue configurada y activa",
              any(f["nombre"] == FUENTE + "-2" and f["activo"] for f in adm.get("/api/fuentes-web").json()["fuentes"]))
    except Exception as e:  # noqa: BLE001
        check("el recorrido terminó sin excepción", False, f"{type(e).__name__}: {e}")
    finally:
        lab.detener()
        shutil.rmtree(tmp, ignore_errors=True)
    res["comprobaciones"] = ck
    res["resumen"] = f"{sum(c['ok'] for c in ck)}/{len(ck)}"
    (out / "recorrido_estabilidad.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n{res['resumen']} comprobaciones. Evidencia: {out / 'recorrido_estabilidad.json'}")
    return 0 if all(c["ok"] for c in ck) else 1


if __name__ == "__main__":
    sys.exit(main())
