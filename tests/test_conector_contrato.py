"""Contrato unificado cauce.conector.v1: esquemas, validadores, simulador, idempotencia, caducidad y datos personales.

Datos ficticios. No usa la base de datos ni expedientes. El único subproceso que se lanza aquí es el SIMULADOR con una
solicitud sintética (lanzar el conector con datos de expedientes sigue bloqueado).
"""
import copy
import json
import os
import secrets
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from monitoreo.conector import aislamiento, contrato as C
from monitoreo.conector.registro import ErrorRegistro, Registro
from monitoreo.conector.simulador import responder

RAIZ = Path(__file__).resolve().parents[1]
T0 = datetime(2027, 1, 10, 12, 0, 0, tzinfo=timezone.utc)
CLAVE = "EXP-0001:RES-0001:preparar"


def _f(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def solicitud(**cambios):
    campos = {"nombre_completo": "Persona Ficticia Uno", "tramite": "alta", "telefono": "5500000001"}
    s = {"contrato": C.VERSION, "tipo": "preparar_formulario", "solicitud_id": "sol_" + secrets.token_hex(8),
         "emitida_en": _f(T0), "caduca_en": _f(T0 + timedelta(seconds=600)),
         "autorizacion": {"ref": "aut_51f0c2", "formulario_id": "frm_alta_demo", "otorgada_por_ref": "rev_0007",
                          "vigente_hasta": "2027-12-31", "consentimiento_vigente": True},
         "formulario": {"formulario_id": "frm_alta_demo", "url": "http://127.0.0.1:8200/alta", "perfil": "alta_demo",
                        "perfil_sha256": "3b1f0d6c9e2a47b8a1c0f5e4d3b2a19087f6e5d4c3b2a1908f7e6d5c4b3a2910",
                        "firma_estructura_esperada": "v2:8e1d4c7a0b9f2e36"},
         "campos": campos, "opciones": {"espera_revision_max_s": 300, "pausa_tecla_ms": 35}}
    s.update(cambios)
    return s


def correr(sol, modo="ok", timeout=20):
    env = {**os.environ, "CONECTOR_SIM_MODO": modo, "PYTHONPATH": str(RAIZ)}
    return subprocess.run([sys.executable, "-m", "monitoreo.conector.simulador"], input=json.dumps(sol),
                          capture_output=True, text=True, timeout=timeout, cwd=RAIZ, env=env)


# ------------------------------------------------------------------ esquemas y solicitud
def test_solicitud_valida_y_los_esquemas_son_de_la_misma_version():
    assert C.validar_solicitud(solicitud(), T0) == []
    for n in ("solicitud", "respuesta"):
        assert C.cargar_esquema(n)["$id"].startswith("cauce.conector.v1/")


@pytest.mark.parametrize("cambio", [
    {"contrato": "cauce.conector/1"}, {"tipo": "enviar_formulario"}, {"solicitud_id": "9c41e7a0-uuid"},
    {"solicitud_id": "sol_corto"}, {"extra": 1}, {"opciones": {"espera_revision_max_s": 901, "pausa_tecla_ms": 35}},
    {"formulario": {**solicitud()["formulario"], "firma_estructura_esperada": "clave:tipo"}},      # firma v1 (H4)
    {"formulario": {**solicitud()["formulario"], "perfil_sha256": "abc"}},
    {"formulario": {**solicitud()["formulario"], "url": "http://127.0.0.1:8200/alta?token=x"}},    # sin query
])
def test_solicitud_rechaza_lo_que_no_es_del_contrato(cambio):
    assert C.validar_solicitud(solicitud(**cambio), T0)


def test_solicitud_exige_autorizacion_y_consentimiento():
    s = solicitud()
    s["autorizacion"]["consentimiento_vigente"] = False
    assert C.validar_solicitud(s, T0)
    s = solicitud()
    del s["autorizacion"]
    assert C.validar_solicitud(s, T0)
    s = solicitud()
    s["autorizacion"]["formulario_id"] = "frm_otro"
    assert any("FORMULARIO_NO_AUTORIZADO" in e for e in C.validar_solicitud(s, T0))


def test_politica_de_laboratorio_solo_127_0_0_1_pero_el_contrato_admite_otros():
    s = solicitud()
    s["formulario"]["url"] = "https://formularios.example.org/alta"
    assert any("PORTAL_NO_LOCAL" in e for e in C.validar_solicitud(s, T0))
    assert C.validar_solicitud(s, T0, solo_local=False) == []


def test_marcas_de_tiempo_con_zona_y_caducidad():
    s = solicitud(emitida_en="2027-01-10T06:00:00-06:00", caduca_en="2027-01-10T06:10:00-06:00")   # = 12:00Z
    assert C.validar_solicitud(s, T0) == []
    assert C.ts(s["emitida_en"]) == T0
    with pytest.raises(ValueError):
        C.ts("2027-01-10T12:00:00")
    assert any("SOLICITUD_CADUCADA" in e for e in C.validar_solicitud(solicitud(), T0 + timedelta(seconds=600)))
    assert C.validar_solicitud(solicitud(caduca_en=_f(T0 + timedelta(seconds=901))), T0)
    assert C.validar_solicitud(solicitud(caduca_en=_f(T0 - timedelta(seconds=1))), T0)
    assert C.validar_solicitud(solicitud(caduca_en=_f(T0 + timedelta(seconds=900))), T0) == []


def test_autorizacion_vencida():
    s = solicitud()
    s["autorizacion"]["vigente_hasta"] = "2027-01-09"
    assert any("AUTORIZACION_CADUCADA" in e for e in C.validar_solicitud(s, T0))


def test_campos_con_nombre_o_valor_invalido():
    for campos in ({"Nombre": "x"}, {"n": "x" * 201}, {"n": 5}, {}):
        assert C.validar_solicitud(solicitud(campos=campos), T0), campos


# ------------------------------------------------------------------ huella de preparación
def test_huella_la_recalcula_monitor_y_depende_de_id_firma_y_valores():
    s = solicitud()
    h = C.huella_preparacion(s["solicitud_id"], s["formulario"]["firma_estructura_esperada"], s["campos"])
    assert h == C.huella_preparacion(s["solicitud_id"], s["formulario"]["firma_estructura_esperada"], dict(reversed(list(s["campos"].items()))))
    assert h != C.huella_preparacion("sol_" + "0" * 16, s["formulario"]["firma_estructura_esperada"], s["campos"])
    assert h != C.huella_preparacion(s["solicitud_id"], "v2:" + "1" * 16, s["campos"])
    assert h != C.huella_preparacion(s["solicitud_id"], s["formulario"]["firma_estructura_esperada"], {**s["campos"], "tramite": "baja"})


# ------------------------------------------------------------------ simulador por stdin/stdout
def test_simulador_intercambia_una_solicitud_ficticia_sin_enviar():
    s = solicitud()
    p = correr(s)
    assert p.returncode == 0 and p.stderr == ""
    r = json.loads(p.stdout)
    assert C.validar_respuesta(r, s, p.returncode) == []
    assert r["estado"] == "preparado_revisado_sin_envio" and r["envio"]["realizado_por_conector"] is False
    assert sorted(c["campo"] for c in r["campos"]) == sorted(s["campos"])


@pytest.mark.parametrize("modo,estado", [
    ("envio_humano", "enviado_por_persona_observado"), ("incidencias", "preparado_con_incidencias"),
    ("estructura", "detenido_estructura_cambiada"), ("no_autorizado", "rechazado_no_autorizado"),
    ("intervencion", "detenido_intervencion_humana"), ("invalida", "rechazado_solicitud_invalida"),
    ("revision_caducada", "revision_caducada"), ("error", "error_interno")])
def test_cada_estado_del_contrato_es_valido_con_su_codigo_de_salida(modo, estado):
    s = solicitud()
    p = correr(s, modo)
    r = json.loads(p.stdout)
    assert r["estado"] == estado and p.returncode == C.SALIDA[estado]
    assert C.validar_respuesta(r, s, p.returncode) == []
    assert r["envio"]["realizado_por_conector"] is False
    assert C.MAPA_ESTADO[estado] in C.ESTADOS_SOLICITUD


def test_simulador_no_responde_a_entrada_invalida():
    p = correr(solicitud(tipo="enviar_formulario"))
    assert p.returncode == 6 and p.stdout == "" and p.stderr == ""
    q = subprocess.run([sys.executable, "-m", "monitoreo.conector.simulador"], input="{roto", text=True, capture_output=True,
                       cwd=RAIZ, env={**os.environ, "PYTHONPATH": str(RAIZ)})
    assert q.returncode == 6 and q.stdout == ""


def test_tiempo_maximo_mata_al_simulador_lento():
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run([sys.executable, "-m", "monitoreo.conector.simulador"], input=json.dumps(solicitud()), text=True,
                       capture_output=True, timeout=1, cwd=RAIZ,
                       env={**os.environ, "CONECTOR_SIM_MODO": "tardio", "CONECTOR_SIM_DEMORA_S": "10", "PYTHONPATH": str(RAIZ)})


# ------------------------------------------------------------------ datos personales
def test_respuesta_con_dato_personal_se_rechaza_sin_reproducirlo():
    s = solicitud()
    r = json.loads(correr(s, "filtra_pii").stdout)
    errores = C.validar_respuesta(r, s, 0)
    assert errores and not any("Persona Ficticia" in e for e in errores)


def test_el_detalle_de_un_error_no_puede_repetir_valores_ni_parecer_datos_personales():
    s = solicitud()
    r, cod = responder(s, "error")
    assert C.validar_respuesta(r, s, cod) == []                   # un error normal, sin texto: válido
    for texto in ("fallo con Persona Ficticia Uno", "contacto persona@ejemplo.com", "tel 5500000001"):
        r2 = copy.deepcopy(r)
        r2["errores"][0]["detalle"] = texto
        errores = C.validar_respuesta(r2, s, cod)
        assert errores and not any(v in e for e in errores for v in ("Persona", "persona@", "5500000001")), texto


@pytest.mark.parametrize("valor", ["persona@ejemplo.com", "5512345678", "1990-05-17", "17/05/1990", "PEPJ900517HDFRRN09"])
def test_patrones_de_datos_personales_se_detectan(valor):
    assert C.hallazgos_pii({"x": valor})
    assert C.hallazgos_pii({valor: "ok"}), "también en las claves"


def test_huellas_identificadores_y_origen_no_dan_falsos_positivos():
    s = solicitud()
    r, _ = responder(s, "ok")
    assert C.hallazgos_pii(r, s["campos"].values()) == []
    assert C.hallazgos_pii({"h": "1234567890" * 6 + "1234", "s": "sol_" + "1234567890123456"}) == []


def test_un_valor_corto_solo_cuenta_si_es_igual_y_uno_largo_si_esta_contenido():
    assert C.hallazgos_pii({"o": "http://127.0.0.1:8200/alta"}, ["alta"]) == []            # «alta» en la ruta: no es fuga
    assert C.hallazgos_pii({"d": "alta"}, ["alta"])
    assert C.hallazgos_pii({"d": "fallo con Persona Ficticia Uno"}, ["Persona Ficticia Uno"])


def test_campos_no_declarados_o_texto_libre_se_rechazan():
    s = solicitud()
    r, _ = responder(s, "ok")
    r["detalle"] = "texto libre"
    assert C.validar_respuesta(r, s, 0)
    r2, _ = responder(s, "ok")
    r2["campos"][0]["campo"] = "documento"                 # campo que nadie pidió (campo tardío, H8)
    assert C.validar_respuesta(r2, s, 0)
    r3, _ = responder(s, "ok")
    r3["huella_preparacion"] = "Persona Ficticia Uno"
    assert C.validar_respuesta(r3, s, 0)


# ------------------------------------------------------------------ coherencia: los hallazgos de seguridad del conector
def test_el_conector_nunca_envia_ni_informa_envio_sin_observarlo_H1():
    s = solicitud()
    r, _ = responder(s, "ok")
    r["envio"]["realizado_por_conector"] = True
    assert C.validar_respuesta(r, s, 0)
    r2, _ = responder(s, "ok")
    r2["estado"] = "enviado_por_persona_observado"          # «enviado» sin envío humano observado
    assert C.validar_respuesta(r2, s, 0)


def test_estructura_distinta_nunca_acompana_a_un_estado_exitoso_H3_H4():
    s = solicitud()
    r, _ = responder(s, "ok")
    r["formulario"]["firma_estructura"] = "v2:" + "f" * 16
    assert C.validar_respuesta(r, s, 0)
    r2, _ = responder(s, "ok")
    r2["formulario"]["estructura_coincide"] = False
    assert C.validar_respuesta(r2, s, 0)


def test_estados_que_se_detienen_antes_de_teclear_no_pueden_traer_campos_ni_huella():
    s = solicitud()
    for modo in ("estructura", "no_autorizado", "intervencion", "invalida"):
        r, cod = responder(s, modo)
        assert C.validar_respuesta(r, s, cod) == []
        r["campos"] = [{"campo": "tramite", "estado": "ok", "metodo": "teclado_secuencial", "caracteres": 4}]
        assert C.validar_respuesta(r, s, cod), modo


def test_codigo_de_salida_incoherente_o_huella_mala_se_rechazan():
    s = solicitud()
    p = correr(s, "codigo_mal")
    assert C.validar_respuesta(json.loads(p.stdout), s, p.returncode)
    q = correr(s, "huella_mala")
    assert any("HUELLA_NO_COINCIDE" in e for e in C.validar_respuesta(json.loads(q.stdout), s, q.returncode))
    r, _ = responder(s, "ok")
    r["solicitud_id"] = "sol_" + "9" * 16
    assert C.validar_respuesta(r, s, 0)


# ------------------------------------------------------------------ registro: idempotencia, estados, incertidumbre
def _lanzada(reg, s, ahora=T0, clave=CLAVE):
    reg.crear(s, clave, ahora)
    reg.mover(s["solicitud_id"], "AUTORIZADA", ahora)
    reg.mover(s["solicitud_id"], "LANZADA", ahora)


def test_idempotencia_misma_clave_y_contenido_no_duplica_y_distinto_es_conflicto():
    reg, s = Registro(), solicitud()
    a, nuevo = reg.crear(s, CLAVE, T0)
    b, nuevo2 = reg.crear(solicitud(campos=s["campos"]), CLAVE, T0)       # otro solicitud_id, mismo contenido
    assert nuevo and not nuevo2 and a is b and len(reg._por_id) == 1
    with pytest.raises(ErrorRegistro) as e:
        reg.crear(solicitud(campos={"tramite": "baja"}), CLAVE, T0)
    assert e.value.codigo == "IDEMPOTENCIA_CONFLICTO"


def test_solicitud_id_de_un_solo_uso():
    reg, s = Registro(), solicitud()
    reg.crear(s, CLAVE, T0)
    with pytest.raises(ErrorRegistro) as e:
        reg.crear(s, "OTRA-CLAVE-0002", T0)
    assert e.value.codigo == "SOLICITUD_REPETIDA"


def test_no_se_lanza_una_solicitud_caducada():
    reg, s = Registro(), solicitud()
    reg.crear(s, CLAVE, T0)
    reg.mover(s["solicitud_id"], "AUTORIZADA", T0)
    with pytest.raises(ErrorRegistro) as e:
        reg.mover(s["solicitud_id"], "LANZADA", T0 + timedelta(seconds=601))
    assert e.value.codigo == "SOLICITUD_CADUCADA"
    with pytest.raises(ErrorRegistro):
        Registro().crear(s, CLAVE, T0 + timedelta(seconds=601))


def test_el_plazo_del_proceso_es_la_espera_de_revision_mas_el_margen():
    reg, s = Registro(), solicitud()          # espera 300 s + 180 s de margen
    _lanzada(reg, s)
    limite = reg._por_id[s["solicitud_id"]]["limite"]
    assert limite == T0 + timedelta(seconds=300 + C.MARGEN_PROCESO_S)
    r, cod = responder(s, "ok")
    assert reg.recibir(s, r, limite - timedelta(seconds=1), cod)["estado"] == "PREPARADO"
    reg2, s2 = Registro(), solicitud()
    _lanzada(reg2, s2)
    r2, cod2 = responder(s2, "ok")
    out = reg2.recibir(s2, r2, limite + timedelta(seconds=1), cod2)
    assert out["estado"] == "INCIERTA" and out["codigo"] == "RESPUESTA_FUERA_DE_PLAZO"


@pytest.mark.parametrize("modo,esperado", [
    ("ok", "PREPARADO"), ("envio_humano", "ENVIADA_POR_PERSONA"), ("incidencias", "CON_INCIDENCIAS"),
    ("estructura", "DETENIDA"), ("intervencion", "DETENIDA"), ("no_autorizado", "RECHAZADA"), ("invalida", "RECHAZADA"),
    ("revision_caducada", "CADUCADA"), ("error", "ERROR")])
def test_cada_estado_del_conector_se_traduce_al_de_monitor_y_es_terminal(modo, esperado):
    reg, s = Registro(), solicitud()
    _lanzada(reg, s)
    r, cod = responder(s, modo)
    assert reg.recibir(s, r, T0 + timedelta(seconds=3), cod)["estado"] == esperado
    assert esperado in C.TERMINALES
    with pytest.raises(ErrorRegistro):
        reg.recibir(s, r, T0, cod)
    with pytest.raises(ErrorRegistro):
        reg.mover(s["solicitud_id"], "LANZADA", T0)


def test_respuesta_invalida_o_pii_deja_incierta_y_solo_una_persona_la_resuelve():
    reg, s = Registro(), solicitud()
    _lanzada(reg, s)
    assert reg.recibir(s, json.loads(correr(s, "filtra_pii").stdout), T0, 0)["estado"] == "INCIERTA"
    with pytest.raises(ErrorRegistro) as e:
        reg.mover(s["solicitud_id"], "PREPARADO", T0)
    assert e.value.codigo == "CONCILIACION_REQUERIDA"
    assert reg.mover(s["solicitud_id"], "RECHAZADA", T0, "CONCILIADA", conciliado_por="usr-1")["estado"] == "RECHAZADA"
    assert all("Persona" not in json.dumps(ev) for ev in reg.diario), "el diario no lleva datos personales"


def test_proceso_muerto_o_sin_salida_es_incierta_nunca_exito():
    reg, s = Registro(), solicitud()
    _lanzada(reg, s)
    assert correr(s, "sin_salida").stdout == ""
    assert reg.proceso_terminado_sin_respuesta(s["solicitud_id"], T0)["estado"] == "INCIERTA"


def test_no_hay_atajos_de_estado():
    reg, s = Registro(), solicitud()
    reg.crear(s, CLAVE, T0)
    with pytest.raises(ErrorRegistro):
        reg.mover(s["solicitud_id"], "PREPARADO", T0)          # CREADA no salta a PREPARADO


# ------------------------------------------------------------------ seguridad
def test_lanzamiento_bloqueado_sin_aislamiento_demostrado():
    ok, motivo = aislamiento.puede_lanzar()
    assert ok is False and "BLOQUEADO" in motivo


def test_la_capa_no_importa_base_de_datos_ni_lanza_procesos():
    for f in (RAIZ / "monitoreo" / "conector").glob("*.py"):
        txt = f.read_text(encoding="utf-8")
        for prohibido in ("sqlalchemy", "monitoreo.db", "subprocess", "from ..models", "Popen", "import socket", "urllib.request", "httpx"):
            assert prohibido not in txt, (f.name, prohibido)
