"""v1.4 (auditoría): sesiones al cambiar la contraseña y límites de los parámetros del motor."""
from .test_api import ADMIN_PWD, app_db, client, login  # noqa: F401  (fixture)


def test_password_change_closes_other_sessions_but_keeps_current(app_db):
    with client(app_db) as c:
        old = login(c)                       # p. ej. una sesión olvidada en otro equipo
        cur = login(c)
        r = c.post("/api/auth/password", headers=cur,
                   json={"current_password": ADMIN_PWD, "new_password": "Otra-Clave-456"})
        assert r.status_code == 200, r.text
        assert r.json()["sesiones_cerradas"] >= 1
        assert c.get("/api/auth/me", headers=old).status_code == 401
        assert c.get("/api/auth/me", headers=cur).status_code == 200


def test_engine_params_out_of_bounds_are_rejected(app_db):
    with client(app_db) as c:
        h = login(c)
        before = c.get("/api/settings/engine", headers=h).json()
        for bad in ({"tick_segundos": 0}, {"concurrencia_maxima": 100000},
                    {"enfriamiento_min_s": 100, "enfriamiento_max_s": 10}):
            r = c.put("/api/settings/engine", headers=h, json=bad)
            assert r.status_code == 422, (bad, r.text)
        assert c.get("/api/settings/engine", headers=h).json() == before   # nada se aplicó a medias
        assert c.put("/api/settings/engine", headers=h, json={"tick_segundos": 0.5}).status_code == 200


def test_log_filter_hides_telegram_token_in_httpx_lines():
    """httpx registra la URL de cada petición en INFO; con Telegram la URL lleva el token."""
    import logging

    from monitoreo.__main__ import RedactSecrets
    rec = logging.LogRecord("httpx", logging.INFO, __file__, 1, 'HTTP Request: %s %s "%s %d %s"',
                            ("POST", "https://api.telegram.org/bot123456:AAFicticio-Token_x/sendMessage",
                             "HTTP/1.1", 200, "OK"), None)
    assert RedactSecrets().filter(rec)
    msg = rec.getMessage()
    assert "AAFicticio" not in msg and "/bot<oculto>/sendMessage" in msg


def test_operator_cannot_cancel_a_request_another_operator_is_handling(app_db):
    from .test_asignaciones_abandonadas import _assigned
    with client(app_db) as c:
        h, ho, aid, rid = _assigned(c)
        assert c.post(f"/api/assignments/{aid}/tomar", headers=ho).status_code == 200
        c.post("/api/users", headers=h, json={"username": "opy", "password": "Operador-456", "role": "operador",
                                              "regions": ["OESTE"], "operator_name": "Operador Y"})
        hy = login(c, "opy", "Operador-456")
        r = c.post(f"/api/requests/{rid}/cancelar", headers=hy)
        assert r.status_code == 403
        assert c.get(f"/api/requests/{rid}", headers=h).json()["status"] == "EN_PROCESO"
        own = c.post("/api/requests", headers=hy, json={"code": "PROPIA-1", "holder": "Ficticio", "region": "OESTE",
                                                       "resource_type": "turno", "priority": 3}).json()
        assert c.post(f"/api/requests/{own['id']}/cancelar", headers=hy).status_code == 200   # la suya, sí
        assert c.post(f"/api/requests/{rid}/cancelar", headers=ho).status_code == 200         # la tiene asignada
