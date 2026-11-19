"""v2.4 · Recuperación segura tras restaurar un respaldo (monitoreo/recuperacion.py).

Datos FICTICIOS y portal de laboratorio propio (proceso local en 127.0.0.1). La restauración real
(`migrate.restore`) solo existe para SQLite; lo demás corre también con PostgreSQL.

Lo que se demuestra:
- el diario de eventos críticos va firmado con HMAC (clave secreta): una línea alterada, borrada o
  recalculada sin la clave se detecta; un corte a mitad de escritura no rompe la cadena;
- no guarda códigos de expediente ni folios en claro;
- tras restaurar: detección, bloqueo de las operaciones con el portal (también tras reiniciar), el
  consentimiento revocado NO vuelve a ser vigente, la confirmación incierta NO se repite sola, la
  conciliación exige acciones del administrador con motivo, queda auditada, y solo entonces se desbloquea;
- un fallo de escritura del diario no deja llamar al portal y bloquea;
- truncamiento detectado por el ancla de la base y por la copia externa; sin copia externa, aviso.
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from monitoreo import migrate
from monitoreo import recuperacion as rc
from monitoreo.app import create_app
from monitoreo.captura import reservaciones as rs
from monitoreo.models import AuditLog, Consentimiento, Reservacion

from .captura_util import ADMIN_PWD, OP_PWD
from .test_captura_dia import lab as _lab_dia, portal  # noqa: F401  (fixtures)
from .test_consentimiento_v22 import (_confirmadas_en_portal, asignado, consentimiento_de, crear, lab,  # noqa: F401
                                      revocar)


# ---------------------------------------------------------------- utilidades
def estado(lab) -> dict:
    r = lab.c.get("/api/recuperacion", headers=lab.h)
    assert r.status_code == 200, r.text
    return r.json()


def motivos(e) -> set[str]:
    return {m["codigo"] for m in e["motivos"]}


def eventos(lab=None) -> list[dict]:
    p = rc.ruta_diario()
    return [json.loads(x) for x in p.read_text("utf-8").splitlines() if x.strip()] if p.exists() else []


def reabrir(lab):
    """Reinicia el motor (nueva app sobre la misma base): el arranque vuelve a evaluar la recuperación."""
    lab.c.__exit__(None, None, None)
    lab.c = TestClient(create_app(lab.db.url, start_engine=False, seed_demo=False))
    lab.c.__enter__()
    lab.db = lab.c.app.state.db
    lab.h = lab.login("admin", ADMIN_PWD)
    lab.ops = {u: lab.login(u, OP_PWD) for u in lab.ops}


def confirmar(lab, rid, h):
    return lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})


def resolver(lab, d, accion, h=None, **kw):
    return lab.c.post(f"/api/recuperacion/discrepancias/{d['ref']}/resolver", headers=h or lab.h,
                      json={"tipo": d["tipo"], "accion": accion, **kw})


def auditoria(lab, accion) -> list[AuditLog]:
    with lab.db.session() as s:
        return list(s.scalars(select(AuditLog).where(AuditLog.action == accion)))


# ================================================================ 1. diario firmado (sin base)
@pytest.fixture
def diario(tmp_path, monkeypatch):
    from monitoreo.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.delenv("MONITOREO_DIARIO_COPIA", raising=False)
    rc._invalidar()
    for i in range(3):
        rc._anexar("BLOQUEO", {"motivo": f"PRUEBA_{i}"})
    yield tmp_path
    rc._invalidar()


def _leer():
    raw = rc._leer_clave()
    return rc._leer(rc.ruta_diario(), rc._Claves(raw))


def test_diario_integro_con_inicio_y_cadena(diario):
    lect = _leer()
    assert lect.problema is None and lect.ultimo[0] == 4
    assert [e["tipo"] for e in lect.eventos] == ["INICIO", "BLOQUEO", "BLOQUEO", "BLOQUEO"]
    assert all(e["ant"] == p["mac"] for p, e in zip(lect.eventos, lect.eventos[1:]))


def test_alterar_una_linea_se_detecta_aunque_se_recalcule_un_sha256(diario):
    p = rc.ruta_diario()
    lineas = p.read_text("utf-8").splitlines()
    obj = json.loads(lineas[2])
    obj["d"]["motivo"] = "OTRO"
    # quien no tiene la clave solo puede recalcular un hash sin clave (lo que hacía una cadena SHA-256)
    import hashlib
    obj["mac"] = hashlib.sha256(rc._canon({k: v for k, v in obj.items() if k != "mac"})).hexdigest()
    lineas[2] = json.dumps(obj)
    p.write_text("\n".join(lineas) + "\n", "utf-8")
    assert _leer().problema == "DIARIO_MANIPULADO"


def test_borrar_o_reordenar_una_linea_rompe_la_cadena(diario):
    p = rc.ruta_diario()
    lineas = p.read_text("utf-8").splitlines()
    p.write_text("\n".join(lineas[:1] + lineas[2:]) + "\n", "utf-8")
    assert _leer().problema == "DIARIO_MANIPULADO"
    p.write_text("\n".join([lineas[0], lineas[2], lineas[1], lineas[3]]) + "\n", "utf-8")
    assert _leer().problema == "DIARIO_MANIPULADO"


def test_corte_a_mitad_de_escritura_se_tolera_y_no_rompe_la_cadena(diario):
    p = rc.ruta_diario()
    with open(p, "ab") as f:
        f.write(b'{"v":1,"n":5,"t":"2026-11-19T00:00')        # corte de luz a mitad de una línea
    lect = _leer()
    assert lect.problema is None and lect.fragmentos == 1 and lect.ultimo[0] == 4
    rc._anexar("BLOQUEO", {"motivo": "DESPUES_DEL_CORTE"})        # la siguiente línea queda aislada del fragmento
    lect = _leer()
    assert lect.problema is None and lect.ultimo[0] == 5 and lect.eventos[-1]["d"]["motivo"] == "DESPUES_DEL_CORTE"


def test_otra_clave_no_verifica(diario):
    p = rc.ruta_clave()
    p.unlink()
    rc._guardar_clave(os.urandom(32))
    assert _leer().problema == "DIARIO_MANIPULADO"


@pytest.mark.skipif(os.name == "nt", reason="permisos POSIX")
def test_clave_solo_legible_por_su_dueno(diario):
    assert (rc.ruta_clave().stat().st_mode & 0o077) == 0
    assert rc.proteccion_clave() == "archivo-solo-dueno"


@pytest.mark.skipif(os.name != "nt", reason="DPAPI solo existe en Windows")
def test_clave_protegida_con_dpapi_en_windows(diario):
    import base64
    raw = rc._leer_clave()
    cont = json.loads(rc.ruta_clave().read_text("utf-8"))
    assert cont["proteccion"] == "dpapi-usuario"
    assert base64.b64decode(cont["clave"]) != raw and base64.b64encode(raw).decode() not in rc.ruta_clave().read_text()
    assert rc._dpapi(rc._dpapi(b"x" * 32, True), False) == b"x" * 32


# ================================================================ 2. flujo normal (SQLite y PostgreSQL)
def test_flujo_normal_registra_y_no_bloquea_ni_guarda_identificadores_en_claro(lab):
    eid, u, h = asignado(lab)
    rid, dia = lab.hasta_fecha(eid, h)
    r = confirmar(lab, rid, h)
    assert r.status_code == 200, r.text
    folio = r.json()["reservacion"]["folio"]
    e2 = crear(lab)
    revocar(lab, e2["id"])
    tipos = [e["tipo"] for e in eventos()]
    assert tipos == ["INICIO", "CONFIRMACION_INTENTO", "CONFIRMACION_RESULTADO", "REVOCACION"], tipos
    assert eventos()[2]["d"]["resultado"] == "CONFIRMADA"
    e = estado(lab)
    assert not e["bloqueado"] and e["pendientes"] == 0, e["motivos"]
    texto = rc.ruta_diario().read_text("utf-8")
    with lab.db.session() as s:
        r_ = s.get(Reservacion, rid)
        codigos = [r_.clave, folio] + [c.codigo for c in s.scalars(select(Consentimiento))]
    from monitoreo.models import Expediente
    with lab.db.session() as s:
        codigos += [x.codigo for x in s.scalars(select(Expediente))]
    assert not [c for c in codigos if c and c in texto], "el diario no debe llevar códigos ni folios en claro"
    # el administrador ve el estado; un operador no
    assert lab.c.get("/api/recuperacion", headers=h).status_code == 403
    # sin copia externa: el autodiagnóstico lo dice claramente
    d = lab.c.get("/api/autodiagnostico", headers=lab.h).json()
    sin_copia = [x for x in d["comprobaciones"] if x["clave"] == "recuperacion:sin_copia"]
    assert sin_copia and "NO está protegida contra la pérdida del disco" in sin_copia[0]["detalle"]


def test_fallo_de_escritura_no_llama_al_portal_y_bloquea_aunque_se_reinicie(lab, monkeypatch):
    eid, u, h = asignado(lab)
    rid, dia = lab.hasta_fecha(eid, h)
    antes = _confirmadas_en_portal(lab)
    real = rc._escribir_linea

    def falla(ruta, linea):
        raise OSError("disco lleno (simulado)")
    monkeypatch.setattr(rc, "_escribir_linea", falla)
    r = confirmar(lab, rid, h)
    assert r.status_code == 503 and r.json()["detail"]["codigo"] == "DIARIO_NO_DISPONIBLE", r.text
    assert _confirmadas_en_portal(lab) == antes, "sin línea en el diario no se llama al portal"
    with lab.db.session() as s:
        assert s.get(Reservacion, rid).estado == "FECHA_ELEGIDA"
    monkeypatch.setattr(rc, "_escribir_linea", real)       # el disco vuelve, pero el fallo no se olvida
    reabrir(lab)
    e = estado(lab)
    assert e["bloqueado"] and "FALLO_ESCRITURA" in motivos(e)
    r = confirmar(lab, rid, h := lab.ops[u])
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "RECUPERACION_PENDIENTE"
    assert auditoria(lab, "diario_fallo_escritura")
    r = lab.c.post("/api/recuperacion/levantar", headers=lab.h, json={"motivo": "disco sustituido y revisado"})
    assert r.status_code == 200 and r.json()["levantado"], r.text
    assert confirmar(lab, rid, h).status_code == 200


def test_revocacion_sin_diario_queda_hecha_y_bloquea(lab, monkeypatch):
    e = crear(lab)
    monkeypatch.setattr(rc, "_escribir_linea", lambda *a: (_ for _ in ()).throw(OSError("sin permiso")))
    out = revocar(lab, e["id"])
    assert out["consentimiento"]["estado"] == "REVOCADO"          # lo restrictivo no se deshace
    monkeypatch.undo()
    assert "FALLO_ESCRITURA" in motivos(estado(lab))


def test_discrepancia_sin_restauracion_deja_un_bloqueo_firmado(lab):
    """Base que retrocede sin pasar por `restaurar` (copia a mano, pg_restore): se detecta igual."""
    e = crear(lab)
    revocar(lab, e["id"])
    with lab.db.session() as s:     # simula la base vieja: el consentimiento vuelve a VIGENTE
        s.execute(update(Consentimiento).where(Consentimiento.expediente_id == e["id"]).values(
            estado="VIGENTE", revocado_en=None))
    rc._invalidar()
    est = estado(lab)
    assert est["bloqueado"] and {"DISCREPANCIAS", "BLOQUEO_PENDIENTE"} <= motivos(est)
    assert eventos()[-1]["tipo"] == "BLOQUEO"
    c = consentimiento_de(lab, e["id"])
    assert c["vigente"] is None and c["bloqueo"]["codigo"] == "REVOCACION_EN_DIARIO"
    d = est["discrepancias"][0]
    assert d["tipo"] == "REVOCACION_AUSENTE" and d["acciones"] == ["reaplicar_revocacion"]
    r = lab.c.post(f"/api/expedientes/{e['id']}/consentimientos", headers=lab.h,
                   json={"medio": "FIRMA_PAPEL", "aviso_privacidad": "AP-2026-01", "evidencia_ref": "Carpeta LAB-09",
                         "evidencia_sha256": "a" * 64})
    assert r.status_code == 409          # tampoco se puede «sustituir» con otro consentimiento
    assert resolver(lab, d, "reaplicar_revocacion", motivo="la persona lo revocó por escrito").status_code == 200
    est = estado(lab)
    assert est["pendientes"] == 0 and "BLOQUEO_PENDIENTE" in motivos(est)    # sigue bloqueado hasta levantar
    assert lab.c.post("/api/recuperacion/levantar", headers=lab.h, json={"motivo": "revisado"}).json()["levantado"]


def test_truncamiento_detectado_por_el_ancla_de_la_base(lab):
    e = crear(lab)
    revocar(lab, e["id"])
    e2 = crear(lab)
    revocar(lab, e2["id"])
    p = rc.ruta_diario()
    lineas = p.read_text("utf-8").splitlines()
    p.write_text("\n".join(lineas[:-1]) + "\n", "utf-8")        # alguien borra la última línea
    est = estado(lab)
    assert est["bloqueado"] and "DIARIO_TRUNCADO" in motivos(est), est["motivos"]
    p.unlink()                                                     # o el diario entero
    assert "DIARIO_AUSENTE" in motivos(estado(lab))


def test_clave_perdida_bloquea_y_se_puede_reiniciar_el_diario_con_auditoria(lab):
    e = crear(lab)
    revocar(lab, e["id"])
    rc.ruta_clave().unlink()
    est = estado(lab)
    assert est["bloqueado"] and "CLAVE_NO_DISPONIBLE" in motivos(est)
    assert lab.c.post("/api/recuperacion/levantar", headers=lab.h, json={"motivo": "intento"}).status_code == 409
    r = lab.c.post("/api/recuperacion/reiniciar-diario", headers=lab.h, json={"motivo": "clave perdida al reinstalar"})
    assert r.status_code == 200, r.text
    assert r.json()["archivados"] and "BLOQUEO_PENDIENTE" in motivos(r.json())
    assert [x["tipo"] for x in eventos()][:3] == ["INICIO", "BLOQUEO", "REVOCACION"]   # relleno desde la base
    assert auditoria(lab, "recuperacion_diario_reiniciado")
    assert lab.c.post("/api/recuperacion/levantar", headers=lab.h, json={"motivo": "revisado a mano"}).json()["levantado"]


def test_copia_externa_configurada(lab, tmp_path_factory, monkeypatch):
    otra = tmp_path_factory.mktemp("otra-unidad")
    monkeypatch.setenv("MONITOREO_DIARIO_COPIA", str(otra))
    e = crear(lab)
    revocar(lab, e["id"])
    assert (otra / rc.ARCHIVO).read_text("utf-8") == rc.ruta_diario().read_text("utf-8")
    est = estado(lab)
    assert not est["bloqueado"] and est["copia"]["al_dia"]
    # la copia está más larga que el principal: el principal fue truncado
    p = rc.ruta_diario()
    p.write_text("\n".join(p.read_text("utf-8").splitlines()[:-1]) + "\n", "utf-8")
    assert "DIARIO_TRUNCADO" in motivos(estado(lab))
    # la unidad de la copia no está: se bloquea (configurada = se espera que funcione)
    monkeypatch.setenv("MONITOREO_DIARIO_COPIA", str(otra / "no-existe" / "sub"))
    rc._invalidar()
    assert "COPIA_NO_DISPONIBLE" in motivos(estado(lab))


# ================================================================ 3. el escenario: restaurar (SQLite)
def test_restaurar_detecta_bloquea_concilia_y_solo_entonces_desbloquea(lab):
    if not lab.db.is_sqlite:
        pytest.skip("migrate.restore solo restaura SQLite (PostgreSQL: pg_restore + marcar-restauracion)")
    # Antes del respaldo: A con el día elegido (sin confirmar) y G con consentimiento
    eid, u, h = asignado(lab)
    rid, dia = lab.hasta_fecha(eid, h)
    g = crear(lab)
    respaldo = migrate.backup(lab.db.url, "prueba")
    # Después del respaldo: se confirma A (folio F) y se revoca G
    r = confirmar(lab, rid, h)
    assert r.status_code == 200, r.text
    folio = r.json()["reservacion"]["folio"]
    revocar(lab, g["id"])
    citas = _confirmadas_en_portal(lab)
    # Restaurar con el motor detenido y arrancar de nuevo
    lab.c.__exit__(None, None, None)
    lab.db.engine.dispose()
    rep = migrate.restore(lab.db.url, respaldo)
    assert "RECUPERACION_PENDIENTE" in rep["diario"]
    lab.c = TestClient(create_app(lab.db.url, start_engine=False, seed_demo=False))
    lab.c.__enter__()
    lab.db = lab.c.app.state.db
    lab.h = lab.login("admin", ADMIN_PWD)
    lab.ops = {x: lab.login(x, OP_PWD) for x in lab.ops}
    h = lab.ops[u]
    # 1. detección
    est = estado(lab)
    assert est["bloqueado"] and {"RESTAURACION_PENDIENTE", "DISCREPANCIAS"} <= motivos(est), est["motivos"]
    tipos = {d["tipo"]: d for d in est["discrepancias"]}
    assert set(tipos) == {"REVOCACION_AUSENTE", "CONFIRMACION_AUSENTE"}, est["discrepancias"]
    assert tipos["CONFIRMACION_AUSENTE"]["reservacion_id"] == rid
    assert tipos["REVOCACION_AUSENTE"]["expediente_id"] == g["id"]
    # 2. la base restaurada tiene a G VIGENTE, pero el motor NO lo trata como vigente
    with lab.db.session() as s:
        assert s.scalar(select(Consentimiento.estado).where(Consentimiento.expediente_id == g["id"])) == "VIGENTE"
    c = consentimiento_de(lab, g["id"])
    assert c["vigente"] is None and c["bloqueo"]["codigo"] == "REVOCACION_EN_DIARIO"
    # 3. bloqueo: ni el operador ni el administrador pueden confirmar; nada se repite solo
    for quien in (h, lab.h):
        r = confirmar(lab, rid, quien)
        assert r.status_code == 409 and r.json()["detail"]["codigo"] == "RECUPERACION_PENDIENTE", r.text
    rs.recuperar(lab.db, __import__("monitoreo.db", fromlist=["utcnow"]).utcnow())
    with lab.db.session() as s:
        assert s.get(Reservacion, rid).estado == "FECHA_ELEGIDA", "la recuperación automática no cambia nada"
    assert _confirmadas_en_portal(lab) == citas, "no se repitió ninguna confirmación"
    assert lab.c.post("/api/recuperacion/levantar", headers=lab.h, json={"motivo": "quiero seguir"}).status_code == 409
    # 4. el bloqueo persiste al reiniciar
    reabrir(lab)
    assert estado(lab)["bloqueado"]
    # 5. conciliación: solo el administrador, con motivo, y el folio debe ser el del diario
    d_conf, d_rev = tipos["CONFIRMACION_AUSENTE"], tipos["REVOCACION_AUSENTE"]
    assert resolver(lab, d_conf, "registrar_folio", h=lab.ops[u], motivo="x" * 10, folio=folio).status_code == 403
    assert resolver(lab, d_conf, "registrar_folio", motivo="", folio=folio).status_code == 422
    r = resolver(lab, d_conf, "registrar_folio", motivo="comprobado en el portal", folio="F-INVENTADO")
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "FOLIO_NO_COINCIDE"
    r = resolver(lab, d_conf, "registrar_folio", motivo="comprobado en el portal", folio=folio)
    assert r.status_code == 200, r.text
    with lab.db.session() as s:
        res = s.get(Reservacion, rid)
        assert res.estado == "CONFIRMADA" and res.folio == folio and res.evidencia["fuente"] == "conciliacion_manual"
    r = resolver(lab, d_rev, "reaplicar_revocacion", motivo="revocación por escrito del expediente")
    assert r.status_code == 200, r.text
    est = r.json()
    assert est["pendientes"] == 0 and est["bloqueado"], "conciliado pero aún sin levantar"
    with lab.db.session() as s:
        assert s.scalar(select(Consentimiento.estado).where(Consentimiento.expediente_id == g["id"])) == "REVOCADO"
    assert lab.c.get("/api/consentimientos/verificar", headers=lab.h).json()["ok"]
    # 6. desbloqueo auditado
    r = lab.c.post("/api/recuperacion/levantar", headers=lab.h, json={"motivo": "conciliación revisada"})
    assert r.status_code == 200 and r.json()["levantado"], r.text
    assert len(auditoria(lab, "recuperacion_conciliada")) == 2 and auditoria(lab, "recuperacion_levantada")
    tipos_ev = [x["tipo"] for x in eventos()]
    assert tipos_ev.count("CONCILIADO") == 2 and tipos_ev[-1] == "LEVANTAMIENTO", tipos_ev
    reabrir(lab)
    assert not estado(lab)["bloqueado"]
    # 7. continuidad: la cita sigue siendo una sola
    r = confirmar(lab, rid, lab.ops[u])
    assert r.status_code == 200 and r.json().get("repetida") and r.json()["reservacion"]["folio"] == folio
    assert _confirmadas_en_portal(lab) == citas


def test_dos_hilos_creando_la_clave_a_la_vez_usan_la_misma(tmp_path, monkeypatch):
    """Lo encontró la regresión test_dos_solicitudes_por_la_ultima_unidad: dos confirmaciones simultáneas
    creaban la clave a la vez (una sobrescribía a la otra o fallaba con 500)."""
    import threading
    from monitoreo.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    rc._invalidar()
    out, err = [], []

    def crea():
        try:
            out.append(rc._claves(crear=True).id)
        except Exception as e:  # noqa: BLE001
            err.append(e)
    hs = [threading.Thread(target=crea) for _ in range(8)]
    for h in hs:
        h.start()
    for h in hs:
        h.join()
    assert not err and len(set(out)) == 1 and len(out) == 8
