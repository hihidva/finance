"""FastAPI dependencies — wraps existing finance_bot session helpers."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from finance_bot.db.session import get_session


def db_session() -> Iterator[Session]:
    """Per-request DB session. Yields one session, auto-commits/rolls back on exit."""
    with get_session() as session:
        yield session
