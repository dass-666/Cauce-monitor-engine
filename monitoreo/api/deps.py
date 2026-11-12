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
        op = s.query(Operator.id).filter(Operator.user_id == user.id).first()
        return CurrentUser(user.id, user.username, user.role, op[0] if op else None, cred.credentials)


def require_admin(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Requiere rol administrador")
    return user


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""
