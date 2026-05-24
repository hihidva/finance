"""Technical Evaluation Service — Module 9.

Aggregate 14 indicator votes (Module 8) into one normalized score in [-1, +1].

Formula (Module 9 §9.3):
    score = (Σ strength | side=buy − Σ strength | side=sell) / total_strength

`hold` votes contribute neither to numerator nor denominator. When all votes are
hold (total_strength = 0), score = 0.0 (neutral).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from finance_bot.analysis.technical import TechSnapshot, Vote, compute_snapshot
from finance_bot.settings import AssetConfig


@dataclass
class TechScore:
    score: float | None  # [-1, +1] or None when insufficient data
    dominant_side: str = "hold"   # "buy" | "sell" | "hold"
    agree_ratio: float = 0.0
    confidence: float = 0.0
    votes_detail: list[Vote] = field(default_factory=list)
    reason: str = ""


def _aggregate(snapshot: TechSnapshot) -> tuple[float, float]:
    """Return (score, confidence) from a snapshot. Pure math, no I/O."""
    buy_sum = sum(v.strength for v in snapshot.votes if v.side == "buy")
    sell_sum = sum(v.strength for v in snapshot.votes if v.side == "sell")
    total = buy_sum + sell_sum
    if total == 0.0:
        return 0.0, 0.0

    score = (buy_sum - sell_sum) / total
    dominant = "buy" if snapshot.buy_count > snapshot.sell_count else (
        "sell" if snapshot.sell_count > snapshot.buy_count else "hold"
    )
    matching = [v for v in snapshot.votes if v.side == dominant]
    confidence = (sum(v.strength for v in matching) / len(matching)) if matching else 0.0
    return score, confidence


def compute_technical_score(asset: AssetConfig, df_1d: pd.DataFrame) -> TechScore:
    """Compute TechScore from a daily OHLCV frame.

    asset is accepted for symmetry with macro/micro services but currently unused;
    indicator votes are asset-agnostic on D1 timeframe.
    """
    del asset  # not yet used; reserved for per-asset-class indicator tuning

    if len(df_1d) < 60:
        return TechScore(
            score=None,
            reason=f"insufficient_data: cần ≥60 bars, hiện có {len(df_1d)}",
        )

    snapshot = compute_snapshot(df_1d)
    score, confidence = _aggregate(snapshot)
    total = len(snapshot.votes)
    agree_ratio = snapshot.agree_count / total if total else 0.0
    dominant = "buy" if snapshot.buy_count > snapshot.sell_count else (
        "sell" if snapshot.sell_count > snapshot.buy_count else "hold"
    )

    reason = (
        f"{snapshot.agree_count}/{total} indicators đồng thuận {dominant}, "
        f"score={score:+.2f}, confidence={confidence:.2f}"
    )

    return TechScore(
        score=score,
        dominant_side=dominant,
        agree_ratio=agree_ratio,
        confidence=confidence,
        votes_detail=list(snapshot.votes),
        reason=reason,
    )
