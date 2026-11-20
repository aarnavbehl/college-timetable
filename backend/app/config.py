"""Settings from environment variables / backend/.env (everything has a default)."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent
load_dotenv(BACKEND_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    database_url: str
    token_secret: str
    token_hours: int
    port: int
    frontend_dir: Path


def _token_secret() -> str:
    if os.getenv("TOKEN_SECRET"):
        return os.environ["TOKEN_SECRET"]
    key_file = BACKEND_DIR / ".secret_key"
    if key_file.exists():
        return key_file.read_text().strip()
    secret = secrets.token_hex(32)
    key_file.write_text(secret)
    return secret


@lru_cache
def get_settings() -> Settings:
    return Settings(
        database_url=os.getenv("DATABASE_URL") or f"sqlite:///{BACKEND_DIR / 'college_timetable.db'}",
        token_secret=_token_secret(),
        token_hours=int(os.getenv("TOKEN_HOURS") or 12),
        port=int(os.getenv("PORT") or 8000),
        frontend_dir=ROOT_DIR / "frontend",
    )
