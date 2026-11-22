"""Laboratorio · ¿detener o reiniciar Monitor con la ventana de Forms abierta deja procesos huérfanos?

Arranca el laboratorio de Ventanilla con Forms REAL en una carpeta temporal nueva (datos FICTICIOS, portal propio en
127.0.0.1; no toca data/, ni otras instalaciones, bases o respaldos) y prueba dos casos con la ventana abierta:

  A. parada ORDENADA de Monitor (Ctrl+Break en Windows, SIGTERM en Linux: lo que hace cerrar el laboratorio con Ctrl+C)
  B. muerte FORZADA de Monitor (TerminateProcess / SIGKILL: como cerrar la consola o un corte)

En cada caso cuenta los procesos de Forms (Python de Forms y Chromium: llevan la marca CAUCE_LANZADOR_ID en su
entorno) antes y después, vuelve a arrancar Monitor y comprueba que responde y que permite un autorrellenado nuevo.

Uso: python scripts/prueba_reinicio_autorrelleno.py --forms <carpeta de Forms> [--salida DIR] [--con-ventana]
Código 0 si todo pasa, 1 si algo falla, 3 si falta Forms.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx
import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from autorrelleno_forms import FormsNoListo  # noqa: E402
from ventanilla_laboratorio import OPERADOR, Laboratorio  # noqa: E402

MARCA = "CAUCE_LANZADOR_ID"


def procesos_forms() -> list[int]:
    pids = []
    for p in psutil.process_iter():
        try:
            if MARCA in p.environ():
                pids.append(p.pid)
        except (psutil.Error, OSError):
            continue
    return pids


def esperar(cond, t: float, paso: float = 0.25):
    fin = time.monotonic() + t
    while time.monotonic() < fin:
        v = cond()
        if v:
            return v
        time.sleep(paso)
    return cond()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--forms", required=True)
    ap.add_argument("--salida", default=str(ROOT / "resultados" / "prueba_reinicio_autorrelleno"))
    ap.add_argument("--con-ventana", action="store_true")
    a = ap.parse_args()
    out = Path(a.salida)
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="reinicio-autorrelleno-"))
    lab = Laboratorio(tmp / "lab", nuevo=True, forms=Path(a.forms))
    lab.env_extra.update({"MONITOREO_AUTORRELLENO_ESPERA_S": "600",       # la ventana sigue abierta: no caduca sola
                          "MONITOREO_FORMS_HEADLESS": "0" if a.con_ventana else "1"})
    ck: list[dict] = []
    res: dict = {"prueba": "reinicio_monitor_con_forms_abierto", "plataforma": platform.platform(),
                 "python": platform.python_version(), "inicio": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "datos": "ficticios; portal de laboratorio propio en 127.0.0.1"}

    def check(nombre, ok, detalle=None):
        ck.append({"comprobacion": nombre, "ok": bool(ok), "detalle": detalle})
        print(("OK   " if ok else "FALLA") + " " + nombre + ("" if ok else f"  {detalle}"), flush=True)
        return ok

    n_reg = [0]

    def lanzar_forms(d: dict) -> tuple[httpx.Client, int]:
        """Un expediente ficticio con consentimiento y pulsar «Autorrellenar»; espera a que Forms esté abierto."""
        op = httpx.Client(base_url=d["motor"], timeout=30, trust_env=False)
        op.headers["Authorization"] = "Bearer " + op.post("/api/auth/login", json={"username": OPERADOR[0], "password": OPERADOR[1]}).json()["token"]
        ctl = httpx.Client(base_url=d["portal"], timeout=10, trust_env=False, headers={"X-Control-Token": lab.cfg["token_control"]})
        n_reg[0] += 1
        reg = ctl.get("/control/registro-ejemplo", params={"n": n_reg[0]}).json()
        tel, correo = f"555000{n_reg[0]:04d}", f"reinicio{n_reg[0]}@ejemplo.test"
        datos = {"nombre": "Persona Ficticia Reinicio " + "ABCDEFGHIJ"[n_reg[0] % 10], "fecha_nacimiento": reg["fecha_nacimiento"],
                 "telefono": tel, "telefono_confirmacion": tel, "correo": correo, "correo_confirmacion": correo,
                 "region": "NORTE", "sucursal": "NOR-1", "sucursales_alternativas": ["NOR-2"],
                 "tipo_tramite": reg["tramite"], "id_registro_previo": reg["identificador"],
                 "consentimiento": {"medio": "FIRMA_PAPEL", "aviso_privacidad": "AP-2026-01",
                                    "evidencia_sha256": hashlib.sha256(f"formato ficticio reinicio {n_reg[0]}".encode()).hexdigest(),
                                    "evidencia_ref": "Carpeta LAB-REINICIO"}}
        at = op.post("/api/ventanilla/atenciones", json={"clave": uuid.uuid4().hex}).json()
        r = op.post(f"/api/ventanilla/atenciones/{at['id']}/expediente", json={"datos": datos})
        if r.status_code != 200:
            raise RuntimeError(f"expediente no creado: {r.status_code} {r.text[:300]}")
        r = op.post(f"/api/autorrelleno/atenciones/{at['id']}")
        if r.status_code != 202:
            raise RuntimeError(f"autorrellenado no aceptado: {r.status_code} {r.text[:200]}")
        esperar(lambda: len(procesos_forms()) >= 2, 40)          # Python de Forms + Chromium
        time.sleep(6)                                              # Forms teclea
        return op, at["id"]

    try:
        try:
            d = lab.iniciar()
        except FormsNoListo as e:
            print(e)
            return 3
        check("antes de empezar no hay procesos de Forms", not procesos_forms(), procesos_forms())
        for caso, como in (("A_parada_ordenada", "ordenada"), ("B_muerte_forzada", "forzada")):
            _, aid = lanzar_forms(d)
            antes = procesos_forms()
            check(f"{caso} · Forms y Chromium abiertos (≥2 procesos)", len(antes) >= 2, len(antes))
            if como == "ordenada":
                forma = lab.motor.stop()
                check(f"{caso} · Monitor se detuvo de forma ordenada", forma == "ordenada", forma)
            else:
                lab.motor.proc.kill()                              # TerminateProcess / SIGKILL: sin limpieza
                lab.motor.proc.wait(15)
                lab.motor.log.close()
            restos = esperar(lambda: not procesos_forms(), 20) or procesos_forms()
            check(f"{caso} · 0 procesos de Forms/Chromium tras detener Monitor", restos is True or not restos,
                  {"antes": len(antes), "despues": procesos_forms()})
            # Monitor vuelve a arrancar (misma base) y responde
            lab.motor.start()
            lab.motor.wait_ready()
            check(f"{caso} · Monitor vuelve a arrancar y responde", True)
            op2 = httpx.Client(base_url=d["motor"], timeout=30, trust_env=False)
            op2.headers["Authorization"] = "Bearer " + op2.post("/api/auth/login", json={"username": OPERADOR[0], "password": OPERADOR[1]}).json()["token"]
            r = op2.get(f"/api/autorrelleno/atenciones/{aid}")
            check(f"{caso} · tras reiniciar no figura ninguna corrida en curso", r.status_code == 200
                  and not (r.json().get("corrida") or {}).get("en_curso"), r.text[:200])
        # tras los dos reinicios, un autorrellenado nuevo funciona (no quedó nada bloqueado)
        op, aid = lanzar_forms(d)
        lab.motor.stop()
        check("final · tras reiniciar, un autorrellenado nuevo arranca y se detiene sin restos",
              esperar(lambda: not procesos_forms(), 20), procesos_forms())
        lab.motor = None
    finally:
        try:
            lab.detener()
        except Exception:  # noqa: BLE001
            pass
        for pid in procesos_forms():                               # limpieza de seguridad (debe ser vacío)
            try:
                psutil.Process(pid).kill()
            except psutil.Error:
                pass
        res["comprobaciones"] = ck
        res["ok"] = bool(ck) and all(x["ok"] for x in ck)
        res["fin"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        (out / "prueba_reinicio_autorrelleno.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), "utf-8")
        print(f"\n{sum(x['ok'] for x in ck)}/{len(ck)} comprobaciones. Evidencia: {out}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
