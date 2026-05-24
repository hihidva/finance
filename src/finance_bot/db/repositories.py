"""Lightweight repositories for upserts and common queries."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from finance_bot.db.models import (
    Asset,
    CorporateEvent,
    FetchLog,
    FundamentalSnapshotRow,
    IndustryAverageRow,
    Knowledge,
    News,
    Price,
    Signal,
    VnFlow,
    WatchlistEntry,
)
from finance_bot.settings import AssetConfig


def upsert_asset(session: Session, cfg: AssetConfig) -> Asset:
    """Insert asset if missing; otherwise return existing row."""
    stmt = select(Asset).where(
        Asset.symbol == cfg.symbol, Asset.asset_class == cfg.asset_class
    )
    existing = session.scalars(stmt).one_or_none()
    if existing:
        existing.name = cfg.name
        existing.source = cfg.source
        existing.exchange = cfg.exchange
        existing.context_only = cfg.context_only
        existing.is_active = True
        return existing

    asset = Asset(
        symbol=cfg.symbol,
        name=cfg.name,
        asset_class=cfg.asset_class,
        source=cfg.source,
        exchange=cfg.exchange,
        context_only=cfg.context_only,
        is_active=True,
    )
    session.add(asset)
    session.flush()  # populate asset.id
    return asset


def bulk_upsert_prices(
    session: Session,
    asset_id: int,
    timeframe: str,
    candles: Iterable[dict],
) -> int:
    """Insert OHLCV candles, ignoring duplicates on (asset_id, timeframe, ts).

    Each candle dict: {ts, open, high, low, close, volume}.
    Returns number of rows actually inserted.
    """
    rows = [
        {
            "asset_id": asset_id,
            "timeframe": timeframe,
            "ts": c["ts"],
            "open": Decimal(str(c["open"])),
            "high": Decimal(str(c["high"])),
            "low": Decimal(str(c["low"])),
            "close": Decimal(str(c["close"])),
            "volume": Decimal(str(c.get("volume", 0))),
        }
        for c in candles
    ]
    if not rows:
        return 0

    stmt = mysql_insert(Price).values(rows)
    # ON DUPLICATE KEY UPDATE ts=ts -> no-op so insert is idempotent
    stmt = stmt.on_duplicate_key_update(ts=stmt.inserted.ts)
    result = session.execute(stmt)
    return result.rowcount or 0


def latest_price_ts(session: Session, asset_id: int, timeframe: str) -> datetime | None:
    stmt = (
        select(Price.ts)
        .where(Price.asset_id == asset_id, Price.timeframe == timeframe)
        .order_by(Price.ts.desc())
        .limit(1)
    )
    return session.scalars(stmt).one_or_none()


def bulk_upsert_vn_flows(session: Session, asset_id: int, rows: Iterable[dict]) -> int:
    """Upsert daily VN flow rows. Each row must contain `trade_date` (date)."""
    payload = [{"asset_id": asset_id, **r} for r in rows]
    if not payload:
        return 0
    stmt = mysql_insert(VnFlow).values(payload)
    update_cols = {c.name: stmt.inserted[c.name] for c in VnFlow.__table__.columns
                   if c.name not in {"id", "asset_id", "trade_date", "created_at"}}
    stmt = stmt.on_duplicate_key_update(**update_cols)
    return session.execute(stmt).rowcount or 0


def bulk_upsert_corporate_events(
    session: Session, asset_id: int, rows: Iterable[dict]
) -> int:
    """Upsert corporate events; unique on (asset_id, event_type, event_date)."""
    payload = [{"asset_id": asset_id, **r} for r in rows]
    if not payload:
        return 0
    stmt = mysql_insert(CorporateEvent).values(payload)
    update_cols = {
        c.name: stmt.inserted[c.name]
        for c in CorporateEvent.__table__.columns
        if c.name not in {"id", "asset_id", "event_type", "event_date", "created_at"}
    }
    stmt = stmt.on_duplicate_key_update(**update_cols)
    return session.execute(stmt).rowcount or 0


def bulk_upsert_news(session: Session, rows: Iterable[dict]) -> int:
    """Insert news rows, skipping duplicates on URL."""
    payload = list(rows)
    if not payload:
        return 0
    stmt = mysql_insert(News).values(payload)
    # No-op on duplicate (preserve original row)
    stmt = stmt.on_duplicate_key_update(url=stmt.inserted.url)
    return session.execute(stmt).rowcount or 0


def latest_alerted_signal(
    session: Session, asset_id: int, within_hours: int
) -> Signal | None:
    """Most recent already-notified Tier A signal for this asset (for cooldown)."""
    cutoff = datetime.utcnow() - timedelta(hours=within_hours)
    stmt = (
        select(Signal)
        .where(
            Signal.asset_id == asset_id,
            Signal.notified.is_(True),
            Signal.notified_at >= cutoff,
        )
        .order_by(Signal.notified_at.desc())
        .limit(1)
    )
    return session.scalars(stmt).one_or_none()


def insert_signal(session: Session, payload: dict) -> Signal:
    """Persist a SignalDecision row. Caller flushes/commits."""
    signal = Signal(**payload)
    session.add(signal)
    session.flush()
    return signal


def mark_signal_notified(
    session: Session, signal_id: int, message_id: int | None = None
) -> None:
    signal = session.get(Signal, signal_id)
    if signal is None:
        return
    signal.notified = True
    signal.notified_at = datetime.utcnow()
    if message_id is not None:
        signal.notification_message_id = message_id


def set_user_decision(
    session: Session, signal_id: int, decision: str
) -> Signal | None:
    """decision ∈ {'entered','skipped'}. Idempotent: ghi đè nếu user đổi ý."""
    if decision not in ("entered", "skipped"):
        return None
    signal = session.get(Signal, signal_id)
    if signal is None:
        return None
    signal.user_decision = decision
    signal.user_decision_at = datetime.utcnow()
    return signal


def insert_knowledge(
    session: Session,
    *,
    title: str,
    body: str,
    tags: list[str] | None = None,
    source: str = "user",
) -> Knowledge:
    kb = Knowledge(title=title, body=body, tags=tags, source=source, is_active=True)
    session.add(kb)
    session.flush()
    return kb


def list_knowledge(session: Session, only_active: bool = True) -> list[Knowledge]:
    stmt = select(Knowledge)
    if only_active:
        stmt = stmt.where(Knowledge.is_active.is_(True))
    stmt = stmt.order_by(Knowledge.created_at.desc())
    return list(session.scalars(stmt).all())


def deactivate_knowledge(session: Session, kb_id: int) -> bool:
    kb = session.get(Knowledge, kb_id)
    if not kb:
        return False
    kb.is_active = False
    return True


def update_knowledge_chroma_id(session: Session, kb_id: int, chroma_id: str) -> None:
    kb = session.get(Knowledge, kb_id)
    if kb:
        kb.chroma_id = chroma_id


def list_watchlist_entries(
    session: Session,
    *,
    only_active: bool = False,
    asset_class: str | None = None,
) -> list[WatchlistEntry]:
    stmt = select(WatchlistEntry)
    if only_active:
        stmt = stmt.where(WatchlistEntry.is_active.is_(True))
    if asset_class:
        stmt = stmt.where(WatchlistEntry.asset_class == asset_class)
    stmt = stmt.order_by(WatchlistEntry.symbol.asc())
    return list(session.scalars(stmt).all())


def get_watchlist_entry(session: Session, entry_id: int) -> WatchlistEntry | None:
    return session.get(WatchlistEntry, entry_id)


def get_watchlist_entry_by_symbol(session: Session, symbol: str) -> WatchlistEntry | None:
    stmt = select(WatchlistEntry).where(WatchlistEntry.symbol == symbol)
    return session.scalars(stmt).one_or_none()


def insert_watchlist_entry(session: Session, **fields) -> WatchlistEntry:
    entry = WatchlistEntry(**fields)
    session.add(entry)
    session.flush()
    return entry


def upsert_watchlist_entry_from_cfg(
    session: Session, cfg: AssetConfig, *, overwrite: bool = False
) -> tuple[WatchlistEntry, bool]:
    """Seed from AssetConfig (YAML). Returns (entry, inserted_bool).

    overwrite=False  → only insert when symbol missing.
    overwrite=True   → also update existing row.
    """
    existing = get_watchlist_entry_by_symbol(session, cfg.symbol)
    if existing:
        if overwrite:
            existing.name = cfg.name
            existing.asset_class = cfg.asset_class
            existing.source = cfg.source
            existing.exchange = cfg.exchange
            existing.timeframes = list(cfg.timeframes)
            existing.context_only = cfg.context_only
        return existing, False

    entry = WatchlistEntry(
        symbol=cfg.symbol,
        name=cfg.name,
        asset_class=cfg.asset_class,
        source=cfg.source,
        exchange=cfg.exchange,
        timeframes=list(cfg.timeframes),
        context_only=cfg.context_only,
        is_active=True,
    )
    session.add(entry)
    session.flush()
    return entry, True


def update_watchlist_entry(
    session: Session, entry_id: int, **fields
) -> WatchlistEntry | None:
    entry = session.get(WatchlistEntry, entry_id)
    if entry is None:
        return None
    for k, v in fields.items():
        if hasattr(entry, k) and k not in {"id", "created_at", "updated_at"}:
            setattr(entry, k, v)
    return entry


def delete_watchlist_entry(session: Session, entry_id: int) -> bool:
    entry = session.get(WatchlistEntry, entry_id)
    if entry is None:
        return False
    session.delete(entry)
    return True


# ----------------------------------------------------------------------
# Fundamentals + industry averages (Module 11 — v2 checklist coverage)
# ----------------------------------------------------------------------

# Whitelist of FundamentalSnapshotRow numeric columns the upsert accepts.
# Kept here (not imported from data layer) so the repository stays at the
# bottom of the dependency chain. Add to this list when models.py grows a
# new ratio column.
_FUNDAMENTAL_RATIO_FIELDS: frozenset[str] = frozenset({
    # v1
    "roa", "roe", "pe", "pb",
    # Phần 1.1 — income statement
    "revenue", "gross_profit", "net_profit", "eps",
    # Phần 1.2 — balance sheet
    "cash_and_equivalents", "total_assets", "total_debt", "total_equity",
    "inventory", "receivables", "current_assets", "current_liabilities",
    # Phần 1.3 — cash flow
    "cfo", "capex", "cff", "fcf",
    # Phần 2.1 — valuation (pe/pb above)
    "ev_ebitda", "ps",
    # Phần 2.2 — profitability (roa/roe above)
    "roic", "gross_margin", "net_margin",
    # Phần 2.3 — leverage & liquidity
    "de_ratio", "current_ratio", "quick_ratio", "interest_coverage",
    # Phần 2.4 — operating efficiency
    "inventory_days", "receivable_days", "ccc",
})

_INDUSTRY_RATIO_FIELDS: frozenset[str] = frozenset({
    "roa_avg", "roa_median", "roe_avg", "roe_median",
    "pe_avg",  "pe_median",  "pb_avg",  "pb_median",
    "ev_ebitda_avg",   "ev_ebitda_median",
    "ps_avg",          "ps_median",
    "roic_avg",        "roic_median",
    "gross_margin_avg", "gross_margin_median",
    "net_margin_avg",   "net_margin_median",
    "de_ratio_avg",     "de_ratio_median",
})


def upsert_fundamental_snapshot(
    session: Session,
    *,
    asset_symbol: str,
    period: str,
    period_end,
    industry_code: str | None = None,
    source: str = "vnstock",
    raw_payload: dict | None = None,
    **ratios,
) -> FundamentalSnapshotRow:
    """Upsert one (symbol, period) snapshot.

    Unlike v1, this does NOT mark older periods as superseded — v2 keeps
    multiple `active` rows per symbol so the engine can compute CAGR / YoY
    across quarters.

    Pass any ratio column from `_FUNDAMENTAL_RATIO_FIELDS` as a keyword
    argument; unknown keys are silently ignored (forward-compat for callers
    upgrading at different rates).
    """
    now = datetime.utcnow()
    clean = {k: v for k, v in ratios.items() if k in _FUNDAMENTAL_RATIO_FIELDS}

    stmt = select(FundamentalSnapshotRow).where(
        FundamentalSnapshotRow.asset_symbol == asset_symbol,
        FundamentalSnapshotRow.period == period,
    )
    existing = session.scalars(stmt).one_or_none()
    if existing:
        for k, v in clean.items():
            setattr(existing, k, v)
        existing.industry_code = industry_code
        existing.raw_payload = raw_payload
        existing.fetched_at = now
        # Preserve 'overridden' if an analyst manually fixed this row.
        if existing.status != "overridden":
            existing.status = "active"
        return existing

    row = FundamentalSnapshotRow(
        asset_symbol=asset_symbol,
        period=period,
        period_end=period_end,
        industry_code=industry_code,
        source=source,
        status="active",
        raw_payload=raw_payload,
        fetched_at=now,
        **clean,
    )
    session.add(row)
    session.flush()
    return row


def latest_fundamental_snapshot(
    session: Session, asset_symbol: str,
) -> FundamentalSnapshotRow | None:
    """Newest snapshot by `period_end`. `active` and `overridden` both qualify."""
    stmt = (
        select(FundamentalSnapshotRow)
        .where(
            FundamentalSnapshotRow.asset_symbol == asset_symbol,
            FundamentalSnapshotRow.status != "superseded",
        )
        .order_by(FundamentalSnapshotRow.period_end.desc())
        .limit(1)
    )
    return session.scalars(stmt).one_or_none()


def recent_fundamental_snapshots(
    session: Session, asset_symbol: str, n: int = 12,
) -> list[FundamentalSnapshotRow]:
    """Return up to `n` most recent snapshots (newest → oldest).

    Used by the v2 micro engine for CAGR / YoY / trend signals across the
    7 checklist sections. Excludes rows marked superseded; includes
    `active` and `overridden`.
    """
    stmt = (
        select(FundamentalSnapshotRow)
        .where(
            FundamentalSnapshotRow.asset_symbol == asset_symbol,
            FundamentalSnapshotRow.status != "superseded",
        )
        .order_by(FundamentalSnapshotRow.period_end.desc())
        .limit(n)
    )
    return list(session.scalars(stmt).all())


def upsert_industry_average(
    session: Session,
    *,
    industry_code: str,
    period: str,
    n_companies: int,
    source: str = "vnstock_screen",
    **aggregates,
) -> IndustryAverageRow:
    """Upsert per-industry, per-period aggregates.

    Pass ratio aggregates (e.g. `roa_avg=...`, `de_ratio_median=...`) as keyword
    arguments; unknown keys are silently ignored.
    """
    now = datetime.utcnow()
    clean = {k: v for k, v in aggregates.items() if k in _INDUSTRY_RATIO_FIELDS}

    stmt = select(IndustryAverageRow).where(
        IndustryAverageRow.industry_code == industry_code,
        IndustryAverageRow.period == period,
    )
    existing = session.scalars(stmt).one_or_none()
    if existing:
        for k, v in clean.items():
            setattr(existing, k, v)
        existing.n_companies = n_companies
        existing.fetched_at = now
        return existing

    row = IndustryAverageRow(
        industry_code=industry_code,
        period=period,
        n_companies=n_companies,
        source=source,
        fetched_at=now,
        **clean,
    )
    session.add(row)
    session.flush()
    return row


def latest_industry_average(
    session: Session, industry_code: str,
) -> IndustryAverageRow | None:
    stmt = (
        select(IndustryAverageRow)
        .where(IndustryAverageRow.industry_code == industry_code)
        .order_by(IndustryAverageRow.fetched_at.desc())
        .limit(1)
    )
    return session.scalars(stmt).one_or_none()


def write_fetch_log(
    session: Session,
    *,
    asset_id: int | None,
    source: str,
    kind: str,
    timeframe: str | None,
    started_at: datetime,
    finished_at: datetime,
    status: str,
    rows_inserted: int,
    error_message: str | None = None,
) -> None:
    session.add(
        FetchLog(
            asset_id=asset_id,
            source=source,
            kind=kind,
            timeframe=timeframe,
            started_at=started_at,
            finished_at=finished_at,
            status=status,
            rows_inserted=rows_inserted,
            error_message=error_message,
        )
    )
