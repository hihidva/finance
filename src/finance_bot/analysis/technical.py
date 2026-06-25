"""Technical-indicator computation on a daily OHLCV DataFrame.

Mỗi indicator trả về một "vote" {'side': 'buy'|'sell'|'hold', 'strength': 0..1, ...}.
Signal engine ở `analysis/signal.py` sẽ tổng hợp các vote này thành Tier A/B/C.

Tránh phụ thuộc cứng vào `pandas-ta`: code dưới đây tự cài đặt 6 chỉ báo cốt lõi
bằng pandas thuần, để khi pandas-ta upgrade ta không bị vỡ.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

Side = Literal["buy", "sell", "hold"]


# ----------------------------------------------------------------------
# Indicator math (pure pandas)
# ----------------------------------------------------------------------
def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / length, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / length, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
         ) -> tuple[pd.Series, pd.Series, pd.Series]:
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def bollinger(close: pd.Series, length: int = 20, mult: float = 2.0
              ) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = close.rolling(length).mean()
    std = close.rolling(length).std()
    upper = mid + mult * std
    lower = mid - mult * std
    return lower, mid, upper


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat(
        [(h - l), (h - prev_c).abs(), (l - prev_c).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()


def ichimoku(
    df: pd.DataFrame,
    tenkan_period: int = 9,
    kijun_period: int = 26,
    senkou_b_period: int = 52,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Ichimoku Cloud components — tenkan, kijun, senkou_a, senkou_b (NON-shifted).

    Note: senkou A/B đã KHÔNG dịch về tương lai 26 bar. Vote function dưới đây so
    sánh close[-1] với senkou A/B[-1] (cloud "hiện tại" tính trên dữ liệu hiện tại),
    không phải cloud projected ahead.
    """
    h, low = df["high"], df["low"]
    tenkan = (h.rolling(tenkan_period).max() + low.rolling(tenkan_period).min()) / 2
    kijun = (h.rolling(kijun_period).max() + low.rolling(kijun_period).min()) / 2
    senkou_a = (tenkan + kijun) / 2
    senkou_b = (h.rolling(senkou_b_period).max() + low.rolling(senkou_b_period).min()) / 2
    return tenkan, kijun, senkou_a, senkou_b


def adx(df: pd.DataFrame, length: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Wilder ADX — return (adx, plus_di, minus_di)."""
    h, low, c = df["high"], df["low"], df["close"]
    up_move = h.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    prev_c = c.shift(1)
    tr = pd.concat(
        [(h - low), (h - prev_c).abs(), (low - prev_c).abs()],
        axis=1,
    ).max(axis=1)

    alpha = 1.0 / length
    tr_smooth = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_dm_smooth = plus_dm.ewm(alpha=alpha, adjust=False).mean()
    minus_dm_smooth = minus_dm.ewm(alpha=alpha, adjust=False).mean()

    plus_di = 100 * plus_dm_smooth / tr_smooth.replace(0, np.nan)
    minus_di = 100 * minus_dm_smooth / tr_smooth.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_val = dx.ewm(alpha=alpha, adjust=False).mean()
    return adx_val, plus_di, minus_di


def supertrend(
    df: pd.DataFrame, length: int = 10, multiplier: float = 3.0
) -> tuple[pd.Series, pd.Series]:
    """Return (supertrend_line, direction). direction ∈ {1: uptrend, -1: downtrend}."""
    hl2 = (df["high"] + df["low"]) / 2
    a = atr(df, length)
    upper_basic = hl2 + multiplier * a
    lower_basic = hl2 - multiplier * a

    upper = upper_basic.copy()
    lower = lower_basic.copy()
    direction = pd.Series(1, index=df.index, dtype=int)
    st = pd.Series(np.nan, index=df.index, dtype=float)

    close = df["close"]
    for i in range(1, len(df)):
        # Final upper band carries forward unless basic upper is lower
        # or prior close broke above it.
        if upper_basic.iloc[i] < upper.iloc[i - 1] or close.iloc[i - 1] > upper.iloc[i - 1]:
            upper.iloc[i] = upper_basic.iloc[i]
        else:
            upper.iloc[i] = upper.iloc[i - 1]

        if lower_basic.iloc[i] > lower.iloc[i - 1] or close.iloc[i - 1] < lower.iloc[i - 1]:
            lower.iloc[i] = lower_basic.iloc[i]
        else:
            lower.iloc[i] = lower.iloc[i - 1]

        prev_st = st.iloc[i - 1]
        prev_dir = direction.iloc[i - 1]
        if pd.isna(prev_st):
            direction.iloc[i] = 1 if close.iloc[i] > upper_basic.iloc[i] else -1
        elif prev_dir == 1:
            direction.iloc[i] = -1 if close.iloc[i] < lower.iloc[i] else 1
        else:
            direction.iloc[i] = 1 if close.iloc[i] > upper.iloc[i] else -1

        st.iloc[i] = lower.iloc[i] if direction.iloc[i] == 1 else upper.iloc[i]

    return st, direction


def obv(df: pd.DataFrame) -> pd.Series:
    """Cumulative On-Balance Volume."""
    direction = np.sign(df["close"].diff().fillna(0))
    return (direction * df["volume"]).cumsum()


def donchian(
    df: pd.DataFrame, length: int = 20
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return (upper, mid, lower) — rolling max/min của high/low."""
    upper = df["high"].rolling(length).max()
    lower = df["low"].rolling(length).min()
    mid = (upper + lower) / 2
    return upper, mid, lower


def mfi(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """Money Flow Index — RSI tích hợp volume.

    Typical Price → Raw Money Flow → tách Positive/Negative theo TP diff
    → rolling sum theo `length` → MFI = 100 - 100/(1 + ratio).
    """
    tp = (df["high"] + df["low"] + df["close"]) / 3
    rmf = tp * df["volume"]
    tp_diff = tp.diff()
    positive_mf = rmf.where(tp_diff > 0, 0.0)
    negative_mf = rmf.where(tp_diff < 0, 0.0)
    pos_sum = positive_mf.rolling(length).sum()
    neg_sum = negative_mf.rolling(length).sum()
    ratio = pos_sum / neg_sum.replace(0, np.nan)
    return 100 - (100 / (1 + ratio))


def chaikin_money_flow(df: pd.DataFrame, length: int = 20) -> pd.Series:
    """Chaikin Money Flow — Σ(MFV, length) / Σ(volume, length), range [-1, +1].

    MFV (Money Flow Volume) = ((C - L) - (H - C)) / (H - L) × volume.
    """
    high_low = (df["high"] - df["low"]).replace(0, np.nan)
    mfm = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / high_low
    mfv = mfm * df["volume"]
    return mfv.rolling(length).sum() / df["volume"].rolling(length).sum()


def psar(
    df: pd.DataFrame,
    af_init: float = 0.02,
    af_step: float = 0.02,
    af_max: float = 0.20,
) -> tuple[pd.Series, pd.Series]:
    """Parabolic SAR (Wilder). Return (sar_line, trend). trend ∈ {1: up, -1: down}.

    SAR là trailing stop: trend up khi giá nằm trên SAR, trend down khi dưới.
    Khi giá xuyên qua SAR → đảo trend (flip) + reset acceleration factor.
    """
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    n = len(df)
    sar = np.full(n, np.nan)
    trend = np.ones(n, dtype=int)
    if n < 2:
        return pd.Series(sar, index=df.index), pd.Series(trend, index=df.index)

    # Khởi tạo: đoán trend từ 2 bar đầu.
    up = high[1] >= high[0]
    cur_trend = 1 if up else -1
    ep = high[1] if up else low[1]          # extreme point
    af = af_init
    sar[0] = low[0] if up else high[0]
    sar[1] = sar[0]

    for i in range(1, n):
        prior_sar = sar[i - 1]
        new_sar = prior_sar + af * (ep - prior_sar)

        if cur_trend == 1:  # uptrend
            # SAR không được vượt low của 2 bar gần nhất.
            new_sar = min(new_sar, low[i - 1], low[i - 2] if i >= 2 else low[i - 1])
            if high[i] > ep:
                ep = high[i]
                af = min(af + af_step, af_max)
            if low[i] < new_sar:  # đảo chiều sang downtrend
                cur_trend = -1
                new_sar = ep
                ep = low[i]
                af = af_init
        else:  # downtrend
            # SAR không được thấp hơn high của 2 bar gần nhất.
            new_sar = max(new_sar, high[i - 1], high[i - 2] if i >= 2 else high[i - 1])
            if low[i] < ep:
                ep = low[i]
                af = min(af + af_step, af_max)
            if high[i] > new_sar:  # đảo chiều sang uptrend
                cur_trend = 1
                new_sar = ep
                ep = high[i]
                af = af_init

        sar[i] = new_sar
        trend[i] = cur_trend

    return pd.Series(sar, index=df.index), pd.Series(trend, index=df.index)


# ----------------------------------------------------------------------
# Vote dataclass
# ----------------------------------------------------------------------
@dataclass
class Vote:
    name: str
    side: Side
    strength: float           # 0..1
    detail: dict = field(default_factory=dict)


# ----------------------------------------------------------------------
# Individual indicator votes (1D timeframe)
# ----------------------------------------------------------------------
def vote_rsi(close: pd.Series) -> Vote:
    r = rsi(close, 14).iloc[-1]
    prev = rsi(close, 14).iloc[-2]
    if pd.isna(r):
        return Vote("RSI14", "hold", 0.0, {"rsi": None})
    if r < 30 and r > prev:
        return Vote("RSI14", "buy", min(1.0, (35 - r) / 10 + 0.4), {"rsi": float(r)})
    if r > 70 and r < prev:
        return Vote("RSI14", "sell", min(1.0, (r - 65) / 10 + 0.4), {"rsi": float(r)})
    if 50 < r < 65 and r > prev:
        return Vote("RSI14", "buy", 0.45, {"rsi": float(r)})
    if 35 < r < 50 and r < prev:
        return Vote("RSI14", "sell", 0.45, {"rsi": float(r)})
    return Vote("RSI14", "hold", 0.2, {"rsi": float(r)})


def vote_macd(close: pd.Series) -> Vote:
    macd_line, sig_line, hist = macd(close)
    h_now, h_prev = hist.iloc[-1], hist.iloc[-2]
    if pd.isna(h_now) or pd.isna(h_prev):
        return Vote("MACD", "hold", 0.0)
    crossed_up = h_prev <= 0 < h_now
    crossed_down = h_prev >= 0 > h_now
    if crossed_up:
        return Vote("MACD", "buy", 0.8, {"hist": float(h_now)})
    if crossed_down:
        return Vote("MACD", "sell", 0.8, {"hist": float(h_now)})
    if h_now > 0 and h_now > h_prev:
        return Vote("MACD", "buy", 0.5, {"hist": float(h_now)})
    if h_now < 0 and h_now < h_prev:
        return Vote("MACD", "sell", 0.5, {"hist": float(h_now)})
    return Vote("MACD", "hold", 0.2, {"hist": float(h_now)})


def vote_ema_cross(close: pd.Series, fast: int = 20, slow: int = 50) -> Vote:
    ef = ema(close, fast)
    es = ema(close, slow)
    diff_now = ef.iloc[-1] - es.iloc[-1]
    diff_prev = ef.iloc[-2] - es.iloc[-2]
    if pd.isna(diff_now) or pd.isna(diff_prev):
        return Vote(f"EMA{fast}/{slow}", "hold", 0.0)
    if diff_prev <= 0 < diff_now:
        return Vote(f"EMA{fast}/{slow}", "buy", 0.85, {"diff": float(diff_now)})
    if diff_prev >= 0 > diff_now:
        return Vote(f"EMA{fast}/{slow}", "sell", 0.85, {"diff": float(diff_now)})
    if diff_now > 0:
        return Vote(f"EMA{fast}/{slow}", "buy", 0.4, {"diff": float(diff_now)})
    return Vote(f"EMA{fast}/{slow}", "sell", 0.4, {"diff": float(diff_now)})


def vote_long_term_trend(close: pd.Series) -> Vote:
    """EMA50 vs EMA200 = bộ lọc xu hướng dài hạn."""
    return vote_ema_cross(close, 50, 200)


def vote_bollinger(close: pd.Series) -> Vote:
    lower, mid, upper = bollinger(close)
    c = close.iloc[-1]
    l, u, m = lower.iloc[-1], upper.iloc[-1], mid.iloc[-1]
    if any(pd.isna(x) for x in (c, l, u, m)):
        return Vote("BB20", "hold", 0.0)
    width = (u - l) / m if m else 0
    # Touch lower band + close inside = mua đáy
    prev_c = close.iloc[-2]
    prev_l = lower.iloc[-2]
    if prev_c <= prev_l and c > l:
        return Vote("BB20", "buy", 0.7, {"width": float(width)})
    if prev_c >= upper.iloc[-2] and c < u:
        return Vote("BB20", "sell", 0.7, {"width": float(width)})
    return Vote("BB20", "hold", 0.2, {"width": float(width)})


def vote_volume(df: pd.DataFrame, length: int = 20) -> Vote:
    vol = df["volume"]
    close = df["close"]
    avg = vol.rolling(length).mean().iloc[-1]
    v_now = vol.iloc[-1]
    if pd.isna(avg) or avg == 0:
        return Vote("VOL", "hold", 0.0)
    spike = v_now / avg
    direction: Side = "buy" if close.iloc[-1] > close.iloc[-2] else "sell"
    if spike >= 1.8:
        return Vote("VOL", direction, min(1.0, 0.4 + (spike - 1.8) / 4),
                    {"spike": float(spike)})
    return Vote("VOL", "hold", 0.2, {"spike": float(spike)})


def vote_atr_breakout(df: pd.DataFrame) -> Vote:
    """Phát hiện cây nến breakout: |close - prev_close| > 1.5*ATR."""
    a = atr(df, 14)
    if pd.isna(a.iloc[-1]):
        return Vote("ATR_BO", "hold", 0.0)
    move = df["close"].iloc[-1] - df["close"].iloc[-2]
    threshold = 1.5 * a.iloc[-1]
    if move > threshold:
        return Vote("ATR_BO", "buy", 0.7, {"move": float(move), "atr": float(a.iloc[-1])})
    if move < -threshold:
        return Vote("ATR_BO", "sell", 0.7, {"move": float(move), "atr": float(a.iloc[-1])})
    return Vote("ATR_BO", "hold", 0.1, {"move": float(move), "atr": float(a.iloc[-1])})


def vote_ichimoku(df: pd.DataFrame) -> Vote:
    """Ichimoku Cloud vote — kết hợp 3 tín hiệu: Kumo position, Tenkan/Kijun cross, momentum.

    - Bullish strong: close > Kumo (both senkou A & B) + Tenkan > Kijun + cross up
    - Bullish weak: close > Kumo + Tenkan > Kijun (đã trên Kumo nhiều phiên)
    - Bearish strong: close < Kumo + Tenkan < Kijun + cross down
    - Bearish weak: close < Kumo + Tenkan < Kijun
    - Mixed (giá trong Kumo): hold
    """
    tenkan, kijun, senkou_a, senkou_b = ichimoku(df)
    close = df["close"]
    c, t, k, sa, sb = (
        close.iloc[-1], tenkan.iloc[-1], kijun.iloc[-1], senkou_a.iloc[-1], senkou_b.iloc[-1]
    )
    if any(pd.isna(x) for x in (t, k, sa, sb)):
        return Vote("ICHIMOKU", "hold", 0.0, {"insufficient_data": True})

    kumo_top = max(sa, sb)
    kumo_bot = min(sa, sb)
    diff_now = t - k
    diff_prev = tenkan.iloc[-2] - kijun.iloc[-2]
    crossed_up = pd.notna(diff_prev) and diff_prev <= 0 < diff_now
    crossed_down = pd.notna(diff_prev) and diff_prev >= 0 > diff_now

    detail = {
        "close": float(c),
        "tenkan": float(t),
        "kijun": float(k),
        "senkou_a": float(sa),
        "senkou_b": float(sb),
        "kumo_top": float(kumo_top),
        "kumo_bot": float(kumo_bot),
    }

    if c > kumo_top and t > k:
        return Vote("ICHIMOKU", "buy", 0.85 if crossed_up else 0.55, detail)
    if c < kumo_bot and t < k:
        return Vote("ICHIMOKU", "sell", 0.85 if crossed_down else 0.55, detail)
    return Vote("ICHIMOKU", "hold", 0.2, detail)


def vote_adx(df: pd.DataFrame) -> Vote:
    """ADX vote — chỉ vote buy/sell khi xu hướng đủ mạnh (ADX > 25)."""
    if len(df) < 28:
        return Vote("ADX", "hold", 0.0, {"insufficient_data": True})
    adx_val, plus_di, minus_di = adx(df, 14)
    a, p, m = adx_val.iloc[-1], plus_di.iloc[-1], minus_di.iloc[-1]
    if any(pd.isna(x) for x in (a, p, m)):
        return Vote("ADX", "hold", 0.0, {"insufficient_data": True})

    detail = {"adx": float(a), "plus_di": float(p), "minus_di": float(m)}
    if a < 20:
        return Vote("ADX", "hold", 0.1, detail | {"reason": "sideways"})
    if a < 25:
        return Vote("ADX", "hold", 0.2, detail | {"reason": "weak_trend"})

    strength = min(1.0, 0.4 + (a - 25) / 50)
    if p > m:
        return Vote("ADX", "buy", strength, detail)
    if m > p:
        return Vote("ADX", "sell", strength, detail)
    return Vote("ADX", "hold", 0.2, detail)


def vote_supertrend(df: pd.DataFrame) -> Vote:
    """Supertrend vote — flip mới mạnh nhất, hold-trend yếu hơn."""
    if len(df) < 14:
        return Vote("SUPERTREND", "hold", 0.0, {"insufficient_data": True})
    st_line, direction = supertrend(df, length=10, multiplier=3.0)
    if pd.isna(st_line.iloc[-1]):
        return Vote("SUPERTREND", "hold", 0.0, {"insufficient_data": True})

    dir_now = int(direction.iloc[-1])
    dir_prev = int(direction.iloc[-2]) if pd.notna(direction.iloc[-2]) else dir_now
    flipped = dir_now != dir_prev
    detail = {
        "supertrend": float(st_line.iloc[-1]),
        "direction": dir_now,
        "flipped": flipped,
    }

    if dir_now == 1:
        return Vote("SUPERTREND", "buy", 0.85 if flipped else 0.45, detail)
    return Vote("SUPERTREND", "sell", 0.85 if flipped else 0.45, detail)


def vote_psar(df: pd.DataFrame) -> Vote:
    """Parabolic SAR vote — flip mới mạnh nhất, trend đang chạy yếu hơn."""
    if len(df) < 10:
        return Vote("PSAR", "hold", 0.0, {"insufficient_data": True})
    sar_line, trend = psar(df)
    if pd.isna(sar_line.iloc[-1]):
        return Vote("PSAR", "hold", 0.0, {"insufficient_data": True})

    dir_now = int(trend.iloc[-1])
    dir_prev = int(trend.iloc[-2]) if pd.notna(trend.iloc[-2]) else dir_now
    flipped = dir_now != dir_prev
    detail = {
        "psar": float(sar_line.iloc[-1]),
        "close": float(df["close"].iloc[-1]),
        "direction": dir_now,
        "flipped": flipped,
    }

    if dir_now == 1:
        return Vote("PSAR", "buy", 0.85 if flipped else 0.45, detail)
    return Vote("PSAR", "sell", 0.85 if flipped else 0.45, detail)


def vote_obv(df: pd.DataFrame, lookback: int = 20) -> Vote:
    """OBV vote — bắt divergence + confirmation trên window `lookback` bars."""
    obv_series = obv(df)
    if len(obv_series) < lookback + 1 or pd.isna(obv_series.iloc[-1]):
        return Vote("OBV", "hold", 0.0, {"insufficient_data": True})

    obv_now = obv_series.iloc[-1]
    obv_then = obv_series.iloc[-lookback - 1]
    price_now = df["close"].iloc[-1]
    price_then = df["close"].iloc[-lookback - 1]
    obv_up = obv_now > obv_then
    price_up = price_now > price_then

    detail = {
        "obv_now": float(obv_now),
        "obv_change": float(obv_now - obv_then),
        "price_change_pct": float((price_now - price_then) / price_then * 100)
        if price_then else 0.0,
        "lookback": lookback,
    }

    if obv_up and price_up:
        return Vote("OBV", "buy", 0.5, detail | {"signal": "confirmation_up"})
    if obv_up and not price_up:
        return Vote("OBV", "buy", 0.75, detail | {"signal": "bullish_divergence"})
    if not obv_up and not price_up:
        return Vote("OBV", "sell", 0.5, detail | {"signal": "confirmation_down"})
    return Vote("OBV", "sell", 0.75, detail | {"signal": "bearish_divergence"})


def vote_donchian(df: pd.DataFrame, length: int = 20) -> Vote:
    """Donchian breakout — vote buy khi close phá upper của (length) bars trước, ngược lại."""
    upper, mid, lower = donchian(df, length)
    if len(df) < length + 1 or pd.isna(upper.iloc[-2]) or pd.isna(lower.iloc[-2]):
        return Vote("DONCHIAN", "hold", 0.0, {"insufficient_data": True})

    c = df["close"].iloc[-1]
    prev_upper = upper.iloc[-2]
    prev_lower = lower.iloc[-2]
    detail = {
        "close": float(c),
        "prev_upper": float(prev_upper),
        "prev_lower": float(prev_lower),
        "length": length,
    }

    if c > prev_upper:
        breakout_pct = (c - prev_upper) / prev_upper if prev_upper else 0
        return Vote(
            "DONCHIAN", "buy", min(1.0, 0.6 + breakout_pct * 10),
            detail | {"signal": "breakout_up"},
        )
    if c < prev_lower:
        breakout_pct = (prev_lower - c) / prev_lower if prev_lower else 0
        return Vote(
            "DONCHIAN", "sell", min(1.0, 0.6 + breakout_pct * 10),
            detail | {"signal": "breakout_down"},
        )
    return Vote("DONCHIAN", "hold", 0.15, detail | {"signal": "inside_channel"})


def vote_mfi(df: pd.DataFrame, length: int = 14) -> Vote:
    """MFI vote — oversold reversal (<20 + hồi) hoặc overbought reversal (>80 + giảm)."""
    if len(df) < length + 2:
        return Vote("MFI", "hold", 0.0, {"insufficient_data": True})
    mfi_series = mfi(df, length)
    m_now, m_prev = mfi_series.iloc[-1], mfi_series.iloc[-2]
    if pd.isna(m_now) or pd.isna(m_prev):
        return Vote("MFI", "hold", 0.0, {"insufficient_data": True})

    detail = {"mfi": float(m_now), "mfi_prev": float(m_prev), "length": length}

    if m_now < 20 and m_now > m_prev:
        return Vote("MFI", "buy", min(1.0, (25 - m_now) / 15 + 0.5), detail)
    if m_now > 80 and m_now < m_prev:
        return Vote("MFI", "sell", min(1.0, (m_now - 75) / 15 + 0.5), detail)
    if 50 < m_now < 65 and m_now > m_prev:
        return Vote("MFI", "buy", 0.45, detail)
    if 35 < m_now < 50 and m_now < m_prev:
        return Vote("MFI", "sell", 0.45, detail)
    return Vote("MFI", "hold", 0.2, detail)


def vote_cmf(df: pd.DataFrame, length: int = 20) -> Vote:
    """CMF vote — dòng tiền tích lũy/phân phối; |CMF| > 0.05 là tín hiệu rõ."""
    if len(df) < length + 1:
        return Vote("CMF", "hold", 0.0, {"insufficient_data": True})
    cmf_series = chaikin_money_flow(df, length)
    c_now, c_prev = cmf_series.iloc[-1], cmf_series.iloc[-2]
    if pd.isna(c_now) or pd.isna(c_prev):
        return Vote("CMF", "hold", 0.0, {"insufficient_data": True})

    detail = {"cmf": float(c_now), "cmf_prev": float(c_prev), "length": length}

    if c_now > 0.05 and c_now > c_prev:
        return Vote("CMF", "buy", min(1.0, 0.4 + abs(c_now) * 4), detail)
    if c_now < -0.05 and c_now < c_prev:
        return Vote("CMF", "sell", min(1.0, 0.4 + abs(c_now) * 4), detail)
    if c_now > 0.05:
        return Vote("CMF", "buy", 0.35, detail)
    if c_now < -0.05:
        return Vote("CMF", "sell", 0.35, detail)
    return Vote("CMF", "hold", 0.2, detail)


# ----------------------------------------------------------------------
# Aggregator
# ----------------------------------------------------------------------
INDICATORS = (
    "RSI14", "MACD", "EMA20/50", "EMA50/200", "BB20", "VOL", "ATR_BO",
    "ICHIMOKU", "ADX", "SUPERTREND", "PSAR", "OBV", "DONCHIAN", "MFI", "CMF",
)


@dataclass
class TechSnapshot:
    last_close: float
    atr_value: float
    votes: list[Vote]

    @property
    def buy_count(self) -> int:
        return sum(1 for v in self.votes if v.side == "buy")

    @property
    def sell_count(self) -> int:
        return sum(1 for v in self.votes if v.side == "sell")

    @property
    def dominant_side(self) -> Side:
        if self.buy_count > self.sell_count:
            return "buy"
        if self.sell_count > self.buy_count:
            return "sell"
        return "hold"

    @property
    def agree_count(self) -> int:
        return max(self.buy_count, self.sell_count)


def compute_snapshot(df: pd.DataFrame) -> TechSnapshot:
    """Run all indicators on a 1D OHLCV frame sorted ascending by ts."""
    if len(df) < 60:
        raise ValueError(f"Need >=60 daily bars, got {len(df)}")

    close = df["close"]
    votes = [
        vote_rsi(close),
        vote_macd(close),
        vote_ema_cross(close, 20, 50),
        vote_long_term_trend(close),
        vote_bollinger(close),
        vote_volume(df),
        vote_atr_breakout(df),
        vote_ichimoku(df),
        vote_adx(df),
        vote_supertrend(df),
        vote_psar(df),
        vote_obv(df),
        vote_donchian(df),
        vote_mfi(df),
        vote_cmf(df),
    ]
    return TechSnapshot(
        last_close=float(close.iloc[-1]),
        atr_value=float(atr(df, 14).iloc[-1]),
        votes=votes,
    )
