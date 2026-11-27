"""E8: centro de revisiones pendientes en Monitor (integración visual del PR #13 de Network Engine).

- UNITARIAS: saneado de las revisiones, contador que cuenta TODAS, validación de la petición.
- CONTRA EL MOTOR REAL (NETWORK_ENGINE_REPO): marca de una sesión ya cerrada, visible en /api/red; resolución desde
  Monitor solo con rol admin y credencial «review» separada; nota y confirmación obligatorias; auditoría en ambos."""
import json
import time

from fastapi.testclient import TestClient

from monitoreo.app import create_app
from monitoreo.red_lectura import revisiones as R
from monitoreo.red_lectura import servicio as SV

from .captura_util import ADMIN_PWD
from .test_red_lectura import REPO_RED, Motor, real
from .test_red_servicio import _login


def ses(pend, activas=()):
    return {"schema": "cauce.sessions/1", "sessions": list(activas), "closed": [], "pending_human_review": pend}


def marca(i, general=False):
    return {"id": f"pm_{i:012x}", "kind": "general" if general else "session", "label": None if general else f"forms-{i}",
            "reason": "operation_interrupted", "age_s": 30.0 + i, "confirm_with": "GENERAL" if general else f"forms-{i}",
            "owner_token_id": "SECRETO-tk", "session_id": "bs_SECRETO"}


def test_revisiones_sin_sesion_se_cuentan_y_se_sanean():
    activa = {"label": "forms-x", "phase": "requiere_intervencion", "requires_human": True, "operation": "in_progress"}
    v = SV.ServicioRed._sanear_sesiones(ses([marca(1), marca(2, general=True)], [activa]))
    assert v["revisiones_pendientes"] == 3, "1 activa + 2 sin sesión (RC2 decía 1)"
    assert [r["tipo"] for r in v["revisiones"]] == ["sesion", "general"]
    assert v["revisiones"][0] == {"id": "pm_000000000001", "tipo": "sesion", "etiqueta": "forms-1",
                                  "motivo": "operation_interrupted", "hace_s": 31.0, "confirmar_con": "forms-1"}
    assert "SECRETO" not in json.dumps(v)


def test_contador_cuenta_todas_aunque_no_quepan_en_la_tabla():
    v = SV.ServicioRed._sanear_sesiones(ses([marca(i) for i in range(SV.MAX_REVISIONES + 7)]))
    assert (len(v["revisiones"]), v["revisiones_pendientes"]) == (SV.MAX_REVISIONES, SV.MAX_REVISIONES + 7)


def test_id_o_etiqueta_no_validos_no_se_muestran_tal_cual():
    m = marca(1)
    m.update(id="pm_../../x", label="<script>", confirm_with="<script>")
    r = SV.ServicioRed._sanear_sesiones(ses([m]))["revisiones"][0]
    assert (r["id"], r["etiqueta"], r["confirmar_con"]) == (None, None, None)


def test_validacion_de_la_peticion():
    assert R.validar("pm_000000000001", "ok", "forms-1") == "nota_obligatoria"
    assert R.validar("pm_000000000001", "x" * 501, "forms-1") == "nota_obligatoria"
    assert R.validar("pm_000000000001", "revisado", "  ") == "confirmacion_obligatoria"
    assert R.validar("../tokens", "revisado", "forms-1") == "id_invalido"
    assert R.validar("pm_000000000001", "revisado", "forms-1") is None


def test_sin_credencial_de_revisiones_no_se_habilita():
    try:
        R.cliente_revisiones({})
        raise AssertionError("debió negarse")
    except R.NoHabilitado as e:
        assert e.razon == "no_configurado"


def test_misma_ruta_que_la_de_lectura_se_rechaza(tmp_path):
    f = tmp_path / "t"
    f.write_text("cr1.x.y")
    try:
        R.cliente_revisiones({R.ARCHIVO_REVISIONES_ENV: str(f), "MONITOREO_NETWORK_TOKEN_FILE": str(f)})
        raise AssertionError("debió negarse")
    except R.NoHabilitado as e:
        assert e.razon == "misma_credencial"


# ============================================================================================ contra el motor real
def _marca_real(m):
    import sys
    sys.path.insert(0, REPO_RED)
    from cauce.client import CauceSessionClient
    adm = m.admin()
    t = adm.create_token("forms-a", "session")
    c = CauceSessionClient(t["token"], port=m.puerto)
    c.register("forms-a")
    time.sleep(0.6)
    c.heartbeat(ok=False, error_code="network_error", operation="in_progress")
    c.close()
    return adm


@real
def test_centro_de_revisiones_con_network_engine_real(tmp_path, monkeypatch):
    from monitoreo.config import settings
    (tmp_path / "red").mkdir()
    m = Motor(tmp_path / "red").arrancar()
    try:
        m.crear_credencial()
        adm = _marca_real(m)
        rev = tmp_path / "red" / "revisiones.token"
        r = m._cli("token", "create", "--name", "monitor-revisiones", "--scope", "review", "--out", str(rev))
        assert r.returncode == 0, r.stderr[-300:]
        monkeypatch.setattr(settings, "data_dir", tmp_path)
        monkeypatch.setattr(settings, "admin_password", ADMIN_PWD)
        for k, v in m.entorno().items():
            monkeypatch.setenv(k, v)
        monkeypatch.setenv("MONITOREO_NETWORK_DIR", REPO_RED)
        monkeypatch.delenv(R.ARCHIVO_REVISIONES_ENV, raising=False)
        c = TestClient(create_app(f"sqlite:///{(tmp_path / 'm.db').as_posix()}", start_engine=False, seed_demo=False))
        with c:
            h = _login(c)
            svc = c.app.state.red

            def esperar(cond, limite=40):
                t0 = time.time()
                while time.time() - t0 < limite:
                    e = c.get("/api/red", headers=h).json()
                    if cond(e):
                        return e
                    svc.lector._proximo = 0
                    time.sleep(0.3)
                raise AssertionError(json.dumps(e, ensure_ascii=False)[:2000])

            e = esperar(lambda e: (e.get("sesiones") or {}).get("revisiones"))
            s = e["sesiones"]
            assert (s["revisiones_pendientes"], s["requieren_persona"]) == (1, 0), "RC2 decía 0"
            rv = s["revisiones"][0]
            assert (rv["etiqueta"], rv["confirmar_con"]) == ("forms-a", "forms-a")
            assert e["revisiones_resolubles"] is False
            url = f"/api/red/revisiones/{rv['id']}/resolver"
            # 0) un operador (no administrador) no puede resolver
            c.post("/api/users", headers=h, json={"username": "opx", "password": "Operador-123", "role": "operador",
                                                  "regions": ["NORTE"]})
            ho = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": "opx", "password": "Operador-123"}).json()["token"]}
            assert c.post(url, headers=ho, json={"nota": "revisado en el portal", "confirmar": "forms-a"}).status_code == 403
            assert c.get("/api/red", headers=ho).json()["sesiones"]["revisiones_pendientes"] == 1, "el operador sí la ve"
            # 1) sin credencial de revisiones: no se resuelve; se da la orden para el equipo del motor
            r = c.post(url, headers=h, json={"nota": "revisado en el portal", "confirmar": "forms-a"})
            assert r.status_code == 409 and "cauce revisiones resolver" in r.json()["detail"]["orden"]
            # 2) con la credencial de LECTURA puesta como de revisiones: se rechaza
            monkeypatch.setenv(R.ARCHIVO_REVISIONES_ENV, m.entorno()["MONITOREO_NETWORK_TOKEN_FILE"])
            r = c.post(url, headers=h, json={"nota": "revisado en el portal", "confirmar": "forms-a"})
            assert r.status_code == 409 and r.json()["detail"]["razon"] == "misma_credencial"
            # 3) con la credencial «review»: nota obligatoria y confirmación exacta
            monkeypatch.setenv(R.ARCHIVO_REVISIONES_ENV, str(rev))
            assert c.post(url, headers=h, json={"nota": "ok", "confirmar": "forms-a"}).status_code == 400
            r = c.post(url, headers=h, json={"nota": "revisado en el portal", "confirmar": "forms-b"})
            assert r.status_code == 409 and r.json()["detail"]["codigo"] == "confirmation_mismatch"
            assert len(adm.pending_reviews()) == 1, "una confirmación equivocada no resuelve nada"
            r = c.post(url, headers=h, json={"nota": "revisado en el portal: no se envió", "confirmar": "forms-a"})
            assert r.status_code == 200, r.text
            assert adm.pending_reviews() == []
            assert c.get("/api/red", headers=h).json()["sesiones"]["revisiones_pendientes"] == 0
            r = c.post(url, headers=h, json={"nota": "otra vez", "confirmar": "forms-a"})
            assert r.status_code == 404, "resolver dos veces no hace nada"
            # auditoría en Monitor
            from monitoreo.models import AuditLog
            with c.app.state.db.session() as sdb:
                filas = [(a.action, a.detail.get("resultado")) for a in sdb.query(AuditLog).filter(
                    AuditLog.action == "red_revision_resolver")]
            assert ("red_revision_resolver", "resuelta") in filas and len(filas) >= 4
            texto = json.dumps(filas) + c.get("/api/red", headers=h).text
            assert "cr1." not in texto
    finally:
        m.parar()
