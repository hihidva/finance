"""Stop-loss / take-profit calculator.

Logic chốt với user:
  SL = entry ± atr_mult * ATR
  TP = entry ± (atr_mult * ATR) * R:R       (R:R default = 2.5)

Nếu `use_recent_swing_for_sr=True` và swing hợp lý gần entry hơn ATR-stop, dùng swing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from finance_bot.settings import RiskConfig

Side = Literal["buy", "sell"]


@dataclass
class RiskPlan:
    side: Side
    entry: float
    stop_loss: float
    take_profit: float
    risk_per_share: float       # |entry - stop|
    reward_per_share: float     # |tp - entry|
    rr_ratio: float
    sl_basis: str               # "atr" | "swing"


def _recent_swing_low(df: pd.DataFrame, lookback: int = 20) -> float | None:
    if len(df) < lookback:
        return None
    return float(df["low"].iloc[-lookback:].min())


def _recent_swing_high(df: pd.DataFrame, lookback: int = 20) -> float | None:
    if len(df) < lookback:
        return None
    return float(df["high"].iloc[-lookback:].max())


def plan_for(
    side: Side,
    entry: float,
    df_1d: pd.DataFrame,
    atr_value: float,
    cfg: RiskConfig,
) -> RiskPlan:
    """Compose a SL/TP plan for a given side at `entry` price."""
    atr_stop_distance = cfg.stop_loss_atr_mult * atr_value
    sl_basis = "atr"

    if side == "buy":
        sl_atr = entry - atr_stop_distance
        sl = sl_atr
        if cfg.use_recent_swing_for_sr:
            swing_low = _recent_swing_low(df_1d)
            # Use swing only if it sits above the ATR stop (tighter risk) and below entry
            if swing_low is not None and sl_atr < swing_low < entry:
                sl = swing_low * 0.995  # buffer 0.5%
                sl_basis = "swing"
        risk = entry - sl
        reward = risk * cfg.take_profit_rr
        tp = entry + reward
    else:  # sell
        sl_atr = entry + atr_stop_distance
        sl = sl_atr
        if cfg.use_recent_swing_for_sr:
            swing_high = _recent_swing_high(df_1d)
            if swing_high is not None and entry < swing_high < sl_atr:
                sl = swing_high * 1.005
                sl_basis = "swing"
        risk = sl - entry
        reward = risk * cfg.take_profit_rr
        tp = entry - reward

    return RiskPlan(
        side=side,
        entry=entry,
        stop_loss=round(sl, 8),
        take_profit=round(tp, 8),
        risk_per_share=round(risk, 8),
        reward_per_share=round(reward, 8),
        rr_ratio=cfg.take_profit_rr,
        sl_basis=sl_basis,
    )
