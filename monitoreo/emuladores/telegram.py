"""Emulador local de la Bot API de Telegram (solo para pruebas, sin credenciales reales).

Expone /api-prueba/telegram/bot{token}/sendMessage y getMe con respuestas con la misma forma
que la API real, y permite provocar fallos:
  POST /api-prueba/telegram/control  {"modo": "normal|429|500|lento", "chats_bloqueados": ["..."],
                                      "chats_validos": ["..."], "retry_after": 3, "fallos_restantes": 2}
  GET  /api-prueba/telegram/mensajes   mensajes recibidos (para verificar)
  POST /api-prueba/telegram/reiniciar  vacía mensajes y vuelve a modo normal
Token válido del emulador: MONITOREO_TELEGRAM_EMULADOR_TOKEN (por defecto "000000:EMULADOR").
"""
from __future__ import annotations

import os
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api-prueba/telegram", include_in_schema=False)
_lock = threading.Lock()
STATE: dict = {}


def reset() -> None:
    with _lock:
        STATE.clear()
        STATE.update({"modo": "normal", "chats_bloqueados": [], "chats_validos": None, "retry_after": 3,
                      "fallos_restantes": None, "mensajes": [], "next_id": 1})


reset()


def _token() -> str:
    return os.environ.get("MONITOREO_TELEGRAM_EMULADOR_TOKEN", "000000:EMULADOR")


def _err(code: int, desc: str, **extra):
    return JSONResponse({"ok": False, "error_code": code, "description": desc, **extra}, status_code=code)


@router.get("/bot{token}/getMe")
def get_me(token: str):
    if token != _token():
        return _err(401, "Unauthorized")
    return {"ok": True, "result": {"id": 1, "is_bot": True, "first_name": "Emulador", "username": "emulador_bot"}}


@router.post("/bot{token}/sendMessage")
async def send_message(token: str, request: Request):
    if token != _token():
        return _err(401, "Unauthorized")
    body = await request.json()
    chat = str(body.get("chat_id", ""))
    with _lock:
        modo = STATE["modo"]
        if STATE["fallos_restantes"] is not None:
            if STATE["fallos_restantes"] <= 0:
                modo = STATE["modo"] = "normal"
            else:
                STATE["fallos_restantes"] -= 1
    if modo == "429":
        return _err(429, "Too Many Requests: retry later", parameters={"retry_after": STATE["retry_after"]})
    if modo == "500":
        return _err(500, "Internal Server Error")
    if modo == "lento":
        time.sleep(3)
    if not chat or (STATE["chats_validos"] is not None and chat not in STATE["chats_validos"]):
        return _err(400, "Bad Request: chat not found")
    if chat in STATE["chats_bloqueados"]:
        return _err(403, "Forbidden: bot was blocked by the user")
    text = str(body.get("text", ""))
    if not text or len(text) > 4096:
        return _err(400, "Bad Request: message text is empty or too long")
    with _lock:
        mid = STATE["next_id"]
        STATE["next_id"] += 1
        STATE["mensajes"].append({"message_id": mid, "chat_id": chat, "text": text, "ts": time.time()})
    return {"ok": True, "result": {"message_id": mid, "chat": {"id": chat}, "date": int(time.time()), "text": text}}


@router.post("/control")
async def control(request: Request):
    body = await request.json()
    with _lock:
        for k in ("modo", "chats_bloqueados", "chats_validos", "retry_after", "fallos_restantes"):
            if k in body:
                STATE[k] = body[k]
    return {"ok": True}


@router.get("/mensajes")
def mensajes():
    with _lock:
        return {"total": len(STATE["mensajes"]), "mensajes": list(STATE["mensajes"])[-100:]}


@router.post("/reiniciar")
def reiniciar():
    reset()
    return {"ok": True}
