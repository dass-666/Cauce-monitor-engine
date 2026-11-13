"""Autenticación, contraseñas y tokens.

- Contraseñas: scrypt (biblioteca estándar) con sal aleatoria. Nunca se guardan en claro.
- Tokens de sesión: aleatorios de 256 bits; en la BD solo se guarda su hash SHA-256,
  así un robo de la base de datos no permite suplantar sesiones. Se pueden revocar.
- Bloqueo temporal tras varios intentos fallidos de inicio de sesión.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import utcnow
from .models import ApiToken, AuditLog, User

ROLES = ("admin", "operador")
_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, dk = stored.split("$")
        calc = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                              dklen=len(base64.b64decode(dk)))
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except (ValueError, TypeError):
        return False


def password_problems(password: str) -> list[str]:
    p = []
    if len(password) < 10:
        p.append("mínimo 10 caracteres")
    if password.isalpha() or password.isdigit():
        p.append("combina letras y números o símbolos")
    return p


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(s: Session, user: User, ttl_hours: int) -> str:
    token = secrets.token_urlsafe(32)
    s.add(ApiToken(user_id=user.id, token_hash=_token_hash(token), expires_at=utcnow() + timedelta(hours=ttl_hours)))
    return token


def resolve_token(s: Session, token: str) -> User | None:
    row = s.scalar(select(ApiToken).where(ApiToken.token_hash == _token_hash(token)))
    if row is None or row.revoked or row.expires_at < utcnow():
        return None
    user = s.get(User, row.user_id)
    return user if user and user.active else None


def revoke_other_tokens(s: Session, user_id: int, keep_token: str | None = None) -> int:
    """v1.4: cierra todas las sesiones de un usuario salvo la actual (tras cambiar la contraseña)."""
    keep = _token_hash(keep_token) if keep_token else None
    n = 0
    for row in s.scalars(select(ApiToken).where(ApiToken.user_id == user_id, ApiToken.revoked.is_(False))):
        if row.token_hash != keep:
            row.revoked = True
            n += 1
    return n


def revoke_token(s: Session, token: str) -> None:
    row = s.scalar(select(ApiToken).where(ApiToken.token_hash == _token_hash(token)))
    if row:
        row.revoked = True


def audit(s: Session, username: str, action: str, target: str = "", detail: dict | None = None, ip: str = "") -> None:
    s.add(AuditLog(username=username, action=action, target=str(target)[:128], detail=_redact(detail or {}), ip=ip))


SENSITIVE = ("password", "secret", "token", "clave", "contrasena", "contraseña",
             # v1.5: datos personales de expedientes (defensa adicional: el código no los envía)
             "telefono", "correo", "nacimiento")


def _redact(d):
    """Evita que secretos acaben en el registro de auditoría."""
    if isinstance(d, dict):
        return {k: ("***" if any(x in k.lower() for x in SENSITIVE) else _redact(v)) for k, v in d.items()}
    if isinstance(d, list):
        return [_redact(x) for x in d]
    return d


class LoginThrottle:
    """Máx. `max_fails` fallos por usuario+IP en `window` segundos -> bloqueo temporal."""

    def __init__(self, max_fails: int = 5, window: int = 300):
        self.max_fails, self.window = max_fails, window
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def blocked(self, key: str) -> bool:
        with self._lock:
            now = time.monotonic()
            self._fails[key] = [t for t in self._fails.get(key, []) if now - t < self.window]
            return len(self._fails[key]) >= self.max_fails

    def fail(self, key: str) -> None:
        with self._lock:
            self._fails.setdefault(key, []).append(time.monotonic())

    def reset(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)
