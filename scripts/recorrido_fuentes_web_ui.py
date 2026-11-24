"""Laboratorio · «Páginas observadas»: configurar una página a observar SOLO con la pantalla del administrador.

1. Pone el motor de laboratorio (carpeta temporal nueva) y el sitio de turnos local `laboratorio_web` (127.0.0.1).
2. Fase «configurar» (navegador automatizado): el administrador escribe el perfil con dos errores a propósito, la vista
   previa enseña qué no reconoce y por qué, lo corrige, vuelve a probar y guarda. El observador queda mirando.
3. El sitio publica un turno nuevo: el motor lo registra con evidencia (sin tocar la pantalla).
4. Fase «detener»: detiene la observación desde la lista.
5. Fase «captcha»: el sitio pide un CAPTCHA; la vista previa lo dice y no deja guardar.
6. El sitio no recibió ningún envío.

Uso: python scripts/recorrido_fuentes_web_ui.py [--salida DIR]      Código 0 si todo pasa, 1 si algo falla, 3 sin Node.
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
from datetime import date, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from laboratorio_web.servidor import PERFIL_TURNOS, SitioTurnos  # noqa: E402
from ventanilla_laboratorio import ADMIN_PWD, Laboratorio  # noqa: E402

from monitoreo import navegador  # noqa: E402


def _dmy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida", default=str(ROOT / "resultados" / "recorrido_fuentes_web_ui"))
    a = ap.parse_args()
    out = Path(a.salida)
    out.mkdir(parents=True, exist_ok=True)
    if not shutil.which("node"):
        print("Falta Node.js para el navegador automatizado.")
        return 3
    tmp = Path(tempfile.mkdtemp(prefix="fuentes-web-ui-"))
    lab = Laboratorio(tmp / "lab", nuevo=True)
    sitio = SitioTurnos().iniciar()
    hoy = date.today()
    turno = lambda lugar, dias, estado="Disponible", cupo=2: {"lugar": lugar, "fecha": _dmy(hoy + timedelta(days=dias)),  # noqa: E731
                                                              "hora": "09:30", "estado": estado, "cupo": cupo}
    sitio.poner(turnos=[turno("Oficina Norte Centro", 30), turno("Oficina Norte Poniente", 32),
                        turno("Oficina Norte Centro", 33, "Sin lugares", 0)])
    ck: list[dict] = []
    res: dict = {"recorrido": "fuentes_web_interfaz_laboratorio", "plataforma": platform.platform(),
                 "python": platform.python_version(), "inicio": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "datos": "ficticios; motor y sitio de turnos locales en 127.0.0.1"}

    def check(nombre, ok, detalle=None):
        ck.append({"comprobacion": nombre, "ok": bool(ok), "detalle": detalle})
        print(("OK   " if ok else "FALLA") + " " + nombre + ("" if ok else f"  {detalle}"))
        return ok

    def fase(nombre, d):
        entrada = {"admin": ADMIN_PWD, "nombre": "turnos-lab", "url": sitio.url, "tramite": "alta", "perfil": PERFIL_TURNOS}
        (tmp / "entrada.json").write_text(json.dumps(entrada), "utf-8")
        p = subprocess.run([shutil.which("node"), str(ROOT / "scripts" / "recorrido_fuentes_web_ui.js"), nombre, d["motor"],
                            str(out / "capturas"), str(tmp / "entrada.json")], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600, env=navegador.entorno(), cwd=str(ROOT / "scripts"))
        linea = next((x for x in p.stdout.splitlines() if x.startswith("RESULTADO ")), None)
        if linea is None:
            check(f"interfaz · {nombre}: el navegador automatizado se ejecutó", False, (p.stderr or p.stdout)[-400:])
            return None
        nav = json.loads(linea[len("RESULTADO "):])
        res.setdefault("interfaz", {})[nombre] = nav
        for x in nav["pasos"]:
            check(f"interfaz · {nombre} · {x['paso']}", x["ok"], x["detalle"])
        check(f"interfaz · {nombre}: sin interrupciones", not nav.get("fallo"), nav.get("fallo"))
        return nav

    try:
        d = lab.iniciar()
        adm = httpx.Client(base_url=d["motor"], timeout=30, trust_env=False)
        adm.headers["Authorization"] = "Bearer " + adm.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PWD}).json()["token"]

        if not fase("configurar", d):
            return 1
        fs = adm.get("/api/fuentes-web").json()["fuentes"]
        guardada = next((f for f in fs if f["nombre"] == "turnos-lab"), None)
        check("lo guardado es el perfil probado (tabla de ubicaciones y textos corregidos)",
              guardada and guardada["perfil"]["ubicaciones"] == PERFIL_TURNOS["ubicaciones"]
              and guardada["perfil"]["estado"]["no_disponible"] == ["Sin lugares", "Agotado"], guardada and guardada["perfil"])

        # el sitio publica un turno nuevo: el observador que configuró la pantalla lo registra con evidencia
        nueva = (hoy + timedelta(days=35)).isoformat()
        sitio.poner(turnos=sitio.estado["turnos"] + [turno("Oficina Norte Poniente", 35)])

        def evento():
            return next((e for e in adm.get("/api/observaciones").json()["eventos"]
                         if e["tipo"] == "DIA_DISPONIBLE" and e["fecha"] == nueva), None)
        t = time.perf_counter()
        ev = None
        fin = time.monotonic() + 30
        while ev is None and time.monotonic() < fin:
            ev = evento()
            time.sleep(0.3)
        res["publicacion_a_registro_s"] = round(time.perf_counter() - t, 2)
        evid = (ev or {}).get("evidencia") or {}
        check("el turno nuevo quedó registrado con fuente, sucursal y huella SHA-256",
              ev and ev["observador"] == "web-turnos-lab" and ev["sucursal"] == "NOR-2" and len(evid.get("sha256", "")) == 64,
              ev)

        fase("detener", d)
        fs = adm.get("/api/fuentes-web").json()["fuentes"]
        check("la fuente quedó detenida en el motor", fs and not fs[0]["activo"], fs and fs[0].get("estado"))

        sitio.poner(captcha=True)
        fase("captcha", d)
        fs = adm.get("/api/fuentes-web").json()["fuentes"]
        check("con CAPTCHA no se guardó ninguna fuente nueva", len(fs) == 1, [f["nombre"] for f in fs])
        check("el sitio no recibió ningún envío (observar nunca envía)", sitio.envios == 0, sitio.envios)
    except Exception as e:  # noqa: BLE001
        check("el recorrido se detuvo por un error", False, f"{type(e).__name__}: {str(e)[:300]}")
    finally:
        lab.detener()
        sitio.detener()
        res["comprobaciones"] = ck
        res["ok"] = bool(ck) and all(x["ok"] for x in ck)
        res["fin"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        (out / "recorrido_fuentes_web_ui.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), "utf-8")
        print(f"\n{sum(x['ok'] for x in ck)}/{len(ck)} comprobaciones. Evidencia: {out}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
