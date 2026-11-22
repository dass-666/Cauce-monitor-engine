"""Conector de PRUEBA que hace de Forms: guarda lo que recibe y responde con el simulador del contrato.

Uso (solo pruebas): python forms_espia.py <carpeta> [modo] [demora_s]
- <carpeta>/solicitud.json: la solicitud tal como llegó por stdin (datos FICTICIOS de la prueba).
- <carpeta>/entorno.json: los NOMBRES de las variables de entorno recibidas (no sus valores).
- <carpeta>/pid: el pid, para comprobar que Monitor lo terminó.
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from monitoreo.conector import simulador  # noqa: E402

carpeta = Path(sys.argv[1])
modo = sys.argv[2] if len(sys.argv) > 2 else "ok"
demora = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
carpeta.mkdir(parents=True, exist_ok=True)
(carpeta / "pid").write_text(str(os.getpid()))
texto = sys.stdin.read()
(carpeta / "solicitud.json").write_text(texto, encoding="utf-8")
(carpeta / "entorno.json").write_text(json.dumps(sorted(os.environ)), encoding="utf-8")
sol = json.loads(texto)
fin = time.monotonic() + demora
while time.monotonic() < fin:
    time.sleep(0.05)
resp, salida = simulador.responder(sol, modo)
sys.stdout.write(json.dumps(resp, ensure_ascii=False))
sys.exit(salida)
