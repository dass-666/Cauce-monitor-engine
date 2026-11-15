"""v2.2.1: contratos de interoperabilidad con Jarvis (lado Cauce).

Cubre: manifiesto (lista blanca), ejecutor (límites, cancelación, aislamiento, candado, integridad),
exportador (estados VERIFICADO/REPORTADO/SIMULADO/PENDIENTE, huella idempotente, esquema), saneado,
y que los contratos de jarvis/ no contengan secretos ni rutas fijas.

Las ejecuciones reales del ejecutor usan un PROYECTO TEMPORAL con pruebas diminutas, para que estas
pruebas sean rápidas y no dependan de la suite del motor.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psutil
import pytest

from monitoreo.interop import comun, ejecutor, esquema, informe, inventario, manifiesto, saneado, yaml_simple
from monitoreo.interop.comun import RAIZ, ErrorSolicitud

JARVIS = RAIZ / "jarvis"


# --------------------------------------------------------------------------- proyecto temporal

PRUEBAS_TMP = {
    "test_ok.py": "def test_uno():\n    assert 1 + 1 == 2\n\ndef test_dos():\n    assert True\n",
    "test_falla.py": "def test_falla():\n    assert 1 == 2\n",
    "test_lenta.py": textwrap.dedent("""
        import os, subprocess, sys, time
        from pathlib import Path
        def test_lenta():
            hijo = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
            Path(os.environ["CAUCE_PRUEBA_PID"]).write_text(str(hijo.pid))
            time.sleep(120)
    """),
    "test_ruidosa.py": "def test_ruido():\n    print('x' * 200000)\n    assert False\n",
    "test_entorno.py": textwrap.dedent("""
        import os
        def test_entorno_aislado():
            assert os.environ.get("MONITOREO_IGNORAR_DOTENV") == "1"
            assert "MONITOREO_TELEGRAM_TOKEN" not in os.environ
            assert "MONITOREO_PORTAL_URL" not in os.environ
            assert "MONITOREO_TEST_PG" not in os.environ
            assert "HTTPS_PROXY" not in os.environ
            assert os.environ["MONITOREO_DB_URL"].startswith("sqlite:///")
            assert "secreta" not in os.environ["MONITOREO_DB_URL"]
            assert os.environ["MONITOREO_INICIAR_MOTOR"] == "0"
    """),
    "test_ensucia.py": textwrap.dedent("""
        from pathlib import Path
        def test_escribe_en_data():
            Path("data").mkdir(exist_ok=True)
            Path("data/real.db").write_text("cambio")
    """),
}


def _manifiesto_tmp(**cambios) -> dict:
    suites = [
        {"id": "ok", "argv": ["{python}", "-m", "pytest", "-q", "tests/test_ok.py"], "tiempo_s": 120},
        {"id": "falla", "argv": ["{python}", "-m", "pytest", "-q", "tests/test_falla.py"], "tiempo_s": 120},
        {"id": "lenta", "argv": ["{python}", "-m", "pytest", "-q", "tests/test_lenta.py"], "tiempo_s": 15},
        {"id": "ruidosa", "argv": ["{python}", "-m", "pytest", "-q", "tests/test_ruidosa.py"], "tiempo_s": 120,
         "salida_bytes": 4096},
        {"id": "entorno", "argv": ["{python}", "-m", "pytest", "-q", "tests/test_entorno.py"], "tiempo_s": 120},
        {"id": "ensucia", "argv": ["{python}", "-m", "pytest", "-q", "tests/test_ensucia.py"], "tiempo_s": 120},
        {"id": "cancelable", "argv": ["{python}", "-m", "pytest", "-q", "tests/test_lenta.py"], "tiempo_s": 120},
    ]
    m = {"formato": manifiesto.FORMATO, "proyecto": comun.PROYECTO_ID, "interprete": "{python}",
         "entorno_fijo": {"MONITOREO_INICIAR_MOTOR": "0"}, "suites": suites}
    m.update(cambios)
    return m


def _git(raiz: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=prueba", "-c", "user.email=prueba@example.invalid", "-c",
                    "commit.gpgsign=false", *args], cwd=raiz, check=True, capture_output=True)


@pytest.fixture
def proyecto(tmp_path) -> Path:
    raiz = tmp_path / "proyecto"
    (raiz / "tests").mkdir(parents=True)
    (raiz / "jarvis").mkdir()
    for nombre, codigo in PRUEBAS_TMP.items():
        (raiz / "tests" / nombre).write_text(codigo, encoding="utf-8")
    (raiz / "jarvis" / "test-manifest.json").write_text(json.dumps(_manifiesto_tmp()), encoding="utf-8")
    for f in ("report.schema.json", "declaraciones.json"):
        shutil.copy(JARVIS / f, raiz / "jarvis" / f)
    (raiz / ".gitignore").write_text("/resultados/\n/data/\n", encoding="utf-8")
    if shutil.which("git"):
        _git(raiz, "init", "-q")
        _git(raiz, "add", "-A")
        _git(raiz, "commit", "-q", "-m", "inicial")
    return raiz


def correr(proyecto: Path, suite: str, **kw):
    return ejecutor.ejecutar(suite, raiz=proyecto, salida=proyecto / "resultados" / "jarvis",
                             autorizadas=[proyecto.resolve()], **kw)


# --------------------------------------------------------------------------- contratos reales

def test_manifiesto_real_es_valido_y_sus_rutas_existen():
    m = manifiesto.cargar(RAIZ)
    assert {"interop", "humo", "consentimiento", "planificador", "completa"} <= set(m.suites)
    for s in m.suites.values():
        assert s.argv[:3] == ("{python}", "-m", "pytest")
        assert s.tiempo_s <= manifiesto.TIEMPO_MAX_S


def test_project_yaml_coherente_con_codigo_y_contratos():
    datos = yaml_simple.cargar((JARVIS / "project.yaml").read_text(encoding="utf-8"))
    from monitoreo.version import VERSION
    assert datos["formato"] == "cauce.jarvis.project/1"
    assert datos["proyecto"]["id"] == comun.PROYECTO_ID
    assert datos["proyecto"]["version"] == VERSION
    for ruta in datos["contratos"].values():
        assert (RAIZ / ruta).is_file(), ruta
    for h in datos["herramientas_autorizadas"]:
        assert h["argv"][:3] == ["{python}", "-m", "monitoreo.interop"], h["id"]
    pol = datos["politicas"]
    for clave in ("comandos_arbitrarios", "fusion_automatica", "despliegue_automatico", "modifica_datos_reales",
                  "modifica_codigo", "portal_externo", "captura_datos_reales"):
        assert pol[clave] is False, clave
    assert pol["estados_informe"] == list(comun.ESTADOS)
    assert datos["resultados"]["codigos_salida"]["ocupado"] == ejecutor.SALIDA_OCUPADO


def test_project_yaml_igual_con_pyyaml_si_esta_instalado():
    yaml = pytest.importorskip("yaml")
    texto = (JARVIS / "project.yaml").read_text(encoding="utf-8")
    assert yaml_simple.cargar(texto) == yaml.safe_load(texto)


def test_version_unica_en_api_y_exportador():
    from monitoreo.app import VERSION as v_app
    from monitoreo.version import VERSION
    assert v_app == VERSION == comun.version_proyecto()
    assert VERSION.startswith("2.2.1")


def test_contratos_sin_secretos_ni_rutas_privadas():
    for archivo in JARVIS.iterdir():
        texto = archivo.read_text(encoding="utf-8")
        datos = json.loads(texto) if archivo.suffix == ".json" else yaml_simple.cargar(texto)
        assert saneado.buscar_sensibles(datos, raiz=RAIZ) == [], archivo.name
        for prohibido in ("C:\\", "/home/", "/Users/", "postgresql://", "password", "token="):
            assert prohibido not in texto, (archivo.name, prohibido)


def test_esquema_solo_usa_palabras_clave_soportadas():
    # validar contra un objeto vacío recorre el esquema raíz; las $defs se recorren con un informe real abajo
    errores = esquema.validar({}, informe.cargar_esquema())
    assert any("falta 'formato'" in e for e in errores)
    with pytest.raises(esquema.EsquemaNoSoportado):
        esquema.validar({}, {"type": "object", "oneOf": []})


# --------------------------------------------------------------------------- manifiesto: lista blanca

@pytest.mark.parametrize("argv", [
    "{python} -m pytest tests",                                 # cadena para un shell
    ["bash", "-c", "rm -rf /"],
    ["{python}", "-c", "print(1)"],
    ["{python}", "-m", "pip", "install", "x"],
    ["{python}", "-m", "pytest", "-p", "malicioso", "tests/test_ok.py"],
    ["{python}", "-m", "pytest", "-k", "x", "tests/test_ok.py"],
    ["{python}", "-m", "pytest", "--rootdir=/", "tests/test_ok.py"],
    ["{python}", "-m", "pytest", "tests/../../etc/passwd"],
    ["{python}", "-m", "pytest", "/etc/passwd"],
    ["{python}", "-m", "pytest", "tests/no_existe.py"],
    ["{python}", "-m", "pytest", "-q"],
    ["{python}", "-m", "pytest", "tests/test_ok.py;", "ls"],
])
def test_manifiesto_rechaza_comandos_no_permitidos(proyecto, argv):
    m = _manifiesto_tmp(suites=[{"id": "xx", "argv": argv}])
    with pytest.raises(ErrorSolicitud):
        manifiesto.desde_datos(m, proyecto)


@pytest.mark.parametrize("cambio", [
    {"entorno_fijo": {"MONITOREO_DB_URL": "postgresql://real"}},
    {"entorno_fijo": {"MONITOREO_TELEGRAM_TOKEN": "x"}},
    {"entorno_fijo": {"PATH": "/tmp"}},
    {"entorno_fijo": {"PYTHONPATH": "/tmp"}},
    {"proyecto": "otro"},
    {"interprete": "/usr/bin/python"},
    {"formato": "otro/1"},
    {"extra": 1},
    {"suites": []},
])
def test_manifiesto_rechaza_configuracion_insegura(proyecto, cambio):
    with pytest.raises(ErrorSolicitud):
        manifiesto.desde_datos(_manifiesto_tmp(**cambio), proyecto)


def test_manifiesto_rechaza_limites_fuera_de_rango(proyecto):
    for extra in ({"tiempo_s": 0}, {"tiempo_s": 10**6}, {"salida_bytes": 10}, {"postgres": "obligatorio"},
                  {"shell": True}):
        s = {"id": "xx", "argv": ["{python}", "-m", "pytest", "tests/test_ok.py"], **extra}
        with pytest.raises(ErrorSolicitud):
            manifiesto.desde_datos(_manifiesto_tmp(suites=[s]), proyecto)


@pytest.mark.parametrize("sid", ["no-existe", "ok; rm -rf /", "../ok", "", None, "OK", "ok\n"])
def test_solo_ids_del_manifiesto(proyecto, sid):
    with pytest.raises(ErrorSolicitud):
        correr(proyecto, sid)


def test_ruta_de_proyecto_no_autorizada(proyecto, tmp_path):
    with pytest.raises(ErrorSolicitud):
        ejecutor.ejecutar("ok", raiz=proyecto, autorizadas=[(tmp_path / "otra").resolve()])
    with pytest.raises(ErrorSolicitud):
        ejecutor.ejecutar("ok", raiz=tmp_path, autorizadas=[tmp_path.resolve()])  # sin jarvis/


def test_carpeta_de_resultados_no_puede_ser_data(proyecto):
    with pytest.raises(ErrorSolicitud):
        ejecutor.ejecutar("ok", raiz=proyecto, salida=proyecto / "data", autorizadas=[proyecto.resolve()])


# --------------------------------------------------------------------------- ejecutor

def test_suite_aprobada_resultado_estructurado(proyecto):
    codigo, r = correr(proyecto, "ok")
    assert codigo == 0 and r["resultado"] == "APROBADA"
    assert r["conteo"] == {"total": 2, "aprobadas": 2, "fallidas": 0, "errores": 0, "omitidas": 0}
    d = proyecto / "resultados" / "jarvis" / r["id"]
    assert json.loads((d / "resultado.json").read_text(encoding="utf-8")) == r
    assert r["salida"]["sha256"] == comun.sha256_archivo(d / "salida.txt")
    assert r["comando"] == ["{python}", "-m", "pytest", "-q", "tests/test_ok.py"]  # sin rutas locales
    assert r["integridad"] == {"git_sin_cambios": True, "data_sin_cambios": True}
    assert "passed" in r["salida"]["resumen_final"]
    assert saneado.buscar_sensibles(r, raiz=proyecto) == []
    assert not (proyecto / "resultados" / "jarvis" / ".ejecutor.lock").exists()


def test_suite_fallida(proyecto):
    codigo, r = correr(proyecto, "falla")
    assert codigo == ejecutor.SALIDA_FALLIDA and r["resultado"] == "FALLIDA"
    assert r["conteo"]["fallidas"] == 1


def test_tiempo_agotado_detiene_todo_el_arbol(proyecto, monkeypatch, tmp_path):
    archivo_pid = tmp_path / "nieto.pid"
    monkeypatch.setitem(os.environ, "PATH", os.environ.get("PATH", ""))
    monkeypatch.setattr(ejecutor, "VARIABLES_SISTEMA", (*ejecutor.VARIABLES_SISTEMA, "CAUCE_PRUEBA_PID"))
    monkeypatch.setenv("CAUCE_PRUEBA_PID", str(archivo_pid))
    t0 = time.monotonic()
    codigo, r = correr(proyecto, "lenta")
    assert codigo == ejecutor.SALIDA_TIEMPO and r["resultado"] == "TIEMPO_AGOTADO"
    assert time.monotonic() - t0 < 90
    pid = int(archivo_pid.read_text())
    _sin_proceso(pid)


def _sin_proceso(pid: int):
    fin = time.monotonic() + 15
    while time.monotonic() < fin:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return
        except psutil.NoSuchProcess:
            return
        time.sleep(0.2)
    pytest.fail(f"el proceso nieto {pid} sigue vivo")


def test_cancelacion_por_archivo(proyecto, monkeypatch, tmp_path):
    monkeypatch.setattr(ejecutor, "VARIABLES_SISTEMA", (*ejecutor.VARIABLES_SISTEMA, "CAUCE_PRUEBA_PID"))
    monkeypatch.setenv("CAUCE_PRUEBA_PID", str(tmp_path / "pid"))
    salida = proyecto / "resultados" / "jarvis"

    def cancelar_luego():
        fin = time.monotonic() + 60
        while not (tmp_path / "pid").exists() and time.monotonic() < fin:
            time.sleep(0.1)
        (salida / "CANCELAR").write_text("")

    threading.Thread(target=cancelar_luego, daemon=True).start()
    codigo, r = correr(proyecto, "cancelable")
    assert codigo == ejecutor.SALIDA_CANCELADA and r["resultado"] == "CANCELADA"
    _sin_proceso(int((tmp_path / "pid").read_text()))
    (salida / "CANCELAR").unlink()


def test_cancelacion_por_evento(proyecto, monkeypatch, tmp_path):
    monkeypatch.setattr(ejecutor, "VARIABLES_SISTEMA", (*ejecutor.VARIABLES_SISTEMA, "CAUCE_PRUEBA_PID"))
    monkeypatch.setenv("CAUCE_PRUEBA_PID", str(tmp_path / "pid"))
    ev = threading.Event()

    def cancelar_luego():
        fin = time.monotonic() + 60
        while not (tmp_path / "pid").exists() and time.monotonic() < fin:
            time.sleep(0.1)
        ev.set()

    threading.Thread(target=cancelar_luego, daemon=True).start()
    codigo, _ = correr(proyecto, "cancelable", cancelar=ev)
    assert codigo == ejecutor.SALIDA_CANCELADA


def test_limite_de_salida(proyecto):
    codigo, r = correr(proyecto, "ruidosa")
    assert codigo == ejecutor.SALIDA_FALLIDA
    assert r["salida"]["truncada"] is True and r["salida"]["bytes"] > 200000
    archivo = proyecto / "resultados" / "jarvis" / r["id"] / "salida.txt"
    assert archivo.stat().st_size < 4096 + 300


def test_entorno_aislado_no_hereda_secretos(proyecto, monkeypatch):
    monkeypatch.setenv("MONITOREO_TELEGRAM_TOKEN", "123456789:" + "A" * 35)
    monkeypatch.setenv("MONITOREO_PORTAL_URL", "https://portal-real.invalid")
    monkeypatch.setenv("MONITOREO_DB_URL", "postgresql://u:secreta@h/real")
    monkeypatch.setenv("MONITOREO_TEST_PG", "postgresql+psycopg://u:p@h/real")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:1")
    codigo, r = correr(proyecto, "entorno")
    assert codigo == 0, (proyecto / "resultados" / "jarvis" / r["id"] / "salida.txt").read_text()


def test_postgres_solo_con_base_de_pruebas():
    assert ejecutor.url_pg_de_pruebas("postgresql+psycopg://u:p@h:5432/monitoreo_test")
    assert ejecutor.url_pg_de_pruebas("postgresql://u@h/pruebas")
    assert ejecutor.url_pg_de_pruebas("postgresql://u@h/ci_cauce")
    assert not ejecutor.url_pg_de_pruebas("postgresql+psycopg://u:p@h/monitoreo")
    assert not ejecutor.url_pg_de_pruebas("postgresql://u@h/produccion")
    assert not ejecutor.url_pg_de_pruebas("postgresql://u@h/latest")  # "test" dentro de otra palabra
    assert not ejecutor.url_pg_de_pruebas("sqlite:///test.db")
    assert not ejecutor.url_pg_de_pruebas(None)


def test_postgres_rechazado_queda_como_aviso(proyecto, monkeypatch):
    m = manifiesto.desde_datos(_manifiesto_tmp(suites=[{"id": "xx", "argv": ["{python}", "-m", "pytest",
                                                         "tests/test_ok.py"], "postgres": "opcional"}]), proyecto)
    env, avisos = ejecutor.entorno_hijo(m, m.suite("xx"), Path("a.db"), True,
                                        {"MONITOREO_TEST_PG": "postgresql://u@h/monitoreo"})
    assert "MONITOREO_TEST_PG" not in env and avisos
    env, avisos = ejecutor.entorno_hijo(m, m.suite("xx"), Path("a.db"), True,
                                        {"MONITOREO_TEST_PG": "postgresql://u@h/monitoreo_test"})
    assert env["MONITOREO_TEST_PG"].endswith("monitoreo_test") and not avisos
    env, _ = ejecutor.entorno_hijo(m, m.suite("xx"), Path("a.db"), False,
                                   {"MONITOREO_TEST_PG": "postgresql://u@h/monitoreo_test"})
    assert "MONITOREO_TEST_PG" not in env  # sin --con-postgres no se pasa


def test_candado_impide_dos_ejecuciones_y_se_recupera_si_queda_huerfano(proyecto):
    salida = proyecto / "resultados" / "jarvis"
    salida.mkdir(parents=True)
    candado = salida / ".ejecutor.lock"
    vivo = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        candado.write_text(str(vivo.pid))
        with pytest.raises(ejecutor.ErrorOcupado):
            correr(proyecto, "ok")
    finally:
        vivo.kill()
        vivo.wait()
    candado.write_text(str(vivo.pid))  # proceso ya terminado: candado huérfano
    codigo, _ = correr(proyecto, "ok")
    assert codigo == 0 and not candado.exists()


def test_detecta_si_la_suite_cambia_data(proyecto):
    codigo, r = correr(proyecto, "ensucia")
    assert codigo == 0
    assert r["integridad"]["data_sin_cambios"] is False
    assert any("data/" in a for a in r["avisos"])


# --------------------------------------------------------------------------- exportador

@pytest.fixture
def con_git():
    if not shutil.which("git"):
        pytest.skip("requiere git")


def test_informe_verificado_valido_contra_esquema(proyecto, con_git):
    _, r = correr(proyecto, "ok")
    inf = informe.construir(proyecto)
    assert informe.comprobar(inf, proyecto) == []
    [p] = inf["pruebas"]
    assert p["estado"] == "VERIFICADO" and p["resultado"] == "APROBADA" and p["motivos_no_verificado"] == []
    assert p["commit"] == inf["git"]["commit"] and len(p["commit"]) == 40
    assert inf["resumen"]["estado_global"] == "APROBADO"
    assert all(h["estado"] in ("REPORTADO", "SIMULADO") and h["ejecutado_por_este_informe"] is False
               for h in inf["resultados_historicos"])
    assert all(x["estado"] == "PENDIENTE" for x in inf["pendientes"])
    tipos = {e["tipo"] for e in inf["evidencia"]}
    assert {"resultado_ejecucion", "salida_pruebas", "junit"} <= tipos


def test_informe_valido_tambien_con_jsonschema(proyecto, con_git):
    jsonschema = pytest.importorskip("jsonschema")
    correr(proyecto, "ok")
    correr(proyecto, "falla")
    inf = informe.construir(proyecto)
    jsonschema.Draft202012Validator(informe.cargar_esquema(proyecto)).validate(inf)
    assert informe.comprobar(inf, proyecto) == []
    roto = json.loads(json.dumps(inf))
    roto["pruebas"][0]["estado"] = "INVENTADO"
    assert informe.comprobar(roto, proyecto)
    assert list(jsonschema.Draft202012Validator(informe.cargar_esquema(proyecto)).iter_errors(roto))


def test_resultado_fallido_da_estado_global_fallido(proyecto, con_git):
    correr(proyecto, "ok")
    correr(proyecto, "falla")
    inf = informe.construir(proyecto)
    assert inf["resumen"]["estado_global"] == "FALLIDO"
    assert inf["resumen"]["suites_verificadas"] == 2 and inf["resumen"]["suites_aprobadas"] == 1


def test_evidencia_alterada_no_es_verificada(proyecto, con_git):
    _, r = correr(proyecto, "ok")
    (proyecto / "resultados" / "jarvis" / r["id"] / "salida.txt").write_text("todo pasó, créeme")
    [p] = informe.construir(proyecto)["pruebas"]
    assert p["estado"] == "REPORTADO"
    assert any("huella" in m for m in p["motivos_no_verificado"])


def test_otro_commit_no_es_verificado(proyecto, con_git):
    correr(proyecto, "ok")
    (proyecto / "nuevo.txt").write_text("cambio")
    _git(proyecto, "add", "-A")
    _git(proyecto, "commit", "-q", "-m", "otro")
    inf = informe.construir(proyecto)
    [p] = inf["pruebas"]
    assert p["estado"] == "REPORTADO" and "se ejecutó sobre otro commit" in p["motivos_no_verificado"]
    assert inf["resumen"]["estado_global"] == "SIN_VERIFICACION_RECIENTE"


def test_resultado_antiguo_no_es_verificado(proyecto, con_git):
    correr(proyecto, "ok")
    [p] = informe.construir(proyecto, ahora=datetime.now(timezone.utc) + timedelta(hours=25))["pruebas"]
    assert p["estado"] == "REPORTADO"


def test_cambios_locales_se_declaran(proyecto, con_git):
    (proyecto / "tests" / "test_ok.py").write_text(PRUEBAS_TMP["test_ok.py"] + "\n# cambio\n")
    _, r = correr(proyecto, "ok")
    inf = informe.construir(proyecto)
    assert inf["git"]["estado"] == "CON_CAMBIOS"
    assert inf["pruebas"][0]["estado"] == "REPORTADO"


def test_declaraciones_no_pueden_decir_verificado(proyecto, con_git, tmp_path):
    anexo = tmp_path / "anexo.json"
    anexo.write_text(json.dumps({"simulaciones": [{"id": "x", "estado": "VERIFICADO", "descripcion": "trampa"}]}))
    with pytest.raises(informe.ErrorInforme):
        informe.construir(proyecto, anexos=[anexo])
    anexo.write_text(json.dumps({"simulaciones": [{"id": "consulta-jarvis", "descripcion": "simulada"}]}))
    inf = informe.construir(proyecto, anexos=[anexo])
    assert inf["simulaciones"][-1]["estado"] == "SIMULADO"


def test_huella_idempotente(proyecto, con_git):
    correr(proyecto, "ok")
    a = informe.construir(proyecto)
    time.sleep(1.1)
    correr(proyecto, "ok")  # repetir: otra ejecución, mismo commit, mismo resultado
    b = informe.construir(proyecto)
    assert a["generado_en"] != b["generado_en"] or a["pruebas"][0]["ejecucion_id"] != b["pruebas"][0]["ejecucion_id"]
    assert a["huella_contenido"] == b["huella_contenido"]
    correr(proyecto, "falla")
    assert informe.construir(proyecto)["huella_contenido"] != a["huella_contenido"]


def test_exportar_escribe_y_cli_valida(proyecto, con_git):
    correr(proyecto, "ok")
    destino = proyecto / "resultados" / "jarvis" / "informe.json"
    informe.exportar(destino, raiz=proyecto)
    datos = json.loads(destino.read_text(encoding="utf-8"))
    assert informe.comprobar(datos, proyecto) == []
    datos["version"] = "x"
    assert informe.comprobar(datos, proyecto)  # el esquema y la huella lo detectan


def test_exportador_no_escribe_si_queda_un_dato_sensible(proyecto, con_git, monkeypatch):
    correr(proyecto, "ok")
    monkeypatch.setattr(informe, "sanear", lambda obj, raiz=None: obj)  # simula un fallo del saneado
    decl = json.loads((proyecto / "jarvis" / "declaraciones.json").read_text(encoding="utf-8"))
    decl["pendientes"].append({"id": "fuga", "descripcion": "escribir a persona@example.com"})
    (proyecto / "jarvis" / "declaraciones.json").write_text(json.dumps(decl), encoding="utf-8")
    destino = proyecto / "resultados" / "informe.json"
    with pytest.raises(informe.ErrorInforme):
        informe.exportar(destino, raiz=proyecto)
    assert not destino.exists()


# --------------------------------------------------------------------------- saneado

@pytest.mark.parametrize("texto,no_debe", [
    ("escribe a juan.perez@correo.mx", "juan.perez"),
    ("postgresql+psycopg://postgres:pg-secreta@localhost/db", "pg-secreta"),
    ("bot 123456789:AAHk3l2jd9sl3kd9sk3ld9sk3ld9sk3ld9s ok", "AAHk3l2jd9"),
    ("CURP GODE561231HDFRRN09 registrada", "GODE561231HDFRRN09"),
    ("teléfono 5512345678", "5512345678"),
    ("password=hunter22 y token: abcdef123", "hunter22"),
    (r"C:\Users\Diego\Cauce\data", "Diego"),
    ("/home/diego/cauce/data", "diego"),
])
def test_saneado_quita_datos_sensibles(texto, no_debe):
    limpio = saneado.sanear_texto(texto)
    assert no_debe not in limpio
    assert saneado.buscar_sensibles({"t": limpio}) == []
    assert saneado.buscar_sensibles({"t": texto})


def test_saneado_reemplaza_la_ruta_del_proyecto(tmp_path):
    raiz = tmp_path / "Cauce"
    assert saneado.sanear_texto(f"falló {raiz / 'tests' / 'x.py'}", raiz) == "falló <proyecto>" + os.sep + \
        os.path.join("tests", "x.py")
    assert saneado.buscar_sensibles({"x": str(raiz)}, raiz=raiz)


def test_saneado_respeta_commits_y_fechas():
    commit = "4d6d8c3a4c85d87c06e5d31b6ee00082d95c9c40"
    assert saneado.sanear_texto(commit) == commit
    assert saneado.sanear_texto("2026-11-15T01:41:19Z 701 passed in 785.96s") == "2026-11-15T01:41:19Z 701 passed in 785.96s"
    assert saneado.buscar_sensibles({"c": commit, "h": "a" * 64}) == []


# --------------------------------------------------------------------------- YAML y esquema

@pytest.mark.parametrize("texto", [
    "a: &x 1\nb: *x\n", "a:\n\t- 1\n", "a: [1, 2]\n", "a: {b: 1}\n", "a: yes\n", "a: |\n  x\n",
    "a: 1\na: 2\n", "a: b: c\n", "---\na: 1\n---\nb: 2\n", "- a\nb: 1\n",
])
def test_yaml_simple_rechaza_lo_no_soportado(texto):
    with pytest.raises(yaml_simple.ErrorYaml):
        yaml_simple.cargar(texto)


def test_yaml_simple_basico():
    texto = 'a: 1\nb: "x # no es comentario"  # sí\nc:\n  - uno\n  - d: 2\n    e: true\nf: null\ng: []\n'
    assert yaml_simple.cargar(texto) == {"a": 1, "b": "x # no es comentario", "c": ["uno", {"d": 2, "e": True}],
                                         "f": None, "g": []}


# --------------------------------------------------------------------------- inventario y compatibilidad

def test_inventario_del_repositorio_real():
    inv = inventario.construir(RAIZ)
    m = inv["migraciones"]
    assert m["lineal"] and m["cabezas"] == ["0015_v2_2"] and m["total"] >= 15
    assert inv["pruebas"]["archivos"] >= 50
    assert any(i["vigente"] for i in inv["informes"])
    assert all(s["codificacion_ok"] for s in inv["scripts_windows"]), inv["scripts_windows"]
    assert saneado.buscar_sensibles(inv, raiz=RAIZ) == []
    assert "interop" in inv["modulos"]


def test_ignorar_dotenv_solo_con_la_variable(tmp_path):
    """MONITOREO_IGNORAR_DOTENV=1 evita leer .env; sin ella, todo sigue como antes."""
    pkg = tmp_path / "copia" / "monitoreo"
    pkg.mkdir(parents=True)
    shutil.copy(RAIZ / "monitoreo" / "config.py", pkg / "config.py")
    (tmp_path / "copia" / ".env").write_text("MONITOREO_MARCA_PRUEBA=desde_env\n", encoding="utf-8")
    codigo = ("import os, importlib.util as u; s=u.spec_from_file_location('c', r'%s'); m=u.module_from_spec(s); "
              "import sys; sys.modules['c']=m; s.loader.exec_module(m); print(os.environ.get('MONITOREO_MARCA_PRUEBA', 'no'))" % (pkg / "config.py"))
    env = {k: v for k, v in os.environ.items() if k not in ("MONITOREO_IGNORAR_DOTENV", "MONITOREO_MARCA_PRUEBA")}
    salida = subprocess.run([sys.executable, "-c", codigo], env=env, capture_output=True, text=True, check=True)
    assert salida.stdout.strip() == "desde_env"
    salida = subprocess.run([sys.executable, "-c", codigo], env={**env, "MONITOREO_IGNORAR_DOTENV": "1"},
                            capture_output=True, text=True, check=True)
    assert salida.stdout.strip() == "no"


def test_cli_suites_y_solicitud_invalida():
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, "-m", "monitoreo.interop", "suites"], cwd=RAIZ, capture_output=True,
                       text=True, encoding="utf-8", env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    ids = {s["id"] for s in json.loads(r.stdout.strip().splitlines()[-1])["suites"]}
    assert "humo" in ids
    r = subprocess.run([sys.executable, "-m", "monitoreo.interop", "ejecutar", "--suite", "humo; del *"], cwd=RAIZ,
                       capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
    assert r.returncode == ejecutor.SALIDA_SOLICITUD
    assert json.loads(r.stdout.strip().splitlines()[-1])["error"] == "SOLICITUD_NO_PERMITIDA"


def test_control_positivo_del_manifiesto(proyecto):
    """Las pruebas de rechazo de arriba solo valen si un manifiesto correcto SÍ se acepta."""
    m = manifiesto.desde_datos(_manifiesto_tmp(suites=[{"id": "xx", "argv": ["{python}", "-m", "pytest", "-q", "-rs",
                                                          "tests/test_ok.py::test_uno"], "postgres": "opcional"}]),
                               proyecto)
    assert m.suite("xx").argv[-1] == "tests/test_ok.py::test_uno"
