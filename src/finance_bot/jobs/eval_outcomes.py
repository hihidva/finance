"""Job: với mọi signal đủ tuổi → tính P&L thực tế từ price table → ghi outcomes
→ re-embed signal (kèm outcome) vào RAG.

Đây là vòng học của bot:
  1. Mỗi ngày, scan signals đã tới ngưỡng 1d / 3d / 7d / 30d nhưng chưa có row outcome.
  2. Cho mỗi (signal, horizon) → tìm giá close gần nhất sau (signal_ts + horizon).
  3. Tính pnl_pct dựa trên side (buy/sell) và entry_price (next session ATO cho VN).
  4. Insert outcomes row.
  5. Re-embed signal + tất cả outcomes hiện có vào ChromaDB → bot có thể "rút kinh nghiệm".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert

from finance_bot.ai.memory import SignalCase, remember_signal_outcome
from finance_bot.db.models import Asset, Outcome, Price, Signal
from finance_bot.db.session import get_session
from finance_bot.logger import logger

HORIZONS_HOURS = (24, 72, 168, 720)  # 1d, 3d, 7d, 30d


@dataclass
class _Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float


def _load_bars_after(session, asset_id: int, timeframe: str,
                     after_ts: datetime, until_ts: datetime) -> list[_Bar]:
    stmt = (
        select(Price.ts, Price.open, Price.high, Price.low, Price.close)
        .where(
            Price.asset_id == asset_id,
            Price.timeframe == timeframe,
            Price.ts > after_ts,
            Price.ts <= until_ts,
        )
        .order_by(Price.ts.asc())
    )
    return [_Bar(ts=r.ts, open=float(r.open), high=float(r.high),
                 low=float(r.low), close=float(r.close))
            for r in session.execute(stmt).all()]


def _entry_price(signal: Signal, bars: list[_Bar]) -> float | None:
    """For VN ATO entry, use next session's open. Else use price_at_signal."""
    if signal.entry_window == "ato_next_session":
        if not bars:
            return None
        return bars[0].open
    return float(signal.price_at_signal)


def _bar_at_or_before(bars: list[_Bar], target_ts: datetime) -> _Bar | None:
    found: _Bar | None = None
    for b in bars:
        if b.ts <= target_ts:
            found = b
        else:
            break
    return found


def _compute_outcome_for_horizon(
    signal: Signal, bars: list[_Bar], horizon_hours: int, entry_price: float,
) -> dict | None:
    target_ts = signal.ts + timedelta(hours=horizon_hours)
    if not bars or bars[-1].ts < target_ts:
        return None  # chưa đủ dữ liệu
    bar = _bar_at_or_before(bars, target_ts)
    if bar is None:
        return None

    price_then = bar.close
    if entry_price == 0:
        return None
    raw_pnl = (price_then - entry_price) / entry_price * 100.0
    pnl_pct = raw_pnl if signal.side == "buy" else -raw_pnl

    # Max favorable / adverse during horizon window
    window = [b for b in bars if signal.ts <= b.ts <= target_ts]
    if window:
        if signal.side == "buy":
            max_fav = max((b.high - entry_price) / entry_price * 100 for b in window)
            max_adv = min((b.low - entry_price) / entry_price * 100 for b in window)
        else:
            max_fav = max((entry_price - b.low) / entry_price * 100 for b in window)
            max_adv = min((entry_price - b.high) / entry_price * 100 for b in window)
    else:
        max_fav = max_adv = None

    return {
        "horizon_hours": horizon_hours,
        "evaluated_at": datetime.utcnow(),
        "price_then": Decimal(f"{price_then:.8f}"),
        "pnl_pct": Decimal(f"{pnl_pct:.4f}"),
        "hit_target": bool(pnl_pct > 0),
        "max_favorable": Decimal(f"{max_fav:.4f}") if max_fav is not None else None,
        "max_adverse": Decimal(f"{max_adv:.4f}") if max_adv is not None else None,
    }


def _existing_horizons(session, signal_id: int) -> set[int]:
    stmt = select(Outcome.horizon_hours).where(Outcome.signal_id == signal_id)
    return {int(h) for h in session.scalars(stmt).all()}


def _re_embed(session, signal: Signal) -> None:
    """Re-embed signal kèm tất cả outcomes hiện có vào ChromaDB."""
    try:
        outcomes = list(
            session.scalars(
                select(Outcome).where(Outcome.signal_id == signal.id)
                .order_by(Outcome.horizon_hours.asc())
            ).all()
        )
        if not outcomes:
            return  # nothing to learn from yet

        asset = session.get(Asset, signal.asset_id)
        if asset is None:
            return

        indicators_summary = _summarize_indicators(signal.indicators or {})
        case = SignalCase(
            signal_id=signal.id,
            asset_symbol=asset.symbol,
            asset_class=asset.asset_class,
            side=signal.side,
            tier=signal.tier,
            confidence=float(signal.confidence),
            indicators_summary=indicators_summary,
            llm_reasoning=signal.llm_reasoning,
            outcomes=[
                {
                    "horizon_hours": int(o.horizon_hours),
                    "pnl_pct": float(o.pnl_pct),
                    "hit_target": bool(o.hit_target),
                }
                for o in outcomes
            ],
            signal_ts=signal.ts,
            user_decision=signal.user_decision,
            evaluation_version=(signal.indicators or {}).get("evaluation_version", "v1"),
        )
        chroma_id = remember_signal_outcome(case)
        if signal.chroma_id != chroma_id:
            signal.chroma_id = chroma_id
    except Exception:
        logger.exception("re-embed failed for signal id={}", signal.id)


def _summarize_indicators(payload: dict) -> str:
    votes = payload.get("votes", []) if isinstance(payload, dict) else []
    if not votes:
        return "(không có)"
    return ", ".join(
        f"{v.get('name')}={str(v.get('side','?'))[0].upper()}({float(v.get('strength',0)):.2f})"
        for v in votes
    )


def _signals_due_for_eval(session, max_age_days: int = 60):
    """Signals ≤ max_age_days và còn thiếu ÍT NHẤT 1 horizon outcome."""
    cutoff = datetime.utcnow() - timedelta(days=max_age_days)
    stmt = (
        select(Signal)
        .where(Signal.ts >= cutoff)
        .order_by(Signal.ts.asc())
    )
    return list(session.scalars(stmt).all())


def evaluate_all() -> int:
    """Return số outcome row mới được insert."""
    new_outcomes = 0
    with get_session() as session:
        signals = _signals_due_for_eval(session)
        logger.info("eval_outcomes: scanning {} candidate signals", len(signals))

        for sig in signals:
            existing = _existing_horizons(session, sig.id)
            missing = [h for h in HORIZONS_HOURS if h not in existing]
            if not missing:
                continue

            # Load enough bars covering longest needed horizon
            until = sig.ts + timedelta(hours=max(missing) + 24)
            bars = _load_bars_after(session, sig.asset_id, sig.timeframe, sig.ts, until)
            entry_price = _entry_price(sig, bars)
            if entry_price is None:
                continue

            for h in missing:
                row = _compute_outcome_for_horizon(sig, bars, h, entry_price)
                if row is None:
                    continue
                stmt = mysql_insert(Outcome).values(signal_id=sig.id, **row)
                # ON DUPLICATE KEY → no-op (uk_signal_horizon)
                stmt = stmt.on_duplicate_key_update(signal_id=stmt.inserted.signal_id)
                session.execute(stmt)
                new_outcomes += 1
                logger.info(
                    "OUTCOME signal={} h={}h pnl={:+.2f}% hit={}",
                    sig.id, h, float(row["pnl_pct"]), row["hit_target"],
                )

            # Re-embed AFTER the inserts so new outcomes are reflected.
            session.flush()
            _re_embed(session, sig)

    logger.info("eval_outcomes: done — new outcome rows = {}", new_outcomes)
    return new_outcomes
