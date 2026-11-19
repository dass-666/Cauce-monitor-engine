"""Observabilidad (v1.1): estado del motor, regiones, fuentes y diagnóstico de por qué una
fuente no se actualiza."""
from datetime import timedelta

from sqlalchemy import select

from monitoreo.db import utcnow
from monitoreo.models import Channel, SchedulerLease, Source

from .test_api import app_db, client, login  # noqa: F401


def _first_source(c):
    with c.app.state.db.session() as s:
        return s.scalars(select(Source).order_by(Source.id)).first().id


def _lease(c, holder="otra-maquina:1:abc", alive=True):
    now = utcnow()
    with c.app.state.db.session() as s:
        s.merge(SchedulerLease(name="planificador", holder=holder, acquired_at=now, renewed_at=now,
                               expires_at=now + timedelta(seconds=30 if alive else -30), fencing_token=1))


def test_observability_payload(app_db):
    with client(app_db) as c:
        h = login(c)
        assert c.get("/api/observabilidad").status_code == 401
        d = c.get("/api/observabilidad", headers=h).json()
        assert len(d["regiones"]) == 5
        assert d["fuentes"] and {"segundos_desde_ok", "desactualizada", "errores_seguidos",
                                 "latencia_deteccion_ms"} <= set(d["fuentes"][0])
        assert {"solicitudes_en_espera", "entregas_pendientes", "alertas_sin_confirmar"} <= set(d["colas"])
        assert d["motor"]["rol"] == "SIN_MOTOR" and d["motor"]["lider_global"] is None
        assert d["proceso"]["memoria_mb"] > 0
        assert "token" not in str(d["telegram"]).lower() or d["telegram"].get("token_configurado") is not None
        # Nunca se consultó: todas las fuentes activas figuran como desactualizadas.
        assert all(f["desactualizada"] for f in d["fuentes"] if f["status"] == "ACTIVA")


def test_diagnosis_no_leader_then_suspended(app_db):
    with client(app_db) as c:
        h = login(c)
        sid = _first_source(c)
        d = c.get(f"/api/diagnostico/fuentes/{sid}", headers=h).json()
        assert d["veredicto"] == "CRITICO"
        assert d["causas"][0]["causa"].startswith("El planificador no está activo")
        _lease(c)
        assert c.post(f"/api/sources/{sid}/suspender", headers=h).status_code == 200
        d = c.get(f"/api/diagnostico/fuentes/{sid}", headers=h).json()
        causas = [x["causa"] for x in d["causas"]]
        assert "Suspendida manualmente" in causas
        sus = next(x for x in d["causas"] if x["causa"] == "Suspendida manualmente")
        assert "admin" in sus["detalle"]
        assert d["motivo_estado"].startswith("Suspendida manualmente por admin")
        assert c.get("/api/diagnostico/fuentes/99999", headers=h).status_code == 404


def test_diagnosis_circuit_and_retry_after(app_db):
    with client(app_db) as c:
        h = login(c)
        sid = _first_source(c)
        _lease(c)
        now = utcnow()
        with c.app.state.db.session() as s:
            src = s.get(Source, sid)
            src.circuit_state, src.circuit_opened_at, src.consecutive_failures = "ABIERTO", now, 3
            src.last_outcome, src.next_poll_at = "LIMITADO", now + timedelta(seconds=120)
            src.last_success_at = now - timedelta(hours=1)
        d = c.get(f"/api/diagnostico/fuentes/{sid}", headers=h).json()
        causas = [x["causa"] for x in d["causas"]]
        assert "Circuito abierto (pausa automática)" in causas
        assert "La fuente pidió bajar el ritmo" in causas
        assert "Sin datos frescos" in causas
        assert d["veredicto"] == "AVISO"


def test_diagnosis_ok_when_healthy(app_db):
    with client(app_db) as c:
        h = login(c)
        sid = _first_source(c)
        _lease(c)
        r = c.post(f"/api/sources/{sid}/consultar", headers=h)
        assert r.status_code == 200 and r.json()["resultado"] == "OK", r.text
        d = c.get(f"/api/diagnostico/fuentes/{sid}", headers=h).json()
        assert d["veredicto"] in ("OK", "AVISO"), d  # AVISO solo si la fuente simulada respondió vacía
        assert d["ultimas_consultas"][0]["outcome"] == "OK"
        st = c.get("/api/estadisticas?group=region&days=1", headers=h).json()
        assert sum(x["consultas"] for x in st) >= 1
        assert c.get("/api/estadisticas?group=otro", headers=h).status_code == 422


def test_channel_test_admin_only_and_safe(app_db, monkeypatch):
    monkeypatch.setenv("MONITOREO_TELEGRAM_TOKEN", "999:VALOR-SECRETO")
    monkeypatch.delenv("MONITOREO_TELEGRAM_CHATS", raising=False)
    with client(app_db) as c:
        h = login(c)
        c.post("/api/users", headers=h, json={"username": "op2", "password": "Operador-123", "role": "operador"})
        ho = login(c, "op2", "Operador-123")
        with c.app.state.db.session() as s:
            ids = {ch.type: ch.id for ch in s.scalars(select(Channel))}
        assert c.post(f"/api/channels/{ids['registro']}/probar", headers=ho).status_code == 403
        r = c.post(f"/api/channels/{ids['registro']}/probar", headers=h).json()
        assert r["ok"] is True
        r = c.post(f"/api/channels/{ids['telegram']}/probar", headers=h).json()
        assert r["ok"] is False and "VALOR-SECRETO" not in r["detalle"]   # sin destinatarios autorizados
        obs = c.get("/api/observabilidad", headers=h).text
        assert "VALOR-SECRETO" not in obs


def test_observability_with_running_engine_does_not_block(app_db):
    """Regresión: con el motor en marcha, /api/observabilidad abría una sesión anidada
    (scheduler.status -> lease.info) y quedaba bloqueada 30 s en SQLite."""
    import time
    from fastapi.testclient import TestClient
    from monitoreo.app import create_app
    with TestClient(create_app(app_db, start_engine=True, seed_demo=True)) as c:
        h = login(c)
        deadline = time.time() + 10
        while time.time() < deadline:
            d = c.get("/api/observabilidad", headers=h).json()
            if d["motor"].get("rol") == "LIDER":
                break
            time.sleep(0.3)
        t0 = time.perf_counter()
        d = c.get("/api/observabilidad", headers=h).json()
        assert d["motor"]["rol"] == "LIDER" and d["motor"]["lider_global"]["vigente"]
        assert time.perf_counter() - t0 < 5
        sid = d["fuentes"][0]["id"]
        dg = c.get(f"/api/diagnostico/fuentes/{sid}", headers=h).json()
        assert dg["motor"]["rol"] == "LIDER"
