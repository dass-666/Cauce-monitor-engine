"""Recuperación segura DESDE LA INTERFAZ: conciliación y desbloqueo por un administrador con clics.

Reutiliza el escenario de `recorrido_integral.py` sin cambiarlo (laboratorio propio en 127.0.0.1, datos
ficticios): respaldo -> cambios posteriores (B confirmada en el portal, G revocada) -> restauración. En el punto
en que el recorrido integral concilia por API, aquí lo hace una persona en la pantalla «Recuperación»
(`recorrido_recuperacion_ui.js`, Chromium):
  - la Mesa de control muestra «Recuperación segura: BLOQUEADA»;
  - la pantalla lista los motivos y las 2 discrepancias; «Levantar el bloqueo» está desactivado;
  - un folio que no es el del diario se rechaza y el error se ve en la pantalla;
  - con el folio que se lee en el portal (SIMULADO: lo lee el script del control del laboratorio) y la
    revocación reaplicada, quedan 0 pendientes; se levanta el bloqueo con motivo;
  - el historial de auditoría de la pantalla muestra las 3 acciones.
Después se comprueba POR API lo que la pantalla dijo, y el recorrido integral sigue igual (continuidad,
retención, nada fuera del laboratorio).

Uso: python scripts/recorrido_recuperacion_ui.py [--salida resultados/recorrido_recuperacion_ui] [--manual]
  --manual: tras restaurar, deja el laboratorio BLOQUEADO, imprime la dirección y el folio del portal y espera a
            que una persona concilie en la pantalla «Recuperación»; luego comprueba lo mismo por API.
Código 0 si todo pasa, 1 si algo falla, 3 si falta Node.js/Playwright.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from recorrido_integral import Recorrido  # noqa: E402
from ventanilla_laboratorio import ADMIN_PWD  # noqa: E402

from monitoreo import navegador  # noqa: E402


class RecorridoUI(Recorrido):
    capturas: Path
    manual = False

    def recuperacion(self, rid_b: int, folio2: str | None, f2: str) -> None:
        ck = self.check
        rec = self.adm.get("/api/recuperacion").json()
        disc = {d["tipo"]: d for d in rec.get("discrepancias", [])}
        ck("UI · escenario: tras restaurar hay bloqueo con las 2 discrepancias esperadas",
           rec.get("bloqueado") and set(disc) == {"CONFIRMACION_AUSENTE", "REVOCACION_AUSENTE"},
           {"motivos": [m["codigo"] for m in rec.get("motivos", [])], "tipos": sorted(disc)})
        citas = [x.get("folio") for x in self.portal_reservas() if x.get("estado") == "CONFIRMADA" and x.get("fecha") == f2]
        if self.manual:
            self._esperar_persona(rec, citas)
        else:
            self._navegador(citas)
        self._comprobar(citas, folio2)

    def _esperar_persona(self, rec: dict, citas: list) -> None:
        """Modo --manual: el laboratorio queda en marcha, BLOQUEADO, y una persona concilia en la pantalla."""
        print("\n" + "=" * 78)
        print("  RESTAURACIÓN HECHA: Cauce está BLOQUEADO con discrepancias (datos ficticios).")
        print(f"  Abre en el navegador:  {self.lab.motor.base}/#recuperacion")
        print(f"  Usuario: admin   Contraseña: {ADMIN_PWD}   (solo laboratorio)")
        print(f"  Discrepancias: {', '.join(d['tipo'] for d in rec.get('discrepancias', []))}")
        print(f"  Folio que muestra el portal de laboratorio (SIMULADO): {citas[0] if citas else '¿?'}")
        print("  Sugerencia: prueba primero un folio falso (p. ej. F-QUE-NO-ES): debe rechazarse.")
        print("  Resuelve las dos discrepancias, levanta el bloqueo y vuelve aquí.")
        print("=" * 78)
        input("\nPulsa Enter cuando la pantalla diga «Operable» (o para comprobar cómo quedó)... ")

    def _navegador(self, citas: list) -> None:
        ck = self.check
        nodo = shutil.which("node")
        if not ck("UI · Node.js disponible para el navegador automatizado", bool(nodo), None):
            return
        entrada = {"admin": ["admin", ADMIN_PWD], "folio_portal": citas[0] if citas else "",
                   "folio_falso": "F-QUE-NO-ES", "exp_b": self.e["B"]["codigo"], "exp_g": self.e["G"]["codigo"]}
        f_in = self.lab.carpeta / "entrada_ui.json"
        f_in.write_text(json.dumps(entrada), "utf-8")
        p = subprocess.run([nodo, str(ROOT / "scripts" / "recorrido_recuperacion_ui.js"), self.lab.motor.base,
                            str(self.capturas), str(f_in)], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600, env=navegador.entorno(), cwd=str(ROOT / "scripts"))
        linea = next((x for x in p.stdout.splitlines() if x.startswith("RESULTADO ")), None)
        if linea is None:
            ck("UI · el navegador terminó", False, (p.stdout + p.stderr)[-800:])
            return
        r = json.loads(linea[len("RESULTADO "):])
        self.res["interfaz"] = r
        for x in r["pasos"]:
            ck("UI · " + x["paso"], x["ok"], x.get("detalle"))
        ck("UI · sin errores de JavaScript en la página", not r["errores_js"], r["errores_js"][:5])

    def _comprobar(self, citas: list, folio2) -> None:
        """Lo que la pantalla dijo (o lo que hizo la persona), comprobado por API."""
        ck = self.check
        self.conectar()
        rec = self.adm.get("/api/recuperacion").json()
        ck("UI → API · el bloqueo quedó levantado y sin pendientes", not rec.get("bloqueado") and rec.get("pendientes") == 0,
           {k: rec.get(k) for k in ("bloqueado", "pendientes", "motivos")})
        aud = [a["accion"] for a in self.adm.get("/api/audit", params={"limit": 200}).json()]
        ck("UI → API · auditoría: 2 conciliaciones y 1 desbloqueo",
           aud.count("recuperacion_conciliada") == 2 and aud.count("recuperacion_levantada") == 1,
           {"conciliadas": aud.count("recuperacion_conciliada"), "levantadas": aud.count("recuperacion_levantada")})
        cg = self.adm.get(f"/api/expedientes/{self.e['G']['id']}/consentimientos").json()["consentimientos"]
        ck("UI → API · G tiene su consentimiento REVOCADO otra vez", cg[-1]["estado"] == "REVOCADO", cg[-1]["estado"])
        tb = self.traza(self.e["B"])
        ck("UI → API · B CONFIRMADA con el folio del portal (sin repetir la confirmación)",
           any(x.get("estado") == "CONFIRMADA" and x.get("folio") == folio2 for x in tb.get("reservaciones", []))
           and len(citas) == 1, {"folio_portal": citas, "f2": folio2})


def main() -> int:
    # Windows con la salida redirigida (CI, «> archivo») usa cp1252: sin esto «→» detenía el recorrido (CI 104).
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida", default=str(ROOT / "resultados" / "recorrido_recuperacion_ui"))
    ap.add_argument("--manual", action="store_true",
                    help="deja el laboratorio bloqueado y espera a que una persona concilie en la pantalla")
    a = ap.parse_args()
    out = Path(a.salida).resolve()
    capt = out / "capturas"
    if capt.exists():
        shutil.rmtree(capt)
    capt.mkdir(parents=True, exist_ok=True)
    if not a.manual and not shutil.which("node"):
        print("Falta Node.js (https://nodejs.org) para el navegador automatizado.")
        return 3
    tmp = Path(tempfile.mkdtemp(prefix="recuperacion-ui-"))
    rec = RecorridoUI(tmp / "lab")
    rec.capturas = capt
    rec.manual = a.manual
    rec.res["version"] = "recuperacion-ui"
    try:
        rec.run()
    except Exception as e:  # noqa: BLE001
        rec.check("el recorrido se detuvo por un error", False, f"{type(e).__name__}: {str(e)[:300]}")
    finally:
        rec.lab.detener()
        rec.res["comprobaciones"] = rec.ck
        rec.res["aprobadas"], rec.res["total"] = sum(x["ok"] for x in rec.ck), len(rec.ck)
        (out / "recorrido_recuperacion_ui.json").write_text(json.dumps(rec.res, ensure_ascii=False, indent=2), "utf-8")
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nRESULTADO: {rec.res['aprobadas']}/{rec.res['total']} comprobaciones · evidencia en "
          f"{out / 'recorrido_recuperacion_ui.json'}")
    return 0 if rec.ck and all(x["ok"] for x in rec.ck) else 1


if __name__ == "__main__":
    sys.exit(main())
