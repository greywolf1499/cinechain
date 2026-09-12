"""Password hashing and signed session cookies.

bcrypt (not argon2) is used deliberately: argon2's default memory cost
(~64MB per hash) would blow the < 120MB idle RAM budget on its own.
"""

from __future__ import annotations

import hashlib
import secrets

import bcrypt
from fastapi import Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import Settings, get_settings

COOKIE_NAME = "cinechain_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30  # 30 days
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


def _serializer(settings: Settings | None = None) -> URLSafeTimedSerializer:
    settings = settings or get_settings()
    return URLSafeTimedSerializer(
        _get_secret_key(settings),
        salt="cinechain-session",
        signer_kwargs={"digest_method": hashlib.sha256},
    )


def create_session_token(user_id: str) -> str:
    return _serializer().dumps({"user_id": user_id})


def read_session_token(token: str) -> str | None:
    try:
        data = _serializer().loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("user_id")


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
