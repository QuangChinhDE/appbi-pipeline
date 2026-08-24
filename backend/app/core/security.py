"""Password hashing and session tokens."""

from __future__ import annotations

import time
import uuid
from typing import Any

# PyJWT rather than python-jose: jose is effectively unmaintained, and it pulls
# in `ecdsa`, whose timing-attack advisory has no fixed version. Three call
# sites used it and PyJWT is a drop-in for all three.
import jwt
from jwt import PyJWTError
from passlib.context import CryptContext

from app.core.config import settings
from app.core.errors import UnauthorizedError

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# Long enough to survive an offline attack on the bcrypt hash, and stated as a
# list of specific problems rather than one "password too weak" -- a rule the
# user cannot see is a rule they retry against blindly.
MIN_PASSWORD_LENGTH = 12


def password_problems(raw: str) -> list[str]:
    """Every reason this password is refused, so the user fixes them at once."""
    problems: list[str] = []
    if len(raw) < MIN_PASSWORD_LENGTH:
        problems.append(f"Mật khẩu phải có ít nhất {MIN_PASSWORD_LENGTH} ký tự.")
    if raw.lower() == raw or raw.upper() == raw:
        problems.append("Mật khẩu phải có cả chữ hoa và chữ thường.")
    if not any(character.isdigit() for character in raw):
        problems.append("Mật khẩu phải có ít nhất một chữ số.")
    # Whitespace-only padding passes every rule above and is not a password.
    if not raw.strip():
        problems.append("Mật khẩu không được để trống.")
    return problems


def hash_password(raw: str) -> str:
    return pwd_context.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(raw, hashed)
    except ValueError:
        return False


def issue_session_token(user_id: uuid.UUID, workspace_id: uuid.UUID | None,
                        session_version: int = 0) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "ws": str(workspace_id) if workspace_id else None,
        "iat": now,
        "exp": now + settings.session_ttl_seconds,
        "jti": uuid.uuid4().hex,
        # Bumped whenever the password changes, and compared on every request.
        # Without it a token stayed valid across a password change: two people
        # could sign in with the same one-time bootstrap secret, and after the
        # first changed the password the second still held a live
        # platform-admin session -- the DB flag was clear, so nothing stopped
        # them. `exp` alone does not revoke; `jti` was never checked.
        "sv": int(session_version),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_session_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except PyJWTError as exc:  # noqa: BLE001 - normalised on purpose
        raise UnauthorizedError(technical_message=str(exc)) from exc
