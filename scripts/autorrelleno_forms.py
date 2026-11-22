"""Laboratorio · preparar Forms para que Ventanilla pueda autorrellenar el formulario del portal propio.

Hace dos cosas, sin tocar la base de Monitor ni ninguna otra instalación:

1. Comprueba que Forms (Conector Universal) está instalado en `--forms` (su `.venv` y `cauce_form_connector`).
2. APRUEBA el perfil del formulario de datos del portal de laboratorio (`/cita`) con la correspondencia revisada
   que vive en `monitoreo/autorrelleno/perfil_portal_laboratorio_dia.json` (paso humano del contrato: en este
   laboratorio lo ejecuta quien abre el laboratorio, con la referencia `--aprobado-por`). Forms guarda el
   perfil en `<carpeta>/forms/perfiles` y devuelve su huella SHA-256 y la firma de la estructura; Monitor las
   fija en `<carpeta>/forms/perfil_aprobado.json`. Si alguien cambia el perfil después, Monitor y Forms lo rechazan.

Uso directo (normalmente lo llama `ventanilla_laboratorio.py --forms RUTA`):
  python scripts/autorrelleno_forms.py --forms C:\\cauce-forms --portal http://127.0.0.1:8201 --carpeta resultados\\lab
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAPEO = ROOT / "monitoreo" / "autorrelleno" / "perfil_portal_laboratorio_dia.json"


class FormsNoListo(RuntimeError):
    pass


def python_de(forms: Path) -> Path:
    return forms / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def comprobar_forms(forms: Path, python: Path | None = None) -> Path:
    py = python or python_de(forms)
    if not (forms / "cauce_form_connector" / "integracion.py").is_file():
        raise FormsNoListo(f"No encuentro Forms en {forms} (falta cauce_form_connector). Clona el repositorio "
                           "CAUCE-UNIVERSAL-CONEXION ahí.")
    if not py.is_file():
        raise FormsNoListo(f"Forms no está instalado: falta {py}. Ejecuta instalar_windows.bat dentro de {forms}.")
    return py


def env_forms(forms: Path, perfiles: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MONITOREO_", "PLAYWRIGHT_"))}
    env.update({"CAUCE_PROFILES_DIR": str(perfiles), "CAUCE_HEADLESS": "1", "PYTHONIOENCODING": "utf-8"})
    if (forms / ".pw-browsers").is_dir():
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(forms / ".pw-browsers")
    if os.environ.get("MONITOREO_FORMS_BROWSER_PATH"):
        env["CAUCE_BROWSER_PATH"] = os.environ["MONITOREO_FORMS_BROWSER_PATH"]
    return env


def commit_de(forms: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(forms), "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def aprobar(forms: Path, portal: str, carpeta: Path, aprobado_por: str = "rev_laboratorio",
            python: Path | None = None) -> Path:
    """Aprueba el perfil en Forms y escribe el archivo de Monitor con la huella. Devuelve su ruta."""
    py = comprobar_forms(forms, python)
    mapeo = json.loads(MAPEO.read_text(encoding="utf-8"))
    base = carpeta / "forms"
    perfiles, estado = base / "perfiles", base / "estado"
    perfiles.mkdir(parents=True, exist_ok=True)
    estado.mkdir(parents=True, exist_ok=True)
    campos = base / "campos_aprobados.json"
    campos.write_text(json.dumps(mapeo["campos"], indent=2), encoding="utf-8")
    url = portal.rstrip("/") + mapeo["ruta"]
    r = subprocess.run([str(py), "-m", "cauce_form_connector", "aprobar-perfil", "--url", url,
                        "--perfil", mapeo["perfil"], "--formulario-id", mapeo["formulario_id"],
                        "--aprobado-por", aprobado_por, "--campos", str(campos)],
                       cwd=str(forms), env=env_forms(forms, perfiles), capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise FormsNoListo(f"Forms no pudo aprobar el perfil (código {r.returncode}): {r.stderr.strip()[-400:]}")
    out = json.loads(r.stdout)
    pin = {"version": 1, "formulario_id": mapeo["formulario_id"], "perfil": out["perfil"], "modo": mapeo["modo"],
           "url": url, "perfil_sha256": out["perfil_sha256"], "firma_estructura_esperada": out["firma_estructura_esperada"],
           "campos": sorted(mapeo["campos"]), "aprobado_por": aprobado_por,
           "aprobado_en": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "profiles_dir": str(perfiles), "state_dir": str(estado), "forms_dir": str(forms), "forms_commit": commit_de(forms)}
    destino = base / "perfil_aprobado.json"
    destino.write_text(json.dumps(pin, indent=2, ensure_ascii=False), encoding="utf-8")
    return destino


def variables_motor(forms: Path, pin: Path, python: Path | None = None) -> dict:
    return {"MONITOREO_AUTORRELLENO_LAB": "1", "MONITOREO_AUTORRELLENO_PERFIL": str(pin),
            "MONITOREO_FORMS_DIR": str(forms), "MONITOREO_FORMS_PYTHON": str(python or python_de(forms))}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--forms", required=True, type=Path)
    ap.add_argument("--portal", required=True, help="URL base del portal de laboratorio (http://127.0.0.1:PUERTO)")
    ap.add_argument("--carpeta", required=True, type=Path)
    ap.add_argument("--aprobado-por", default="rev_laboratorio")
    a = ap.parse_args()
    try:
        pin = aprobar(a.forms.resolve(), a.portal, a.carpeta.resolve(), a.aprobado_por)
    except FormsNoListo as e:
        print(e)
        return 1
    print(json.dumps({"perfil_aprobado": str(pin), **variables_motor(a.forms.resolve(), pin)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
