import json
import os
import sys

import pytest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
import receptor_graph as rg  # noqa: E402
import prueba_buzon as pb  # noqa: E402

TOKEN = "eyJ-SECRETO-NO-DEBE-SALIR"


def msg(i, asunto="hola"):
    return {"id": f"id{i}", "internetMessageId": f"<{i}@x>", "receivedDateTime": f"2026-11-13T10:0{i}:00Z",
            "from": {"emailAddress": {"address": f"r{i}@ejemplo.com"}}, "subject": asunto, "hasAttachments": False}


class FalsoGraph:
    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = []

    def __call__(self, url, headers, timeout):
        self.llamadas.append((url, headers))
        r = self.respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def receptor(resp, token=lambda: TOKEN):
    g = FalsoGraph(resp)
    return rg.ReceptorGraph(token, g), g


def test_primera_sincronizacion_pagina_y_devuelve_delta_link():
    r, g = receptor([
        (200, {"value": [msg(1)], "@odata.nextLink": "https://graph/next"}, {}),
        (200, {"value": [msg(2), {"id": "id9", "@removed": {"reason": "deleted"}}],
               "@odata.deltaLink": "https://graph/delta?t=1"}, {}),
    ])
    res = r.sincronizar()
    assert res.estado == rg.OK
    assert [m.id for m in res.mensajes] == ["id1", "id2"]
    assert res.delta_link == "https://graph/delta?t=1"
    assert "/me/mailFolders/inbox/messages/delta" in g.llamadas[0][0]
    assert g.llamadas[1][0] == "https://graph/next"
    assert g.llamadas[0][1]["Authorization"] == f"Bearer {TOKEN}"


def test_incremental_usa_el_delta_link_guardado():
    r, g = receptor([(200, {"value": [], "@odata.deltaLink": "https://graph/delta?t=2"}, {})])
    res = r.sincronizar("https://graph/delta?t=1")
    assert g.llamadas[0][0] == "https://graph/delta?t=1"
    assert res.estado == rg.OK and res.delta_link.endswith("t=2")


@pytest.mark.parametrize("status,estado", [
    (401, rg.NECESITA_CONSENTIMIENTO), (403, rg.PERMISO_DENEGADO),
    (429, rg.LIMITADO), (503, rg.LIMITADO), (500, rg.ERROR_DESCONOCIDO)])
def test_errores_http_se_traducen_a_estado_del_buzon(status, estado):
    r, _ = receptor([(status, {"error": {"code": "X"}}, {"Retry-After": "120"})])
    res = r.sincronizar()
    assert res.estado == estado
    if estado == rg.LIMITADO:
        assert res.reintentar_en == 120
    assert res.delta_link is None


def test_error_de_red():
    r, _ = receptor([ConnectionError("caído")])
    assert r.sincronizar().estado == rg.ERROR_RED


def test_token_revocado_pide_consentimiento_sin_llamar_a_graph():
    def sin_token():
        raise rg.clasificar_error_msal({"error": "invalid_grant", "error_description": "AADSTS70000 revocado"})
    r, g = receptor([], token=sin_token)
    res = r.sincronizar()
    assert res.estado == rg.NECESITA_CONSENTIMIENTO and g.llamadas == []


def test_cuenta_distinta_se_rechaza():
    with pytest.raises(rg.ErrorRecepcion) as e:
        rg.comprobar_cuenta("a@outlook.com", {"preferred_username": "otra@outlook.com"})
    assert e.value.estado == rg.CUENTA_DISTINTA
    rg.comprobar_cuenta(" A@Outlook.com ", {"preferred_username": "a@outlook.com"})  # no lanza


def test_permiso_minimo_solo_lectura():
    assert rg.PERMISOS == ["Mail.Read"]
    assert "consumers" in rg.AUTORIDAD_CONSUMIDOR


def test_esperar_correo_encuentra_marca_y_respeta_limitacion():
    r, _ = receptor([
        (200, {"value": [msg(1, "otro")], "@odata.deltaLink": "d2"}, {}),
        (429, {}, {"Retry-After": "90"}),
        (200, {"value": [msg(2, "Re: CAUCE-PRUEBA-ABCD")], "@odata.deltaLink": "d3"}, {}),
    ])
    esperas = []
    t = [0.0]
    def dormir(s):
        esperas.append(s); t[0] += s
    m, estado, delta, pasadas = pb.esperar_correo(r, "d1", "CAUCE-PRUEBA-ABCD", minutos=10,
                                                  intervalo=30, dormir=dormir, reloj=lambda: t[0])
    assert m and m.id == "id2" and delta == "d3" and pasadas == 3
    assert esperas == [30, 90]  # tras el 429 espera lo que Microsoft indica


def test_esperar_correo_no_insiste_si_falta_consentimiento():
    r, g = receptor([(401, {}, {})])
    m, estado, _, pasadas = pb.esperar_correo(r, "d1", "X", minutos=10, dormir=lambda s: None, reloj=lambda: 0)
    assert m is None and estado == rg.NECESITA_CONSENTIMIENTO and pasadas == 1


def test_cache_se_escribe_con_permisos_restringidos(tmp_path):
    class Cache:
        has_state_changed = True
        def serialize(self):
            return "{}"
    ruta = tmp_path / "c.json"
    rg.guardar_cache(Cache(), str(ruta))
    assert ruta.read_text() == "{}"
    if os.name == "posix":
        assert (ruta.stat().st_mode & 0o777) == 0o600


def test_evidencia_no_contiene_tokens(tmp_path, monkeypatch):
    class AuthFalso:
        def __init__(self, *a, **k): pass
        def obtener_token(self): return TOKEN
    respuestas = [(200, {"value": [msg(1)], "@odata.deltaLink": "d1"}, {})]
    monkeypatch.setattr(rg, "AutenticadorMSAL", AuthFalso)
    monkeypatch.setattr(rg, "http_get_requests", FalsoGraph(respuestas))
    codigo = pb.main(["--direccion", "a@outlook.com", "--client-id", "x", "--sin-espera",
                      "--carpeta", str(tmp_path)])
    assert codigo == 0
    texto = (tmp_path / "a_at_outlook.com" / "evidencia.json").read_text()
    assert TOKEN not in texto
    assert json.loads(texto)["pasos"][0]["ok"] is True


def test_registro_de_azure_incorrecto_no_se_confunde_con_buzon():
    e = rg.clasificar_error_msal({"error": "unauthorized_client",
                                  "error_description": "AADSTS700038: no es un identificador válido"})
    assert e.estado == rg.APP_MAL_CONFIGURADA
