"""API serialization helpers."""
from __future__ import annotations

from datetime import datetime


def iso(value: datetime | None) -> str | None:
    """ISO-8601 for an API response. The database stores UTC; SQLite hands it back without a zone, and a
    browser would read a zone-less string as LOCAL time (wrong by the user's UTC offset), so mark it as UTC."""
    if value is None:
        return None
    return value.isoformat() + ("Z" if value.tzinfo is None else "")
