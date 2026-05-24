# Test Cases — Module 9: Technical Evaluation Service

> Test framework: `pytest` (theo `pyproject.toml`).
> Đặt test vào `tests/analysis/test_evaluation_technical.py`.
> Mọi test chạy offline — dùng fixture DataFrame synthetic từ `tests/analysis/conftest.py` (đã có ở Module 8 catalog).

## Fixture chuẩn

Tái sử dụng `build_ohlcv(n, start_price, trend, noise, seed)` từ Module 8. Bổ sung:

```python
# tests/analysis/conftest.py — extend
@pytest.fixture
def asset_vn_stock():
    return AssetConfig(symbol="FPT", name="FPT Corp", asset_class="vn_stock",
                       context_only=False)

@pytest.fixture
def df_strong_uptrend():
    return build_ohlcv(n=200, start_price=100.0, trend=+0.5, noise=0.3, seed=1)

@pytest.fixture
def df_strong_downtrend():
    return build_ohlcv(n=200, start_price=100.0, trend=-0.5, noise=0.3, seed=2)

@pytest.fixture
def df_sideways():
    return build_ohlcv(n=200, start_price=100.0, trend=0.0, noise=0.5, seed=3)

@pytest.fixture
def df_short(): # < 60 bars
    return build_ohlcv(n=40)
```

---

## TC-TECH-01: Score range — strong uptrend ra score dương cao

- **Scope:** `compute_technical_score(asset, df_strong_uptrend)`
- **Precondition:** Fixture `asset_vn_stock`, `df_strong_uptrend`.
- **Steps:**
  1. Call `result = compute_technical_score(asset_vn_stock, df_strong_uptrend)`.
- **Expected:**
  - `result.score` is not None
  - `result.score > 0.5` (mạnh thiên về buy)
  - `result.dominant_side == "buy"`
  - `result.agree_ratio >= 0.5`
  - `result.confidence > 0.5`
  - `len(result.votes_detail) == 14`

## TC-TECH-02: Score range — strong downtrend ra score âm sâu

- **Scope:** `compute_technical_score(asset, df_strong_downtrend)`
- **Precondition:** Fixture `asset_vn_stock`, `df_strong_downtrend`.
- **Steps:**
  1. Call `result = compute_technical_score(asset_vn_stock, df_strong_downtrend)`.
- **Expected:**
  - `result.score < -0.5`
  - `result.dominant_side == "sell"`
  - `len(result.votes_detail) == 14`

## TC-TECH-03: Score range — sideways ra score gần 0

- **Scope:** `compute_technical_score(asset, df_sideways)`
- **Precondition:** Fixture `df_sideways` (trend=0, noise=0.5).
- **Steps:**
  1. Call `result = compute_technical_score(asset_vn_stock, df_sideways)`.
- **Expected:**
  - `abs(result.score) < 0.3`
  - `result.confidence < 0.6`

## TC-TECH-04: Insufficient data — trả score None

- **Scope:** `compute_technical_score(asset, df_short)`
- **Precondition:** Fixture `df_short` (40 bars < 60 guard).
- **Steps:**
  1. Call `result = compute_technical_score(asset_vn_stock, df_short)`.
- **Expected:**
  - `result.score is None`
  - `"insufficient_data" in result.reason`
  - **KHÔNG raise exception** — caller depends on graceful degradation

## TC-TECH-05: Score bounds — luôn nằm trong [-1, +1]

- **Scope:** Property test — chạy 100 lần với seed khác nhau.
- **Precondition:** N/A.
- **Steps:**
  1. For seed in range(100):
     1. `df = build_ohlcv(n=200, start_price=100.0, trend=random_trend, noise=random_noise, seed=seed)`
     2. `result = compute_technical_score(asset_vn_stock, df)`
     3. If `result.score is not None`: assert `-1.0 <= result.score <= 1.0`
- **Expected:**
  - 100/100 case score nằm trong [-1, +1]

## TC-TECH-06: Determinism — cùng input cùng output

- **Scope:** Pure-function contract.
- **Precondition:** Fixture `df_strong_uptrend`.
- **Steps:**
  1. `r1 = compute_technical_score(asset_vn_stock, df_strong_uptrend)`
  2. `r2 = compute_technical_score(asset_vn_stock, df_strong_uptrend)`
- **Expected:**
  - `r1.score == r2.score`
  - `r1.dominant_side == r2.dominant_side`
  - `r1.confidence == r2.confidence`

## TC-TECH-07: Formula edge — tất cả vote hold ra score 0

- **Scope:** Tổng hợp khi `total_strength = 0`.
- **Precondition:** Mock `compute_snapshot` trả về `TechSnapshot` với 14 vote toàn `hold` strength 0.0.
- **Steps:**
  1. Patch `compute_snapshot` → trả mock snapshot.
  2. Call `compute_technical_score(asset_vn_stock, df_any)`.
- **Expected:**
  - `result.score == 0.0` (không phải NaN)
  - `result.dominant_side == "hold"`
  - `result.confidence == 0.0`

## TC-TECH-08: Formula edge — 1 vote buy mạnh + 13 hold ra score gần +1

- **Scope:** Single strong vote thắng.
- **Precondition:** Mock snapshot với 1 vote `buy` strength=1.0 và 13 vote `hold` strength=0.0.
- **Steps:**
  1. Patch `compute_snapshot` → mock.
  2. Call `compute_technical_score(asset_vn_stock, df_any)`.
- **Expected:**
  - `result.score == 1.0` (theo công thức: buy_sum=1.0, sell_sum=0, total=1.0 → 1.0)
  - `result.dominant_side == "buy"`

## TC-TECH-09: votes_detail giữ nguyên thứ tự + cấu trúc

- **Scope:** Trace data từ Module 8 lên Module 9.
- **Precondition:** Fixture `df_strong_uptrend`.
- **Steps:**
  1. `snapshot = compute_snapshot(df_strong_uptrend)`
  2. `result = compute_technical_score(asset_vn_stock, df_strong_uptrend)`
- **Expected:**
  - `len(result.votes_detail) == len(snapshot.votes)` (== 14)
  - `[v.name for v in result.votes_detail] == [v.name for v in snapshot.votes]`
  - Mọi `Vote.detail` dict JSON-serializable (test bằng `json.dumps`)

## TC-TECH-10: Asset context_only — không gatekeep ở Module 9, vẫn tính nhưng caller phải skip

- **Scope:** Behavior khi gọi nhầm với `context_only=True`.
- **Precondition:** `AssetConfig(symbol="DXY", asset_class="macro", context_only=True)`, df hợp lệ.
- **Steps:**
  1. Call `result = compute_technical_score(asset_dxy, df_strong_uptrend)`.
- **Expected:**
  - KHÔNG raise — Module 9 vẫn trả `TechScore` bình thường
  - (Caller Module 2 chịu trách nhiệm skip; Module 9 là pure function)
