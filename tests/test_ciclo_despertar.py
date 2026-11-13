"""v1.4 (optimización medida): una consulta con cambios despierta el ciclo del planificador.

Antes, los cambios detectados esperaban al siguiente tick (`tick_segundos`, 0,5 s) para
asignarse: en la v1.3 eso era ~40 % del tiempo entre la detección y la asignación.
"""
import asyncio
import time

from monitoreo.engine import scheduler as sch
from monitoreo.engine.params import set_params
from monitoreo.engine.pipeline import PollOutcome


def _run(db, monkeypatch, events):
    with db.session() as s:
        set_params(s, {"tick_segundos": 2.0})
    calls, polled = [], []
    monkeypatch.setattr(sch, "due_sources", lambda db, now: [] if polled else [1])

    def fake_poll(*a, **kw):
        polled.append(time.monotonic())
        time.sleep(0.05)
        return PollOutcome("OK", events=events)
    monkeypatch.setattr(sch, "poll_source", fake_poll)
    monkeypatch.setattr(sch, "process_pending_events", lambda *a, **kw: calls.append(time.monotonic()))
    monkeypatch.setattr(sch, "maintenance", lambda *a, **kw: None)

    async def scenario():
        s1 = sch.Scheduler(db, holder="uno", lease_ttl_s=10)
        s1.start()
        await asyncio.sleep(1.0)
        await s1.stop()
        return s1.wakeups
    return asyncio.run(scenario()), calls, polled


def test_changes_wake_the_cycle(db, monkeypatch):
    wakeups, calls, polled = _run(db, monkeypatch, events=3)
    assert wakeups >= 1
    after = [c for c in calls if c > polled[0] + 0.05]
    assert after and after[0] - polled[0] < 0.4        # sin esperar los 2 s del tick


def test_no_changes_no_extra_cycles(db, monkeypatch):
    wakeups, calls, polled = _run(db, monkeypatch, events=0)
    assert wakeups == 0 and len(calls) == 1            # el tick de 2 s se respeta
