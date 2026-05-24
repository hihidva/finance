# Test Cases — Module 10: Macro Evaluation Service

> Test framework: `pytest`.
> Đặt test vào `tests/analysis/test_evaluation_macro.py`.
> Mọi test chạy offline — dùng `MacroBrief` thuần dataclass.

## Fixture chuẩn

```python
# tests/analysis/conftest.py — extend
@pytest.fixture
def dxy_strong_up():
    return MacroBrief(symbol="DXY", name="USD Index", last_close=105.0,
                      pct_change_7d=1.5, pct_change_30d=4.0)  # saturated +

@pytest.fixture
def dxy_strong_down():
    return MacroBrief(symbol="DXY", name="USD Index", last_close=97.0,
                      pct_change_7d=-1.2, pct_change_30d=-3.5)

@pytest.fixture
def wti_up():
    return MacroBrief(symbol="WTI", name="WTI Crude", last_close=85.0,
                      pct_change_7d=2.0, pct_change_30d=10.0)

@pytest.fixture
def asset_vn_stock():
    return AssetConfig(symbol="FPT", asset_class="vn_stock", context_only=False)

@pytest.fixture
def asset_crypto():
    return AssetConfig(symbol="BTC/USDT", asset_class="crypto", context_only=False)

@pytest.fixture
def asset_gold():
    return AssetConfig(symbol="XAU/USD", asset_class="commodity", context_only=False)
```

---

## TC-MACRO-01: Empty briefs → score None

- **Scope:** Graceful degradation.
- **Precondition:** `macro_briefs = []`.
- **Steps:**
  1. Call `result = compute_macro_score(asset_vn_stock, [])`.
- **Expected:**
  - `result.score is None`
  - `"no_macro_data" in result.reason`

## TC-MACRO-02: DXY mạnh lên → vn_stock score âm

- **Scope:** Sensitivity DXY × vn_stock (weight=0.40, dir=−1).
- **Precondition:** `dxy_strong_up`, `asset_vn_stock`.
- **Steps:**
  1. Call `result = compute_macro_score(asset_vn_stock, [dxy_strong_up])`.
- **Expected:**
  - `result.score is not None`
  - `result.score < -0.5` (DXY +4% saturated × dir=−1 × weight=0.4 / weight_sum=0.4 = −1.0)
  - `abs(result.score - (-1.0)) < 0.01` (chính xác = −1.0 vì chỉ 1 macro)
  - `"DXY" in result.breakdown`
  - `result.breakdown["DXY"] == pytest.approx(-0.4, abs=0.01)`

## TC-MACRO-03: DXY giảm mạnh → vn_stock score dương

- **Scope:** Đối xứng dấu.
- **Precondition:** `dxy_strong_down`, `asset_vn_stock`.
- **Steps:**
  1. Call `result = compute_macro_score(asset_vn_stock, [dxy_strong_down])`.
- **Expected:**
  - `result.score > 0.7`
  - `result.breakdown["DXY"] > 0`

## TC-MACRO-04: WTI tăng → gold (XAU) score dương (dir=+1)

- **Scope:** Asymmetric: WTI bearish vn_stock nhưng bullish gold.
- **Precondition:** `wti_up`, `asset_gold`.
- **Steps:**
  1. Call `result = compute_macro_score(asset_gold, [wti_up])`.
- **Expected:**
  - `result.score > 0` (WTI tăng → gold bullish vì hedge inflation)
  - `result.breakdown["WTI"] > 0`

## TC-MACRO-05: WTI tăng → crypto neutral (dir=0)

- **Scope:** Sensitivity = 0 → contribution = 0.
- **Precondition:** `wti_up`, `asset_crypto`.
- **Steps:**
  1. Call `result = compute_macro_score(asset_crypto, [wti_up])`.
- **Expected:**
  - `result.breakdown.get("WTI", 0.0) == 0.0`
  - `result.score == 0.0` (vì chỉ 1 macro và nó neutral)

## TC-MACRO-06: Multi macro — DXY up + WTI up vào vn_stock

- **Scope:** Aggregate weighted.
- **Precondition:** Cả 2 brief, `asset_vn_stock`.
- **Steps:**
  1. Call `result = compute_macro_score(asset_vn_stock, [dxy_strong_up, wti_up])`.
- **Expected:**
  - `result.score is not None`
  - `result.score < 0` (cả hai đều bearish vn_stock)
  - Có cả `"DXY"` và `"WTI"` trong `breakdown`

## TC-MACRO-07: Score bounded [-1, +1] với input extreme

- **Scope:** Clamp ở `signal_strength` không cho overflow.
- **Precondition:** `MacroBrief(symbol="DXY", pct_change_30d=999.0)` (extreme).
- **Steps:**
  1. Call `result = compute_macro_score(asset_vn_stock, [extreme_brief])`.
- **Expected:**
  - `-1.0 <= result.score <= 1.0`
  - `result.breakdown["DXY"] == pytest.approx(-0.4, abs=0.01)` (clamped before weighting)

## TC-MACRO-08: pct_change_30d = None → bỏ qua brief đó

- **Scope:** Tolerate missing data.
- **Precondition:** `MacroBrief(symbol="DXY", pct_change_30d=None, pct_change_7d=None)`.
- **Steps:**
  1. Call `result = compute_macro_score(asset_vn_stock, [brief_no_change])`.
- **Expected:**
  - `result.score == 0.0` hoặc `result.score is None`
  - `"macro_briefs_have_no_pct_change"` hoặc `"no_macro_data"` in `result.reason`
  - KHÔNG raise

## TC-MACRO-09: Asset class lạ → score 0 neutral

- **Scope:** Default behavior cho asset class không có trong sensitivity_table.
- **Precondition:** `AssetConfig(symbol="BOND", asset_class="bond", context_only=False)`.
- **Steps:**
  1. Call `result = compute_macro_score(asset_bond, [dxy_strong_up])`.
- **Expected:**
  - `result.score == 0.0`
  - `"asset_class_no_macro_sensitivity"` in `result.reason`

## TC-MACRO-10: Determinism — cùng input cùng output

- **Scope:** Pure function.
- **Precondition:** `[dxy_strong_up, wti_up]`, `asset_vn_stock`.
- **Steps:**
  1. `r1 = compute_macro_score(asset_vn_stock, [dxy_strong_up, wti_up])`
  2. `r2 = compute_macro_score(asset_vn_stock, [dxy_strong_up, wti_up])`
- **Expected:**
  - `r1.score == r2.score`
  - `r1.breakdown == r2.breakdown`

## TC-MACRO-11: Reason field tiếng Việt khi score lệch mạnh

- **Scope:** Human-readable reasoning.
- **Precondition:** `dxy_strong_up`, `asset_vn_stock`.
- **Steps:**
  1. Call `result = compute_macro_score(asset_vn_stock, [dxy_strong_up])`.
- **Expected:**
  - `result.reason` chứa "DXY"
  - `result.reason` chứa "%" hoặc số phần trăm
  - Format tiếng Việt (có thể chứa "tăng", "bearish", "ngược chiều", v.v.)
