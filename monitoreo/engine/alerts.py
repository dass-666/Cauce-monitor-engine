"""Sistema de alertas.

Flujo: evento relevante / asignación -> Alert (deduplicada por dedup_key UNIQUE)
-> un Delivery por canal habilitado (bandeja de salida) -> el despachador envía,
reintenta con retroceso exponencial y registra cada intento.

Garantía de entrega: "al menos una vez". Si el proceso muere justo después de
enviar un webhook y antes de guardarlo, al reiniciar se reenvía; por eso cada
webhook lleva la cabecera X-Id-Alerta para que el receptor descarte repetidos.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import random
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from urllib.parse import urlparse

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..db import Database
from ..models import Alert, Channel, Delivery, NotificationLog
from .resilience import backoff_delay

log = logging.getLogger("monitoreo.alertas")

_http_lock = threading.Lock()
_http: httpx.Client | None = None


def http_client() -> httpx.Client:
    """Cliente HTTP compartido por los canales (webhook, Telegram). Crear uno por envío cargaba
    los certificados TLS cada vez (~20 ms de CPU) y no reutilizaba conexiones: con un destinatario
    caído y reintentos, eso dominaba el consumo de CPU (medido en la prueba de resistencia v1.1).
    Sin seguir redirecciones: una redirección podría llevar a un host no permitido."""
    global _http
    with _http_lock:
        if _http is None:
            _http = httpx.Client(follow_redirects=False,
                                 limits=httpx.Limits(max_connections=20, max_keepalive_connections=10))
        return _http


class DeliveryError(Exception):
    def __init__(self, msg: str, permanent: bool = False, retry_after: float | None = None):
        super().__init__(msg)
        self.permanent = permanent
        self.retry_after = retry_after  # p. ej. Telegram 429: segundos que pide esperar


class ChannelSender(ABC):
    """Interfaz de canal. Telegram o correo se integran implementando `send`."""

    def __init__(self, channel: Channel):
        self.channel = channel
        self.config = channel.config or {}

    @abstractmethod
    def send(self, alert_payload: dict, progress: dict | None = None) -> str:
        """Envía y devuelve un detalle corto. Lanza DeliveryError si falla.

        `progress` es un diccionario persistente entre reintentos (Delivery.meta): un canal con
        varios destinatarios anota ahí a quién ya entregó para no repetirle el mensaje."""


class PanelSender(ChannelSender):
    """La alerta queda visible en el panel (ya está en la BD): entrega inmediata."""

    def send(self, alert_payload: dict, progress: dict | None = None) -> str:
        return "visible en panel"


class LogSender(ChannelSender):
    _lock = threading.Lock()

    def send(self, alert_payload: dict, progress: dict | None = None) -> str:
        path = settings.data_dir / "notificaciones.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(alert_payload, ensure_ascii=False, default=str) + "\n")
        return "escrita en notificaciones.log"


class SimulatedSender(ChannelSender):
    """Canal de pruebas: guarda en memoria y puede fallar a propósito (fail_rate)."""
    received: list[dict] = []
    _lock = threading.Lock()
    rng = random.Random()

    def send(self, alert_payload: dict, progress: dict | None = None) -> str:
        if self.rng.random() < float(self.config.get("fail_rate", 0)):
            raise DeliveryError("fallo simulado del canal")
        with self._lock:
            SimulatedSender.received.append(alert_payload)
        return "recibida por canal simulado"


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class WebhookSender(ChannelSender):
    def send(self, alert_payload: dict, progress: dict | None = None) -> str:
        url = self.config.get("url", "")
        host = urlparse(url).hostname
        if urlparse(url).scheme not in ("http", "https") or not host:
            raise DeliveryError("URL de webhook inválida", permanent=True)
        if host not in settings.webhook_allowed_hosts:
            raise DeliveryError(f"host {host} no está en MONITOREO_WEBHOOK_HOSTS", permanent=True)
        body = json.dumps(alert_payload, ensure_ascii=False, default=str).encode()
        secret = self.config.get("secret") or settings.secret_key
        headers = {"Content-Type": "application/json", "X-Id-Alerta": str(alert_payload["dedup_key"]),
                   "X-Firma-SHA256": _sign(body, secret)}
        try:
            r = http_client().post(url, content=body, headers=headers, timeout=float(self.config.get("timeout_s", 5)))
        except httpx.HTTPError as e:
            raise DeliveryError(f"error de red: {e.__class__.__name__}") from e
        if 200 <= r.status_code < 300:
            return f"HTTP {r.status_code}"
        raise DeliveryError(f"HTTP {r.status_code}", permanent=400 <= r.status_code < 500 and r.status_code != 429)


class NotConfiguredSender(ChannelSender):
    """Telegram / correo: interfaz preparada, sin implementación en esta versión."""

    def send(self, alert_payload: dict, progress: dict | None = None) -> str:
        raise DeliveryError(f"canal '{self.channel.type}' preparado pero no implementado", permanent=True)


SENDERS: dict[str, type[ChannelSender]] = {
    "panel": PanelSender, "registro": LogSender, "simulado": SimulatedSender, "webhook": WebhookSender,
    "correo": NotConfiguredSender,
}


def _register_telegram() -> None:
    from ..notifiers.telegram import TelegramSender
    SENDERS["telegram"] = TelegramSender


_register_telegram()


# ---------------------------------------------------------------- creación
def create_alert(s: Session, *, dedup_key: str, kind: str, title: str, source_code: str, region: str,
                 resource: str, now: datetime, change_event_id: int | None = None, assignment_id: int | None = None,
                 request_id: int | None = None, priority: int | None = None, operator_name: str | None = None,
                 payload: dict | None = None) -> Alert | None:
    """Crea la alerta si no existe ya (deduplicación). Devuelve None si era duplicada."""
    if s.scalar(select(Alert.id).where(Alert.dedup_key == dedup_key)) is not None:
        return None
    alert = Alert(dedup_key=dedup_key, kind=kind, title=title, source_code=source_code, region=region,
                  resource=resource, change_event_id=change_event_id, assignment_id=assignment_id,
                  request_id=request_id, priority=priority, operator_name=operator_name, status="PENDIENTE",
                  payload=payload or {}, created_at=now)
    try:
        with s.begin_nested():  # savepoint: si otra transacción ganó la carrera, solo se descarta esta alerta
            s.add(alert)
            s.flush()
    except IntegrityError:
        return None
    for ch in s.scalars(select(Channel).where(Channel.enabled.is_(True))):
        kinds = (ch.config or {}).get("tipos")  # v1.1: un canal puede limitarse a ciertos tipos de alerta
        if kinds and kind not in kinds:
            continue
        s.add(Delivery(alert_id=alert.id, channel_id=ch.id, status="PENDIENTE", next_attempt_at=now))
    return alert


def alert_payload(a: Alert) -> dict:
    return {"id": a.id, "dedup_key": a.dedup_key, "tipo": a.kind, "titulo": a.title, "fuente": a.source_code,
            "region": a.region, "recurso": a.resource, "fecha_hora": a.created_at.isoformat() + "Z",
            "solicitud_id": a.request_id, "prioridad": a.priority, "operador": a.operator_name,
            "estado": a.status, **(a.payload or {})}


# ---------------------------------------------------------------- despacho
def recover_inflight(db: Database, now: datetime | None = None, stale_s: float = 0) -> int:
    """Entregas que quedaron 'ENVIANDO' por un corte vuelven a la cola.
    Con `stale_s` solo las reclamadas hace más de ese tiempo (al tomar el relevo de otro líder)."""
    cond = [Delivery.status == "ENVIANDO"]
    if now is not None and stale_s > 0:
        cond.append((Delivery.claimed_at.is_(None)) | (Delivery.claimed_at < now - timedelta(seconds=stale_s)))
    with db.session() as s:
        return s.execute(update(Delivery).where(*cond).values(status="PENDIENTE")).rowcount


def _refresh_alert_status(s: Session, alert_id: int) -> None:
    a = s.get(Alert, alert_id)
    if a.status == "CONFIRMADA":
        return
    sts = list(s.scalars(select(Delivery.status).where(Delivery.alert_id == alert_id)))
    if not sts or all(x == "ENVIADA" for x in sts):
        a.status = "ENVIADA"
    elif all(x == "FALLIDA" for x in sts):
        a.status = "FALLIDA"
    elif all(x in ("ENVIADA", "FALLIDA") for x in sts):
        a.status = "PARCIAL"
    else:
        a.status = "PENDIENTE"


def dispatch_pending(db: Database, now: datetime, params: dict, limit: int = 200,
                     rng: random.Random | None = None) -> dict:
    stats = {"enviadas": 0, "fallos": 0, "definitivas": 0}
    with db.session() as s:
        ids = list(s.scalars(select(Delivery.id).where(Delivery.status == "PENDIENTE", Delivery.next_attempt_at <= now)
                             .order_by(Delivery.id).limit(limit)))
    for did in ids:
        with db.session() as s:  # reclamar la entrega (evita doble envío si hubiera dos despachadores)
            if s.execute(update(Delivery).where(Delivery.id == did, Delivery.status == "PENDIENTE")
                         .values(status="ENVIANDO", claimed_at=now)).rowcount != 1:
                continue
            d = s.get(Delivery, did)
            ch = s.get(Channel, d.channel_id)
            alert = s.get(Alert, d.alert_id)
            payload = alert_payload(alert)
            sender = SENDERS.get(ch.type, NotConfiguredSender)(ch)
            ch_name = ch.name
            progress = dict(d.meta or {})
        ok, detail, permanent, retry_after = True, "", False, None
        try:
            detail = sender.send(payload, progress)
        except DeliveryError as e:
            ok, detail, permanent, retry_after = False, str(e), e.permanent, e.retry_after
        except Exception as e:  # un canal defectuoso nunca debe tumbar el motor
            ok, detail = False, f"error inesperado: {e.__class__.__name__}"
            log.exception("fallo en canal %s", ch_name)
        with db.session() as s:
            d = s.get(Delivery, did)
            d.attempts += 1
            d.meta = progress or None
            if ok:
                d.status, d.delivered_at, d.last_error = "ENVIADA", now, None
                a = s.get(Alert, d.alert_id)
                d.latency_ms = max(0.0, (now - a.created_at).total_seconds() * 1000)
                stats["enviadas"] += 1
            else:
                stats["fallos"] += 1
                d.last_error = detail
                if permanent or d.attempts >= int(params["reintentos_notificacion"]):
                    d.status = "FALLIDA"
                    stats["definitivas"] += 1
                else:
                    d.status = "PENDIENTE"
                    wait = backoff_delay(d.attempts, float(params["backoff_notificacion_s"]), 300, rng)
                    d.next_attempt_at = now + timedelta(seconds=max(wait, retry_after or 0))
            s.add(NotificationLog(ts=now, alert_id=d.alert_id, channel=ch_name, outcome="OK" if ok else "FALLO",
                                  detail=detail[:500]))
            s.flush()
            _refresh_alert_status(s, d.alert_id)
    return stats


def acknowledge(db: Database, alert_id: int, username: str, now: datetime) -> bool:
    """Confirmación de recepción por una persona (idempotente)."""
    with db.session() as s:
        return s.execute(update(Alert).where(Alert.id == alert_id, Alert.acknowledged_at.is_(None))
                         .values(status="CONFIRMADA", acknowledged_at=now, acknowledged_by=username)).rowcount == 1
