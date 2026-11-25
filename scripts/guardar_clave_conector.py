"""Guarda la clave de la cuenta del conector cifrada con DPAPI de la cuenta de Windows que ejecuta Monitor.

Solo LABORATORIO (docs/PLAN_AISLAMIENTO_FORMS_WINDOWS.md, fase F3). El archivo resultante solo lo puede abrir la misma
cuenta de Windows en el mismo equipo; Monitor lo lee con MONITOREO_FORMS_CLAVE_DPAPI. La clave NUNCA va en el .env.
La clave se pide sin mostrarla (getpass) y no se imprime. El archivo se crea en exclusiva: no sustituye uno existente.

Uso (en la cuenta de Windows que ejecuta Monitor):
    python scripts\\guardar_clave_conector.py <archivo de salida, p. ej. datos\\claves\\conector.dpapi>
"""
from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    if sys.platform != "win32":
        print("Solo Windows (DPAPI). En Linux el laboratorio aislado no usa clave.", file=sys.stderr)
        return 2
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    from monitoreo.autorrelleno import aislamiento_lab as al
    destino = Path(sys.argv[1])
    clave = getpass.getpass("Clave de la cuenta del conector: ")
    if not clave or clave != getpass.getpass("Repítela: "):
        print("Las claves no coinciden o están vacías: no se guardó nada.", file=sys.stderr)
        return 1
    destino.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(destino, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    except FileExistsError:
        print(f"{destino} ya existe: bórralo a mano si quieres sustituirlo.", file=sys.stderr)
        return 1
    with os.fdopen(fd, "wb") as f:
        f.write(al.proteger_clave(clave))
    print(f"Guardada en {destino}. Define MONITOREO_FORMS_CLAVE_DPAPI={destino} solo en el laboratorio aislado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
