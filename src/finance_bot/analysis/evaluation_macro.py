"""Macro Evaluation Service — Module 10.

Convert a list of MacroBrief (per `context_only` watchlist asset) into one
normalized score in [-1, +1] per primary asset, using a per-asset-class
sensitivity matrix.

Phase 1 indicators (data already fetched by Module 1):
    - DXY  (DX-Y.NYB)   — USD strength index
    - WTI  (CL=F)        — crude oil
Phase 3 indicators (also already fetched as ^IRX proxy):
    - ^IRX (13W T-Bill yield) — proxy for FED policy rate AND short-end UST

The mapping `watchlist_symbol → macro_indicator` is done by `_MACRO_ALIAS` so
yfinance tickers (DX-Y.NYB) resolve to logical names (DXY) used in the
sensitivity matrix.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from finance_bot.ai.prompt import MacroBrief
from finance_bot.settings import AssetConfig

# Resolve real watchlist tickers (Module 1) to logical macro names used here.
_MACRO_ALIAS: dict[str, str] = {
    "DX-Y.NYB": "DXY",
    "CL=F": "WTI",
    "^IRX": "RATES",  # 13W T-Bill — proxy for FED rate + short-end UST curve
}

# sensitivity[asset_class][macro_indicator] = (weight, direction)
# direction: +1 = macro ↑ → bullish asset, -1 = macro ↑ → bearish asset, 0 = neutral.
_SENSITIVITY: dict[str, dict[str, tuple[float, int]]] = {
    "vn_stock":  {"DXY": (0.40, -1), "WTI": (0.25, -1), "RATES": (0.35, -1)},
    "crypto":    {"DXY": (0.50, -1), "WTI": (0.15,  0), "RATES": (0.35, -1)},
    "commodity": {"DXY": (0.55, -1), "WTI": (0.20, +1), "RATES": (0.25, -1)},
    "fx_index":  {},  # context_only assets — not scored
}

# Threshold to saturate signal_strength = clamp(pct_change_30d / threshold, -1, +1).
_THRESHOLD_30D: dict[str, float] = {
    "DXY":   3.0,   # 3 %  / 30d = strong dollar move
    "WTI":  12.0,   # 12 % / 30d = strong oil move
    "RATES": 0.25,  # 25 bps / 30d on ^IRX yield = meaningful Fed move
}


@dataclass
class MacroScore:
    score: float | None
    breakdown: dict[str, float] = field(default_factory=dict)
    reason: str = ""
    macro_briefs_used: list[MacroBrief] = field(default_factory=list)


def _signal_strength(pct_change: float | None, threshold: float) -> float | None:
    if pct_change is None:
        return None
    if threshold <= 0:
        return 0.0
    return max(-1.0, min(1.0, pct_change / threshold))


def compute_macro_score(
    asset: AssetConfig,
    macro_briefs: list[MacroBrief],
) -> MacroScore:
    """Compute a per-asset macro score in [-1, +1] from a set of macro briefs.

    Score = 0.0 (neutral) when asset class has no sensitivity defined.
    Score = None when no macro brief is provided at all.
    """
    if not macro_briefs:
        return MacroScore(score=None, reason="no_macro_data")

    asset_sens = _SENSITIVITY.get(asset.asset_class)
    if asset_sens is None or not asset_sens:
        return MacroScore(
            score=0.0,
            reason=f"asset_class_no_macro_sensitivity: {asset.asset_class}",
            macro_briefs_used=list(macro_briefs),
        )

    weighted = 0.0
    weight_sum = 0.0
    breakdown: dict[str, float] = {}
    used: list[MacroBrief] = []

    for brief in macro_briefs:
        logical = _MACRO_ALIAS.get(brief.symbol, brief.symbol)
        cell = asset_sens.get(logical)
        if cell is None:
            continue
        weight, direction = cell
        if weight == 0 or direction == 0:
            breakdown[logical] = 0.0
            used.append(brief)
            continue

        threshold = _THRESHOLD_30D.get(logical, 1.0)
        ss = _signal_strength(brief.pct_change_30d, threshold)
        if ss is None:
            continue

        contribution = weight * direction * ss
        weighted += contribution
        weight_sum += weight
        breakdown[logical] = contribution
        used.append(brief)

    if weight_sum == 0:
        # All matching briefs had pct_change_30d=None or weight=0.
        return MacroScore(
            score=0.0,
            breakdown=breakdown,
            reason="macro_briefs_have_no_pct_change",
            macro_briefs_used=used,
        )

    score = weighted / weight_sum
    # Build a short human-readable reason in Vietnamese.
    parts = []
    for sym, contrib in breakdown.items():
        sign = "+" if contrib >= 0 else ""
        parts.append(f"{sym}={sign}{contrib:.2f}")
    reason = (
        f"composite macro={score:+.2f} cho {asset.asset_class} "
        f"({', '.join(parts) or 'no contribution'})"
    )

    return MacroScore(
        score=score,
        breakdown=breakdown,
        reason=reason,
        macro_briefs_used=used,
    )
