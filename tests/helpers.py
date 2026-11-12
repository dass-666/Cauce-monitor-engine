from datetime import datetime, timedelta

from monitoreo.engine.clock import VirtualClock
from monitoreo.engine.pipeline import RuntimeState, maintenance, poll_source, process_pending_events
from monitoreo.models import Operator, Request, Source
from monitoreo.simulator.world import World


def make_source(db, code="sim-norte", region="NORTE", feed=None, **kw):
    with db.session() as s:
        src = Source(code=code, name=code, region=region, availability_type="evento", connector_type="simulacion",
                     config={"feed": feed or code, **kw.pop("config", {})}, interval_s=kw.pop("interval_s", 5),
                     jitter_pct=0.0, rate_limit_per_min=kw.pop("rate_limit_per_min", 600), **kw)
        s.add(src)
        s.flush()
        return src.id


def make_operator(db, name="Op", regions=None, max_concurrent=5):
    with db.session() as s:
        op = Operator(name=name, regions=regions or [], max_concurrent=max_concurrent)
        s.add(op)
        s.flush()
        return op.id


def make_request(db, code, region="NORTE", rtype="entrada", priority=3, quantity=1, created_at=None,
                 status="ELEGIBLE", conditions=None):
    with db.session() as s:
        r = Request(code=code, holder="Titular Ficticio", region=region, resource_type=rtype, priority=priority,
                    quantity=quantity, status=status, conditions=conditions or {},
                    created_at=created_at or datetime(2026, 1, 1))
        s.add(r)
        s.flush()
        return r.id


class Harness:
    """Ejecuta el ciclo completo con reloj virtual."""

    def __init__(self, db, rng):
        self.db, self.rng = db, rng
        self.clock = VirtualClock(datetime(2026, 6, 1, 12, 0, 0))
        self.runtime = RuntimeState()
        self.world = World(db, clock=self.clock, rng=rng)

    def poll(self, source_id):
        out = poll_source(self.db, source_id, self.clock, self.runtime, self.rng)
        self.clock.advance(1)
        return out

    def process(self):
        return process_pending_events(self.db, self.clock)

    def maintain(self):
        return maintenance(self.db, self.clock)

    def advance(self, seconds):
        self.clock.advance(seconds)
