"""v2.2.2: regresiones de la ronda de estabilización.

1. MONITOREO_DATA_DIR opcional (sin la variable, todo igual) y test_restart.py ya no escribe en el
   data/ del proyecto.
2. jarvis/project.yaml declara la rama de integración y su commit fijado; el informe lo compara con HEAD.
3. Defecto del Supervisor de observadores: corregido; sus pruebas están en test_supervisor_v222.py.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from monitoreo.interop import comun, informe, yaml_simple
from monitoreo.interop.comun import RAIZ

# --------------------------------------------------------------------------- 1. MONITOREO_DATA_DIR

_LEER = ("import sys; sys.path.insert(0, r'%s'); from monitoreo.config import settings, BASE_DIR; "
         "import json; print(json.dumps({'data': str(settings.data_dir), 'db': settings.db_url, 'base': str(BASE_DIR)}))")


def _config(env_extra: dict) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("MONITOREO_DATA_DIR", "MONITOREO_DB_URL")}
    env.update(env_extra, MONITOREO_IGNORAR_DOTENV="1")
    r = subprocess.run([sys.executable, "-c", _LEER % RAIZ], env=env, capture_output=True, text=True, timeout=60,
                       check=True)
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_data_dir_sin_variable_es_el_de_siempre():
    c = _config({})
    assert Path(c["data"]) == Path(c["base"]) / "data"
    assert c["db"] == f"sqlite:///{(Path(c['base']) / 'data' / 'monitoreo.db').as_posix()}"


def test_data_dir_con_variable(tmp_path):
    c = _config({"MONITOREO_DATA_DIR": str(tmp_path / "datos")})
    assert Path(c["data"]) == (tmp_path / "datos").resolve()
    assert c["db"] == f"sqlite:///{((tmp_path / 'datos').resolve() / 'monitoreo.db').as_posix()}"
    # MONITOREO_DB_URL explícita sigue mandando sobre la base
    c = _config({"MONITOREO_DATA_DIR": str(tmp_path), "MONITOREO_DB_URL": "sqlite:///otra.db"})
    assert c["db"] == "sqlite:///otra.db"


def test_data_dir_vacia_equivale_a_sin_variable():
    c = _config({"MONITOREO_DATA_DIR": "   "})
    assert Path(c["data"]) == Path(c["base"]) / "data"


def _huella_data() -> str:
    return comun.huella_directorio(RAIZ / "data")


def _puerto() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_servidor_real_con_data_dir_no_toca_el_data_del_proyecto(tmp_path):
    """Lo mismo que hace test_restart.py: servidor real con el motor en marcha durante unos segundos.
    Antes de la v2.2.2 esto creaba data/feeds y data/notificaciones.log en la carpeta del proyecto."""
    antes = _huella_data()
    datos, port = tmp_path / "datos", _puerto()
    env = {**os.environ, "MONITOREO_DB_URL": f"sqlite:///{(tmp_path / 'r.db').as_posix()}",
           "MONITOREO_DATA_DIR": str(datos), "MONITOREO_ADMIN_PASSWORD": "DataDir-Prueba-123",
           "MONITOREO_PUERTO": str(port), "MONITOREO_INICIAR_MOTOR": "1", "MONITOREO_LOG": "warning"}
    p = subprocess.Popen([sys.executable, "-m", "monitoreo"], cwd=RAIZ, env=env, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    try:
        for _ in range(150):
            try:
                if httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            pytest.fail("el servidor no arrancó")
        fin = time.monotonic() + 20
        while time.monotonic() < fin and not (datos / "feeds").exists():
            time.sleep(0.3)
    finally:
        p.kill()
        p.wait()
    assert (datos / "feeds").is_dir(), "el servidor debía escribir sus archivos en MONITOREO_DATA_DIR"
    assert _huella_data() == antes, "el data/ del proyecto cambió"


def test_test_restart_usa_data_dir_temporal():
    texto = (RAIZ / "tests" / "test_restart.py").read_text(encoding="utf-8")
    assert '"MONITOREO_DATA_DIR": str(db.parent / "datos")' in texto


# --------------------------------------------------------------------------- 2. rama de integración fijada

def test_project_yaml_declara_rama_de_integracion_y_conserva_la_historica():
    d = yaml_simple.cargar((RAIZ / "jarvis" / "project.yaml").read_text(encoding="utf-8"))
    assert d["proyecto"]["rama_principal"] == "master"              # compatibilidad: el campo sigue
    assert d["proyecto"]["rama_principal_es_historica"] is True
    integ = d["integracion"]
    assert integ["rama"] and integ["rama"] != "master"
    assert len(integ["commit_fijado"]) == 40 and int(integ["commit_fijado"], 16) >= 0
    assert "docs/" in integ["rutas_solo_documentacion"]


def _repo(tmp_path: Path) -> Path:
    raiz = tmp_path / "r"
    (raiz / "jarvis").mkdir(parents=True)
    (raiz / "monitoreo").mkdir()
    (raiz / "monitoreo" / "x.py").write_text("x = 1\n")
    (raiz / "docs").mkdir()
    (raiz / "docs" / "a.md").write_text("a\n")
    _g(raiz, "init", "-q", "-b", "integra")
    _g(raiz, "add", "-A")
    _g(raiz, "commit", "-q", "-m", "base")
    return raiz


def _g(raiz: Path, *args) -> str:
    r = subprocess.run(["git", "-c", "user.name=p", "-c", "user.email=p@example.invalid", "-c", "commit.gpgsign=false",
                        *args], cwd=raiz, check=True, capture_output=True, text=True)
    return r.stdout.strip()


def _fijar(raiz: Path, rama: str, commit: str) -> None:
    (raiz / "jarvis" / "project.yaml").write_text(
        f"integracion:\n  rama: {rama}\n  commit_fijado: {commit}\n  rutas_solo_documentacion:\n    - docs/\n"
        "    - jarvis/\n", encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    if not shutil.which("git"):
        pytest.skip("requiere git")
    return _repo(tmp_path)


def test_relacion_fijado_y_posteriores(repo):
    base = _g(repo, "rev-parse", "HEAD")
    assert comun.relacion_con_fijado(repo)["relacion"] == "SIN_DECLARAR"
    _fijar(repo, "integra", base)
    r = comun.relacion_con_fijado(repo)
    # project.yaml aún sin confirmar: HEAD sigue siendo el fijado
    assert r["relacion"] == "FIJADO" and r["rama_actual_coincide"] is True
    _g(repo, "add", "-A")
    _g(repo, "commit", "-q", "-m", "fijar")
    (repo / "docs" / "a.md").write_text("b\n")
    _g(repo, "commit", "-qam", "docs")
    r = comun.relacion_con_fijado(repo)
    assert r["relacion"] == "POSTERIOR_SOLO_DOCUMENTOS" and r["commits_desde_fijado"] == 2
    (repo / "monitoreo" / "x.py").write_text("x = 2\n")
    _g(repo, "commit", "-qam", "código")
    r = comun.relacion_con_fijado(repo)
    assert r["relacion"] == "POSTERIOR_CON_CODIGO" and r["archivos_de_codigo_cambiados"] == 1


def test_relacion_otra_rama_y_commit_inexistente(repo):
    base = _g(repo, "rev-parse", "HEAD")
    _g(repo, "checkout", "-q", "--orphan", "otra")
    _g(repo, "commit", "-q", "-m", "otra historia")
    _fijar(repo, "integra", base)
    r = comun.relacion_con_fijado(repo)
    assert r["relacion"] == "DISTINTO" and r["rama_actual_coincide"] is False
    _fijar(repo, "integra", "0" * 40)
    assert comun.relacion_con_fijado(repo)["relacion"] == "DESCONOCIDO"
    _fijar(repo, "integra", "no-es-un-commit")
    assert comun.relacion_con_fijado(repo)["relacion"] == "SIN_DECLARAR"


def test_informe_real_incluye_integracion_y_valida():
    inf = informe.construir(RAIZ, ejecuciones=[])
    assert inf["integracion"]["rama_declarada"]
    assert inf["integracion"]["relacion"] in ("FIJADO", "POSTERIOR_SOLO_DOCUMENTOS", "POSTERIOR_CON_CODIGO",
                                              "DISTINTO", "DESCONOCIDO")
    assert informe.comprobar(inf, RAIZ) == []


def test_esquema_acepta_informes_anteriores_sin_integracion():
    inf = informe.construir(RAIZ, ejecuciones=[])
    inf.pop("integracion")
    inf["huella_contenido"] = informe.huella(inf)
    assert informe.comprobar(inf, RAIZ) == []


# --------------------------------------------------------------------------- 3. Supervisor de observadores
# El defecto quedó CORREGIDO en la v2.2.2 (autorizado por Diego). La prueba que aquí era xfail estricto
# pasó a tests/test_supervisor_v222.py como prueba normal, junto con las de fallos consecutivos, límite de
# reintentos, reinicios, concurrencia y persistencia.
