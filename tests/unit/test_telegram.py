"""Unit tests cho notifier/telegram.py — TC-SIG-16/17/18."""
from __future__ import annotations

from datetime import datetime

from finance_bot.analysis.risk import RiskPlan
from finance_bot.analysis.signal import SignalDecision
from finance_bot.analysis.technical import TechSnapshot, Vote
from finance_bot.notifier.telegram import (
    build_callback_data,
    format_alert,
    parse_callback_data,
)


# ----------------------------------------------------------------------
# Callback data round-trip
# ----------------------------------------------------------------------
def test_build_callback_data_format():
    assert build_callback_data("enter", 42) == "act:enter:42"
    assert build_callback_data("skip", 99) == "act:skip:99"


def test_parse_callback_data_valid():
    """TC-SIG-17."""
    assert parse_callback_data("act:enter:42") == ("enter", 42)
    assert parse_callback_data("act:skip:99") == ("skip", 99)


def test_parse_callback_data_invalid_returns_none():
    """TC-SIG-18: handle gracefully — return None thay vì raise."""
    assert parse_callback_data("foo") is None
    assert parse_callback_data("act:enter") is None
    assert parse_callback_data("act:invalid:42") is None
    assert parse_callback_data("act:enter:abc") is None
    assert parse_callback_data("notact:enter:42") is None


def test_round_trip_build_then_parse():
    for action in ("enter", "skip"):
        for sid in (1, 42, 999_999):
            assert parse_callback_data(build_callback_data(action, sid)) == (action, sid)


# ----------------------------------------------------------------------
# format_alert (TC-SIG-16)
# ----------------------------------------------------------------------
def _build_decision(asset_cfg, side: str = "buy", with_risk: bool = True,
                    entry_window: str = "ato_next_session") -> SignalDecision:
    snap = TechSnapshot(
        last_close=125_300.0,
        atr_value=2_500.0,
        votes=[
            Vote("RSI14", "buy", 0.55),
            Vote("MACD", "buy", 0.8),
            Vote("EMA20/50", "buy", 0.85),
            Vote("EMA50/200", "buy", 0.4),
            Vote("BB20", "hold", 0.2),
            Vote("VOL", "buy", 0.6),
            Vote("ATR_BO", "hold", 0.1),
        ],
    )
    risk = None
    if with_risk:
        risk = RiskPlan(
            side=side, entry=125_300.0, stop_loss=119_800.0, take_profit=138_750.0,
            risk_per_share=5_500.0, reward_per_share=13_750.0, rr_ratio=2.5,
            sl_basis="atr",
        )
    return SignalDecision(
        asset=asset_cfg,
        timeframe="1d",
        ts=datetime(2026, 5, 2, 9, 0),
        side=side,
        tier="A",
        confidence=0.82,
        price_at_signal=125_300.0,
        snapshot=snap,
        risk=risk,
        entry_window=entry_window,
        expected_entry_at=datetime(2026, 5, 5, 2, 15),  # UTC for 09:15 ICT
        rationale=["test"],
    )


def test_format_alert_contains_required_fields(asset_fpt):
    """Alert text Vietnamese, có Tier A + side + symbol + giá + SL/TP."""
    decision = _build_decision(asset_fpt, side="buy")
    text = format_alert(decision)

    assert "TIER A" in text
    assert "MUA" in text
    assert "FPT" in text
    assert "Confidence:   0.82" in text
    assert "SL:" in text
    assert "TP:" in text
    assert "R:R 1:2.5" in text
    assert "ATO phiên kế tiếp" in text


def test_format_alert_sell_uses_ban_label(asset_fpt):
    decision = _build_decision(asset_fpt, side="sell")
    text = format_alert(decision)
    assert "BÁN" in text
    assert "MUA" not in text.split("\n")[0]


def test_format_alert_immediate_window_for_crypto(asset_btc):
    decision = _build_decision(asset_btc, side="buy", entry_window="immediate")
    text = format_alert(decision)
    assert "Khớp lệnh: ngay" in text
    assert "ATO phiên kế tiếp" not in text


def test_format_alert_no_risk_block_when_risk_is_none(asset_fpt):
    """Hold signals don't have risk plan — alert should not crash."""
    decision = _build_decision(asset_fpt, side="buy", with_risk=False)
    text = format_alert(decision)
    assert "SL:" not in text
    assert "FPT" in text
