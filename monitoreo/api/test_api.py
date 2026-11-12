"""API de prueba local (simula un proveedor externo autorizado) y receptor de webhooks.

/api-prueba/feeds/{feed}   devuelve el inventario ficticio de un feed en el contrato interno.
    ?fallar=1     responde 500 (error temporal)
    ?limitar=1    responde 429 con Retry-After (política del proveedor)
/api-prueba/webhook         recibe webhooks, verifica la firma y guarda los últimos 200.

Solo maneja datos ficticios. Está pensada para escuchar en 127.0.0.1.
"""
from __future__ import annotations

import hashlib
import hmac
from collections import deque

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from ..config import settings
from ..models import SimItem

router = APIRouter(prefix="/api-prueba", include_in_schema=False)
received: deque = deque(maxlen=200)
_seen_ids: set[str] = set()


@router.get("/feeds/{feed}")
def feed(feed: str, request: Request, fallar: int = 0, limitar: int = 0):
    if limitar:
        return JSONResponse({"detail": "demasiadas solicitudes"}, status_code=429, headers={"Retry-After": "30"})
    if fallar:
        raise HTTPException(500, "fallo simulado")
    db = request.app.state.db
    with db.session() as s:
        items = s.scalars(select(SimItem).where(SimItem.feed == feed)).all()
        return {"complete": True, "items": [{
            "resource_key": i.resource_key, "name": i.name, "resource_type": i.resource_type, "region": i.region,
            "starts_at": i.starts_at.isoformat() if i.starts_at else None, "capacity": i.capacity,
            "available": 0 if i.cancelled else i.available, "status": "CANCELADO" if i.cancelled else "ACTIVO",
            "changed_at": i.changed_at.isoformat() if i.changed_at else None} for i in items]}


@router.post("/webhook")
async def webhook(request: Request, x_firma_sha256: str = Header(""), x_id_alerta: str = Header("")):
    body = await request.body()
    secret = getattr(request.app.state, "webhook_secret", None) or settings.secret_key
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, x_firma_sha256):
        raise HTTPException(401, "firma inválida")
    duplicate = x_id_alerta in _seen_ids
    _seen_ids.add(x_id_alerta)
    if not duplicate:
        received.append({"id": x_id_alerta, "len": len(body)})
    return {"ok": True, "duplicado": duplicate}


@router.get("/webhook")
def webhook_list():
    return {"recibidos": len(received), "ultimos": list(received)[-20:]}
