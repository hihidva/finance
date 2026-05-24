"""Signals list / detail / override-user-decision endpoints."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from finance_bot.db.models import Asset, Outcome, Signal
from finance_bot.db.repositories import set_user_decision
from finance_bot.web.deps import db_session
from finance_bot.web.schemas import (
    OutcomeOut,
    SignalDetail,
    SignalListItem,
    SignalListResponse,
    UserDecisionPatch,
)

router = APIRouter()

HORIZON_KEY = {24: "1d", 72: "3d", 168: "7d", 720: "30d"}


@router.get("", response_model=SignalListResponse)
def list_signals(
    symbols: str | None = Query(None, description="Comma-separated symbols"),
    tiers: str | None = Query(None, description="Comma-separated tiers (A,B,C)"),
    sides: str | None = Query(None, description="Comma-separated sides (buy,sell,hold)"),
    user_decision: str | None = Query(
        None,
        pattern=r"^(entered|skipped|pending|all)$",
        description="entered | skipped | pending | all",
    ),
    notified: bool | None = Query(None),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    session: Session = Depends(db_session),
) -> SignalListResponse:
    if from_ and to and from_ > to:
        raise HTTPException(status_code=422, detail="`from` phải <= `to`")

    base = select(Signal).join(Asset, Asset.id == Signal.asset_id).options(
        selectinload(Signal.asset),
        selectinload(Signal.outcomes),
    )
    base = _apply_filters(base, symbols, tiers, sides, user_decision, notified, from_, to)

    count_stmt = (
        select(func.count(Signal.id))
        .select_from(Signal)
        .join(Asset, Asset.id == Signal.asset_id)
    )
    count_stmt = _apply_filters(
        count_stmt, symbols, tiers, sides, user_decision, notified, from_, to
    )
    total = session.scalar(count_stmt) or 0

    stmt = base.order_by(Signal.ts.desc()).limit(page_size).offset((page - 1) * page_size)
    rows = list(session.scalars(stmt).all())

    items = [_to_list_item(r) for r in rows]
    return SignalListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{signal_id}", response_model=SignalDetail)
def get_signal(
    signal_id: int,
    session: Session = Depends(db_session),
) -> SignalDetail:
    signal = session.scalars(
        select(Signal)
        .options(selectinload(Signal.asset), selectinload(Signal.outcomes))
        .where(Signal.id == signal_id)
    ).one_or_none()
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal không tồn tại")

    return SignalDetail(
        id=signal.id,
        asset_id=signal.asset_id,
        symbol=signal.asset.symbol,
        asset_name=signal.asset.name,
        asset_class=signal.asset.asset_class,  # type: ignore[arg-type]
        ts=signal.ts,
        side=signal.side,  # type: ignore[arg-type]
        tier=signal.tier,  # type: ignore[arg-type]
        confidence=float(signal.confidence),
        price_at_signal=float(signal.price_at_signal),
        entry_window=signal.entry_window,  # type: ignore[arg-type]
        expected_entry_at=signal.expected_entry_at,
        stop_loss=float(signal.stop_loss) if signal.stop_loss is not None else None,
        take_profit=float(signal.take_profit) if signal.take_profit is not None else None,
        indicators=signal.indicators or {},
        news_context=signal.news_context,
        rag_context=signal.rag_context,
        llm_model=signal.llm_model,
        llm_reasoning=signal.llm_reasoning,
        notified=signal.notified,
        notified_at=signal.notified_at,
        user_decision=signal.user_decision,  # type: ignore[arg-type]
        user_decision_at=signal.user_decision_at,
        outcomes=[_outcome_to_out(o) for o in signal.outcomes],
    )


@router.patch("/{signal_id}/user-decision", response_model=SignalDetail)
def patch_user_decision(
    signal_id: int,
    payload: UserDecisionPatch,
    session: Session = Depends(db_session),
) -> SignalDetail:
    if payload.decision is None:
        signal = session.get(Signal, signal_id)
        if signal is None:
            raise HTTPException(status_code=404, detail="Signal không tồn tại")
        signal.user_decision = None
        signal.user_decision_at = None
    else:
        result = set_user_decision(session, signal_id, payload.decision)
        if result is None:
            raise HTTPException(status_code=404, detail="Signal không tồn tại")

    return get_signal(signal_id, session)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _apply_filters(
    stmt,
    symbols: str | None,
    tiers: str | None,
    sides: str | None,
    user_decision: str | None,
    notified: bool | None,
    from_: datetime | None,
    to: datetime | None,
):
    if symbols:
        sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
        if sym_list:
            stmt = stmt.where(Asset.symbol.in_(sym_list))
    if tiers:
        tier_list = [t.strip() for t in tiers.split(",") if t.strip()]
        if tier_list:
            stmt = stmt.where(Signal.tier.in_(tier_list))
    if sides:
        side_list = [s.strip() for s in sides.split(",") if s.strip()]
        if side_list:
            stmt = stmt.where(Signal.side.in_(side_list))
    if user_decision in ("entered", "skipped"):
        stmt = stmt.where(Signal.user_decision == user_decision)
    elif user_decision == "pending":
        stmt = stmt.where(Signal.user_decision.is_(None))
    if notified is True:
        stmt = stmt.where(Signal.notified.is_(True))
    elif notified is False:
        stmt = stmt.where(Signal.notified.is_(False))
    if from_:
        stmt = stmt.where(Signal.ts >= from_)
    if to:
        stmt = stmt.where(Signal.ts <= to)
    return stmt


def _to_list_item(signal: Signal) -> SignalListItem:
    pnls = {HORIZON_KEY[o.horizon_hours]: float(o.pnl_pct)
            for o in signal.outcomes if o.horizon_hours in HORIZON_KEY}

    indicators = signal.indicators or {}
    buy = indicators.get("buy_count")
    sell = indicators.get("sell_count")
    total = indicators.get("indicator_count") or len(indicators.get("votes", []) or [])
    if buy is not None and sell is not None and total:
        dominant = "buy" if buy >= sell else "sell"
        agree = max(buy, sell)
        summary = f"{agree}/{total} {dominant}"
    else:
        summary = "n/a"

    return SignalListItem(
        id=signal.id,
        asset_id=signal.asset_id,
        symbol=signal.asset.symbol,
        ts=signal.ts,
        side=signal.side,  # type: ignore[arg-type]
        tier=signal.tier,  # type: ignore[arg-type]
        confidence=float(signal.confidence),
        price_at_signal=float(signal.price_at_signal),
        entry_window=signal.entry_window,  # type: ignore[arg-type]
        expected_entry_at=signal.expected_entry_at,
        stop_loss=float(signal.stop_loss) if signal.stop_loss is not None else None,
        take_profit=float(signal.take_profit) if signal.take_profit is not None else None,
        notified=signal.notified,
        user_decision=signal.user_decision,  # type: ignore[arg-type]
        indicators_summary=summary,
        pnl_1d=pnls.get("1d"),
        pnl_3d=pnls.get("3d"),
        pnl_7d=pnls.get("7d"),
        pnl_30d=pnls.get("30d"),
    )


def _outcome_to_out(o: Outcome) -> OutcomeOut:
    return OutcomeOut(
        horizon_hours=o.horizon_hours,
        evaluated_at=o.evaluated_at,
        price_then=float(o.price_then),
        pnl_pct=float(o.pnl_pct),
        hit_target=o.hit_target,
        max_favorable=float(o.max_favorable) if o.max_favorable is not None else None,
        max_adverse=float(o.max_adverse) if o.max_adverse is not None else None,
    )
