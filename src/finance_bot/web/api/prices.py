"""OHLCV endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from finance_bot.db.models import Asset
from finance_bot.db.queries import load_ohlcv_df
from finance_bot.web.deps import db_session
from finance_bot.web.schemas import Candle, PricesResponse

router = APIRouter()

MAX_LOOKBACK = 730


@router.get("/{symbol}", response_model=PricesResponse)
def get_prices(
    symbol: str,
    response: Response,
    timeframe: str = Query("1d"),
    lookback: int = Query(180, ge=10),
    session: Session = Depends(db_session),
) -> PricesResponse:
    asset = _resolve_asset(session, symbol)

    clamped = False
    if lookback > MAX_LOOKBACK:
        lookback = MAX_LOOKBACK
        clamped = True

    df = load_ohlcv_df(session, asset.id, timeframe, limit=lookback)
    candles = [
        Candle(
            ts=ts,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
        )
        for ts, row in df.iterrows()
    ]

    if clamped:
        response.headers["X-Lookback-Clamped"] = "true"

    return PricesResponse(
        symbol=asset.symbol,
        asset_class=asset.asset_class,  # type: ignore[arg-type]
        timeframe=timeframe,
        candles=candles,
    )


def _resolve_asset(session: Session, symbol: str) -> Asset:
    """Look up Asset by symbol — try exact, fall back to upper-case match."""
    from sqlalchemy import select

    asset = session.scalars(select(Asset).where(Asset.symbol == symbol)).one_or_none()
    if asset is None:
        asset = session.scalars(
            select(Asset).where(Asset.symbol == symbol.upper())
        ).one_or_none()
    if asset is None:
        raise HTTPException(
            status_code=404,
            detail=f"Symbol {symbol!r} chưa có trong assets — chạy sync-prices trước.",
        )
    return asset
