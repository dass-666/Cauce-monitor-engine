"""Muestra registros previos FICTICIOS del portal de prueba para probar la captura a mano (v1.5).

Lee data/portal/estado.json (lo crea el portal la primera vez que arranca). No cambia nada.
Uso:  .venv\\Scripts\\python scripts\\registros_portal.py [cantidad]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
archivo = ROOT / "data" / "portal" / "estado.json"
if not archivo.exists():
    sys.exit("Todavía no existe data/portal/estado.json: arranca antes el portal (iniciar_portal.bat).")
n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
regs = json.loads(archivo.read_text(encoding="utf-8"))["registros"]
print("Registros previos ficticios del portal de prueba (identificador, fecha de nacimiento, trámite):")
for k in sorted(regs)[:n]:
    print(f"  {k}   {regs[k]['fecha_nacimiento']}   {regs[k]['tramite']}")
