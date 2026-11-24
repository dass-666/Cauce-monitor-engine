"""Laboratorio · perfil de formulario EXTERNO autorizado con el flujo de Ventanilla -> Forms.

Datos FICTICIOS. El «sitio externo» es un servidor de esta prueba en 127.0.0.2 (para Monitor y para Forms no es
127.0.0.1, así que recorre el camino externo completo sin salir del equipo).

1. Siempre (conector ESPÍA): las dos llaves (perfil externo con autorización vigente + host escrito por quien abre
   el laboratorio), la traducción con la tabla de equivalencias aprobada, que un código sin fila no viaja, la
   autorización caducada, producción, URLs no válidas y que el perfil del portal propio sigue igual.
2. Con `FORMS_REPO`: Forms REAL aprueba el perfil externo (con --authorized y un allowed_hosts.json propio) y
   rellena el formulario externo simulado; no envía.
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import sys
import threading
from datetime import date, timedelta
from pathlib import Path

import pytest

from monitoreo.autorrelleno import adaptador as ad

from .test_autorrelleno_forms import (FORMS_REPO, RAIZ, atencion, esperar, eventos, lab, portal,  # noqa: F401
                                      pulsar, textos_guardados)

HTML = RAIZ / "tests" / "herramientas" / "formulario_externo_simulado.html"
PLANTILLA = RAIZ / "monitoreo" / "autorrelleno" / "perfiles_externos" / "ejemplo_sitio_externo.json"
EQUIV = {"alta": "T-ALTA", "renovacion": "T-RENO", "constancia": "T-CONS"}
CAMPOS = ["correo", "correo_confirmacion", "fecha_nacimiento", "telefono", "tramite"]


def _autorizacion(dias=30):
    return {"ref": "autext_prueba", "otorgada_por": "Responsable ficticio del sitio",
            "documento_sha256": "c" * 64, "vigente_hasta": (date.today() + timedelta(days=dias)).isoformat()}


def _pin_externo(carpeta: Path, url="http://127.0.0.2:9999/solicitud", **cambios) -> Path:
    pin = {"version": 1, "externo": True, "formulario_id": "frm_prueba_externo", "perfil": "perfil_prueba_externo",
           "modo": "POR_DIA", "url": url, "perfil_sha256": "a" * 64, "firma_estructura_esperada": "v2:" + "b" * 16,
           "campos": CAMPOS, "equivalencias": {"tramite": dict(EQUIV)}, "autorizacion": _autorizacion(),
           "allowed_hosts_file": str(carpeta / "allowed_hosts.json"), "aprobado_por": "rev_prueba"}
    pin.update(cambios)
    ruta = carpeta / "perfil_externo_aprobado.json"
    ruta.write_text(json.dumps(pin), encoding="utf-8")
    return ruta


@pytest.fixture
def externo(lab, monkeypatch):
    monkeypatch.setenv("MONITOREO_AUTORRELLENO_PERFIL", str(_pin_externo(lab.tmp)))
    monkeypatch.setenv(ad.VARIABLE_HOSTS, "127.0.0.2")
    return lab


def _sol(lab):
    return json.loads((lab.espia / "solicitud.json").read_text(encoding="utf-8"))


# ================================================================ espía: qué sale de Monitor
def test_externo_traduce_con_la_tabla_y_solo_viajan_los_campos_del_perfil(externo):
    lab, h = externo, externo.ops["op1"]
    a, datos = atencion(lab, h)
    st = lab.c.get(f"/api/autorrelleno/atenciones/{a['id']}", headers=h).json()
    assert st["habilitado"] and st.get("externo") is True and st.get("sitio") == "127.0.0.2", st
    r = pulsar(lab, a["id"], h)
    assert r.status_code == 202, r.text
    esperar(lab, a["id"], h)
    sol = _sol(lab)
    assert sol["formulario"]["url"] == "http://127.0.0.2:9999/solicitud"
    assert sorted(sol["campos"]) == CAMPOS                       # ni identificador ni operacion: el perfil no los pide
    assert sol["campos"]["tramite"] == EQUIV[datos["tipo_tramite"]]   # código de Monitor traducido
    assert sol["campos"]["correo_confirmacion"] == sol["campos"]["correo"] == datos["correo"]
    assert sol["autorizacion"]["vigente_hasta"] <= _autorizacion()["vigente_hasta"]
    blob = json.dumps(sol, ensure_ascii=False)
    assert datos["nombre"] not in blob and datos["id_registro_previo"] not in blob
    # el proceso de Forms recibe el allowed_hosts.json propio y ninguna variable de Monitor
    nombres = json.loads((lab.espia / "entorno.json").read_text(encoding="utf-8"))
    assert "CAUCE_ALLOWED_HOSTS_FILE" in nombres and not [n for n in nombres if n.startswith("MONITOREO_")]
    texto = textos_guardados(lab)
    assert datos["correo"] not in texto and datos["telefono"] not in texto


def test_externo_codigo_sin_fila_no_viaja_lo_pone_la_persona(externo, monkeypatch):
    lab, h = externo, externo.ops["op1"]
    monkeypatch.setenv("MONITOREO_AUTORRELLENO_PERFIL",
                       str(_pin_externo(lab.tmp, equivalencias={"tramite": {"constancia_rara": "T-CONS"}})))
    a, _ = atencion(lab, h)
    r = pulsar(lab, a["id"], h)
    assert r.status_code == 202, r.text
    esperar(lab, a["id"], h)
    sol = _sol(lab)
    assert "tramite" not in sol["campos"] and sorted(sol["campos"]) == [c for c in CAMPOS if c != "tramite"]
    evs = [d for t, d in eventos(lab, a["expediente"]["id"]) if t == "AUTORRELLENO_SOLICITADO"]
    assert evs and evs[-1]["sin_equivalencia"] == ["tramite"] and evs[-1]["externo"] is True
    c = esperar(lab, a["id"], h)
    assert [x["campo"] for x in c["informe"]["sin_equivalencia"]] == ["tramite"]


def test_externo_sin_host_escrito_por_el_laboratorio_se_niega(externo, monkeypatch):
    lab, h = externo, externo.ops["op1"]
    monkeypatch.delenv(ad.VARIABLE_HOSTS)
    a, _ = atencion(lab, h)
    r = pulsar(lab, a["id"], h)
    assert r.status_code == 403 and "HOST_EXTERNO_NO_AUTORIZADO" in r.text
    monkeypatch.setenv(ad.VARIABLE_HOSTS, "otro.ejemplo.com")
    r = pulsar(lab, a["id"], h)
    assert r.status_code == 403 and "HOST_EXTERNO_NO_AUTORIZADO" in r.text
    assert not (lab.espia / "solicitud.json").exists()


def test_externo_autorizacion_caducada_se_niega(externo, monkeypatch):
    lab, h = externo, externo.ops["op1"]
    monkeypatch.setenv("MONITOREO_AUTORRELLENO_PERFIL", str(_pin_externo(lab.tmp, autorizacion=_autorizacion(-1))))
    a, _ = atencion(lab, h)
    r = pulsar(lab, a["id"], h)
    assert r.status_code == 403 and "AUTORIZACION_EXTERNA_CADUCADA" in r.text
    assert not (lab.espia / "solicitud.json").exists()


def test_externo_no_existe_en_produccion(externo, monkeypatch):
    lab, h = externo, externo.ops["op1"]
    a, _ = atencion(lab, h)
    monkeypatch.setenv("MONITOREO_ENTORNO", "produccion")
    assert ad.estado_configuracion()["codigo"] == "AUTORRELLENO_DESACTIVADO"


@pytest.mark.parametrize("cambios", [
    {"url": "http://formulario.ejemplo.com/x"},              # externo sin https
    {"url": "https://127.0.0.1:8201/cita"},                  # el laboratorio no es «externo»
    {"url": "https://usuario:clave@formulario.ejemplo.com/x"},
    {"url": "https://formulario.ejemplo.com/x?paso=1"},      # el contrato fijado no admite consulta
    {"autorizacion": {"ref": "aut_x", "otorgada_por": "x", "documento_sha256": "1", "vigente_hasta": "2030-01-01"}},
    {"autorizacion": None},
    {"equivalencias": {"no_declarado": {"a": "b"}}},
    {"allowed_hosts_file": ""},
])
def test_externo_perfil_invalido_no_se_usa(tmp_path, cambios):
    with pytest.raises(ad.Negado) as e:
        ad.leer_perfil(_pin_externo(tmp_path, **cambios))
    assert e.value.codigo == "PERFIL_NO_APROBADO"


def test_perfil_del_portal_propio_no_admite_equivalencias_ni_url_externa(tmp_path):
    base = json.loads(_pin_externo(tmp_path).read_text("utf-8"))
    base.pop("externo")
    for p in ({**base}, {**base, "url": "http://127.0.0.1:8201/cita"}):
        r = tmp_path / "p.json"
        r.write_text(json.dumps(p), "utf-8")
        with pytest.raises(ad.Negado):
            ad.leer_perfil(r)


def test_plantilla_de_perfil_externo_es_valida():
    sys.path.insert(0, str(RAIZ / "scripts"))
    from autorrelleno_forms import cargar_perfil_externo
    m = cargar_perfil_externo(PLANTILLA)
    assert m["externo"] is True and set(m["equivalencias"]) <= set(m["campos"])


def test_perfil_externo_con_campo_fuera_del_contrato_se_rechaza(tmp_path):
    sys.path.insert(0, str(RAIZ / "scripts"))
    from autorrelleno_forms import FormsNoListo, cargar_perfil_externo
    m = json.loads(PLANTILLA.read_text("utf-8"))
    m["campos"]["nombre"] = "solicitud::nombre"                # el nombre nunca es automático
    r = tmp_path / "p.json"
    r.write_text(json.dumps(m), "utf-8")
    with pytest.raises(FormsNoListo, match="nombre"):
        cargar_perfil_externo(r)


# ================================================================ Forms real contra el «sitio externo» simulado
@pytest.fixture
def sitio_externo():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(HTML.parent))
    handler.log_message = lambda *a: None
    try:
        srv = http.server.ThreadingHTTPServer(("127.0.0.2", 0), handler)
    except OSError:
        pytest.skip("127.0.0.2 no disponible en este equipo")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.2:{srv.server_address[1]}/{HTML.name}"
    srv.shutdown()


def test_forms_real_rellena_el_formulario_externo_autorizado(lab, sitio_externo, monkeypatch):
    if not FORMS_REPO:
        pytest.skip("FORMS_REPO no definido: prueba con Forms real omitida (no verificada en esta corrida)")
    sys.path.insert(0, str(RAIZ / "scripts"))
    from autorrelleno_forms import aprobar_externo, variables_motor
    m = json.loads(PLANTILLA.read_text("utf-8"))
    m.update(url=sitio_externo, formulario_id="frm_prueba_externo", perfil="perfil_prueba_externo",
             autorizacion=_autorizacion())
    ruta = lab.tmp / "perfil_externo.json"
    ruta.write_text(json.dumps(m), "utf-8")
    forms = Path(FORMS_REPO).resolve()
    py = Path(os.environ["FORMS_PYTHON"]) if os.environ.get("FORMS_PYTHON") else None
    pin = aprobar_externo(forms, ruta, lab.tmp, "rev_prueba", python=py)
    aprobado = json.loads(pin.read_text("utf-8"))
    assert aprobado["externo"] is True and aprobado["url"] == sitio_externo
    hosts = json.loads(Path(aprobado["allowed_hosts_file"]).read_text("utf-8"))
    assert hosts["allowed_hosts"] == ["127.0.0.2"]
    monkeypatch.delenv("MONITOREO_FORMS_COMANDO")
    for k, v in variables_motor(forms, pin, py, host_externo="127.0.0.2").items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("MONITOREO_FORMS_HEADLESS", os.environ.get("FORMS_HEADLESS", "1"))
    monkeypatch.setenv("MONITOREO_AUTORRELLENO_ESPERA_S", "30")
    h = lab.ops["op1"]
    a, datos = atencion(lab, h)
    assert pulsar(lab, a["id"], h).status_code == 202
    c = esperar(lab, a["id"], h, t=120)
    assert c["estado"] == "CADUCADA", c                  # rellenado y verificado; nadie cerró la ventana; no se envió
    inf = c["informe"]
    assert sorted(x["campo"] for x in inf["campos"]) == CAMPOS and all(x["correcto"] for x in inf["campos"]), inf
    assert inf["confirmaciones"] and all(x["coinciden"] for x in inf["confirmaciones"])
    assert inf["estructura_coincide"] and not inf["envio_realizado_por_conector"] and not inf["envio_humano_observado"]
    texto = textos_guardados(lab)
    assert datos["correo"] not in texto and datos["telefono"] not in texto
