"""v1.4 (auditoría): firma de webhooks con marca de tiempo contra repeticiones."""
import json
import time

from monitoreo.engine import alerts as al
from monitoreo.webhook_firma import sign_v2, verify


def test_valid_signature_and_rejections():
    body, secret, now = b'{"id": 1}', "secreto-de-prueba-ficticio", 1_800_000_000
    h = {"X-Marca-Tiempo": str(now), "X-Firma-V2": sign_v2(body, secret, now)}
    assert verify(body, h, secret, now=now) == (True, "ok")
    assert verify(body + b" ", h, secret, now=now)[0] is False                  # cuerpo alterado
    assert verify(body, h, "otro-secreto", now=now)[0] is False                 # secreto distinto
    ok, why = verify(body, h, secret, now=now + 301)                            # petición capturada y reenviada tarde
    assert not ok and "ventana" in why
    forged = {"X-Marca-Tiempo": str(now + 300), "X-Firma-V2": h["X-Firma-V2"]}  # cambiar la marca invalida la firma
    assert verify(body, forged, secret, now=now + 300)[0] is False
    assert verify(body, {}, secret)[0] is False


def test_sender_adds_v2_headers(monkeypatch):
    sent = {}

    class Resp:
        status_code = 200
        headers = {}

    class Client:
        def post(self, url, content, headers, timeout):
            sent.update(body=content, headers=headers)
            return Resp()
    monkeypatch.setattr(al, "http_client", lambda: Client())
    monkeypatch.setattr(al.settings, "webhook_allowed_hosts", ["127.0.0.1"])

    class Ch:
        id, type, config = 7, "webhook", {"url": "http://127.0.0.1:9/x", "secret": "secreto-canal-ficticio"}
    al.WebhookSender(Ch()).send({"dedup_key": "asg:1", "titulo": "prueba"})
    h = sent["headers"]
    assert verify(sent["body"], h, "secreto-canal-ficticio", now=time.time())[0]
    assert h["Idempotency-Key"] == "asg:1@7" and "X-Firma-SHA256" in h          # compatibilidad v1.3
    assert json.loads(sent["body"])["dedup_key"] == "asg:1"
