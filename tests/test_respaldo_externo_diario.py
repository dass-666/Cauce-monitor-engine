"""Copia externa del diario de eventos críticos: revisión de la carpeta SIN activarla, exportación de la clave
con huella y avisos del autodiagnóstico (monitoreo/recuperacion.py, docs/RESPALDO_EXTERNO_DIARIO.md).

Solo carpetas temporales y eventos de control (sin datos de personas). En Linux todas las carpetas temporales
están en la misma unidad: cuando la prueba necesita «otra unidad» se simula el identificador de volumen.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from monitoreo import recuperacion as rc


@pytest.fixture
def datos(tmp_path, monkeypatch):
    from monitoreo.config import settings
    d = tmp_path / "datos"
    d.mkdir()
    monkeypatch.setattr(settings, "data_dir", d)
    for k in ("MONITOREO_DIARIO_COPIA", "MONITOREO_DIARIO_DIR", "MONITOREO_DIARIO_CLAVE_ARCHIVO"):
        monkeypatch.delenv(k, raising=False)
    rc._invalidar()
    rc._anexar("BLOQUEO", {"motivo": "PRUEBA"})
    yield d
    rc._invalidar()


def _otra_unidad(monkeypatch, carpeta_externa):
    real = rc._volumen

    def falso(p):
        p = os.path.abspath(p)
        return "USB" if p.startswith(os.path.abspath(carpeta_externa)) else real(Path(p))
    monkeypatch.setattr(rc, "_volumen", falso)


def test_carpeta_inexistente_no_es_apta(datos, tmp_path):
    r = rc.revisar_copia(tmp_path / "no-existe", probar_escritura=True)
    assert not r["apta"] and any("no está conectada" in p for p in r["problemas"])


def test_dentro_de_la_carpeta_de_datos_no_es_apta(datos):
    sub = datos / "copia"
    sub.mkdir()
    r = rc.revisar_copia(sub)
    assert not r["apta"] and any("carpeta de datos" in p for p in r["problemas"])


def test_misma_unidad_no_es_apta(datos, tmp_path):
    ext = tmp_path / "externa"
    ext.mkdir()
    r = rc.revisar_copia(ext, probar_escritura=True)
    assert r["misma_unidad"] is True
    assert not r["apta"] and any("MISMA unidad" in p for p in r["problemas"])


def test_otra_unidad_es_apta_prueba_escritura_sin_dejar_rastro_y_no_activa_nada(datos, tmp_path, monkeypatch):
    ext = tmp_path / "usb"
    ext.mkdir()
    _otra_unidad(monkeypatch, ext)
    r = rc.revisar_copia(ext, probar_escritura=True)
    assert r["apta"], r
    assert r["escritura"] == "OK" and list(ext.iterdir()) == []      # el archivo de prueba se borró
    assert rc.ruta_copia() is None and "MONITOREO_DIARIO_COPIA" not in os.environ   # no se activó


def test_consola_revisar_copia_devuelve_codigo_y_la_linea_para_env(datos, tmp_path, monkeypatch, capsys):
    ext = tmp_path / "usb"
    ext.mkdir()
    assert rc.main(["revisar-copia", str(ext)]) == 1                 # misma unidad
    _otra_unidad(monkeypatch, ext)
    assert rc.main(["revisar-copia", str(ext)]) == 0
    out = capsys.readouterr().out
    assert f"MONITOREO_DIARIO_COPIA={ext}" in out and "No se ha cambiado ninguna configuración" in out


def test_exportar_clave_deja_huella_y_marca_e_importar_la_reconoce(datos, tmp_path, monkeypatch):
    raw = rc._leer_clave()
    assert rc.revisar_copia(tmp_path)["clave"]["exportada_coincide"] is False
    dst = tmp_path / "clave_exportada.txt"
    assert rc.main(["exportar-clave", str(dst)]) == 0
    texto = dst.read_text("utf-8")
    assert rc.huella_clave(raw) in texto.splitlines()[0]
    assert rc.huella_clave(raw) != raw.hex()[:16]
    marca = rc.clave_exportada()
    assert marca["huella"] == rc.huella_clave(raw)
    assert rc.revisar_copia(tmp_path)["clave"]["exportada_coincide"] is True
    assert rc.main(["exportar-clave", str(dst)]) == 1                 # nunca sobrescribe
    # Otro equipo / otra cuenta: carpeta de datos nueva, se importa la clave exportada.
    from monitoreo.config import settings
    nuevo = tmp_path / "equipo2"
    nuevo.mkdir()
    monkeypatch.setattr(settings, "data_dir", nuevo)
    assert rc.main(["importar-clave", str(dst)]) == 0
    assert rc._leer_clave() == raw
    assert rc.clave_exportada()["origen"] == "importada"


def test_estado_avisa_si_la_copia_configurada_esta_en_la_misma_unidad(db, datos, tmp_path, monkeypatch):
    ext = tmp_path / "misma"
    ext.mkdir()
    monkeypatch.setenv("MONITOREO_DIARIO_COPIA", str(ext))
    rc._invalidar()
    with db.session() as s:
        e = rc.evaluar(s, usar_cache=False)
    assert e["copia"]["configurada"] and e["copia"]["carpeta"] == str(ext)
    assert e["copia"]["misma_unidad"] is True
    assert any("MISMA unidad" in a for a in e["avisos"])


def test_autodiagnostico_avisa_clave_sin_exportar_y_copia_no_apta(db, datos, tmp_path, monkeypatch):
    from monitoreo import diagnostico
    ext = tmp_path / "misma"
    ext.mkdir()
    monkeypatch.setenv("MONITOREO_DIARIO_COPIA", str(ext))
    rc._invalidar()
    claves = {i["clave"] for i in diagnostico.run(db, motor_detenido=True)["comprobaciones"]}
    assert "recuperacion:clave_sin_exportar" in claves
    assert "recuperacion:copia_no_apta" in claves
    assert rc.main(["exportar-clave", str(tmp_path / "k.txt")]) == 0
    rc._invalidar()
    claves = {i["clave"] for i in diagnostico.run(db, motor_detenido=True)["comprobaciones"]}
    assert "recuperacion:clave_sin_exportar" not in claves


def test_marca_de_exportacion_no_contiene_la_clave(datos, tmp_path):
    raw = rc._leer_clave()
    rc.main(["exportar-clave", str(tmp_path / "k.txt")])
    contenido = json.loads(rc._ruta_marca_exportada().read_text("utf-8"))
    assert set(contenido) == {"hora", "huella"}
    import base64
    assert base64.b64encode(raw).decode() not in rc._ruta_marca_exportada().read_text("utf-8")
