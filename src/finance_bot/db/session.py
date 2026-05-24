"""SQLAlchemy engine + session factory."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from finance_bot.settings import get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Build engine from settings.db_url. Adapts options per dialect.

    SQLite branch is used by `start_test` (APP_ENV=test → .env.test → sqlite:///…).
    SQLite doesn't support MySQL pool params and needs `check_same_thread=False`
    so FastAPI's per-request dependency-injected sessions can cross threads.
    """
    settings = get_settings()
    url = settings.db_url

    if url.startswith("sqlite"):
        # Ensure parent directory exists for file-based SQLite URLs.
        # sqlite:///./.cache/x.db → relative path; sqlite:////abs/x.db → absolute.
        if "///" in url and not url.startswith("sqlite:///:memory:"):
            db_path = url.split("///", 1)[1]
            if db_path and db_path != ":memory:":
                Path(db_path).expanduser().resolve().parent.mkdir(
                    parents=True, exist_ok=True
                )
        return create_engine(
            url,
            future=True,
            connect_args={"check_same_thread": False},
        )

    return create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=3600,
        future=True,
    )


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


@contextmanager
def get_session() -> Iterator[Session]:
    """Context-managed DB session with auto rollback on error."""
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
