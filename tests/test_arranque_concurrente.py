"""Regresiones v1.2 encontradas en las pruebas de capacidad con PostgreSQL."""
import threading

from sqlalchemy import func, select

from monitoreo.app import bootstrap
from monitoreo.config import settings
from monitoreo.models import Channel, Region, User


def test_two_instances_bootstrapping_an_empty_database_at_once(db, monkeypatch):
    """Dos instancias arrancan a la vez sobre una base vacía: ninguna debe caerse
    (antes: UniqueViolation en users) ni duplicar canales (avisos dobles)."""
    monkeypatch.setattr(settings, "admin_password", "Arranque-Doble-2026")
    db.reset_for_tests()
    errors, barrier = [], threading.Barrier(3)

    def start():
        barrier.wait()
        try:
            bootstrap(db, seed_demo=True)
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))
    ts = [threading.Thread(target=start) for _ in range(3)]
    [t.start() for t in ts]
    [t.join(120) for t in ts]
    assert not errors, errors
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(User).where(User.role == "admin")) == 1
        names = [c.name for c in s.scalars(select(Channel))]
        assert len(names) == len(set(names)), names
        assert s.scalar(select(func.count()).select_from(Region)) == 5


def test_integrity_check_reads_a_consistent_snapshot(db):
    """En PostgreSQL (READ COMMITTED) cada consulta veía un instante distinto: con el motor
    escribiendo, el verificador informaba incoherencias que no existían."""
    from monitoreo.engine import integrity
    with db.session() as s:
        integrity.check(s)
        if not db.is_sqlite:
            iso = s.connection().get_isolation_level()
            assert iso in ("REPEATABLE READ", "SERIALIZABLE"), iso
