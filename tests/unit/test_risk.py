"""Unit tests cho analysis/risk.py — TC-SIG-08, TC-SIG-09."""
from __future__ import annotations

import pandas as pd

from finance_bot.analysis.risk import plan_for
from finance_bot.settings import RiskConfig


def _df_with_swing(swing_low: float, swing_high: float, n: int = 30) -> pd.DataFrame:
    """Build a tiny df where last 20 bars contain known swing low/high."""
    lows = [swing_low + 0.5 * i for i in range(n)]
    highs = [h + 5 for h in lows]
    closes = [(l + h) / 2 for l, h in zip(lows, highs)]
    # Ép swing_low ở vị trí cuối lookback range để _recent_swing_low pick up
    lows[-5] = swing_low
    highs[-5] = swing_high
    return pd.DataFrame({
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [100_000.0] * n,
    })


def test_buy_atr_only_when_swing_disabled():
    """TC-SIG-08: SL = entry - 2*ATR; TP = entry + 2.5 * (entry - SL)."""
    cfg = RiskConfig(stop_loss_atr_mult=2.0, take_profit_rr=2.5,
                     use_recent_swing_for_sr=False)
    # Use empty DataFrame — swing helpers will return None anyway.
    df = pd.DataFrame({"open": [], "high": [], "low": [], "close": [], "volume": []})

    plan = plan_for(side="buy", entry=100.0, df_1d=df, atr_value=2.0, cfg=cfg)
    assert plan.side == "buy"
    assert plan.stop_loss == 96.0           # 100 - 2*2
    assert plan.take_profit == 110.0        # 100 + 2.5 * 4
    assert plan.risk_per_share == 4.0
    assert plan.reward_per_share == 10.0
    assert plan.rr_ratio == 2.5
    assert plan.sl_basis == "atr"


def test_sell_atr_only_when_swing_disabled():
    cfg = RiskConfig(stop_loss_atr_mult=2.0, take_profit_rr=2.5,
                     use_recent_swing_for_sr=False)
    df = pd.DataFrame({"open": [], "high": [], "low": [], "close": [], "volume": []})

    plan = plan_for(side="sell", entry=100.0, df_1d=df, atr_value=2.0, cfg=cfg)
    assert plan.stop_loss == 104.0          # 100 + 2*2
    assert plan.take_profit == 90.0         # 100 - 2.5 * 4
    assert plan.sl_basis == "atr"


def test_buy_uses_swing_when_tighter_than_atr():
    """TC-SIG-09: swing_low between (atr_stop, entry) → use swing*0.995."""
    cfg = RiskConfig(stop_loss_atr_mult=2.0, take_profit_rr=2.5,
                     use_recent_swing_for_sr=True)
    # entry=100, atr=2 → atr_stop = 96. Swing low = 98 → trong khoảng (96, 100), nên dùng.
    df = _df_with_swing(swing_low=98.0, swing_high=120.0)

    plan = plan_for(side="buy", entry=100.0, df_1d=df, atr_value=2.0, cfg=cfg)
    assert plan.sl_basis == "swing"
    # SL = 98 * 0.995 = 97.51
    assert abs(plan.stop_loss - 97.51) < 1e-6


def test_buy_falls_back_to_atr_when_swing_below_atr_stop():
    """Swing below atr_stop → atr safer; keep atr."""
    cfg = RiskConfig(stop_loss_atr_mult=2.0, take_profit_rr=2.5,
                     use_recent_swing_for_sr=True)
    df = _df_with_swing(swing_low=90.0, swing_high=120.0)

    plan = plan_for(side="buy", entry=100.0, df_1d=df, atr_value=2.0, cfg=cfg)
    assert plan.sl_basis == "atr"
    assert plan.stop_loss == 96.0


def test_rr_ratio_propagates_to_plan():
    """RR ratio configurable end-to-end."""
    cfg = RiskConfig(stop_loss_atr_mult=2.0, take_profit_rr=3.0,
                     use_recent_swing_for_sr=False)
    df = pd.DataFrame({"open": [], "high": [], "low": [], "close": [], "volume": []})

    plan = plan_for(side="buy", entry=100.0, df_1d=df, atr_value=2.0, cfg=cfg)
    assert plan.rr_ratio == 3.0
    assert plan.take_profit == 112.0    # 100 + 3.0 * 4
