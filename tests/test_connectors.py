import json
import socket
import threading
import time

import pytest
import uvicorn

from monitoreo.config import settings
from monitoreo.connectors.base import ConnectorError, RateLimited, TemporaryError
from monitoreo.connectors.files import CsvFileConnector, JsonFileConnector
from monitoreo.connectors.http_api import HttpApiConnector
from monitoreo.connectors.registry import build_connector


def test_json_connector(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / "feeds").mkdir()
    (tmp_path / "feeds" / "a.json").write_text(json.dumps({"complete": False, "items": [
        {"id": "X1", "nombre": "Uno", "tipo": "turno", "disponibles": 2, "capacidad": 3}]}))
    snap = build_connector("json", {"path": "a.json"}).fetch()
    assert snap.complete is False and snap.observations[0].resource_key == "X1"
    assert snap.observations[0].available == 2


def test_json_partial_write_is_temporary(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / "feeds").mkdir()
    (tmp_path / "feeds" / "a.json").write_text('{"items": [ {"id": ')
    with pytest.raises(TemporaryError):
        build_connector("json", {"path": "a.json"}).fetch()


def test_csv_connector_and_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / "feeds").mkdir()
    (tmp_path / "feeds" / "b.csv").write_text("clave;nombre;tipo;disponibles;estado\nC1;Uno;unidad;0;activo\n"
                                              "C2;Dos;unidad;4;CANCELADO\n")
    snap = build_connector("csv", {"path": "b.csv", "delimiter": ";"}).fetch()
    assert [o.status for o in snap.observations] == ["ACTIVO", "CANCELADO"]
    (tmp_path / "feeds" / "c.csv").write_text("clave,nombre,tipo,disponibles\nC1,Uno,unidad,-3\n")
    with pytest.raises(ConnectorError):
        build_connector("csv", {"path": "c.csv"}).fetch()


def test_path_traversal_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    for p in ("../x.json", "/etc/passwd", "..\\..\\x.json"):
        try:
            c = build_connector("json", {"path": p})
            # si la ruta no escapa (p. ej. barra invertida en Linux) debe quedar dentro de feeds
            assert (tmp_path / "feeds").resolve() in c.path.parents
        except ConnectorError:
            pass
    with pytest.raises(ConnectorError):   # claves internas inyectadas por el usuario se ignoran
        build_connector("json", {"path": "../../etc/passwd", "_base_dir": "/"})


def test_unknown_connector():
    with pytest.raises(ConnectorError):
        build_connector("ftp", {})


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def live_server(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from monitoreo.app import create_app
    from monitoreo.simulator.world import World
    app = create_app(f"sqlite:///{(tmp_path / 'live.db').as_posix()}", start_engine=False, seed_demo=False)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    World(app.state.db).create_item("feed-x", "K1", "Recurso API", "SUR", "mesa", capacity=4, available=2)
    yield app, port
    server.should_exit = True
    t.join(5)


def test_http_connector_against_local_api(live_server):
    app, port = live_server
    base = f"http://127.0.0.1:{port}/api-prueba/feeds/feed-x"
    snap = build_connector("http", {"url": base}).fetch()
    assert snap.observations[0].available == 2
    with pytest.raises(RateLimited) as e:
        build_connector("http", {"url": base + "?limitar=1"}).fetch()
    assert e.value.retry_after == 30
    with pytest.raises(TemporaryError):
        build_connector("http", {"url": base + "?fallar=1"}).fetch()
    with pytest.raises(TemporaryError):
        build_connector("http", {"url": f"http://127.0.0.1:{_free_port()}/nada", "timeout_s": 1}).fetch()


def test_http_host_allowlist():
    with pytest.raises(ConnectorError):
        build_connector("http", {"url": "https://ejemplo-externo.test/api"})


def test_webhook_delivery_signed_and_idempotent(live_server, monkeypatch):
    app, port = live_server
    from monitoreo.engine.alerts import DeliveryError, WebhookSender
    from monitoreo.models import Channel
    ch = Channel(name="w", type="webhook", config={"url": f"http://127.0.0.1:{port}/api-prueba/webhook"})
    sender = WebhookSender(ch)
    payload = {"dedup_key": "evt:abc", "titulo": "prueba"}
    assert sender.send(payload) == "HTTP 200"
    assert sender.send(payload) == "HTTP 200"            # reenvío: el receptor lo marca como duplicado
    import httpx
    info = httpx.get(f"http://127.0.0.1:{port}/api-prueba/webhook").json()
    assert info["recibidos"] == 1
    bad = WebhookSender(Channel(name="w2", type="webhook", config={
        "url": f"http://127.0.0.1:{port}/api-prueba/webhook", "secret": "otra"}))
    with pytest.raises(DeliveryError) as e:               # firma incorrecta -> 401 -> fallo permanente
        bad.send(payload)
    assert e.value.permanent
