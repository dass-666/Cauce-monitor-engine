"""Piloto asistido · confirmación humana PERSISTENTE (monitoreo/captura/asistida.py).

La cita la agenda una persona en el portal oficial; Cauce guarda quién revisó, cuándo, con qué huella de
datos, y el folio registrado. Datos FICTICIOS. Lo que se demuestra:
- recorrido completo abrir -> revisar -> registrar sin una sola llamada al portal; el expediente queda
  RESERVACION_CONFIRMADA, la reservación CONFIRMADA con contrato `oficial_asistido` y evidencia «persona»,
  la coherencia de estados se mantiene y el diario de eventos críticos lo anota (origen «persona»);
- nada automático cuenta: una revisión abandonada (navegador cerrado, horas después) sigue ABIERTA y no
  permite registrar; registrar sin revisión confirmada se rechaza;
- frase exacta, tiempo mínimo, huella de lo visto y datos sin cambios son obligatorios;
- folio tecleado dos veces, folio duplicado rechazado, idempotencia ante doble clic;
- permisos (otro operador no lo ve; solo quien abrió continúa; el administrador puede revocar);
- consentimiento revocado entre la revisión y el registro: no hay cita;
- recuperación pendiente: no se abren revisiones nuevas, pero una cita ya hecha sí se puede registrar;
- diario no disponible: la cita NO se guarda (503) y el intento queda como estaba;
- persistencia: tras reiniciar el motor la revisión sigue ahí;
- PRODUCCIÓN con el flujo administrativo activo y sin portal: la ruta existe y el recorrido de Ventanilla
  termina en atención CONFIRMADA.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update

from monitoreo import modulos
from monitoreo import recuperacion as rc
from monitoreo.app import create_app
from monitoreo.captura import asistida as asi
from monitoreo.captura import expedientes as ex
from monitoreo.models import AuditLog, ConfirmacionHumana, Expediente, Reservacion

from .captura_util import ADMIN_PWD, OP_PWD
from .test_consentimiento_v22 import _confirmadas_en_portal, asignado, cons, crear, lab, revocar  # noqa: F401
from .test_captura_dia import lab as _lab_dia, portal  # noqa: F401  (fixtures)
from .test_produccion import _login  # noqa: F401
from .test_produccion_flujo_admin import OP_PWD as OP_PWD_PROD, prod_flujo  # noqa: F401
from .test_produccion import prod_env  # noqa: F401

MANANA = (date.today() + timedelta(days=3)).isoformat()


def _url(eid, paso=""):
    return f"/api/expedientes/{eid}/asistida" + (f"/{paso}" if paso else "")


def abrir(lab, eid, h):
    r = lab.c.post(_url(eid, "abrir"), headers=h)
    assert r.status_code == 200, r.text
    return r.json()["confirmacion"]


def envejecer(lab, cid, segundos):
    """Simula que pasó el tiempo desde que se abrió la revisión (sin dormir la prueba)."""
    with lab.db.session() as s:
        c = s.get(ConfirmacionHumana, cid)
        c.abierta_en = c.abierta_en - timedelta(seconds=segundos)


def revisar(lab, eid, h, conf, frase=asi.FRASE_REVISION, huella=None):
    return lab.c.post(_url(eid, "revisar"), headers=h, json={"frase": frase, "huella": huella or conf["huella_datos"]})


def cita(folio="FON-2026-000123", **kw):
    d = {"folio": folio, "folio_confirmacion": folio, "fecha": MANANA, "hora": "10:30", "sucursal": "NOR-1",
         "comprobante_sha256": hashlib.sha256(b"acuse ficticio").hexdigest(), "comprobante_ref": "Carpeta PILOTO-01",
         "declaro": True}
    d.update(kw)
    return d


def hasta_revisada(lab, eid, h):
    conf = abrir(lab, eid, h)
    envejecer(lab, conf["id"], asi.MIN_REVISION_S + 1)
    r = revisar(lab, eid, h, conf)
    assert r.status_code == 200, r.text
    return r.json()["confirmacion"]


def diario_tipos():
    p = rc.ruta_diario()
    import json
    return [json.loads(x) for x in p.read_text("utf-8").splitlines() if x.strip()] if p.exists() else []


# ---------------------------------------------------------------- recorrido completo
def test_recorrido_completo_sin_llamar_al_portal(lab):
    eid, u, h = asignado(lab)
    en_portal = _confirmadas_en_portal(lab)
    assert lab.c.get(_url(eid), headers=h).json()["viva"] is None
    conf = abrir(lab, eid, h)
    assert conf["estado"] == "ABIERTA" and conf["persona"] == u and len(conf["huella_datos"]) == 64
    assert conf["verificada_con_proveedor"] is False
    # demasiado rápido, frase incorrecta y huella ajena: rechazados
    r = revisar(lab, eid, h, conf)
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "REVISION_DEMASIADO_RAPIDA"
    envejecer(lab, conf["id"], asi.MIN_REVISION_S + 1)
    r = revisar(lab, eid, h, conf, frase="revisé y apruebo")
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "FRASE_INCORRECTA"
    r = revisar(lab, eid, h, conf, huella="0" * 64)
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "HUELLA_NO_COINCIDE"
    r = revisar(lab, eid, h, conf)
    assert r.status_code == 200 and r.json()["confirmacion"]["estado"] == "REVISADA"
    # sin declarar el folio, con folios distintos o con fecha pasada: rechazado
    assert lab.c.post(_url(eid, "registrar"), headers=h, json=cita(declaro=False)).status_code == 422
    r = lab.c.post(_url(eid, "registrar"), headers=h, json=cita(folio_confirmacion="FON-2026-000124"))
    assert r.status_code == 422 and "folio_confirmacion" in r.json()["detail"]["errores"]
    r = lab.c.post(_url(eid, "registrar"), headers=h, json=cita(fecha="2020-01-01"))
    assert r.status_code == 422 and "fecha" in r.json()["detail"]["errores"]
    r = lab.c.post(_url(eid, "registrar"), headers=h, json=cita(folio=" fon-2026-000123 "))
    assert r.status_code == 200, r.text
    c = r.json()["confirmacion"]
    assert c["estado"] == "CITA_REGISTRADA" and c["folio"] == "FON-2026-000123" and c["comprobante"] == "CON_HUELLA"
    # doble clic / respuesta perdida: misma respuesta, nada nuevo
    r2 = lab.c.post(_url(eid, "registrar"), headers=h, json=cita())
    assert r2.status_code == 200 and r2.json()["repetida"] is True
    with lab.db.session() as s:
        e = s.get(Expediente, eid)
        assert e.estado == ex.RES_CONFIRMADA
        res = list(s.scalars(select(Reservacion).where(Reservacion.expediente_id == eid)))
        assert len(res) == 1 and res[0].estado == "CONFIRMADA" and res[0].contrato == asi.CONTRATO
        assert res[0].evidencia["fuente"] == "PERSONA_PORTAL_OFICIAL"
        assert res[0].evidencia["verificada_con_proveedor"] is False and res[0].confirmada_por == u
        assert ex.coherencia(s) == []
        assert len(list(s.scalars(select(ConfirmacionHumana).where(ConfirmacionHumana.expediente_id == eid)))) == 1
        acciones = {a.action for a in s.scalars(select(AuditLog))}
    assert {"asistida_revision_abierta", "asistida_revision_confirmada", "asistida_cita_registrada"} <= acciones
    assert _confirmadas_en_portal(lab) == en_portal                   # ninguna llamada de reserva al portal
    ev = [x for x in diario_tipos() if x["tipo"].startswith("CONFIRMACION_")]
    assert [x["tipo"] for x in ev[-2:]] == ["CONFIRMACION_INTENTO", "CONFIRMACION_RESULTADO"]
    assert ev[-1]["d"]["resultado"] == "CONFIRMADA" and ev[-1]["d"]["origen"] == "persona"
    assert "FON-2026-000123" not in rc.ruta_diario().read_text("utf-8")      # el folio va seudonimizado
    with lab.db.session() as s:
        assert rc.evaluar(s, usar_cache=False)["bloqueado"] is False
    # la ficha y la mesa de control la muestran como cita con folio
    ficha = lab.c.get(f"/api/expedientes/{eid}", headers=h).json()
    assert any(r["estado"] == "CONFIRMADA" and r["folio"] == "FON-2026-000123" for r in ficha["reservaciones"])
    assert lab.c.get("/api/admin/panel", headers=lab.h).status_code == 200


def test_abandonar_la_revision_nunca_cuenta_como_aprobacion(lab):
    eid, u, h = asignado(lab)
    conf = abrir(lab, eid, h)
    envejecer(lab, conf["id"], 3 * 3600)        # «cerró el navegador» y volvió horas después
    est = lab.c.get(_url(eid), headers=h).json()["viva"]
    assert est["estado"] == "ABIERTA" and est["revisada_en"] is None
    r = lab.c.post(_url(eid, "registrar"), headers=h, json=cita())
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "SIN_REVISION_CONFIRMADA"
    # retomar devuelve la MISMA revisión (no crea otra ni la da por buena)
    r = lab.c.post(_url(eid, "abrir"), headers=h)
    assert r.status_code == 200 and r.json()["retomada"] is True and r.json()["confirmacion"]["id"] == conf["id"]
    with lab.db.session() as s:
        assert s.get(Expediente, eid).estado == ex.CAPTURA


def test_registrar_sin_revision_se_rechaza(lab):
    eid, u, h = asignado(lab)
    r = lab.c.post(_url(eid, "registrar"), headers=h, json=cita())
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "SIN_REVISION"


def test_datos_cambiados_tras_la_revision_impiden_registrar(lab):
    eid, u, h = asignado(lab)
    hasta_revisada(lab, eid, h)
    with lab.db.session() as s:
        s.get(Expediente, eid).telefono = "5559999999"
    r = lab.c.post(_url(eid, "registrar"), headers=h, json=cita())
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "DATOS_CAMBIARON"
    with lab.db.session() as s:
        assert s.get(Expediente, eid).estado == ex.CAPTURA


def test_permisos_y_revocacion(lab):
    eid, u, h = asignado(lab)
    otro = next(x for x in lab.ops if x != u)
    assert lab.c.post(_url(eid, "abrir"), headers=lab.ops[otro]).status_code == 404     # no lo ve
    assert lab.c.get(_url(eid), headers=lab.ops[otro]).status_code == 404
    conf = abrir(lab, eid, h)
    # el administrador no puede continuar la revisión de otra persona, pero sí revocarla
    r = lab.c.post(_url(eid, "abrir"), headers=lab.h)
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "OTRA_PERSONA"
    assert lab.c.post(_url(eid, "revocar"), headers=lab.h, json={"motivo": "x"}).status_code == 422
    r = lab.c.post(_url(eid, "revocar"), headers=lab.h, json={"motivo": "datos por confirmar con la persona"})
    assert r.status_code == 200 and r.json()["confirmacion"]["estado"] == "REVOCADA"
    with lab.db.session() as s:
        assert s.get(Reservacion, conf["reservacion_id"]).estado == "CANCELADA"
    # tras revocar se puede abrir una nueva, limpia
    nueva = abrir(lab, eid, h)
    assert nueva["id"] != conf["id"] and nueva["estado"] == "ABIERTA"
    hist = lab.c.get(_url(eid), headers=h).json()["historial"]
    assert [x["estado"] for x in hist] == ["ABIERTA", "REVOCADA"]


def test_folio_duplicado_en_otro_expediente(lab):
    e1, u1, h1 = asignado(lab)
    hasta_revisada(lab, e1, h1)
    assert lab.c.post(_url(e1, "registrar"), headers=h1, json=cita("FON-DUP-0001")).status_code == 200
    e2, u2, h2 = asignado(lab)
    hasta_revisada(lab, e2, h2)
    r = lab.c.post(_url(e2, "registrar"), headers=h2, json=cita("FON-DUP-0001"))
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "FOLIO_DUPLICADO"
    with lab.db.session() as s:
        assert s.get(Expediente, e2).estado == ex.CAPTURA


def test_consentimiento_revocado_antes_de_registrar(lab):
    eid, u, h = asignado(lab)
    hasta_revisada(lab, eid, h)
    revocar(lab, eid, h)
    r = lab.c.post(_url(eid, "registrar"), headers=h, json=cita())
    assert r.status_code in (404, 409), r.text
    with lab.db.session() as s:
        assert s.get(Expediente, eid).estado == ex.CERRADO
        assert not s.scalar(select(Reservacion.id).where(Reservacion.expediente_id == eid,
                                                         Reservacion.estado == "CONFIRMADA"))


def test_recuperacion_pendiente_bloquea_abrir_pero_no_registrar_lo_ya_hecho(lab, monkeypatch):
    e1, u1, h1 = asignado(lab)
    hasta_revisada(lab, e1, h1)
    e2, u2, h2 = asignado(lab)
    real = rc.evaluar

    def bloqueado(s, usar_cache=True):
        return {**real(s, usar_cache=False), "bloqueado": True, "motivos": [{"codigo": "RESTAURACION_DETECTADA"}]}
    monkeypatch.setattr(rc, "evaluar", bloqueado)
    r = lab.c.post(_url(e2, "abrir"), headers=h2)
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "RECUPERACION_PENDIENTE"
    r = lab.c.post(_url(e1, "registrar"), headers=h1, json=cita("FON-REC-0001"))
    assert r.status_code == 200, r.text


def test_diario_no_disponible_no_guarda_la_cita(lab, monkeypatch):
    eid, u, h = asignado(lab)
    conf = hasta_revisada(lab, eid, h)

    def falla(db, res_id):
        raise rc.ErrorDiario("disco lleno (simulado)")
    monkeypatch.setattr(rc, "confirmacion_intento", falla)
    r = lab.c.post(_url(eid, "registrar"), headers=h, json=cita())
    assert r.status_code == 503 and r.json()["detail"]["codigo"] == "DIARIO_NO_DISPONIBLE"
    with lab.db.session() as s:
        assert s.get(Expediente, eid).estado == ex.CAPTURA
        assert s.get(ConfirmacionHumana, conf["id"]).estado == "REVISADA"
        assert s.get(Reservacion, conf["reservacion_id"]).estado == "PREPARADA"
    monkeypatch.undo()
    assert lab.c.post(_url(eid, "registrar"), headers=h, json=cita()).status_code == 200   # al reintentar, sí


def test_la_revision_persiste_tras_reiniciar_el_motor(lab):
    eid, u, h = asignado(lab)
    conf = hasta_revisada(lab, eid, h)
    url = lab.db.url
    lab.c.__exit__(None, None, None)
    lab.c = TestClient(create_app(url, start_engine=False, seed_demo=False))
    lab.c.__enter__()
    lab.db = lab.c.app.state.db
    h = lab.login(u, OP_PWD)
    lab.h = lab.login("admin", ADMIN_PWD)
    viva = lab.c.get(_url(eid), headers=h).json()["viva"]
    assert viva["id"] == conf["id"] and viva["estado"] == "REVISADA"
    assert lab.c.post(_url(eid, "registrar"), headers=h, json=cita("FON-REINICIO-01")).status_code == 200


def test_sin_datos_personales_en_confirmacion_eventos_ni_auditoria(lab):
    eid, u, h = asignado(lab)
    with lab.db.session() as s:
        e = s.get(Expediente, eid)
        valores = [e.nombre, e.telefono, e.correo]
    hasta_revisada(lab, eid, h)
    assert lab.c.post(_url(eid, "registrar"), headers=h, json=cita("FON-PRIV-0001")).status_code == 200
    import json
    with lab.db.session() as s:
        blob = json.dumps([[str(v) for v in row] for t in ("confirmaciones_humanas", "audit_log", "expediente_eventos")
                           for row in s.execute(text(f"SELECT * FROM {t}"))])
    for v in valores:
        assert v and v not in blob, v


# ---------------------------------------------------------------- producción (piloto) sin portal
def test_produccion_con_flujo_admin_recorrido_de_ventanilla_asistido(prod_flujo, monkeypatch):  # noqa: F811
    with TestClient(create_app(prod_flujo, start_engine=False)) as c:
        h = _login(c)
        assert c.post("/api/users", headers=h, json={"username": "opprod", "password": OP_PWD_PROD, "role": "operador",
                                                     "regions": ["NORTE"], "operator_name": "Operador piloto"}).status_code == 201
        r = c.post("/api/auth/login", json={"username": "opprod", "password": OP_PWD_PROD})
        ho = {"Authorization": f"Bearer {r.json()['token']}"}
        a = c.post("/api/ventanilla/atenciones", headers=ho, json={"clave": uuid.uuid4().hex}).json()
        datos = {"nombre": "Persona Ficticia Piloto", "fecha_nacimiento": "1990-05-04", "telefono": "5550001234",
                 "telefono_confirmacion": "5550001234", "correo": "piloto@ejemplo.test",
                 "correo_confirmacion": "piloto@ejemplo.test", "region": "NORTE", "tipo_tramite": "alta",
                 "id_registro_previo": "REG000777", "sucursal": "NOR-1", "consentimiento": cons()}
        r = c.post(f"/api/ventanilla/atenciones/{a['id']}/expediente", headers=ho, json={"datos": datos})
        assert r.status_code == 200, r.text
        eid = r.json()["expediente"]["id"]
        db = c.app.state.db
        with db.session() as s:
            assert s.get(Expediente, eid).estado in (ex.ASIGNADO, ex.CAPTURA), s.get(Expediente, eid).estado
        r = c.post(_url(eid, "abrir"), headers=ho)
        assert r.status_code == 200, r.text
        conf = r.json()["confirmacion"]
        with db.session() as s:
            s.execute(update(ConfirmacionHumana).where(ConfirmacionHumana.id == conf["id"]).values(
                abierta_en=s.get(ConfirmacionHumana, conf["id"]).abierta_en - timedelta(seconds=30)))
        r = c.post(_url(eid, "revisar"), headers=ho, json={"frase": asi.FRASE_REVISION, "huella": conf["huella_datos"]})
        assert r.status_code == 200, r.text
        r = c.post(_url(eid, "registrar"), headers=ho, json=cita("FON-PILOTO-0001", comprobante_sha256=None))
        assert r.status_code == 200, r.text
        assert r.json()["confirmacion"]["comprobante"] == "SOLO_REFERENCIA"
        r = c.post(f"/api/ventanilla/atenciones/{a['id']}/cerrar", headers=ho, json={})
        assert r.status_code == 200, r.text
        assert r.json()["estado"] == "CONFIRMADA" and r.json()["folio"] == "FON-PILOTO-0001"
        with db.session() as s:
            assert ex.coherencia(s) == []
        assert c.get("/api/admin/panel", headers=h).status_code == 200
        assert modulos.VARIABLE
