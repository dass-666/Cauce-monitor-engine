"""Modo producción (v1.2): configuración insegura rechazada, laboratorio y rutas de prueba
inaccesibles, sin datos de demostración y límite de peticiones a la API."""
import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from monitoreo import entorno
from monitoreo.api.ratelimit import ApiRateLimiter
from monitoreo.app import create_app
from monitoreo.config import settings
from monitoreo.models import Channel, Source

PWD = "Produccion-Segura-2026"
KEY = "k" * 40
LAB_PATHS = [("GET", "/api/lab/items"), ("POST", "/api/lab/items"), ("POST", "/api/lab/items/1/liberar"),
             ("POST", "/api/lab/random"), ("POST", "/api/lab/competencia"), ("POST", "/api/lab/acelerada"),
             ("GET", "/api/lab/runs"), ("GET", "/api-prueba/feeds/sim-norte"), ("POST", "/api-prueba/webhook"),
             ("GET", "/api-prueba/webhook"), ("GET", "/api-prueba/telegram/mensajes"),
             ("POST", "/api-prueba/telegram/control"), ("POST", "/api-prueba/telegram/bot000000:EMULADOR/sendMessage"),
             ("GET", "/api/docs"), ("GET", "/api/openapi.json")]


@pytest.fixture
def prod_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "admin_password", PWD)
    for k, v in {"MONITOREO_ENTORNO": "produccion", "MONITOREO_SECRET_KEY": KEY, "MONITOREO_ADMIN_PASSWORD": PWD,
                 "MONITOREO_DATOS_DEMO": "0", "MONITOREO_HOST": "127.0.0.1"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("MONITOREO_TELEGRAM_API", raising=False)
    return f"sqlite:///{(tmp_path / 'prod.db').as_posix()}"


def _login(c, pwd=PWD):
    r = c.post("/api/auth/login", json={"username": "admin", "password": pwd})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.mark.parametrize("override,expected", [
    ({"MONITOREO_SECRET_KEY": ""}, "MONITOREO_SECRET_KEY"),
    ({"MONITOREO_SECRET_KEY": "corta"}, "MONITOREO_SECRET_KEY"),
    ({"MONITOREO_SECRET_KEY": "cambiame"}, "MONITOREO_SECRET_KEY"),
    ({"MONITOREO_ADMIN_PASSWORD": "12345"}, "MONITOREO_ADMIN_PASSWORD"),
    ({"MONITOREO_ADMIN_PASSWORD": "abcdef12345"}, "12 caracteres"),
    ({"MONITOREO_DATOS_DEMO": "1"}, "MONITOREO_DATOS_DEMO"),
    ({"MONITOREO_TELEGRAM_API": "http://127.0.0.1:8000/api-prueba/telegram"}, "MONITOREO_TELEGRAM_API"),
    ({"MONITOREO_HOST": "0.0.0.0"}, "MONITOREO_HOST"),
    ({"MONITOREO_ENTORNO": "produción-x"}, "MONITOREO_ENTORNO"),
])
def test_insecure_production_config_is_rejected(prod_env, monkeypatch, override, expected):
    for k, v in override.items():
        monkeypatch.setenv(k, v)
    r = entorno.review()
    assert any(expected in e for e in r.errores), r.as_dict()
    with pytest.raises(entorno.ConfigError):
        create_app(prod_env, start_engine=False)


def test_same_issues_are_only_warnings_in_development(prod_env, monkeypatch):
    monkeypatch.setenv("MONITOREO_ENTORNO", "desarrollo")
    monkeypatch.setenv("MONITOREO_SECRET_KEY", "corta")
    monkeypatch.setenv("MONITOREO_HOST", "0.0.0.0")
    r = entorno.review()
    assert not r.errores and len(r.avisos) >= 2


def test_host_on_network_allowed_only_behind_trusted_proxy(prod_env, monkeypatch):
    monkeypatch.setenv("MONITOREO_HOST", "0.0.0.0")
    monkeypatch.setenv("MONITOREO_PROXY_CONFIABLE", "10.0.0.5")
    assert not entorno.review().errores


def test_production_hides_lab_test_routes_docs_and_demo_data(prod_env, monkeypatch):
    monkeypatch.setenv("MONITOREO_DATOS_DEMO", "0")
    with TestClient(create_app(prod_env, start_engine=False, seed_demo=True)) as c:  # seed_demo pedido: se ignora
        h = _login(c)
        for method, path in LAB_PATHS:
            r = c.request(method, path, headers=h, json={})
            assert r.status_code in (404, 405), f"{method} {path} accesible en producción: {r.status_code}"
            assert r.status_code == 404 or method == "POST", (method, path, r.status_code)
        paths = [getattr(r, "path", "") for r in c.app.routes]
        assert not [x for x in paths if x.startswith(("/api/lab", "/api-prueba", "/api/docs", "/api/openapi"))]
        info = c.get("/api/info", headers=h).json()
        assert info == {**info, "entorno": "produccion", "laboratorio": False, "version": "1.2.0"}
        with c.app.state.db.session() as s:
            assert s.scalar(select(func.count()).select_from(Source)) == 0
            types = {ch.type for ch in s.scalars(select(Channel))}
            names = {ch.name for ch in s.scalars(select(Channel))}
        assert "simulado" not in types and "Webhook local" not in names
        assert c.get("/api/dashboard", headers=h).status_code == 200      # lo normal sigue funcionando
        assert c.get("/api/health").json()["ok"] is True


def test_development_keeps_lab_available(prod_env, monkeypatch):
    monkeypatch.setenv("MONITOREO_ENTORNO", "desarrollo")
    with TestClient(create_app(prod_env, start_engine=False, seed_demo=True)) as c:
        h = _login(c)
        assert c.get("/api/lab/items", headers=h).status_code == 200
        assert c.get("/api/docs").status_code == 200
        assert c.get("/api/info", headers=h).json()["laboratorio"] is True


def test_production_requires_admin_password_on_empty_database(prod_env, monkeypatch):
    monkeypatch.delenv("MONITOREO_ADMIN_PASSWORD")
    monkeypatch.setattr(settings, "admin_password", None)
    with pytest.raises(entorno.ConfigError):
        with TestClient(create_app(prod_env, start_engine=False)):
            pass
    assert not (settings.data_dir / "CREDENCIAL_INICIAL.txt").exists()


def test_internal_errors_are_hidden(prod_env):
    app = create_app(prod_env, start_engine=False)

    def boom():
        raise RuntimeError("detalle interno /ruta/secreta")
    app.add_api_route("/api/_fallo", boom)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/api/_fallo")
        assert r.status_code == 500 and r.json() == {"detail": "Error interno"}
        assert "secreta" not in r.text


def test_api_rate_limit_is_independent_from_source_limits(prod_env, monkeypatch):
    monkeypatch.setenv("MONITOREO_API_ESCRITURA_MIN", "3")
    with TestClient(create_app(prod_env, start_engine=False)) as c:
        h = _login(c)          # 1 escritura
        codes = [c.post("/api/auth/logout", headers={"Authorization": "Bearer x"}).status_code for _ in range(4)]
        assert codes[-1] == 429 and "Retry-After" in c.post("/api/auth/logout").headers
        assert c.get("/api/dashboard", headers=h).status_code == 200      # las lecturas siguen permitidas
        # El límite de la API no toca el límite de consulta de las fuentes (otro mecanismo):
        # con la API saturada, el motor sigue consultando con normalidad.
        from monitoreo.engine.clock import RealClock
        from monitoreo.engine.pipeline import RuntimeState, poll_source
        with c.app.state.db.session() as s:
            src = Source(code="p1", name="p1", region="NORTE", category="x", availability_type="x",
                         connector_type="simulacion", config={"feed": "p1"}, interval_s=60, rate_limit_per_min=30)
            s.add(src)
            s.flush()
            sid = src.id
        assert poll_source(c.app.state.db, sid, RealClock(), RuntimeState()).outcome == "OK"
        assert c.app.state.api_limiter.rejected >= 1


def test_rate_limiter_refills_over_time():
    t = [0.0]
    lim = ApiRateLimiter(per_min=60, writes_per_min=2, clock=lambda: t[0])
    assert lim.check("1.1.1.1", "POST") == 0 and lim.check("1.1.1.1", "POST") == 0
    assert lim.check("1.1.1.1", "POST") > 0              # tercera escritura: esperar
    assert lim.check("2.2.2.2", "POST") == 0             # otra IP no se ve afectada
    assert lim.check("1.1.1.1", "GET") == 0              # lecturas aparte
    t[0] += 30                                            # 30 s = 1 ficha de escritura
    assert lim.check("1.1.1.1", "POST") == 0
    off = ApiRateLimiter(per_min=0, writes_per_min=0, clock=lambda: 0)
    assert all(off.check("x", "POST") == 0 for _ in range(1000))


def test_production_logging_writes_rotating_file(prod_env):
    from monitoreo.__main__ import configure_logging
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        configure_logging("produccion")
        logging.getLogger("monitoreo").warning("prueba de registro")
        for hd in root.handlers:
            hd.flush()
        log = settings.data_dir / "logs" / "monitoreo.log"
        assert log.exists() and "prueba de registro" in log.read_text(encoding="utf-8")
    finally:
        for hd in root.handlers[:]:
            if hd not in before:
                root.removeHandler(hd)
                hd.close()


def test_logs_never_contain_telegram_token(prod_env):
    """Regresión v1.2: httpx registra la URL completa en INFO y la URL de Telegram lleva el token."""
    import httpx
    from monitoreo.__main__ import configure_logging
    root = logging.getLogger()
    before = list(root.handlers)
    secret = "987654321:AAFakeTokenSoloParaPruebas_xyz"
    try:
        configure_logging("produccion")
        cli = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True})))
        cli.post(f"https://api.telegram.org/bot{secret}/sendMessage", json={})
        logging.getLogger("monitoreo").error("fallo en https://api.telegram.org/bot%s/getMe", secret)
        for hd in root.handlers:
            hd.flush()
        text = (settings.data_dir / "logs" / "monitoreo.log").read_text(encoding="utf-8")
        assert "getMe" in text and secret not in text and "AAFake" not in text
    finally:
        for hd in root.handlers[:]:
            if hd not in before:
                root.removeHandler(hd)
                hd.close()
