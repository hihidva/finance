"""High-level memory API trên ChromaDB.

Đây là tầng "trí nhớ" của bot — biến bot từ pure rule+LLM (zero-memory) thành
case-based reasoner: mỗi signal có outcome thực tế đều được embed lại để tương
lai retrieve khi gặp tình huống tương tự.

3 hành động chính:
  - remember_signal_outcome():  sau khi job eval_outcomes tính được P&L,
                                 ta embed signal kèm outcome → thêm vào RAG.
  - retrieve_similar_signals(): khi đang chạy arbiter, lấy top-k case lịch sử
                                 tương tự để LLM tham khảo.
  - learn_knowledge() / retrieve_knowledge(): kênh user-fed kiến thức mới.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from finance_bot.ai.rag import (
    KNOWLEDGE_COLLECTION,
    SIGNALS_COLLECTION,
    RetrievedDoc,
    delete,
    query,
    upsert,
)


# ======================================================================
# Signals history (case-based learning)
# ======================================================================
@dataclass
class SignalCase:
    signal_id: int
    asset_symbol: str
    asset_class: str
    side: str
    tier: str
    confidence: float
    indicators_summary: str       # 1-line text mô tả indicator votes
    llm_reasoning: str | None     # ý kiến LLM lúc bắn signal
    outcomes: list[dict]          # [{horizon_hours, pnl_pct, hit_target}, ...]
    signal_ts: datetime
    user_decision: str | None = None  # 'entered'|'skipped'|None (Telegram feedback)
    evaluation_version: str = "v1"    # "v1"=legacy technical-only, "v2"=composite engine


def _signal_doc_id(signal_id: int) -> str:
    return f"sig:{signal_id}"


def _format_signal_case(case: SignalCase) -> tuple[str, dict]:
    """Compose embedding text + metadata for a signal case."""
    outcome_lines = []
    for o in case.outcomes:
        hit = "✓" if o.get("hit_target") else "✗"
        outcome_lines.append(
            f"  - sau {o.get('horizon_hours')}h: pnl={o.get('pnl_pct'):+.2f}% {hit}"
        )
    outcome_block = "\n".join(outcome_lines) if outcome_lines else "  (chưa có outcome)"

    decision_label = {
        "entered": "user đã VÀO LỆNH thực tế",
        "skipped": "user BỎ QUA",
    }.get(case.user_decision or "", "user chưa phản hồi")

    text = (
        f"[{case.asset_symbol} | {case.asset_class}] "
        f"side={case.side} tier={case.tier} conf={case.confidence:.2f}\n"
        f"signal_ts={case.signal_ts.strftime('%Y-%m-%d %H:%M')}\n"
        f"user_decision: {decision_label}\n"
        f"indicators: {case.indicators_summary}\n"
        f"llm_reasoning: {case.llm_reasoning or '(không có)'}\n"
        f"outcomes:\n{outcome_block}"
    )

    metadata = {
        "signal_id": int(case.signal_id),
        "asset_symbol": case.asset_symbol,
        "asset_class": case.asset_class,
        "side": case.side,
        "tier": case.tier,
        "confidence": float(case.confidence),
        "signal_ts": case.signal_ts.isoformat(),
        "outcome_count": len(case.outcomes),
        "best_pnl_pct": max((float(o.get("pnl_pct", 0)) for o in case.outcomes),
                            default=0.0),
        "worst_pnl_pct": min((float(o.get("pnl_pct", 0)) for o in case.outcomes),
                             default=0.0),
        "user_decision": case.user_decision or "none",
        "evaluation_version": case.evaluation_version,
    }
    return text, metadata


def remember_signal_outcome(case: SignalCase) -> str:
    """Upsert (signal + outcomes) vào collection. Trả về chroma_id."""
    text, meta = _format_signal_case(case)
    doc_id = _signal_doc_id(case.signal_id)
    upsert(SIGNALS_COLLECTION, [doc_id], [text], [meta])
    return doc_id


def forget_signal(signal_id: int) -> None:
    delete(SIGNALS_COLLECTION, [_signal_doc_id(signal_id)])


def retrieve_similar_signals(
    *,
    asset_symbol: str,
    asset_class: str,
    side: str,
    indicators_summary: str,
    n: int = 5,
    evaluation_version: str | None = "v2",
) -> list[RetrievedDoc]:
    """Tìm top-k case lịch sử tương tự.

    `evaluation_version`: filter past cases by scoring schema version. Default
    `"v2"` (composite engine). Pass `None` to disable the filter and include
    legacy v1 cases — useful when the v2 corpus is still small. Cases written
    before this field existed are tagged "v1".
    """
    q_text = (
        f"[{asset_symbol} | {asset_class}] side={side}\n"
        f"indicators: {indicators_summary}"
    )
    base_where: dict = {"asset_class": asset_class}
    if evaluation_version is not None:
        base_where["evaluation_version"] = evaluation_version
    docs = query(SIGNALS_COLLECTION, q_text, n=n, where=base_where)
    if len(docs) < n:
        # Widen by dropping asset_class restriction but keep version filter.
        fallback_where: dict | None = (
            {"evaluation_version": evaluation_version} if evaluation_version else None
        )
        more = query(SIGNALS_COLLECTION, q_text, n=n - len(docs), where=fallback_where)
        seen = {d.id for d in docs}
        docs.extend(d for d in more if d.id not in seen)
    return docs


# ======================================================================
# Knowledge base (user-fed)
# ======================================================================
def _knowledge_doc_id(kb_id: int) -> str:
    return f"kb:{kb_id}"


def learn_knowledge(kb_id: int, title: str, body: str,
                    tags: list[str] | None = None,
                    source: str = "user") -> str:
    """Embed knowledge entry vào collection; trả về chroma_id."""
    text = f"{title}\n\n{body}"
    meta = {
        "kb_id": int(kb_id),
        "title": title,
        "tags": ",".join(tags or []),
        "source": source,
    }
    doc_id = _knowledge_doc_id(kb_id)
    upsert(KNOWLEDGE_COLLECTION, [doc_id], [text], [meta])
    return doc_id


def forget_knowledge(kb_id: int) -> None:
    delete(KNOWLEDGE_COLLECTION, [_knowledge_doc_id(kb_id)])


def retrieve_knowledge(query_text: str, n: int = 4) -> list[RetrievedDoc]:
    return query(KNOWLEDGE_COLLECTION, query_text, n=n)


# ======================================================================
# Helpers
# ======================================================================
def make_signal_id() -> str:
    """Use when caller doesn't have a DB id yet (rare)."""
    return uuid.uuid4().hex
