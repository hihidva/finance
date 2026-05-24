"""Prompt builder cho final-arbiter LLM call.

Chiến lược (đã chốt với user — option A "Final arbiter"):
  Rule engine ra Tier draft (A/B/C) cùng side và confidence.
  LLM xem indicators + tin tức gần đây + bối cảnh vĩ mô (DXY, WTI) rồi:
    - confirm hoặc DOWN-tier (KHÔNG được up-tier).
    - đặt cờ news_against=true nếu tin tức ngược chiều rõ rệt.
    - đưa lập luận ngắn (≤ 120 từ) bằng tiếng Việt.

Output JSON schema (LLM phải tuân thủ):
{
  "final_side":   "buy" | "sell" | "hold",
  "final_tier":   "A"   | "B"    | "C",
  "confidence":   number 0..1,
  "news_against": boolean,
  "reasoning":    string (Vietnamese, ≤ 120 từ)
}
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class NewsBrief:
    title: str
    source: str
    published_at: datetime
    summary: str | None = None
    lang: str = "vi"


@dataclass
class MacroBrief:
    symbol: str
    name: str
    last_close: float
    pct_change_7d: float | None
    pct_change_30d: float | None


SYSTEM_PROMPT = """Bạn là một analyst tài chính cao cấp tại một quỹ đầu tư.
Nhiệm vụ duy nhất: review một quyết định giao dịch DRAFT do rule engine đưa ra,
sau đó CONFIRM hoặc HẠ tier (KHÔNG bao giờ nâng tier).

Quy tắc bắt buộc:
1. Bạn chỉ được phép giữ nguyên tier hoặc HẠ tier (A → B/C, B → C).
2. Nếu tin tức gần đây ngược chiều với side (ví dụ side='buy' nhưng có tin tiêu cực
   nghiêm trọng về asset) → đặt news_against=true VÀ hạ tier ít nhất 1 bậc.
3. Bối cảnh vĩ mô (DXY mạnh, dầu tăng sốc, lãi suất, ...) có thể ảnh hưởng tới
   asset; nếu xung đột rõ rệt → cũng hạ tier.
4. Trả lời PHẢI là JSON đúng schema, KHÔNG có markdown, KHÔNG có giải thích ngoài JSON.

Schema JSON:
{
  "final_side":   "buy" | "sell" | "hold",
  "final_tier":   "A" | "B" | "C",
  "confidence":   number trong [0, 1],
  "news_against": true | false,
  "reasoning":    string tiếng Việt, tối đa 120 từ
}
"""


def _format_indicators(snapshot_json: dict[str, Any]) -> str:
    votes = snapshot_json.get("votes", [])
    if not votes:
        return "(không có dữ liệu indicator)"
    lines = []
    for v in votes:
        detail = ", ".join(f"{k}={round(val, 3) if isinstance(val, float) else val}"
                           for k, val in v.get("detail", {}).items())
        lines.append(
            f"  - {v['name']:<10} {v['side']:<4} strength={v['strength']:.2f}  {detail}"
        )
    lines.append(
        f"  -> tổng kết: {snapshot_json.get('buy_count', 0)} buy / "
        f"{snapshot_json.get('sell_count', 0)} sell / "
        f"{7 - snapshot_json.get('buy_count', 0) - snapshot_json.get('sell_count', 0)} hold"
    )
    return "\n".join(lines)


def _format_news(news: list[NewsBrief]) -> str:
    if not news:
        return "(không có tin gần đây)"
    lines = []
    for n in news[:8]:
        ts = n.published_at.strftime("%Y-%m-%d %H:%M")
        title = n.title.strip().replace("\n", " ")
        if len(title) > 200:
            title = title[:200] + "…"
        lines.append(f"  - [{ts}] [{n.source}] {title}")
    return "\n".join(lines)


def _format_rag(retrieved: list[dict] | None, label: str) -> str:
    if not retrieved:
        return f"({label}: chưa có)"
    lines = []
    for r in retrieved:
        score = r.get("score")
        score_str = f"  (sim={1 - score:.2f})" if isinstance(score, (int, float)) else ""
        text = r.get("text", "").strip().replace("\n", " ")
        if len(text) > 280:
            text = text[:280] + "…"
        lines.append(f"  - {text}{score_str}")
    return "\n".join(lines)


def _format_macro(macro: list[MacroBrief]) -> str:
    if not macro:
        return "(không có bối cảnh vĩ mô)"
    lines = []
    for m in macro:
        d7 = f"{m.pct_change_7d:+.2f}%" if m.pct_change_7d is not None else "-"
        d30 = f"{m.pct_change_30d:+.2f}%" if m.pct_change_30d is not None else "-"
        lines.append(f"  - {m.symbol:<10} ({m.name})  last={m.last_close:,.4f}  7d={d7}  30d={d30}")
    return "\n".join(lines)


def _format_evaluation_scores(indicators_json: dict[str, Any]) -> str:
    """Render the 3 v2 evaluation scores (Module 9/10/11) into a readable block.

    Returns "(legacy v1 signal — no composite scores)" when the draft was
    produced by the pre-composite engine (no `tech_score` key in indicators_json).
    """
    if "evaluation_version" not in indicators_json:
        return "(legacy v1 signal — không có composite score)"

    composite = indicators_json.get("composite")
    agreeing = indicators_json.get("agreeing_services")
    lines = [
        f"  composite = {composite:+.2f}  ({agreeing}/3 service đồng thuận)"
        if composite is not None
        else "  composite = n/a"
    ]

    for label, key in (
        ("Technical (Module 9)", "tech_score"),
        ("Macro    (Module 10)", "macro_score"),
        ("Micro    (Module 11)", "micro_score"),
    ):
        block = indicators_json.get(key) or {}
        score = block.get("score")
        reason = block.get("reason", "")
        score_str = f"{score:+.2f}" if isinstance(score, (int, float)) else "n/a"
        lines.append(f"  - {label}: {score_str}  — {reason}")

    micro = indicators_json.get("micro_score") or {}
    if micro.get("news_against"):
        lines.append("  ⚠ news_against=True (Tier A bị gác)")

    return "\n".join(lines)


def build_user_prompt(
    *,
    asset_symbol: str,
    asset_name: str,
    asset_class: str,
    timeframe: str,
    last_close: float,
    draft_side: str,
    draft_tier: str,
    draft_confidence: float,
    indicators_json: dict[str, Any],
    risk: dict[str, Any] | None,
    news: list[NewsBrief],
    macro: list[MacroBrief],
    similar_cases: list[dict] | None = None,
    knowledge_snippets: list[dict] | None = None,
) -> str:
    risk_block = "(không có vì draft tier không phải A)"
    if risk:
        risk_block = (
            f"  entry={risk['entry']:.4f}  SL={risk['stop_loss']:.4f}  "
            f"TP={risk['take_profit']:.4f}  R:R=1:{risk['rr_ratio']:.1f}  "
            f"({risk['sl_basis']}-based)"
        )

    return f"""ASSET
  symbol = {asset_symbol}
  name   = {asset_name}
  class  = {asset_class}
  timeframe = {timeframe}
  last_close = {last_close}

DRAFT (từ rule engine)
  side       = {draft_side}
  tier       = {draft_tier}
  confidence = {draft_confidence:.2f}

EVALUATION SCORES (3 thước đo trọng số bằng nhau — Module 2 §2.10)
{_format_evaluation_scores(indicators_json)}

INDICATORS
{_format_indicators(indicators_json)}

RISK PLAN (nếu có)
{risk_block}

TIN TỨC GẦN ĐÂY (≤ 48h)
{_format_news(news)}

BỐI CẢNH VĨ MÔ
{_format_macro(macro)}

CASE LỊCH SỬ TƯƠNG TỰ (RAG — bot đã từng signal trong tình huống giống vầy)
{_format_rag(similar_cases, "case lịch sử")}

KIẾN THỨC ĐÃ HỌC (knowledge base)
{_format_rag(knowledge_snippets, "knowledge")}

Hãy review draft trên và trả về JSON đúng schema.
Lưu ý: nếu CASE LỊCH SỬ cho thấy pattern này thường thua → hạ tier mạnh tay.
Nếu KIẾN THỨC mâu thuẫn với draft → cân nhắc kỹ trước khi confirm.
Quan trọng: ngày hôm nay là {datetime.utcnow().strftime("%Y-%m-%d")}.
Nếu draft tier=A nhưng tin tức ngược chiều → hạ xuống B hoặc C và đặt news_against=true.
Nếu mọi thứ ổn → giữ tier draft.
"""
