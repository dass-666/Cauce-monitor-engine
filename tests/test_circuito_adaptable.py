"""v1.4: enfriamiento adaptable del circuito (hallazgo 7.1 de la v1.3)."""
from datetime import timedelta

import pytest

from monitoreo.connectors.base import RateLimited
from monitoreo.contracts import Snapshot
from monitoreo.engine import pipeline as pl
from monitoreo.engine.params import get_params, set_params
from monitoreo.engine.resilience import circuit_cooldown
from monitoreo.models import Source

from .helpers import Harness, make_source


def _params(db, **changes):
    with db.session() as s:
        if changes:
            set_params(s, changes)
        return get_params(s)


def _src(db, sid):
    with db.session() as s:
        x = s.get(Source, sid)
        return {k: getattr(x, k) for k in ("circuit_state", "circuit_opened_at", "circuit_open_count", "status",
                                           "consecutive_failures", "next_poll_at", "status_reason", "last_success_at")}


def _set(db, sid, **kw):
    with db.session() as s:
        x = s.get(Source, sid)
        for k, v in kw.items():
            setattr(x, k, v)


def _fail(db, sid, on=True):
    with db.session() as s:
        x = s.get(Source, sid)
        x.config = {**x.config, "error_rate": 1.0 if on else 0.0}
        x.max_retries = 0


def _open_circuit(h, db, sid):
    p = _params(db)
    _fail(db, sid)
    for _ in range(p["umbral_circuito"]):
        h.advance(1000)
        h.poll(sid)
    st = _src(db, sid)
    assert st["circuit_state"] == "ABIERTO"
    return st


def test_cooldown_formula():
    p = {"enfriamiento_modo": "adaptable", "enfriamiento_intervalos": 5, "enfriamiento_min_s": 5,
         "enfriamiento_max_s": 300, "enfriamiento_circuito_s": 60}
    assert [circuit_cooldown(k, 1, p) for k in (1, 2, 3, 4, 5, 6, 7, 8)] == [5, 10, 20, 40, 80, 160, 300, 300]
    assert circuit_cooldown(1, 0.2, p) == 5                     # mínimo
    assert circuit_cooldown(1, 30, p) == 150 and circuit_cooldown(2, 30, p) == 300   # máximo
    assert circuit_cooldown(3, 1, {**p, "enfriamiento_modo": "fijo"}) == 60          # compatibilidad v1.3


def test_invalid_mode_is_rejected(db):
    with db.session() as s, pytest.raises(ValueError):
        set_params(s, {"enfriamiento_modo": "agresivo"})


def test_short_failure_costs_a_few_intervals_not_a_minute(db, rng):
    h = Harness(db, rng)
    sid = make_source(db, interval_s=2)                          # base = 2 × 5 = 10 s
    h.poll(sid)
    st = _open_circuit(h, db, sid)
    opened = st["circuit_opened_at"]
    assert st["next_poll_at"] == opened + timedelta(seconds=10)
    assert "prueba en 10 s" in st["status_reason"]
    assert h.poll(sid).outcome == "CIRCUITO_ABIERTO"
    _fail(db, sid, on=False)                                      # el fallo era breve
    h.advance(10)
    assert h.poll(sid).outcome == "OK"                            # única consulta de prueba: bien
    st = _src(db, sid)
    assert st["circuit_state"] == "CERRADO" and st["circuit_open_count"] == 0


def test_persistent_failure_grows_cooldown_and_recovers_gradually(db, rng):
    h = Harness(db, rng)
    sid = make_source(db, interval_s=2)
    h.poll(sid)
    _open_circuit(h, db, sid)
    waits = []
    for _ in range(3):                                            # la prueba vuelve a fallar 3 veces
        st = _src(db, sid)
        waits.append((st["next_poll_at"] - st["circuit_opened_at"]).total_seconds())
        h.advance(waits[-1])
        h.poll(sid)
    st = _src(db, sid)
    waits.append((st["next_poll_at"] - st["circuit_opened_at"]).total_seconds())
    assert waits == [10, 20, 40, 80] and st["circuit_open_count"] == 4
    _fail(db, sid, on=False)
    h.advance(80)
    assert h.poll(sid).outcome == "OK"
    assert _src(db, sid)["circuit_open_count"] == 3               # baja de uno en uno, no a cero
    h.advance(5)
    h.poll(sid)
    assert _src(db, sid)["circuit_open_count"] == 2
    _fail(db, sid)                                                # recae: no empieza por 10 s
    for _ in range(3):
        h.advance(1000)
        h.poll(sid)
    st = _src(db, sid)
    assert st["circuit_open_count"] == 3
    assert (st["next_poll_at"] - st["circuit_opened_at"]).total_seconds() == 40


def test_only_one_probe_while_half_open(db, rng):
    """Si una consulta de prueba ya empezó (otro hilo, o el proceso murió a mitad), no se lanza otra
    hasta que termine o venza el enfriamiento."""
    h = Harness(db, rng)
    sid = make_source(db, interval_s=2)
    h.poll(sid)
    _open_circuit(h, db, sid)
    _set(db, sid, circuit_state="SEMIABIERTO", circuit_opened_at=h.clock.now())
    assert h.poll(sid).outcome == "CIRCUITO_ABIERTO"
    h.advance(10)
    _fail(db, sid, on=False)
    assert h.poll(sid).outcome == "OK"                            # venció: se permite una nueva prueba


def test_probe_marks_itself_before_querying(db, rng, monkeypatch):
    """Durante la consulta de prueba, la base ya dice SEMIABIERTO y la siguiente consulta está apartada."""
    h = Harness(db, rng)
    sid = make_source(db, interval_s=2)
    h.poll(sid)
    _open_circuit(h, db, sid)
    _fail(db, sid, on=False)
    h.advance(10)
    seen = {}
    real = pl.build_connector

    def spy(ctype, cfg, ctx):
        seen.update(_src(db, sid))
        return real(ctype, cfg, ctx)
    monkeypatch.setattr(pl, "build_connector", spy)
    assert h.poll(sid).outcome == "OK"
    assert seen["circuit_state"] == "SEMIABIERTO" and seen["next_poll_at"] > seen["circuit_opened_at"]


def test_circuit_survives_engine_restart(db, rng):
    h = Harness(db, rng)
    sid = make_source(db, interval_s=2)
    h.poll(sid)
    _open_circuit(h, db, sid)
    h.runtime = pl.RuntimeState()                                 # "reinicio": memoria nueva
    assert h.poll(sid).outcome == "CIRCUITO_ABIERTO"
    assert _src(db, sid)["circuit_open_count"] == 1


def test_rate_limit_during_probe_is_respected_and_not_a_failure(db, rng, monkeypatch):
    h = Harness(db, rng)
    sid = make_source(db, interval_s=2)
    h.poll(sid)
    _open_circuit(h, db, sid)
    h.advance(10)

    class Limited:
        def fetch(self):
            raise RateLimited("429", retry_after=45)
    monkeypatch.setattr(pl, "build_connector", lambda *a: Limited())
    assert h.poll(sid).outcome == "LIMITADO"
    st = _src(db, sid)
    assert st["circuit_open_count"] == 1 and st["consecutive_failures"] == 3   # no suma fallos
    assert st["next_poll_at"] >= h.clock.now() - timedelta(seconds=1) + timedelta(seconds=44)


def test_adaptive_mode_does_not_stop_a_source_after_a_short_outage(db, rng):
    """Con enfriamientos cortos, 5 aperturas llegan en ~2,5 min. No debe pasar a ERROR (revisión
    manual) hasta llevar `minutos_sin_exito_hasta_error` sin éxito."""
    h = Harness(db, rng)
    sid = make_source(db, interval_s=1)
    h.poll(sid)
    _open_circuit(h, db, sid)
    _set(db, sid, last_success_at=h.clock.now())
    for _ in range(6):
        st = _src(db, sid)
        h.advance((st["next_poll_at"] - h.clock.now()).total_seconds() + 0.1)
        h.poll(sid)
    st = _src(db, sid)
    assert st["circuit_open_count"] >= 5 and st["status"] == "ACTIVA"
    _set(db, sid, last_success_at=h.clock.now() - timedelta(minutes=16))
    h.advance((st["next_poll_at"] - h.clock.now()).total_seconds() + 0.1)
    h.poll(sid)
    assert _src(db, sid)["status"] == "ERROR"


def test_fixed_mode_keeps_v13_behaviour(db, rng):
    _params(db, enfriamiento_modo="fijo")
    h = Harness(db, rng)
    sid = make_source(db, interval_s=2)
    h.poll(sid)
    st = _open_circuit(h, db, sid)
    assert st["next_poll_at"] == st["circuit_opened_at"] + timedelta(seconds=60)
    _fail(db, sid, on=False)
    h.advance(60)
    assert h.poll(sid).outcome == "OK"
    assert _src(db, sid)["circuit_open_count"] == 0
