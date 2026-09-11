"""SQLAlchemy engine/session setup.

SQLite is the default, but the schema and every query stay on portable
constructs so a PostgreSQL URL can be dropped in without code changes.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_directory(url: str) -> None:
    """Create the parent directory of a SQLite file so the first boot works."""
    parsed = urlparse(url)
    raw_path = (parsed.netloc or "") + (parsed.path or "")
    raw_path = raw_path.lstrip("/") if not url.startswith("sqlite:////") else raw_path
    if not raw_path or raw_path == ":memory:":
        return
    directory = Path(raw_path).parent
    if str(directory) not in ("", "."):
        directory.mkdir(parents=True, exist_ok=True)


def build_engine(url: str | None = None) -> Engine:
    settings = get_settings()
    url = url or settings.database_url

    connect_args: dict = {}
    if url.startswith("sqlite"):
        _ensure_sqlite_directory(url)
        # FastAPI runs sync endpoints in a threadpool, so connections are
        # handed between threads; SQLite needs this flag to allow it.
        connect_args["check_same_thread"] = False

    engine = create_engine(
        url,
        future=True,
        pool_pre_ping=True,
        connect_args=connect_args,
    )

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver hook
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return engine


engine: Engine = build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """Create tables. Import models first so they register on the metadata."""
    from . import models  # noqa: F401  (side-effect import)

    Base.metadata.create_all(bind=engine)
    logger.info("database_initialized", extra={"dialect": engine.dialect.name})


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for background jobs and service-layer helpers."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("database_error")
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def reset_engine_for_tests(url: str) -> Engine:
    """Rebind the module-level engine/session factory (used by the test suite)."""
    global engine
    os.environ["DATABASE_URL"] = url
    engine = build_engine(url)
    # Rebound in place, so sessions already handed out keep working.
    SessionLocal.configure(bind=engine)
    return engine
