"""E10 S5: el centro de revisiones distingue las revisiones de Network Engine (red/sesión) de las operaciones locales
pendientes en los puestos Forms, y avisa de que resolver una revisión de red NO confirma ninguna recepción.

Unitarias sobre el saneado (lo que llega a la pantalla). La prueba con el motor E10 real está en Network Engine
(tests/test_e10_forms_nativo.py) y en el laboratorio integral E10."""
import json
from pathlib import Path

from monitoreo.red_lectura import servicio as SV


def ses(activas=(), pend=()):
    return {"schema": "cauce.sessions/1", "sessions": list(activas), "closed": [], "pending_human_review": list(pend)}


def act(label, local=None, rh=False, reason=None):
    x = {"label": label, "phase": "requiere_intervencion" if rh else "activa", "requires_human": rh,
         "human_reason": reason, "operation": "idle", "owner_token_id": "SECRETO-tk"}
    if local is not None:
        x["local_review"] = local
    return x


def test_separa_revisiones_de_red_y_de_forms():
    v = SV.ServicioRed._sanear_sesiones(ses([
        act("forms-ventanilla-1", "pending", True, "owner_local_review_pending"),
        act("forms-ventanilla-2", "none"),
        act("forms-ventanilla-3"),                    # Forms que no informa (RC3 o Forms anterior)
        act("lab-supervision-x", rh=True, reason="operation_interrupted")]))
    assert v["revisiones_red"] == 2                     # las dos sesiones que piden persona en el motor
    assert v["revisiones_forms"] == 1
    assert v["forms_sin_informar"] == 1
    assert v["forms_puestos"] == 3
    filas = {f["etiqueta"]: f for f in v["activas"]}
    assert filas["forms-ventanilla-1"]["revision_local"] == "pending"
    assert filas["forms-ventanilla-2"]["revision_local"] == "none"
    assert filas["forms-ventanilla-3"]["revision_local"] is None
    assert filas["lab-supervision-x"]["es_forms"] is False
    assert "SECRETO" not in json.dumps(v)


def test_valor_desconocido_de_local_review_no_pasa_tal_cual():
    v = SV.ServicioRed._sanear_sesiones(ses([act("forms-a", "<script>")]))
    assert v["activas"][0]["revision_local"] is None


def test_la_pantalla_avisa_que_resolver_no_confirma_recepcion():
    js = (Path(SV.__file__).resolve().parents[1] / "web" / "revisiones.js").read_text("utf-8")
    assert "NO confirma que el portal recibiera ninguna operación" in js
    assert "data-forms-revisiones" in js and "data-red-revisiones-red" in js
    # la pantalla de red sigue sin escribir nada
    red = (Path(SV.__file__).resolve().parents[1] / "web" / "red.js").read_text("utf-8")
    assert "method: \"POST\"" not in red
