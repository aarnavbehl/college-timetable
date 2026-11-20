"""SQLAlchemy engine/session. SQLite by default; foreign keys are enforced."""
from __future__ import annotations

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from .config import get_settings
from .models import Base

_url = get_settings().database_url
engine = create_engine(
    _url, connect_args={"check_same_thread": False} if _url.startswith("sqlite") else {}
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _):
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


# Columns added after the first release. create_all() creates missing TABLES but never alters existing
# ones, so these are added with a guarded ALTER TABLE (a no-op once the column exists).
_ADDED_COLUMNS = [("rooms", "is_active", "BOOLEAN NOT NULL DEFAULT {true}")]


def ensure_schema(eng=None) -> list[str]:
    """Create missing tables and add missing columns. Returns the columns it had to add."""
    eng = eng or engine
    Base.metadata.create_all(eng)
    added = []
    existing = inspect(eng)
    for table, column, ddl in _ADDED_COLUMNS:
        if table in existing.get_table_names() and column not in {c["name"] for c in existing.get_columns(table)}:
            true = "1" if eng.dialect.name == "sqlite" else "TRUE"
            with eng.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl.format(true=true)}"))
            added.append(f"{table}.{column}")
    return added


def init_db() -> None:
    ensure_schema()


def get_db():
    """FastAPI dependency (tests override this with an in-memory database)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
