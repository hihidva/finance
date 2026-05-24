"""Computed indicator series — reuses analysis/technical.py."""
from __future__ import annotations

import math

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from finance_bot.analysis.technical import atr, bollinger, ema, macd, rsi
from finance_bot.db.queries import load_ohlcv_df
from finance_bot.web.api.prices import _resolve_asset
from finance_bot.web.deps import db_session
from finance_bot.web.schemas import IndicatorSeries, IndicatorsResponse

router = APIRouter()

AVAILABLE = {
    "ema20", "ema50", "ema200",
    "bb_upper", "bb_mid", "bb_lower",
    "rsi14",
    "macd_line", "macd_signal", "macd_hist",
    "atr14",
    "vol_ma20",
}


@router.get("/{symbol}", response_model=IndicatorsResponse)
def get_indicators(
    symbol: str,
    names: str = Query("ema20,ema50,ema200,rsi14",
                       description="Comma-separated indicator names"),
    timeframe: str = Query("1d"),
    lookback: int = Query(180, ge=60),
    session: Session = Depends(db_session),
) -> IndicatorsResponse:
    requested = [n.strip() for n in names.split(",") if n.strip()]
    unknown = [n for n in requested if n not in AVAILABLE]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail={
                "message": f"Unknown indicators: {unknown}",
                "available": sorted(AVAILABLE),
            },
        )

    asset = _resolve_asset(session, symbol)
    # Need extra bars at the head for indicator warm-up (esp. EMA200).
    df = load_ohlcv_df(session, asset.id, timeframe, limit=lookback + 220)
    if df.empty:
        return IndicatorsResponse(
            symbol=asset.symbol,
            timeframe=timeframe,
            series=[],
            available=sorted(AVAILABLE),
        )

    series_by_name = _compute(df, requested)

    # Trim to last `lookback` bars so payload size stays bounded.
    tail_index = df.index[-lookback:]
    series_out = [
        IndicatorSeries(
            name=n,
            values=[(ts, _safe_float(series.get(ts))) for ts in tail_index],
        )
        for n, series in series_by_name.items()
    ]

    return IndicatorsResponse(
        symbol=asset.symbol,
        timeframe=timeframe,
        series=series_out,
        available=sorted(AVAILABLE),
    )


def _compute(df: pd.DataFrame, names: list[str]) -> dict[str, pd.Series]:
    out: dict[str, pd.Series] = {}
    close = df["close"]

    if "ema20" in names:
        out["ema20"] = ema(close, 20)
    if "ema50" in names:
        out["ema50"] = ema(close, 50)
    if "ema200" in names:
        out["ema200"] = ema(close, 200)

    if any(n.startswith("bb_") for n in names):
        lo, mid, up = bollinger(close, 20, 2.0)
        if "bb_lower" in names:
            out["bb_lower"] = lo
        if "bb_mid" in names:
            out["bb_mid"] = mid
        if "bb_upper" in names:
            out["bb_upper"] = up

    if "rsi14" in names:
        out["rsi14"] = rsi(close, 14)

    if any(n.startswith("macd_") for n in names):
        line, signal_line, hist = macd(close)
        if "macd_line" in names:
            out["macd_line"] = line
        if "macd_signal" in names:
            out["macd_signal"] = signal_line
        if "macd_hist" in names:
            out["macd_hist"] = hist

    if "atr14" in names:
        out["atr14"] = atr(df, 14)

    if "vol_ma20" in names:
        out["vol_ma20"] = df["volume"].rolling(20).mean()

    return out


def _safe_float(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f
