"""Validación física del autorrellenado Ventanilla -> Forms en la MSI (Windows 11). Un solo paso, sin preguntas.

Lo lanza `validar_autorrelleno_windows.bat`. Usa carpetas temporales nuevas y datos FICTICIOS; no toca data/, ni
C:\\cauce-msi, ni D:\\cauce-demo-8328e2c, ni bases o respaldos. Ejecuta, en orden:

  0. instalación: Python de Monitor, Forms en ..\\cauce-forms con su .venv y su commit (se espera aa1bb02)
  1. pruebas automáticas (pytest) del adaptador y del lanzador, con Forms REAL y VENTANA VISIBLE:
     8 campos y 2 confirmaciones, revocación a mitad (Forms y Chromium terminan), cierre de la ventana con la X
     (WM_CLOSE) -> PREPARADO, Monitor detenido con la ventana abierta, Monitor muerto de golpe (Job Object)
  2. recorrido por la interfaz (clics) con la ventana de Forms visible: 18+ comprobaciones, incluida la búsqueda
     de los datos ficticios en todos los registros
  3. reinicio de Monitor con la ventana abierta (parada ordenada y forzada): 0 procesos de Forms/Chromium

Deja todo en resultados\\validacion_autorrelleno\\ y un RESUMEN.txt para enviar (solo códigos y conteos).
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORMS_ESPERADO = "aa1bb02"


def main() -> int:
    forms = Path(os.environ.get("CAUCE_FORMS_DIR") or ROOT.parent / "cauce-forms").resolve()
    out = ROOT / "resultados" / "validacion_autorrelleno"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    lineas: list[str] = []
    pasos: list[dict] = []

    def anotar(nombre: str, ok: bool | None, detalle: str = "") -> None:
        marca = {True: "OK   ", False: "FALLA", None: "OMITE"}[ok]
        lineas.append(f"{marca} {nombre}" + (f"  ({detalle})" if detalle else ""))
        pasos.append({"paso": nombre, "ok": ok, "detalle": detalle})
        print(lineas[-1], flush=True)

    def correr(nombre: str, cmd: list[str], log: str, env: dict | None = None, timeout: int = 1800) -> int:
        print(f"\n--- {nombre} (puede tardar unos minutos; se abrirán y cerrarán ventanas de Chromium) ---", flush=True)
        t0 = time.monotonic()
        with open(out / log, "w", encoding="utf-8", errors="replace") as f:
            try:
                p = subprocess.run(cmd, cwd=ROOT, env=env, stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
                rc = p.returncode
            except subprocess.TimeoutExpired:
                rc = -1
        estado = None if rc == 3 else rc == 0           # 3 = falta una herramienta (Node/Playwright): no es un fallo
        anotar(nombre, estado, f"código {rc}{' (falta Node.js o Playwright)' if rc == 3 else ''}, "
                               f"{round(time.monotonic() - t0)} s, detalle en {log}")
        return rc

    py = sys.executable
    lineas += [f"Validación autorrelleno Ventanilla -> Forms · {time.strftime('%Y-%m-%d %H:%M:%S')}",
               f"Equipo: {platform.platform()} · Python {platform.python_version()}", ""]
    # 0. instalación
    rama = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    anotar("Monitor instalado", (ROOT / "monitoreo").exists(), f"rama {rama or '?'} @ {commit or '?'}")
    forms_py = forms / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    ok_forms = forms_py.exists() and (forms / "cauce_form_connector").exists()
    fc = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=forms, capture_output=True, text=True).stdout.strip() if forms.exists() else ""
    anotar("Forms instalado con su .venv", ok_forms, f"{forms} @ {fc or '?'}")
    if fc and not fc.startswith(FORMS_ESPERADO[:7]):
        anotar(f"Forms en el commit esperado {FORMS_ESPERADO}", False, f"está en {fc}")
    if not ok_forms:
        lineas.append("\nFalta Forms: ejecuta en ..\\cauce-forms  git checkout aa1bb02  y  instalar_windows.bat")
        (out / "RESUMEN.txt").write_text("\n".join(lineas) + "\n", "utf-8")
        return 3
    node = shutil.which("node")
    anotar("Node.js para el recorrido por la interfaz", bool(node), node or "no encontrado: el paso 2 se omitirá")

    env = dict(os.environ, FORMS_REPO=str(forms), FORMS_HEADLESS="0", PYTHONUTF8="1", FORMS_ESPERA_RELLENO_S="25")
    # 1. pruebas automáticas
    correr("1. Pruebas automáticas con Forms real y ventana visible (pytest)",
           [py, "-m", "pytest", "-q", "-rA", "--tb=line", "-p", "no:cacheprovider",
            "tests/test_autorrelleno_forms.py", "tests/test_lanzador_lab.py"], "1_pytest.log", env)
    txt = (out / "1_pytest.log").read_text("utf-8", errors="replace")
    for prueba in ("test_forms_real_rellena_el_formulario_de_laboratorio",
                   "test_forms_real_revocacion_a_mitad_termina_chromium",
                   "test_forms_real_cerrar_la_ventana_a_mano_da_preparado",
                   "test_detener_monitor_con_la_ventana_abierta_termina_forms",
                   "test_si_monitor_muere_de_golpe_el_conector_no_queda_huerfano"):
        if f"PASSED tests/test_autorrelleno_forms.py::{prueba}" in txt or f"PASSED tests/test_lanzador_lab.py::{prueba}" in txt:
            anotar(f"   {prueba}", True)
        elif prueba in txt and "SKIPPED" in txt.split(prueba)[0][-200:]:
            anotar(f"   {prueba}", None, "omitida")
        else:
            anotar(f"   {prueba}", False, "ver 1_pytest.log")
    # 2. recorrido por la interfaz
    if node:
        correr("2. Recorrido por la interfaz con la ventana de Forms visible",
               [py, "scripts/recorrido_autorrelleno.py", "--forms", str(forms), "--con-ventana",
                "--salida", str(out / "2_recorrido")], "2_recorrido.log")
    else:
        anotar("2. Recorrido por la interfaz", None, "falta Node.js")
    # 3. reinicio de Monitor con la ventana abierta
    correr("3. Reiniciar Monitor con la ventana de Forms abierta (ordenado y forzado)",
           [py, "scripts/prueba_reinicio_autorrelleno.py", "--forms", str(forms), "--con-ventana",
            "--salida", str(out / "3_reinicio")], "3_reinicio.log")

    ok = all(p["ok"] is not False for p in pasos)
    lineas += ["", "RESULTADO: " + ("TODO OK" if ok else "HAY FALLOS (ver los .log de esta carpeta)"), "",
               "Falta solo una comprobación A MANO (2 minutos):",
               "  1. Doble clic en autorrelleno_laboratorio.bat; entra como operador1 / Operador-Laboratorio-2026.",
               "  2. Ventanilla -> Nueva atención -> persona ficticia CON consentimiento -> «Autorrellenar formulario».",
               "  3. Cuando Forms termine de escribir, cierra TÚ la ventana de Chromium con la X.",
               "  4. Ventanilla debe decir PREPARADO (no CADUCADA). Anótalo debajo y envía esta carpeta."]
    (out / "RESUMEN.txt").write_text("\n".join(lineas) + "\n", "utf-8")
    (out / "resumen.json").write_text(json.dumps({"ok": ok, "pasos": pasos}, indent=2, ensure_ascii=False), "utf-8")
    print("\n" + "\n".join(lineas[-7:]))
    print(f"\nResumen: {out / 'RESUMEN.txt'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
