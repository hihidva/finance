"""Unit tests cho analysis/signal.py — TC-SIG-04..07."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from finance_bot.analysis.signal import _next_vn_ato_at, analyze


# ----------------------------------------------------------------------
# _next_vn_ato_at (TC-SIG-06, TC-SIG-07)
# ----------------------------------------------------------------------
def _utc_from_vn(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Return UTC naive datetime for a wall-clock time in Asia/Ho_Chi_Minh."""
    local = datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    return local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _next_ato_in_vn(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Helper: pass VN wall-clock now, return result converted back to VN wall-clock."""
    now_utc = _utc_from_vn(year, month, day, hour, minute)
    result_utc = _next_vn_ato_at(now_utc)
    return (result_utc.replace(tzinfo=ZoneInfo("UTC"))
            .astimezone(ZoneInfo("Asia/Ho_Chi_Minh")))


def test_next_ato_after_close_returns_next_business_day():
    """TC-SIG-06: Friday 16:00 ICT → next Monday 09:15 ICT."""
    # 2026-05-01 is Friday.
    result_local = _next_ato_in_vn(2026, 5, 1, 16, 0)
    assert result_local.weekday() == 0  # Monday
    assert result_local.hour == 9
    assert result_local.minute == 15
    # Date exactly 3 days after Friday May 1.
    assert result_local.date() == datetime(2026, 5, 4).date()


def test_next_ato_morning_before_open_returns_same_day():
    """TC-SIG-07: Tuesday 06:00 ICT → cùng ngày 09:15 ICT."""
    # 2026-05-05 is Tuesday.
    result_local = _next_ato_in_vn(2026, 5, 5, 6, 0)
    assert result_local.date() == datetime(2026, 5, 5).date()
    assert (result_local.hour, result_local.minute) == (9, 15)


def test_next_ato_at_open_time_skips_to_next_day():
    """09:15 sharp counts as already opened → next business day."""
    # 2026-05-05 Tuesday at 09:15.
    result_local = _next_ato_in_vn(2026, 5, 5, 9, 15)
    assert result_local.date() == datetime(2026, 5, 6).date()  # Wednesday


def test_next_ato_saturday_skips_weekend():
    # 2026-05-02 is Saturday.
    result_local = _next_ato_in_vn(2026, 5, 2, 10, 0)
    assert result_local.weekday() == 0  # Monday
    assert result_local.date() == datetime(2026, 5, 4).date()


def test_next_ato_sunday_returns_monday():
    # 2026-05-03 is Sunday.
    result_local = _next_ato_in_vn(2026, 5, 3, 12, 0)
    assert result_local.weekday() == 0
    assert result_local.date() == datetime(2026, 5, 4).date()


# ----------------------------------------------------------------------
# analyze() public surface (TC-SIG-04, TC-SIG-05)
# ----------------------------------------------------------------------
def test_analyze_returns_decision_with_required_fields(asset_fpt, watchlist, ohlcv_uptrend):
    decision = analyze(asset_fpt, ohlcv_uptrend, watchlist)
    assert decision.asset is asset_fpt
    assert decision.timeframe == "1d"
    assert decision.tier in {"A", "B", "C"}
    assert decision.side in {"buy", "sell", "hold"}
    assert 0.0 <= decision.confidence <= 1.0
    assert decision.snapshot is not None
    # vn_stock → entry_window là ATO next session
    assert decision.entry_window == "ato_next_session"
    assert decision.expected_entry_at is not None


def test_analyze_crypto_uses_immediate_entry_window(asset_btc, watchlist, ohlcv_uptrend):
    decision = analyze(asset_btc, ohlcv_uptrend, watchlist)
    assert decision.entry_window == "immediate"


def test_analyze_context_only_caps_at_tier_c(asset_dxy, watchlist, ohlcv_uptrend):
    """TC-SIG-04 inverse: context_only KHÔNG bao giờ vượt Tier C."""
    decision = analyze(asset_dxy, ohlcv_uptrend, watchlist)
    assert decision.tier == "C"
    assert any("context_only" in r for r in decision.rationale)


def test_analyze_news_against_demotes_tier_a_to_b(
    asset_fpt, watchlist, ohlcv_uptrend, monkeypatch
):
    """Khi có technicals đủ Tier A nhưng news_against=True → tier hạ B."""
    # Force technicals tốt bằng cách mock compute_snapshot trả votes mạnh.
    from finance_bot.analysis import signal as signal_mod
    from finance_bot.analysis.technical import TechSnapshot, Vote

    fake_snap = TechSnapshot(
        last_close=125.0,
        atr_value=2.0,
        votes=[
            Vote("RSI14", "buy", 0.8),
            Vote("MACD", "buy", 0.85),
            Vote("EMA20/50", "buy", 0.9),
            Vote("EMA50/200", "buy", 0.7),
            Vote("BB20", "buy", 0.7),
            Vote("VOL", "hold", 0.2),
            Vote("ATR_BO", "hold", 0.1),
        ],
    )
    monkeypatch.setattr(signal_mod, "compute_snapshot", lambda df: fake_snap)

    a = analyze(asset_fpt, ohlcv_uptrend, watchlist, news_against=False)
    b = analyze(asset_fpt, ohlcv_uptrend, watchlist, news_against=True)

    assert a.tier == "A"
    assert b.tier == "B"
    assert any("tin tức ngược chiều" in r for r in b.rationale)


def test_analyze_holds_when_no_indicator_agreement(
    asset_fpt, watchlist, ohlcv_uptrend, monkeypatch
):
    """TC-SIG-05: tất cả indicator hold → Tier C, side=hold."""
    from finance_bot.analysis import signal as signal_mod
    from finance_bot.analysis.technical import TechSnapshot, Vote

    fake_snap = TechSnapshot(
        last_close=100.0, atr_value=1.0,
        votes=[Vote(f"i{i}", "hold", 0.1) for i in range(7)],
    )
    monkeypatch.setattr(signal_mod, "compute_snapshot", lambda df: fake_snap)

    decision = analyze(asset_fpt, ohlcv_uptrend, watchlist)
    assert decision.tier == "C"
    assert decision.side == "hold"
    assert decision.risk is None


def test_tier_a_signal_attaches_risk_plan(
    asset_fpt, watchlist, ohlcv_uptrend, monkeypatch
):
    """Khi achieve Tier A buy, decision.risk phải có RiskPlan."""
    from finance_bot.analysis import signal as signal_mod
    from finance_bot.analysis.technical import TechSnapshot, Vote

    fake_snap = TechSnapshot(
        last_close=100.0, atr_value=2.0,
        votes=[
            Vote("RSI14", "buy", 0.85),
            Vote("MACD", "buy", 0.85),
            Vote("EMA20/50", "buy", 0.85),
            Vote("EMA50/200", "buy", 0.85),
            Vote("BB20", "buy", 0.85),
            Vote("VOL", "hold", 0.2),
            Vote("ATR_BO", "hold", 0.1),
        ],
    )
    monkeypatch.setattr(signal_mod, "compute_snapshot", lambda df: fake_snap)

    decision = analyze(asset_fpt, ohlcv_uptrend, watchlist)
    assert decision.tier == "A"
    assert decision.side == "buy"
    assert decision.risk is not None
    assert decision.risk.entry == 100.0
    assert decision.risk.rr_ratio == 2.5
