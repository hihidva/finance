"""Shared fixtures cho unit tests.

Mọi external (MySQL, Claude CLI, Telegram, ChromaDB) đều phải được mock ở từng test.
Conftest cung cấp:
  - autouse cache_clear cho lru_cache trong settings
  - asset config fixtures (FPT, BTC, DXY context-only)
  - sample OHLCV DataFrame builder
  - minimal Watchlist instance
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from finance_bot.settings import (
    AssetConfig,
    NewsSourceConfig,
    RiskConfig,
    ScheduleConfig,
    SignalConfig,
    Watchlist,
    get_settings,
    get_watchlist,
)


@pytest.fixture(autouse=True)
def _clear_lru_cache():
    """Reset cached settings/watchlist between tests so monkeypatch.setenv works."""
    get_settings.cache_clear()
    get_watchlist.cache_clear()
    yield
    get_settings.cache_clear()
    get_watchlist.cache_clear()


# ----------------------------------------------------------------------
# Asset configs
# ----------------------------------------------------------------------
@pytest.fixture
def asset_fpt() -> AssetConfig:
    return AssetConfig(
        symbol="FPT",
        name="FPT Corporation",
        asset_class="vn_stock",
        source="vnstock",
        timeframes=["1d"],
        context_only=False,
    )


@pytest.fixture
def asset_btc() -> AssetConfig:
    return AssetConfig(
        symbol="BTC/USDT",
        name="Bitcoin",
        asset_class="crypto",
        source="ccxt",
        exchange="binance",
        timeframes=["1d"],
        context_only=False,
    )


@pytest.fixture
def asset_dxy() -> AssetConfig:
    return AssetConfig(
        symbol="DX-Y.NYB",
        name="US Dollar Index",
        asset_class="fx_index",
        source="yfinance",
        timeframes=["1d"],
        context_only=True,
    )


# ----------------------------------------------------------------------
# Minimal watchlist
# ----------------------------------------------------------------------
@pytest.fixture
def watchlist(asset_fpt, asset_btc, asset_dxy) -> Watchlist:
    return Watchlist(
        assets=[asset_fpt, asset_btc, asset_dxy],
        news_sources=[
            NewsSourceConfig(name="TestFeed", url="https://example.com/rss",
                             lang="vi", tags=["vn"]),
        ],
        signal=SignalConfig(),
        risk=RiskConfig(),
        schedule=ScheduleConfig(),
    )


# ----------------------------------------------------------------------
# OHLCV DataFrame builder
# ----------------------------------------------------------------------
def _make_ohlcv(
    n: int,
    seed: int = 42,
    base_price: float = 100.0,
    drift: float = 0.0,
    volatility: float = 1.0,
) -> pd.DataFrame:
    """Generate synthetic OHLCV df sorted ascending by ts. Index = DatetimeIndex (UTC)."""
    rng = np.random.default_rng(seed)
    ts_index = pd.date_range(end=datetime(2026, 4, 30), periods=n, freq="D")
    closes = base_price + np.cumsum(rng.normal(drift, volatility, size=n))
    highs = closes + np.abs(rng.normal(0, volatility * 0.5, size=n))
    lows = closes - np.abs(rng.normal(0, volatility * 0.5, size=n))
    opens = closes + rng.normal(0, volatility * 0.3, size=n)
    volumes = rng.integers(50_000, 500_000, size=n).astype(float)
    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=ts_index,
    )
    return df


@pytest.fixture
def ohlcv_uptrend() -> pd.DataFrame:
    """100 bars with positive drift → buy bias likely."""
    return _make_ohlcv(n=120, seed=1, base_price=100, drift=0.5, volatility=1.0)


@pytest.fixture
def ohlcv_downtrend() -> pd.DataFrame:
    return _make_ohlcv(n=120, seed=2, base_price=100, drift=-0.5, volatility=1.0)


@pytest.fixture
def ohlcv_sideways() -> pd.DataFrame:
    return _make_ohlcv(n=120, seed=3, base_price=100, drift=0.0, volatility=0.3)


@pytest.fixture
def ohlcv_short() -> pd.DataFrame:
    """Only 30 bars — below the 60-bar warmup minimum."""
    return _make_ohlcv(n=30, seed=4, base_price=100)
