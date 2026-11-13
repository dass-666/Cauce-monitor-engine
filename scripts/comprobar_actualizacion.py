"""Comprobaciones de la actualización entre versiones (las usa la integración continua, v1.4).

    python scripts/comprobar_actualizacion.py cuentas <bd.sqlite>          -> "solicitudes en_proceso"
    python scripts/comprobar_actualizacion.py verificar <bd.sqlite> "<cuentas de antes>"
`verificar` exige el esquema de esta versión, las mismas cuentas y la integridad correcta.
Solo lee la base; no modifica nada.
"""
import sqlite3
import sys
from pathlib import Path


def counts(path: str) -> str:
    c = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    try:
        n = c.execute("SELECT count(*) FROM requests").fetchone()[0]
        p = c.execute("SELECT count(*) FROM assignments WHERE status = 'EN_PROCESO'").fetchone()[0]
        return f"{n} {p}"
    finally:
        c.close()


def verify(path: str, before: str) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from monitoreo.db import Database
    from monitoreo.engine.integrity import check
    from monitoreo.migrate import head_revision, inspect_state
    url = f"sqlite:///{Path(path).resolve().as_posix()}"
    rev, now = inspect_state(url)["revision_actual"], counts(path)
    db = Database(url)
    with db.session() as s:
        problems = check(s)
    db.engine.dispose()
    print(f"esquema {rev} (esperado {head_revision()}) · solicitudes/en_proceso antes {before.strip()} ahora {now}"
          f" · integridad {problems or 'correcta'}")
    return 0 if rev == head_revision() and now == before.strip() and not problems else 1


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "cuentas":
        print(counts(sys.argv[2]))
    elif len(sys.argv) == 4 and sys.argv[1] == "verificar":
        raise SystemExit(verify(sys.argv[2], sys.argv[3]))
    else:
        print(__doc__)
        raise SystemExit(2)
