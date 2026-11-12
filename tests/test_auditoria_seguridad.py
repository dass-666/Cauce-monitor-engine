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
