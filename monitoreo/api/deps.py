from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..db import Database
from ..models import Operator
from ..security import resolve_token

bearer = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    id: int
    username: str
    role: str
    operator_id: int | None
    token: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def get_db(request: Request) -> Database:
    return request.app.state.db


def current_user(request: Request, cred: HTTPAuthorizationCredentials | None = Depends(bearer)) -> CurrentUser:
    if cred is None or cred.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No autenticado", headers={"WWW-Authenticate": "Bearer"})
    db: Database = request.app.state.db
    with db.session() as s:
        user = resolve_token(s, cred.credentials)
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesión inválida o expirada")
        op = s.query(Operator.id, Operator.visto_en).filter(Operator.user_id == user.id).first()
        if op is not None:
            _marcar_presencia(s, op[0], op[1])
        return CurrentUser(user.id, user.username, user.role, op[0] if op else None, cred.credentials)


PRESENCIA_CADA_S = 15


def _marcar_presencia(s, operator_id: int, visto_en) -> None:
    """v1.7: el operador está usando el panel (su página consulta la API cada pocos segundos).
    Se escribe como mucho cada 15 s por operador para no cargar la base. La supervisión distingue
    con esto: conectado, conexión perdida (sin señal unos minutos) e inactivo."""
    from datetime import timedelta

    from sqlalchemy import update

    from ..db import utcnow
    now = utcnow()
    if visto_en is None or now - visto_en >= timedelta(seconds=PRESENCIA_CADA_S):
        s.execute(update(Operator).where(Operator.id == operator_id).values(visto_en=now))


def require_admin(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Requiere rol administrador")
    return user


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""
