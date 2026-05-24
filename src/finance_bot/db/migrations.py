"""Forward-only SQL migration runner — MySQL production only.

Reads `.sql` files from `<repo>/migrations/` (sorted alphabetically; date
prefix `YYYY-MM-DD_*.sql` doubles as chronological order), executes each
one not yet recorded in the `schema_migrations` tracking table, and inserts
a row when done. Idempotent — re-running is a no-op once everything is up.

Statement splitting is naïve:
    - drop blank / `--`-only lines
    - split by `;`
    - skip `USE <db>;` (connection is already on the target DB from .env)
    - run each remaining chunk via `engine.execute(text(stmt))`

Adequate for ALTER TABLE / CREATE TABLE migrations the project actually
uses. Not safe for stored procedures, triggers, or string literals that
contain `;` — switch to Alembic when those appear.

SQLite (test mode) is intentionally skipped: test schema is built fresh
from `models.py` via `db-init`, so SQL migrations don't apply.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from finance_bot.db.session import get_engine
from finance_bot.logger import logger

# repo root = src/finance_bot/db/migrations.py → parents[3]
_DEFAULT_DIR = Path(__file__).resolve().parents[3] / "migrations"

_CREATE_TRACKER_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   VARCHAR(255) NOT NULL PRIMARY KEY,
    applied_at DATETIME     NOT NULL
)
"""


def _split_statements(content: str) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buf.append(line)
        if stripped.endswith(";"):
            stmt = "\n".join(buf).rstrip(";").strip()
            if stmt:
                out.append(stmt)
            buf = []
    if buf:
        leftover = "\n".join(buf).strip()
        if leftover:
            out.append(leftover)
    return [s for s in out if not _is_connection_directive(s)]


def _is_connection_directive(stmt: str) -> bool:
    """Statements that target the SQL CLI session, not the actual schema.

    Skipped by the runner because SQLAlchemy is already connected to the
    target DB from `.env` — re-issuing `USE <db>` here can throw if the
    hardcoded name in the SQL file differs from the env-configured DB.
    """
    first = stmt.lstrip().split(None, 1)[0].upper() if stmt.strip() else ""
    return first in {"USE", "DELIMITER"}


def _ensure_tracker(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(_CREATE_TRACKER_TABLE))


def _applied_set(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT filename FROM schema_migrations"))
        return {r[0] for r in rows}


def stamp_pending(
    migrations_dir: Path | None = None,
    engine: Engine | None = None,
) -> list[str]:
    """Mark every pending `.sql` file as applied WITHOUT executing its SQL.

    Use case: `db-init` on a fresh DB has already produced the schema that
    those migration files describe (via `Base.metadata.create_all` from
    `models.py`). Re-running the SQL would duplicate columns and fail — so
    stamp the files instead, then future `db-migrate` is a no-op.

    Skips SQLite (test mode keeps no migration tracking — schema is always
    rebuilt from `models.py`).
    """
    migrations_dir = migrations_dir or _DEFAULT_DIR
    engine = engine or get_engine()
    if engine.dialect.name == "sqlite":
        return []
    if not migrations_dir.exists():
        return []

    _ensure_tracker(engine)
    applied = _applied_set(engine)
    files = sorted(p for p in migrations_dir.glob("*.sql") if p.is_file())
    pending = [p for p in files if p.name not in applied]
    if not pending:
        return []

    stamped: list[str] = []
    with engine.begin() as conn:
        for path in pending:
            conn.execute(
                text(
                    "INSERT INTO schema_migrations(filename, applied_at) "
                    "VALUES (:f, :t)"
                ),
                {"f": path.name, "t": datetime.utcnow()},
            )
            stamped.append(path.name)
    return stamped


def run_pending(
    migrations_dir: Path | None = None,
    engine: Engine | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """Apply every `.sql` file not yet recorded.

    Returns (newly_applied, already_applied, skipped_sqlite).
    """
    migrations_dir = migrations_dir or _DEFAULT_DIR
    engine = engine or get_engine()

    if engine.dialect.name == "sqlite":
        files = (
            sorted(p.name for p in migrations_dir.glob("*.sql"))
            if migrations_dir.exists() else []
        )
        logger.info(
            "db-migrate: SQLite engine detected — skipping {} migration file(s); "
            "use db-init for test mode", len(files),
        )
        return [], [], files

    if not migrations_dir.exists():
        logger.warning("migrations dir not found: {}", migrations_dir)
        return [], [], []

    _ensure_tracker(engine)
    applied = _applied_set(engine)

    files = sorted(p for p in migrations_dir.glob("*.sql") if p.is_file())
    newly: list[str] = []
    already: list[str] = []

    for path in files:
        if path.name in applied:
            already.append(path.name)
            continue

        stmts = _split_statements(path.read_text())
        if not stmts:
            logger.info("{} has no statements; recording as applied", path.name)
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO schema_migrations(filename, applied_at) "
                        "VALUES (:f, :t)"
                    ),
                    {"f": path.name, "t": datetime.utcnow()},
                )
            newly.append(path.name)
            continue

        logger.info("applying {} ({} statement(s))", path.name, len(stmts))
        with engine.begin() as conn:
            for stmt in stmts:
                conn.execute(text(stmt))
            conn.execute(
                text(
                    "INSERT INTO schema_migrations(filename, applied_at) "
                    "VALUES (:f, :t)"
                ),
                {"f": path.name, "t": datetime.utcnow()},
            )
        newly.append(path.name)

    return newly, already, []
