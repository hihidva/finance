"""Final-arbiter: áp LLM lên rule-engine draft để confirm/down-tier.

Thiết kế đã chốt với user (option A):
  - LLM CHỈ được CONFIRM hoặc HẠ tier (A→B/C, B→C). Không được up-tier.
  - LLM trả về news_against để ta lưu vào DB.
  - Reasoning ngắn (≤120 từ) tiếng Việt được lưu vào signals.llm_reasoning.

Nếu Claude CLI unavailable / LLM trả JSON sai → giữ nguyên draft, ghi lý do
fallback vào reasoning.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from finance_bot.ai.llm import ClaudeClient, LLMUnavailable
from finance_bot.ai.memory import (
    retrieve_knowledge,
    retrieve_similar_signals,
)
from finance_bot.ai.prompt import (
    MacroBrief,
    NewsBrief,
    SYSTEM_PROMPT,
    build_user_prompt,
)
from finance_bot.analysis.signal import SignalDecision
from finance_bot.logger import logger

Tier = Literal["A", "B", "C"]
_TIER_RANK = {"A": 3, "B": 2, "C": 1}


@dataclass
class ArbitrationResult:
    decision: SignalDecision      # post-arbiter (final)
    llm_used: bool
    llm_model: str | None
    news_against: bool
    reasoning: str
    rag_context: list[dict] | None = None    # similar cases retrieved
    knowledge_used: list[dict] | None = None # knowledge snippets used


def _coerce_tier(value: str | None, fallback: Tier) -> Tier:
    if value in ("A", "B", "C"):
        return value  # type: ignore[return-value]
    return fallback


def _coerce_side(value: str | None, fallback: str) -> str:
    if value in ("buy", "sell", "hold"):
        return value
    return fallback


def arbitrate(
    draft: SignalDecision,
    *,
    news: list[NewsBrief],
    macro: list[MacroBrief],
    client: ClaudeClient | None = None,
) -> ArbitrationResult:
    """Run LLM arbitration over a draft SignalDecision."""
    # Context-only assets: never produce signals — short-circuit.
    if draft.asset.context_only:
        return ArbitrationResult(
            decision=draft,
            llm_used=False,
            llm_model=None,
            news_against=False,
            reasoning="context_only asset, bỏ qua arbitration",
        )

    client = client or ClaudeClient()

    # ---- RAG retrieval ----------------------------------------------
    indicators_summary = ", ".join(
        f"{v['name']}={v['side'][0].upper()}({v['strength']:.2f})"
        for v in draft.indicators_json().get("votes", [])
    )
    similar_docs = retrieve_similar_signals(
        asset_symbol=draft.asset.symbol,
        asset_class=draft.asset.asset_class,
        side=draft.side,
        indicators_summary=indicators_summary,
        n=5,
    )
    similar_payload = [
        {"text": d.text, "score": d.distance, "metadata": d.metadata}
        for d in similar_docs
    ]

    knowledge_query = (
        f"{draft.asset.symbol} {draft.asset.asset_class} {draft.side} "
        f"indicators: {indicators_summary}"
    )
    knowledge_docs = retrieve_knowledge(knowledge_query, n=4)
    knowledge_payload = [
        {"text": d.text, "score": d.distance, "metadata": d.metadata}
        for d in knowledge_docs
    ]

    user_prompt = build_user_prompt(
        asset_symbol=draft.asset.symbol,
        asset_name=draft.asset.name,
        asset_class=draft.asset.asset_class,
        timeframe=draft.timeframe,
        last_close=draft.price_at_signal,
        draft_side=draft.side,
        draft_tier=draft.tier,
        draft_confidence=draft.confidence,
        indicators_json=draft.indicators_json(),
        risk={
            "entry": draft.risk.entry,
            "stop_loss": draft.risk.stop_loss,
            "take_profit": draft.risk.take_profit,
            "rr_ratio": draft.risk.rr_ratio,
            "sl_basis": draft.risk.sl_basis,
        } if draft.risk else None,
        news=news,
        macro=macro,
        similar_cases=similar_payload,
        knowledge_snippets=knowledge_payload,
    )

    try:
        resp = client.chat_json(SYSTEM_PROMPT, user_prompt)
    except LLMUnavailable as exc:
        logger.warning("Claude CLI unavailable for {} — keeping rule-engine draft. Reason: {}",
                       draft.asset.symbol, exc)
        return ArbitrationResult(
            decision=draft,
            llm_used=False,
            llm_model=None,
            news_against=False,
            reasoning=f"LLM unavailable ({exc}); giữ nguyên rule-engine draft.",
            rag_context=similar_payload,
            knowledge_used=knowledge_payload,
        )

    parsed = resp.parsed or {}
    if not parsed:
        logger.warning("LLM trả về JSON không parse được cho {} — giữ draft", draft.asset.symbol)
        return ArbitrationResult(
            decision=draft,
            llm_used=True,
            llm_model=resp.model,
            news_against=False,
            reasoning=f"LLM trả không hợp lệ; giữ draft. Raw: {resp.raw_text[:200]}",
            rag_context=similar_payload,
            knowledge_used=knowledge_payload,
        )

    llm_side = _coerce_side(parsed.get("final_side"), draft.side)
    llm_tier = _coerce_tier(parsed.get("final_tier"), draft.tier)
    llm_conf = parsed.get("confidence", draft.confidence)
    try:
        llm_conf = float(llm_conf)
        llm_conf = max(0.0, min(1.0, llm_conf))
    except (TypeError, ValueError):
        llm_conf = draft.confidence
    news_against = bool(parsed.get("news_against", False))
    reasoning = str(parsed.get("reasoning", ""))[:1500]

    # Enforce: LLM may only confirm or down-tier (never up).
    if _TIER_RANK[llm_tier] > _TIER_RANK[draft.tier]:
        logger.info(
            "{}: LLM tried to up-tier {}→{}, rejected — keeping draft tier",
            draft.asset.symbol, draft.tier, llm_tier,
        )
        llm_tier = draft.tier

    # If LLM disagrees on side, force hold (never flip side silently).
    if llm_side != draft.side and draft.side != "hold":
        logger.info(
            "{}: LLM flipped side {}→{}, demote to hold/C",
            draft.asset.symbol, draft.side, llm_side,
        )
        llm_side = "hold"
        llm_tier = "C"

    final_decision = replace(
        draft,
        side=llm_side,
        tier=llm_tier,
        confidence=llm_conf,
    )

    return ArbitrationResult(
        decision=final_decision,
        llm_used=True,
        llm_model=resp.model,
        news_against=news_against,
        reasoning=reasoning,
        rag_context=similar_payload,
        knowledge_used=knowledge_payload,
    )
