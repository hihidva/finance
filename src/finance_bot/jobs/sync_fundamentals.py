"""Sync fundamental snapshots for every vn_stock in the watchlist.

Daily cron entry. For each ticker, pulls the latest 12 quarters of
fundamentals (ratios + income statement + balance sheet + cash flow) and
upserts them. Idempotent on (symbol, period).
"""
from __future__ import annotations

import dataclasses
from datetime import datetime

from finance_bot.data.vn_fundamentals import (
    FundamentalRow,
    fetch_fundamentals_history,
)
from finance_bot.data.vn_industry import fetch_industry_code
from finance_bot.db.repositories import (
    upsert_asset,
    upsert_fundamental_snapshot,
    write_fetch_log,
)
from finance_bot.db.session import get_session
from finance_bot.logger import logger
from finance_bot.settings import AssetConfig, get_watchlist

# How many quarters of history to keep per ticker. 12 = 3 years, enough for
# CAGR + YoY + trend signals across the 7 quantitative checklist sections.
_HISTORY_QUARTERS = 12

# FundamentalRow fields that map 1:1 onto FundamentalSnapshotRow columns.
# We exclude bookkeeping fields and rebuild the kwargs dict per row.
_PASSTHROUGH_FIELDS = frozenset({
    f.name for f in dataclasses.fields(FundamentalRow)
} - {"asset_symbol", "period", "period_end", "industry_code", "raw"})


def _row_to_kwargs(row: FundamentalRow) -> dict:
    return {k: getattr(row, k) for k in _PASSTHROUGH_FIELDS}


def sync_one(cfg: AssetConfig) -> int:
    """Fetch + upsert up to `_HISTORY_QUARTERS` snapshots for one asset.

    Returns rows inserted/updated.
    """
    started = datetime.utcnow()
    asset_id: int | None = None
    status = "ok"
    rows_written = 0
    err: str | None = None

    try:
        with get_session() as session:
            asset = upsert_asset(session, cfg)
            asset_id = asset.id

            history = fetch_fundamentals_history(cfg.symbol, n_periods=_HISTORY_QUARTERS)
            if not history:
                status = "partial"
            else:
                # Resolve industry code once and stamp every period with it.
                industry_code = (
                    history[0].industry_code or fetch_industry_code(cfg.symbol)
                )
                if industry_code and asset.industry_code != industry_code:
                    asset.industry_code = industry_code

                for fr in history:
                    upsert_fundamental_snapshot(
                        session,
                        asset_symbol=fr.asset_symbol,
                        period=fr.period,
                        period_end=fr.period_end,
                        industry_code=industry_code,
                        raw_payload=fr.raw,
                        **_row_to_kwargs(fr),
                    )
                    rows_written += 1
    except Exception as exc:
        logger.exception("sync_fundamentals failed for {}", cfg.symbol)
        status = "error"
        err = str(exc)[:500]

    with get_session() as session:
        write_fetch_log(
            session,
            asset_id=asset_id,
            source="vnstock",
            kind="price",   # FetchLog enum has no 'fundamental'; reuse closest
            timeframe=None,
            started_at=started,
            finished_at=datetime.utcnow(),
            status=status,
            rows_inserted=rows_written,
            error_message=err,
        )
    return rows_written


def run_all() -> None:
    wl = get_watchlist()
    primary_vn = [a for a in wl.primary_assets if a.asset_class == "vn_stock"]
    logger.info("sync_fundamentals: {} VN tickers × {} quarters",
                len(primary_vn), _HISTORY_QUARTERS)
    total = 0
    for cfg in primary_vn:
        total += sync_one(cfg)
    logger.info("sync_fundamentals: done — {} rows", total)
