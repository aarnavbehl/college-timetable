"""Passwords (bcrypt) and login tokens (JWT)."""
from __future__ import annotations

import time

import bcrypt
import jwt

from .config import get_settings


def _bytes(password: str) -> bytes:
    return password.encode("utf-8")[:72]          # bcrypt ignores everything after 72 bytes


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_bytes(password), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_bytes(password), hashed.encode())
    except ValueError:
        return False


def create_token(user_id: int) -> str:
    s = get_settings()
    now = int(time.time())
    return jwt.encode({"sub": str(user_id), "iat": now, "exp": now + s.token_hours * 3600},
                      s.token_secret, algorithm="HS256")


def read_token(token: str) -> int | None:
    try:
        return int(jwt.decode(token, get_settings().token_secret, algorithms=["HS256"])["sub"])
    except Exception:
        return None
