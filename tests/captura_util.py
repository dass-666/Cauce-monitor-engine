"""Utilidades de las pruebas v1.5 (portal de prueba como proceso + motor sin planificador)."""
from __future__ import annotations

import itertools
import random

from fastapi.testclient import TestClient

from monitoreo.app import create_app
from monitoreo.captura import seguimiento
from monitoreo.engine.clock import RealClock
from monitoreo.engine.pipeline import RuntimeState, maintenance, poll_source, process_pending_events
from monitoreo.models import Source

ADMIN_PWD = "Admin-Prueba-123"
OP_PWD = "Operador-123"
_n = itertools.count(1)


def letras(n: int) -> str:
    out = ""
    while True:
        n, r = divmod(n, 26)
        out = chr(65 + r) + out
        if n == 0:
            return out.capitalize()


def siguiente_registro() -> int:
    return next(_n)


class Lab:
    """Motor (API + ciclo manual) apuntando a un portal de prueba que corre en otro proceso."""

    def __init__(self, url: str, portal, operadores=("op1", "op2"), regiones=("NORTE",)):
        self.portal = portal
        self.c = TestClient(create_app(url, start_engine=False, seed_demo=False))
        self.c.__enter__()
        self.db = self.c.app.state.db
        self.h = self.login("admin", ADMIN_PWD)
        self.clock, self.runtime, self.rng = RealClock(), RuntimeState(), random.Random(3)
        self.ops = {}
        for u in operadores:
            r = self.c.post("/api/users", headers=self.h, json={"username": u, "password": OP_PWD, "role": "operador",
                                                                 "regions": list(regiones), "operator_name": f"Operador {u}"})
            assert r.status_code == 201, r.text
            self.ops[u] = self.login(u, OP_PWD)
        with self.db.session() as s:
            src = Source(code="portal-lab", name="Portal de reservaciones de prueba", region="CENTRO",
                         availability_type="calendario", connector_type="portal_reservas",
                         config={"url_base": portal.base, "token_env": "MONITOREO_PORTAL_TOKEN", "timeout_s": 5},
                         interval_s=1, jitter_pct=0, rate_limit_per_min=600)
            s.add(src)
            s.flush()
            self.source_id = src.id

    def cerrar(self):
        self.c.__exit__(None, None, None)

    def login(self, u, p):
        r = self.c.post("/api/auth/login", json={"username": u, "password": p})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}

    def ciclo(self):
        """Una vuelta del motor: consultar el portal, procesar eventos, mantenimiento y seguimiento."""
        with self.db.session() as s:
            s.get(Source, self.source_id).next_poll_at = None
        poll_source(self.db, self.source_id, self.clock, self.runtime, self.rng)
        process_pending_events(self.db, self.clock)
        maintenance(self.db, self.clock)
        return seguimiento.mantenimiento(self.db, self.clock.now())

    def datos_expediente(self, n: int | None = None, region="NORTE", **kw):
        n = n or siguiente_registro()
        reg = self.portal.registro(n)
        d = {"nombre": f"Persona Ficticia {letras(n)}", "fecha_nacimiento": reg["fecha_nacimiento"],
             "telefono": f"555{n:07d}", "telefono_confirmacion": f"555{n:07d}",
             "correo": f"persona{n}@ejemplo.test", "correo_confirmacion": f"persona{n}@ejemplo.test",
             "region": region, "tipo_tramite": reg["tramite"], "id_registro_previo": reg["identificador"]}
        d.update(kw)
        return d

    def crear(self, h=None, **kw):
        r = self.c.post("/api/expedientes", headers=h or self.h, json=self.datos_expediente(**kw))
        assert r.status_code == 201, r.text
        return r.json()

    def operador_de(self, eid) -> str:
        with self.db.session() as s:
            from monitoreo.models import Expediente, Operator, User
            e = s.get(Expediente, eid)
            op = s.get(Operator, e.operator_id)
            return s.get(User, op.user_id).username

    def hasta_confirmar(self, eid, h):
        """Recorrido del operador: preparar -> revisar -> enviar -> calendario -> horario -> confirmar."""
        r = self.c.post(f"/api/expedientes/{eid}/preparar", headers=h)
        assert r.status_code == 200, r.text
        rid = r.json()["reservacion"]["id"]
        r = self.c.post(f"/api/reservaciones/{rid}/enviar-datos", headers=h, json={"revisado": True})
        assert r.status_code == 200, r.text
        hs = r.json()["calendario"]["horarios"]
        assert hs, "el calendario debería tener horarios libres"
        r = self.c.post(f"/api/reservaciones/{rid}/seleccionar", headers=h, json={"horario": hs[0]["id"]})
        assert r.status_code == 200, r.text
        return rid, hs
