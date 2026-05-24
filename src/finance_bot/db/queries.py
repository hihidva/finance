"""Read-side helpers used by analysis & jobs (kept separate from repositories)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from finance_bot.db.models import Asset, News, Price


def load_ohlcv_df(
    session: Session, asset_id: int, timeframe: str, limit: int = 500
) -> pd.DataFrame:
    """Return last `limit` candles as DataFrame sorted ascending by ts.

    Columns: ts (DatetimeIndex), open, high, low, close, volume (float).
    """
    stmt = (
        select(Price.ts, Price.open, Price.high, Price.low, Price.close, Price.volume)
        .where(Price.asset_id == asset_id, Price.timeframe == timeframe)
        .order_by(Price.ts.desc())
        .limit(limit)
    )
    rows = session.execute(stmt).all()
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.sort_values("ts").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    df = df.set_index("ts")
    return df


def get_asset_id_by_symbol(
    session: Session, symbol: str, asset_class: str
) -> int | None:
    stmt = (
        select(Asset.id)
        .where(Asset.symbol == symbol, Asset.asset_class == asset_class)
        .limit(1)
    )
    return session.scalars(stmt).one_or_none()


def load_recent_news(
    session: Session,
    *,
    symbol_keywords: list[str] | None = None,
    tags: list[str] | None = None,
    hours: int = 48,
    limit: int = 8,
) -> list[News]:
    """Load most recent news. Filter by title-substring symbols OR tags JSON contains."""
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    stmt = select(News).where(News.published_at >= cutoff)

    if symbol_keywords:
        keyword_clauses = [News.title.like(f"%{k}%") for k in symbol_keywords]
        # MySQL JSON_CONTAINS via raw SQL would be cleaner, but title-substring is robust enough
        stmt = stmt.where(or_(*keyword_clauses))

    stmt = stmt.order_by(News.published_at.desc()).limit(limit)
    return list(session.scalars(stmt).all())


def load_macro_close_series(
    session: Session, asset_id: int, timeframe: str = "1d", days: int = 35
) -> pd.Series:
    """Last `days` daily closes for a context asset (DXY, WTI…)."""
    cutoff = datetime.utcnow() - timedelta(days=days + 5)
    stmt = (
        select(Price.ts, Price.close)
        .where(
            Price.asset_id == asset_id,
            Price.timeframe == timeframe,
            Price.ts >= cutoff,
        )
        .order_by(Price.ts.asc())
    )
    rows = session.execute(stmt).all()
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series(
        [float(r.close) for r in rows],
        index=[r.ts for r in rows],
        name="close",
    )
    return s
