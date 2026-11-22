"""Laboratorio · Ventanilla -> adaptador -> Forms («Autorrellenar formulario»).

Dos niveles, con datos FICTICIOS, portal de laboratorio propio como proceso (127.0.0.1) y bases temporales:

1. Siempre: un conector ESPÍA hace de Forms (guarda la solicitud que recibe y responde con el simulador del
   contrato). Demuestra QUÉ sale de Monitor: solo los campos necesarios, la autorización derivada del
   consentimiento REAL de la base y del operador que pulsó, un entorno sin variables de Monitor, y que sin
   consentimiento vigente (o revocado, o manipulado) no se lanza nada.
2. Con `FORMS_REPO=<ruta a CAUCE-UNIVERSAL-CONEXION con su entorno>`: Forms REAL. Aprueba el perfil sobre el
   portal de esta prueba, rellena el formulario /cita en Chromium (sin ventana en CI) y verifica 8 campos y
   2 confirmaciones; y una revocación a mitad termina el proceso de Forms.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

import psutil
import pytest
from sqlalchemy import select, update

from monitoreo import modulos
from monitoreo.autorrelleno import adaptador as ad
from monitoreo.captura.formularios import contrato as ct
from monitoreo.config import settings
from monitoreo.conector import aislamiento
from monitoreo.db import Database
from monitoreo.models import AuditLog, Consentimiento, ExpedienteEvento
from portal_reservas.proceso import PortalProceso

from .captura_util import ADMIN_PWD, LabDia
from .conftest import BACKENDS, PG_URL

RAIZ = Path(__file__).resolve().parents[1]
ESPIA = RAIZ / "tests" / "herramientas" / "forms_espia.py"
MAPEO = RAIZ / "monitoreo" / "autorrelleno" / "perfil_portal_laboratorio_dia.json"
FORMS_REPO = os.environ.get("FORMS_REPO")
AUTOMATICOS = ["correo", "correo_confirmacion", "fecha_nacimiento", "identificador", "operacion", "telefono",
               "telefono_confirmacion", "tramite"]


@pytest.fixture(scope="module")
def portal(tmp_path_factory):
    d = tmp_path_factory.mktemp("portal")
    p = PortalProceso(d / "estado.json", extra=["--zona-horaria", "America/Mexico_City"])
    p.iniciar()
    yield p
    p.detener()


def _pin(carpeta: Path, portal, **cambios) -> Path:
    """Perfil «aprobado» para el ESPÍA (no hay Forms): misma forma que el que escribe autorrelleno_forms.py."""
    m = json.loads(MAPEO.read_text(encoding="utf-8"))
    pin = {"version": 1, "formulario_id": m["formulario_id"], "perfil": m["perfil"], "modo": m["modo"],
           "url": portal.base + m["ruta"], "perfil_sha256": "a" * 64, "firma_estructura_esperada": "v2:" + "b" * 16,
           "campos": sorted(m["campos"]), "aprobado_por": "rev_prueba", **cambios}
    ruta = carpeta / "perfil_aprobado.json"
    ruta.write_text(json.dumps(pin), encoding="utf-8")
    return ruta


@pytest.fixture(params=BACKENDS)
def lab(request, portal, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "admin_password", ADMIN_PWD)
    monkeypatch.setenv("MONITOREO_PORTAL_URL", portal.base)
    monkeypatch.setenv("MONITOREO_PORTAL_TOKEN", portal.token)
    monkeypatch.setenv("MONITOREO_PORTAL_TOKEN_CONTROL", portal.token_control)
    monkeypatch.setenv("MONITOREO_EXIGIR_CONSENTIMIENTO", "1")
    monkeypatch.setenv("MONITOREO_AUTORRELLENO_LAB", "1")
    monkeypatch.setenv("MONITOREO_AUTORRELLENO_PERFIL", str(_pin(tmp_path, portal)))
    monkeypatch.setenv("MONITOREO_SECRET_KEY", "secreto-de-monitor-que-no-debe-salir")
    espia = tmp_path / "espia"
    monkeypatch.setenv("MONITOREO_FORMS_COMANDO", json.dumps([sys.executable, str(ESPIA), str(espia)]))
    ad.reiniciar_para_pruebas()
    portal.fallos(reset=True)
    if request.param == "postgres":
        Database(PG_URL).reset_for_tests()
        url = PG_URL
    else:
        url = f"sqlite:///{(tmp_path / 'a.db').as_posix()}"
    lb = LabDia(url, portal)
    lb.espia = espia
    lb.tmp = tmp_path
    yield lb
    _esperar_fin(lb)
    lb.cerrar()
    ad.reiniciar_para_pruebas()
    if request.param == "postgres":
        Database(PG_URL).reset_for_tests()


def _esperar_fin(lab, t=20.0):
    fin = time.monotonic() + t
    while time.monotonic() < fin and ad.en_curso() is not None:
        time.sleep(0.1)


def cons(n=1):
    return {"medio": "FIRMA_PAPEL", "aviso_privacidad": "AP-2026-01",
            "evidencia_sha256": hashlib.sha256(f"formato ficticio {n}".encode()).hexdigest(),
            "evidencia_ref": "Carpeta LAB-01"}


def atencion(lab, h, consentimiento=True, **kw):
    r = lab.c.post("/api/ventanilla/atenciones", headers=h, json={"clave": uuid.uuid4().hex})
    assert r.status_code in (200, 201), r.text
    a = r.json()
    datos = lab.datos_expediente(**kw)
    if consentimiento:
        datos["consentimiento"] = cons()
    r = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/expediente", headers=h, json={"datos": datos})
    assert r.status_code == 200, r.text
    return r.json(), datos


def pulsar(lab, aid, h):
    return lab.c.post(f"/api/autorrelleno/atenciones/{aid}", headers=h)


def esperar(lab, aid, h, t=60.0):
    fin = time.monotonic() + t
    while time.monotonic() < fin:
        r = lab.c.get(f"/api/autorrelleno/atenciones/{aid}", headers=h)
        assert r.status_code == 200, r.text
        c = r.json()["corrida"]
        if c and not c["en_curso"]:
            return c
        time.sleep(0.2)
    raise AssertionError("el autorrellenado no terminó a tiempo")


def textos_guardados(lab) -> str:
    with lab.db.session() as s:
        aud = [json.dumps(a.detail, ensure_ascii=False) + a.target for a in s.scalars(select(AuditLog))]
        evs = [json.dumps(e.detalle, ensure_ascii=False) for e in s.scalars(select(ExpedienteEvento))]
    return "\n".join(aud + evs)


def eventos(lab, eid):
    with lab.db.session() as s:
        return [(e.tipo, e.detalle) for e in s.scalars(select(ExpedienteEvento).where(
            ExpedienteEvento.expediente_id == eid, ExpedienteEvento.tipo.like("AUTORRELLENO%")).order_by(ExpedienteEvento.id))]


# ================================================================ flujo con el consentimiento real de Monitor
def test_flujo_completo_con_consentimiento_real_y_solo_los_campos_necesarios(lab):
    h = lab.ops["op1"]
    a, datos = atencion(lab, h)
    assert a["consentimiento"]["vigente"] and a["expediente"]["estado"] == "ASIGNADO"
    st = lab.c.get(f"/api/autorrelleno/atenciones/{a['id']}", headers=h).json()
    assert st["habilitado"] and st["disponible"], st
    r = pulsar(lab, a["id"], h)
    assert r.status_code == 202, r.text
    fin = esperar(lab, a["id"], h)
    assert fin["estado"] == "PREPARADO", fin
    inf = fin["informe"]
    assert sorted(c["campo"] for c in inf["campos"]) == AUTOMATICOS and all(c["correcto"] for c in inf["campos"])
    assert "Clave de identidad" in inf["manuales"] and "Aceptación del aviso de privacidad" in inf["manuales"]
    assert inf["envio_realizado_por_conector"] is False

    sol = json.loads((lab.espia / "solicitud.json").read_text(encoding="utf-8"))
    # 1) solo los campos AUTOMÁTICOS del contrato, con los valores de la base (nunca el nombre ni lo manual)
    assert sorted(sol["campos"]) == AUTOMATICOS
    assert sol["campos"]["telefono"] == sol["campos"]["telefono_confirmacion"] == datos["telefono"]
    assert sol["campos"]["correo"] == sol["campos"]["correo_confirmacion"] == datos["correo"]
    assert sol["campos"]["fecha_nacimiento"] == datos["fecha_nacimiento"]
    assert sol["campos"]["identificador"] == datos["id_registro_previo"]
    assert sol["campos"]["tramite"] == datos["tipo_tramite"] and sol["campos"]["operacion"] == "nueva"
    blob = json.dumps(sol, ensure_ascii=False)
    assert datos["nombre"] not in blob and "clave_identidad" not in blob and "acepto_privacidad" not in blob
    # 2) la autorización sale del consentimiento REAL (código de la base) y del operador que pulsó
    with lab.db.session() as s:
        c = s.scalar(select(Consentimiento).where(Consentimiento.expediente_id == a["expediente"]["id"]))
    assert sol["autorizacion"]["ref"] == "aut_" + c.codigo.lower().replace("-", "_")
    assert sol["autorizacion"]["otorgada_por_ref"] == "rev_op1" and sol["autorizacion"]["consentimiento_vigente"] is True
    assert c.codigo == a["consentimiento"]["codigo"] == fin["consentimiento"]
    assert sol["formulario"]["url"] == lab.portal.base + "/cita"
    # 3) Forms no hereda nada de Monitor (ni claves, ni tokens, ni la URL de su base)
    nombres = json.loads((lab.espia / "entorno.json").read_text(encoding="utf-8"))
    assert not [n for n in nombres if n.startswith("MONITOREO_")]
    # 4) historial y auditoría: estados y nombres de campo, nunca valores
    tipos = [t for t, _ in eventos(lab, a["expediente"]["id"])]
    assert tipos == ["AUTORRELLENO_SOLICITADO", "AUTORRELLENO_TERMINADO"]
    guardado = textos_guardados(lab)
    for v in (datos["telefono"], datos["correo"], datos["fecha_nacimiento"], datos["id_registro_previo"]):
        assert v not in guardado


def test_sin_consentimiento_no_se_lanza_nada(lab, monkeypatch):
    h = lab.ops["op1"]
    a, _ = atencion(lab, h, consentimiento=False)
    r = pulsar(lab, a["id"], h)
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "SIN_CONSENTIMIENTO", r.text
    assert not lab.espia.exists()
    # aunque la exigencia general esté desactivada (expediente LISTO sin consentimiento), aquí se exige
    monkeypatch.setenv("MONITOREO_EXIGIR_CONSENTIMIENTO", "0")
    b, _ = atencion(lab, h, consentimiento=False)
    assert b["expediente"]["estado"] == "ASIGNADO"
    r = pulsar(lab, b["id"], h)
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "SIN_CONSENTIMIENTO", r.text
    assert not lab.espia.exists()
    with lab.db.session() as s:
        negados = [x.detail["codigo"] for x in s.scalars(select(AuditLog).where(AuditLog.action == "autorrelleno_negado"))]
    assert negados == ["SIN_CONSENTIMIENTO", "SIN_CONSENTIMIENTO"]


def test_consentimiento_revocado_niega(lab):
    h = lab.ops["op1"]
    a, _ = atencion(lab, h)
    cid = a["consentimiento"]["id"]
    r = lab.c.post(f"/api/consentimientos/{cid}/revocar", headers=lab.h, json={"medio": "ESCRITO", "motivo": "PERSONA_LO_PIDE"})
    assert r.status_code == 200, r.text
    r = pulsar(lab, a["id"], h)
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "CONSENTIMIENTO_REVOCADO", r.text
    assert not lab.espia.exists()


def test_consentimiento_manipulado_en_la_base_niega(lab):
    h = lab.ops["op1"]
    a, _ = atencion(lab, h)
    with lab.db.session() as s:     # alguien cambia la huella del documento directamente en la base
        s.execute(update(Consentimiento).where(Consentimiento.id == a["consentimiento"]["id"])
                  .values(evidencia_sha256="f" * 64))
    r = pulsar(lab, a["id"], h)
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "CONSENTIMIENTO_NO_VERIFICABLE", r.text
    assert not lab.espia.exists()


def test_revocar_con_la_ventana_abierta_termina_forms(lab, monkeypatch):
    monkeypatch.setenv("MONITOREO_FORMS_COMANDO", json.dumps([sys.executable, str(ESPIA), str(lab.espia), "ok", "60"]))
    h = lab.ops["op1"]
    a, _ = atencion(lab, h)
    assert pulsar(lab, a["id"], h).status_code == 202
    fin = time.monotonic() + 15
    while time.monotonic() < fin and not (lab.espia / "pid").exists():
        time.sleep(0.05)
    pid = int((lab.espia / "pid").read_text())
    t0 = time.monotonic()
    r = lab.c.post(f"/api/consentimientos/{a['consentimiento']['id']}/revocar", headers=lab.h,
                   json={"medio": "PRESENCIAL", "motivo": "PERSONA_LO_PIDE"})
    assert r.status_code == 200, r.text
    c = esperar(lab, a["id"], h, t=30)
    assert c["estado"] == "CANCELADA" and c["codigo"] in ("CONSENTIMIENTO_NO_VIGENTE", "ATENCION_CERRADA"), c
    assert c["procesos_restantes"] == 0
    assert time.monotonic() - t0 < 15
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


def test_detener_monitor_con_la_ventana_abierta_termina_forms(lab, monkeypatch):
    """Punto 7: al detener Monitor (fin de la app: Ctrl+C en el laboratorio) no queda Forms abierto."""
    monkeypatch.setenv("MONITOREO_FORMS_COMANDO", json.dumps([sys.executable, str(ESPIA), str(lab.espia), "ok", "60"]))
    h = lab.ops["op1"]
    a, _ = atencion(lab, h)
    assert pulsar(lab, a["id"], h).status_code == 202
    fin = time.monotonic() + 15
    while time.monotonic() < fin and not (lab.espia / "pid").exists():
        time.sleep(0.05)
    pid = int((lab.espia / "pid").read_text())
    c = ad.en_curso()
    t0 = time.monotonic()
    lab.cerrar()                                    # apagado de la aplicación (lifespan)
    lab.cerrar = lambda: None
    assert time.monotonic() - t0 < 15
    assert c.estado == "CANCELADA" and c.codigo == "MONITOR_DETENIDO" and c.procesos_restantes == 0, c.vista()
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    assert ad.en_curso() is None


def test_permisos_de_monitor(lab):
    h1, h2 = lab.ops["op1"], lab.ops["op2"]
    a, _ = atencion(lab, h1)
    r = pulsar(lab, a["id"], h2)                    # otro operador: ni la ve
    assert r.status_code == 404 and r.json()["detail"]["codigo"] == "ATENCION_NO_ENCONTRADA"
    r = pulsar(lab, a["id"], lab.h)                 # el administrador tampoco opera la atención de otro
    assert r.status_code == 403 and r.json()["detail"]["codigo"] == "NO_ES_TU_ATENCION"
    # operador de otra región: Ventanilla ya no le deja tomarlo, y el adaptador vuelve a comprobarlo
    r = lab.c.post("/api/users", headers=lab.h, json={"username": "op_sur", "password": "Operador-123", "role": "operador",
                                                       "regions": ["SUR"], "operator_name": "Operador sur"})
    assert r.status_code == 201, r.text
    hs = lab.login("op_sur", "Operador-123")
    b = lab.c.post("/api/ventanilla/atenciones", headers=hs, json={"clave": uuid.uuid4().hex}).json()
    r = lab.c.post(f"/api/ventanilla/atenciones/{b['id']}/expediente", headers=hs,
                   json={"expediente_id": a["expediente"]["id"]})
    assert r.status_code == 409
    from monitoreo.models import Atencion
    with lab.db.session() as s:                     # aunque alguien forzara la vinculación en la base
        s.execute(update(Atencion).where(Atencion.id == b["id"]).values(expediente_id=a["expediente"]["id"]))
    r = pulsar(lab, b["id"], hs)
    assert r.status_code == 403 and r.json()["detail"]["codigo"] == "PERMISO_DENEGADO", r.text
    assert not lab.espia.exists()


def test_una_respuesta_que_repite_datos_queda_incierta_y_no_se_muestra(lab, monkeypatch):
    monkeypatch.setenv("MONITOREO_FORMS_COMANDO", json.dumps([sys.executable, str(ESPIA), str(lab.espia), "filtra_pii"]))
    h = lab.ops["op1"]
    a, _ = atencion(lab, h)
    assert pulsar(lab, a["id"], h).status_code == 202
    c = esperar(lab, a["id"], h)
    assert c["estado"] == "INCIERTA" and c["codigo"] == "RESPUESTA_INVALIDA" and c["informe"] is None


def test_una_sola_ventana_a_la_vez(lab, monkeypatch):
    monkeypatch.setenv("MONITOREO_FORMS_COMANDO", json.dumps([sys.executable, str(ESPIA), str(lab.espia), "ok", "3"]))
    h = lab.ops["op1"]
    a, _ = atencion(lab, h)
    b, _ = atencion(lab, lab.ops["op2"])
    assert pulsar(lab, a["id"], h).status_code == 202
    r = pulsar(lab, b["id"], lab.ops["op2"])
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "YA_HAY_UNA_EN_CURSO"
    assert pulsar(lab, a["id"], h).status_code == 409
    assert esperar(lab, a["id"], h)["estado"] == "PREPARADO"


def test_configuracion_de_laboratorio_obligatoria(lab, monkeypatch, tmp_path):
    h = lab.ops["op1"]
    a, _ = atencion(lab, h)
    monkeypatch.delenv("MONITOREO_AUTORRELLENO_LAB")
    assert lab.c.get("/api/autorrelleno/estado", headers=h).json()["habilitado"] is False
    assert lab.c.get(f"/api/autorrelleno/atenciones/{a['id']}", headers=h).json()["habilitado"] is False
    r = pulsar(lab, a["id"], h)
    assert r.status_code == 403 and r.json()["detail"]["codigo"] == "AUTORRELLENO_DESACTIVADO"
    monkeypatch.setenv("MONITOREO_AUTORRELLENO_LAB", "1")
    monkeypatch.delenv("MONITOREO_PORTAL_TOKEN_CONTROL")
    assert pulsar(lab, a["id"], h).json()["detail"]["codigo"] == "PORTAL_NO_LABORATORIO"
    monkeypatch.setenv("MONITOREO_PORTAL_TOKEN_CONTROL", lab.portal.token_control)
    monkeypatch.setenv("MONITOREO_AUTORRELLENO_PERFIL", str(_pin(tmp_path, lab.portal, url="http://127.0.0.1:1/cita")))
    assert pulsar(lab, a["id"], h).json()["detail"]["codigo"] == "PERFIL_DE_OTRO_PORTAL"
    perfiles = tmp_path / "perfiles"
    perfiles.mkdir()
    (perfiles / "perfil_cauce_lab_cita.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("MONITOREO_AUTORRELLENO_PERFIL", str(_pin(tmp_path, lab.portal, profiles_dir=str(perfiles))))
    assert pulsar(lab, a["id"], h).json()["detail"]["codigo"] == "PERFIL_ALTERADO"
    assert not lab.espia.exists()


def test_solo_laboratorio_y_lanzamiento_operativo_sigue_bloqueado():
    assert aislamiento.puede_lanzar()[0] is False
    assert any(m == "monitoreo.api.autorrelleno" for m, _, _ in modulos.SOLO_LABORATORIO)
    assert not any(m == "monitoreo.api.autorrelleno" for m, _, _ in modulos.FLUJO_ADMIN)
    assert ad.configuracion.__module__ == "monitoreo.autorrelleno.adaptador"
    with pytest.raises(ad.Negado) as e:
        ad.configuracion({"MONITOREO_AUTORRELLENO_LAB": "1", "MONITOREO_ENTORNO": "produccion"})
    assert e.value.codigo == "AUTORRELLENO_DESACTIVADO"


def test_el_mapeo_aprobado_son_los_campos_automaticos_del_contrato():
    m = json.loads(MAPEO.read_text(encoding="utf-8"))
    c = ct.cargar("portal_laboratorio_dia")
    assert set(m["campos"]) == {x.nombre for x in c.automaticos}
    assert not set(m["campos"]) & {x.nombre for x in c.manuales}


# ================================================================ con Forms REAL (FORMS_REPO)
@pytest.fixture
def forms_real(lab, monkeypatch):
    if not FORMS_REPO:
        pytest.skip("FORMS_REPO no definido: prueba con Forms real omitida (no verificada en esta corrida)")
    sys.path.insert(0, str(RAIZ / "scripts"))
    from autorrelleno_forms import aprobar, variables_motor
    forms = Path(FORMS_REPO).resolve()
    py = Path(os.environ.get("FORMS_PYTHON") or "") if os.environ.get("FORMS_PYTHON") else None
    pin = aprobar(forms, lab.portal.base, lab.tmp, "rev_prueba", python=py)
    monkeypatch.delenv("MONITOREO_FORMS_COMANDO")
    for k, v in variables_motor(forms, pin, py).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("MONITOREO_FORMS_HEADLESS", os.environ.get("FORMS_HEADLESS", "1"))
    monkeypatch.setenv("MONITOREO_AUTORRELLENO_ESPERA_S", "30")
    return pin


def test_forms_real_rellena_el_formulario_de_laboratorio(lab, forms_real):
    h = lab.ops["op1"]
    a, _ = atencion(lab, h)
    assert pulsar(lab, a["id"], h).status_code == 202
    c = esperar(lab, a["id"], h, t=120)
    # sin persona que cierre la ventana, la revisión caduca a los 30 s: se rellenó y verificó, no se envió
    assert c["estado"] == "CADUCADA", c
    inf = c["informe"]
    assert sorted(x["campo"] for x in inf["campos"]) == AUTOMATICOS and all(x["correcto"] for x in inf["campos"]), inf
    assert len(inf["confirmaciones"]) == 2 and all(x["coinciden"] for x in inf["confirmaciones"])
    assert inf["estructura_coincide"] and not inf["envio_realizado_por_conector"] and not inf["envio_humano_observado"]


def test_forms_real_revocacion_a_mitad_termina_chromium(lab, forms_real, monkeypatch):
    monkeypatch.setenv("MONITOREO_AUTORRELLENO_ESPERA_S", "300")
    h = lab.ops["op1"]
    a, _ = atencion(lab, h)
    assert pulsar(lab, a["id"], h).status_code == 202
    time.sleep(8)                                   # Forms abre Chromium y teclea
    r = lab.c.post(f"/api/consentimientos/{a['consentimiento']['id']}/revocar", headers=lab.h,
                   json={"medio": "PRESENCIAL", "motivo": "PERSONA_LO_PIDE"})
    assert r.status_code == 200, r.text
    c = esperar(lab, a["id"], h, t=30)
    assert c["estado"] == "CANCELADA" and c["procesos_restantes"] == 0, c
    marcados = []                                   # ningún proceso (Python de Forms ni Chromium) sigue vivo
    for p in psutil.process_iter():
        try:
            if "CAUCE_LANZADOR_ID" in p.environ():
                marcados.append(p.pid)
        except (psutil.Error, OSError):
            continue
    assert marcados == []


def _pids_forms() -> set[int]:
    """Procesos lanzados por el lanzador de laboratorio (Python de Forms y su Chromium)."""
    pids = set()
    for p in psutil.process_iter():
        try:
            if "CAUCE_LANZADOR_ID" in p.environ():
                pids.add(p.pid)
        except (psutil.Error, OSError):
            continue
    return pids


def test_forms_real_cerrar_la_ventana_a_mano_da_preparado(lab, forms_real, monkeypatch):
    """Punto 5 de la validación: la persona cierra la ventana (la X) tras el rellenado -> PREPARADO, no CADUCADA."""
    if os.environ.get("FORMS_HEADLESS", "1") != "0":
        pytest.skip("necesita ventana visible (FORMS_HEADLESS=0)")
    from .herramientas.cerrar_ventana import cerrar
    monkeypatch.setenv("MONITOREO_AUTORRELLENO_ESPERA_S", "300")
    h = lab.ops["op1"]
    a, _ = atencion(lab, h)
    assert pulsar(lab, a["id"], h).status_code == 202
    time.sleep(float(os.environ.get("FORMS_ESPERA_RELLENO_S", "15")))   # Forms abre Chromium y teclea los 8 campos
    assert cerrar(_pids_forms()) >= 1, "no se encontró la ventana de Forms"
    c = esperar(lab, a["id"], h, t=60)
    assert c["estado"] == "PREPARADO", c
    inf = c["informe"]
    assert all(x["correcto"] for x in inf["campos"]) and len(inf["campos"]) == 8, inf
    assert not inf["envio_realizado_por_conector"] and c["procesos_restantes"] == 0, c
    assert _pids_forms() == set()
