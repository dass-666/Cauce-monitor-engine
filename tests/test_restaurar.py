"""v1.4 (auditoría): restauración segura de un respaldo SQLite.

Defecto reproducido: el README de la v1.3 decía "copia el respaldo como data/monitoreo.db". Si un
cierre brusco había dejado `monitoreo.db-wal`, SQLite volvía a aplicar esas páginas sobre la copia:
la base "restaurada" contenía datos posteriores al respaldo (o quedaba dañada).
"""
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import timedelta

import pytest

from monitoreo import migrate
from monitoreo.config import settings
from monitoreo.db import Database, utcnow
from monitoreo.models import SchedulerLease


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    path = tmp_path / "monitoreo.db"
    url = f"sqlite:///{path.as_posix()}"
    db = Database(url)
    db.migrate(make_backup=False)
    db.engine.dispose()
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE marca(x)")
    c.execute("INSERT INTO marca VALUES ('antes')")
    c.commit()
    c.close()
    return url, path


def _crash_after_writing(path):
    """Otro proceso escribe sin volcar el WAL y muere de golpe (como un corte de luz)."""
    subprocess.run([sys.executable, "-c", f"""
import sqlite3, os
c = sqlite3.connect({str(path)!r}); c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA wal_autocheckpoint=0')
for i in range(300): c.execute("INSERT INTO marca VALUES ('despues')")
c.commit(); os._exit(0)"""], check=True)
    assert os.path.getsize(f"{path}-wal") > 0


def _marks(path):
    c = sqlite3.connect(path)
    try:
        return c.execute("SELECT x, count(*) FROM marca GROUP BY x ORDER BY x").fetchall()
    finally:
        c.close()


def test_copying_the_file_was_unsafe(base):
    """Documenta el problema: la copia simple NO restaura el respaldo."""
    url, path = base
    bk = migrate.backup(url, "prueba")
    _crash_after_writing(path)
    shutil.copy(bk, path)
    assert ("despues", 300) in _marks(path)          # aparecen datos posteriores al respaldo


def test_restore_returns_exactly_the_backup_and_keeps_the_current_base(base):
    url, path = base
    bk = migrate.backup(url, "prueba")
    _crash_after_writing(path)
    rep = migrate.restore(url, bk)
    assert _marks(path) == [("antes", 1)]
    assert rep["revision"] == migrate.head_revision()
    assert ("despues", 300) in _marks(rep["respaldo_previo"])   # lo que había no se pierde


def test_restore_refuses_while_engine_is_running(base):
    url, path = base
    bk = migrate.backup(url, "prueba")
    db = Database(url)
    with db.session() as s:
        now = utcnow()
        s.add(SchedulerLease(name="planificador", holder="equipo:123", acquired_at=now,
                             expires_at=now + timedelta(seconds=15), renewed_at=now, fencing_token=1))
    db.engine.dispose()
    with pytest.raises(migrate.MigrationError, match="en marcha"):
        migrate.restore(url, bk)
    assert len(list((path.parent / "respaldos").glob("*antes-de-restaurar*"))) == 0


def test_restore_rejects_a_damaged_backup(base, tmp_path):
    url, path = base
    bad = tmp_path / "roto.db"
    bad.write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
    with pytest.raises(migrate.MigrationError):
        migrate.restore(url, str(bad))
    assert _marks(path) == [("antes", 1)]             # la base actual no se tocó
