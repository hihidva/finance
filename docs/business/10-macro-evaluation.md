# Module 10: Macro Evaluation Service (Service đánh giá vĩ mô)

> Một file = một domain. Domain "Macro Evaluation" định nghĩa **service tổng hợp các chỉ báo vĩ mô (DXY, WTI, lãi suất, …) thành 1 score chuẩn hoá `MacroScore ∈ [-1, +1]`** cho từng asset. Là 1 trong 3 thước đo đầu vào của Composite Score Alert Engine (xem [Module 2 §2.11](2-signal-pipeline.md)).

## 10.1 Mục đích

Macro context (DXY, WTI, lãi suất Fed, lợi suất 10Y, …) ảnh hưởng khác nhau đến từng asset class. Service này **mã hoá quy tắc nghiệp vụ** (sensitivity rules) thành 1 score `[-1, +1]` cho mỗi asset, độc lập với technical và micro.

**Đầu vào:**
- `AssetConfig` (cần `asset_class`)
- `list[MacroBrief]` — các macro asset từ watchlist (`context_only=True`): mỗi brief có `symbol`, `last_close`, `pct_change_7d`, `pct_change_30d`

**Đầu ra:**
- `MacroScore` object — `score ∈ [-1, +1]`, `breakdown` (theo từng macro indicator), `reason`
- `score = None` khi không có macro brief nào → Composite engine treat = 0

**KHÔNG nhận**: OHLCV asset, news, fundamentals — thuộc Module 9/11.

## 10.2 Service contract

```python
def compute_macro_score(
    asset: AssetConfig,
    macro_briefs: list[MacroBrief],
) -> MacroScore:
    """
    Pure function. Tổng hợp macro_briefs theo sensitivity_table[asset.asset_class].
    - Nếu macro_briefs rỗng: trả MacroScore(score=None, reason="no_macro_data")
    - Nếu sensitivity không định nghĩa cho asset_class: score = 0.0 (neutral)
    """
```

`MacroScore` dataclass:

| Trường | Kiểu | Mô tả |
|---|---|---|
| `score` | `float \| None` | Composite ∈ [-1, +1] |
| `breakdown` | `dict[str, float]` | Score per macro indicator (vd `{"DXY": -0.6, "WTI": +0.2}`) |
| `reason` | `str` | Lý do ngắn tiếng Việt (vd "DXY tăng 4% trong 30d → bearish VN stock") |
| `macro_briefs_used` | `list[MacroBrief]` | Echo lại input để debug + ghi DB |

## 10.3 Sensitivity table (matrix nghiệp vụ)

Mỗi cell = `(weight, direction)`:
- `weight ∈ [0, 1]`: tầm quan trọng của macro indicator với asset class này
- `direction ∈ {+1, -1, 0}`: dấu — `+1` nghĩa macro tăng → bullish asset, `-1` ngược lại

| Macro indicator | `vn_stock` | `crypto` | `commodity` (XAU) |
|---|---|---|---|
| **DXY** (USD index) | weight=0.40, dir=−1 | weight=0.50, dir=−1 | weight=0.55, dir=−1 |
| **WTI** (crude oil) | weight=0.25, dir=−1 | weight=0.15, dir=0 | weight=0.20, dir=+1 |
| **Lãi suất Fed FFR** | weight=0.20, dir=−1 | weight=0.20, dir=−1 | weight=0.10, dir=−1 |
| **Lợi suất 10Y UST** | weight=0.15, dir=−1 | weight=0.15, dir=−1 | weight=0.15, dir=−1 |

> **Lưu ý**: Lãi suất Fed và lợi suất 10Y **chưa fetch** ở Module 1 (chỉ có DXY, WTI). Phải bổ sung Module 1 trước khi enable 2 cell này — xem §10.7.

**Cách đọc**: DXY tăng mạnh → VN stock chịu áp lực bán (vốn FII rút) → score thành phần DXY cho `vn_stock` = `-0.40 × signal_strength`.

### Quy tắc dấu (rationale)

- **DXY ↑ → tất cả risk-on asset ↓**: USD mạnh hút vốn về Mỹ. Áp dụng cho vn_stock, crypto, gold.
- **WTI ↑ → vn_stock ↓**: dầu tăng → inflation → lãi suất ↑ → DCF model giảm. Áp dụng VN.
- **WTI ↑ → XAU ↑**: dầu tăng → inflation expectation ↑ → vàng tăng (hedge).
- **WTI ↑ → crypto neutral**: crypto không có sensitivity rõ với dầu (dir=0).
- **Lãi suất Fed ↑ → tất cả risk asset ↓**: cost of capital tăng.
- **Lợi suất 10Y ↑ → risk asset ↓**: discount rate tăng cho DCF.

## 10.4 Signal strength từ % change

Mỗi macro indicator → `signal_strength ∈ [-1, +1]` từ `pct_change_30d`:

```
signal_strength = clamp(pct_change_30d / threshold_30d, -1.0, +1.0)
```

Với `threshold_30d` chuẩn hoá độ biến động:

| Macro | `threshold_30d` | Lý do |
|---|---|---|
| DXY | 3.0 % | DXY biến động hẹp; +3%/30d đã là cú sốc |
| WTI | 12.0 % | Dầu volatile; +12%/30d mới đáng kể |
| FFR | 0.25 pt | Fed hike chuẩn 25bp |
| 10Y UST | 0.50 pt | 50bp swing là biến động lớn |

**Ví dụ**: DXY tăng 4%/30d → `signal_strength = clamp(4 / 3, -1, +1) = +1.0` (saturated).

## 10.5 Aggregation formula

```
weighted_score = 0
sum_of_weights = 0
for each macro_brief in macro_briefs:
    if asset.asset_class not in sensitivity_table:
        continue
    (w, d) = sensitivity_table[asset.asset_class][macro_brief.symbol]
    if w == 0 or d == 0:
        continue
    ss = signal_strength(macro_brief.pct_change_30d, threshold_30d[macro_brief.symbol])
    contribution = w * d * ss
    weighted_score += contribution
    sum_of_weights += w
    breakdown[macro_brief.symbol] = contribution

if sum_of_weights == 0:
    score = 0.0
else:
    score = weighted_score / sum_of_weights   # normalize → [-1, +1]
```

**Tính chất:**

- Chuẩn hoá theo `sum_of_weights` → khi chỉ có 1 macro available (DXY weight=0.4), score vẫn dùng đủ range `[-1, +1]`.
- Một macro brief có `pct_change_30d = None` → bỏ qua (không penalty).
- Asset class không có sensitivity defined → score = 0 (neutral).

## 10.6 Edge cases

| Edge case | Hành vi |
|---|---|
| `macro_briefs` rỗng | `score = None`, `reason = "no_macro_data"`. Composite Engine treat = 0. |
| Tất cả macro brief có `pct_change_30d = None` | `score = 0.0`, `breakdown = {}`, `reason = "macro_briefs_have_no_pct_change"`. |
| Asset class lạ (vd `bond`, chưa có trong sensitivity_table) | `score = 0.0`, `reason = "asset_class_no_macro_sensitivity"`. |
| Asset là `context_only=True` (DXY, WTI) | Module 10 KHÔNG nên được gọi cho asset này. Caller (Module 2) gatekeep. |
| Macro brief `last_close` valid nhưng `pct_change_30d > 100%` (extreme) | `signal_strength` clamp về `+1.0` — không lan ra ngoài range. |
| Sensitivity table chưa load FFR/10Y (Module 1 chưa fetch) | Macro brief đó không tồn tại → tự bỏ qua trong loop. Không crash. |

## 10.7 Phụ thuộc dữ liệu (Module 1)

Hiện Module 1 đã fetch:

- ✅ DXY (qua yfinance, watchlist `context_assets`)
- ✅ WTI (qua yfinance)

Chưa fetch (cần bổ sung Module 1 trước khi đầy đủ Module 10):

- ❌ FFR (Fed Funds Rate) — fetch từ FRED API (`FEDFUNDS`)
- ❌ Lợi suất 10Y UST — fetch từ FRED (`DGS10`) hoặc yfinance (`^TNX`)

**Quyết định triển khai**: Phase 1 enable Module 10 với chỉ DXY + WTI; Phase 2 thêm FFR + 10Y khi Module 1 mở rộng. Sensitivity table giữ nguyên (cell chưa available tự skip).

## 10.8 Versioning

- Đổi sensitivity table → **breaking change cho RAG**. Bump `evaluation_version` trong `signals.indicators` JSON.
- Đổi `threshold_30d` → cùng tác động — bump version.

## 10.9 Không thuộc phạm vi Module 10

- ❌ Fetch macro data — Module 1.
- ❌ Quyết định Tier A/B/C — Module 2.
- ❌ Sentiment news vĩ mô (vd "Fed dovish") — Module 11 micro hoặc một service riêng tương lai.
- ❌ Per-stock fundamental (P/E, ROE) — Module 11.

## API endpoints

Module 10 KHÔNG expose HTTP endpoint trực tiếp.

- Code: `from finance_bot.analysis.evaluation_macro import compute_macro_score`
- Doc: file này
- Test: `tests/analysis/test_evaluation_macro.py`

## Phân quyền (Capacities)

| Capacity | Chức năng |
|---|---|
| `evaluation.macro.run` | Reserved — chưa expose qua HTTP. |
| `evaluation.macro.config` | Reserved — nếu sau này cho phép admin chỉnh sensitivity table qua web. |
