"""Integridad de notificaciones (v1.2): caídas durante y después del envío, reintentos, 429,
destinatarios bloqueados, entregas parciales, doble despachador, resultados tardíos y claves
estables. Webhook con transporte HTTP simulado; Telegram contra el emulador local."""
import threading
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select, update

from monitoreo.db import utcnow
from monitoreo.engine import alerts as al
from monitoreo.engine.params import get_params
from monitoreo.engine.recovery import run_recovery
from monitoreo.models import Channel, Delivery, NotificationLog

from .test_telegram import TOKEN, emulator, tg  # noqa: F401  (fixtures)


class Crash(BaseException):
    """Muerte del proceso: no la captura `except Exception` del despachador."""


def _only_channel(db, ctype, config=None):
    with db.session() as s:
        for ch in s.scalars(select(Channel)):
            ch.enabled = ch.type == ctype
            if ch.type == ctype and config is not None:
                ch.config = config


def _alert(db, key="evt:prueba-1", now=None):
    with db.session() as s:
        a = al.create_alert(s, dedup_key=key, kind="SISTEMA", title="Prueba", source_code="x", region="NORTE",
                            resource="r", now=now or utcnow())
        return a.id


def _delivery(db):
    with db.session() as s:
        d = s.scalars(select(Delivery)).one()
        return {"status": d.status, "attempts": d.attempts, "next": d.next_attempt_at, "meta": d.meta,
                "error": d.last_error, "token": d.claim_token}


def _params(db, **kw):
    with db.session() as s:
        p = get_params(s)
    p.update(kw)
    return p


@pytest.fixture
def webhook(db, monkeypatch):
    """Webhook hacia un receptor simulado que, como pide la documentación, descarta repetidos por clave."""
    state = {"responses": [], "seen": [], "unique": set(), "crash_after_send": False, "crash_before_send": False}

    def handler(request: httpx.Request):
        if state["crash_before_send"]:
            raise Crash()
        key = request.headers["Idempotency-Key"]
        state["seen"].append((key, request.headers["X-Id-Alerta"]))
        state["unique"].add(key)
        code, headers = state["responses"].pop(0) if state["responses"] else (200, {})
        resp = httpx.Response(code, headers=headers, json={"ok": code == 200})
        if state["crash_after_send"]:
            raise Crash()       # el receptor ya lo tiene, pero el motor muere antes de registrarlo
        return resp

    monkeypatch.setattr(al, "_http", httpx.Client(transport=httpx.MockTransport(handler)))
    _only_channel(db, "webhook", {"url": "http://127.0.0.1:9/hook"})
    return state


def test_crash_before_send_resends_once_without_duplicate(db, webhook):
    _alert(db)
    now = utcnow()
    webhook["crash_before_send"] = True
    with pytest.raises(Crash):
        al.dispatch_pending(db, now, _params(db))
    assert _delivery(db)["status"] == "ENVIANDO" and not webhook["seen"]
    webhook["crash_before_send"] = False
    run_recovery(db, now + timedelta(seconds=1), "nuevo", 0)          # reinicio del proceso
    al.dispatch_pending(db, now + timedelta(seconds=2), _params(db))
    assert _delivery(db)["status"] == "ENVIADA" and len(webhook["seen"]) == 1


def test_crash_after_send_before_commit_duplicates_but_key_is_stable(db, webhook):
    """Garantía 'al menos una vez': el receptor recibe dos veces y las descarta por la clave."""
    _alert(db)
    now = utcnow()
    webhook["crash_after_send"] = True
    with pytest.raises(Crash):
        al.dispatch_pending(db, now, _params(db))
    webhook["crash_after_send"] = False
    run_recovery(db, now + timedelta(seconds=1), "nuevo", 0)
    al.dispatch_pending(db, now + timedelta(seconds=2), _params(db))
    assert _delivery(db)["status"] == "ENVIADA"
    assert len(webhook["seen"]) == 2 and len(webhook["unique"]) == 1        # mismo Idempotency-Key
    assert webhook["seen"][0] == webhook["seen"][1]


def test_stuck_claim_is_requeued_without_leadership_change(db, webhook):
    _alert(db)
    now = utcnow()
    webhook["crash_before_send"] = True
    with pytest.raises(Crash):
        al.dispatch_pending(db, now, _params(db))
    webhook["crash_before_send"] = False
    al.dispatch_pending(db, now + timedelta(seconds=60), _params(db))       # aún no vence el reclamo
    assert _delivery(db)["status"] == "ENVIANDO"
    st = al.dispatch_pending(db, now + timedelta(seconds=301), _params(db))
    assert st["reencoladas"] == 1 and _delivery(db)["status"] == "ENVIADA" and len(webhook["seen"]) == 1


def test_retries_backoff_and_permanent_errors(db, webhook):
    _alert(db)
    now = utcnow()
    p = _params(db, reintentos_notificacion=3, backoff_notificacion_s=2)
    webhook["responses"] = [(500, {}), (500, {}), (500, {})]
    for k in range(3):
        al.dispatch_pending(db, now + timedelta(hours=k), p)
    d = _delivery(db)
    assert d["status"] == "FALLIDA" and d["attempts"] == 3 and d["error"] == "HTTP 500"
    _alert(db, "evt:prueba-2")
    webhook["responses"] = [(400, {})]
    al.dispatch_pending(db, now + timedelta(hours=5), p)
    with db.session() as s:
        d2 = s.scalar(select(Delivery).order_by(Delivery.id.desc()))
        assert d2.status == "FALLIDA" and d2.attempts == 1      # 4xx: permanente, no se insiste


@pytest.mark.parametrize("code", [429, 503])
def test_webhook_retry_after_is_respected(db, webhook, code):
    _alert(db)
    now = utcnow()
    webhook["responses"] = [(code, {"Retry-After": "120"})]
    al.dispatch_pending(db, now, _params(db, backoff_notificacion_s=1))
    d = _delivery(db)
    assert d["status"] == "PENDIENTE" and d["next"] >= now + timedelta(seconds=120)
    al.dispatch_pending(db, now + timedelta(seconds=60), _params(db))       # antes de tiempo: no se envía
    assert len(webhook["seen"]) == 1
    al.dispatch_pending(db, now + timedelta(seconds=121), _params(db))
    assert _delivery(db)["status"] == "ENVIADA" and len(webhook["seen"]) == 2


def test_late_result_does_not_overwrite_newer_state(db, monkeypatch):
    """Un despachador colgado termina después de que otro ya entregó: su resultado no pisa el estado."""
    _only_channel(db, "simulado", {"fail_rate": 0})
    _alert(db)
    now = utcnow()
    started, release = threading.Event(), threading.Event()
    calls = {"n": 0}

    class Slow(al.ChannelSender):
        def send(self, payload, progress=None):
            calls["n"] += 1
            if calls["n"] == 1:
                started.set()
                release.wait(10)
                raise al.DeliveryError("tiempo agotado tras quedarse colgado")
            return "ok"
    monkeypatch.setitem(al.SENDERS, "simulado", Slow)
    t = threading.Thread(target=al.dispatch_pending, args=(db, now, _params(db)))
    t.start()
    started.wait(5)
    al.dispatch_pending(db, now + timedelta(seconds=400), _params(db))      # reclamo vencido: otro lo entrega
    assert _delivery(db)["status"] == "ENVIADA"
    release.set()
    t.join(10)
    d = _delivery(db)
    assert d["status"] == "ENVIADA" and d["error"] is None                  # el fallo tardío no lo pisó
    with db.session() as s:
        logs = [x.detail for x in s.scalars(select(NotificationLog))]
    assert any(x.startswith("[resultado tardío") for x in logs)


def test_double_dispatcher_many_deliveries_each_sent_once(db, monkeypatch):
    _only_channel(db, "simulado", {"fail_rate": 0})
    for k in range(60):
        _alert(db, f"evt:doble-{k}")
    sent = []
    lock = threading.Lock()

    class Rec(al.ChannelSender):
        def send(self, payload, progress=None):
            with lock:
                sent.append(payload["dedup_key"])
            return "ok"
    monkeypatch.setitem(al.SENDERS, "simulado", Rec)
    now = utcnow()
    barrier = threading.Barrier(3)

    def w():
        barrier.wait()
        al.dispatch_pending(db, now, _params(db))
    ts = [threading.Thread(target=w) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(sent) == 60 == len(set(sent))
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Delivery).where(Delivery.status == "ENVIADA")) == 60


def test_telegram_crash_mid_delivery_does_not_repeat_served_chats(db, tg, monkeypatch):  # noqa: F811
    """Tres destinatarios; el proceso muere tras servir al primero. Al reintentar, ese no se repite."""
    control, messages = tg
    monkeypatch.setenv("MONITOREO_TELEGRAM_CHATS", "111,222,333")
    _only_channel(db, "telegram", {})
    _alert(db)
    now = utcnow()
    real = al.http_client()
    calls = {"n": 0}

    class CrashingClient:
        def post(self, *a, **kw):
            calls["n"] += 1
            if calls["n"] == 2:
                raise Crash()
            return real.post(*a, **kw)
    monkeypatch.setattr(al, "http_client", lambda: CrashingClient())
    import monitoreo.notifiers.telegram as tgm
    monkeypatch.setattr(tgm, "http_client", lambda: CrashingClient())
    with pytest.raises(Crash):
        al.dispatch_pending(db, now, _params(db))
    assert _delivery(db)["meta"] == {"entregado_a": ["111"], "rechazado_por": {}}   # guardado al instante
    run_recovery(db, now + timedelta(seconds=1), "nuevo", 0)
    al.dispatch_pending(db, now + timedelta(seconds=2), _params(db))
    chats = [m["chat_id"] for m in messages()]
    assert sorted(chats) == ["111", "222", "333"]                           # nadie lo recibió dos veces
    assert _delivery(db)["status"] == "ENVIADA"


def test_telegram_blocked_recipient_and_429_partial(db, tg, monkeypatch):  # noqa: F811
    control, messages = tg
    monkeypatch.setenv("MONITOREO_TELEGRAM_CHATS", "111,222,333")
    _only_channel(db, "telegram", {})
    control(chats_bloqueados=["222"])
    _alert(db)
    now = utcnow()
    al.dispatch_pending(db, now, _params(db))
    d = _delivery(db)
    assert d["status"] == "ENVIADA" and d["meta"]["entregado_a"] == ["111", "333"] and "222" in d["meta"]["rechazado_por"]
    _alert(db, "evt:prueba-429")
    control(chats_bloqueados=[], modo="429", retry_after=90)
    later = utcnow() + timedelta(seconds=1)
    al.dispatch_pending(db, later, _params(db))
    with db.session() as s:
        d2 = s.scalar(select(Delivery).order_by(Delivery.id.desc()))
        assert d2.attempts == 1 and d2.status == "PENDIENTE"
        assert d2.next_attempt_at >= later + timedelta(seconds=90)


def test_idempotency_key_is_stable_across_retries(db, webhook):
    _alert(db)
    now = utcnow()
    webhook["responses"] = [(500, {}), (200, {})]
    al.dispatch_pending(db, now, _params(db, backoff_notificacion_s=1))
    al.dispatch_pending(db, now + timedelta(hours=1), _params(db))
    assert len(webhook["seen"]) == 2 and len(set(webhook["seen"])) == 1
    with db.session() as s:
        s.execute(update(Delivery).values(status="ENVIADA"))
