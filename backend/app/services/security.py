"""Password hashing and JWT session cookies (HTTP-Only, so JS/XSS can never read them).

bcrypt (not argon2) is used deliberately: argon2's default memory cost
(~64MB per hash) would blow the < 120MB idle RAM budget on its own.
"""

from __future__ import annotations

import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt
from fastapi import Response

from app.config import Settings, get_settings

COOKIE_NAME = "cinechain_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30  # 30 days
SESSION_RENEW_AFTER_SECONDS = 60 * 60 * 24 * 7
JWT_ALGORITHM = "HS256"
BCRYPT_ROUNDS = 12


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode(
        "utf-8"
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def _get_secret_key(settings: Settings) -> str:
    if settings.secret_key:
        return settings.secret_key

    settings.config_dir.mkdir(parents=True, exist_ok=True)
    key_file = settings.secret_key_file
    if key_file.exists():
        return key_file.read_text().strip()

    secret = secrets.token_urlsafe(64)
    key_file.write_text(secret)
    key_file.chmod(0o600)
    return secret


def create_session_token(user_id: str) -> str:
    """Signed, stateless JWT (HS256). No server-side session table, so any number
    of devices can be logged in at once and each keeps its own independent token."""
    now = datetime.now(UTC)
    claims = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(seconds=SESSION_MAX_AGE_SECONDS),
        "jti": secrets.token_urlsafe(8),
    }
    return jwt.encode(claims, _get_secret_key(get_settings()), algorithm=JWT_ALGORITHM)


def read_session_claims(token: str) -> dict[str, Any] | None:
    """Verified claims, or None for a bad signature, expiry, or a legacy/malformed token."""
    try:
        claims = jwt.decode(
            token,
            _get_secret_key(get_settings()),
            algorithms=[JWT_ALGORITHM],  # pinned: never trust the token's own `alg`
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError:
        return None
    return claims if isinstance(claims.get("sub"), str) else None


def read_session_token(token: str) -> str | None:
    claims = read_session_claims(token)
    return claims["sub"] if claims else None


def session_needs_renewal(claims: dict[str, Any]) -> bool:
    """Sliding session: re-issue once a token is older than a week so an
    actively used phone never gets logged out."""
    return time.time() - float(claims["iat"]) > SESSION_RENEW_AFTER_SECONDS


def set_session_cookie(response: Response, user_id: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=COOKIE_NAME,
        value=create_session_token(user_id),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")
