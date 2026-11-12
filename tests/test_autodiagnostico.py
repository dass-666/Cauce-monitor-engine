"""Autodiagnóstico (v1.2): detecta problemas reales y sugiere acciones."""
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select, update

from monitoreo import diagnostico as dg
from monitoreo.db import utcnow
from monitoreo.engine.lease import Lease
from monitoreo.models import Alert, Channel, Delivery, SlotState, Source

from .helpers import make_source

ROOT = Path(__file__).resolve().parent.parent


def _by_key(res):
    return {i["clave"]: i for i in res["comprobaciones"] if i["clave"]}


def _lead(db):
    Lease(db, holder="prueba:1:abc", ttl_s=60).acquire_or_renew(utcnow())


def test_no_leader_is_critical_and_stale_sources_are_grouped(db):
    make_source(db, "s1")
    make_source(db, "s2", region="SUR")
    res = dg.run(db)
    k = _by_key(res)
    assert res["estado"] == "CRITICO" and k["plan:sin_lider"]["estado"] == "CRITICO"
    assert k["plan:sin_lider"]["accion"]
    assert not [x for x in k if x.endswith(":viejo")]          # sin repetir "sin datos" por cada fuente
    _lead(db)
    res = dg.run(db)
    assert "plan:sin_lider" not in _by_key(res)
    assert {x for x in _by_key(res) if x.endswith(":viejo")}   # ahora sí: hay líder y no llegan datos


def test_healthy_database_reports_ok_integrity_and_schema(db):
    _lead(db)
    res = dg.run(db, deep=True)
    areas = {(i["area"], i["estado"]) for i in res["comprobaciones"]}
    assert ("Integridad", "OK") in areas and ("Esquema", "OK") in areas and ("Base de datos", "OK") in areas
    assert res["duracion_ms"] < 5000


def test_broken_invariant_is_detected(db):
    _lead(db)
    sid = make_source(db, "s1")
    with db.session() as s:
        s.add(SlotState(source_id=sid, region="NORTE", resource_key="r1", name="r", resource_type="entrada",
                        capacity=5, available=5, held=3, status="ACTIVO", fingerprint="x", version=1))
    k = _by_key(dg.run(db))
    assert k["int:datos"]["estado"] == "CRITICO" and "held=3" in k["int:datos"]["detalle"]


def test_outdated_schema_is_critical(db):
    if not db.is_sqlite:
        return  # la reversión con datos se prueba en test_migrations para PostgreSQL
    from monitoreo import migrate
    migrate.downgrade(db.url, migrate.V1_0_REVISION, make_backup=False)
    k = _by_key(dg.run(db))
    assert k["esq:version"]["estado"] == "CRITICO" and "migrate actualizar" in k["esq:version"]["accion"]


def test_misconfigured_channels_and_connectors(db, monkeypatch):
    _lead(db)
    monkeypatch.delenv("MONITOREO_TELEGRAM_TOKEN", raising=False)
    with db.session() as s:
        for ch in s.scalars(select(Channel)):
            if ch.type == "telegram":
                ch.enabled = True
            if ch.type == "webhook":
                ch.enabled, ch.config = True, {"url": "https://ejemplo-no-permitido.invalid/hook"}
        s.add(Source(code="http-mal", name="h", region="NORTE", availability_type="x", connector_type="http",
                     config={"url": "https://otro-host.invalid/api"}, interval_s=60, rate_limit_per_min=10))
        s.add(Source(code="csv-falta", name="c", region="NORTE", availability_type="x", connector_type="csv",
                     config={"path": "no_existe.csv"}, interval_s=60, rate_limit_per_min=10))
    res = dg.run(db)
    titles = [i["titulo"] for i in res["comprobaciones"] if i["estado"] == "CRITICO"]
    assert any("Telegram" in t and "sin configurar" in t for t in titles)
    assert any("host no permitido" in t for t in titles)
    assert any(t.startswith("http-mal") for t in titles) and any(t.startswith("csv-falta") for t in titles)


def test_stuck_deliveries_warning(db):
    _lead(db)
    from monitoreo.engine import alerts as al
    now = utcnow()
    with db.session() as s:
        al.create_alert(s, dedup_key="t:1", kind="SISTEMA", title="t", source_code="x", region="-", resource="r", now=now)
    with db.session() as s:
        s.execute(update(Delivery).values(status="ENVIANDO", claimed_at=now - timedelta(minutes=30)))
    k = _by_key(dg.run(db))
    assert k["ntf:atascadas"]["estado"] == "AVISO"


def test_database_down_is_reported_without_crashing(tmp_path):
    from monitoreo.db import Database
    bad = Database("postgresql+psycopg://nadie@127.0.0.1:1/noexiste?connect_timeout=2")
    try:
        res = dg.run(bad)
    finally:
        bad.engine.dispose()
    k = _by_key(res)
    assert res["estado"] == "CRITICO" and k["db:conexion"]["estado"] == "CRITICO"
    assert not any(i["area"] == "Integridad" for i in res["comprobaciones"])  # no se intenta lo imposible


def test_periodic_selfcheck_creates_one_system_alert_per_problem_per_day(db):
    now = utcnow()
    dg.run_and_alert(db, now=now)
    dg.run_and_alert(db, now=now + timedelta(minutes=10))
    with db.session() as s:
        n = s.scalar(select(func.count()).select_from(Alert).where(Alert.dedup_key.like("diag:plan:sin_lider:%")))
        assert n == 1
        assert s.get(__import__("monitoreo.models", fromlist=["Setting"]).Setting, "autodiagnostico.ultimo").value["estado"] == "CRITICO"
    dg.run_and_alert(db, now=now + timedelta(days=1))
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Alert).where(Alert.dedup_key.like("diag:plan:sin_lider:%"))) == 2


def test_cli_exit_codes(tmp_path):
    url = f"sqlite:///{(tmp_path / 'cli.db').as_posix()}"
    from monitoreo.db import Database
    d = Database(url)
    d.create_all()
    d.engine.dispose()
    env = {**os.environ, "MONITOREO_DB_URL": url, "MONITOREO_ENTORNO": "desarrollo"}
    r = subprocess.run([sys.executable, "-m", "monitoreo.diagnostico"], cwd=ROOT, env=env, capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 2 and "Ninguna instancia tiene el planificador activo" in r.stdout
    r = subprocess.run([sys.executable, "-m", "monitoreo.diagnostico", "--json"], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=60)
    import json
    assert json.loads(r.stdout)["estado"] == "CRITICO"


def test_telegram_module_can_be_imported_first():
    """Regresión v1.1: importar notifiers.telegram antes que engine.alerts daba ImportError circular."""
    code = ("import monitoreo.notifiers.telegram\n"
            "from monitoreo.engine.alerts import SENDERS\n"
            "assert SENDERS['telegram'].__name__ == 'TelegramSender'\n")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr


def test_api_is_admin_only(tmp_path, monkeypatch):
    from .test_api import ADMIN_PWD, client, login
    from monitoreo.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "admin_password", ADMIN_PWD)
    with client(f"sqlite:///{(tmp_path / 'a.db').as_posix()}") as c:
        h = login(c)
        c.post("/api/users", headers=h, json={"username": "op9", "password": "Operador-123", "role": "operador"})
        assert c.get("/api/autodiagnostico", headers=login(c, "op9", "Operador-123")).status_code == 403
        d = c.get("/api/autodiagnostico", headers=h).json()
        assert d["estado"] in ("OK", "AVISO", "CRITICO") and d["comprobaciones"]
