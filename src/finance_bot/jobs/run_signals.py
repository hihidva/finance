"""Job: signal pipeline cho mọi primary asset.

Pipeline (M3):
  1. Load OHLCV 1D từ MySQL (>=60 bars).
  2. Compute rule-engine draft (analysis.signal.analyze).
  3. Build news + macro context, gọi LLM final-arbiter (ai.arbiter.arbitrate).
     - LLM CHỈ confirm hoặc HẠ tier (không bao giờ up-tier).
     - Nếu Claude CLI không khả dụng → fallback giữ rule-engine draft.
  4. Persist row vào `signals` (mọi tier; ghi cả news_against, llm_reasoning).
  5. Nếu post-arbiter Tier A và chưa cooldown → gửi Telegram, mark notified.
"""
from __future__ import annotations

from decimal import Decimal

from finance_bot.ai.arbiter import ArbitrationResult, arbitrate
from finance_bot.ai.llm import ClaudeClient
from finance_bot.ai.prompt import MacroBrief, NewsBrief
from finance_bot.analysis.signal import SignalDecision, analyze_composite
from finance_bot.db.queries import (
    load_macro_close_series,
    load_ohlcv_df,
    load_recent_news,
)
from finance_bot.db.repositories import (
    insert_signal,
    latest_alerted_signal,
    mark_signal_notified,
    upsert_asset,
)
from finance_bot.db.session import get_session
from finance_bot.logger import logger
from finance_bot.notifier.telegram import TelegramNotifier, format_alert
from finance_bot.settings import AssetConfig, Watchlist, get_watchlist


# ----------------------------------------------------------------------
# Context loaders
# ----------------------------------------------------------------------
def _build_news_briefs(session, asset: AssetConfig) -> list[NewsBrief]:
    keywords = [asset.symbol]
    # crypto pair "BTC/USDT" → also match "Bitcoin"
    if asset.asset_class == "crypto" and "/" in asset.symbol:
        base = asset.symbol.split("/")[0]
        keywords.extend([base, asset.name])
    news = load_recent_news(session, symbol_keywords=keywords, hours=48, limit=8)
    return [
        NewsBrief(
            title=n.title,
            source=n.source,
            published_at=n.published_at,
            summary=n.summary,
            lang=n.lang,
        )
        for n in news
    ]


def _pct_change(series, days: int) -> float | None:
    if series.empty or len(series) <= days:
        return None
    last = series.iloc[-1]
    past = series.iloc[-days - 1]
    if past == 0:
        return None
    return float((last - past) / past * 100.0)


def _build_micro_inputs(
    session, asset: AssetConfig,
) -> tuple[object | None, object | None, list]:
    """Resolve (FundamentalSnapshot latest, IndustryAverage, history) for an asset.

    Returns:
        - latest snapshot (or None) — for `compute_micro_score(fundamentals=...)`
        - industry average (or None)
        - history list (newest → oldest, ≤ 12 quarters; empty when no rows synced)

    Empty/None across the board → v1 path (legacy 3 sub-scores degrade fine).
    """
    from finance_bot.analysis.evaluation_micro import (
        FundamentalSnapshot,
        IndustryAverage,
    )
    from finance_bot.db.repositories import (
        latest_industry_average,
        recent_fundamental_snapshots,
    )

    if asset.asset_class != "vn_stock":
        return None, None, []

    rows = recent_fundamental_snapshots(session, asset.symbol, n=12)
    if not rows:
        return None, None, []

    def _f(v) -> float | None:
        return float(v) if v is not None else None

    # Fields that map 1:1 from DB row → FundamentalSnapshot dataclass.
    _PASSTHROUGH = (
        "roa", "roe", "pe", "pb",
        "revenue", "gross_profit", "net_profit", "eps",
        "cash_and_equivalents", "total_assets", "total_debt", "total_equity",
        "inventory", "receivables", "current_assets", "current_liabilities",
        "cfo", "capex", "cff", "fcf",
        "ev_ebitda", "ps", "roic", "gross_margin", "net_margin",
        "de_ratio", "current_ratio", "quick_ratio", "interest_coverage",
        "inventory_days", "receivable_days", "ccc",
    )

    def _row_to_snap(r) -> FundamentalSnapshot:
        kwargs = {name: _f(getattr(r, name, None)) for name in _PASSTHROUGH}
        return FundamentalSnapshot(
            asset_symbol=r.asset_symbol,
            period=r.period,
            period_end=r.period_end,
            source=r.source,
            fetched_at=r.fetched_at,
            **kwargs,
        )

    history = [_row_to_snap(r) for r in rows]
    latest = history[0]
    industry_code = rows[0].industry_code

    industry: IndustryAverage | None = None
    if industry_code:
        ind_row = latest_industry_average(session, industry_code)
        if ind_row is not None:
            industry = IndustryAverage(
                industry_code=ind_row.industry_code,
                period=ind_row.period,
                roa_avg=_f(ind_row.roa_avg), roa_median=_f(ind_row.roa_median),
                roe_avg=_f(ind_row.roe_avg), roe_median=_f(ind_row.roe_median),
                pe_avg=_f(ind_row.pe_avg),   pe_median=_f(ind_row.pe_median),
                pb_avg=_f(ind_row.pb_avg),   pb_median=_f(ind_row.pb_median),
                ev_ebitda_avg=_f(ind_row.ev_ebitda_avg),
                ev_ebitda_median=_f(ind_row.ev_ebitda_median),
                ps_avg=_f(ind_row.ps_avg), ps_median=_f(ind_row.ps_median),
                roic_avg=_f(ind_row.roic_avg), roic_median=_f(ind_row.roic_median),
                gross_margin_avg=_f(ind_row.gross_margin_avg),
                gross_margin_median=_f(ind_row.gross_margin_median),
                net_margin_avg=_f(ind_row.net_margin_avg),
                net_margin_median=_f(ind_row.net_margin_median),
                de_ratio_avg=_f(ind_row.de_ratio_avg),
                de_ratio_median=_f(ind_row.de_ratio_median),
                n_companies=ind_row.n_companies,
                source=ind_row.source,
            )
    return latest, industry, history


def _build_macro_briefs(session, watchlist: Watchlist) -> list[MacroBrief]:
    out: list[MacroBrief] = []
    for ctx in watchlist.context_assets:
        asset = upsert_asset(session, ctx)
        s = load_macro_close_series(session, asset.id, "1d", days=35)
        if s.empty:
            continue
        out.append(
            MacroBrief(
                symbol=ctx.symbol,
                name=ctx.name,
                last_close=float(s.iloc[-1]),
                pct_change_7d=_pct_change(s, 7),
                pct_change_30d=_pct_change(s, 30),
            )
        )
    return out


# ----------------------------------------------------------------------
# Signal → DB row
# ----------------------------------------------------------------------
def _decision_to_row(
    asset_id: int,
    draft: SignalDecision,
    arb: ArbitrationResult,
) -> dict:
    final = arb.decision
    indicators = draft.indicators_json()
    indicators["draft_tier"] = draft.tier
    indicators["draft_confidence"] = draft.confidence
    return {
        "asset_id": asset_id,
        "timeframe": final.timeframe,
        "ts": final.ts,
        "side": final.side,
        "tier": final.tier,
        "confidence": Decimal(f"{final.confidence:.3f}"),
        "price_at_signal": Decimal(str(final.price_at_signal)),
        "entry_window": final.entry_window,
        "expected_entry_at": final.expected_entry_at,
        "stop_loss": Decimal(str(final.risk.stop_loss)) if final.risk else None,
        "take_profit": Decimal(str(final.risk.take_profit)) if final.risk else None,
        "indicators": indicators,
        "news_context": {"news_against": arb.news_against},
        "rag_context": {
            "similar_cases": [
                {
                    "metadata": d.get("metadata", {}),
                    "score": d.get("score"),
                }
                for d in (arb.rag_context or [])
            ],
            "knowledge_used": [
                {"metadata": d.get("metadata", {}), "score": d.get("score")}
                for d in (arb.knowledge_used or [])
            ],
        },
        "llm_model": arb.llm_model,
        "llm_reasoning": (
            (arb.reasoning + " | " + " | ".join(draft.rationale)).strip(" |")
            if draft.rationale else arb.reasoning
        ),
    }


# ----------------------------------------------------------------------
# Per-asset run
# ----------------------------------------------------------------------
def run_for(
    asset_cfg: AssetConfig,
    notifier: TelegramNotifier | None = None,
    llm: ClaudeClient | None = None,
) -> SignalDecision | None:
    notifier = notifier or TelegramNotifier()
    llm = llm or ClaudeClient()
    timeframe = "1d"
    wl = get_watchlist()

    with get_session() as session:
        asset = upsert_asset(session, asset_cfg)
        df = load_ohlcv_df(session, asset.id, timeframe, limit=500)

        if len(df) < 60:
            logger.warning(
                "{}: chỉ có {} nến 1D — chưa đủ chạy indicators (cần >=60). "
                "Hãy chạy `sync-prices` trước.",
                asset_cfg.symbol, len(df),
            )
            return None

        news = _build_news_briefs(session, asset_cfg)
        macro = _build_macro_briefs(session, wl)
        fundamentals, industry_avg, fundamentals_history = _build_micro_inputs(
            session, asset_cfg,
        )
        draft = analyze_composite(
            asset_cfg, df, wl,
            macro_briefs=macro,
            news_briefs=news,
            fundamentals=fundamentals,
            industry_avg=industry_avg,
            fundamentals_history=fundamentals_history,
        )

    # Run LLM arbitration outside DB session — slow + idempotent.
    arb = arbitrate(draft, news=news, macro=macro, client=llm)
    final = arb.decision

    logger.info(
        "SIGNAL {:>10}  draft={}/{:.2f}  -> final={}/{:.2f}  llm={}  news_against={}",
        asset_cfg.symbol,
        f"{draft.side}/{draft.tier}", draft.confidence,
        f"{final.side}/{final.tier}", final.confidence,
        arb.llm_used,
        arb.news_against,
    )

    # Persist + maybe notify in a fresh session.
    with get_session() as session:
        asset = upsert_asset(session, asset_cfg)
        signal = insert_signal(session, _decision_to_row(asset.id, draft, arb))

        if not (final.tier == "A" and final.side != "hold"):
            return final

        prior = latest_alerted_signal(
            session, asset.id, within_hours=wl.signal.cooldown_hours_per_ticker
        )
        if prior is not None:
            logger.info(
                "{}: cooldown — đã alert lúc {} ({}h trước)",
                asset_cfg.symbol, prior.notified_at, wl.signal.cooldown_hours_per_ticker,
            )
            return final

        text = format_alert(final)
        message_id = notifier.send_alert(text, signal_id=signal.id)
        if message_id:
            mark_signal_notified(session, signal.id, message_id=message_id)
            logger.info("ALERT  {} sent OK (message_id={})",
                        asset_cfg.symbol, message_id)
        else:
            logger.warning("ALERT  {} skipped (notifier not configured or failed)",
                           asset_cfg.symbol)

    return final


def _pull_feedback_safely() -> None:
    """Pull Telegram callback queue → ghi user_decision trước khi compute signals.

    Vì user chọn cách "no separate cron entry", ta gọi đây ở đầu run_all để RAG
    luôn có user_decision mới nhất khi retrieve case lịch sử. Không bao giờ block
    pipeline chính nếu Telegram lỗi.
    """
    try:
        from finance_bot.jobs.process_feedback import process_feedback

        n = process_feedback()
        logger.info("run_signals: pulled {} feedback callback(s) from Telegram", n)
    except Exception:
        logger.exception("process_feedback failed at start of run_all (non-fatal)")


def run_all() -> None:
    _pull_feedback_safely()

    wl = get_watchlist()
    notifier = TelegramNotifier()
    llm = ClaudeClient()
    primary = wl.primary_assets
    logger.info("run_signals: starting for {} primary assets (LLM model={})",
                len(primary), llm.model)
    for asset_cfg in primary:
        try:
            run_for(asset_cfg, notifier=notifier, llm=llm)
        except Exception:
            logger.exception("run_for crashed on {}", asset_cfg.symbol)
    logger.info("run_signals: done")
