"""Piloto · respaldo de la base a un medio externo, verificación, ensayo y restauración desde ese medio
(monitoreo/respaldo_externo.py).

Datos FICTICIOS, carpetas temporales y portal de laboratorio propio. En Linux todas las carpetas temporales
están en la misma unidad: cuando la prueba necesita «otra unidad» se simula el identificador de volumen.

Lo que se demuestra:
- el destino se rechaza si no existe, está en la misma unidad o dentro de data/;
- la copia escrita en el medio se relee y su SHA-256 coincide; el manifiesto no lleva datos de personas;
- un corte a mitad de copia (USB desconectada) no deja un respaldo con nombre definitivo ni borra los anteriores;
- la rotación solo borra después de verificar el nuevo;
- un byte alterado en el medio se detecta, y `migrate restaurar` se niega a usarlo;
- el ensayo restaura en una carpeta temporal y NO toca la base real ni el diario;
- restaurar DOS veces el mismo respaldo externo es idempotente: mismas discrepancias, un solo bloqueo, ninguna
  cita repetida, y la conciliación posterior deja una sola cita con el mismo folio.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from monitoreo import migrate
from monitoreo import recuperacion as rc
from monitoreo import respaldo_externo as rx
from monitoreo.app import create_app
from monitoreo.models import Reservacion

from .captura_util import ADMIN_PWD, OP_PWD
from .test_captura_dia import lab as _lab_dia, portal  # noqa: F401  (fixtures)
from .test_consentimiento_v22 import (_confirmadas_en_portal, asignado, crear, lab,  # noqa: F401
                                      revocar)
from .test_recuperacion_v24 import confirmar, estado, eventos, motivos, resolver


def _otra_unidad(monkeypatch, *externas):
    real = rc._volumen
    ext = [os.path.abspath(e) for e in externas]

    def falso(p):
        p = os.path.abspath(p)
        return "USB" if any(p.startswith(e) for e in ext) else real(Path(p))
    monkeypatch.setattr(rc, "_volumen", falso)


@pytest.fixture
def base(tmp_path, monkeypatch):
    """Una base SQLite mínima con el esquema real y una carpeta de datos aislada."""
    from monitoreo.config import settings
    from monitoreo.db import Database
    d = tmp_path / "datos"
    d.mkdir()
    monkeypatch.setattr(settings, "data_dir", d)
    for k in ("MONITOREO_DIARIO_COPIA", "MONITOREO_RESPALDO_EXTERNO"):
        monkeypatch.delenv(k, raising=False)
    rc._invalidar()
    url = f"sqlite:///{(tmp_path / 'cauce.db').as_posix()}"
    migrate.upgrade(url, make_backup=False)
    db = Database(url)
    from sqlalchemy import text
    with db.session() as s:
        s.execute(text("INSERT INTO settings(key, value) VALUES ('prueba_respaldo', '\"ficticio\"')"))
    db.engine.dispose()
    usb = tmp_path / "usb"
    usb.mkdir()
    yield {"url": url, "datos": d, "usb": usb, "db": tmp_path / "cauce.db"}
    rc._invalidar()


# ---------------------------------------------------------------- destino
def test_destino_inexistente_misma_unidad_o_dentro_de_datos_se_rechaza(base, tmp_path):
    assert not rx.revisar(tmp_path / "no-existe", base["url"])["apta"]
    r = rx.revisar(base["usb"], base["url"])
    assert not r["apta"] and r["misma_unidad"] and any("MISMA unidad" in p for p in r["problemas"])
    sub = base["datos"] / "copia"
    sub.mkdir()
    assert any("carpeta de datos" in p for p in rx.revisar(sub, base["url"], laboratorio=True)["problemas"])
    with pytest.raises(rx.ErrorRespaldo, match="no es apto"):
        rx.respaldar(base["usb"], base["url"])
    assert not list(base["usb"].iterdir()), "un destino rechazado no recibe nada"


def test_postgres_se_rechaza_con_mensaje_claro(base):
    r = rx.revisar(base["usb"], "postgresql+psycopg://u:p@localhost/x")
    assert not r["apta"] and "solo existe para SQLite" in r["problemas"][0]


# ---------------------------------------------------------------- respaldar y verificar
def test_respaldo_verificado_en_otra_unidad_con_manifiesto_sin_datos_personales(base, monkeypatch):
    _otra_unidad(monkeypatch, base["usb"])
    r = rx.respaldar(base["usb"], base["url"])
    a = Path(r["archivo"])
    assert a.parent == base["usb"].resolve() and a.name.startswith(rx.PREFIJO)
    assert rx.sha256(a) == r["sha256"] == rx.sha256(Path(r["respaldo_local"]))
    man = json.loads(rx.manifiesto_de(a).read_text("utf-8"))
    assert man["sha256"] == r["sha256"] and man["misma_unidad"] is False and man["filas"]["settings"] >= 1
    assert "ficticio" not in rx.manifiesto_de(a).read_text("utf-8"), "el manifiesto no copia contenido"
    assert not list(base["usb"].glob("*.parcial"))
    v = rx.verificar(a)
    assert v["ok"] and v["integridad"] == "ok", v
    assert rx.ultimo()["sha256"] == r["sha256"] and rx.horas_desde_ultimo() < 0.1
    assert rx.listar(base["usb"])["respaldos"][0]["manifiesto"]


def test_corte_a_mitad_de_copia_no_deja_respaldo_ni_borra_los_anteriores(base, monkeypatch):
    _otra_unidad(monkeypatch, base["usb"])
    previos = [Path(rx.respaldar(base["usb"], base["url"], conservar=1)["archivo"])]
    real_open = open

    class Corte:
        def __init__(self, f):
            self.f, self.n = f, 0

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.f.close()

        def write(self, b):
            raise OSError(5, "Error de E/S: la unidad se desconectó")

        def __getattr__(self, k):
            return getattr(self.f, k)

    def abrir(ruta, modo="r", *a, **k):
        f = real_open(ruta, modo, *a, **k)
        return Corte(f) if modo == "xb" else f
    monkeypatch.setattr("builtins.open", abrir)
    with pytest.raises(OSError):
        rx.respaldar(base["usb"], base["url"], conservar=1)
    monkeypatch.setattr("builtins.open", real_open)
    quedan = rx.respaldos_en(base["usb"])
    assert quedan == previos, "el anterior sigue y no hay respaldo a medias con nombre definitivo"
    assert not list(base["usb"].glob("*.parcial")), "el provisional se borra"
    assert rx.verificar(previos[0])["ok"]


def test_copia_distinta_en_el_medio_se_detecta_y_no_se_conserva(base, monkeypatch):
    _otra_unidad(monkeypatch, base["usb"])
    real = rx.sha256
    llamadas = {"n": 0}

    def sha_falsa(p):
        llamadas["n"] += 1
        return real(p) if llamadas["n"] == 1 else "0" * 64     # lo releído del medio no coincide
    monkeypatch.setattr(rx, "sha256", sha_falsa)
    with pytest.raises(rx.ErrorRespaldo, match="no coincide"):
        rx.respaldar(base["usb"], base["url"])
    assert not list(base["usb"].iterdir())


def test_rotacion_conserva_los_n_mas_recientes_tras_verificar(base, monkeypatch):
    _otra_unidad(monkeypatch, base["usb"])
    hechos = [Path(rx.respaldar(base["usb"], base["url"], conservar=2)["archivo"]).name for _ in range(4)]
    quedan = [p.name for p in rx.respaldos_en(base["usb"])]
    assert quedan == sorted(hechos[-2:], reverse=True)
    assert sorted(p.name for p in base["usb"].glob("*.json")) == sorted(x.replace(".db", ".json") for x in quedan)


def test_byte_alterado_en_el_medio_se_detecta_y_restaurar_se_niega(base, monkeypatch):
    _otra_unidad(monkeypatch, base["usb"])
    a = Path(rx.respaldar(base["usb"], base["url"])["archivo"])
    antes = rx.sha256(base["db"])
    b = bytearray(a.read_bytes())
    b[len(b) // 2] ^= 0xFF
    a.write_bytes(bytes(b))
    v = rx.verificar(a)
    assert not v["ok"] and any("SHA-256" in p for p in v["problemas"])
    with pytest.raises(migrate.MigrationError, match="no supera la verificación"):
        migrate.restore(base["url"], str(a))
    assert rx.sha256(base["db"]) == antes, "la base real no se tocó"


def test_ensayo_restaura_en_temporal_sin_tocar_la_base_ni_el_diario(base, monkeypatch):
    _otra_unidad(monkeypatch, base["usb"])
    rc._anexar("BLOQUEO", {"motivo": "PRUEBA"})            # que exista un diario que vigilar
    a = Path(rx.respaldar(base["usb"], base["url"])["archivo"])
    h_db, h_diario = rx.sha256(base["db"]), rx.sha256(rc.ruta_diario())
    r = rx.ensayar(a)
    assert r["ok"], r
    assert r["revision_respaldo"] == r["revision_instalada"]
    assert rx.sha256(base["db"]) == h_db and rx.sha256(rc.ruta_diario()) == h_diario
    assert not [x for x in base["datos"].iterdir() if x.name.startswith("cauce-ensayo")]


def test_consola_codigos_de_salida(base, monkeypatch, capsys):
    from monitoreo.config import settings
    monkeypatch.setattr(settings, "db_url", base["url"])
    assert rx.main(["revisar", str(base["usb"])]) == 1            # misma unidad
    assert rx.main(["respaldar", str(base["usb"]), "--laboratorio"]) == 0
    a = rx.respaldos_en(base["usb"])[0]
    assert json.loads(rx.manifiesto_de(a).read_text("utf-8"))["misma_unidad"] is True
    assert rx.main(["verificar", str(a)]) == 0
    assert rx.main(["ensayar", str(a)]) == 0
    assert rx.main(["verificar", str(base["usb"] / "no-existe.db")]) == 1
    capsys.readouterr()


def test_autodiagnostico_informa_del_respaldo_externo(base, monkeypatch):
    from monitoreo import diagnostico
    from monitoreo.db import Database
    db = Database(base["url"])
    try:
        out: list = []
        diagnostico.check_respaldo_externo(out, db, None, {})
        assert out[0].clave == "respaldo_externo:sin_configurar"
        monkeypatch.setenv("MONITOREO_RESPALDO_EXTERNO", str(base["usb"]))
        out = []
        diagnostico.check_respaldo_externo(out, db, None, {})
        assert out[0].clave == "respaldo_externo:nunca"
        rx.respaldar(None, base["url"], laboratorio=True)
        out = []
        diagnostico.check_respaldo_externo(out, db, None, {})
        assert out[0].estado == "OK"
    finally:
        db.engine.dispose()


# ---------------------------------------------------------------- restauración desde el medio, dos veces
def _reiniciar_motor(lab):
    lab.c = TestClient(create_app(lab.db.url, start_engine=False, seed_demo=False))
    lab.c.__enter__()
    lab.db = lab.c.app.state.db
    lab.h = lab.login("admin", ADMIN_PWD)
    lab.ops = {x: lab.login(x, OP_PWD) for x in lab.ops}


def _restaurar(lab, archivo):
    lab.c.__exit__(None, None, None)
    lab.db.engine.dispose()
    rep = migrate.restore(lab.db.url, str(archivo))
    _reiniciar_motor(lab)
    return rep


def test_restaurar_dos_veces_desde_el_medio_es_idempotente(lab, tmp_path_factory, monkeypatch):
    if not lab.db.is_sqlite:
        pytest.skip("migrate.restore solo restaura SQLite")
    usb = tmp_path_factory.mktemp("usb")
    _otra_unidad(monkeypatch, usb)
    eid, u, h = asignado(lab)
    rid, _ = lab.hasta_fecha(eid, h)
    g = crear(lab)
    archivo = Path(rx.respaldar(usb, lab.db.url)["archivo"])
    assert rx.ensayar(archivo)["ok"]
    r = confirmar(lab, rid, h)
    assert r.status_code == 200, r.text
    folio = r.json()["reservacion"]["folio"]
    revocar(lab, g["id"])
    citas = _confirmadas_en_portal(lab)

    _restaurar(lab, archivo)
    e1 = estado(lab)
    _restaurar(lab, archivo)                                   # segunda vez, mismo archivo del medio
    e2 = estado(lab)
    clave = sorted((d["tipo"], d["ref"]) for d in e1["discrepancias"])
    assert clave == sorted((d["tipo"], d["ref"]) for d in e2["discrepancias"]), "sin discrepancias duplicadas"
    assert {d[0] for d in clave} == {"CONFIRMACION_AUSENTE", "REVOCACION_AUSENTE"}
    assert e2["bloqueado"] and "RESTAURACION_PENDIENTE" in motivos(e2)
    assert [x["tipo"] for x in eventos()].count("RESTAURACION") == 2
    assert confirmar(lab, rid, lab.ops[u]).status_code == 409
    assert _confirmadas_en_portal(lab) == citas, "ninguna confirmación se repitió"
    tipos = {d["tipo"]: d for d in e2["discrepancias"]}
    assert resolver(lab, tipos["CONFIRMACION_AUSENTE"], "registrar_folio", motivo="comprobado en el portal",
                    folio=folio).status_code == 200
    assert resolver(lab, tipos["REVOCACION_AUSENTE"], "reaplicar_revocacion",
                    motivo="revocación por escrito").status_code == 200
    # resolver otra vez lo ya resuelto no cambia nada
    assert resolver(lab, tipos["CONFIRMACION_AUSENTE"], "registrar_folio", motivo="comprobado en el portal",
                    folio=folio).status_code in (404, 409, 422)
    r = lab.c.post("/api/recuperacion/levantar", headers=lab.h, json={"motivo": "conciliación revisada"})
    assert r.status_code == 200 and r.json()["levantado"], r.text
    r = confirmar(lab, rid, lab.ops[u])
    assert r.status_code == 200 and r.json()["reservacion"]["folio"] == folio
    assert _confirmadas_en_portal(lab) == citas
    with lab.db.session() as s:
        assert s.get(Reservacion, rid).estado == "CONFIRMADA"
        assert len(list(s.scalars(select(Reservacion).where(Reservacion.folio == folio)))) == 1


def test_usb_del_diario_retirada_bloquea_y_al_reconectarla_se_recupera_sola(lab, tmp_path_factory, monkeypatch):
    """Paso 5 de docs/VALIDAR_PROTECCION_DATOS_WINDOWS.md, sin hardware: retirar la USB = la carpeta desaparece."""
    usb = tmp_path_factory.mktemp("usb")
    copia = usb / "diario"
    copia.mkdir()
    monkeypatch.setenv("MONITOREO_DIARIO_COPIA", str(copia))
    revocar(lab, crear(lab)["id"])
    assert not estado(lab)["bloqueado"]
    fuera = usb.parent / (usb.name + "-retirada")
    usb.rename(fuera)
    rc._invalidar()
    e = estado(lab)
    assert e["bloqueado"] and "COPIA_NO_DISPONIBLE" in motivos(e)
    fuera.rename(usb)
    rc._invalidar()
    e = estado(lab)
    assert not e["bloqueado"], e["motivos"]
    assert (copia / rc.ARCHIVO).read_text("utf-8") == rc.ruta_diario().read_text("utf-8")
