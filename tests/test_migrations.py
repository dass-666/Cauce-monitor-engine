"""Migraciones: base nueva, actualización desde una base real creada con la v1.0, respaldo,
reversión, concurrencia y protección ante bases desconocidas."""
import hashlib
import shutil
import sqlite3
import threading
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from monitoreo import migrate
from monitoreo.config import settings
from monitoreo.db import Base, Database

from .conftest import PG_URL

FIX = Path(__file__).parent / "fixtures"
V10_TABLES = sorted(migrate.V1_0_TABLES)


def _digest_sqlite(path, tables=V10_TABLES):
    c = sqlite3.connect(path)
    out = {}
    for t in tables:
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
        v10cols = [x for x in cols if x not in ("status_reason", "status_changed_at", "last_outcome", "meta", "claimed_at", "claim_token")]
        rows = c.execute(f"SELECT {','.join(v10cols)} FROM {t} ORDER BY 1").fetchall()
        out[t] = (len(rows), hashlib.sha256(repr(rows).encode()).hexdigest())
    c.close()
    return out


@pytest.fixture
def v10_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    dst = tmp_path / "v10.db"
    shutil.copy(FIX / "bd_v1_0.sqlite", dst)
    return dst


def test_fresh_database_matches_models(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    url = f"sqlite:///{(tmp_path / 'n.db').as_posix()}"
    rep = migrate.upgrade(url)
    assert rep.after == migrate.head_revision() and rep.backup is None   # base vacía: sin respaldo
    eng = create_engine(url)
    with eng.connect() as conn:
        diffs = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diffs == [], diffs


def test_revision_0001_equals_real_v1_0_schema(tmp_path, monkeypatch):
    """El esquema que genera la migración 0001 es idéntico al que creó la v1.0 real."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    url = f"sqlite:///{(tmp_path / 'a.db').as_posix()}"
    migrate.upgrade(url, target=migrate.V1_0_REVISION)

    def shape(u):
        i = inspect(create_engine(u))
        return {t: (sorted((c["name"], str(c["type"]), c["nullable"]) for c in i.get_columns(t)),
                    sorted((x["name"], tuple(x["column_names"])) for x in i.get_indexes(t)),
                    sorted(tuple(x["column_names"]) for x in i.get_unique_constraints(t)))
                for t in V10_TABLES}
    assert shape(url) == shape(f"sqlite:///{(FIX / 'bd_v1_0.sqlite').as_posix()}")


def test_upgrade_real_v1_0_database_preserves_data(v10_copy):
    url = f"sqlite:///{v10_copy.as_posix()}"
    before = _digest_sqlite(v10_copy)
    assert before["requests"][0] == 40 and before["assignments"][0] > 0
    state = migrate.inspect_state(url)
    assert state["base_v1_0_sin_versionar"] and state["revision_actual"] is None
    rep = migrate.upgrade(url)
    assert rep.legacy_detected and rep.after == migrate.head_revision()
    assert Path(rep.backup).exists()
    assert _digest_sqlite(v10_copy) == before                       # mismos datos, fila a fila
    assert _digest_sqlite(rep.backup) == before                     # el respaldo es la base original
    with sqlite3.connect(rep.backup) as c:
        assert not c.execute("SELECT name FROM sqlite_master WHERE name='alembic_version'").fetchall()
    # la aplicación v1.1 funciona sobre la base migrada
    from monitoreo.engine.clock import RealClock
    from monitoreo.engine.integrity import check
    from monitoreo.engine.pipeline import maintenance, process_pending_events
    db = Database(url)
    maintenance(db, RealClock())
    process_pending_events(db, RealClock())
    with db.session() as s:
        assert check(s) == []
    db.engine.dispose()
    # segunda ejecución: no hay nada que hacer, no se crea otro respaldo
    rep2 = migrate.upgrade(url)
    assert rep2.backup is None and rep2.steps == []


def test_downgrade_then_upgrade_keeps_v1_0_data(v10_copy):
    url = f"sqlite:///{v10_copy.as_posix()}"
    migrate.upgrade(url)
    before = _digest_sqlite(v10_copy)
    rep = migrate.downgrade(url, migrate.V1_0_REVISION)
    assert rep.after == migrate.V1_0_REVISION and Path(rep.backup).exists()
    tables = set(inspect(create_engine(url)).get_table_names())
    assert "poll_stats_hourly" not in tables and "scheduler_leases" not in tables
    assert _digest_sqlite(v10_copy) == before
    migrate.upgrade(url)
    assert _digest_sqlite(v10_copy) == before


def test_unknown_database_is_not_touched(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    p = tmp_path / "otra.db"
    with sqlite3.connect(p) as c:
        c.execute("CREATE TABLE clientes (id INTEGER PRIMARY KEY, nombre TEXT)")
        c.execute("INSERT INTO clientes VALUES (1, 'x')")
    raw = p.read_bytes()
    with pytest.raises(migrate.MigrationError):
        migrate.upgrade(f"sqlite:///{p.as_posix()}")
    assert p.read_bytes() == raw


def test_concurrent_startups_migrate_once(v10_copy):
    url = f"sqlite:///{v10_copy.as_posix()}"
    errors, reports = [], []
    barrier = threading.Barrier(3)

    def w():
        barrier.wait()
        try:
            reports.append(migrate.upgrade(url))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=w) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors, errors
    assert {r.after for r in reports} == {migrate.head_revision()}


@pytest.mark.skipif(not PG_URL, reason="requiere MONITOREO_TEST_PG")
def test_postgres_upgrade_from_v1_0_dump_and_downgrade(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    db = Database(PG_URL)
    db.reset_for_tests()
    sql = (FIX / "bd_v1_0_postgres.sql").read_text(encoding="utf-8")
    sql = "\n".join(l for l in sql.splitlines() if not l.startswith("\\"))  # metacomandos de psql
    raw = db.engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute(sql)
        raw.commit()
    finally:
        raw.close()
    db.engine.dispose()  # el volcado cambia search_path en esa conexión: no reutilizarla

    def counts():
        with db.engine.connect() as c:
            return {t: c.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() for t in V10_TABLES}
    before = counts()
    assert before["requests"] == 40
    rep = migrate.upgrade(PG_URL)
    assert rep.legacy_detected and rep.after == migrate.head_revision()
    assert rep.backup == "omitido" or Path(rep.backup).stat().st_size > 0
    assert counts() == before
    with db.engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
    migrate.downgrade(PG_URL, migrate.V1_0_REVISION)
    assert counts() == before
    migrate.upgrade(PG_URL)
    db.reset_for_tests()
    db.engine.dispose()
