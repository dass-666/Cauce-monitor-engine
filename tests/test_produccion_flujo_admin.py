"""Preparación del flujo administrativo para producción (monitoreo/modulos.py).

Se demuestra:
- por omisión, la producción sigue igual que en la candidata RC1 + v2.4: sin flujo administrativo;
- con MONITOREO_PRODUCCION_FLUJO_ADMIN=1 el servidor NO arranca si faltan los requisitos (portal propio en
  este equipo, consentimiento exigido, copia externa del diario);
- con la activación y los requisitos, se montan las MISMAS rutas con los MISMOS controles de acceso
  (sin sesión 401, operador 403 en lo de administrador) y nada del laboratorio aparece;
- la interfaz recibe `flujo_admin` en /api/info.
Nada de esto habilita producción en ningún equipo: solo se prueba con TestClient y datos vacíos.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from monitoreo import entorno, modulos
from monitoreo.app import create_app

from .test_produccion import LAB_PATHS, _login, prod_env  # noqa: F401  (fixture)

OP_PWD = "Operador-Prod-2026"
FLUJO_ADMIN_PATHS = ["/api/admin/panel", "/api/admin/expedientes", "/api/admin/importaciones", "/api/planes",
                     "/api/observador", "/api/proveedor"]
FLUJO_OPERADOR_PATHS = ["/api/expedientes", "/api/ventanilla", "/api/consentimientos/catalogos",
                        "/api/captura/mi-trabajo"]
SOLO_LAB_EXTRA = [("GET", "/api/laboratorio/portal"), ("GET", "/api/conexiones"), ("GET", "/api/adaptadores"),
                  ("GET", "/api/capturas-manuales"), ("POST", "/api/observador/simular-caida-navegador")]


@pytest.fixture
def prod_flujo(prod_env, tmp_path_factory, monkeypatch):  # noqa: F811
    copia = tmp_path_factory.mktemp("copia_diario_externa")
    monkeypatch.setenv(modulos.VARIABLE, "1")
    monkeypatch.setenv("MONITOREO_EXIGIR_CONSENTIMIENTO", "1")
    monkeypatch.setenv("MONITOREO_DIARIO_COPIA", str(copia))
    monkeypatch.setenv("MONITOREO_PORTAL_URL", "http://127.0.0.1:8200")
    return prod_env


def test_por_omision_produccion_no_monta_el_flujo_administrativo(prod_env, monkeypatch):  # noqa: F811
    monkeypatch.delenv(modulos.VARIABLE, raising=False)
    with TestClient(create_app(prod_env, start_engine=False)) as c:
        h = _login(c)
        for p in FLUJO_ADMIN_PATHS + FLUJO_OPERADOR_PATHS:
            assert c.get(p, headers=h).status_code == 404, p
        info = c.get("/api/info", headers=h).json()
        assert info["flujo_admin"] is False and info["laboratorio"] is False
        assert c.get("/api/recuperacion", headers=h).status_code == 200      # la recuperación sí existe
        assert c.app.state.supervisor is None                                  # sin observadores


@pytest.mark.parametrize("override,esperado", [
    ({"MONITOREO_PORTAL_URL": "https://portal.example.com"}, "autorización expresa"),
    ({"MONITOREO_PORTAL_URL": "http://192.168.1.20:8200"}, "fuera de este equipo"),
    ({"MONITOREO_EXIGIR_CONSENTIMIENTO": "0"}, "MONITOREO_EXIGIR_CONSENTIMIENTO=1"),
    ({"MONITOREO_DIARIO_COPIA": ""}, "MONITOREO_DIARIO_COPIA"),
])
def test_activar_sin_requisitos_impide_arrancar(prod_flujo, monkeypatch, override, esperado):
    for k, v in override.items():
        monkeypatch.setenv(k, v)
    r = entorno.review()
    assert any(esperado in e for e in r.errores), r.as_dict()
    with pytest.raises(entorno.ConfigError):
        create_app(prod_flujo, start_engine=False)


def test_la_variable_no_cambia_nada_fuera_de_produccion(prod_flujo, monkeypatch):
    monkeypatch.setenv("MONITOREO_ENTORNO", "desarrollo")
    monkeypatch.setenv("MONITOREO_PORTAL_URL", "https://portal.example.com")
    assert not any(modulos.VARIABLE in e for e in entorno.review().errores + entorno.review().avisos)


def test_activado_monta_las_mismas_rutas_con_sus_controles_y_nada_del_laboratorio(prod_flujo):
    r = entorno.review()
    assert not r.errores and any(modulos.VARIABLE in a for a in r.avisos)
    with TestClient(create_app(prod_flujo, start_engine=False)) as c:
        h = _login(c)
        assert c.post("/api/users", headers=h, json={"username": "opprod", "password": OP_PWD, "role": "operador",
                                                     "regions": ["NORTE"]}).status_code == 201
        ho = _login_op(c)
        for p in FLUJO_ADMIN_PATHS:
            assert c.get(p).status_code == 401, p                       # sin sesión
            assert c.get(p, headers=ho).status_code == 403, p           # operador: solo administrador
            assert c.get(p, headers=h).status_code == 200, p
        for p in FLUJO_OPERADOR_PATHS:
            assert c.get(p).status_code == 401, p
            assert c.get(p, headers=h).status_code == 200, p
        for method, path in LAB_PATHS + SOLO_LAB_EXTRA:
            st = c.request(method, path, headers=h, json={}).status_code
            assert st in (404, 405), f"{method} {path} accesible en producción: {st}"
        paths = [getattr(x, "path", "") for x in c.app.routes]
        assert not [x for x in paths if x.startswith(("/api/lab", "/api-prueba", "/api/laboratorio", "/api/conexiones",
                                                      "/api/adaptadores", "/api/docs"))]
        assert "/api/observador/simular-caida-navegador" not in paths
        info = c.get("/api/info", headers=h).json()
        assert info["flujo_admin"] is True and info["laboratorio"] is False
        assert c.app.state.modulos["flujo_admin_modulos"]
        assert c.app.state.supervisor is not None
        # la recuperación sigue siendo solo de administrador
        assert c.get("/api/recuperacion", headers=ho).status_code == 403


def _login_op(c):
    r = c.post("/api/auth/login", json={"username": "opprod", "password": OP_PWD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_desarrollo_conserva_las_herramientas_de_laboratorio(prod_env, monkeypatch):  # noqa: F811
    monkeypatch.setenv("MONITOREO_ENTORNO", "desarrollo")
    with TestClient(create_app(prod_env, start_engine=False)) as c:
        h = _login(c)
        assert c.post("/api/observador/simular-caida-navegador", headers=h).status_code == 200
        assert c.get("/api/admin/panel", headers=h).status_code == 200
        info = c.get("/api/info", headers=h).json()
        assert info["flujo_admin"] is True and info["laboratorio"] is True
