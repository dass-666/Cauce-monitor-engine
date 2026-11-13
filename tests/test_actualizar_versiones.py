"""v1.4: actualización desde bases reales de la v1.1, v1.2 y v1.3.

Las bases de `tests/fixtures/bd_v1_X.sqlite` las creó el código de cada versión (ramas fase-1.X)
con datos ficticios: 70 solicitudes, consultas, eventos, alertas y asignaciones ACTIVA y
EN_PROCESO. Se comprueba que la actualización conserva cada fila, que la integridad sigue bien
y que el motor nuevo trabaja con ellas (sin liberar las EN_PROCESO antiguas).
"""
import hashlib
import shutil
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from monitoreo import migrate
from monitoreo.app import create_app
from monitoreo.config import settings
from monitoreo.db import Database, utcnow
from monitoreo.engine.integrity import check
from monitoreo.engine.params import get_params
from monitoreo.engine.pipeline import flag_inactive_assignments
from monitoreo.models import Assignment

FIX = Path(__file__).parent / "fixtures"
VERSIONS = {"1.1": "0002_v1_1", "1.2": "0003_v1_2", "1.3": "0004_v1_3"}


def _digest(path):
    """Huella de cada tabla con las columnas que ya existían (las nuevas no cuentan)."""
    c = sqlite3.connect(path)
    try:
        out = {}
        for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                              "AND name != 'alembic_version'"):
            cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
            rows = c.execute(f"SELECT {','.join(cols)} FROM {t} ORDER BY 1").fetchall()
            out[t] = (cols, len(rows), hashlib.sha256(repr(rows).encode()).hexdigest())
        return out
    finally:
        c.close()


def _after_cols(path, table, cols):
    c = sqlite3.connect(path)
    try:
        rows = c.execute(f"SELECT {','.join(cols)} FROM {table} ORDER BY 1").fetchall()
        return len(rows), hashlib.sha256(repr(rows).encode()).hexdigest()
    finally:
        c.close()


@pytest.fixture(params=sorted(VERSIONS))
def old_db(request, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    dst = tmp_path / "monitoreo.db"
    shutil.copy(FIX / f"bd_v{request.param.replace('.', '_')}.sqlite", dst)
    return request.param, dst


def test_upgrade_keeps_every_row_and_backs_up(old_db):
    version, path = old_db
    before = _digest(path)
    url = f"sqlite:///{path.as_posix()}"
    assert migrate.inspect_state(url)["revision_actual"] == VERSIONS[version]
    rep = migrate.upgrade(url)
    assert rep.after == migrate.head_revision()
    assert rep.backup and Path(rep.backup).exists()
    for table, (cols, n, h) in before.items():
        assert _after_cols(path, table, cols) == (n, h), f"{version}: cambió {table}"
    assert _digest(rep.backup) == before                              # el respaldo es la base original
    db = Database(url)
    with db.session() as s:
        assert check(s) == []


def test_old_in_progress_assignments_are_flagged_not_released(old_db):
    version, path = old_db
    url = f"sqlite:///{path.as_posix()}"
    migrate.upgrade(url)
    db = Database(url)
    with db.session() as s:
        in_progress = {a.id for a in s.query(Assignment).filter(Assignment.status == "EN_PROCESO")}
        params = get_params(s)
    assert in_progress
    flag_inactive_assignments(db, utcnow() + timedelta(hours=2), params)
    with db.session() as s:
        still = {a.id for a in s.query(Assignment).filter(Assignment.status == "EN_PROCESO")}
        assert still == in_progress                                   # avisadas, nunca liberadas
        assert all(s.get(Assignment, i).inactive_alerted_at for i in in_progress)
        assert check(s) == []


def test_new_engine_serves_the_upgraded_data(old_db):
    version, path = old_db
    settings_pwd = "Version-Anterior-123"                             # la que usó la versión anterior
    with TestClient(create_app(f"sqlite:///{path.as_posix()}", start_engine=False, seed_demo=True)) as c:
        r = c.post("/api/auth/login", json={"username": "admin", "password": settings_pwd})
        assert r.status_code == 200, r.text
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        assert c.get("/api/requests?limit=500", headers=h).json()["total"] == 70
        mine = c.get("/api/assignments", headers=h).json()
        assert any(a["status"] == "EN_PROCESO" for a in mine)
        assert c.get("/api/dashboard", headers=h).status_code == 200
