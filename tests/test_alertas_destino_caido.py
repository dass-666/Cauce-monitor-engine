"""v1.4 (auditoría): un destino de alertas que no responde no debe frenar el motor ni a otros canales.

Defecto reproducido (v1.0–v1.3): el planificador esperaba a `dispatch_pending` dentro de su ciclo y
este enviaba hasta 200 entregas una tras otra. Con un destino colgado (cada envío agota su tiempo de
espera, 5–10 s) el ciclo quedaba parado minutos: no se programaban consultas, no se asignaba, no se
renovaba el arrendamiento y los demás canales esperaban detrás.
"""
import asyncio
import time

from sqlalchemy import func, select

from monitoreo.db import utcnow
from monitoreo.engine import alerts as al
from monitoreo.engine import scheduler as sch
from monitoreo.engine.params import set_params
from monitoreo.models import Channel, Delivery

from .test_notificaciones import _alert, _params

HANG_S = 0.3


def _two_channels(db):
    with db.session() as s:
        for ch in s.scalars(select(Channel)):
            ch.enabled = False
        s.add(Channel(name="colgado", type="webhook", enabled=True, config={"url": "http://127.0.0.1:9/x"}))
        s.add(Channel(name="sano", type="simulado", enabled=True, config={"fail_rate": 0}))


def _count(db, status, channel):
    with db.session() as s:
        cid = s.scalar(select(Channel.id).where(Channel.name == channel))
        return s.scalar(select(func.count()).select_from(Delivery)
                        .where(Delivery.status == status, Delivery.channel_id == cid))


def test_a_hanging_channel_does_not_delay_the_others(db, monkeypatch):
    _two_channels(db)
    calls = {"colgado": 0}

    class Hanging(al.ChannelSender):
        def send(self, payload, progress=None):
            calls["colgado"] += 1
            time.sleep(HANG_S)                   # como un tiempo de espera agotado
            raise al.DeliveryError("red (ReadTimeout)")

    class Ok(al.ChannelSender):
        def send(self, payload, progress=None):
            return "ok"
    monkeypatch.setitem(al.SENDERS, "webhook", Hanging)
    monkeypatch.setitem(al.SENDERS, "simulado", Ok)
    for k in range(20):
        _alert(db, f"evt:caido-{k}")
    t0 = time.monotonic()
    stats = al.dispatch_pending(db, utcnow(), _params(db))
    took = time.monotonic() - t0
    assert _count(db, "ENVIADA", "sano") == 20           # el canal sano no espera al colgado
    assert calls["colgado"] == 1 and took < 20 * HANG_S / 2
    assert stats["aplazadas_por_canal"] == 19
    assert _count(db, "PENDIENTE", "colgado") == 20      # nada se pierde ni gasta intentos de más
    with db.session() as s:
        cid = s.scalar(select(Channel.id).where(Channel.name == "colgado"))
        assert s.scalar(select(func.sum(Delivery.attempts)).where(Delivery.channel_id == cid)) == 1


def test_dispatch_respects_its_time_budget(db, monkeypatch):
    _two_channels(db)

    class Slow(al.ChannelSender):
        def send(self, payload, progress=None):
            time.sleep(0.1)
            return "ok"
    monkeypatch.setitem(al.SENDERS, "simulado", Slow)
    monkeypatch.setitem(al.SENDERS, "webhook", Slow)
    for k in range(30):
        _alert(db, f"evt:lento-{k}")
    t0 = time.monotonic()
    al.dispatch_pending(db, utcnow(), _params(db, despacho_presupuesto_s=0.5))
    assert time.monotonic() - t0 < 1.5
    sent = _count(db, "ENVIADA", "sano") + _count(db, "ENVIADA", "colgado")
    assert 3 <= sent < 60
    al.dispatch_pending(db, utcnow(), _params(db, despacho_presupuesto_s=60))   # el resto, después
    assert _count(db, "ENVIADA", "sano") + _count(db, "ENVIADA", "colgado") == 60


def test_scheduler_keeps_working_while_alerts_are_slow(db, monkeypatch):
    with db.session() as s:
        set_params(s, {"tick_segundos": 0.1})
    real = al.dispatch_pending

    def slow_dispatch(*a, **kw):
        time.sleep(2.0)                          # un envío colgado
        return real(*a, **kw)
    monkeypatch.setattr(sch.alerts_mod, "dispatch_pending", slow_dispatch)

    async def scenario():
        s1 = sch.Scheduler(db, holder="uno", lease_ttl_s=1.5)
        s1.start()
        await asyncio.sleep(1.5)
        cycles = s1.cycles
        role = s1.role
        await s1.stop()
        return cycles, role
    cycles, role = asyncio.run(scenario())
    assert role == "LIDER"                       # el arrendamiento se siguió renovando
    assert cycles >= 5                           # el ciclo no se quedó esperando a las alertas
