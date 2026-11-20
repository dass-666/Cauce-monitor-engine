"""Primera capa del contrato cauce.conector/1: esquemas, validadores, simulador, idempotencia, caducidad, PII.

Datos ficticios. No usa la base de datos ni expedientes. El subproceso que se lanza aquí es SOLO el simulador
con una solicitud sintética (la capacidad de lanzar con datos de expedientes sigue bloqueada).
"""
import copy
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from monitoreo.conector import aislamiento, contrato as C
from monitoreo.conector.registro import ErrorRegistro, Registro
from monitoreo.conector.simulador import responder

RAIZ = Path(__file__).resolve().parents[1]
T0 = datetime(2027, 1, 10, 12, 0, 0, tzinfo=timezone.utc)


def _f(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def solicitud(**cambios):
    campos = {"nombre": "Persona Ficticia Uno", "tramite": "alta", "telefono": "5500000001"}
    s = {"contrato": C.VERSION, "id_solicitud": str(uuid.uuid4()), "clave_idempotencia": "EXP-0001:RES-0001:PREPARAR",
         "accion": "PREPARAR", "envio_permitido": False,
         "portal": {"base": "http://127.0.0.1:8200", "proveedor_id": "lab"},
         "emitida": _f(T0), "caduca": _f(T0 + timedelta(seconds=90)), "tiempo_max_s": 60,
         "autorizacion": {"revision_humana": True, "aprobada_por": "usr-1", "aprobada_en": _f(T0 - timedelta(seconds=5)),
                          "consentimiento_vigente": True},
         "campos": campos, "huella_campos": C.huella_campos(campos)}
    s.update(cambios)
    return s


def correr(sol, modo="ok", timeout=20):
    env = {**os.environ, "CONECTOR_SIM_MODO": modo}
    return subprocess.run([sys.executable, "-m", "monitoreo.conector.simulador"], input=json.dumps(sol),
                          capture_output=True, text=True, timeout=timeout, cwd=RAIZ, env={**env, "PYTHONPATH": str(RAIZ)})


# ------------------------------------------------------------------ esquemas y solicitud
def test_solicitud_valida_y_los_esquemas_cargan():
    assert C.validar_solicitud(solicitud(), T0) == []
    for n in ("solicitud", "respuesta"):
        assert C.cargar_esquema(n)["$id"].startswith("cauce.conector/1/")


@pytest.mark.parametrize("cambio", [
    {"contrato": "cauce.conector/2"}, {"accion": "ENVIAR"}, {"envio_permitido": True},
    {"portal": {"base": "https://www.ejemplo.mx", "proveedor_id": "lab"}},
    {"portal": {"base": "http://192.168.1.5:8200", "proveedor_id": "lab"}},
    {"tiempo_max_s": 999}, {"id_solicitud": "no-es-uuid"}, {"clave_idempotencia": "corta"},
    {"extra": 1},
])
def test_solicitud_rechaza_lo_que_no_es_del_contrato(cambio):
    assert C.validar_solicitud(solicitud(**cambio), T0)


def test_solicitud_exige_revision_humana_y_consentimiento():
    for k in ("revision_humana", "consentimiento_vigente"):
        s = solicitud()
        s["autorizacion"][k] = False
        assert C.validar_solicitud(s, T0), k
    s = solicitud()
    del s["autorizacion"]
    assert C.validar_solicitud(s, T0)


def test_huella_detecta_campos_alterados_y_es_estable():
    s = solicitud()
    s["campos"]["tramite"] = "baja"
    assert any("HUELLA_NO_COINCIDE" in e for e in C.validar_solicitud(s, T0))
    assert C.huella_campos({"a": "1", "b": "2"}) == C.huella_campos({"b": "2", "a": "1"})


def test_campos_con_nombre_o_valor_invalido():
    for campos in ({"Nombre": "x"}, {"n": "x" * 201}, {"n": 5}):
        s = solicitud(campos=campos, huella_campos=C.huella_campos(campos))
        assert C.validar_solicitud(s, T0), campos


def test_caducidad():
    assert any("SOLICITUD_CADUCADA" in e for e in C.validar_solicitud(solicitud(), T0 + timedelta(seconds=90)))
    largo = solicitud(caduca=_f(T0 + timedelta(seconds=500)))
    assert C.validar_solicitud(largo, T0)
    posterior = solicitud()
    posterior["autorizacion"]["aprobada_en"] = _f(T0 + timedelta(seconds=1))
    assert C.validar_solicitud(posterior, T0)


# ------------------------------------------------------------------ simulador por stdin/stdout
def test_simulador_intercambia_una_solicitud_ficticia_sin_enviar():
    s = solicitud()
    p = correr(s)
    assert p.returncode == 0 and p.stderr == ""
    r = json.loads(p.stdout)
    assert C.validar_respuesta(r, s) == []
    assert r["estado"] == "PREPARADO" and r["enviado"] is False and r["campos_llenados"] == sorted(s["campos"])


@pytest.mark.parametrize("modo,estado,codigo", [("rechazar", "RECHAZADO", "CAMPO_NO_RECONOCIDO"),
                                                ("error", "ERROR", "ERROR_INTERNO"), ("incierto", "INCIERTO", "TIEMPO_AGOTADO")])
def test_simulador_estados_no_exitosos_son_validos(modo, estado, codigo):
    s = solicitud()
    r = json.loads(correr(s, modo).stdout)
    assert (r["estado"], r["codigo"]) == (estado, codigo) and C.validar_respuesta(r, s) == []


def test_simulador_no_responde_a_entrada_invalida():
    p = correr(solicitud(accion="ENVIAR"))
    assert p.returncode == 2 and p.stdout == ""
    assert subprocess.run([sys.executable, "-m", "monitoreo.conector.simulador"], input="{roto", text=True,
                          capture_output=True, cwd=RAIZ, env={**os.environ, "PYTHONPATH": str(RAIZ)}).returncode == 2


def test_tiempo_maximo_mata_al_simulador_lento():
    s = solicitud()
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run([sys.executable, "-m", "monitoreo.conector.simulador"], input=json.dumps(s), text=True,
                       capture_output=True, timeout=1, cwd=RAIZ,
                       env={**os.environ, "CONECTOR_SIM_MODO": "tardio", "CONECTOR_SIM_DEMORA_S": "10", "PYTHONPATH": str(RAIZ)})


# ------------------------------------------------------------------ datos personales
def test_respuesta_con_dato_personal_se_rechaza_sin_reproducirlo():
    s = solicitud()
    r = json.loads(correr(s, "filtra_pii").stdout)
    errores = C.validar_respuesta(r, s)
    assert errores
    assert not any("Persona Ficticia" in e for e in errores), "el error no debe contener el valor filtrado"


@pytest.mark.parametrize("valor", ["persona@ejemplo.com", "5512345678", "1990-05-17", "17/05/1990", "PEPJ900517HDFRRN09"])
def test_patrones_de_datos_personales_se_detectan(valor):
    assert C.hallazgos_pii({"x": valor})
    assert C.hallazgos_pii({valor: "ok"}), "también en las claves"


def test_huellas_e_identificadores_no_dan_falsos_positivos():
    s = solicitud()
    r = responder(s, "ok")
    assert C.hallazgos_pii(r) == []
    assert C.hallazgos_pii({"h": "1234567890" * 6 + "1234"}) == []   # 64 dígitos: huella válida de forma


def test_respuesta_sin_canal_de_texto_libre():
    s = solicitud()
    r = responder(s, "ok")
    r["detalle"] = "texto libre"
    assert C.validar_respuesta(r, s)
    r2 = responder(s, "ok")
    r2["evidencia"]["huella_pagina"] = "Persona Ficticia Uno"
    assert C.validar_respuesta(r2, s)


# ------------------------------------------------------------------ coherencia de la respuesta
def test_respuesta_incoherente_o_ajena_se_rechaza():
    s = solicitud()
    base = responder(s, "ok")
    casos = []
    for k, v in (("id_solicitud", str(uuid.uuid4())), ("enviado", True), ("estado", "ERROR"), ("codigo", "ERROR_INTERNO")):
        r = copy.deepcopy(base); r[k] = v; casos.append(r)
    r = copy.deepcopy(base); r["campos_llenados"] = ["nombre"]; casos.append(r)
    r = copy.deepcopy(base); r["evidencia"]["huella_pagina"] = ""; casos.append(r)
    casos.append(json.loads(correr(s, "huella_mala").stdout))
    for r in casos:
        assert C.validar_respuesta(r, s), r


# ------------------------------------------------------------------ registro: idempotencia, estados, incertidumbre
def _lanzada(reg, s, ahora=T0):
    reg.crear(s, ahora)
    reg.mover(s["id_solicitud"], "AUTORIZADA", ahora)
    reg.mover(s["id_solicitud"], "LANZADA", ahora)


def test_idempotencia_misma_clave_no_duplica():
    reg, s = Registro(), solicitud()
    a, nuevo = reg.crear(s, T0)
    b, nuevo2 = reg.crear(solicitud(id_solicitud=str(uuid.uuid4())), T0)
    assert nuevo and not nuevo2 and a is b and len(reg._por_id) == 1


def test_idempotencia_clave_con_contenido_distinto_es_conflicto():
    reg = Registro()
    reg.crear(solicitud(), T0)
    otros = {"nombre": "Otra Persona Ficticia"}
    with pytest.raises(ErrorRegistro) as e:
        reg.crear(solicitud(campos=otros, huella_campos=C.huella_campos(otros)), T0)
    assert e.value.codigo == "IDEMPOTENCIA_CONFLICTO"


def test_no_se_lanza_una_solicitud_caducada():
    reg, s = Registro(), solicitud()
    reg.crear(s, T0)
    reg.mover(s["id_solicitud"], "AUTORIZADA", T0)
    with pytest.raises(ErrorRegistro) as e:
        reg.mover(s["id_solicitud"], "LANZADA", T0 + timedelta(seconds=91))
    assert e.value.codigo == "SOLICITUD_CADUCADA"
    with pytest.raises(ErrorRegistro):
        Registro().crear(s, T0 + timedelta(seconds=91))


def test_respuesta_tardia_se_descarta_como_caducada():
    reg, s = Registro(), solicitud()
    _lanzada(reg, s)
    out = reg.recibir(s, responder(s, "ok"), T0 + timedelta(seconds=95))
    assert out["estado"] == "CADUCADA" and out["codigo"] == "RESPUESTA_TARDIA_DESCARTADA"


def test_flujo_normal_llega_a_preparado_y_es_terminal():
    reg, s = Registro(), solicitud()
    _lanzada(reg, s)
    assert reg.recibir(s, responder(s, "ok"), T0 + timedelta(seconds=3))["estado"] == "PREPARADO"
    with pytest.raises(ErrorRegistro):
        reg.mover(s["id_solicitud"], "LANZADA", T0)
    with pytest.raises(ErrorRegistro):
        reg.recibir(s, responder(s, "ok"), T0)


def test_respuesta_invalida_o_pii_deja_incierta_y_solo_una_persona_la_resuelve():
    reg, s = Registro(), solicitud()
    _lanzada(reg, s)
    assert reg.recibir(s, json.loads(correr(s, "filtra_pii").stdout), T0)["estado"] == "INCIERTA"
    with pytest.raises(ErrorRegistro) as e:
        reg.mover(s["id_solicitud"], "PREPARADO", T0)
    assert e.value.codigo == "CONCILIACION_REQUERIDA"
    assert reg.mover(s["id_solicitud"], "RECHAZADA", T0, "CONCILIADA", conciliado_por="usr-1")["estado"] == "RECHAZADA"
    assert all("Persona" not in json.dumps(ev) for ev in reg.diario), "el diario no lleva datos personales"


def test_no_hay_atajos_de_estado():
    reg, s = Registro(), solicitud()
    reg.crear(s, T0)
    with pytest.raises(ErrorRegistro):
        reg.mover(s["id_solicitud"], "PREPARADO", T0)     # CREADA no salta a PREPARADO


# ------------------------------------------------------------------ seguridad
def test_lanzamiento_bloqueado_sin_aislamiento_demostrado():
    ok, motivo = aislamiento.puede_lanzar()
    assert ok is False and "BLOQUEADO" in motivo


def test_la_capa_no_importa_base_de_datos_ni_lanza_procesos():
    for f in (RAIZ / "monitoreo" / "conector").glob("*.py"):
        txt = f.read_text(encoding="utf-8")
        for prohibido in ("sqlalchemy", "monitoreo.db", "subprocess", "from ..models", "Popen"):
            assert prohibido not in txt, (f.name, prohibido)
