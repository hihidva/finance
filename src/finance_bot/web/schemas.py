"""Pydantic request/response schemas for the dashboard API."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AssetClass = Literal["vn_stock", "crypto", "commodity", "fx_index"]
SourceName = Literal["vnstock", "ccxt", "yfinance"]


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------
class WatchlistEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    name: str
    asset_class: AssetClass
    source: SourceName
    exchange: str | None = None
    timeframes: list[str]
    context_only: bool
    is_active: bool
    note: str | None = None
    created_at: datetime
    updated_at: datetime


class WatchlistEntryCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=32, pattern=r"^[A-Z0-9./\-]{1,32}$")
    name: str = Field(min_length=1, max_length=128)
    asset_class: AssetClass
    source: SourceName
    exchange: str | None = Field(default=None, max_length=32)
    timeframes: list[str] = Field(default_factory=lambda: ["1d"])
    context_only: bool = False
    is_active: bool = True
    note: str | None = None


class WatchlistEntryPatch(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    asset_class: AssetClass | None = None
    source: SourceName | None = None
    exchange: str | None = Field(default=None, max_length=32)
    timeframes: list[str] | None = None
    context_only: bool | None = None
    is_active: bool | None = None
    note: str | None = None


# ---------------------------------------------------------------------------
# Prices & indicators
# ---------------------------------------------------------------------------
class Candle(BaseModel):
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class PricesResponse(BaseModel):
    symbol: str
    asset_class: AssetClass
    timeframe: str
    candles: list[Candle]


class IndicatorSeries(BaseModel):
    name: str
    values: list[tuple[datetime, float | None]]  # (ts, value); None when not enough lookback


class IndicatorsResponse(BaseModel):
    symbol: str
    timeframe: str
    series: list[IndicatorSeries]
    available: list[str]


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------
class OutcomeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    horizon_hours: int
    evaluated_at: datetime
    price_then: float
    pnl_pct: float
    hit_target: bool
    max_favorable: float | None = None
    max_adverse: float | None = None


class SignalListItem(BaseModel):
    id: int
    asset_id: int
    symbol: str
    ts: datetime
    side: Literal["buy", "sell", "hold"]
    tier: Literal["A", "B", "C"]
    confidence: float
    price_at_signal: float
    entry_window: Literal["immediate", "ato_next_session"]
    expected_entry_at: datetime | None
    stop_loss: float | None
    take_profit: float | None
    notified: bool
    user_decision: Literal["entered", "skipped"] | None
    indicators_summary: str  # e.g. "4/7 buy"
    pnl_1d: float | None
    pnl_3d: float | None
    pnl_7d: float | None
    pnl_30d: float | None


class SignalListResponse(BaseModel):
    items: list[SignalListItem]
    total: int
    page: int
    page_size: int


class SignalDetail(BaseModel):
    id: int
    asset_id: int
    symbol: str
    asset_name: str
    asset_class: AssetClass
    ts: datetime
    side: Literal["buy", "sell", "hold"]
    tier: Literal["A", "B", "C"]
    confidence: float
    price_at_signal: float
    entry_window: Literal["immediate", "ato_next_session"]
    expected_entry_at: datetime | None
    stop_loss: float | None
    take_profit: float | None
    indicators: dict
    news_context: dict | None
    rag_context: dict | None
    llm_model: str | None
    llm_reasoning: str | None
    notified: bool
    notified_at: datetime | None
    user_decision: Literal["entered", "skipped"] | None
    user_decision_at: datetime | None
    outcomes: list[OutcomeOut]


class UserDecisionPatch(BaseModel):
    decision: Literal["entered", "skipped"] | None


# ---------------------------------------------------------------------------
# Health / meta
# ---------------------------------------------------------------------------
class HealthResponse(BaseModel):
    db: bool
    llm: bool
    llm_model: str
    watchlist_count: int
