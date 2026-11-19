"""v2.2.2 · Integración: reglas para fijar la rama y el commit que Jarvis supervisa sin referencias
autocontradictorias, y suite `planificador` con las pruebas del Supervisor.

Problemas que resuelve (ver docs/INFORME_v2.2.2_integracion.md):
1. Un commit no puede contener su propio hash: el commit fijado es siempre el último commit de CÓDIGO y el
   que escribe `commit_fijado` es posterior y solo de documentación (relación POSTERIOR_SOLO_DOCUMENTOS).
2. Antes, `rutas_solo_documentacion` incluía todo `jarvis/`: cambiar después del fijado el manifiesto
   (qué se ejecuta), el esquema o los argv/políticas de project.yaml se clasificaba como «solo
   documentos». Ahora esos cambios cuentan como código.
3. Las rutas de documentación valen las del commit fijado: ampliarlas después no oculta código.
"""
from __future__ import annotations

import json
import shutil

import pytest

from monitoreo.interop import comun, manifiesto, yaml_simple
from monitoreo.interop.comun import RAIZ

from .test_estabilizacion_v222 import _g, _repo

YAML_BASE = """formato: cauce.jarvis.project/1
integracion:
  rama: integra
  commit_fijado: {commit}
  rutas_solo_documentacion:
    - docs/
    - README.md
herramientas_autorizadas:
  - id: informe
    argv:
      - "{{python}}"
      - informe
politicas:
  comandos_arbitrarios: false
"""


@pytest.fixture
def repo(tmp_path):
    if not shutil.which("git"):
        pytest.skip("requiere git")
    raiz = _repo(tmp_path)
    (raiz / "jarvis" / "project.yaml").write_text(YAML_BASE.format(commit="0" * 40), encoding="utf-8")
    (raiz / "jarvis" / "test-manifest.json").write_text('{"suites": []}\n', encoding="utf-8")
    _g(raiz, "add", "-A")
    _g(raiz, "commit", "-q", "-m", "contrato")
    return raiz


def _fijar_head(raiz):
    """Lo que hace una persona al fijar: escribe el hash del último commit de código en un commit nuevo."""
    codigo = _g(raiz, "rev-parse", "HEAD")
    texto = (raiz / "jarvis" / "project.yaml").read_text(encoding="utf-8")
    import re
    texto = re.sub(r"commit_fijado: [0-9a-f]{40}", f"commit_fijado: {codigo}", texto)
    (raiz / "jarvis" / "project.yaml").write_text(texto, encoding="utf-8")
    _g(raiz, "commit", "-qam", "fijar")
    return codigo


def test_fijar_en_commit_posterior_no_es_contradictorio(repo):
    codigo = _fijar_head(repo)
    r = comun.relacion_con_fijado(repo)
    assert r["commit_fijado"] == codigo
    assert r["relacion"] == "POSTERIOR_SOLO_DOCUMENTOS" and r["commits_desde_fijado"] == 1
    assert r["archivos_de_codigo_cambiados"] == 0 and r["rama_actual_coincide"] is True


def test_cambiar_el_manifiesto_tras_fijar_es_codigo(repo):
    _fijar_head(repo)
    (repo / "jarvis" / "test-manifest.json").write_text('{"suites": [{"id": "otra"}]}\n', encoding="utf-8")
    _g(repo, "commit", "-qam", "manifiesto")
    r = comun.relacion_con_fijado(repo)
    assert r["relacion"] == "POSTERIOR_CON_CODIGO" and r["archivos_de_codigo_cambiados"] == 1


def test_cambiar_argv_o_politicas_de_project_yaml_tras_fijar_es_codigo(repo):
    _fijar_head(repo)
    p = repo / "jarvis" / "project.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("comandos_arbitrarios: false", "comandos_arbitrarios: true"),
                 encoding="utf-8")
    _g(repo, "commit", "-qam", "politica")
    assert comun.relacion_con_fijado(repo)["relacion"] == "POSTERIOR_CON_CODIGO"


def test_cambiar_solo_el_bloque_integracion_es_documentacion(repo):
    _fijar_head(repo)
    p = repo / "jarvis" / "project.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("  rama: integra\n", "  rama: integra\n  fijado_en: \"2026-11-19\"\n"),
                 encoding="utf-8")
    (repo / "docs" / "a.md").write_text("otra\n")
    _g(repo, "commit", "-qam", "docs")
    assert comun.relacion_con_fijado(repo)["relacion"] == "POSTERIOR_SOLO_DOCUMENTOS"


def test_ampliar_las_rutas_despues_no_oculta_codigo(repo):
    _fijar_head(repo)
    p = repo / "jarvis" / "project.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("    - README.md\n", "    - README.md\n    - monitoreo/\n"),
                 encoding="utf-8")
    (repo / "monitoreo" / "x.py").write_text("x = 3\n")
    _g(repo, "commit", "-qam", "esconder")
    r = comun.relacion_con_fijado(repo)
    assert r["relacion"] == "POSTERIOR_CON_CODIGO" and r["archivos_de_codigo_cambiados"] == 1


def test_el_repositorio_real_esta_fijado_cuando_se_esta_en_la_rama_declarada():
    """Guarda contra olvidar volver a fijar: en la rama de integración, HEAD debe ser el commit fijado o
    posterior SOLO con documentación. En cualquier otra rama no aplica (se omite)."""
    if not shutil.which("git"):
        pytest.skip("requiere git")
    r = comun.relacion_con_fijado(RAIZ)
    if not r["rama_actual_coincide"]:
        pytest.skip(f"no se está en la rama declarada ({r['rama_declarada']})")
    if r["relacion"] == "DESCONOCIDO" and comun.git(RAIZ, "rev-parse", "--is-shallow-repository")[1] == "true":
        # Clon superficial (p. ej. actions/checkout sin fetch-depth: 0): el commit fijado no está en el
        # clon y no se puede comparar. En un clon completo, DESCONOCIDO sí es un fallo.
        pytest.skip("clon superficial: el commit fijado no está disponible")
    assert r["relacion"] in ("FIJADO", "POSTERIOR_SOLO_DOCUMENTOS"), r


def test_project_yaml_real_sin_jarvis_entero_como_documentacion():
    d = yaml_simple.cargar((RAIZ / "jarvis" / "project.yaml").read_text(encoding="utf-8"))
    rutas = d["integracion"]["rutas_solo_documentacion"]
    assert "jarvis/" not in rutas and "docs/" in rutas


def test_suite_planificador_incluye_el_supervisor():
    m = manifiesto.cargar(RAIZ)
    argv = m.suites["planificador"].argv
    for f in ("tests/test_planificador.py", "tests/test_plan_unidades_propiedad.py",
              "tests/test_observador_persistente.py", "tests/test_supervisor_v222.py"):
        assert f in argv, argv
    texto = json.loads((RAIZ / "jarvis" / "test-manifest.json").read_text(encoding="utf-8"))
    assert [s["id"] for s in texto["suites"]] == ["interop", "humo", "consentimiento", "planificador", "completa"]


def test_recorridos_escriben_sus_archivos_en_la_carpeta_temporal(tmp_path):
    """Hallazgo v2.2.2: scripts/integracion_http.py (y los demás que usan base_env) arrancaban el motor
    sin MONITOREO_DATA_DIR y dejaban data/notificaciones.log en la carpeta del proyecto."""
    import sys
    sys.path.insert(0, str(RAIZ / "scripts"))
    try:
        from verificar_arranque import base_env
    finally:
        sys.path.remove(str(RAIZ / "scripts"))
    for entorno in ("desarrollo", "produccion"):
        env = base_env(tmp_path, entorno)
        assert env["MONITOREO_DATA_DIR"].startswith(str(tmp_path)), env["MONITOREO_DATA_DIR"]
