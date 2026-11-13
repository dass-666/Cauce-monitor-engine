"""v1.7 (auditoría): un canal LENTO que sí responde no debe retrasar a los demás canales.

Defecto real (v1.4–v1.6): el despachador solo apartaba un canal tras un FALLO. Un canal que responde
bien pero despacio (p. ej. 2–3 s por envío) consumía todo el presupuesto de la pasada y las entregas
de los demás canales (intercaladas por orden de id) esperaban a pasadas siguientes. Lo vio el
laboratorio v1.7 con un webhook de 2,5 s: cientos de entregas pendientes en cola.
Corrección: cada canal tiene, además, un presupuesto propio por pasada (`despacho_canal_presupuesto_s`);
al agotarlo, sus entregas restantes esperan a la siguiente pasada sin gastar intentos.
"""
import time

from monitoreo.db import utcnow
from monitoreo.engine import alerts as al

from .test_alertas_destino_caido import _count, _two_channels
from .test_notificaciones import _alert, _params

LENTO_S = 0.15


def test_un_canal_lento_que_responde_no_retrasa_a_los_demas(db, monkeypatch):
    _two_channels(db)
    llamadas = {"lento": 0}

    class Lento(al.ChannelSender):
        def send(self, payload, progress=None):
            llamadas["lento"] += 1
            time.sleep(LENTO_S)
            return "HTTP 200"

    class Ok(al.ChannelSender):
        def send(self, payload, progress=None):
            return "ok"
    monkeypatch.setitem(al.SENDERS, "webhook", Lento)
    monkeypatch.setitem(al.SENDERS, "simulado", Ok)
    for k in range(20):
        _alert(db, f"evt:lento-v17-{k}")
    stats = al.dispatch_pending(db, utcnow(), _params(db, despacho_presupuesto_s=1.0,
                                                      despacho_canal_presupuesto_s=0.3))
    assert _count(db, "ENVIADA", "sano") == 20            # el canal rápido no espera al lento
    assert 1 <= llamadas["lento"] <= 4                    # el lento avanza, pero con su propio límite
    assert _count(db, "ENVIADA", "colgado") == llamadas["lento"]
    assert _count(db, "PENDIENTE", "colgado") == 20 - llamadas["lento"]   # nada se pierde
    assert stats["aplazadas_por_lentitud"] >= 1
    # en pasadas siguientes el canal lento termina sin gastar intentos de más
    for _ in range(10):
        al.dispatch_pending(db, utcnow(), _params(db, despacho_presupuesto_s=1.0, despacho_canal_presupuesto_s=0.3))
    assert _count(db, "ENVIADA", "colgado") == 20


def test_presupuesto_por_canal_configurable_y_validado(db):
    import pytest
    from monitoreo.engine.params import get_params, set_params
    with db.session() as s:
        assert float(get_params(s)["despacho_canal_presupuesto_s"]) == 3
        set_params(s, {"despacho_canal_presupuesto_s": 1.5})
    with db.session() as s:
        assert float(get_params(s)["despacho_canal_presupuesto_s"]) == 1.5
        with pytest.raises(ValueError):
            set_params(s, {"despacho_canal_presupuesto_s": 0})
