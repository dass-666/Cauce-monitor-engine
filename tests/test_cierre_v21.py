"""Cierre v2.1 · regresiones de la primera validación en el servidor Linux de Diego:

1. Playwright no encontraba Chromium (instalado en `<instalación>/.browsers`, buscado en
   `~/.cache/ms-playwright`) y nada lo explicaba.
2. Los ERROR de Node del observador se descartaban (stderr a /dev/null, líneas ERROR sin registrar).
3. `recorrido_v21.py` caía con `KeyError: 'eventos'` ante una respuesta HTTP de error.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from monitoreo import navegador
from monitoreo.texto_seguro import RespuestaInesperada, cuerpo_esperado, sanitizar

ROOT = Path(__file__).resolve().parent.parent


def _hay_playwright() -> bool:
    if not shutil.which("node"):
        return False
    return subprocess.run(["node", "-e", "require('playwright')"], capture_output=True, cwd=str(ROOT / "scripts"),
                          env=navegador.entorno()).returncode == 0


con_playwright = pytest.mark.skipif(not _hay_playwright(), reason="requiere Node + playwright")


# ---------------------------------------------------------------------------------- texto seguro
def test_sanitizar_quita_secretos_y_datos_personales_y_conserva_lo_util():
    crudo = ("browserType.launch: Executable doesn't exist at /root/.cache/ms-playwright/chromium_headless_shell-1243/x\n"
             "╔══════╗\n║ Looks like Playwright was just installed ║\n╚══════╝ "
             "url http://127.0.0.1:8200/observar/calendario?sucursal=NOR-1&token=abc123 "
             "token=s3cr3t password: \"una clave\" Authorization: Bearer eyJhbGciOi.xyz "
             "correo persona@ejemplo.test tel 5550004101 y +52 55 1234 5678 CURP GODE561231HDFRRN09 "
             "fecha 2026-12-05 folio DIA-20261205-00001D puerto 127.0.0.1:8200\x1b[31m")
    s = sanitizar(crudo, 1000)
    for fuga in ("abc123", "s3cr3t", "una clave", "eyJhbGciOi", "persona@", "5550004101", "1234 5678",
                 "GODE561231", "sucursal=NOR-1", "\n", "\x1b", "╔"):
        assert fuga not in s, (fuga, s)
    for util in ("Executable doesn't exist at /root/.cache/ms-playwright/chromium_headless_shell-1243",
                 "Looks like Playwright was just installed", "2026-12-05", "DIA-20261205-00001D", "127.0.0.1:8200",
                 "http://127.0.0.1:8200/observar/calendario?…"):
        assert util in s, (util, s)
    assert len(sanitizar("x" * 1000)) == 300 and sanitizar(None) == ""


def _resp(codigo, cuerpo=None, texto=None, metodo="GET", url="http://127.0.0.1:8000/api/observaciones?desde=3"):
    req = httpx.Request(metodo, url)
    if texto is not None:
        return httpx.Response(codigo, text=texto, request=req, headers={"content-type": "text/html"})
    return httpx.Response(codigo, json=cuerpo, request=req)


def test_cuerpo_esperado_devuelve_la_clave_o_un_diagnostico_seguro():
    assert cuerpo_esperado(_resp(200, {"eventos": [1]}), "eventos") == [1]
    assert cuerpo_esperado(_resp(200, {"a": 1})) == {"a": 1}
    # el caso de la validación: la respuesta no trae «eventos»
    with pytest.raises(RespuestaInesperada) as e:
        cuerpo_esperado(_resp(503, {"detail": "Base de datos no disponible token=abc persona@ejemplo.test"}), "eventos")
    m = str(e.value)
    assert e.value.codigo == 503
    assert "GET /api/observaciones respondió HTTP 503" in m and "«eventos»" in m and "claves recibidas: detail" in m
    assert "abc" not in m and "persona@" not in m and "desde=3" not in m, m
    # 200 pero sin la clave: también se explica (no KeyError)
    with pytest.raises(RespuestaInesperada, match=r"HTTP 200; se esperaba la clave «eventos»; claves recibidas: "
                                                  r"observadores, proceso"):
        cuerpo_esperado(_resp(200, {"observadores": [], "proceso": "x"}), "eventos")
    # cuerpo que no es JSON (p. ej. una página de error de un proxy): no se copia el contenido
    with pytest.raises(RespuestaInesperada) as e:
        cuerpo_esperado(_resp(502, texto="<html>Juan Pérez 5550004101</html>"), "eventos")
    assert "no es JSON (tipo text/html" in str(e.value) and "Juan" not in str(e.value)
    with pytest.raises(RespuestaInesperada, match="el cuerpo es list"):
        cuerpo_esperado(_resp(200, [1, 2]), "eventos")


def test_cuerpo_esperado_con_el_motor_real_sin_sesion(db):
    """Reproduce la respuesta real del motor cuando el token no vale: 401 con `detail`."""
    from fastapi.testclient import TestClient

    from monitoreo.app import create_app
    with TestClient(create_app(db.url, start_engine=False, seed_demo=False)) as c:
        r = c.get("/api/observaciones")
        with pytest.raises(RespuestaInesperada) as e:
            cuerpo_esperado(r, "eventos")
    assert str(e.value).startswith("GET /api/observaciones respondió HTTP 401; se esperaba la clave «eventos»; "
                                   "claves recibidas: detail; detalle: "), str(e.value)


@pytest.mark.parametrize("script", ["recorrido_v21.py", "aceptacion_v21.py"])
def test_los_recorridos_no_leen_claves_de_respuestas_sin_comprobar(script):
    """Guardia contra la regresión: ningún `.json()["clave"]` directo (daba KeyError sin el código HTTP)."""
    src = (ROOT / "scripts" / script).read_text(encoding="utf-8")
    assert not re.search(r"\.json\(\)\[", src), re.findall(r".*\.json\(\)\[.*", src)[:3]
    assert "except RespuestaInesperada" in src and "from monitoreo.texto_seguro import cuerpo_esperado as datos" in src


# ---------------------------------------------------------------------------------- ruta del navegador
def _falso_chromium(carpeta: Path, rev: str = "9999", sin_ventana: bool = True) -> Path:
    exe = carpeta / f"chromium-{rev}" / "chrome-linux64" / "chrome"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    if sin_ventana:
        (carpeta / f"chromium_headless_shell-{rev}" / "x").mkdir(parents=True)
    return exe


def test_ruta_del_navegador_orden_de_busqueda(tmp_path, monkeypatch):
    local = tmp_path / "instalacion" / ".browsers"
    monkeypatch.setattr(navegador, "carpeta_local", lambda: local)
    monkeypatch.setattr(navegador, "carpeta_por_omision", lambda: tmp_path / "cache" / "ms-playwright")
    # 1) nada instalado: por omisión, sin Chromium
    r = navegador.resolver({})
    assert r["origen"] == "carpeta por omisión de Playwright" and r["chromium"] is False
    assert navegador.VARIABLE not in navegador.entorno({})
    # 2) la instalación tiene su .browsers (el caso del servidor de Diego): se usa SOLA, sin exportar nada
    _falso_chromium(local)
    r = navegador.resolver({})
    assert r == {"ruta": str(local), "origen": "carpeta .browsers de la instalación", "chromium": True}
    assert navegador.entorno({"PATH": "x"}) == {"PATH": "x", navegador.VARIABLE: str(local)}
    # 3) la variable (entorno o .env) manda y nunca se pisa
    otra = tmp_path / "otra"
    assert navegador.entorno({navegador.VARIABLE: str(otra)})[navegador.VARIABLE] == str(otra)
    r = navegador.resolver({navegador.VARIABLE: str(otra)})
    assert r["ruta"] == str(otra) and r["chromium"] is False and "variable" in r["origen"]
    # 4) ruta relativa: relativa a la instalación
    assert navegador.resolver({navegador.VARIABLE: ".browsers"})["ruta"] == str((navegador.ROOT / ".browsers").resolve())


def test_env_de_la_instalacion_fija_la_ruta(tmp_path):
    """PLAYWRIGHT_BROWSERS_PATH en .env llega al entorno del motor (config carga .env sin pisar)."""
    from monitoreo import config
    env_file = tmp_path / ".env"
    env_file.write_text("PLAYWRIGHT_BROWSERS_PATH=/srv/lab/.browsers\n", encoding="utf-8")
    viejo = os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
    try:
        config._load_dotenv(env_file)
        assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == "/srv/lab/.browsers"
    finally:
        os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
        if viejo is not None:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = viejo


def test_falta_el_chromium_sin_ventana(tmp_path):
    exe = _falso_chromium(tmp_path / "b", sin_ventana=False)
    assert navegador._sin_ventana(str(exe)) is False
    exe2 = _falso_chromium(tmp_path / "c")
    assert navegador._sin_ventana(str(exe2)) is True
    assert navegador._sin_ventana("/raro/chrome") is None


@con_playwright
def test_diagnostico_real_detecta_carpeta_vacia_y_la_buena(tmp_path, monkeypatch):
    bueno = navegador.diagnosticar(probar=True)
    assert bueno["listo"] and bueno["existe"] and bueno["prueba"]["ok"], bueno
    monkeypatch.setenv(navegador.VARIABLE, str(tmp_path / "vacia"))
    malo = navegador.diagnosticar(probar=True)
    assert not malo["listo"] and not malo["existe"] and "prueba" not in malo
    assert str(tmp_path / "vacia") in malo["problemas"][0] and "preparar_navegador.py" in malo["problemas"][0]
    p = subprocess.run(["python", "-m", "monitoreo.navegador"], cwd=str(ROOT), capture_output=True, text=True,
                       env={**os.environ, navegador.VARIABLE: str(tmp_path / "vacia")})
    assert p.returncode == 1 and "FALTA ALGO" in p.stdout


# ---------------------------------------------------------------------------------- errores de Node
@con_playwright
def test_observador_registra_el_error_de_chromium_ausente_y_se_detiene_con_el_motivo(tmp_path, monkeypatch, caplog):
    """La falla de la validación: Chromium no está donde Playwright lo busca. Antes: el observador
    se relanzaba en silencio. Ahora: ERROR registrado (saneado) y, al superar el límite, se detiene
    con el último error en el motivo."""
    from monitoreo.planificador.observador import Observador
    monkeypatch.setenv(navegador.VARIABLE, str(tmp_path / "vacia"))
    caplog.set_level(logging.ERROR, logger="monitoreo.planificador")
    o = Observador(None, "NOR-1", "alta", atender=False, max_reinicios=1)
    o.iniciar()
    try:
        t0 = time.monotonic()
        while not o.fallo and time.monotonic() - t0 < 60:
            time.sleep(0.2)
    finally:
        o.detener(espera=5)
    assert o.fallo, o.estado()
    assert "se relanzó 2 veces" in o.fallo and "último error: browserType.launch: Executable doesn't exist" in o.fallo
    assert len(o.fallo) <= 200
    assert o.stats["errores_navegador"] >= 2 and o.ultimo_error["origen"] == "observador"
    assert str(tmp_path / "vacia") in o.ultimo_error["mensaje"]
    registrados = [r.getMessage() for r in caplog.records if "ERROR del navegador" in r.getMessage()]
    assert registrados and "Executable doesn't exist" in registrados[0] and "\n" not in registrados[0]
    est = o.estado()
    assert est["ultimo_error"] == o.ultimo_error and json.dumps(est)


def test_script_del_observador_avisa_si_falta_el_modulo_playwright(tmp_path):
    if not shutil.which("node"):
        pytest.skip("requiere Node")
    copia = tmp_path / "obs.js"                 # fuera de la instalación: no hay node_modules cerca
    shutil.copy(ROOT / "scripts" / "observador_calendario.js", copia)
    (tmp_path / "c.json").write_text("{}", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "NODE_PATH"}
    p = subprocess.run(["node", str(copia), str(tmp_path / "c.json")], capture_output=True, text=True, env=env,
                       cwd=str(tmp_path), timeout=60)
    lineas = [json.loads(x[4:]) for x in p.stdout.splitlines() if x.startswith("OBS ")]
    assert p.returncode == 4, (p.returncode, p.stdout, p.stderr[-300:])
    assert lineas and lineas[0]["tipo"] == "ERROR" and "no se encontró el módulo playwright" in lineas[0]["mensaje"]


def test_observador_conserva_la_salida_de_error_de_node_saneada(tmp_path, monkeypatch, caplog):
    """Si Node termina con error SIN emitir una línea ERROR (fallo antes de arrancar el script), se
    registra su salida de error, saneada."""
    if not shutil.which("node"):
        pytest.skip("requiere Node")
    from monitoreo.planificador import observador as ob
    falso = tmp_path / "falla.js"
    falso.write_text('process.stderr.write("Error: no arranca token=s3cr3t persona@ejemplo.test\\n"); process.exit(7);',
                     encoding="utf-8")
    monkeypatch.setattr(ob, "SCRIPT", falso)
    caplog.set_level(logging.ERROR, logger="monitoreo.planificador")
    o = ob.Observador(None, "NOR-1", "alta", atender=False, max_reinicios=0)
    o.iniciar()
    try:
        t0 = time.monotonic()
        while not o.fallo and time.monotonic() - t0 < 30:
            time.sleep(0.1)
    finally:
        o.detener(espera=5)
    assert o.ultimo_error and o.ultimo_error["origen"] == "node", o.estado()
    m = o.ultimo_error["mensaje"]
    assert "Error: no arranca" in m and "s3cr3t" not in m and "persona@" not in m, m
    assert "último error: Error: no arranca token=***" in o.fallo
    todo = " ".join(r.getMessage() for r in caplog.records)
    assert "s3cr3t" not in todo and "persona@" not in todo


def test_diagnostico_avisa_de_observador_detenido_y_sin_chromium(db, tmp_path, monkeypatch):
    from monitoreo import diagnostico
    from monitoreo.db import utcnow
    from monitoreo.planificador import observador as ob
    r = diagnostico.run(db)
    assert not [i for i in r["comprobaciones"] if i["area"] == "Observador"]       # sin observadores: silencio
    ob.configurar(db, "NOR-1", "alta", None, "dom", 250, "admin", utcnow())
    monkeypatch.setenv(navegador.VARIABLE, str(tmp_path / "vacia"))
    r = diagnostico.run(db)
    obs = [i for i in r["comprobaciones"] if i["area"] == "Observador"]
    assert [i["clave"] for i in obs] == ["obs-sin-chromium"] and obs[0]["estado"] == diagnostico.AVISO
    assert "preparar_navegador.py" in obs[0]["accion"]
    from monitoreo.models import ObservadorConfig
    with db.session() as s:
        c = s.get(ObservadorConfig, "NOR-1|alta")
        c.activo, c.estado, c.motivo = False, "DETENIDO_POR_FALLOS", "el navegador se relanzó 9 veces; último error: x"
        s.commit()
    r = diagnostico.run(db)
    obs = [i for i in r["comprobaciones"] if i["area"] == "Observador"]
    assert [i["clave"] for i in obs] == ["obs-fallo:NOR-1|alta"] and "último error: x" in obs[0]["detalle"]


@pytest.mark.parametrize("modulo", ["config", "verificar_arranque"])
def test_al_arrancar_se_usa_la_carpeta_browsers_de_la_instalacion(tmp_path, monkeypatch, modulo):
    """El caso del servidor de Diego, sin exportar nada: la instalación tiene `.browsers` con Chromium
    y la variable no está definida -> el proceso (motor o recorrido) la fija para sus hijos Node."""
    import importlib
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    m = importlib.import_module("monitoreo.config" if modulo == "config" else "verificar_arranque")
    atributo = "BASE_DIR" if modulo == "config" else "ROOT_INSTALACION"
    monkeypatch.setattr(m, atributo, tmp_path)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    m._navegadores_de_la_instalacion()
    assert "PLAYWRIGHT_BROWSERS_PATH" not in os.environ          # sin .browsers: no inventa nada
    _falso_chromium(tmp_path / ".browsers")
    m._navegadores_de_la_instalacion()
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(tmp_path / ".browsers")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/definida/por/diego")
    m._navegadores_de_la_instalacion()
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == "/definida/por/diego"   # nunca pisa lo definido
