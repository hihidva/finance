"""Unit tests cho ai/arbiter.py — TC-SIG-10..15.

Invariants:
  - LLM may CONFIRM hoặc HẠ tier, KHÔNG được up-tier.
  - Nếu LLM flip side → ép hold/C.
  - Nếu Claude CLI down hoặc JSON parse fail → giữ rule-engine draft.
  - context_only asset → short-circuit hold/C, KHÔNG gọi LLM.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

from finance_bot.ai.arbiter import arbitrate
from finance_bot.ai.llm import LLMResponse, LLMUnavailable
from finance_bot.analysis.risk import RiskPlan
from finance_bot.analysis.signal import SignalDecision
from finance_bot.analysis.technical import TechSnapshot, Vote


def _make_draft(asset_cfg, side: str = "buy", tier: str = "B",
                confidence: float = 0.65) -> SignalDecision:
    snap = TechSnapshot(
        last_close=100.0,
        atr_value=2.0,
        votes=[
            Vote("RSI14", side, 0.5),
            Vote("MACD", side, 0.6),
            Vote("EMA20/50", side, 0.5),
            Vote("EMA50/200", "hold", 0.3),
            Vote("BB20", "hold", 0.2),
            Vote("VOL", "hold", 0.2),
            Vote("ATR_BO", "hold", 0.1),
        ],
    )
    risk = RiskPlan(
        side=side if side != "hold" else "buy",
        entry=100.0, stop_loss=96.0, take_profit=110.0,
        risk_per_share=4.0, reward_per_share=10.0, rr_ratio=2.5, sl_basis="atr",
    ) if side != "hold" else None
    return SignalDecision(
        asset=asset_cfg,
        timeframe="1d",
        ts=datetime(2026, 5, 2),
        side=side,
        tier=tier,
        confidence=confidence,
        price_at_signal=100.0,
        snapshot=snap,
        risk=risk,
        entry_window="ato_next_session",
        expected_entry_at=datetime(2026, 5, 5),
        rationale=[],
    )


def _patch_rag():
    """Patch RAG retrieval so arbiter doesn't touch ChromaDB."""
    return patch.multiple(
        "finance_bot.ai.arbiter",
        retrieve_similar_signals=MagicMock(return_value=[]),
        retrieve_knowledge=MagicMock(return_value=[]),
    )


# ----------------------------------------------------------------------
# Invariant: LLM up-tier rejected
# ----------------------------------------------------------------------
def test_llm_up_tier_rejected_keeps_draft_tier(asset_fpt):
    """TC-SIG-10: draft=B, LLM trả tier=A → final.tier giữ B."""
    draft = _make_draft(asset_fpt, side="buy", tier="B", confidence=0.65)
    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = LLMResponse(
        raw_text="{}",
        parsed={
            "final_side": "buy",
            "final_tier": "A",          # Cố tình up-tier
            "confidence": 0.95,
            "news_against": False,
            "reasoning": "All indicators agree",
        },
        model="claude-opus-4-7",
    )

    with _patch_rag():
        result = arbitrate(draft, news=[], macro=[], client=mock_llm)

    assert result.decision.tier == "B", "LLM tried to up-tier B→A; arbiter must reject"
    assert result.llm_used is True
    assert result.llm_model == "claude-opus-4-7"


def test_llm_down_tier_accepted(asset_fpt):
    """LLM hạ tier A→B là hợp lệ."""
    draft = _make_draft(asset_fpt, side="buy", tier="A", confidence=0.85)
    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = LLMResponse(
        raw_text="{}",
        parsed={
            "final_side": "buy", "final_tier": "B", "confidence": 0.6,
            "news_against": False, "reasoning": "concerns about news",
        },
        model="claude-opus-4-7",
    )

    with _patch_rag():
        result = arbitrate(draft, news=[], macro=[], client=mock_llm)

    assert result.decision.tier == "B"


# ----------------------------------------------------------------------
# Invariant: LLM flip side → hold/C
# ----------------------------------------------------------------------
def test_llm_flip_side_demoted_to_hold_c(asset_fpt):
    """TC-SIG-11: draft buy/A, LLM trả sell → ép side=hold, tier=C."""
    draft = _make_draft(asset_fpt, side="buy", tier="A", confidence=0.85)
    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = LLMResponse(
        raw_text="{}",
        parsed={
            "final_side": "sell", "final_tier": "A", "confidence": 0.8,
            "news_against": False, "reasoning": "reversed view",
        },
        model="claude-opus-4-7",
    )

    with _patch_rag():
        result = arbitrate(draft, news=[], macro=[], client=mock_llm)

    assert result.decision.side == "hold"
    assert result.decision.tier == "C"


def test_llm_hold_to_hold_is_idempotent(asset_fpt):
    """draft=hold, LLM=hold không flip → giữ hold/C."""
    draft = _make_draft(asset_fpt, side="hold", tier="C", confidence=0.0)
    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = LLMResponse(
        raw_text="{}",
        parsed={
            "final_side": "hold", "final_tier": "C", "confidence": 0.0,
            "news_against": False, "reasoning": "no edge",
        },
        model="claude-opus-4-7",
    )

    with _patch_rag():
        result = arbitrate(draft, news=[], macro=[], client=mock_llm)

    assert result.decision.side == "hold"
    assert result.decision.tier == "C"


# ----------------------------------------------------------------------
# Fallback paths
# ----------------------------------------------------------------------
def test_llm_unavailable_keeps_draft(asset_fpt):
    """TC-SIG-12: Claude CLI down → fallback giữ draft."""
    draft = _make_draft(asset_fpt, side="buy", tier="A", confidence=0.85)
    mock_llm = MagicMock()
    mock_llm.chat_json.side_effect = LLMUnavailable("claude binary not found in PATH")

    with _patch_rag():
        result = arbitrate(draft, news=[], macro=[], client=mock_llm)

    assert result.decision.side == "buy"
    assert result.decision.tier == "A"
    assert result.decision.confidence == 0.85
    assert result.llm_used is False
    assert result.llm_model is None
    assert "unavailable" in result.reasoning.lower()


def test_llm_returns_unparseable_keeps_draft(asset_fpt):
    """TC-SIG-13: parsed=None (JSON parse fail) → giữ draft."""
    draft = _make_draft(asset_fpt, side="buy", tier="A", confidence=0.85)
    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = LLMResponse(
        raw_text="not json", parsed=None, model="claude-opus-4-7",
    )

    with _patch_rag():
        result = arbitrate(draft, news=[], macro=[], client=mock_llm)

    assert result.decision.tier == "A"
    assert result.decision.side == "buy"
    assert result.llm_used is True
    assert "không hợp lệ" in result.reasoning


# ----------------------------------------------------------------------
# context_only short-circuit (TC-SIG-14)
# ----------------------------------------------------------------------
def test_context_only_asset_skips_llm(asset_dxy):
    """TC-SIG-14: context_only asset → arbiter trả sớm, KHÔNG gọi LLM."""
    draft = _make_draft(asset_dxy, side="hold", tier="C", confidence=0.0)
    mock_llm = MagicMock()

    with _patch_rag():
        result = arbitrate(draft, news=[], macro=[], client=mock_llm)

    mock_llm.chat_json.assert_not_called()
    assert result.llm_used is False
    assert result.decision.side == "hold"
    assert "context_only" in result.reasoning


# ----------------------------------------------------------------------
# Confidence clipping
# ----------------------------------------------------------------------
def test_llm_confidence_clipped_to_unit_range(asset_fpt):
    """LLM trả confidence > 1 hoặc < 0 → clamp [0, 1]."""
    draft = _make_draft(asset_fpt, side="buy", tier="A", confidence=0.85)
    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = LLMResponse(
        raw_text="{}",
        parsed={
            "final_side": "buy", "final_tier": "A", "confidence": 1.5,
            "news_against": False, "reasoning": "",
        },
        model="claude-opus-4-7",
    )

    with _patch_rag():
        result = arbitrate(draft, news=[], macro=[], client=mock_llm)

    assert 0.0 <= result.decision.confidence <= 1.0


def test_llm_invalid_confidence_uses_draft_value(asset_fpt):
    """LLM trả confidence='not a number' → giữ draft confidence."""
    draft = _make_draft(asset_fpt, side="buy", tier="A", confidence=0.85)
    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = LLMResponse(
        raw_text="{}",
        parsed={
            "final_side": "buy", "final_tier": "A", "confidence": "high",
            "news_against": False, "reasoning": "",
        },
        model="claude-opus-4-7",
    )

    with _patch_rag():
        result = arbitrate(draft, news=[], macro=[], client=mock_llm)

    assert result.decision.confidence == 0.85


# ----------------------------------------------------------------------
# news_against is propagated
# ----------------------------------------------------------------------
def test_news_against_flag_propagated(asset_fpt):
    draft = _make_draft(asset_fpt, side="buy", tier="A", confidence=0.85)
    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = LLMResponse(
        raw_text="{}",
        parsed={
            "final_side": "buy", "final_tier": "B", "confidence": 0.6,
            "news_against": True, "reasoning": "negative news",
        },
        model="claude-opus-4-7",
    )

    with _patch_rag():
        result = arbitrate(draft, news=[], macro=[], client=mock_llm)

    assert result.news_against is True
    assert result.decision.tier == "B"
