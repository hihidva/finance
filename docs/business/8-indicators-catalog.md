# Module 8: Indicators Catalog (Danh mục chỉ báo kỹ thuật)

> Một file = một domain. Domain "Indicators Catalog" là **single source of truth** cho mọi chỉ báo kỹ thuật mà rule engine [analysis/technical.py](../../src/finance_bot/analysis/technical.py) sử dụng. Khác với [Module 2 — Signal Pipeline](2-signal-pipeline.md), file này KHÔNG mô tả pipeline tổng hợp vote → Tier; nó chỉ định nghĩa **từng indicator** như một entity độc lập có lifecycle (planned → active → deprecated).

## 8.1 Mục đích

`finance-bot` chạy rule engine vote-based trên khung 1D. Mỗi chỉ báo trả về 1 `Vote{name, side, strength, detail}`; `TechSnapshot` aggregate toàn bộ vote để ra `dominant_side` + `agree_count` → Module 2 quyết Tier A/B/C dựa trên `agree_ratio` (tỷ lệ động).

**Mục tiêu domain này:**

1. Catalog 15 chỉ báo **đang active** — kèm công thức, vote rule, data requirement, horizon.
2. Định nghĩa **contract chuẩn** để bất kỳ contributor nào thêm 1 chỉ báo mới đều theo cùng pattern.
3. Tracking **status** từng chỉ báo qua lifecycle: `active` (đang vote) / `deprecated` (đã loại).

> Domain này KHÔNG quy định cách tổng hợp vote thành Tier — đó là Module 2. KHÔNG quy định cách lưu DB — đó là `signals.indicators` JSON column.

## 8.2 Vote engine contract

Mọi indicator function (cũ và mới) phải tuân thủ:

```python
def vote_<name>(df_or_series) -> Vote:
    """
    Input: pd.DataFrame (full OHLCV ≥ 60 bars) hoặc pd.Series (close-only).
    Output: Vote(name=<canonical_name>, side, strength, detail)
    Constraints:
      - Không raise exception khi data NaN/insufficient → trả Vote(side="hold", strength=0.0, detail={"insufficient_data": True})
      - strength ∈ [0.0, 1.0]
      - side ∈ {"buy", "sell", "hold"}
      - detail là dict JSON-serializable (sẽ ghi vào signals.indicators column)
      - Deterministic: cùng input → cùng output (không random, không I/O)
      - Không gọi I/O (no DB, no HTTP) — pure function
    """
```

### Thuộc tính bắt buộc của 1 indicator entity

| Trường | Kiểu | Mô tả |
|---|---|---|
| `canonical_name` | string | Tên duy nhất xuất hiện trong `INDICATORS` tuple và `signals.indicators` JSON |
| `group` | enum | `trend`, `momentum`, `volatility`, `volume`, `advanced` |
| `min_bars` | int | Số bar tối thiểu cần để tính (NaN-safe nếu thiếu) |
| `formula` | string | Mô tả ngắn công thức (1 câu) |
| `buy_rule` | string | Điều kiện để side = "buy" |
| `sell_rule` | string | Điều kiện để side = "sell" |
| `status` | enum | `active`, `deprecated` |
| `horizon` | enum | `short` (< 1 tuần), `medium` (1 tuần - vài tháng), `long` (vài tháng+) |

### Lifecycle

```
implement math + vote ──> active ──obsolete──> deprecated
```

- `active`: hàm `vote_<name>()` tồn tại VÀ được include trong `compute_snapshot()` votes list.
- `deprecated`: từng active, nay đã loại khỏi `compute_snapshot()` votes (giữ math cho test backwards-compat 1 release cycle trước khi xoá hẳn).

> Khi muốn thêm indicator mới ngoài 15 hiện có, follow quy trình ở §8.4 (Tích hợp vào Signal Pipeline).

## 8.3 Indicator catalog (15 chỉ báo active)

### Nhóm 1 — Trend (xu hướng)

| # | Canonical name | Horizon | Min bars | Formula tóm tắt | Buy rule | Sell rule |
|---|---|---|---|---|---|---|
| 1 | `MACD` | medium | 35 | EMA(12) − EMA(26), signal = EMA(9) của diff | hist cross up qua 0 | hist cross down qua 0 |
| 2 | `EMA20/50` | medium | 50 | EMA(20) vs EMA(50) cross | EMA20 > EMA50 + crossing up | EMA20 < EMA50 + crossing down |
| 3 | `EMA50/200` | long | 200 | EMA(50) vs EMA(200) — filter xu hướng dài hạn | close > EMA200 & EMA50 > EMA200 | close < EMA200 & EMA50 < EMA200 |
| 4 | `ICHIMOKU` | medium | 52 | Tenkan(9), Kijun(26), Senkou A/B | Giá > Kumo top + Tenkan > Kijun | Giá < Kumo bot + Tenkan < Kijun |
| 5 | `ADX` | medium | 28 | ADX(14) — Wilder DI+ / DI− smoothing | ADX > 25 và DI+ > DI− | ADX > 25 và DI− > DI+ |

### Nhóm 2 — Momentum

| # | Canonical name | Horizon | Min bars | Formula tóm tắt | Buy rule | Sell rule |
|---|---|---|---|---|---|---|
| 6 | `RSI14` | medium | 15 | Wilder RSI(14) trên close | RSI < 30 và đang hồi (RSI > RSI[-1]) | RSI > 70 và đang giảm |

### Nhóm 3 — Volatility

| # | Canonical name | Horizon | Min bars | Formula tóm tắt | Buy rule | Sell rule |
|---|---|---|---|---|---|---|
| 7 | `BB20` | medium | 20 | SMA(20) ± 2σ trên close | Prev close ≤ lower & nay đóng lại trong band | Prev close ≥ upper & nay đóng lại trong band |
| 8 | `ATR_BO` | short | 15 | \|close − prev_close\| > 1.5 × ATR(14) | move > +1.5 × ATR | move < −1.5 × ATR |

### Nhóm 4 — Volume

| # | Canonical name | Horizon | Min bars | Formula tóm tắt | Buy rule | Sell rule |
|---|---|---|---|---|---|---|
| 9 | `VOL` | short | 20 | volume / SMA(volume, 20) > 1.8 | spike + close > prev close | spike + close < prev close |
| 10 | `OBV` | medium | 60 | Cumulative sum của signed volume | OBV trending up (lookback 20) | OBV trending down |
| 11 | `MFI` | medium | 16 | RSI-like trên typical price × volume | MFI < 20 và đang hồi | MFI > 80 và đang giảm |
| 12 | `CMF` | medium | 21 | Σ(((C−L) − (H−C))/(H−L) × V) / ΣV, length=20 | CMF > +0.05 và đang tăng | CMF < −0.05 và đang giảm |

### Nhóm 5 — Advanced

| # | Canonical name | Horizon | Min bars | Formula tóm tắt | Buy rule | Sell rule |
|---|---|---|---|---|---|---|
| 13 | `SUPERTREND` | medium | 14 | HL2 ± 3 × ATR(10), flip-based | direction = +1 (flip mới strength cao) | direction = −1 (flip mới strength cao) |
| 14 | `PSAR` | medium | 10 | Parabolic SAR (Wilder), AF 0.02→0.20 — trailing stop flip-based | trend = +1 / giá trên SAR (flip mới strength cao) | trend = −1 / giá dưới SAR (flip mới strength cao) |
| 15 | `DONCHIAN` | medium | 21 | Max(high, 20) / Min(low, 20) — breakout 20 phiên | close > prev 20-bar high | close < prev 20-bar low |

## 8.4 Tích hợp vào Signal Pipeline

Khi thêm 1 indicator mới:

1. Implement math function trong [analysis/technical.py](../../src/finance_bot/analysis/technical.py).
2. Implement `vote_<name>(df_or_series) -> Vote` cùng file, **phải có guard `if len(df) < min_bars`** trước khi tính (không phụ thuộc `pd.isna` của EWM smoothing — EWM không có warmup period).
3. Thêm `canonical_name` vào `INDICATORS` tuple.
4. Thêm vote vào list `votes` trong `compute_snapshot()`.
5. Cập nhật **Module 2 section 2.3 table** để liệt kê.
6. Cập nhật **`min_bars` guard toàn cục** trong `compute_snapshot()` (hiện đang là 60) nếu indicator mới cần nhiều bar hơn.
7. Thêm row vào **catalog §8.3** trên đúng nhóm (status = `active`).
8. Threshold Tier A/B **không cần cập nhật** — `min_agree_ratio` tự co dãn theo `len(votes)`.

### Backward compatibility

- KHÔNG xoá indicator đang active mà không có lý do — RAG `signals_history` có embedded vote pattern; xoá làm vỡ similarity search.
- Nếu muốn loại bỏ → set status = `deprecated` trong catalog, giữ math + vote function ở lại 1 release cycle để backtest so sánh, sau đó mới xoá.
- Đổi vote rule (vd threshold) cũng phá similarity vì `detail` dict khác — chỉ làm khi có data-driven lý do (backtest đo ROI).

## 8.5 Edge cases & data requirements

| Edge case | Hành vi mong đợi |
|---|---|
| OHLCV < `min_bars` của indicator | Vote trả `side="hold"`, `strength=0.0`, `detail={"insufficient_data": True}` — KHÔNG raise |
| OHLCV < 60 (guard toàn cục) | `compute_snapshot()` raise `ValueError("Need >=60 daily bars")` |
| NaN trong indicator value | Vote trả `hold`, `strength=0.0` |
| Volume = 0 (vd weekend của 24/7 market) | Indicator volume-based trả `hold`, không divide-by-zero |
| `vn_stock` thiếu volume cuối ngày (lỗi vnstock) | Volume indicators trả `hold` cho ngày đó |
| `crypto` BTC có volume rất lớn → spike threshold (1.8×) có thể chưa tối ưu | Có thể cần threshold riêng theo asset_class — nhưng KHÔNG hardcode; nếu cần thì tính `dynamic_threshold` dựa trên rolling-stdev |

## 8.6 Open questions

Các quyết định **chưa chốt** — cần backtest hoặc user trả lời trước khi đổi:

1. ~~**Tier A threshold sau khi mở rộng**~~ → **GIẢI QUYẾT**: Đã chuyển sang tỷ lệ động `min_agree_ratio`. Tier A = 0.60, Tier B = 0.45. Config ở [config/watchlist.yaml](../../config/watchlist.yaml).
2. **Weighted vote**: hiện mỗi indicator có `strength` nhưng `dominant_side` chỉ count majority. Có nên đổi sang **weighted majority** (sum strength theo side) không? Trade-off: tăng tính nhạy nhưng phá similarity search.
3. **Per-asset-class catalog**: BTC có nên dùng indicator khác `vn_stock` không (vd Donchian breakout thì BTC chạy tốt hơn)? Hiện code chạy chung 1 catalog cho mọi asset → reuse logic, nhưng có thể là điểm cải thiện.
4. **Multi-timeframe**: hiện chỉ D1. Ichimoku tuần đòi hỏi sync W1 — có nên thêm 1 timeframe vào `prices` table không?

> Các quyết định này thuộc Module 2 Signal Pipeline, ghi ở đây để contributor biết các ràng buộc khi nâng cấp catalog.

## 8.7 CLI tương lai (chưa có)

Có thể bổ sung subcommand `main.py list-indicators` để dump catalog ra console — phục vụ debug:

```bash
uv run python main.py list-indicators              # toàn bộ catalog
uv run python main.py list-indicators --group trend
```

Chưa implement; sẽ làm khi catalog vượt 20 indicator (lúc đó nhớ trong đầu khó hơn).

## API endpoints

Module 8 KHÔNG expose HTTP endpoint. Catalog đọc qua:

- Code: `from finance_bot.analysis.technical import INDICATORS`
- Doc: file này
- CLI tương lai: `main.py list-indicators` (§8.7)

## Phân quyền (Capacities)

Module 8 chạy nội bộ trong rule engine, không yêu cầu auth. Mọi vote function là pure-function, có thể call freely trong test/backtest.

| Capacity | Chức năng |
|---|---|
| (n/a) | Không có capacity vì không expose UI/API |
