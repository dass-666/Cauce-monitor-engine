"""Laboratorio AISLADO · Ventanilla -> Forms como OTRA cuenta del sistema (PR #13 conectado al recorrido actual).

Qué se demuestra aquí, con datos FICTICIOS, el portal de laboratorio propio y permisos REALES del sistema de archivos:
- Antes de cada lanzamiento Monitor ejecuta la sonda COMO la cuenta del conector; solo si la base, la carpeta de datos,
  los respaldos, el diario, su clave y el .env dan «permiso denegado» (y no puede escribir en el código) lanza Forms.
- Un conector «fisgón» que corre como esa cuenta NO puede leer nada de eso (lo intenta de verdad).
- Control negativo: con los permisos abiertos Monitor NO lanza nada (AISLAMIENTO_NO_VERIFICADO).
- Revocar, cerrar la atención, detener Monitor, matar Monitor de golpe, árboles de procesos y desertores: no queda
  ningún proceso de la cuenta.
- El recorrido coincidencia -> Forms -> folio verificado funciona igual con Forms aislado.

Dónde corre:
- Linux (contenedor o CI): necesita root (crea la cuenta `cauce_conector_t` con useradd si falta). Es el equivalente
  de pruebas de la cuenta + ACL de Windows; NO es la MSI.
- Windows: solo en un runner EFÍMERO con CAUCE_AISLAMIENTO_EFIMERO=1, administrador, y la cuenta creada por el
  workflow (CAUCE_AISLAMIENTO_CUENTA / CAUCE_AISLAMIENTO_CLAVE). Aplica ACL NTFS a carpetas temporales.
En cualquier otro caso se omite (y lo dice).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import psutil
import pytest
from sqlalchemy import select

from monitoreo.autorrelleno import adaptador as ad
from monitoreo.autorrelleno import aislamiento_lab as al
from monitoreo.autorrelleno import coincidencia as cz
from monitoreo.config import settings
from monitoreo.conector import aislamiento
from monitoreo.models import AuditLog, Expediente, Reservacion

from .test_autorrelleno_coincidencia import folio, persona_confirma_en_portal, preparar_coincidencia
from .test_autorrelleno_forms import AUTOMATICOS, FORMS_REPO, atencion, esperar, lab, portal, pulsar  # noqa: F401

RAIZ = Path(__file__).resolve().parents[1]
ES_WINDOWS = sys.platform == "win32"
CUENTA_LINUX = "cauce_conector_t"


# ================================================================ disponibilidad
def _cuenta() -> tuple[str, str] | None:
    """(cuenta, clave) si este equipo puede ejecutar la prueba real; None si no."""
    if ES_WINDOWS:
        if os.environ.get("CAUCE_AISLAMIENTO_EFIMERO") != "1" or not os.environ.get("CAUCE_AISLAMIENTO_CUENTA"):
            return None
        return os.environ["CAUCE_AISLAMIENTO_CUENTA"], os.environ.get("CAUCE_AISLAMIENTO_CLAVE", "")
    if not sys.platform.startswith("linux") or os.geteuid() != 0:
        return None
    import pwd
    try:
        pwd.getpwnam(CUENTA_LINUX)
    except KeyError:
        if not shutil.which("useradd"):
            return None
        subprocess.run(["useradd", "-M", "-s", "/usr/sbin/nologin", "-d", "/nonexistent", CUENTA_LINUX], check=True)
    return CUENTA_LINUX, ""


CUENTA = _cuenta()
requiere_cuenta = pytest.mark.skipif(CUENTA is None, reason="sin cuenta de pruebas: Linux como root o runner Windows "
                                     "efímero (CAUCE_AISLAMIENTO_EFIMERO=1). NO verificado en esta corrida.")


def _icacls(*a):
    subprocess.run(["icacls", *map(str, a)], check=True, capture_output=True)


def _yo_windows() -> str:
    return f"{os.environ.get('USERDOMAIN')}\\{os.environ.get('USERNAME')}"


def proteger_privado(p: Path, cuenta: str):
    """Lo privado de Monitor: solo Monitor (y administradores) entran."""
    if ES_WINDOWS:
        _icacls(p, "/inheritance:r", "/grant:r", "*S-1-5-32-544:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F",
                f"{_yo_windows()}:(OI)(CI)F")
    else:
        os.chmod(p, 0o700)


def abrir_privado(p: Path, cuenta: str):
    """CONTROL NEGATIVO: la cuenta del conector puede leer la carpeta de datos (como si no hubiera ACL)."""
    if ES_WINDOWS:
        _icacls(p, "/grant", f"{cuenta}:(OI)(CI)RX")
    else:
        os.chmod(p, 0o755)
        for f in p.rglob("*"):
            os.chmod(f, 0o755 if f.is_dir() else 0o644)
        q = p.parent                                  # que se pueda atravesar hasta ella
        while q != q.parent:
            os.chmod(q, os.stat(q).st_mode | 0o005)
            q = q.parent


def preparar_raiz_conector(raiz: Path, cuenta: str) -> tuple[Path, Path]:
    """<raiz>/codigo: solo lectura para la cuenta; <raiz>/trabajo: lectura y escritura solo para la cuenta."""
    codigo, trabajo = raiz / "codigo", raiz / "trabajo"
    (codigo / "tests" / "herramientas").mkdir(parents=True)
    (trabajo / "tmp").mkdir(parents=True)
    shutil.copytree(RAIZ / "monitoreo", codigo / "monitoreo", ignore=shutil.ignore_patterns("__pycache__"))
    for f in ("forms_espia.py", "forms_fisgon.py"):
        shutil.copy(RAIZ / "tests" / "herramientas" / f, codigo / "tests" / "herramientas" / f)
    shutil.copy(RAIZ / "scripts" / "aislamiento_sonda.py", codigo / "aislamiento_sonda.py")
    if ES_WINDOWS:
        _icacls(raiz, "/inheritance:r", "/grant:r", "*S-1-5-32-544:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F",
                f"{_yo_windows()}:(OI)(CI)F")
        _icacls(raiz, "/grant", f"{cuenta}:(RX)")
        _icacls(codigo, "/grant", f"{cuenta}:(OI)(CI)RX")
        _icacls(trabajo, "/grant", f"{cuenta}:(OI)(CI)M")
        _icacls(RAIZ, "/deny", f"{cuenta}:(OI)(CI)W")       # el código de Monitor: la cuenta no escribe
    else:
        import pwd
        pw = pwd.getpwnam(cuenta)
        for d in (raiz, *raiz.rglob("*")):
            os.chmod(d, 0o755 if d.is_dir() else 0o644)
        for d in (trabajo, *trabajo.rglob("*")):
            os.chown(d, pw.pw_uid, pw.pw_gid)
        os.chmod(trabajo, 0o700)
    return codigo, trabajo


def procesos(cuenta: str) -> list[int]:
    return [p.pid for p in al.procesos_de_la_cuenta(cuenta) if p.status() != psutil.STATUS_ZOMBIE]


def esperar_sin_procesos(cuenta: str, t: float = 10.0) -> list[int]:
    fin = time.monotonic() + t
    while time.monotonic() < fin:
        if not procesos(cuenta):
            return []
        time.sleep(0.1)
    return procesos(cuenta)


@pytest.fixture
def aislado(lab, tmp_path, monkeypatch):
    if CUENTA is None:
        pytest.skip("sin cuenta de pruebas (ver requiere_cuenta)")
    cuenta, clave = CUENTA
    url = lab.db.engine.url.render_as_string(hide_password=False)
    if not url.startswith("sqlite"):
        pytest.skip("el aislamiento por archivos se prueba con SQLite (PostgreSQL protege la base con su credencial)")
    monkeypatch.setattr(settings, "db_url", url)
    datos = tmp_path                                    # carpeta de datos de Monitor (ya contiene la base a.db)
    (datos / "respaldos").mkdir(exist_ok=True)
    (datos / "respaldos" / "monitoreo-copia.db").write_bytes((datos / "a.db").read_bytes())
    # el diario de eventos críticos y su clave los crea Monitor al arrancar (no se fabrican: la recuperación los mira)
    env_privado = datos / "privado.env"                 # hace de .env de la instalación
    env_privado.write_text("MONITOREO_SECRET_KEY=secreto-de-monitor-que-no-debe-salir\n")
    proteger_privado(datos, cuenta)
    raiz = Path(tempfile.mkdtemp(prefix="cauce_aisl_", dir=("C:\\" if ES_WINDOWS else "/tmp")))
    codigo, trabajo = preparar_raiz_conector(raiz, cuenta)
    espia = trabajo / "espia"
    monkeypatch.setenv(al.VARIABLE_MODO, al.MODO_CUENTA)
    monkeypatch.setenv(al.VARIABLE_CUENTA, cuenta)
    monkeypatch.setenv(al.VARIABLE_TRABAJO, str(trabajo))
    monkeypatch.setenv(al.VARIABLE_EXTRA, str(env_privado))
    monkeypatch.setenv("MONITOREO_FORMS_COMANDO", json.dumps(
        [sys.executable, str(codigo / "tests" / "herramientas" / "forms_espia.py"), str(espia)]))
    if ES_WINDOWS:
        archivo = datos / "claves" / "conector.dpapi"
        archivo.write_bytes(al.proteger_clave(clave))
        monkeypatch.setenv(al.VARIABLE_CLAVE, str(archivo))
    lab.espia, lab.codigo, lab.trabajo, lab.datos, lab.cuenta, lab.raiz = espia, codigo, trabajo, datos, cuenta, raiz
    lab.env_privado = env_privado
    yield lab
    esperar_sin_procesos(cuenta, 15)
    if ES_WINDOWS:
        subprocess.run(["icacls", str(RAIZ), "/remove:d", cuenta], capture_output=True)
        subprocess.run(["icacls", str(raiz), "/reset", "/T", "/Q"], capture_output=True)
    shutil.rmtree(raiz, ignore_errors=True)


def fisgon(lab, monkeypatch):
    """Cambia el conector por el fisgón, que intenta abrir todo lo privado de Monitor."""
    from monitoreo.config import BASE_DIR
    rutas = [{"ref": x["ref"], "ruta": x["ruta"], "tipo": x["tipo"]}
             for x in al.rutas_privadas(al.leer_config(dict(os.environ)))]
    rutas += [{"ref": "escribir_codigo_monitor", "ruta": str(BASE_DIR / "monitoreo"), "tipo": "escribir"},
              {"ref": "escribir_codigo_conector", "ruta": str(lab.codigo), "tipo": "escribir"},
              {"ref": "escribir_trabajo", "ruta": str(lab.trabajo), "tipo": "escribir"}]
    lista = lab.codigo / "rutas_fisgon.json"
    lista.write_text(json.dumps(rutas), encoding="utf-8")
    if not ES_WINDOWS:
        os.chmod(lista, 0o644)
    monkeypatch.setenv("MONITOREO_FORMS_COMANDO", json.dumps(
        [sys.executable, str(lab.codigo / "tests" / "herramientas" / "forms_fisgon.py"), str(lab.espia), str(lista)]))
    return rutas


def esperar_pid(lab, t=20.0) -> int:
    fin = time.monotonic() + t
    while time.monotonic() < fin and not (lab.espia / "pid").exists():
        time.sleep(0.05)
    return int((lab.espia / "pid").read_text())


# ================================================================ unidad (cualquier equipo)
def test_sin_la_variable_el_laboratorio_es_el_de_siempre_y_puede_lanzar_sigue_bloqueado(lab):
    cfg = ad.configuracion()
    assert cfg.aislamiento is None and ad.comprobar_aislamiento(cfg) is None
    assert ad.estado_configuracion()["aislamiento"] == "ninguno"
    assert aislamiento.puede_lanzar()[0] is False


def test_configuracion_aislada_incompleta_o_invalida_niega(lab, monkeypatch):
    monkeypatch.setenv(al.VARIABLE_MODO, "otro")
    with pytest.raises(ad.Negado) as e:
        ad.configuracion()
    assert e.value.codigo == "AISLAMIENTO_INVALIDO"
    monkeypatch.setenv(al.VARIABLE_MODO, al.MODO_CUENTA)
    with pytest.raises(ad.Negado) as e:
        ad.configuracion()
    assert e.value.codigo == "AISLAMIENTO_INCOMPLETO"
    if not ES_WINDOWS:
        monkeypatch.setenv(al.VARIABLE_CUENTA, "cuenta_que_no_existe_zz")
        monkeypatch.setenv(al.VARIABLE_TRABAJO, "/tmp/x")
        with pytest.raises(ad.Negado) as e:
            ad.configuracion()
        assert e.value.codigo == "CUENTA_NO_EXISTE"
        monkeypatch.setenv(al.VARIABLE_CUENTA, "root")
        with pytest.raises(ad.Negado) as e:
            ad.configuracion()
        assert e.value.codigo in ("AISLAMIENTO_INVALIDO", "AISLAMIENTO_NO_DISPONIBLE")
    # producción: ni con aislamiento
    monkeypatch.setenv("MONITOREO_ENTORNO", "produccion")
    with pytest.raises(ad.Negado) as e:
        ad.configuracion()
    assert e.value.codigo == "AUTORRELLENO_DESACTIVADO"


def test_linux_sin_root_no_hay_modo_aislado(lab, monkeypatch):
    if ES_WINDOWS:
        pytest.skip("solo Linux")
    monkeypatch.setenv(al.VARIABLE_MODO, al.MODO_CUENTA)
    monkeypatch.setenv(al.VARIABLE_CUENTA, "nobody")
    monkeypatch.setenv(al.VARIABLE_TRABAJO, "/tmp/x")
    monkeypatch.setattr(al.os, "geteuid", lambda: 1000)
    with pytest.raises(ad.Negado) as e:
        ad.configuracion()
    assert e.value.codigo == "AISLAMIENTO_NO_DISPONIBLE"


def test_entorno_aislado_no_apunta_al_perfil_de_monitor():
    cfg = al.ConfigAislamiento("c", Path("/w"))
    env = al.entorno({"PATH": "/bin", "HOME": "/root", "USERPROFILE": "C:\\Users\\Diego", "APPDATA": "a",
                      "LOCALAPPDATA": "b", "TEMP": "t", "TMP": "t", "XAUTHORITY": "/root/.Xauthority",
                      "XDG_RUNTIME_DIR": "/run/user/0", "CAUCE_HEADLESS": "1"}, cfg)
    assert env["PATH"] == "/bin" and env["CAUCE_HEADLESS"] == "1"
    assert {env[k] for k in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA")} == {str(Path("/w"))}
    assert env["TEMP"] == env["TMP"] == str(Path("/w") / "tmp")
    assert "XAUTHORITY" not in env and "XDG_RUNTIME_DIR" not in env


def test_veredicto_de_la_sonda_solo_acepta_denegado():
    cfg = al.ConfigAislamiento("cauce_conector_t", Path("/w"))
    priv = [{"ref": "base", "ruta": "/d/a.db", "tipo": "archivo"}, {"ref": ".env", "ruta": "/i/.env", "tipo": "archivo"}]
    bueno = {"usuario": "cauce_conector_t", "privadas": [{"ref": "base", "resultado": "DENEGADO"},
                                                         {"ref": ".env", "resultado": "DENEGADO"}],
             "escritura": [{"ref": "codigo_de_monitor", "resultado": "DENEGADO"}], "trabajo": "PERMITIDO"}
    assert al.evaluar_sonda(bueno, cfg, priv)["ok"] is True
    for cambio, fallo in (({"privadas": [{"ref": "base", "resultado": "PERMITIDO"}, {"ref": ".env", "resultado": "DENEGADO"}]}, "base:PERMITIDO"),
                          ({"privadas": [{"ref": "base", "resultado": "NO_EXISTE"}, {"ref": ".env", "resultado": "DENEGADO"}]}, "base:NO_EXISTE"),
                          ({"privadas": [{"ref": "base", "resultado": "DENEGADO"}]}, ".env:SIN_DATO"),
                          ({"escritura": [{"ref": "codigo_de_monitor", "resultado": "PERMITIDO"}]}, "escribir_codigo_de_monitor:PERMITIDO"),
                          ({"trabajo": "DENEGADO"}, "trabajo:DENEGADO"),
                          ({"usuario": "otra"}, "CUENTA_INESPERADA")):
        v = al.evaluar_sonda({**bueno, **cambio}, cfg, priv)
        assert v["ok"] is False and fallo in v["fallos"], (cambio, v)
    assert al.evaluar_sonda(None, cfg, priv)["fallos"] == ["SONDA_SIN_RESPUESTA"]
    assert "SIN_RUTAS_PRIVADAS" in al.evaluar_sonda(bueno, cfg, [])["fallos"]
    yo = al._usuario_actual()
    assert "MISMA_CUENTA_QUE_MONITOR" in al.evaluar_sonda({**bueno, "usuario": yo}, al.ConfigAislamiento(yo, Path("/w")), priv)["fallos"]


def test_rutas_privadas_cubren_base_datos_respaldos_diario_y_env(lab, tmp_path, monkeypatch):
    url = lab.db.engine.url.render_as_string(hide_password=False)
    monkeypatch.setattr(settings, "db_url", url)
    for d in ("respaldos", "claves", "diario"):
        (tmp_path / d).mkdir(exist_ok=True)
    (tmp_path / "claves" / "diario.clave").write_bytes(b"x")
    extra = tmp_path / "privado.env"
    extra.write_text("X=1")
    refs = {x["ref"] for x in al.rutas_privadas(al.ConfigAislamiento("c", tmp_path, rutas_extra=[extra]))}
    assert {"base", "carpeta_de_datos", "respaldos", "claves", "clave_del_diario", "diario", "extra_1"} <= refs


# ================================================================ con la cuenta real (Linux root / Windows efímero)
@requiere_cuenta
def test_forms_corre_como_la_cuenta_y_no_puede_leer_nada_privado_de_monitor(aislado, monkeypatch):
    lab = aislado
    rutas = fisgon(lab, monkeypatch)
    h = lab.ops["op1"]
    a, datos = atencion(lab, h)
    st = lab.c.get(f"/api/autorrelleno/atenciones/{a['id']}", headers=h).json()
    assert st["habilitado"] and st["disponible"] and st["aislamiento"] == al.MODO_CUENTA, st
    r = pulsar(lab, a["id"], h)
    assert r.status_code == 202, r.text
    assert r.json()["aislamiento"]["verificado"] is True and r.json()["aislamiento"]["rutas_denegadas"] >= 6
    fin = esperar(lab, a["id"], h)
    assert fin["estado"] == "PREPARADO" and fin["procesos_restantes"] == 0, fin
    f = json.loads((lab.espia / "fisgon.json").read_text(encoding="utf-8"))
    # 1) corrió como la cuenta del conector, no como Monitor
    assert f["usuario"].lower().split("\\")[-1] == lab.cuenta.lower() != al._usuario_actual().lower()
    # 2) todo lo privado de Monitor: permiso denegado (ni base, ni datos, ni respaldos, ni diario, ni clave, ni .env)
    res = {x["ref"]: x["resultado"] for x in f["intentos"]}
    privadas = [x["ref"] for x in rutas if x["tipo"] != "escribir"]
    assert len(privadas) >= 6 and all(res[k] == "DENEGADO" for k in privadas), res
    assert res["escribir_codigo_monitor"] == "DENEGADO" and res["escribir_codigo_conector"] == "DENEGADO", res
    assert res["escribir_trabajo"] == "PERMITIDO"
    # 3) sin variables de Monitor; HOME y TEMP en su carpeta de trabajo, no en el perfil de Monitor
    assert not [n for n in f["entorno"] if n.startswith("MONITOREO_")]
    assert Path(f["home"]) == lab.trabajo and Path(f["temp"]) == lab.trabajo / "tmp"
    # 4) la comprobación quedó en el historial (sin rutas ni datos) y no queda ningún proceso de la cuenta
    with lab.db.session() as s:
        ev = s.scalar(select(AuditLog).where(AuditLog.action == "autorrelleno_terminado"))
        assert ev is not None
    assert esperar_sin_procesos(lab.cuenta) == []
    for v in (datos["telefono"], datos["correo"]):
        assert v not in (lab.trabajo / "espia" / "fisgon.json").read_text(encoding="utf-8")


@requiere_cuenta
def test_control_negativo_con_permisos_abiertos_no_se_lanza_nada(aislado, monkeypatch):
    lab = aislado
    fisgon(lab, monkeypatch)
    abrir_privado(lab.datos, lab.cuenta)
    h = lab.ops["op1"]
    a, _ = atencion(lab, h)
    r = pulsar(lab, a["id"], h)
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "AISLAMIENTO_NO_VERIFICADO", r.text
    assert not (lab.espia / "fisgon.json").exists() and not (lab.espia / "pid").exists()   # Forms no se lanzó
    with lab.db.session() as s:
        neg = s.scalars(select(AuditLog).where(AuditLog.action == "autorrelleno_negado")).all()
    det = neg[-1].detail
    assert det["codigo"] == "AISLAMIENTO_NO_VERIFICADO" and "base:PERMITIDO" in det["aislamiento"]["fallos"], det
    assert ad.en_curso() is None and esperar_sin_procesos(lab.cuenta) == []
    # vuelve a cerrarse -> vuelve a funcionar (la comprobación es en cada lanzamiento, no una bandera)
    proteger_privado(lab.datos, lab.cuenta)
    if not ES_WINDOWS:
        for f in lab.datos.rglob("*"):
            os.chmod(f, 0o700 if f.is_dir() else 0o600)
    else:
        _icacls(lab.datos, "/remove:g", lab.cuenta)
    assert pulsar(lab, a["id"], h).status_code == 202
    assert esperar(lab, a["id"], h)["estado"] == "PREPARADO"


@requiere_cuenta
def test_python_que_la_cuenta_no_puede_ejecutar_falla_cerrado(aislado, monkeypatch):
    if ES_WINDOWS:
        pytest.skip("en Windows se cubre con H2 del plan (Python «solo para mí»)")
    lab = aislado
    oculto = lab.datos / "py"                            # dentro de la carpeta privada de Monitor: la cuenta no entra
    oculto.mkdir()
    os.symlink(sys.executable, oculto / "python")
    monkeypatch.setenv("MONITOREO_FORMS_COMANDO", json.dumps([str(oculto / "python"), "-c", "print(1)"]))
    h = lab.ops["op1"]
    a, _ = atencion(lab, h)
    r = pulsar(lab, a["id"], h)
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "AISLAMIENTO_NO_VERIFICADO", r.text
    assert ad.en_curso() is None and esperar_sin_procesos(lab.cuenta) == []


@requiere_cuenta
def test_revocar_con_la_ventana_abierta_termina_todo_lo_de_la_cuenta(aislado, monkeypatch):
    lab = aislado
    monkeypatch.setenv("MONITOREO_FORMS_COMANDO", json.dumps(
        [sys.executable, str(lab.codigo / "tests" / "herramientas" / "forms_espia.py"), str(lab.espia), "ok", "60"]))
    h = lab.ops["op1"]
    a, _ = atencion(lab, h)
    assert pulsar(lab, a["id"], h).status_code == 202
    esperar_pid(lab)
    assert procesos(lab.cuenta), "el conector no corre como la cuenta"
    t0 = time.monotonic()
    r = lab.c.post(f"/api/consentimientos/{a['consentimiento']['id']}/revocar", headers=lab.h,
                   json={"medio": "PRESENCIAL", "motivo": "PERSONA_LO_PIDE"})
    assert r.status_code == 200, r.text
    c = esperar(lab, a["id"], h, t=30)
    assert c["estado"] == "CANCELADA" and c["codigo"] in ("CONSENTIMIENTO_NO_VIGENTE", "ATENCION_CERRADA"), c
    assert c["procesos_restantes"] == 0 and time.monotonic() - t0 < 15
    assert esperar_sin_procesos(lab.cuenta) == []


@requiere_cuenta
def test_cerrar_la_atencion_termina_forms(aislado, monkeypatch):
    lab = aislado
    monkeypatch.setenv("MONITOREO_FORMS_COMANDO", json.dumps(
        [sys.executable, str(lab.codigo / "tests" / "herramientas" / "forms_espia.py"), str(lab.espia), "ok", "60"]))
    h = lab.ops["op1"]
    a, _ = atencion(lab, h)
    assert pulsar(lab, a["id"], h).status_code == 202
    esperar_pid(lab)
    r = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/cerrar", headers=h, json={"resultado": "CANCELADA"})
    assert r.status_code == 200, r.text
    c = esperar(lab, a["id"], h, t=30)
    assert c["estado"] == "CANCELADA" and c["procesos_restantes"] == 0, c
    assert esperar_sin_procesos(lab.cuenta) == []


@requiere_cuenta
def test_detener_monitor_termina_forms_de_la_cuenta(aislado, monkeypatch):
    lab = aislado
    monkeypatch.setenv("MONITOREO_FORMS_COMANDO", json.dumps(
        [sys.executable, str(lab.codigo / "tests" / "herramientas" / "forms_espia.py"), str(lab.espia), "ok", "60"]))
    h = lab.ops["op1"]
    a, _ = atencion(lab, h)
    assert pulsar(lab, a["id"], h).status_code == 202
    esperar_pid(lab)
    c = ad.en_curso()
    t0 = time.monotonic()
    lab.cerrar()
    lab.cerrar = lambda: None
    assert time.monotonic() - t0 < 15
    assert c.estado == "CANCELADA" and c.codigo == "MONITOR_DETENIDO" and c.procesos_restantes == 0, c.vista()
    assert esperar_sin_procesos(lab.cuenta) == []


@requiere_cuenta
def test_respuesta_basura_o_error_del_conector_y_no_quedan_procesos(aislado, monkeypatch):
    lab = aislado
    h = lab.ops["op1"]
    for args, esperado in ((["-c", "print('no es json')"], "INCIERTA"), (["-c", "import sys; sys.exit(3)"], "INCIERTA")):
        monkeypatch.setenv("MONITOREO_FORMS_COMANDO", json.dumps([sys.executable, *args]))
        a, _ = atencion(lab, h)
        assert pulsar(lab, a["id"], h).status_code == 202
        c = esperar(lab, a["id"], h)
        assert c["estado"] == esperado and c["procesos_restantes"] == 0 and c["informe"] is None, c
        assert esperar_sin_procesos(lab.cuenta) == []


@requiere_cuenta
def test_arbol_desertor_cancelacion_y_plazo_con_el_lanzador_de_la_cuenta(aislado):
    lab = aislado
    cfg = al.leer_config(dict(os.environ))
    env = al.entorno({k: os.environ[k] for k in ("PATH", "SYSTEMROOT") if k in os.environ}, cfg)
    sonda = str(lab.codigo / "aislamiento_sonda.py")
    cwd = str(lab.trabajo)
    out = {}
    lz = al.fabrica(cfg)
    threading.Timer(2.0, lz.cancelar).start()
    r = lz.lanzar([sys.executable, sonda, "arbol"], cwd=cwd, env=env, plazo_s=60)
    out["cancelacion"] = (r.estado, r.sobrevivientes, esperar_sin_procesos(lab.cuenta))
    r = al.fabrica(cfg).lanzar([sys.executable, sonda, "dormir"], cwd=cwd, env=env, plazo_s=2)
    out["plazo"] = (r.estado, r.sobrevivientes, esperar_sin_procesos(lab.cuenta))
    r = al.fabrica(cfg).lanzar([sys.executable, sonda, "desertor"], cwd=cwd, env=env, plazo_s=30)
    out["desertor"] = (r.estado, r.sobrevivientes, esperar_sin_procesos(lab.cuenta), r.eliminados, r.stdout)
    assert out["cancelacion"][0] == "CANCELADO" and out["plazo"][0] == "PLAZO", out
    assert all(not v[1] and not v[2] for v in out.values()), out
    assert "desertor" in out["desertor"][4], out            # el padre llegó a dejar a su hijo
    if ES_WINDOWS:                                           # Linux lo termina con SIGTERM al grupo (no cuenta como forzado)
        assert out["desertor"][3] >= 1, out


@requiere_cuenta
def test_monitor_muere_de_golpe_y_el_siguiente_arranque_no_deja_restos(aislado):
    """kill -9 / taskkill /F de Monitor con un árbol de la cuenta vivo. Windows: el Job Object lo mata todo.
    Linux: muere el hijo directo (PDEATHSIG); un nieto separado puede quedar y lo termina `al_arrancar()`."""
    lab = aislado
    codigo = ("import json,os,sys; sys.path.insert(0, sys.argv[1]);"
              "from monitoreo.autorrelleno import aislamiento_lab as al;"
              "cfg = al.leer_config(dict(os.environ)); env = al.entorno({k: os.environ[k] for k in ('PATH','SYSTEMROOT') if k in os.environ}, cfg);"
              "al.fabrica(cfg).lanzar([sys.executable, sys.argv[2], 'arbol'], cwd=str(cfg.trabajo), env=env, plazo_s=600)")
    m = subprocess.Popen([sys.executable, "-c", codigo, str(RAIZ), str(lab.codigo / "aislamiento_sonda.py")],
                         env=dict(os.environ))
    fin = time.monotonic() + 30
    while time.monotonic() < fin and len(procesos(lab.cuenta)) < 3:
        time.sleep(0.2)
    antes = len(procesos(lab.cuenta))
    m.kill()
    m.wait(10)
    tras_matar = esperar_sin_procesos(lab.cuenta, 8)
    assert antes >= 3, "no se llegó a ver el árbol de la cuenta"
    if ES_WINDOWS:
        assert tras_matar == [], tras_matar            # Job Object: KILL_ON_JOB_CLOSE
    restos = ad.al_arrancar()                          # lo que hace el arranque de Monitor (lifespan)
    assert restos == 0 and esperar_sin_procesos(lab.cuenta) == []
    print(f"\nprocesos de la cuenta: antes={antes}, tras matar a Monitor={len(tras_matar)}, tras arrancar=0")


@requiere_cuenta
def test_un_resto_de_la_cuenta_se_termina_antes_de_lanzar(aislado):
    if ES_WINDOWS:
        pytest.skip("en Windows el Job Object no deja restos (se comprueba en la prueba anterior)")
    lab = aislado
    import pwd
    pw = pwd.getpwnam(lab.cuenta)
    resto = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], user=pw.pw_uid, group=pw.pw_gid,
                             extra_groups=[], cwd=str(lab.trabajo), start_new_session=True)
    h = lab.ops["op1"]
    a, _ = atencion(lab, h)
    assert pulsar(lab, a["id"], h).status_code == 202
    assert esperar(lab, a["id"], h)["estado"] == "PREPARADO"
    resto.wait(10)
    assert resto.returncode is not None and esperar_sin_procesos(lab.cuenta) == []


@requiere_cuenta
def test_recorrido_coincidencia_forms_folio_con_forms_aislado(aislado):
    lab = aislado
    h = lab.ops["op1"]
    k = preparar_coincidencia(lab, h)
    r = pulsar(lab, k["aid"], h)
    assert r.status_code == 202 and r.json()["aislamiento"]["verificado"] is True, r.text
    fin = esperar(lab, k["aid"], h)
    assert fin["estado"] == "PREPARADO" and fin["resultado_operacion"] == cz.LISTA, fin
    f = persona_confirma_en_portal(lab, k["exp_id"], k["fecha"], k["sucursal"])
    r = folio(lab, h, k["rid"], f)
    assert r.status_code == 200, r.text
    assert r.json()["reservacion"]["estado"] == "CONFIRMADA" and r.json()["estado_plan"] == "CONFIRMADO"
    with lab.db.session() as s:
        assert s.get(Expediente, k["exp_id"]).estado == "RESERVACION_CONFIRMADA"
        assert s.get(Reservacion, k["rid"]).evidencia["forms"]["resultado"] == cz.LISTA
    assert esperar_sin_procesos(lab.cuenta) == []


# ================================================================ Forms REAL como la cuenta (Linux, FORMS_REPO)
@pytest.fixture
def forms_real_aislado(aislado, monkeypatch):
    if not FORMS_REPO or ES_WINDOWS:
        pytest.skip("FORMS_REPO no definido (o Windows: allí A11 del workflow): Forms real aislado no verificado aquí")
    lab = aislado
    sys.path.insert(0, str(RAIZ / "scripts"))
    from autorrelleno_forms import aprobar, variables_motor
    forms = Path(FORMS_REPO).resolve()
    perfil = lab.raiz / "perfil"                         # el perfil aprobado: la cuenta lo LEE, no lo escribe
    pin = aprobar(forms, lab.portal.base, perfil, "rev_prueba")
    import pwd
    pw = pwd.getpwnam(lab.cuenta)
    for d in (perfil, *perfil.rglob("*")):
        os.chmod(d, 0o755 if d.is_dir() else 0o644)
    estado = Path(json.loads(pin.read_text())["state_dir"])
    for d in (estado, *estado.rglob("*")):
        os.chown(d, pw.pw_uid, pw.pw_gid)               # su estado: lo escribe la cuenta
    monkeypatch.delenv("MONITOREO_FORMS_COMANDO")
    for k, v in variables_motor(forms, pin).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("MONITOREO_FORMS_HEADLESS", "1")
    monkeypatch.setenv("MONITOREO_AUTORRELLENO_ESPERA_S", "30")
    return lab


@requiere_cuenta
def test_forms_real_aislado_rellena_y_chromium_es_de_la_cuenta(forms_real_aislado):
    lab = forms_real_aislado
    h = lab.ops["op1"]
    a, _ = atencion(lab, h)
    r = pulsar(lab, a["id"], h)
    assert r.status_code == 202 and r.json()["aislamiento"]["verificado"] is True, r.text
    fin = time.monotonic() + 60
    navegador = []
    while time.monotonic() < fin and not navegador:
        navegador = [p for p in al.procesos_de_la_cuenta(lab.cuenta) if "chrom" in (p.name() or "").lower()]
        time.sleep(0.3)
    assert navegador, "Chromium no corre como la cuenta del conector"
    c = esperar(lab, a["id"], h, t=150)
    assert c["estado"] == "CADUCADA", c                  # nadie cierra la ventana: la revisión caduca a los 30 s
    inf = c["informe"]
    assert sorted(x["campo"] for x in inf["campos"]) == AUTOMATICOS and all(x["correcto"] for x in inf["campos"]), inf
    assert inf["envio_realizado_por_conector"] is False
    assert c["procesos_restantes"] == 0 and esperar_sin_procesos(lab.cuenta) == []


@requiere_cuenta
def test_forms_real_aislado_revocar_a_mitad_termina_chromium(forms_real_aislado, monkeypatch):
    lab = forms_real_aislado
    monkeypatch.setenv("MONITOREO_AUTORRELLENO_ESPERA_S", "120")
    h = lab.ops["op1"]
    a, _ = atencion(lab, h)
    assert pulsar(lab, a["id"], h).status_code == 202
    fin = time.monotonic() + 60
    while time.monotonic() < fin and not [p for p in al.procesos_de_la_cuenta(lab.cuenta)
                                          if "chrom" in (p.name() or "").lower()]:
        time.sleep(0.3)
    r = lab.c.post(f"/api/consentimientos/{a['consentimiento']['id']}/revocar", headers=lab.h,
                   json={"medio": "PRESENCIAL", "motivo": "PERSONA_LO_PIDE"})
    assert r.status_code == 200
    c = esperar(lab, a["id"], h, t=60)
    assert c["estado"] == "CANCELADA" and c["procesos_restantes"] == 0, c
    assert esperar_sin_procesos(lab.cuenta) == []
