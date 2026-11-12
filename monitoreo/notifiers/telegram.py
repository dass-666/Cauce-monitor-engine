"""Canal Telegram (Bot API: sendMessage).

Configuración SOLO por variables de entorno (la credencial nunca va a la base de datos):
  MONITOREO_TELEGRAM_TOKEN      token del bot que da @BotFather
  MONITOREO_TELEGRAM_CHATS      ids de chat AUTORIZADOS, separados por coma
  MONITOREO_TELEGRAM_API        (opcional) base de la API; por defecto https://api.telegram.org
                                En pruebas apunta al emulador local.
En el canal (panel) se puede limitar a un subconjunto de chats autorizados y de tipos de alerta:
  {"chats": ["123"], "tipos": ["ASIGNACION", "SISTEMA"]}

Privacidad: el mensaje se construye con una lista blanca de campos. Nunca incluye el
titular del expediente ni otros datos personales; solo código de solicitud, región,
recurso, prioridad, operador y estado.

Entrega por destinatario: se recuerda en Delivery.meta a qué chats ya se entregó, así un
reintento (por 429, 5xx o corte) no repite el mensaje a quien ya lo recibió.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx

from ..config import settings
from ..engine.alerts import ChannelSender, DeliveryError, http_client

DEFAULT_API = "https://api.telegram.org"
MAX_LEN = 4000
ICONS = {"ASIGNACION": "🟢", "DISPONIBILIDAD": "🔵", "SISTEMA": "🔴"}


def telegram_env() -> dict:
    chats = [c.strip() for c in os.environ.get("MONITOREO_TELEGRAM_CHATS", "").split(",") if c.strip()]
    return {"token": os.environ.get("MONITOREO_TELEGRAM_TOKEN", "").strip(), "chats": chats,
            "api": os.environ.get("MONITOREO_TELEGRAM_API", DEFAULT_API).rstrip("/")}


def telegram_status() -> dict:
    """Estado de configuración, sin revelar el token."""
    e = telegram_env()
    return {"token_configurado": bool(e["token"]), "chats_autorizados": len(e["chats"]),
            "api": e["api"], "es_emulador": urlparse(e["api"]).hostname != "api.telegram.org"}


def format_message(p: dict) -> str:
    kind = p.get("tipo", "")
    lines = [f"{ICONS.get(kind, '•')} {p.get('titulo', '')}"]
    fields = [("Tipo", kind), ("Región", p.get("region")), ("Fuente", p.get("fuente")), ("Recurso", p.get("recurso")),
              ("Solicitud", p.get("solicitud")), ("Prioridad", p.get("prioridad")), ("Operador", p.get("operador")),
              ("Cantidad", p.get("cantidad")), ("Inicio", p.get("inicio")), ("Cambio", p.get("cambio")),
              ("Fallos seguidos", p.get("fallos_consecutivos")), ("Fecha", p.get("fecha_hora"))]
    for label, v in fields:
        if v not in (None, "", []):
            lines.append(f"{label}: {v}")
    lines.append(f"Ref: {p.get('dedup_key', '')[:24]}")  # permite detectar repetidos
    lines.append("Confirma la recepción en el panel.")
    text = "\n".join(str(x) for x in lines)
    return text[:MAX_LEN]


class TelegramSender(ChannelSender):
    def _targets(self, env: dict) -> list[str]:
        allowed = env["chats"]
        wanted = [str(c) for c in (self.config.get("chats") or allowed)]
        unauthorized = [c for c in wanted if c not in allowed]
        if unauthorized:
            raise DeliveryError(f"chats no autorizados en MONITOREO_TELEGRAM_CHATS: {len(unauthorized)}",
                                permanent=True)
        return wanted

    def send(self, alert_payload: dict, progress: dict | None = None) -> str:
        env = telegram_env()
        if not env["token"] or not env["chats"]:
            raise DeliveryError("Telegram no configurado (MONITOREO_TELEGRAM_TOKEN / _CHATS)", permanent=True)
        host = urlparse(env["api"]).hostname
        if host != "api.telegram.org" and host not in settings.webhook_allowed_hosts:
            raise DeliveryError(f"host de API no permitido: {host}", permanent=True)
        progress = progress if progress is not None else {}
        done: list = progress.setdefault("entregado_a", [])
        failed: dict = progress.setdefault("rechazado_por", {})
        text = format_message(alert_payload)
        transient: list[str] = []
        retry_after = None
        for chat in self._targets(env):
            if chat in done or chat in failed:
                continue
            try:
                r = http_client().post(f"{env['api']}/bot{env['token']}/sendMessage",
                                      json={"chat_id": chat, "text": text, "disable_web_page_preview": True},
                                      timeout=float(self.config.get("timeout_s", 10)))
            except httpx.HTTPError as e:
                # El mensaje de httpx incluye la URL (con el token): no se propaga.
                transient.append(f"chat …{chat[-3:]}: red ({e.__class__.__name__})")
                continue
            try:
                body = r.json()
            except ValueError:
                body = {}
            if r.status_code == 200 and body.get("ok"):
                done.append(chat)
                self.checkpoint(progress)  # v1.2: guardado inmediato; tras una caída no se repite a este chat
            elif r.status_code == 429:
                retry_after = float((body.get("parameters") or {}).get("retry_after") or 5)
                transient.append(f"chat …{chat[-3:]}: limitado {retry_after:.0f}s")
                break  # Telegram pide esperar: no seguir enviando ahora
            elif r.status_code in (400, 403):
                failed[chat] = f"{r.status_code}: {str(body.get('description', ''))[:80]}"
                self.checkpoint(progress)
            elif r.status_code == 401:
                raise DeliveryError("token de Telegram inválido (401)", permanent=True)
            else:
                transient.append(f"chat …{chat[-3:]}: HTTP {r.status_code}")
        if transient:
            raise DeliveryError("; ".join(transient), retry_after=retry_after)
        if not done:
            raise DeliveryError(f"ningún destinatario aceptó el mensaje: {failed}", permanent=True)
        extra = f"; rechazado por {len(failed)} destinatario(s)" if failed else ""
        return f"entregado a {len(done)} chat(s){extra}"


# Registro del canal (también si este módulo se importa antes que engine.alerts).
from ..engine.alerts import SENDERS as _SENDERS  # noqa: E402

_SENDERS["telegram"] = TelegramSender
