"""v1.6: política de retención y privacidad (resuelve el riesgo abierto de la v1.5).
SQLite y PostgreSQL. Comprueba que nunca se toca una operación activa y que, tras anonimizar,
los datos personales ya no están en ninguna tabla ni en los registros."""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from monitoreo import migrate
from monitoreo.app import create_app
from monitoreo.captura import expedientes as ex
from monitoreo.captura import retencion
from monitoreo.config import settings
from monitoreo.db import utcnow
from monitoreo.engine.params import set_params
from monitoreo.models import AuditLog, Expediente, Reservacion

BASE = {"fecha_nacimiento": "1991-02-03", "telefono": "5550001111", "telefono_confirmacion": "5550001111",
        "correo": "ret@ejemplo.test", "correo_confirmacion": "ret@ejemplo.test", "region": "NORTE",
        "tipo_tramite": "alta", "id_registro_previo": "REG-RET-1", "modo": "POR_DIA", "sucursal": "NOR-1"}
NOMBRES = ["Ana Retencion Uno", "Beto Retencion Dos", "Caro Retencion Tres", "Dani Retencion Cuatro",
           "Eli Retencion Cinco", "Fer Retencion Seis", "Gabi Retencion Siete", "Hugo Retencion Ocho"]


def _exp(db, nombre, estado, dias, reservacion=None, **kw):
    now = utcnow()
    d = {**BASE, "nombre": nombre, "id_registro_previo": "REG-" + nombre.split()[0].upper(), **kw}
    out = ex.crear_seguro(db, d, "admin", now)
    with db.session() as s:
        e = s.get(Expediente, out["id"])
        e.estado = estado
        if estado in ex.CERRADOS:
            e.dedup_activo = None
        e.updated_at = now - timedelta(days=dias)
        if reservacion:
            s.add(Reservacion(expediente_id=e.id, viva_expediente_id=e.id if reservacion != "CONFIRMADA" else e.id,
                              estado=reservacion, modo="POR_DIA", clave=f"cl-{e.id}",
                              folio=f"DIA-X-{e.id}" if reservacion == "CONFIRMADA" else None,
                              created_at=now, updated_at=now))
    return out["id"]


def test_politica_anonimiza_borra_y_respeta_operaciones_activas(db, caplog):
    caplog.set_level(logging.DEBUG)
    with db.session() as s:
        set_params(s, {"retencion_accion": "BORRAR", "retencion_confirmados_dias": 90, "retencion_cerrados_dias": 30,
                       "retencion_incompletos_dias": 60})
    conf = _exp(db, NOMBRES[0], ex.RES_CONFIRMADA, 100, "CONFIRMADA")
    cerr_sin = _exp(db, NOMBRES[1], ex.CERRADO, 40)
    cerr_con = _exp(db, NOMBRES[2], ex.CERRADO, 40, "CANCELADA")
    inc = _exp(db, NOMBRES[3], ex.INCOMPLETO, 70)
    activo = _exp(db, NOMBRES[4], ex.CAPTURA, 400)
    verif = _exp(db, NOMBRES[5], ex.REVISION, 400, "EN_VERIFICACION")
    listo = _exp(db, NOMBRES[6], ex.LISTO, 400)
    reciente = _exp(db, NOMBRES[7], ex.CERRADO, 5)
    now = utcnow()
    prev = retencion.aplicar(db, now, actor="admin", simular=True)
    assert prev["simulado"] and {p["codigo"] for p in prev["expedientes"]} == {
        f"EXP-{i:06d}" for i in (conf, cerr_sin, cerr_con, inc)}
    with db.session() as s:
        assert s.get(Expediente, conf).anonimizado_en is None          # la vista previa no cambia nada
    res = retencion.aplicar(db, now, actor="admin")
    assert (res["anonimizados"], res["borrados"], res["cerrados"]) == (3, 1, 1), res
    with db.session() as s:
        assert s.get(Expediente, cerr_sin) is None                     # sin reservaciones: se borra
        for i in (conf, cerr_con, inc):                                # con evidencia: se anonimiza
            e = s.get(Expediente, i)
            assert e.anonimizado_en and e.nombre == retencion.ANONIMO and e.telefono is None and e.correo is None
            assert e.fecha_nacimiento is None and e.id_registro_previo is None
        assert s.get(Expediente, inc).estado == "CERRADO" and s.get(Expediente, inc).motivo_cierre == "RETENCION"
        assert s.get(Reservacion, s.scalar(select(Reservacion.id).where(Reservacion.expediente_id == conf))).folio
        for i in (activo, verif, listo, reciente):                     # intactos
            e = s.get(Expediente, i)
            assert e.anonimizado_en is None and e.telefono == "5550001111"
        blob = json.dumps([[str(v) for v in r] for t in ("expedientes", "expediente_eventos", "audit_log")
                           for r in s.execute(text(f"SELECT * FROM {t}"))])
        acciones = [a.action for a in s.scalars(select(AuditLog))]
    for n in NOMBRES[:4]:
        assert n not in blob and n not in caplog.text
    assert "retencion_borrado" in acciones and "retencion_aplicada" in acciones
    # idempotente: una segunda pasada no hace nada
    assert retencion.aplicar(db, now)["anonimizados"] == 0


def test_respaldos_protegidos_y_purgados(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    url = f"sqlite:///{(tmp_path / 'r.db').as_posix()}"
    from monitoreo.db import Database
    d = Database(url)
    d.create_all()
    rutas = [migrate.backup(url, f"p{i}") for i in range(6)]
    if os.name != "nt":
        assert oct(os.stat(rutas[0]).st_mode & 0o777) == "0o600"
        assert oct(os.stat(retencion.directorio_respaldos()).st_mode & 0o777) == "0o700"
    viejo = time.time() - 40 * 86400
    for i, r in enumerate(rutas):
        os.utime(r, (viejo + i, viejo + i))                              # todos "viejos"
    out = retencion.purgar_respaldos(utcnow(), 30)
    assert out["borrados"] == 3 and out["conservados"] == 3              # siempre quedan los 3 últimos
    assert sorted(p.name for p in retencion.directorio_respaldos().iterdir()) == sorted(
        os.path.basename(r) for r in rutas[3:])
    d.engine.dispose()


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "admin_password", "Admin-Prueba-123")
    with TestClient(create_app(f"sqlite:///{(tmp_path / 'a.db').as_posix()}", start_engine=False, seed_demo=False)) as c:
        tok = c.post("/api/auth/login", json={"username": "admin", "password": "Admin-Prueba-123"}).json()["token"]
        yield c, {"Authorization": f"Bearer {tok}"}


def test_exportacion_protegida_y_auditada(api):
    c, h = api
    assert c.post("/api/expedientes", headers=h, json={**BASE, "nombre": "Irene Export Prueba"}).status_code == 201
    c.post("/api/users", headers=h, json={"username": "op1", "password": "Operador-123", "role": "operador"})
    ho = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": "op1", "password": "Operador-123"}
                                              ).json()["token"]}
    assert c.get("/api/expedientes-exportar", headers=ho).status_code == 403
    assert c.get("/api/captura/retencion", headers=ho).status_code == 403
    r = c.get("/api/expedientes-exportar", headers=h)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert "Irene" not in r.text and "5550001111" not in r.text and "••••••1111" in r.text   # enmascarada
    assert c.get("/api/expedientes-exportar", headers=h, params={"completo": True}).status_code == 422
    r = c.get("/api/expedientes-exportar", headers=h, params={"completo": True, "motivo": "auditoría trimestral"})
    assert r.status_code == 200 and "Irene Export Prueba" in r.text
    aud = [a for a in c.get("/api/audit", headers=h).json() if a["accion"] == "expedientes_exportados"]
    assert len(aud) == 2 and "Irene" not in json.dumps(aud) and "5550001111" not in json.dumps(aud)
    # la aplicación manual exige confirmación expresa y queda auditada
    assert c.post("/api/captura/retencion/aplicar", headers=h, json={}).status_code == 422
    assert c.post("/api/captura/retencion/aplicar", headers=h, json={"confirmo": True}).status_code == 200
    assert any(a["accion"] == "retencion_manual" for a in c.get("/api/audit", headers=h).json())
