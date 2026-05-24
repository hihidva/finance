# Module 9: Technical Evaluation Service (Service đánh giá kỹ thuật)

> Một file = một domain. Domain "Technical Evaluation" định nghĩa **service tổng hợp 14 indicator votes thành 1 score chuẩn hoá `TechScore ∈ [-1, +1]`**. Là 1 trong 3 thước đo đầu vào của Composite Score Alert Engine (xem [Module 2 §2.11](2-signal-pipeline.md)). Không định nghĩa từng indicator (đó là [Module 8 — Indicators Catalog](8-indicators-catalog.md)) và không quyết định Tier (đó là [Module 2](2-signal-pipeline.md)).

## 9.1 Mục đích

`finance-bot` chia đánh giá ra **3 service độc lập** (technical / macro / micro) với **trọng số bằng nhau 1/3**. Service này chịu trách nhiệm phần **"giá đang nói gì"** — thuần dữ liệu OHLCV, không quan tâm fundamental hay macro.

**Đầu vào:**
- `AssetConfig` (symbol, asset_class)
- OHLCV 1D DataFrame ≥ 60 bars (từ table `prices`)

**Đầu ra:**
- `TechScore` object — `score ∈ [-1, +1]`, `dominant_side`, `agree_ratio`, `confidence`, `votes_detail`
- Khi `score > 0` → nghiêng **buy**, `score < 0` → nghiêng **sell**, `≈ 0` → trung tính
- `score = None` (NaN) khi không đủ dữ liệu → Composite engine treat như 0 (neutral, weight không redistribute)

**Đầu vào KHÔNG nhận**: news, macro briefs, fundamental ratios — những thứ này thuộc Module 10/11.

## 9.2 Service contract

```python
def compute_technical_score(asset: AssetConfig, df_1d: pd.DataFrame) -> TechScore:
    """
    Pure function, không I/O.
    - Nếu len(df_1d) < 60: trả TechScore(score=None, reason="insufficient_data")
    - Nếu df_1d hợp lệ: tính 14 vote → aggregate ra score chuẩn hoá [-1, +1]
    """
```

`TechScore` dataclass:

| Trường | Kiểu | Mô tả |
|---|---|---|
| `score` | `float \| None` | Composite score ∈ [-1, +1]. `None` nếu không đủ dữ liệu |
| `dominant_side` | `"buy" \| "sell" \| "hold"` | Bên đa số đồng thuận |
| `agree_ratio` | `float` | `max(buy_count, sell_count) / total_votes` |
| `confidence` | `float` | Trung bình `strength` của các vote cùng side, range [0, 1] |
| `votes_detail` | `list[Vote]` | Danh sách 14 vote raw — đẩy lên Composite Engine và RAG |
| `reason` | `str` | Lý do ngắn (vd "9/14 indicators agree buy, confidence=0.78") |

## 9.3 Aggregation formula

Score chuẩn hoá `[-1, +1]` được tính từ `TechSnapshot` ([Module 8](8-indicators-catalog.md)):

```
buy_strength_sum  = Σ v.strength | v.side = "buy"
sell_strength_sum = Σ v.strength | v.side = "sell"
total_strength    = buy_strength_sum + sell_strength_sum

if total_strength == 0:
    score = 0.0
else:
    score = (buy_strength_sum − sell_strength_sum) / total_strength
```

**Tính chất:**

- `score ∈ [-1, +1]` đảm bảo bởi công thức (mẫu = |tử| khi tất cả vote cùng side).
- 14 vote tất cả buy với strength = 1.0 → score = +1.0
- 14 vote tất cả sell với strength = 1.0 → score = −1.0
- 7 buy + 7 sell cùng strength → score = 0.0 (cân bằng)
- 1 buy strength 1.0 + 13 hold → score = +1.0 (strong signal dù chỉ 1 vote)

> **Lưu ý**: `hold` vote KHÔNG vào tử số lẫn mẫu số — chỉ buy/sell. Đây là khác biệt với `dominant_side` (count majority bao gồm hold làm noise).

## 9.4 Mapping về `dominant_side` và `agree_ratio`

Hai trường này KHÔNG dùng score chuẩn hoá; giữ nguyên định nghĩa Module 8 để backwards-compatible với RAG `signals_history` (similarity search nhìn vào pattern vote, không phải score):

- `dominant_side = "buy"` nếu `buy_count > sell_count`, `"sell"` nếu ngược lại, `"hold"` nếu bằng nhau.
- `agree_ratio = max(buy_count, sell_count) / len(votes)` — với 14 indicators: 9/14 ≈ 0.64.

Composite Engine ở Module 2 sẽ **chỉ cần `score`** để cộng vào tổng weighted; `dominant_side`/`agree_ratio` truyền lên cho debug + ghi `signals.indicators` JSON.

## 9.5 Sub-component cấu thành

Module 9 KHÔNG tự định nghĩa indicator — nó **consume Module 8**. Cụ thể:

| Layer | Owner | Trách nhiệm |
|---|---|---|
| Indicator math (`rsi`, `macd`, …) | Module 8 | Công thức thuần |
| Vote function (`vote_rsi`, …) | Module 8 | Vote contract → `Vote{side, strength, detail}` |
| `compute_snapshot()` | Module 8 | Aggregate 14 vote thành `TechSnapshot` |
| `compute_technical_score()` | **Module 9** | `TechSnapshot` → `TechScore` (chuẩn hoá `[-1, +1]`) |

Tách layer này cho phép sau này swap aggregation algorithm (vd weighted theo indicator class, hoặc ML-based) **không đụng Module 8**.

## 9.6 Edge cases

| Edge case | Hành vi |
|---|---|
| `len(df_1d) < 60` | `score = None`, `reason = "insufficient_data: cần ≥60 bars"`. Composite Engine treat = 0. |
| Tất cả 14 vote = `hold` (sideways thị trường) | `score = 0.0`, `dominant_side = "hold"`, `confidence = 0.0` |
| `context_only = True` (DXY, WTI) | Module 9 KHÔNG được gọi cho asset này (gatekeeper ở caller). Nếu lỡ gọi → vẫn chạy bình thường, nhưng Module 2 sẽ ignore. |
| OHLCV có gap (NaN giữa series) | Indicator function tự handle NaN, vote trả `hold` cho indicator bị ảnh hưởng. Score vẫn tính bình thường trên các vote còn lại. |
| Score = NaN sau công thức (mẫu = 0 và tử = 0) | Quy ước về `0.0` — neutral. |

## 9.7 Versioning & lifecycle

- Score formula của Module 9 thay đổi → **breaking change cho RAG**. Vì `signals_history` đã embed text chứa score cũ; đổi formula làm vector embedding lệch.
- Trước khi đổi: chạy backtest đo ROI mới so cũ, viết migration note vào CHANGELOG (chưa có file này), bump `evaluation_version` trong `signals.indicators` JSON.
- Indicator catalog (Module 8) thêm/bớt indicator → Module 9 **tự co dãn** không cần sửa code (vì `compute_snapshot()` trả về list voi, length linh hoạt).

## 9.8 Không thuộc phạm vi Module 9

- ❌ Quyết định Tier A/B/C — Module 2.
- ❌ Tính risk plan (SL/TP/RR) — `analysis/risk.py` qua Module 2.
- ❌ News sentiment, macro context — Module 10/11.
- ❌ Fetch dữ liệu OHLCV — Module 1 Data Ingestion.

## API endpoints

Module 9 KHÔNG expose HTTP endpoint. Service đọc qua:

- Code: `from finance_bot.analysis.evaluation_technical import compute_technical_score`
- Doc: file này
- Test: `tests/analysis/test_evaluation_technical.py`

## Phân quyền (Capacities)

Module 9 chạy nội bộ trong rule engine, không yêu cầu auth. Pure function, có thể call freely trong test/backtest.

| Capacity | Chức năng |
|---|---|
| `evaluation.technical.run` | Reserved cho tương lai nếu expose qua HTTP (vd debug endpoint dump TechScore của 1 symbol). Hiện chưa dùng. |
