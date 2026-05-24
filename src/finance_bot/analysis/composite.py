"""Composite Score Alert Engine — Module 2 §2.10.

Combine the three evaluation services with equal weights (1/3 each) and map
the result to Tier A/B/C + side. When a service has score=None, its weight is
omitted and the remaining weights re-normalize automatically — no penalty,
no implicit zero.

Tier mapping (Module 2 §2.10.3):
    Tier A: |composite| ≥ THRESH_A AND ≥ 2 services agree with composite sign
            AND news_against == False
    Tier B: |composite| ≥ THRESH_B AND ≥ 2 services agree
    Tier C: |composite| ≥ THRESH_B but only 1 service agrees
    Tier C: |composite| < THRESH_B
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from finance_bot.analysis.evaluation_macro import MacroScore
from finance_bot.analysis.evaluation_micro import MicroScore
from finance_bot.analysis.evaluation_technical import TechScore

Side = Literal["buy", "sell", "hold"]
Tier = Literal["A", "B", "C"]

EVALUATION_VERSION = "v2"
WEIGHT_TECH = 1 / 3
WEIGHT_MACRO = 1 / 3
WEIGHT_MICRO = 1 / 3

# Tier thresholds on |composite|. Kept in code (not YAML) because they're tied
# to the composite formula's bounded [-1, +1] range; moving them to config
# without also moving the formula would invite drift.
THRESH_A = 0.60
THRESH_B = 0.40


@dataclass
class CompositeResult:
    composite: float
    side: Side
    tier: Tier
    agreeing_services: int          # 0..3 — count of services with sign == sign(composite)
    news_against: bool
    tech_score: TechScore
    macro_score: MacroScore
    micro_score: MicroScore
    reason: str

    def to_indicators_payload(self) -> dict:
        """Compact dict for `signals.indicators` JSON column."""
        return {
            "evaluation_version": EVALUATION_VERSION,
            "composite": self.composite,
            "agreeing_services": self.agreeing_services,
            "tech_score": {
                "score": self.tech_score.score,
                "dominant_side": self.tech_score.dominant_side,
                "agree_ratio": self.tech_score.agree_ratio,
                "confidence": self.tech_score.confidence,
                "reason": self.tech_score.reason,
                "votes": [
                    {"name": v.name, "side": v.side, "strength": v.strength,
                     "detail": v.detail}
                    for v in self.tech_score.votes_detail
                ],
            },
            "macro_score": {
                "score": self.macro_score.score,
                "breakdown": self.macro_score.breakdown,
                "reason": self.macro_score.reason,
            },
            "micro_score": {
                "score": self.micro_score.score,
                "breakdown": self.micro_score.breakdown,
                "news_against": self.micro_score.news_against,
                "reason": self.micro_score.reason,
                "news_count": self.micro_score.news_count,
                "checklist_report": self.micro_score.checklist_report,
            },
        }


def _sign(x: float) -> int:
    return 1 if x > 0 else (-1 if x < 0 else 0)


def aggregate(
    tech: TechScore,
    macro: MacroScore,
    micro: MicroScore,
) -> CompositeResult:
    """Combine 3 scores → composite + tier + side."""
    weighted = 0.0
    weight_sum = 0.0
    nonzero_scores: list[float] = []

    if tech.score is not None:
        weighted += WEIGHT_TECH * tech.score
        weight_sum += WEIGHT_TECH
        nonzero_scores.append(tech.score)
    if macro.score is not None:
        weighted += WEIGHT_MACRO * macro.score
        weight_sum += WEIGHT_MACRO
        nonzero_scores.append(macro.score)
    if micro.score is not None:
        weighted += WEIGHT_MICRO * micro.score
        weight_sum += WEIGHT_MICRO
        nonzero_scores.append(micro.score)

    composite = weighted / weight_sum if weight_sum > 0 else 0.0
    composite_sign = _sign(composite)
    side: Side = "buy" if composite_sign > 0 else (
        "sell" if composite_sign < 0 else "hold"
    )
    agreeing = sum(1 for s in nonzero_scores if _sign(s) == composite_sign != 0)
    news_against = micro.news_against

    abs_comp = abs(composite)
    if abs_comp >= THRESH_A and agreeing >= 2 and not news_against:
        tier: Tier = "A"
    elif abs_comp >= THRESH_B and agreeing >= 2:
        tier = "B"
    else:
        tier = "C"

    reason = (
        f"composite={composite:+.2f} ({agreeing}/3 service đồng thuận); "
        f"tech={tech.score:+.2f}, " if tech.score is not None else
        f"composite={composite:+.2f} ({agreeing}/3 service đồng thuận); tech=n/a, "
    )
    reason += f"macro={macro.score:+.2f}, " if macro.score is not None else "macro=n/a, "
    reason += f"micro={micro.score:+.2f}" if micro.score is not None else "micro=n/a"
    if news_against:
        reason += " [news_against]"

    return CompositeResult(
        composite=composite,
        side=side,
        tier=tier,
        agreeing_services=agreeing,
        news_against=news_against,
        tech_score=tech,
        macro_score=macro,
        micro_score=micro,
        reason=reason,
    )
