"""Auditoría independiente del PR #10 (9949e64) · pruebas mínimas reproducibles.

Copiar este archivo a tests/ y ejecutar:
    python -m pytest -q tests/test_auditoria_pr10_repro.py
Las tres primeras (test_r1, test_r2, test_r3) son las de la auditoría: FALLABAN sobre 9949e64 y pasan con la
corrección. Las siguientes fijan el comportamiento corregido. Datos ficticios.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from monitoreo import recuperacion as rc
from monitoreo import respaldo_externo as rx
from monitoreo.app import create_app
from monitoreo.models import ConfirmacionHumana, Reservacion

from .captura_util import ADMIN_PWD
from .test_piloto_asistido import _lab_dia, _url, anular, asignado, cita, hasta_revisada, lab, portal  # noqa: F401


def test_r1_respaldo_externo_no_debe_dar_por_bueno_una_base_inexistente(tmp_path):
    """R1 · respaldo_externo.respaldar (lo que ejecuta respaldar_externo.bat) contra una ruta donde NO hay base:
    sqlite3.connect crea un archivo vacío y el respaldo sale «ok: true», con revisión None y sin tablas.
    Es lo que ocurre si el piloto se abrió con piloto_asistido.bat (base en resultados\\piloto_asistido\\
    cauce_piloto.db) y el respaldo se lanza con respaldar_externo.bat (base de .env / data\\monitoreo.db).
    Esperado tras corregir: ErrorRespaldo (base inexistente, vacía o sin revisión de esquema)."""
    destino = tmp_path / "medio"
    destino.mkdir()
    url = f"sqlite:///{(tmp_path / 'no_existe' / 'monitoreo.db').as_posix()}"
    (tmp_path / "no_existe").mkdir()
    with pytest.raises(rx.ErrorRespaldo):
        rx.respaldar(destino, url, laboratorio=True)


def test_r2_recorrido_automatico_no_debe_borrar_una_carpeta_de_piloto_con_datos(tmp_path, monkeypatch):
    """R2 · scripts/piloto_asistido.py: `Piloto(carpeta, nuevo=a.nuevo or a.recorrido)` hace shutil.rmtree de la
    carpeta, y la carpeta por omisión es LA MISMA para el uso a mano y para --recorrido. Ejecutar
    `piloto_asistido.bat --recorrido` tras un día de uso a mano borra base, diario, clave y su «copia».
    Esperado tras corregir: --recorrido usa otra carpeta por omisión o se niega si ya hay una base."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import piloto_asistido as pa
    carpeta = tmp_path / "piloto"
    carpeta.mkdir()
    (carpeta / "cauce_piloto.db").write_bytes(b"base con las citas del dia")
    monkeypatch.setattr(pa.Piloto, "iniciar", lambda self: (_ for _ in ()).throw(SystemExit(0)))   # no arranca nada
    monkeypatch.setattr(sys, "argv", ["piloto_asistido.py", "--carpeta", str(carpeta), "--recorrido"])
    try:
        pa.main()
    except SystemExit:
        pass
    assert (carpeta / "cauce_piloto.db").exists(), "--recorrido borró la base de la carpeta del piloto"


def test_r3_conciliar_confirmacion_incierta_debe_normalizar_el_folio(lab):
    """R3 · monitoreo/recuperacion.py resolver(accion=registrar_folio) + captura/asistida.py conciliar_folio:
    con una CONFIRMACION_INCIERTA (corte entre el diario y la base) el folio tecleado por el administrador se
    guarda tal cual (solo strip): minúsculas y espacios. Consecuencias demostradas: (a) el mismo folio se puede
    registrar otra vez en otro expediente (la unicidad compara texto exacto) y (b) esa cita ya no se puede
    anular (anular() pasa el folio a mayúsculas y nunca coincide).
    Esperado tras corregir: mismo normalizado y validación que asistida._normalizar (o 422)."""
    e1, u, h = asignado(lab)
    conf = hasta_revisada(lab, e1, h)
    with lab.db.session() as s:
        res_id = s.get(ConfirmacionHumana, conf["id"]).reservacion_id
    rc.confirmacion_intento(lab.db, res_id)                 # el proceso muere antes de guardar la cita
    url = lab.db.url
    lab.c.__exit__(None, None, None)
    lab.db.engine.dispose()
    lab.c = TestClient(create_app(url, start_engine=False, seed_demo=False))
    lab.c.__enter__()
    lab.db = lab.c.app.state.db
    lab.h = lab.login("admin", ADMIN_PWD)
    d = lab.c.get("/api/recuperacion", headers=lab.h).json()["discrepancias"][0]
    assert d["tipo"] == "CONFIRMACION_INCIERTA"
    r = lab.c.post(f"/api/recuperacion/discrepancias/{d['ref']}/resolver", headers=lab.h,
                   json={"tipo": d["tipo"], "accion": "registrar_folio", "motivo": "folio del comprobante",
                         "folio": "fon d 0001"})
    if r.status_code == 422:
        return                                              # corrección válida: rechazar el folio mal escrito
    assert r.status_code == 200, r.text
    with lab.db.session() as s:
        assert s.get(Reservacion, res_id).folio == "FOND0001"
    assert anular(lab, e1, lab.h, "FOND0001").status_code == 200


# ============================================================================ regresión de las correcciones
def test_respaldo_externo_rechaza_una_base_sin_esquema(tmp_path):
    """Una base SQLite que existe pero no es de Cauce (sin alembic_version ni tablas): tampoco se respalda."""
    import sqlite3
    destino = tmp_path / "medio"
    destino.mkdir()
    base = tmp_path / "otra.db"
    c = sqlite3.connect(base)
    c.execute("CREATE TABLE cualquiera (x)")
    c.commit()
    c.close()
    with pytest.raises(rx.ErrorRespaldo):
        rx.respaldar(destino, f"sqlite:///{base.as_posix()}", laboratorio=True)
    assert not list(destino.glob("*.db")), "no debe quedar ningún respaldo en el medio"
    assert rx.esquema_valido(base) is not None
    v = rx.verificar(base, exigir_manifiesto=False)
    assert not v["ok"] and any("utilizable" in p for p in v["problemas"])


def test_respaldo_externo_de_una_base_real_sigue_funcionando(lab, tmp_path_factory):
    if not lab.db.is_sqlite:
        pytest.skip("respaldo externo: solo SQLite")
    destino = tmp_path_factory.mktemp("medio_externo")      # fuera de la carpeta de datos de Cauce
    r = rx.respaldar(destino, lab.db.url, laboratorio=True)
    assert r["ok"] and r["revision"] and rx.verificar(r["archivo"])["ok"]


def _pa():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import piloto_asistido as pa
    return pa


def test_demostracion_solo_borra_carpetas_que_ella_creo(tmp_path):
    pa = _pa()
    # carpeta nueva: se crea con la marca y contraseñas aleatorias (ninguna fija en el código)
    a = pa.Piloto(tmp_path / "a", modo="a_mano")
    b = pa.Piloto(tmp_path / "b", modo="recorrido")
    assert (a.c / pa.MARCA).is_file() and a.admin_pwd != b.admin_pwd and a.operador[1] != b.operador[1]
    assert "Lab-2026" not in a.admin_pwd + a.operador[1]
    (a.c / "cauce_piloto.db").write_bytes(b"demostracion a mano")
    (b.c / "cauce_piloto.db").write_bytes(b"recorrido anterior")
    # el recorrido NO borra una demostración hecha a mano...
    with pytest.raises(pa.Negado):
        pa.Piloto(a.c, modo="recorrido")
    assert (a.c / "cauce_piloto.db").exists()
    # ...ni --nuevo borra una carpeta sin marca
    sin_marca = tmp_path / "datos_de_alguien"
    sin_marca.mkdir()
    (sin_marca / "monitoreo.db").write_bytes(b"x")
    for modo in ("a_mano", "recorrido"):
        with pytest.raises(pa.Negado):
            pa.Piloto(sin_marca, nuevo=True, modo=modo)
    assert (sin_marca / "monitoreo.db").exists()
    # una instalación (carpeta con .env) nunca es carpeta de demostración
    inst = tmp_path / "instalacion"
    (inst / "sub").mkdir(parents=True)
    (inst / ".env").write_text("MONITOREO_ENTORNO=produccion\n")
    with pytest.raises(pa.Negado):
        pa.Piloto(inst / "sub")
    # el recorrido sí reinicia SU carpeta; retomar a mano conserva datos y contraseñas
    pa.Piloto(b.c, modo="recorrido")
    assert not (b.c / "cauce_piloto.db").exists()
    a2 = pa.Piloto(a.c, modo="a_mano")
    assert (a.c / "cauce_piloto.db").exists() and a2.admin_pwd == a.admin_pwd
    assert pa.CARPETA_A_MANO != pa.CARPETA_RECORRIDO


def _incierta(lab):
    e1, u, h = asignado(lab)
    conf = hasta_revisada(lab, e1, h)
    with lab.db.session() as s:
        res_id = s.get(ConfirmacionHumana, conf["id"]).reservacion_id
    rc.confirmacion_intento(lab.db, res_id)
    url = lab.db.url
    lab.c.__exit__(None, None, None)
    lab.db.engine.dispose()
    lab.c = TestClient(create_app(url, start_engine=False, seed_demo=False))
    lab.c.__enter__()
    lab.db = lab.c.app.state.db
    lab.h = lab.login("admin", ADMIN_PWD)
    d = lab.c.get("/api/recuperacion", headers=lab.h).json()["discrepancias"][0]
    assert d["tipo"] == "CONFIRMACION_INCIERTA"
    return e1, res_id, d


def _resolver(lab, d, folio):
    return lab.c.post(f"/api/recuperacion/discrepancias/{d['ref']}/resolver", headers=lab.h,
                      json={"tipo": d["tipo"], "accion": "registrar_folio", "motivo": "folio del comprobante",
                            "folio": folio})


def test_conciliar_incierta_rechaza_folio_no_valido_y_duplicado(lab):
    from .captura_util import OP_PWD  # noqa: F401
    # otra cita ya registrada con el folio FON-DUP-9
    e0, u0, h0 = asignado(lab)
    hasta_revisada(lab, e0, h0)
    assert lab.c.post(_url(e0, "registrar"), headers=h0, json=cita("FON-DUP-9")).status_code == 200
    e1, res_id, d = _incierta(lab)
    r = _resolver(lab, d, "a b")                                   # demasiado corto tras normalizar
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "FOLIO_NO_VALIDO", r.text
    r = _resolver(lab, d, "fon ñ/0001")
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "FOLIO_NO_VALIDO", r.text
    r = _resolver(lab, d, " fon-dup-9 ")                           # el de la otra cita, escrito distinto
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "FOLIO_DUPLICADO", r.text
    with lab.db.session() as s:
        assert s.get(Reservacion, res_id).estado == "PREPARADA"    # nada cambió
    r = _resolver(lab, d, "fon inc 0002")
    assert r.status_code == 200, r.text
    with lab.db.session() as s:
        assert s.get(Reservacion, res_id).folio == "FONINC0002"
    # el mismo folio, escrito de cualquier forma, ya no entra en otro expediente
    assert lab.c.post("/api/recuperacion/levantar", headers=lab.h, json={"motivo": "revisado"}).status_code == 200
    e2, u2, h2 = asignado(lab)
    hasta_revisada(lab, e2, h2)
    r = lab.c.post(_url(e2, "registrar"), headers=h2, json=cita("Fon Inc 0002"))
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "FOLIO_DUPLICADO", r.text
    # y la cita conciliada se puede anular tecleando el folio con espacios o minúsculas
    assert anular(lab, e1, lab.h, "fon inc 0002").status_code == 200


def test_produccion_rechaza_contrasenas_de_laboratorio():
    from monitoreo import entorno
    base = {"MONITOREO_ENTORNO": "produccion", "MONITOREO_SECRET_KEY": "k" * 48, "MONITOREO_DATOS_DEMO": "0"}
    for pwd in ("Admin-Laboratorio-2026", "Admin-Piloto-Lab-2026", "Operador-Piloto-Lab-2026"):
        r = entorno.review({**base, "MONITOREO_ADMIN_PASSWORD": pwd})
        assert any("laboratorio" in e for e in r.errores), pwd
    assert not entorno.review({**base, "MONITOREO_ADMIN_PASSWORD": "Propia-y-Larga-7391"}).errores


def test_preparar_piloto_deja_una_configuracion_valida_y_sin_claves_de_laboratorio(tmp_path, monkeypatch):
    """scripts/preparar_piloto.py sobre una copia mínima de la instalación: crea .env de producción con el flujo
    administrativo, la copia del diario y el respaldo externo; rechaza contraseñas de laboratorio; la base por
    omisión es data/monitoreo.db de ESA carpeta (la misma que usan iniciar.bat y respaldar_externo.bat)."""
    import shutil
    import sys
    from pathlib import Path
    raiz = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(raiz / "scripts"))
    import preparar_piloto as pp
    inst, ext = tmp_path / "cauce-piloto", tmp_path / "externo"
    inst.mkdir()
    ext.mkdir()
    shutil.copy(raiz / ".env.ejemplo", inst / ".env.ejemplo")
    monkeypatch.setattr(pp, "ROOT", inst)
    monkeypatch.setattr(pp, "ENV", inst / ".env")
    args = ["preparar_piloto.py", "--externo", str(ext), "--permitir-misma-unidad", "--sin-base", "--puerto", "8123"]
    monkeypatch.setattr(sys, "argv", args)
    for mala in ("Admin-Piloto-Lab-2026", "Admin-Laboratorio-2026", "corta1", "sololetrassinnumeros"):
        monkeypatch.setenv("MON_PWD", mala)
        assert pp.main() == 1 and not (inst / ".env").exists(), mala
    monkeypatch.setenv("MON_PWD", "Propia-y-Larga-7391")
    assert pp.main() == 0
    env = pp.leer_env(inst / ".env")
    assert env["MONITOREO_ENTORNO"] == "produccion" and env["MONITOREO_PRODUCCION_FLUJO_ADMIN"] == "1"
    assert env["MONITOREO_DATOS_DEMO"] == "0" and env["MONITOREO_EXIGIR_CONSENTIMIENTO"] == "1"
    assert env["MONITOREO_PUERTO"] == "8123" and env["MONITOREO_HOST"] == "127.0.0.1"
    assert env["MONITOREO_DIARIO_COPIA"] == str(ext / "diario") and (ext / "diario").is_dir()
    assert env["MONITOREO_RESPALDO_EXTERNO"] == str(ext / "respaldos") and (ext / "respaldos").is_dir()
    assert len(env["MONITOREO_SECRET_KEY"]) >= 32 and env["MONITOREO_ADMIN_PASSWORD"] == "Propia-y-Larga-7391"
    assert "MONITOREO_DB_URL" not in env and "MONITOREO_DATA_DIR" not in env       # base = data/monitoreo.db
    assert env.get("MONITOREO_CAPTURA_SOLO_SINTETICOS", "1") == "1"                 # solo datos ficticios
    from monitoreo import entorno
    assert entorno.review(dict(env)).errores == []
    blob = (inst / ".env").read_text("utf-8")
    assert "Lab-2026" not in blob and "Laboratorio-2026" not in blob
    # segunda pasada: conserva .env (con copia) y sigue válida; --comprobar la da por buena
    assert pp.main() == 0 and list(inst.glob(".env.antes-de-piloto-*"))
    monkeypatch.setattr(sys, "argv", ["preparar_piloto.py", "--comprobar"])
    assert pp.main() == 0
    # una .env con contraseña de laboratorio no pasa la comprobación
    (inst / ".env").write_text(blob.replace("Propia-y-Larga-7391", "Admin-Piloto-Lab-2026"), "utf-8")
    assert pp.main() == 1
