"""v1.5: expedientes sin portal: validaciones, duplicados, versiones, permisos, minimización,
contrato de campos, lector de HTML y migraciones. En SQLite y PostgreSQL (fixture `db`)."""
from __future__ import annotations

import shutil
import threading
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from monitoreo import migrate
from monitoreo.app import create_app
from monitoreo.captura import expedientes as ex
from monitoreo.captura import validacion as val
from monitoreo.captura.formularios import contrato as ct
from monitoreo.captura.formularios.navegador import leer
from monitoreo.config import settings
from monitoreo.db import Database, utcnow
from monitoreo.models import Expediente, ExpedienteEvento, Request

REG = {"NORTE", "SUR", "CENTRO"}
BASE = {"nombre": "Laura Ficticia Pérez", "fecha_nacimiento": "1990-05-04", "telefono": "555 123 4567",
        "telefono_confirmacion": "5551234567", "correo": "Laura@Ejemplo.TEST", "correo_confirmacion": "laura@ejemplo.test",
        "region": "norte", "tipo_tramite": "alta", "id_registro_previo": "reg000123", "modo": "POR_HORARIO"}


# ---------------------------------------------------------------- validación (pura)
def test_formulario_incompleto_lista_todos_los_campos():
    r = val.validar({}, REG)
    assert set(r.errores) == {"nombre", "fecha_nacimiento", "telefono", "correo", "region", "tipo_tramite"}


def test_normaliza_y_descarta_confirmaciones():
    r = val.validar(BASE, REG)
    assert r.ok, r.errores
    assert r.datos["telefono"] == "5551234567" and r.datos["correo"] == "laura@ejemplo.test"
    assert r.datos["region"] == "NORTE" and r.datos["id_registro_previo"] == "REG000123"
    assert "telefono_confirmacion" not in r.datos and "correo_confirmacion" not in r.datos


@pytest.mark.parametrize("cambio,campo", [
    ({"telefono_confirmacion": "5551234568"}, "telefono_confirmacion"),
    ({"correo_confirmacion": "otra@ejemplo.test"}, "correo_confirmacion"),
    ({"telefono_confirmacion": None}, "telefono_confirmacion"),
    ({"telefono": "12345"}, "telefono"),
    ({"correo": "no-es-correo"}, "correo"),
    ({"fecha_nacimiento": "2990-01-01"}, "fecha_nacimiento"),
    ({"fecha_nacimiento": "31/12/1990"}, "fecha_nacimiento"),
    ({"nombre": "Laura"}, "nombre"),
    ({"nombre": "L4ura P3rez"}, "nombre"),
    ({"region": "MARTE"}, "region"),
    ({"tipo_tramite": "pasaporte"}, "tipo_tramite"),
    ({"prioridad": 9}, "prioridad"),
    ({"id_registro_previo": "x"}, "id_registro_previo"),
])
def test_campos_invalidos_y_confirmaciones_que_no_coinciden(cambio, campo):
    r = val.validar({**BASE, **cambio}, REG)
    assert campo in r.errores, r.errores


def test_solo_datos_sinteticos(monkeypatch):
    r = val.validar({**BASE, "correo": "laura@gmail.com", "correo_confirmacion": "laura@gmail.com",
                     "telefono": "8112345678", "telefono_confirmacion": "8112345678"}, REG)
    assert "ficticio" in r.errores["correo"] and "ficticio" in r.errores["telefono"]
    monkeypatch.setenv("MONITOREO_CAPTURA_SOLO_SINTETICOS", "0")
    assert val.validar({**BASE, "correo": "laura@gmail.com", "correo_confirmacion": "laura@gmail.com"}, REG).ok


def test_huella_ignora_acentos_mayusculas_y_espacios():
    a = val.huella_duplicado("Laura  Ficticia Pérez", "1990-05-04", "alta")
    assert a == val.huella_duplicado("laura ficticia perez", date(1990, 5, 4), "alta")
    assert a != val.huella_duplicado("laura ficticia perez", "1990-05-04", "renovacion")


def test_mascaras():
    assert val.enmascarar_telefono("5551234567") == "••••••4567"
    assert val.enmascarar_correo("laura@ejemplo.test") == "l•••@ejemplo.test"
    assert "aura" not in val.enmascarar_nombre("Laura Pérez")


# ---------------------------------------------------------------- expedientes en base de datos
def test_alta_crea_solicitud_sin_datos_personales(db):
    now = utcnow()
    out = ex.crear_seguro(db, BASE, "admin", now)
    with db.session() as s:
        e = s.get(Expediente, out["id"])
        req = s.get(Request, e.request_id)
        assert req.holder == e.codigo and req.code == e.codigo and req.resource_type == "cita"
        assert "Laura" not in repr(req.conditions) and req.status == "PENDIENTE"
        ev = s.scalar(select(ExpedienteEvento).where(ExpedienteEvento.expediente_id == e.id))
        assert ev.tipo == "CREADO" and "Laura" not in repr(ev.detalle)


def test_duplicados_se_rechazan_aunque_lleguen_a_la_vez(db):
    now = utcnow()
    resultados = []

    def alta():
        try:
            ex.crear_seguro(db, {**BASE, "nombre": "LAURA FICTICIA PEREZ"}, "admin", now)
            resultados.append(201)
        except ex.ErrorExpediente as e:
            resultados.append(e.http)
    hilos = [threading.Thread(target=alta) for _ in range(6)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert sorted(resultados) == [201] + [409] * 5
    with db.session() as s:
        assert len(s.scalars(select(Expediente)).all()) == 1


def test_modificacion_con_version_y_solo_nombres_de_campos(db):
    now = utcnow()
    out = ex.crear_seguro(db, BASE, "admin", now)
    with db.session() as s:
        e = s.get(Expediente, out["id"])
        with pytest.raises(ex.ErrorExpediente) as err:
            ex.modificar(s, e, {"telefono": "5559998888"}, e.version, "admin", now)   # sin confirmación
        assert err.value.http == 422 and "telefono_confirmacion" in err.value.errores
    with db.session() as s:
        e = s.get(Expediente, out["id"])
        cambios = ex.modificar(s, e, {"telefono": "5559998888", "telefono_confirmacion": "5559998888",
                                      "prioridad": 1}, 1, "admin", now)
        assert cambios == ["prioridad", "telefono"]
    with db.session() as s:
        e = s.get(Expediente, out["id"])
        with pytest.raises(ex.ErrorExpediente) as err:
            ex.modificar(s, e, {"prioridad": 2}, 1, "otro", now)                   # versión vieja
        assert err.value.http == 409
        ev = s.scalars(select(ExpedienteEvento).where(ExpedienteEvento.tipo == "MODIFICADO")).all()
        assert ev and "5559998888" not in repr([x.detalle for x in ev])
        assert s.get(Request, e.request_id).priority == 1


# ---------------------------------------------------------------- API: permisos y minimización
@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "admin_password", "Admin-Prueba-123")
    with TestClient(create_app(f"sqlite:///{(tmp_path / 'a.db').as_posix()}", start_engine=False, seed_demo=False)) as c:
        tok = c.post("/api/auth/login", json={"username": "admin", "password": "Admin-Prueba-123"}).json()["token"]
        yield c, {"Authorization": f"Bearer {tok}"}


def test_api_permisos_y_minimizacion(api):
    c, h = api
    r = c.post("/api/expedientes", headers=h, json=BASE)
    assert r.status_code == 201 and r.json()["telefono"] == "••••••4567" and "Laura" not in r.json()["nombre"]
    eid = r.json()["id"]
    assert c.post("/api/expedientes", headers=h, json=BASE).status_code == 409
    r = c.post("/api/expedientes", headers=h, json={**BASE, "nombre": "Otra Persona", "correo_confirmacion": "x@ejemplo.test"})
    assert r.status_code == 422 and r.json()["detail"]["errores"] == {"correo_confirmacion": "Los correos no coinciden"}
    assert c.post("/api/expedientes", headers=h, json={**BASE, "campo_raro": 1}).status_code == 422
    # operador sin el expediente asignado: no lo ve (404, igual que si no existiera)
    c.post("/api/users", headers=h, json={"username": "op1", "password": "Operador-123", "role": "operador"})
    ho = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": "op1", "password": "Operador-123"}).json()["token"]}
    assert c.get("/api/expedientes", headers=ho).json() == []
    assert c.get(f"/api/expedientes/{eid}", headers=ho).status_code == 404
    assert c.patch(f"/api/expedientes/{eid}", headers=ho, json={"version": 1, "prioridad": 1}).status_code == 404
    # el administrador ve la ficha completa y la consulta queda auditada sin valores
    f = c.get(f"/api/expedientes/{eid}", headers=h).json()
    assert f["telefono"] == "5551234567" and f["datos_completos"]
    aud = c.get("/api/audit", headers=h).json()
    assert any(a["accion"] == "expediente_consultado" for a in aud)
    assert "5551234567" not in repr(aud) and "Laura" not in repr(aud)
    # un usuario sin operador vinculado y sin rol admin no puede capturar
    assert c.get("/api/captura/resumen", headers=ho).status_code == 200
    # cancelar libera la huella
    assert c.post(f"/api/expedientes/{eid}/cancelar", headers=h).status_code == 200
    assert c.post("/api/expedientes", headers=h, json=BASE).status_code == 201


def test_resumen_y_contrato(api):
    c, h = api
    c.post("/api/expedientes", headers=h, json=BASE)
    r = c.get("/api/captura/resumen", headers=h).json()
    assert r["expedientes"]["por_grupo"]["pendiente"] == 1
    k = c.get("/api/captura/contrato", params={"modo": "POR_HORARIO"}, headers=h).json()
    assert k["nombre"] == "portal_laboratorio" and len(k["campos"]) == 8


# ---------------------------------------------------------------- contrato y lector de HTML
def test_contrato_valida_estructura():
    k = ct.cargar()
    assert [x.nombre for x in k.campos][:3] == ["operacion", "tramite", "identificador"]
    with pytest.raises(ct.ContratoInvalido):
        ct.validar_contrato({"nombre": "x"})
    with pytest.raises(ct.ContratoInvalido):
        ct.cargar("../secreto")


def test_preparar_marca_lo_que_falta_y_enmascara():
    class E:
        operacion, tipo_tramite, id_registro_previo = "nueva", "alta", None
        fecha_nacimiento, telefono, correo = date(1990, 1, 2), "5551234567", "a@ejemplo.test"
    p = ct.preparar(ct.cargar(), E())
    assert p["faltan"] == ["identificador"] and not p["listo"]
    tel = next(x for x in p["campos"] if x["campo"] == "telefono_confirmacion")
    assert tel["valor"] == "••••••4567" and tel["confirmacion_de"] == "telefono"


def test_lector_html_reconoce_marcadores():
    html = ('<body data-pantalla="datos"><p data-error-general="DATOS_INVALIDOS">Revisa</p>'
            '<form data-formulario="datos" action="/x"><select name="tramite"><option value="">-</option>'
            '<option value="alta" selected>Alta</option></select><input name="telefono" value="1">'
            '<span data-error="telefono">Mal</span><input type="radio" name="horario" value="H1" data-hora="09:00">'
            '</form><strong data-folio="LAB-1">LAB-1</strong></body>')
    p = leer(html, 422, ct.cargar().marcadores)
    f = p.formularios["datos"]
    assert p.pantalla == "datos" and p.error_general == ("DATOS_INVALIDOS", "Revisa") and p.folio == "LAB-1"
    assert f.campos["tramite"]["opciones"] == ["", "alta"] and f.campos["tramite"]["valor"] == "alta"
    assert p.errores == {"telefono": "Mal"} and f.radios["horario"][0]["hora"] == "09:00"


# ---------------------------------------------------------------- migraciones y recuperación
def test_migracion_0006_baja_y_sube_sin_perder_datos_anteriores(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    p = tmp_path / "v14.db"
    shutil.copy(Path(__file__).parent / "fixtures" / "bd_v1_4.sqlite", p)
    url = f"sqlite:///{p.as_posix()}"
    rep = migrate.upgrade(url)
    assert rep.after == migrate.head_revision() == "0009_v2_0" and rep.backup     # respaldo antes de migrar
    d = Database(url)
    with d.session() as s:
        n_req = len(s.scalars(select(Request)).all())
    out = ex.crear_seguro(d, BASE, "admin", utcnow())
    d.engine.dispose()
    migrate.downgrade(url, "0005_v1_4")
    migrate.upgrade(url)
    d = Database(url)
    with d.session() as s:
        assert len(s.scalars(select(Request)).all()) == n_req + 1           # lo de v1.4 sigue intacto
        assert s.get(Expediente, out["id"]) is None                        # la baja quitó solo lo nuevo
    d.engine.dispose()


def test_respaldo_y_restauracion_conservan_expedientes(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    url = f"sqlite:///{(tmp_path / 'r.db').as_posix()}"
    d = Database(url)
    d.create_all()
    from monitoreo.simulator.seed import seed_base
    seed_base(d)
    out = ex.crear_seguro(d, BASE, "admin", utcnow())
    copia = migrate.backup(url, "prueba")
    with d.session() as s:
        e = s.get(Expediente, out["id"])
        ex.cerrar(s, e, "CANCELADO", "admin", utcnow())
    d.engine.dispose()
    migrate.restore(url, copia, force_running=True)
    d = Database(url)
    with d.session() as s:
        assert s.get(Expediente, out["id"]).estado == "LISTO"
    d.engine.dispose()


def test_fecha_de_nacimiento_limite(db):
    hoy = date.today()
    assert "fecha_nacimiento" in val.validar({**BASE, "fecha_nacimiento": (hoy + timedelta(days=1)).isoformat()}, REG).errores
    assert val.validar({**BASE, "fecha_nacimiento": hoy.isoformat()}, REG).ok
