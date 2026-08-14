"""SQLite connection helpers and schema initialization."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Iterator

from src.config import get_settings
from src.logging_setup import setup_logging

logger = setup_logging("db.connection")

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _resolve_db_path() -> Path:
    settings = get_settings()
    path = Path(settings.sqlite_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else _resolve_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_session(db_path: str | Path | None = None) -> Generator[sqlite3.Connection, None, None]:
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str | Path | None = None) -> Path:
    """Create tables and indexes if they do not exist."""
    path = Path(db_path) if db_path else _resolve_db_path()
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    with db_session(path) as conn:
        conn.executescript(schema)
    logger.info("Database initialized at %s", path)
    return path


def rebuild_fts(conn: sqlite3.Connection) -> None:
    """Rebuild FTS index from doctor table (after bulk load)."""
    try:
        conn.execute("INSERT INTO doctor_fts(doctor_fts) VALUES('rebuild')")
    except sqlite3.OperationalError:
        # FTS table may not exist yet
        logger.warning("Could not rebuild FTS index")
