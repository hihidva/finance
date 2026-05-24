"""Tier A/B/C signal engine — rule-based for M2 (LLM fusion sẽ lên ở M3).

Quy tắc đã chốt với user:
  Tier A → ≥4 indicators đồng thuận, confidence ≥ 0.75, news không ngược chiều
  Tier B → ≥3 đồng thuận, confidence ≥ 0.60
  Tier C → còn lại
Chỉ Tier A mới gửi alert; Tier B/C ghi DB để training RAG sau.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import pandas as pd

from finance_bot.analysis.risk import RiskPlan, plan_for
from finance_bot.analysis.technical import TechSnapshot, compute_snapshot
from finance_bot.settings import AssetConfig, Watchlist

Side = Literal["buy", "sell", "hold"]
Tier = Literal["A", "B", "C"]


@dataclass
class SignalDecision:
    asset: AssetConfig
    timeframe: str = "1d"
    ts: datetime | None = None              # candle ts triggering the signal
    side: Side = "hold"
    tier: Tier = "C"
    confidence: float = 0.0
    price_at_signal: float = 0.0
    snapshot: TechSnapshot | None = None
    risk: RiskPlan | None = None
    entry_window: str = "immediate"
    expected_entry_at: datetime | None = None
    rationale: list[str] = field(default_factory=list)
    composite: object | None = None       # CompositeResult — set by analyze_composite()

    @property
    def should_alert(self) -> bool:
        return self.tier == "A" and self.side != "hold"

    def indicators_json(self) -> dict:
        if not self.snapshot:
            return {}
        payload: dict = {
            "votes": [
                {"name": v.name, "side": v.side, "strength": v.strength, "detail": v.detail}
                for v in self.snapshot.votes
            ],
            "buy_count": self.snapshot.buy_count,
            "sell_count": self.snapshot.sell_count,
            "atr": self.snapshot.atr_value,
            "last_close": self.snapshot.last_close,
        }
        if self.composite is not None and hasattr(self.composite, "to_indicators_payload"):
            payload.update(self.composite.to_indicators_payload())
        return payload


# ----------------------------------------------------------------------
# Confidence scoring
# ----------------------------------------------------------------------
def _confidence_from_votes(snapshot: TechSnapshot, side: Side) -> float:
    """Trung bình strength của các vote cùng side, có bonus theo số lượng."""
    if side == "hold":
        return 0.0
    matching = [v for v in snapshot.votes if v.side == side]
    if not matching:
        return 0.0
    avg_strength = sum(v.strength for v in matching) / len(matching)
    agree = len(matching)
    # bonus: 4/7 indicators = 0.0, 5/7 = 0.05, 6/7 = 0.10, 7/7 = 0.15
    bonus = max(0, agree - 4) * 0.05
    # penalty if opposite votes are strong
    opp = [v for v in snapshot.votes if v.side != side and v.side != "hold"]
    if opp:
        opp_strength = sum(v.strength for v in opp) / len(opp)
        penalty = 0.5 * (len(opp) / len(snapshot.votes)) * opp_strength
    else:
        penalty = 0.0
    return max(0.0, min(1.0, avg_strength + bonus - penalty))


# ----------------------------------------------------------------------
# Entry window (VN ATO next session vs immediate)
# ----------------------------------------------------------------------
_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
_VN_OPEN_TIME = time(9, 15)


def _next_vn_ato_at(now_utc: datetime) -> datetime:
    """Return next VN trading-day open in UTC. Skips Sat/Sun (holidays not handled)."""
    local = now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(_VN_TZ)
    candidate_date = local.date()
    if local.time() >= _VN_OPEN_TIME:
        candidate_date = candidate_date + timedelta(days=1)
    while candidate_date.weekday() >= 5:  # 5=Sat, 6=Sun
        candidate_date += timedelta(days=1)
    candidate_local = datetime.combine(candidate_date, _VN_OPEN_TIME, tzinfo=_VN_TZ)
    return candidate_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _entry_window_for(asset: AssetConfig, now_utc: datetime) -> tuple[str, datetime]:
    if asset.asset_class == "vn_stock":
        return "ato_next_session", _next_vn_ato_at(now_utc)
    return "immediate", now_utc


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------
def analyze(
    asset: AssetConfig,
    df_1d: pd.DataFrame,
    watchlist: Watchlist,
    news_against: bool = False,
    now_utc: datetime | None = None,
) -> SignalDecision:
    """Compute a SignalDecision from a daily OHLCV frame.

    `news_against` is set by caller (M3 LLM/news layer); for M2 we leave it False.
    """
    snapshot = compute_snapshot(df_1d)
    side: Side = snapshot.dominant_side
    confidence = _confidence_from_votes(snapshot, side)

    cfg = watchlist.signal
    total = len(snapshot.votes)
    agree_ratio = snapshot.agree_count / total if total else 0.0
    tier: Tier = "C"
    rationale: list[str] = []

    if asset.context_only:
        rationale.append("context_only=true → bot không sinh signal độc lập, dừng ở Tier C")
    elif side == "hold" or agree_ratio < cfg.tier_b.min_agree_ratio:
        rationale.append(
            f"chỉ {snapshot.agree_count}/{total} ({agree_ratio:.0%}) indicator đồng thuận "
            f"→ dưới ngưỡng Tier B ({cfg.tier_b.min_agree_ratio:.0%})"
        )
    elif (
        agree_ratio >= cfg.tier_a.min_agree_ratio
        and confidence >= cfg.tier_a.min_confidence
    ):
        if cfg.tier_a.require_news_not_against and news_against:
            tier = "B"
            rationale.append(
                "đủ điều kiện kỹ thuật Tier A nhưng tin tức ngược chiều → hạ xuống Tier B"
            )
        else:
            tier = "A"
            rationale.append(
                f"Tier A: {snapshot.agree_count}/{total} ({agree_ratio:.0%}) indicators đồng thuận, "
                f"confidence={confidence:.2f}"
            )
    elif (
        agree_ratio >= cfg.tier_b.min_agree_ratio
        and confidence >= cfg.tier_b.min_confidence
    ):
        tier = "B"
        rationale.append(
            f"Tier B: {snapshot.agree_count}/{total} ({agree_ratio:.0%}) indicators đồng thuận, "
            f"confidence={confidence:.2f}"
        )
    else:
        rationale.append(
            f"confidence={confidence:.2f} dưới ngưỡng "
            f"Tier B={cfg.tier_b.min_confidence}"
        )

    risk_plan: RiskPlan | None = None
    if tier == "A" and side in ("buy", "sell"):
        risk_plan = plan_for(side, snapshot.last_close, df_1d, snapshot.atr_value,
                             watchlist.risk)

    now = now_utc or datetime.utcnow()
    window, expected = _entry_window_for(asset, now)
    candle_ts = (
        df_1d.index[-1].to_pydatetime()
        if isinstance(df_1d.index, pd.DatetimeIndex)
        else df_1d["ts"].iloc[-1].to_pydatetime()
    ) if hasattr(df_1d, "index") else now

    return SignalDecision(
        asset=asset,
        ts=candle_ts,
        side=side,
        tier=tier,
        confidence=confidence,
        price_at_signal=snapshot.last_close,
        snapshot=snapshot,
        risk=risk_plan,
        entry_window=window,
        expected_entry_at=expected,
        rationale=rationale,
    )


# ----------------------------------------------------------------------
# Composite engine entry point (Module 2 §2.10 — v2 alert path)
# ----------------------------------------------------------------------
def analyze_composite(
    asset: AssetConfig,
    df_1d: pd.DataFrame,
    watchlist: Watchlist,
    *,
    macro_briefs: list | None = None,
    news_briefs: list | None = None,
    fundamentals: object | None = None,
    industry_avg: object | None = None,
    fundamentals_history: list | None = None,
    now_utc: datetime | None = None,
) -> SignalDecision:
    """Compute a SignalDecision using the 3-service composite engine.

    Replaces the technical-only tier rule used by `analyze()`; intended for the
    production `run_signals` pipeline. Backtest still uses `analyze()` because
    historical macro / news / fundamentals aren't easily reproducible.
    """
    # Local imports keep this module loadable in degraded envs (no need to
    # pull composite chain when only running backtest).
    from finance_bot.analysis.composite import aggregate
    from finance_bot.analysis.evaluation_macro import compute_macro_score
    from finance_bot.analysis.evaluation_micro import compute_micro_score
    from finance_bot.analysis.evaluation_technical import compute_technical_score

    snapshot = compute_snapshot(df_1d)
    tech = compute_technical_score(asset, df_1d)
    macro = compute_macro_score(asset, macro_briefs or [])
    micro = compute_micro_score(
        asset, fundamentals, industry_avg, news_briefs or [],
        history=fundamentals_history,
    )

    if asset.context_only:
        composite_result = aggregate(tech, macro, micro)
        # Force hold/C for context_only assets regardless of composite outcome.
        side: Side = "hold"
        tier: Tier = "C"
        rationale = ["context_only=true → bot không sinh signal độc lập, dừng ở Tier C"]
        confidence = 0.0
    else:
        composite_result = aggregate(tech, macro, micro)
        side = composite_result.side
        tier = composite_result.tier
        confidence = abs(composite_result.composite)
        rationale = [composite_result.reason]

    risk_plan: RiskPlan | None = None
    if tier == "A" and side in ("buy", "sell"):
        risk_plan = plan_for(side, snapshot.last_close, df_1d, snapshot.atr_value,
                             watchlist.risk)

    now = now_utc or datetime.utcnow()
    window, expected = _entry_window_for(asset, now)
    candle_ts = (
        df_1d.index[-1].to_pydatetime()
        if isinstance(df_1d.index, pd.DatetimeIndex)
        else df_1d["ts"].iloc[-1].to_pydatetime()
    ) if hasattr(df_1d, "index") else now

    return SignalDecision(
        asset=asset,
        ts=candle_ts,
        side=side,
        tier=tier,
        confidence=confidence,
        price_at_signal=snapshot.last_close,
        snapshot=snapshot,
        risk=risk_plan,
        entry_window=window,
        expected_entry_at=expected,
        rationale=rationale,
        composite=composite_result,
    )
