"""SQLAlchemy ORM models — mirror src/finance_bot/db/schema.sql."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (UniqueConstraint("symbol", "asset_class", name="uk_symbol_class"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    asset_class: Mapped[str] = mapped_column(
        Enum("vn_stock", "crypto", "commodity", "fx_index", name="asset_class_enum"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange: Mapped[str | None] = mapped_column(String(32))
    context_only: Mapped[bool] = mapped_column(default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    industry_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    prices: Mapped[list[Price]] = relationship(back_populates="asset", cascade="all, delete")
    signals: Mapped[list[Signal]] = relationship(back_populates="asset", cascade="all, delete")


class Price(Base):
    __tablename__ = "prices"
    __table_args__ = (
        UniqueConstraint("asset_id", "timeframe", "ts", name="uk_asset_tf_ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(28, 8), default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    asset: Mapped[Asset] = relationship(back_populates="prices")


class News(Base):
    __tablename__ = "news"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    url: Mapped[str] = mapped_column(String(768), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    lang: Mapped[str] = mapped_column(String(8), default="vi", nullable=False)
    tags: Mapped[list | None] = mapped_column(JSON)
    related_symbols: Mapped[list | None] = mapped_column(JSON)
    sentiment: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    sentiment_label: Mapped[str | None] = mapped_column(
        Enum("bullish", "bearish", "neutral", "mixed", name="sentiment_label_enum")
    )
    chroma_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    side: Mapped[str] = mapped_column(
        Enum("buy", "sell", "hold", name="signal_side_enum"), nullable=False
    )
    tier: Mapped[str] = mapped_column(
        Enum("A", "B", "C", name="signal_tier_enum"), default="C", nullable=False
    )
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    price_at_signal: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    entry_window: Mapped[str] = mapped_column(
        Enum("immediate", "ato_next_session", name="entry_window_enum"),
        default="immediate",
        nullable=False,
    )
    expected_entry_at: Mapped[datetime | None] = mapped_column(DateTime)
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    take_profit: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    indicators: Mapped[dict] = mapped_column(JSON, nullable=False)
    news_context: Mapped[dict | None] = mapped_column(JSON)
    rag_context: Mapped[dict | None] = mapped_column(JSON)
    llm_model: Mapped[str | None] = mapped_column(String(64))
    llm_reasoning: Mapped[str | None] = mapped_column(Text)
    notified: Mapped[bool] = mapped_column(default=False, nullable=False)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime)
    notification_message_id: Mapped[int | None] = mapped_column(BigInteger)
    user_decision: Mapped[str | None] = mapped_column(
        Enum("entered", "skipped", name="signal_user_decision_enum")
    )
    user_decision_at: Mapped[datetime | None] = mapped_column(DateTime)
    chroma_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    asset: Mapped[Asset] = relationship(back_populates="signals")
    outcomes: Mapped[list[Outcome]] = relationship(
        back_populates="signal", cascade="all, delete"
    )


class Outcome(Base):
    __tablename__ = "outcomes"
    __table_args__ = (
        UniqueConstraint("signal_id", "horizon_hours", name="uk_signal_horizon"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    signal_id: Mapped[int] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"), nullable=False
    )
    horizon_hours: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    price_then: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    pnl_pct: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    hit_target: Mapped[bool] = mapped_column(default=False, nullable=False)
    max_favorable: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    max_adverse: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    signal: Mapped[Signal] = relationship(back_populates="outcomes")


class VnFlow(Base):
    __tablename__ = "vn_flows"
    __table_args__ = (UniqueConstraint("asset_id", "trade_date", name="uk_asset_date"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)

    foreign_buy_vol: Mapped[Decimal | None] = mapped_column(Numeric(28, 8))
    foreign_sell_vol: Mapped[Decimal | None] = mapped_column(Numeric(28, 8))
    foreign_net_vol: Mapped[Decimal | None] = mapped_column(Numeric(28, 8))
    foreign_buy_value: Mapped[Decimal | None] = mapped_column(Numeric(28, 8))
    foreign_sell_value: Mapped[Decimal | None] = mapped_column(Numeric(28, 8))
    foreign_net_value: Mapped[Decimal | None] = mapped_column(Numeric(28, 8))

    proprietary_net_vol: Mapped[Decimal | None] = mapped_column(Numeric(28, 8))
    proprietary_net_value: Mapped[Decimal | None] = mapped_column(Numeric(28, 8))

    margin_outstanding: Mapped[Decimal | None] = mapped_column(Numeric(28, 8))

    raw_payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CorporateEvent(Base):
    __tablename__ = "corporate_events"
    __table_args__ = (
        UniqueConstraint("asset_id", "event_type", "event_date", name="uk_asset_event"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(
        Enum(
            "ex_rights",
            "cash_dividend",
            "stock_dividend",
            "rights_issue",
            "stock_split",
            "agm",
            "other",
            name="corporate_event_type_enum",
        ),
        nullable=False,
    )
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    record_date: Mapped[date | None] = mapped_column(Date)
    payment_date: Mapped[date | None] = mapped_column(Date)
    ratio: Mapped[str | None] = mapped_column(String(64))
    cash_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    description: Mapped[str | None] = mapped_column(Text)
    raw_payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Knowledge(Base):
    __tablename__ = "knowledge"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list | None] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(
        Enum("user", "auto", "external", name="knowledge_source_enum"),
        default="user",
        nullable=False,
    )
    chroma_id: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class WatchlistEntry(Base):
    __tablename__ = "watchlist_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    asset_class: Mapped[str] = mapped_column(
        Enum("vn_stock", "crypto", "commodity", "fx_index", name="asset_class_enum"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange: Mapped[str | None] = mapped_column(String(32))
    timeframes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    context_only: Mapped[bool] = mapped_column(default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class FetchLog(Base):
    __tablename__ = "fetch_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    asset_id: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(
        Enum("price", "news", "vn_flow", "corp_event", name="fetch_kind_enum"), nullable=False
    )
    timeframe: Mapped[str | None] = mapped_column(String(8))
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(
        Enum("ok", "partial", "error", name="fetch_status_enum"), nullable=False
    )
    rows_inserted: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)


class FundamentalSnapshotRow(Base):
    """Per-symbol fundamentals for one accounting period.

    Existing 4-ratio columns (roa/roe/pe/pb) drive the v1 micro engine.
    The columns added below back the v2 checklist engine — see
    `checklist_vi_mo_doanh_nghiep.md` sections 1.1–2.4.

    `status`:
        - active:      one row per (symbol, period); the most recent period is the working snapshot.
        - superseded:  older period kept for history (CAGR / YoY).
        - overridden:  manual analyst fix beats the auto-fetched row.

    Multiple `active` rows per symbol exist on purpose now (one per quarter,
    up to ~12). v2 scoring picks the newest by `period_end`; older rows feed
    growth-rate calculations.
    """
    __tablename__ = "fundamental_snapshots"
    __table_args__ = (
        UniqueConstraint("asset_symbol", "period", name="uk_fund_symbol_period"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    asset_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    roa: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    roe: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    pe: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    pb: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))

    # ---- v2 extension (checklist_vi_mo_doanh_nghiep.md) --------------------
    # Phần 1.1 — Income statement (currency units: same as vnstock raw, billions VND).
    revenue: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    gross_profit: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    net_profit: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    eps: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    # Phần 1.2 — Balance sheet.
    cash_and_equivalents: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    total_assets: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    total_debt: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    total_equity: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    inventory: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    receivables: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    current_assets: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    current_liabilities: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    # Phần 1.3 — Cash flow.
    cfo: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    capex: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    cff: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    fcf: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))  # derived: cfo - capex
    # Phần 2.1 — Valuation (pe/pb already above).
    ev_ebitda: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    ps: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    # Phần 2.2 — Profitability (roa/roe already above).
    roic: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    gross_margin: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    net_margin: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    # Phần 2.3 — Leverage & liquidity.
    de_ratio: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    current_ratio: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    quick_ratio: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    interest_coverage: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    # Phần 2.4 — Operating efficiency.
    inventory_days: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    receivable_days: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    ccc: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    # ------------------------------------------------------------------------

    industry_code: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="vnstock")
    status: Mapped[str] = mapped_column(
        Enum("active", "superseded", "overridden",
             name="fundamental_status_enum"),
        nullable=False, default="active",
    )
    raw_payload: Mapped[dict | None] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class IndustryAverageRow(Base):
    """Per-industry, per-period aggregate ratios. `n_companies` must be ≥5 for reliability.

    Extended in v2 with valuation (ev_ebitda, ps), profitability (roic,
    gross/net margin), and leverage (de_ratio) medians/averages — these are
    the ratios the checklist compares against industry.
    """
    __tablename__ = "industry_averages"
    __table_args__ = (
        UniqueConstraint("industry_code", "period", name="uk_industry_period"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    industry_code: Mapped[str] = mapped_column(String(64), nullable=False)
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    roa_avg: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    roa_median: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    roe_avg: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    roe_median: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    pe_avg: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    pe_median: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    pb_avg: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    pb_median: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))

    # ---- v2 extension ------------------------------------------------------
    ev_ebitda_avg: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    ev_ebitda_median: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    ps_avg: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    ps_median: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    roic_avg: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    roic_median: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    gross_margin_avg: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    gross_margin_median: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    net_margin_avg: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    net_margin_median: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    de_ratio_avg: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    de_ratio_median: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    # ------------------------------------------------------------------------

    n_companies: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="vnstock_screen")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
