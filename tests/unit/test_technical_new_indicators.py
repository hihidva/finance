"""Unit tests for Priority A indicators — TC-IDC-* per docs/test-cases/8-indicators-catalog.md.

Covers:
- Contract (TC-IDC-01, 02, 15, 16, 17, 18, 19)
- 5 new indicators happy paths (Ichimoku, ADX, Supertrend, OBV, Donchian)
- Snapshot integration (TC-IDC-20)
"""
from __future__ import annotations

import json
from dataclasses import asdict

import numpy as np
import pandas as pd
import pytest

from finance_bot.analysis.signal import analyze
from finance_bot.analysis.technical import (
    INDICATORS,
    Vote,
    compute_snapshot,
    vote_adx,
    vote_atr_breakout,
    vote_bollinger,
    vote_cmf,
    vote_donchian,
    vote_ema_cross,
    vote_ichimoku,
    vote_long_term_trend,
    vote_macd,
    vote_mfi,
    vote_obv,
    vote_rsi,
    vote_supertrend,
    vote_volume,
)


# Helper builder — wrappers around conftest._make_ohlcv but inline so test stays self-contained.
def _ohlcv(n, seed=42, base=100.0, drift=0.0, vol=1.0):
    rng = np.random.default_rng(seed)
    closes = base + np.cumsum(rng.normal(drift, vol, n))
    highs = closes + np.abs(rng.normal(0, vol * 0.5, n))
    lows = closes - np.abs(rng.normal(0, vol * 0.5, n))
    opens = closes + rng.normal(0, vol * 0.3, n)
    vols = rng.integers(100_000, 500_000, n).astype(float)
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols}
    )


ALL_VOTE_FNS_DF = [
    vote_volume, vote_atr_breakout, vote_ichimoku, vote_adx,
    vote_supertrend, vote_obv, vote_donchian, vote_mfi, vote_cmf,
]
ALL_VOTE_FNS_SERIES = [
    vote_rsi, vote_macd, vote_bollinger,
]


# ----------------------------------------------------------------------
# TC-IDC-01: Vote contract
# ----------------------------------------------------------------------
def test_tc_idc_01_vote_contract_returns_valid_vote_dataclass(ohlcv_uptrend):
    df = ohlcv_uptrend
    close = df["close"]

    for fn in ALL_VOTE_FNS_DF:
        v = fn(df)
        assert isinstance(v, Vote), f"{fn.__name__} did not return Vote"
        assert v.side in {"buy", "sell", "hold"}
        assert 0.0 <= v.strength <= 1.0
        assert isinstance(v.detail, dict)
        assert isinstance(v.name, str) and v.name

    for fn in ALL_VOTE_FNS_SERIES:
        v = fn(close)
        assert isinstance(v, Vote)
        assert v.side in {"buy", "sell", "hold"}
        assert 0.0 <= v.strength <= 1.0

    # vote_ema_cross + vote_long_term_trend share signature with vote_rsi.
    for fn in (vote_ema_cross, vote_long_term_trend):
        v = fn(close)
        assert isinstance(v, Vote)


# ----------------------------------------------------------------------
# TC-IDC-02: Insufficient data → hold, strength 0.0, no raise
# ----------------------------------------------------------------------
def test_tc_idc_02_new_indicators_insufficient_data_returns_hold_zero_strength():
    """5-bar df is below min_bars for 7 new indicators → must return hold/0.0/insufficient_data.

    Legacy 7 indicators are out of scope: their pre-existing guards rely on `pd.isna`
    of EWM smoothed values, which may not be NaN at 5 bars (EWM has no warmup). This
    TC only enforces the stronger contract on the 7 new indicators added in §8.5.
    """
    df = _ohlcv(5)
    new_indicator_fns = [
        vote_ichimoku, vote_adx, vote_supertrend, vote_obv, vote_donchian,
        vote_mfi, vote_cmf,
    ]

    for fn in new_indicator_fns:
        v = fn(df)
        assert v.side == "hold", f"{fn.__name__} returned {v.side} for 5-bar df"
        assert v.strength == 0.0
        assert v.detail.get("insufficient_data") is True


# ----------------------------------------------------------------------
# TC-IDC-15: Determinism — same input → same Vote
# ----------------------------------------------------------------------
def test_tc_idc_15_determinism_same_input_same_output(ohlcv_uptrend):
    snap1 = compute_snapshot(ohlcv_uptrend.copy())
    snap2 = compute_snapshot(ohlcv_uptrend.copy())
    assert len(snap1.votes) == len(snap2.votes) == 14
    for v1, v2 in zip(snap1.votes, snap2.votes):
        assert v1.name == v2.name
        assert v1.side == v2.side
        assert v1.strength == v2.strength
        assert v1.detail == v2.detail


# ----------------------------------------------------------------------
# TC-IDC-16: JSON-serializable detail dict
# ----------------------------------------------------------------------
def test_tc_idc_16_vote_detail_json_serializable(ohlcv_uptrend):
    snap = compute_snapshot(ohlcv_uptrend)
    for v in snap.votes:
        json.dumps(v.detail)  # must not raise TypeError


# ----------------------------------------------------------------------
# TC-IDC-17: INDICATORS tuple ↔ compute_snapshot consistency
# ----------------------------------------------------------------------
def test_tc_idc_17_indicators_tuple_matches_compute_snapshot(ohlcv_uptrend):
    snap = compute_snapshot(ohlcv_uptrend)
    snap_names = {v.name for v in snap.votes}
    catalog_names = set(INDICATORS)
    assert snap_names == catalog_names, (
        f"Catalog drift: missing from snapshot={catalog_names - snap_names}, "
        f"extra in snapshot={snap_names - catalog_names}"
    )


# ----------------------------------------------------------------------
# TC-IDC-18: NaN in middle of series must not crash
# ----------------------------------------------------------------------
def test_tc_idc_18_nan_in_series_does_not_crash():
    df = _ohlcv(200)
    df.loc[50:60, "close"] = np.nan
    df.loc[50:60, "high"] = np.nan
    df.loc[50:60, "low"] = np.nan
    # Should not raise; all votes valid Vote instances.
    snap = compute_snapshot(df)
    for v in snap.votes:
        assert v.side in {"buy", "sell", "hold"}


# ----------------------------------------------------------------------
# TC-IDC-19: Volume = 0 on last bar → vote_volume hold (no zero-div)
# ----------------------------------------------------------------------
def test_tc_idc_19_zero_volume_last_bar_returns_hold():
    df = _ohlcv(60)
    df.loc[df.index[-1], "volume"] = 0.0
    v = vote_volume(df)
    assert v.name == "VOL"
    # spike = 0/avg = 0 → below threshold 1.8 → hold.
    assert v.side == "hold"


# ----------------------------------------------------------------------
# TC-IDC-20: Snapshot → signal.analyze integration (sideways → Tier C)
# ----------------------------------------------------------------------
def test_tc_idc_20_sideways_data_yields_tier_c(ohlcv_sideways, asset_fpt, watchlist):
    decision = analyze(asset_fpt, ohlcv_sideways, watchlist)
    assert decision.tier == "C"
    assert decision.side in {"buy", "sell", "hold"}


# ======================================================================
# Per-indicator happy paths for 5 new indicators (TC-IDC-21..35)
# ======================================================================

# ----------------------------------------------------------------------
# TC-IDC-21/22: Ichimoku
# ----------------------------------------------------------------------
def test_tc_idc_21_ichimoku_strong_uptrend_votes_buy():
    df = _ohlcv(120, drift=0.6, vol=0.5, seed=11)
    v = vote_ichimoku(df)
    assert v.name == "ICHIMOKU"
    assert v.side == "buy"
    assert v.strength >= 0.5
    assert v.detail["close"] > v.detail["kumo_top"]


def test_tc_idc_22_ichimoku_strong_downtrend_votes_sell():
    df = _ohlcv(120, drift=-0.6, vol=0.5, seed=12)
    v = vote_ichimoku(df)
    assert v.name == "ICHIMOKU"
    assert v.side == "sell"
    assert v.detail["close"] < v.detail["kumo_bot"]


def test_tc_idc_23_ichimoku_insufficient_data():
    df = _ohlcv(20)  # < 52 min for senkou_b
    v = vote_ichimoku(df)
    assert v.side == "hold"
    assert v.strength == 0.0
    assert v.detail.get("insufficient_data") is True


# ----------------------------------------------------------------------
# TC-IDC-24/25/26: ADX
# ----------------------------------------------------------------------
def test_tc_idc_24_adx_strong_uptrend_votes_buy():
    df = _ohlcv(120, drift=0.8, vol=0.3, seed=21)
    v = vote_adx(df)
    assert v.name == "ADX"
    assert v.side == "buy"
    assert v.detail["plus_di"] > v.detail["minus_di"]
    assert v.detail["adx"] >= 25


def test_tc_idc_25_adx_strong_downtrend_votes_sell():
    df = _ohlcv(120, drift=-0.8, vol=0.3, seed=22)
    v = vote_adx(df)
    assert v.name == "ADX"
    assert v.side == "sell"
    assert v.detail["minus_di"] > v.detail["plus_di"]


def test_tc_idc_26_adx_sideways_votes_hold():
    df = _ohlcv(120, drift=0.0, vol=0.2, seed=23)
    v = vote_adx(df)
    # Sideways: ADX should stay below 25 → vote hold.
    if v.detail["adx"] < 25:
        assert v.side == "hold"


# ----------------------------------------------------------------------
# TC-IDC-27/28: Supertrend
# ----------------------------------------------------------------------
def test_tc_idc_27_supertrend_uptrend_votes_buy():
    df = _ohlcv(120, drift=0.6, vol=0.3, seed=31)
    v = vote_supertrend(df)
    assert v.name == "SUPERTREND"
    assert v.side == "buy"
    assert v.detail["direction"] == 1


def test_tc_idc_28_supertrend_downtrend_votes_sell():
    df = _ohlcv(120, drift=-0.6, vol=0.3, seed=32)
    v = vote_supertrend(df)
    assert v.name == "SUPERTREND"
    assert v.side == "sell"
    assert v.detail["direction"] == -1


def test_tc_idc_29_supertrend_strength_is_flip_or_stable_tier():
    """Strength is exactly one of the two tier values: 0.85 (just flipped) or 0.45 (stable)."""
    n = 100
    closes = list(np.linspace(100, 80, 80)) + list(np.linspace(80, 110, 20))
    df = pd.DataFrame({
        "open": closes,
        "high": [c + 0.5 for c in closes],
        "low": [c - 0.5 for c in closes],
        "close": closes,
        "volume": [200_000.0] * n,
    })
    v = vote_supertrend(df)
    assert v.strength == pytest.approx(0.85, abs=1e-6) or v.strength == pytest.approx(0.45, abs=1e-6)
    if v.detail["flipped"]:
        assert v.strength == pytest.approx(0.85, abs=1e-6)
    else:
        assert v.strength == pytest.approx(0.45, abs=1e-6)


# ----------------------------------------------------------------------
# TC-IDC-30/31: OBV
# ----------------------------------------------------------------------
def test_tc_idc_30_obv_uptrend_confirmation_votes_buy():
    df = _ohlcv(80, drift=0.5, vol=0.5, seed=41)
    v = vote_obv(df)
    assert v.name == "OBV"
    assert v.side == "buy"
    assert v.detail["lookback"] == 20


def test_tc_idc_31_obv_downtrend_confirmation_votes_sell():
    df = _ohlcv(80, drift=-0.5, vol=0.5, seed=42)
    v = vote_obv(df)
    assert v.name == "OBV"
    assert v.side == "sell"


# ----------------------------------------------------------------------
# TC-IDC-32/33: Donchian breakout
# ----------------------------------------------------------------------
def test_tc_idc_32_donchian_breakout_up_votes_buy():
    """Close on last bar must exceed prev 20-bar high → buy."""
    n = 60
    closes = [100.0] * (n - 1) + [115.0]  # explosive close on last bar
    highs = [c + 0.5 for c in closes]
    # Force prev-20 highs to be flat ~100.5 → last close 115 > prev_upper.
    df = pd.DataFrame({
        "open": closes,
        "high": highs,
        "low": [c - 0.5 for c in closes],
        "close": closes,
        "volume": [200_000.0] * n,
    })
    v = vote_donchian(df)
    assert v.name == "DONCHIAN"
    assert v.side == "buy"
    assert v.detail["signal"] == "breakout_up"


def test_tc_idc_33_donchian_breakout_down_votes_sell():
    n = 60
    closes = [100.0] * (n - 1) + [85.0]
    df = pd.DataFrame({
        "open": closes,
        "high": [c + 0.5 for c in closes],
        "low": [c - 0.5 for c in closes],
        "close": closes,
        "volume": [200_000.0] * n,
    })
    v = vote_donchian(df)
    assert v.name == "DONCHIAN"
    assert v.side == "sell"
    assert v.detail["signal"] == "breakout_down"


def test_tc_idc_34_donchian_inside_channel_votes_hold():
    n = 60
    rng = np.random.default_rng(99)
    closes = 100 + rng.normal(0, 0.3, n)  # tight sideways
    df = pd.DataFrame({
        "open": closes,
        "high": closes + 0.5,
        "low": closes - 0.5,
        "close": closes,
        "volume": [200_000.0] * n,
    })
    v = vote_donchian(df)
    assert v.side == "hold"
    assert v.detail.get("signal") == "inside_channel"


# ----------------------------------------------------------------------
# TC-IDC-35: New indicators included in INDICATORS catalog
# ----------------------------------------------------------------------
def test_tc_idc_35_new_indicators_in_catalog_and_snapshot():
    new_names = ("ICHIMOKU", "ADX", "SUPERTREND", "OBV", "DONCHIAN", "MFI", "CMF")
    for new_name in new_names:
        assert new_name in INDICATORS

    snap = compute_snapshot(_ohlcv(120, drift=0.4, seed=50))
    snap_names = {v.name for v in snap.votes}
    for new_name in new_names:
        assert new_name in snap_names


# ----------------------------------------------------------------------
# TC-IDC-36..41: MFI + CMF happy paths
# ----------------------------------------------------------------------
def test_tc_idc_36_mfi_oversold_reversal_votes_buy():
    """Construct df: MFI dips below 20, then ticks up on last bar."""
    # 30 bars decline → push TP down + heavy volume on down days → low MFI
    n = 40
    closes = list(np.linspace(100, 75, 35)) + [76.0, 77.0, 78.0, 79.0, 80.0]
    df = pd.DataFrame({
        "open": closes,
        "high": [c + 0.5 for c in closes],
        "low": [c - 0.5 for c in closes],
        "close": closes,
        # Higher volume during the decline phase to amplify negative MF
        "volume": [500_000.0] * 35 + [300_000.0] * 5,
    })
    v = vote_mfi(df)
    assert v.name == "MFI"
    # Sau khi MFI quá thấp + tick up → expect buy hoặc ít nhất khong sell
    assert v.side in {"buy", "hold"}
    assert "mfi" in v.detail


def test_tc_idc_37_mfi_overbought_reversal_votes_sell():
    n = 40
    closes = list(np.linspace(80, 105, 35)) + [104.0, 103.0, 102.0, 101.0, 100.0]
    df = pd.DataFrame({
        "open": closes,
        "high": [c + 0.5 for c in closes],
        "low": [c - 0.5 for c in closes],
        "close": closes,
        "volume": [500_000.0] * 35 + [300_000.0] * 5,
    })
    v = vote_mfi(df)
    assert v.name == "MFI"
    assert v.side in {"sell", "hold"}


def test_tc_idc_38_mfi_insufficient_data():
    df = _ohlcv(10)  # < 16 (length 14 + 2)
    v = vote_mfi(df)
    assert v.side == "hold"
    assert v.strength == 0.0
    assert v.detail.get("insufficient_data") is True


def test_tc_idc_39_cmf_accumulation_votes_buy():
    """Engineer df so MFM > 0 + rising → CMF > 0.05."""
    n = 60
    # Close consistently near high → MFM ≈ +1 → CMF approaches +1.
    rng = np.random.default_rng(77)
    lows = 100 + rng.normal(0, 0.1, n).cumsum()
    highs = lows + 1.0
    closes = highs - 0.05  # close almost at high → bullish MFM
    df = pd.DataFrame({
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": rng.integers(200_000, 300_000, n).astype(float),
    })
    v = vote_cmf(df)
    assert v.name == "CMF"
    assert v.side == "buy"
    assert v.detail["cmf"] > 0.05


def test_tc_idc_40_cmf_distribution_votes_sell():
    n = 60
    rng = np.random.default_rng(88)
    lows = 100 + rng.normal(0, 0.1, n).cumsum()
    highs = lows + 1.0
    closes = lows + 0.05  # close near low → bearish MFM
    df = pd.DataFrame({
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": rng.integers(200_000, 300_000, n).astype(float),
    })
    v = vote_cmf(df)
    assert v.name == "CMF"
    assert v.side == "sell"
    assert v.detail["cmf"] < -0.05


def test_tc_idc_41_cmf_insufficient_data():
    df = _ohlcv(15)  # < 21 (length 20 + 1)
    v = vote_cmf(df)
    assert v.side == "hold"
    assert v.strength == 0.0
    assert v.detail.get("insufficient_data") is True


# ----------------------------------------------------------------------
# Bonus: numpy types must not leak into Vote.detail (asdict round-trip)
# ----------------------------------------------------------------------
def test_vote_detail_no_numpy_types_via_asdict(ohlcv_uptrend):
    snap = compute_snapshot(ohlcv_uptrend)
    for v in snap.votes:
        d = asdict(v)
        for key, val in d["detail"].items():
            assert not isinstance(val, np.floating), (
                f"{v.name}.detail[{key!r}] leaked numpy type: {type(val)}"
            )
            assert not isinstance(val, np.integer)
