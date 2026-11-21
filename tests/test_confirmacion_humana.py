"""Confirmación humana explícita e independiente (laboratorio). Datos ficticios; sin conector real ni BD."""
import hashlib
from datetime import timedelta

import pytest

from monitoreo.conector import contrato as C
from monitoreo.conector.confirmacion import (FRASE_REVISION, MIN_REVISION_S, VIGENCIA_APROBACION_S, VIGENCIA_PREPARACION_S,
                                              Confirmaciones, ErrorConfirmacion)
from monitoreo.conector.registro import Registro
from monitoreo.conector.simulador import responder
from tests.test_conector_contrato import T0, solicitud

PERSONA = "rev_0042"
FOLIO = hashlib.sha256(b"FOLIO-FICTICIO-0001").hexdigest()


def preparar(modo="ok"):
    """Solicitud -> Registro (como Monitor) con la respuesta del simulador; devuelve (conf, sol, resp, registro)."""
    sol = solicitud()
    reg, conf = Registro(), None
    reg.crear(sol, "clave-" + sol["solicitud_id"], T0)
    reg.mover(sol["solicitud_id"], "AUTORIZADA", T0)
    reg.mover(sol["solicitud_id"], "LANZADA", T0)
    resp, salida = responder(sol, modo)
    final = reg.recibir(sol, resp, T0 + timedelta(seconds=30), salida)
    return Confirmaciones(reg), sol, resp, final


def revisado(conf, sol, resp, t=T0 + timedelta(seconds=40)):
    conf.registrar_preparacion(sol, resp, t)
    h = conf.abrir_revision(sol["solicitud_id"], PERSONA, t)
    return h, t + timedelta(seconds=MIN_REVISION_S + 1)


# ---------------------------------------------------------------- lo que NO es revisión ni envío
def test_ventana_cerrada_da_preparado_pero_ninguna_revision_humana():
    conf, sol, resp, final = preparar("ok")
    assert final["estado"] == "PREPARADO" and resp["revision_humana"]["resultado"] == "cerrada_sin_envio"
    conf.registrar_preparacion(sol, resp, T0)
    e = conf.estado(sol["solicitud_id"], T0)
    assert e == {"estado_monitor": "PREPARADO", "revision_humana": "SIN_CONFIRMAR", "envio": "NO_CONFIRMADO"}
    assert conf.puede_continuar(sol["solicitud_id"], T0) == (False, "REVISION_SIN_CONFIRMAR")


def test_un_envio_observado_por_el_conector_es_solo_un_indicio():
    conf, sol, resp, final = preparar("envio_humano")
    assert final["estado"] == "ENVIADA_POR_PERSONA"
    assert conf.estado(sol["solicitud_id"], T0)["envio"] == "INDICIO_DEL_CONECTOR"
    with pytest.raises(ErrorConfirmacion):                       # ni siquiera se puede registrar como preparación
        conf.registrar_preparacion(sol, resp, T0)
    with pytest.raises(ErrorConfirmacion):
        conf.confirmar_envio(sol["solicitud_id"], PERSONA, FOLIO, T0)


@pytest.mark.parametrize("modo", ["error", "incidencias", "revision_caducada", "estructura", "intervencion", "no_autorizado"])
def test_otros_estados_no_se_pueden_preparar_ni_confirmar(modo):
    conf, sol, resp, final = preparar(modo)
    assert final["estado"] != "PREPARADO"
    with pytest.raises(ErrorConfirmacion):
        conf.registrar_preparacion(sol, resp, T0)
    assert conf.puede_continuar(sol["solicitud_id"], T0)[0] is False


def test_nada_de_lo_que_dice_el_conector_puede_aprobar():
    conf, sol, resp, _ = preparar("ok")
    resp = {**resp, "revision_humana": {"requerida": False, "resultado": "cerrada_sin_envio", "duracion_s": 600}}
    conf.registrar_preparacion(sol, resp, T0)
    assert conf.puede_continuar(sol["solicitud_id"], T0)[0] is False        # duración larga, «revisado»: no cuenta


def test_respuesta_invalida_no_se_registra():
    conf, sol, resp, _ = preparar("ok")
    with pytest.raises(ErrorConfirmacion):
        conf.registrar_preparacion(sol, {**resp, "huella_preparacion": "0" * 16}, T0)


# ---------------------------------------------------------------- confirmación correcta
def test_confirmacion_completa_revision_y_luego_envio_con_comprobante():
    conf, sol, resp, _ = preparar("ok")
    sid = sol["solicitud_id"]
    h, t = revisado(conf, sol, resp)
    assert conf.confirmar_revision(sid, PERSONA, FRASE_REVISION, h, t)["revision"] == "CONFIRMADA_POR_PERSONA"
    assert conf.puede_continuar(sid, t) == (True, "OK")
    assert conf.estado(sid, t)["envio"] == "NO_CONFIRMADO"                 # revisar NO es enviar
    assert conf.confirmar_envio(sid, PERSONA, FOLIO, t)["envio"] == "CONFIRMADO_CON_COMPROBANTE"
    assert conf.estado(sid, t)["envio"] == "CONFIRMADO_CON_COMPROBANTE"
    assert all("zz" not in str(e).lower() for e in conf.diario)             # diario sin valores


# ---------------------------------------------------------------- negativas
@pytest.mark.parametrize("persona", ["conector", "monitor", "simulador", "sistema", "rev_", "REV_1", "", "alguien", "rev_" + "x" * 41])
def test_solo_una_persona_con_referencia_valida(persona):
    conf, sol, resp, _ = preparar("ok")
    conf.registrar_preparacion(sol, resp, T0)
    with pytest.raises(ErrorConfirmacion) as ex:
        conf.abrir_revision(sol["solicitud_id"], persona, T0)
    assert ex.value.codigo == "PERSONA_INVALIDA"


@pytest.mark.parametrize("frase", ["revisé y apruebo", "REVISE Y APRUEBO", " REVISÉ Y APRUEBO", "REVISÉ Y APRUEBO ", "", "ok", "ENVIAR"])
def test_la_frase_debe_ser_exacta(frase):
    conf, sol, resp, _ = preparar("ok")
    h, t = revisado(conf, sol, resp)
    with pytest.raises(ErrorConfirmacion) as ex:
        conf.confirmar_revision(sol["solicitud_id"], PERSONA, frase, h, t)
    assert ex.value.codigo == "FRASE_INCORRECTA"


def test_huella_distinta_no_confirma():
    conf, sol, resp, _ = preparar("ok")
    h, t = revisado(conf, sol, resp)
    with pytest.raises(ErrorConfirmacion) as ex:
        conf.confirmar_revision(sol["solicitud_id"], PERSONA, FRASE_REVISION, "0" * len(h), t)
    assert ex.value.codigo == "HUELLA_NO_COINCIDE"


def test_sin_abrir_la_revision_o_con_otra_persona_no_confirma():
    conf, sol, resp, _ = preparar("ok")
    sid = sol["solicitud_id"]
    conf.registrar_preparacion(sol, resp, T0)
    with pytest.raises(ErrorConfirmacion) as ex:
        conf.confirmar_revision(sid, PERSONA, FRASE_REVISION, resp["huella_preparacion"], T0 + timedelta(minutes=1))
    assert ex.value.codigo == "REVISION_NO_ABIERTA_POR_ESTA_PERSONA"
    conf.abrir_revision(sid, "rev_0001", T0)
    with pytest.raises(ErrorConfirmacion) as ex:
        conf.confirmar_revision(sid, PERSONA, FRASE_REVISION, resp["huella_preparacion"], T0 + timedelta(minutes=1))
    assert ex.value.codigo == "REVISION_NO_ABIERTA_POR_ESTA_PERSONA"


def test_un_clic_inmediato_no_es_revision():
    conf, sol, resp, _ = preparar("ok")
    sid = sol["solicitud_id"]
    h, _ = revisado(conf, sol, resp)
    with pytest.raises(ErrorConfirmacion) as ex:
        conf.confirmar_revision(sid, PERSONA, FRASE_REVISION, h, T0 + timedelta(seconds=40 + MIN_REVISION_S - 1))
    assert ex.value.codigo == "REVISION_DEMASIADO_RAPIDA"


def test_preparacion_vencida_no_se_aprueba():
    conf, sol, resp, _ = preparar("ok")
    h, _ = revisado(conf, sol, resp)
    with pytest.raises(ErrorConfirmacion) as ex:
        conf.confirmar_revision(sol["solicitud_id"], PERSONA, FRASE_REVISION, h, T0 + timedelta(seconds=VIGENCIA_PREPARACION_S + 60))
    assert ex.value.codigo == "PREPARACION_VENCIDA"


def test_confirmacion_de_un_solo_uso_y_caduca():
    conf, sol, resp, _ = preparar("ok")
    sid = sol["solicitud_id"]
    h, t = revisado(conf, sol, resp)
    conf.confirmar_revision(sid, PERSONA, FRASE_REVISION, h, t)
    with pytest.raises(ErrorConfirmacion) as ex:
        conf.confirmar_revision(sid, PERSONA, FRASE_REVISION, h, t)
    assert ex.value.codigo == "YA_CONFIRMADA"
    tarde = t + timedelta(seconds=VIGENCIA_APROBACION_S + 1)
    assert conf.estado(sid, tarde)["revision_humana"] == "CADUCADA" and conf.puede_continuar(sid, tarde)[0] is False


def test_revocar_anula_la_revision_y_bloquea_el_envio():
    conf, sol, resp, _ = preparar("ok")
    sid = sol["solicitud_id"]
    h, t = revisado(conf, sol, resp)
    conf.confirmar_revision(sid, PERSONA, FRASE_REVISION, h, t)
    conf.revocar(sid, PERSONA, t)
    assert conf.puede_continuar(sid, t) == (False, "REVISION_REVOCADA")
    with pytest.raises(ErrorConfirmacion) as ex:
        conf.confirmar_envio(sid, PERSONA, FOLIO, t)
    assert ex.value.codigo == "SIN_REVISION_CONFIRMADA"


def test_el_envio_exige_revision_y_comprobante_valido_y_es_unico():
    conf, sol, resp, _ = preparar("ok")
    sid = sol["solicitud_id"]
    h, t = revisado(conf, sol, resp)
    with pytest.raises(ErrorConfirmacion) as ex:
        conf.confirmar_envio(sid, PERSONA, FOLIO, t)                         # sin revisión confirmada
    assert ex.value.codigo == "SIN_REVISION_CONFIRMADA"
    conf.confirmar_revision(sid, PERSONA, FRASE_REVISION, h, t)
    for malo in ("", "FOLIO-0001", "A" * 64, "0" * 63):
        with pytest.raises(ErrorConfirmacion) as ex:
            conf.confirmar_envio(sid, PERSONA, malo, t)
        assert ex.value.codigo == "COMPROBANTE_INVALIDO"
    conf.confirmar_envio(sid, PERSONA, FOLIO, t)
    with pytest.raises(ErrorConfirmacion) as ex:
        conf.confirmar_envio(sid, PERSONA, FOLIO, t)
    assert ex.value.codigo == "ENVIO_YA_CONFIRMADO"


def test_si_monitor_pasa_a_incierta_ya_no_se_puede_continuar():
    conf, sol, resp, _ = preparar("ok")
    sid = sol["solicitud_id"]
    h, t = revisado(conf, sol, resp)
    conf.confirmar_revision(sid, PERSONA, FRASE_REVISION, h, t)
    conf.registro._por_id[sid]["estado"] = "INCIERTA"                       # p. ej. conciliación pendiente
    assert conf.puede_continuar(sid, t) == (False, "ESTADO_NO_ES_PREPARADO")


def test_el_contrato_y_el_registro_no_cambiaron():
    assert C.VERSION == "cauce.conector.v1" and "APROBADA" not in " ".join(C.ESTADOS_SOLICITUD)
