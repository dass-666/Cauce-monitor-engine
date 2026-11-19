"""RC1 · Abrir Cauce (laboratorio, datos FICTICIOS) en la MESA DE CONTROL del administrador.

Una sola instalación y una sola pantalla para todo: mesa de control, importar Excel/CSV, expedientes y
consentimientos, plan, observador, calendario del laboratorio, Ventanilla, historial y folios. Las
herramientas de respaldo, restauración y retención siguen en `python -m monitoreo.migrate` y
`recorrido_integral.bat`.

Arranca en una carpeta propia (no toca `data/`): el portal de laboratorio propio en 127.0.0.1, el motor con
consentimiento exigido, el administrador (vinculado a un operador para poder operar las coincidencias) y un
operador de prueba. Deja en la carpeta una lista de demostración con fechas vigentes
(`clientes_ficticios_rc1.xlsx` y `.csv`).

Uso:
  python scripts/cauce_rc1.py                 (carpeta resultados/cauce_rc1; conserva lo que hiciste)
  python scripts/cauce_rc1.py --nuevo         (borra esa carpeta y empieza de cero)
  python scripts/cauce_rc1.py --abrir         (además abre el navegador)
"""
from __future__ import annotations

import argparse
import signal
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from demo_rc1_datos import generar  # noqa: E402
from ventanilla_laboratorio import Laboratorio  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--carpeta", default=str(ROOT / "resultados" / "cauce_rc1"))
    ap.add_argument("--nuevo", action="store_true", help="borrar la carpeta y empezar de cero")
    ap.add_argument("--abrir", action="store_true", help="abrir el navegador en la mesa de control")
    a = ap.parse_args()
    lab = Laboratorio(Path(a.carpeta), a.nuevo, rc1=True)
    try:
        d = lab.iniciar()
    except Exception as e:  # noqa: BLE001
        lab.detener()
        print(f"No se pudo arrancar Cauce: {e}. Revisa {a.carpeta}/servidor-motor-lab.log y portal.log")
        return 1
    demo = generar(Path(a.carpeta) / "lista_demostracion")
    panel = d["motor"] + "/#panel"
    print("\nCauce Monitor Engine · RC1 · laboratorio (datos FICTICIOS, solo este equipo)\n")
    print(f"  Mesa de control:   {panel}")
    print(f"  Administrador:     {d['admin'][0]} / {d['admin'][1]}")
    print(f"  Operador de apoyo: {d['operador'][0]} / {d['operador'][1]}")
    print(f"  Lista de prueba:   {demo['xlsx']}")
    print(f"                     (mes de la demostración {demo['mes']}; día para abrir en el calendario: {demo['dia']})")
    print(f"  Carpeta:           {d['carpeta']}")
    print("\nLa confirmación de citas es SIMULADA (portal de laboratorio propio). Ctrl+C para detener.\n")
    if a.abrir:
        webbrowser.open(panel)
    parar = {"v": False}
    signal.signal(signal.SIGINT, lambda *_: parar.update(v=True))
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, lambda *_: parar.update(v=True))
    try:
        while not parar["v"]:
            if lab.motor.proc.poll() is not None:
                print("El motor terminó; revisa el registro en la carpeta.")
                break
            time.sleep(0.5)
    finally:
        lab.detener()
    return 0


if __name__ == "__main__":
    sys.exit(main())
