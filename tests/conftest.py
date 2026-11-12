import os
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MONITOREO_INICIAR_MOTOR", "0")
os.environ.setdefault("MONITOREO_SECRET_KEY", "clave-de-pruebas")

from monitoreo.config import settings  # noqa: E402
from monitoreo.db import Database  # noqa: E402

PG_URL = os.environ.get("MONITOREO_TEST_PG")  # p. ej. postgresql+psycopg://postgres@localhost/monitoreo_test
BACKENDS = ["sqlite"] + (["postgres"] if PG_URL else [])


@pytest.fixture(params=BACKENDS)
def db(request, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    if request.param == "sqlite":
        d = Database(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    else:
        d = Database(PG_URL)
        d.drop_all()
    d.create_all()
    from monitoreo.simulator.seed import seed_base
    seed_base(d)
    yield d
    if request.param == "postgres":
        d.drop_all()
    d.engine.dispose()


@pytest.fixture
def rng():
    return random.Random(1234)
