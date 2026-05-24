"""Sync industry-level ratio averages.

Weekly cron entry. For each industry that ≥1 watchlist asset belongs to, scan
peer tickers from vnstock listing → compute aggregate ROA/ROE/P/E/P/B → upsert.
Slow (one fetch per peer); intentionally weekly, not daily.

Industry membership is read from `assets.industry_code` (filled by
`sync-fundamentals`), so this job must run AFTER fundamentals sync at least
once per period.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime

from sqlalchemy import select

from finance_bot.data.vn_industry import IndustryAverageRowOut, compute_industry_average
from finance_bot.db.models import Asset, FundamentalSnapshotRow
from finance_bot.db.repositories import upsert_industry_average, write_fetch_log
from finance_bot.db.session import get_session
from finance_bot.logger import logger

# Aggregate fields on IndustryAverageRowOut → kwargs for upsert_industry_average.
_AGG_FIELDS = frozenset({
    f.name for f in dataclasses.fields(IndustryAverageRowOut)
} - {"industry_code", "period", "n_companies"})


def _industry_to_symbols(session) -> dict[str, list[str]]:
    """Group active VN assets by their industry_code."""
    stmt = select(Asset.symbol, Asset.industry_code).where(
        Asset.asset_class == "vn_stock",
        Asset.is_active.is_(True),
        Asset.industry_code.isnot(None),
    )
    out: dict[str, list[str]] = {}
    for symbol, code in session.execute(stmt).all():
        out.setdefault(code, []).append(symbol)
    return out


def _latest_period(session) -> str | None:
    """Use the most-recent non-superseded period as the target window."""
    stmt = (
        select(FundamentalSnapshotRow.period)
        .where(FundamentalSnapshotRow.status != "superseded")
        .order_by(FundamentalSnapshotRow.period_end.desc())
        .limit(1)
    )
    val = session.execute(stmt).scalar_one_or_none()
    return val


def run_all() -> None:
    started = datetime.utcnow()
    inserted = 0
    status = "ok"
    err: str | None = None

    try:
        with get_session() as session:
            period = _latest_period(session)
            if not period:
                logger.warning("sync_industry_averages: no fundamental rows yet — "
                               "run sync-fundamentals first")
                status = "partial"
                return

            groups = _industry_to_symbols(session)
            logger.info("sync_industry_averages: {} industries, period={}",
                        len(groups), period)

            for code, symbols in groups.items():
                row = compute_industry_average(code, symbols, period)
                if row is None:
                    continue
                aggregates = {k: getattr(row, k) for k in _AGG_FIELDS}
                upsert_industry_average(
                    session,
                    industry_code=row.industry_code,
                    period=row.period,
                    n_companies=row.n_companies,
                    **aggregates,
                )
                inserted += 1
    except Exception as exc:
        logger.exception("sync_industry_averages failed")
        status = "error"
        err = str(exc)[:500]

    with get_session() as session:
        write_fetch_log(
            session,
            asset_id=None,
            source="vnstock_screen",
            kind="price",     # reuse closest enum value
            timeframe=None,
            started_at=started,
            finished_at=datetime.utcnow(),
            status=status,
            rows_inserted=inserted,
            error_message=err,
        )
    logger.info("sync_industry_averages: done — {} rows", inserted)
