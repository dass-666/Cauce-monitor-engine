"""Prueba conjunta Monitor <-> Conector Universal REAL (cauce.conector.v1), solo laboratorio y datos ficticios.

Se omite si CONECTOR_REPO no apunta a una copia del Conector Universal con Playwright/Chromium utilizables.
No activa `aislamiento.puede_lanzar()`; los criterios de aceptación son los del contrato, no se relajan para pasar.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
CONECTOR = os.environ.get("CONECTOR_REPO", "")
pytestmark = pytest.mark.skipif(not CONECTOR or not (Path(CONECTOR) / "cauce_form_connector").is_dir(),
                                reason="defina CONECTOR_REPO (Conector Universal, solo lectura, commit aa1bb02)")
solo_linux = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="control del navegador por CDP solo en Linux; en Windows queda pendiente")

if CONECTOR:
    _spec = importlib.util.spec_from_file_location("arnes_conjunta", RAIZ / "scripts" / "prueba_conjunta_conector.py")
    A = importlib.util.module_from_spec(_spec)
    sys.modules["arnes_conjunta"] = A
    _spec.loader.exec_module(A)
    from monitoreo.conector import aislamiento, contrato as C
    from monitoreo.conector.registro import ErrorRegistro


@pytest.fixture()
def ent(tmp_path):
    e = A.Entorno(tmp_path / "lab")
    yield e
    e.cerrar()


def validar_y_registrar(ent, r):
    """Lo que Monitor haría con lo recibido: esquema + PII + huella + coherencia, y luego el Registro."""
    if r.resp is not None:
        assert C.validar_respuesta(r.resp, r.sol, r.codigo) == [], (r.resp, r.stderr[-200:])
        assert C.SALIDA[r.resp["estado"]] == r.codigo
    return ent.recibir(r)


def sin_datos_personales(ent, r):
    """stdout, stderr y TODOS los archivos del entorno temporal: ni un valor ficticio."""
    texto = r.stdout + r.stderr
    for v in A.VALORES.values():
        if len(v) >= 3:
            assert v not in texto, f"valor en stdout/stderr: {v[:3]}…"
    assert "zz" not in texto.lower()
    assert ent.hallazgos_en_archivos() == []


def lanzar(ent, sol, accion=None, **kw):
    ent.autorizar(sol)
    return ent.lanzar(sol, accion, **kw)


# ---------------------------------------------------------------- 1. verificación inicial (contratos)
def test_esquemas_identicos():
    """Mismo contenido. Se normalizan SOLO los finales de línea: en Windows `git checkout` puede convertirlos a CRLF en
    una de las copias (no cambia el esquema). Además se comparan los JSON ya interpretados."""
    for nombre in ("solicitud", "respuesta"):
        monitor = (RAIZ / "monitoreo" / "conector" / "esquemas" / f"{nombre}.schema.json").read_bytes()
        conector = (Path(CONECTOR) / "cauce_form_connector" / "contrato_v1" / f"{nombre}.schema.json").read_bytes()
        assert monitor.replace(b"\r\n", b"\n") == conector.replace(b"\r\n", b"\n"), f"el esquema {nombre} difiere"
        assert json.loads(monitor) == json.loads(conector)


def test_mapa_de_estados_y_salidas_coinciden_con_el_codigo_del_conector():
    sys.path.insert(0, CONECTOR)
    from cauce_form_connector import contrato_v1 as CC
    assert (CC.VERSION, CC.SALIDA, CC.MAPA_ESTADO) == (C.VERSION, C.SALIDA, C.MAPA_ESTADO)


def test_estados_exigidos_por_el_contrato():
    """Perfil alterado, repetida, caducada, preparación sin envío, incierto, navegador cerrado."""
    m = C.MAPA_ESTADO
    assert m["rechazado_no_autorizado"] == "RECHAZADA"                       # perfil alterado
    assert m["rechazado_solicitud_invalida"] == "RECHAZADA"                  # repetida / caducada (código distingue)
    assert m["preparado_revisado_sin_envio"] == "PREPARADO"                  # preparación sin envío
    assert m["revision_caducada"] == "CADUCADA"                              # nadie revisó
    assert m["error_interno"] == "ERROR"                                     # navegador desaparecido
    assert "INCIERTA" in C.ESTADOS_SOLICITUD and "INCIERTA" not in m.values()   # solo la deriva Monitor


def test_puede_lanzar_sigue_bloqueado_y_el_arnes_no_lo_usa():
    assert aislamiento.puede_lanzar()[0] is False
    import ast
    arbol = ast.parse((RAIZ / "scripts" / "prueba_conjunta_conector.py").read_text(encoding="utf-8"))
    llamadas = [n for n in ast.walk(arbol) if isinstance(n, ast.Call) and getattr(n.func, "attr", getattr(n.func, "id", "")) == "puede_lanzar"]
    assert llamadas == []                       # el arnés no consulta ni activa la puerta operativa


# ---------------------------------------------------------------- 2. interoperabilidad
@solo_linux
def test_preparacion_completa_sin_envio_y_cierre_por_la_persona(ent):
    sol = ent.solicitud(espera=60)
    r = lanzar(ent, sol, A.persona_cierra_ventana, cdp=True, **ent.con_traza())
    assert r.nota == "ventana_cerrada" and r.codigo == 0, (r.codigo, r.nota, r.stderr[-300:])
    assert r.resp["estado"] == "preparado_revisado_sin_envio"
    assert r.resp["envio"] == {"realizado_por_conector": False, "envio_humano_observado": False}
    assert r.resp["revision_humana"]["resultado"] == "cerrada_sin_envio"
    v = r.valores_pagina                                                      # lo que había en la página al cerrar
    assert (v["nombre"], v["apellidos"], v["email"], v["telefono"], v["pais"]) == tuple(
        A.VALORES[k] for k in ("nombre", "apellidos", "email", "telefono", "pais")) and v["acepta_terminos"] is True
    assert v["fecha_nacimiento"] == "" and v["mensaje"] == ""                 # lo no aprobado no se toca
    assert {c["campo"] for c in r.resp["campos"]} == set(A.VALORES) and not r.resp["errores"]
    assert set(ent.sitio.metodos()) == {"GET"}                                # ningún POST: nada se envió
    assert validar_y_registrar(ent, r)["estado"] == "PREPARADO"
    sin_datos_personales(ent, r)


@solo_linux
def test_nadie_revisa_caduca_y_no_es_aprobacion(ent):
    r = lanzar(ent, ent.solicitud(espera=6), A.persona_no_hace_nada, cdp=True)
    assert r.resp["estado"] == "revision_caducada" and r.codigo == 7, (r.codigo, r.resp and r.resp["estado"])
    assert r.resp["revision_humana"]["resultado"] == "caducada"
    assert set(ent.sitio.metodos()) == {"GET"}
    assert validar_y_registrar(ent, r)["estado"] == "CADUCADA"
    sin_datos_personales(ent, r)


# ---------------------------------------------------------------- 3. pruebas negativas
def test_solicitud_repetida_el_conector_y_monitor_la_rechazan_sin_abrir_el_navegador(ent):
    sol = ent.solicitud(espera=0)
    r1 = lanzar(ent, sol)
    assert r1.resp["estado"] == "revision_caducada"          # espera 0: prepara y expira (primer uso consumido)
    validar_y_registrar(ent, r1)
    antes = len(ent.sitio.peticiones)
    r2 = ent.lanzar(sol, registrar=False)                    # reenvío saltándose a Monitor: debe frenar el conector
    assert (r2.resp["estado"], r2.codigo) == ("rechazado_solicitud_invalida", 6)
    assert [e["codigo"] for e in r2.resp["errores"]] == ["SOLICITUD_REPETIDA"]
    assert C.validar_respuesta(r2.resp, sol, r2.codigo) == []
    assert len(ent.sitio.peticiones) == antes                # ni siquiera se pidió la página
    with pytest.raises(ErrorRegistro):                       # Monitor: misma clave = mismo registro ya cerrado, no se relanza
        ent.autorizar(sol)
    with pytest.raises(ErrorRegistro) as ex:                 # y con otra clave, el id es de un solo uso
        ent.registro.crear(sol, "otra-clave-distinta", datetime.now(timezone.utc))
    assert ex.value.codigo == "SOLICITUD_REPETIDA"
    sin_datos_personales(ent, r2)


def test_clave_de_idempotencia_de_monitor_no_duplica(ent):
    sol = ent.solicitud()
    ahora = datetime.now(timezone.utc)
    reg, nuevo = ent.registro.crear(sol, "clave-idem-123456", ahora)
    assert nuevo
    otra = ent.solicitud()                                               # mismo contenido, otro solicitud_id
    reg2, nuevo2 = ent.registro.crear(otra, "clave-idem-123456", ahora)
    assert not nuevo2 and reg2["solicitud_id"] == sol["solicitud_id"]    # devuelve lo guardado, no crea otra
    distinta = ent.solicitud(valores={**A.VALORES, "nombre": "Zzotro"})
    with pytest.raises(ErrorRegistro) as ex:
        ent.registro.crear(distinta, "clave-idem-123456", ahora)
    assert ex.value.codigo == "IDEMPOTENCIA_CONFLICTO"


def test_solicitud_caducada_no_se_lanza_y_el_conector_tambien_la_rechaza(ent):
    ahora = datetime.now(timezone.utc)
    sol = ent.solicitud(ahora=ahora - timedelta(minutes=20))          # emitida hace 20 min; caduca hace 10
    with pytest.raises(ErrorRegistro):
        ent.autorizar(sol)                                            # puerta de Monitor: no se lanza
    antes = len(ent.sitio.peticiones)
    r = ent.lanzar(sol, registrar=False)                              # saltándose la puerta: el conector la frena igual
    assert (r.resp["estado"], r.codigo) == ("rechazado_solicitud_invalida", 6)
    assert [e["codigo"] for e in r.resp["errores"]] == ["SOLICITUD_CADUCADA"]
    assert len(ent.sitio.peticiones) == antes
    sin_datos_personales(ent, r)


def test_perfil_alterado_un_byte_se_rechaza_sin_teclear(ent):
    p = ent.info["archivo"]
    p.write_bytes(p.read_bytes() + b" ")
    r = lanzar(ent, ent.solicitud())
    assert (r.resp["estado"], r.codigo) == ("rechazado_no_autorizado", 3)
    assert [e["codigo"] for e in r.resp["errores"]] == ["PERFIL_NO_COINCIDE"] and r.resp["campos"] == []
    assert validar_y_registrar(ent, r)["estado"] == "RECHAZADA"
    sin_datos_personales(ent, r)


def test_estructura_cambiada_detiene_y_no_teclea(ent):
    ent.sitio.paginas["/f.html"] = ent.html.replace("Nombre", "Nombre completo", 1)    # cambia una etiqueta
    assert ent.sitio.paginas["/f.html"] != ent.html
    r = lanzar(ent, ent.solicitud())
    assert (r.resp["estado"], r.codigo) == ("detenido_estructura_cambiada", 5), (r.resp and r.resp["estado"])
    assert r.resp["campos"] == [] and set(ent.sitio.metodos()) == {"GET"}
    assert validar_y_registrar(ent, r)["estado"] == "DETENIDA"
    sin_datos_personales(ent, r)


def test_correspondencia_ambigua_campo_no_aprobado_no_se_rellena(ent):
    sol = ent.solicitud(valores={**A.VALORES, "mensaje": "Zzsorpresa"})                    # «mensaje» no está en el perfil
    r = lanzar(ent, sol)
    assert (r.resp["estado"], r.codigo) == ("rechazado_solicitud_invalida", 6)
    assert [e["codigo"] for e in r.resp["errores"]] == ["CAMPO_NO_DECLARADO"] and r.resp["campos"] == []
    assert "Zzsorpresa" not in r.stdout + r.stderr
    sin_datos_personales(ent, r)


def test_dominio_externo_lo_rechaza_monitor_y_tambien_el_conector(ent):
    sol = ent.solicitud()
    sol["formulario"]["url"] = "http://sitio-externo.example/f.html"
    with pytest.raises(ErrorRegistro) as ex:                 # Monitor: solo 127.0.0.1 en la fase de laboratorio
        ent.autorizar(sol)
    assert "PORTAL_NO_LOCAL" in str(ex.value)
    r = ent.lanzar(sol, registrar=False)                     # y si alguien lo lanza igualmente, el conector lo frena
    assert (r.resp["estado"], r.codigo) == ("rechazado_no_autorizado", 3)
    assert [e["codigo"] for e in r.resp["errores"]] == ["FORMULARIO_NO_AUTORIZADO"] and r.resp["campos"] == []


def test_captcha_no_admite_perfil_aprobado_ni_teclas(ent):
    import subprocess
    url = ent.sitio.base + "/captcha.html"
    p = subprocess.run([sys.executable, "-m", "cauce_form_connector", "aprobar-perfil", "--url", url, "--perfil", "perfil_cap",
                        "--formulario-id", "frm_cap", "--aprobado-por", "rev_lab", "--campos", str(ent.dir / "campos_aprobados.json")],
                       cwd=ent.dir / "trabajo", env=ent.env(), capture_output=True, text=True, timeout=120)
    assert p.returncode == 4 and not (ent.dir / "perfiles" / "perfil_cap.json").exists()   # exige intervención humana


def test_autorizacion_revocada_antes_de_lanzar_no_se_lanza(ent):
    sol = ent.solicitud()
    sol["autorizacion"]["consentimiento_vigente"] = False
    with pytest.raises(ErrorRegistro):                       # Monitor la rechaza ya en el esquema/puerta
        ent.autorizar(sol)
    assert ent.sitio.peticiones == []                        # no se tocó el formulario
    r = ent.lanzar(sol, registrar=False)                     # y si alguien lo lanza igualmente, el conector lo frena
    assert r.codigo == 6 and r.resp is None and r.stdout.strip() == ""     # esquema: sin consentimiento → solo código 6
    assert ent.sitio.peticiones == []
    sin_datos_personales(ent, r)


@solo_linux
def test_revocacion_a_mitad_monitor_termina_el_arbol_y_queda_incierta(ent):
    """Monitor cancela: pide terminar (SIGTERM), espera una gracia corta y mata el ÁRBOL (como detener_arbol)."""
    def cancela(ent_, proc):
        v, _ = A.persona_no_hace_nada(ent_, proc)
        proc.send_signal(__import__("signal").SIGTERM)
        try:
            proc.wait(timeout=8)
        except Exception:
            A._matar_arbol(proc)
            return v, "arbol_matado_tras_gracia"
        return v, "salio_solo"
    sol = ent.solicitud(espera=60)
    r = lanzar(ent, sol, cancela, cdp=True)
    assert r.resp is None and r.stdout.strip() == ""                                              # sin respuesta
    assert ent.recibir(r)["estado"] == "INCIERTA"
    with pytest.raises(ErrorRegistro) as ex:                                                      # solo una persona concilia
        ent.registro.mover(sol["solicitud_id"], "PREPARADO", datetime.now(timezone.utc))
    assert ex.value.codigo == "CONCILIACION_REQUERIDA"
    sin_datos_personales(ent, r)


@solo_linux
def test_navegador_que_desaparece_no_cuenta_como_revision(ent):
    r = lanzar(ent, ent.solicitud(espera=60), A.navegador_muere, cdp=True)
    assert r.nota == "navegador_matado"
    assert r.resp is not None and r.resp["estado"] != "preparado_revisado_sin_envio", (
        "HALLAZGO: un navegador muerto se informó como revisión humana completa")
    assert r.resp["estado"] == "error_interno" and validar_y_registrar(ent, r)["estado"] == "ERROR"
    sin_datos_personales(ent, r)


@solo_linux
def test_ventana_cerrada_no_es_aprobacion_ni_envio(ent):
    """Con la revisión ya iniciada, cerrar la ventana da PREPARADO (40 de 40 corridas de investigación). PREPARADO NO es
    aprobación humana: Monitor exige su propia confirmación (ver test_confirmacion_humana.py)."""
    r = lanzar(ent, ent.solicitud(espera=60), A.persona_cierra_ventana, cdp=True, **ent.con_traza())
    assert validar_y_registrar(ent, r)["estado"] == "PREPARADO"
    assert set(ent.sitio.metodos()) == {"GET"}
    assert r.resp["envio"]["envio_humano_observado"] is False and r.resp["envio"]["realizado_por_conector"] is False
    assert "aprob" not in json.dumps(r.resp).lower()                      # la respuesta no afirma aprobación humana
    sin_datos_personales(ent, r)


@solo_linux
def test_cierre_antes_de_terminar_el_relleno_da_error_y_nunca_exito(ent):
    """CAUSA de los «2 de 10» (reproducida 25 de 25 veces cerrando sin esperar): si la ventana se cierra mientras el conector
    aún rellena o verifica, Playwright lanza TargetClosedError y el conector responde error_interno (ERROR). Es seguro:
    no hay PREPARADO ni envío. Un cierre limpio no se distingue de este caso por el código (ver informe)."""
    r = lanzar(ent, ent.solicitud(espera=60), A.persona_cierra_de_inmediato, cdp=True, **ent.con_traza())
    assert r.nota == "cierre_inmediato"
    assert r.resp["estado"] in ("error_interno", "preparado_revisado_sin_envio")      # carrera: nunca un éxito de envío
    assert r.resp["envio"] == {"realizado_por_conector": False, "envio_humano_observado": False}
    assert set(ent.sitio.metodos()) == {"GET"}
    sin_datos_personales(ent, r)


def test_lanzador_de_laboratorio_termina_todo_el_arbol_del_conector_real(ent):
    """Monitor cancela a mitad con el lanzador de laboratorio: no queda ni el conector ni Chromium ni sus hijos."""
    import threading
    import psutil
    sys.path.insert(0, str(RAIZ / "scripts"))
    from lanzador_lab import LanzadorLab
    sol = ent.solicitud(espera=120)
    ent.autorizar(sol)
    ent.registro.mover(sol["solicitud_id"], "LANZADA", datetime.now(timezone.utc))
    lan = LanzadorLab(modo_laboratorio=True, gracia_s=3)
    marca = str(ent.tmp)

    def del_entorno():                                   # procesos cuyo comando menciona el temporal de este ensayo
        out = []
        for p in psutil.process_iter(["cmdline"]):
            try:
                if any(marca in a for a in (p.info["cmdline"] or [])) and p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
                    out.append(p.pid)
            except psutil.Error:
                pass
        return out
    visto = {"n": 0}

    def cancelar_cuando_haya_navegador():
        fin = time.monotonic() + 60
        while time.monotonic() < fin and not del_entorno():
            time.sleep(0.1)
        time.sleep(1.0)
        visto["n"] = len(del_entorno())
        lan.cancelar()
    import time
    threading.Thread(target=cancelar_cuando_haya_navegador, daemon=True).start()
    r = lan.lanzar([sys.executable, "-m", "cauce_form_connector", "preparar"], entrada=json.dumps(sol),
                   cwd=ent.dir / "trabajo", env=ent.env(), plazo_s=120)
    assert visto["n"] >= 1, "el navegador nunca llegó a arrancar: la prueba no demostraría nada"
    assert r.estado == "CANCELADO" and r.sobrevivientes == [] and r.stdout.strip() == ""
    assert del_entorno() == []        # el conector puede salir solo con la señal ordenada (CI) o hacer falta forzar (contenedor root)
    assert ent.registro.proceso_terminado_sin_respuesta(sol["solicitud_id"], datetime.now(timezone.utc))["estado"] == "INCIERTA"
    assert set(ent.sitio.metodos()) <= {"GET"}
    texto = r.stdout + r.stderr
    assert all(v not in texto for v in A.VALORES.values() if len(v) >= 3)
    assert ent.hallazgos_en_archivos() == []
