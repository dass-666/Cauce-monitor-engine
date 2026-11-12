"""Canal Telegram contra el emulador local (sin credenciales reales)."""
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from sqlalchemy import select

from monitoreo.config import settings
from monitoreo.engine import alerts as al
from monitoreo.engine.params import get_params
from monitoreo.models import Alert, Channel, Delivery, NotificationLog
from monitoreo.notifiers.telegram import TelegramSender, format_message

from .helpers import Harness, make_operator, make_request, make_source

TOKEN = "000000:EMULADOR"


def _port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p  # noqa: E702


@pytest.fixture(scope="module")
def emulator():
    from fastapi import FastAPI
    from monitoreo.emuladores.telegram import router
    app = FastAPI()
    app.include_router(router)
    port = _port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/api-prueba/telegram"
    server.should_exit = True


@pytest.fixture
def tg(emulator, monkeypatch):
    monkeypatch.setenv("MONITOREO_TELEGRAM_TOKEN", TOKEN)
    monkeypatch.setenv("MONITOREO_TELEGRAM_CHATS", "111,222")
    monkeypatch.setenv("MONITOREO_TELEGRAM_API", emulator)
    httpx.post(f"{emulator}/reiniciar")

    def control(**kw):
        httpx.post(f"{emulator}/control", json=kw)

    def messages():
        return httpx.get(f"{emulator}/mensajes").json()["mensajes"]
    return control, messages


PAYLOAD = {"id": 1, "dedup_key": "asg:1", "tipo": "ASIGNACION", "titulo": "SOL-1 asignada a Operador 01: Kit",
           "fuente": "sim-este", "region": "ESTE", "recurso": "Kit Alfa", "solicitud": "SOL-1", "prioridad": 1,
           "operador": "Operador 01", "titular": "Expediente Ficticio Privado", "fecha_hora": "2026-06-01T12:00:00Z"}


def sender(cfg=None):
    return TelegramSender(Channel(name="tg", type="telegram", config=cfg or {}))


def test_message_excludes_personal_data():
    text = format_message(PAYLOAD)
    assert "Expediente Ficticio Privado" not in text and "titular" not in text.lower()
    assert "SOL-1" in text and "ESTE" in text and "Ref: asg:1" in text


def test_delivers_to_all_authorized_chats(tg):
    control, messages = tg
    progress = {}
    assert sender().send(PAYLOAD, progress) == "entregado a 2 chat(s)"
    assert sorted(m["chat_id"] for m in messages()) == ["111", "222"]
    assert progress["entregado_a"] == ["111", "222"]


def test_rate_limit_then_retry_without_duplicates(tg):
    control, messages = tg
    progress = {}
    control(modo="429", retry_after=7)
    with pytest.raises(al.DeliveryError) as e:
        sender().send(PAYLOAD, progress)
    assert e.value.retry_after == 7 and not e.value.permanent
    control(modo="normal")
    sender().send(PAYLOAD, progress)
    sender().send(PAYLOAD, progress)          # un reintento extra no reenvía nada
    assert sorted(m["chat_id"] for m in messages()) == ["111", "222"]


def test_partial_progress_survives_server_errors(tg):
    control, messages = tg
    progress = {}
    control(modo="normal", chats_bloqueados=[])
    # el primer chat entra; luego el servidor falla
    control(modo="normal")
    p = {"entregado_a": ["111"], "rechazado_por": {}}
    control(modo="500")
    with pytest.raises(al.DeliveryError):
        sender().send(PAYLOAD, p)
    control(modo="normal")
    sender().send(PAYLOAD, p)
    assert [m["chat_id"] for m in messages()] == ["222"]   # a 111 no se le repite


def test_unreachable_recipients(tg):
    control, messages = tg
    control(chats_bloqueados=["222"])
    assert "rechazado por 1" in sender().send(PAYLOAD, {})
    control(chats_bloqueados=["111", "222"])
    with pytest.raises(al.DeliveryError) as e:
        sender().send(PAYLOAD, {})
    assert e.value.permanent


def test_configuration_errors_are_permanent(tg, monkeypatch):
    with pytest.raises(al.DeliveryError) as e:
        sender({"chats": ["999"]}).send(PAYLOAD, {})      # chat no autorizado
    assert e.value.permanent
    monkeypatch.setenv("MONITOREO_TELEGRAM_TOKEN", "malo")
    with pytest.raises(al.DeliveryError) as e:
        sender().send(PAYLOAD, {})
    assert e.value.permanent and "401" in str(e.value)
    monkeypatch.delenv("MONITOREO_TELEGRAM_TOKEN")
    with pytest.raises(al.DeliveryError) as e:
        sender().send(PAYLOAD, {})
    assert e.value.permanent


def test_token_never_leaks_in_errors(tg, monkeypatch):
    monkeypatch.setenv("MONITOREO_TELEGRAM_API", f"http://127.0.0.1:{_port()}")   # nadie escucha
    with pytest.raises(al.DeliveryError) as e:
        sender().send(PAYLOAD, {})
    assert TOKEN not in str(e.value) and "EMULADOR" not in str(e.value)


def test_host_must_be_allowed(tg, monkeypatch):
    monkeypatch.setenv("MONITOREO_TELEGRAM_API", "https://telegram.ejemplo-falso.test")
    with pytest.raises(al.DeliveryError) as e:
        sender().send(PAYLOAD, {})
    assert e.value.permanent


def test_end_to_end_engine_to_telegram_with_flaky_api(db, rng, tg):
    control, messages = tg
    with db.session() as s:
        for ch in s.scalars(select(Channel)):
            ch.enabled = ch.type in ("panel", "telegram")
            if ch.type == "telegram":
                ch.config = {"tipos": ["ASIGNACION", "SISTEMA"]}
    h = Harness(db, rng)
    sid = make_source(db)
    h.world.create_item("sim-norte", "K", "Concierto Ficticio", "NORTE", "entrada", 5, 0)
    make_operator(db, "Operador Uno")
    make_request(db, "SOL-77", priority=1)
    h.poll(sid)
    h.world.release(1, 1)
    h.poll(sid)
    h.process()
    with db.session() as s:
        p = get_params(s)
    control(modo="500", fallos_restantes=2)          # dos fallos y luego se recupera
    for _ in range(6):
        al.dispatch_pending(db, h.clock.now(), p)
        h.advance(600)
    msgs = messages()
    assert len(msgs) == 2 and all("SOL-77" in m["text"] for m in msgs)
    with db.session() as s:
        tgd = s.scalars(select(Delivery).join(Channel, Channel.id == Delivery.channel_id)
                        .where(Channel.type == "telegram")).one()
        assert tgd.status == "ENVIADA" and tgd.attempts == 2   # 1er intento: ambos chats con 500
        logs = s.scalars(select(NotificationLog).where(NotificationLog.channel == "Telegram")).all()
        assert [l.outcome for l in logs] == ["FALLO", "OK"]
        a = s.scalars(select(Alert)).one()
    assert al.acknowledge(db, a.id, "admin", h.clock.now())   # confirmación desde el panel
