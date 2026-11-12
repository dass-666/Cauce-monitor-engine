"""Actualización de esquema con datos existentes (Fase 1.1, prueba de resistencia).

Toma una COPIA de una base SQLite llena (por ejemplo la que deja
`python scripts/resistencia.py --conservar`), la devuelve al esquema v1.0 (revisión 0001) y
mide la actualización a la versión actual con respaldo previo. Comprueba que:
- las filas y valores de todas las columnas de la v1.0 son idénticos antes y después;
- el respaldo creado antes de migrar pasa `integrity_check` y contiene lo mismo;
- el esquema final coincide con el de los modelos.
La base original nunca se modifica.

Uso:  python scripts/migracion_con_datos.py RUTA_BASE.db [--salida resultados.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MONITOREO_SECRET_KEY", "migracion")

import psutil  # noqa: E402

from monitoreo import migrate  # noqa: E402
from monitoreo.config import settings  # noqa: E402


def v10_digest(path: Path, columns: dict[str, list[str]]) -> dict[str, tuple[int, str]]:
    """Huella (filas, sha256) de cada tabla v1.0 usando SOLO las columnas de la v1.0."""
    c = sqlite3.connect(path)
    try:
        out = {}
        for table, cols in columns.items():
            h = hashlib.sha256()
            n = 0
            for row in c.execute(f'SELECT {", ".join(cols)} FROM "{table}" ORDER BY rowid'):
                h.update(repr(row).encode())
                n += 1
            out[table] = (n, h.hexdigest())
        return out
    finally:
        c.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--salida")
    a = ap.parse_args()
    src = Path(a.base)
    work = Path(tempfile.mkdtemp(prefix="monitoreo_migracion_"))
    db = work / "copia.db"
    shutil.copy2(src, db)
    for ext in ("-wal", "-shm"):
        if Path(str(src) + ext).exists():
            shutil.copy2(str(src) + ext, str(db) + ext)
    settings.data_dir = work
    url = f"sqlite:///{db.as_posix()}"

    t0 = time.perf_counter()
    migrate.downgrade(url, migrate.V1_0_REVISION, make_backup=False)
    t_down = time.perf_counter() - t0
    c = sqlite3.connect(db)
    columns = {t: [r[1] for r in c.execute(f'PRAGMA table_info("{t}")')] for t in sorted(migrate.V1_0_TABLES)}
    c.close()
    before = v10_digest(db, columns)
    size_mb = db.stat().st_size / 1048576

    proc = psutil.Process()
    cpu0 = proc.cpu_times()
    t0 = time.perf_counter()
    report = migrate.upgrade(url, make_backup=True)
    t_up = time.perf_counter() - t0
    cpu1 = proc.cpu_times()
    after = v10_digest(db, columns)
    backup = Path(report.backup)
    backup_digest = v10_digest(backup, columns)

    from sqlalchemy import create_engine
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext
    from monitoreo.db import Base
    from monitoreo import models  # noqa: F401
    eng = create_engine(url)
    with eng.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    eng.dispose()

    res = {
        "aviso": "Medición real en esta máquina sobre una copia de una base con datos simulados.",
        "base_origen": src.name, "tamano_mb": round(size_mb, 1),
        "filas_v1_0": {t: n for t, (n, _) in before.items() if n},
        "revertir_a_v1_0_s": round(t_down, 2),
        "actualizar_a_v1_1_s_incluye_respaldo": round(t_up, 2),
        "cpu_actualizacion_s": round((cpu1.user - cpu0.user) + (cpu1.system - cpu0.system), 2),
        "pasos": report.steps, "revision_final": report.after,
        "datos_v1_0_identicos_tras_actualizar": before == after,
        "tablas_con_diferencias": [t for t in before if before[t] != after[t]],
        "respaldo_identico_al_original": backup_digest == before,
        "esquema_final_igual_a_modelos": not diff, "diferencias_esquema": [str(d) for d in diff][:5],
    }
    text = json.dumps(res, ensure_ascii=False, indent=2)
    print(text)
    if a.salida:
        Path(a.salida).write_text(text, encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    ok = res["datos_v1_0_identicos_tras_actualizar"] and res["respaldo_identico_al_original"] \
        and res["esquema_final_igual_a_modelos"]
    print("\nRESULTADO:", "CORRECTO" if ok else "HAY DIFERENCIAS")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
