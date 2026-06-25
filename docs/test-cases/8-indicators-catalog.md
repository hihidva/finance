# Test Cases — Module 8: Indicators Catalog

> Test framework: `pytest` (theo `pyproject.toml`, `asyncio_mode=auto`, `testpaths=tests`).
> Đặt test mới vào `tests/analysis/test_technical_*.py` — 1 file 1 indicator (hoặc 1 group nhỏ).
> Mọi test phải chạy được offline (không cần MySQL / Claude CLI / network). Dùng fixture DataFrame synthetic.

## Fixture chuẩn

```python
# tests/analysis/conftest.py (đề xuất)
import pandas as pd
import numpy as np

def build_ohlcv(n=200, start_price=100.0, trend=0.0, noise=1.0, seed=42):
    """
    n: số bar.
    trend: drift per bar (>0 uptrend, <0 downtrend, 0 sideways).
    noise: std-dev của random walk.
    """
    rng = np.random.default_rng(seed)
    closes = start_price + np.cumsum(rng.normal(loc=trend, scale=noise, size=n))
    highs = closes + np.abs(rng.normal(0, noise/2, n))
    lows = closes - np.abs(rng.normal(0, noise/2, n))
    opens = closes + rng.normal(0, noise/4, n)
    vols = rng.integers(100_000, 500_000, n).astype(float)
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows, "close": closes, "volume": vols
    })
```

---

## TC-IDC-01: Vote contract — tất cả vote function trả Vote dataclass

- **Scope:** Tất cả `vote_*` function trong [analysis/technical.py](../../src/finance_bot/analysis/technical.py)
- **Precondition:** Có fixture `build_ohlcv(200)` trả OHLCV 200 bars hợp lệ.
- **Steps:**
  1. Generate `df = build_ohlcv(200)`.
  2. Với mỗi `fn` trong `[vote_rsi, vote_macd, vote_ema_cross, vote_long_term_trend, vote_bollinger, vote_volume, vote_atr_breakout]`, call function (truyền `df["close"]` hoặc `df` theo signature).
  3. Verify trả về `Vote` instance.
- **Expected:**
  - `isinstance(result, Vote) is True`
  - `result.side in {"buy", "sell", "hold"}`
  - `0.0 <= result.strength <= 1.0`
  - `isinstance(result.detail, dict)`
  - `result.name` là string non-empty
  - `result.detail` JSON-serializable: `json.dumps(result.detail)` không raise

## TC-IDC-02: Insufficient data → vote hold, strength 0.0 (không raise)

- **Scope:** Mọi vote function khi input ngắn hơn `min_bars`
- **Precondition:** Fixture `build_ohlcv(5)` trả 5 bars (dưới min của mọi indicator).
- **Steps:**
  1. Generate `df = build_ohlcv(5)`.
  2. Call từng `vote_*` function.
- **Expected:**
  - Không raise exception nào (kể cả `IndexError`, `ZeroDivisionError`).
  - `result.side == "hold"`
  - `result.strength == 0.0`

## TC-IDC-03: `compute_snapshot` raise khi df < 60 bars

- **Scope:** `compute_snapshot()` guard
- **Precondition:** `df = build_ohlcv(59)`
- **Steps:**
  1. Call `compute_snapshot(df)`.
- **Expected:**
  - `ValueError` được raise với message chứa `"Need >=60 daily bars"`.

## TC-IDC-04: TechSnapshot aggregation — dominant_side correctness

- **Scope:** `TechSnapshot.dominant_side`, `agree_count`
- **Precondition:** Mock `Vote` list với 4 buy + 2 sell + 1 hold.
- **Steps:**
  1. Tạo `snap = TechSnapshot(last_close=100, atr_value=1.0, votes=[Vote("a","buy",0.5), Vote("b","buy",0.5), Vote("c","buy",0.5), Vote("d","buy",0.5), Vote("e","sell",0.5), Vote("f","sell",0.5), Vote("g","hold",0.0)])`.
  2. Đọc `snap.buy_count`, `snap.sell_count`, `snap.dominant_side`, `snap.agree_count`.
- **Expected:**
  - `buy_count == 4`, `sell_count == 2`
  - `dominant_side == "buy"`
  - `agree_count == 4`

## TC-IDC-05: TechSnapshot dominant_side = hold khi tie

- **Scope:** Tie-break logic
- **Precondition:** Mock list 3 buy + 3 sell + 1 hold.
- **Steps:**
  1. Build TechSnapshot với 3 buy + 3 sell.
  2. Đọc `dominant_side`.
- **Expected:**
  - `dominant_side == "hold"` (tie → hold)
  - `agree_count == 3`

## TC-IDC-06: RSI14 — uptrend mạnh phải vote buy (hoặc hold), không bao giờ sell

- **Scope:** `vote_rsi`
- **Precondition:** OHLCV uptrend rõ rệt `build_ohlcv(100, trend=+0.5, noise=0.3)`.
- **Steps:**
  1. Generate df uptrend.
  2. Call `vote_rsi(df["close"])`.
- **Expected:**
  - `vote.side in {"buy", "hold"}` (không bao giờ `"sell"`)
  - `vote.detail["rsi"]` là float trong khoảng (0, 100)

## TC-IDC-07: RSI14 — downtrend mạnh phải vote sell (hoặc hold)

- **Scope:** `vote_rsi`
- **Precondition:** `build_ohlcv(100, trend=-0.5, noise=0.3)`.
- **Steps:**
  1. Generate df downtrend.
  2. Call `vote_rsi(df["close"])`.
- **Expected:**
  - `vote.side in {"sell", "hold"}`

## TC-IDC-08: MACD — vote.side buy khi histogram cross up

- **Scope:** `vote_macd`
- **Precondition:** Construct close series: 30 bar đầu giảm, 10 bar cuối tăng mạnh (force histogram cross qua 0 từ âm → dương).
- **Steps:**
  1. Build series synthetic.
  2. Call `vote_macd(series)`.
- **Expected:**
  - `vote.side == "buy"`
  - `vote.strength >= 0.5`
  - `vote.detail["hist"] > 0`

## TC-IDC-09: EMA cross 20/50 — golden cross vote buy

- **Scope:** `vote_ema_cross(close, 20, 50)`
- **Precondition:** Series sideways 60 bar → trending up 10 bar (đẩy EMA20 cross EMA50 lên).
- **Steps:**
  1. Build series synthetic.
  2. Call `vote_ema_cross(series, 20, 50)`.
- **Expected:**
  - `vote.side == "buy"`
  - `vote.strength >= 0.4`
  - `vote.detail["diff"] > 0`

## TC-IDC-10: Bollinger — touch lower band vote buy

- **Scope:** `vote_bollinger`
- **Precondition:** Series gradient up → drop sharp ở bar -2 chạm lower band, bar -1 hồi lại trong band.
- **Steps:**
  1. Build series; manually verify bar -2 ≤ lower band & bar -1 > lower band.
  2. Call `vote_bollinger(series)`.
- **Expected:**
  - `vote.side == "buy"`
  - `vote.strength == 0.7`

## TC-IDC-11: Volume spike + close up → vote buy

- **Scope:** `vote_volume`
- **Precondition:** OHLCV với volume[-1] = 2.0 × avg, close[-1] > close[-2].
- **Steps:**
  1. Build df, override `df.loc[df.index[-1], "volume"] = avg * 2.0`.
  2. Override `df.loc[df.index[-1], "close"] = df["close"].iloc[-2] + 1.0`.
  3. Call `vote_volume(df)`.
- **Expected:**
  - `vote.side == "buy"`
  - `vote.detail["spike"] >= 1.8`

## TC-IDC-12: Volume spike + close down → vote sell

- **Scope:** `vote_volume`
- **Precondition:** Như TC-IDC-11 nhưng `close[-1] < close[-2]`.
- **Expected:**
  - `vote.side == "sell"`

## TC-IDC-13: ATR breakout — big up move → buy

- **Scope:** `vote_atr_breakout`
- **Precondition:** OHLCV bình thường + override `close[-1] = close[-2] + 3 × ATR`.
- **Steps:**
  1. Build df, compute ATR.
  2. Override close[-1] ≈ close[-2] + 3 × atr[-1].
  3. Call `vote_atr_breakout(df)`.
- **Expected:**
  - `vote.side == "buy"`
  - `vote.detail["move"] > vote.detail["atr"] * 1.5`

## TC-IDC-14: ATR breakout — big down move → sell

- **Scope:** `vote_atr_breakout` (mirror TC-IDC-13)
- **Expected:**
  - `vote.side == "sell"`

## TC-IDC-15: Determinism — cùng input → cùng output

- **Scope:** Mọi vote function (pure-function contract)
- **Precondition:** Fixed seed `build_ohlcv(200, seed=42)`.
- **Steps:**
  1. Build df 2 lần với cùng seed.
  2. Call mỗi vote function 2 lần, so sánh Vote.
- **Expected:**
  - `v1.side == v2.side`, `v1.strength == v2.strength`, `v1.detail == v2.detail`

## TC-IDC-16: JSON-serializable detail dict

- **Scope:** Mọi vote function — `detail` field
- **Precondition:** `df = build_ohlcv(200)`.
- **Steps:**
  1. Call mọi vote function.
  2. `json.dumps(vote.detail)` cho mỗi result.
- **Expected:**
  - Không raise `TypeError` (numpy types phải đã được cast về Python types).

## TC-IDC-17: INDICATORS tuple đồng bộ với compute_snapshot

- **Scope:** Catalog consistency
- **Steps:**
  1. Read `INDICATORS` tuple từ `analysis.technical`.
  2. Call `compute_snapshot(build_ohlcv(200))`.
  3. So sánh `set(v.name for v in snap.votes) == set(INDICATORS)`.
- **Expected:**
  - 2 set bằng nhau (catalog không bị drift).

---

## Test cases khi thêm indicator mới (template)

Mỗi indicator mới được wire vào `compute_snapshot()` phải bổ sung TC tương tự sau:

### TC-IDC-{N}: `vote_<new>` — happy path buy

- Build df với điều kiện thoả buy_rule (theo bảng catalog 8.3).
- Verify `vote.side == "buy"`, `vote.strength >= threshold`.

### TC-IDC-{N+1}: `vote_<new>` — happy path sell

- Mirror cho sell rule.

### TC-IDC-{N+2}: `vote_<new>` — insufficient data → hold

- Pass df ngắn hơn `min_bars` → expect `(hold, 0.0)`.

### TC-IDC-{N+3}: `vote_<new>` — neutral data → hold

- Pass sideways data → expect `(hold, *)`.

### TC-IDC-{N+4}: `vote_<new>` được include trong `INDICATORS` + `compute_snapshot`

- Catalog consistency check (extension của TC-IDC-17).

---

## Edge case tests (cross-indicator)

### TC-IDC-18: NaN trong giữa series không crash

- **Precondition:** `df = build_ohlcv(200); df.loc[50:60, "close"] = np.nan`.
- **Steps:** Call `compute_snapshot(df)`.
- **Expected:** Không raise, mọi vote vẫn có `side` hợp lệ.

### TC-IDC-19: Volume = 0 ở bar cuối → vote_volume hold

- **Precondition:** `df["volume"].iloc[-1] = 0`.
- **Steps:** Call `vote_volume(df)`.
- **Expected:** `vote.side == "hold"`, không divide-by-zero.

### TC-IDC-20: Snapshot integration → Tier C khi không có vote nào agree

- **Scope:** Integration với `analysis/signal.py`
- **Precondition:** Sideways data (vote rải đều buy/sell/hold).
- **Steps:**
  1. Build df sideways.
  2. Call `compute_snapshot(df)` → snap.
  3. Call `signal.analyze(snap, ...)` (xem [analysis/signal.py](../../src/finance_bot/analysis/signal.py)).
- **Expected:**
  - `signal.tier == "C"`
  - `signal.side` follow `snap.dominant_side` (có thể `hold`).

---

## PSAR (Parabolic SAR — chỉ báo #14, Advanced)

> Theo template "Test cases khi thêm indicator mới". PSAR là trailing-stop flip-based (giống Supertrend): với data đủ bar, vote **luôn** là `buy` hoặc `sell` — KHÔNG bao giờ `hold` (trừ insufficient/NaN). Đây là điểm khác biệt với template "neutral → hold".

### TC-IDC-21: `vote_psar` — uptrend → buy

- **Scope:** `vote_psar`
- **Precondition:** `build_ohlcv(100, trend=+0.5, noise=0.3)` (uptrend rõ rệt, giá nằm trên SAR).
- **Steps:**
  1. Generate df uptrend.
  2. Call `vote_psar(df)`.
- **Expected:**
  - `vote.side == "buy"`
  - `vote.detail["direction"] == 1`
  - `vote.detail["psar"] < vote.detail["close"]` (SAR nằm dưới giá trong uptrend)
  - `vote.strength in {0.45, 0.85}`

### TC-IDC-22: `vote_psar` — downtrend → sell

- **Scope:** `vote_psar`
- **Precondition:** `build_ohlcv(100, trend=-0.5, noise=0.3)`.
- **Steps:**
  1. Generate df downtrend.
  2. Call `vote_psar(df)`.
- **Expected:**
  - `vote.side == "sell"`
  - `vote.detail["direction"] == -1`
  - `vote.detail["psar"] > vote.detail["close"]` (SAR nằm trên giá trong downtrend)

### TC-IDC-23: `vote_psar` — flip mới ở bar cuối → strength 0.85

- **Scope:** `vote_psar` flip detection
- **Precondition:** Uptrend ~40 bar, sau đó override bar cuối rớt mạnh xuyên thủng SAR (force `trend[-1] != trend[-2]`).
- **Steps:**
  1. Build df uptrend `build_ohlcv(40, trend=+0.4, noise=0.2)`.
  2. Override `df.loc[df.index[-1], ["low", "close"]]` xuống thấp hơn `psar(df).sar_line.iloc[-2]` đủ để đảo trend.
  3. Call `vote_psar(df)`.
- **Expected:**
  - `vote.detail["flipped"] is True`
  - `vote.strength == 0.85`
  - `vote.side == "sell"` (vừa flip từ up → down)

### TC-IDC-24: `vote_psar` — insufficient data (<10 bars) → hold 0.0

- **Scope:** `vote_psar` guard
- **Precondition:** `build_ohlcv(9)` (dưới `min_bars=10`).
- **Steps:**
  1. Call `vote_psar(build_ohlcv(9))`.
- **Expected:**
  - `vote.side == "hold"`, `vote.strength == 0.0`
  - `vote.detail == {"insufficient_data": True}`
  - Không raise (kể cả `IndexError`).

### TC-IDC-25: `vote_psar` — sideways KHÔNG ra hold + catalog consistency

- **Scope:** `vote_psar` behavior + `INDICATORS` / `compute_snapshot` (extension của TC-IDC-17)
- **Precondition:** `build_ohlcv(200, trend=0.0)` sideways.
- **Steps:**
  1. Call `vote_psar(df)` trên sideways data.
  2. Read `INDICATORS`; call `compute_snapshot(df)`.
- **Expected:**
  - `vote.side in {"buy", "sell"}` (PSAR không bao giờ `hold` khi đủ data — luôn có trend ±1).
  - `"PSAR" in INDICATORS`
  - `"PSAR" in {v.name for v in snap.votes}` (wired vào compute_snapshot).
