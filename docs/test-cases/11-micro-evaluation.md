# Test Cases — Module 11: Micro Evaluation Service

> Test framework: `pytest`.
> Đặt test vào `tests/analysis/test_evaluation_micro.py`.
> Mọi test chạy offline — mock LLM sentiment, dùng dataclass thuần cho fixture.

## Fixture chuẩn

```python
# tests/analysis/conftest.py — extend
@pytest.fixture
def fund_fpt_strong():
    return FundamentalSnapshot(
        asset_symbol="FPT", period="2025-Q4", period_end=date(2025,12,31),
        roa=0.15, roe=0.25, pe=14.0, pb=2.5, source="vnstock",
        fetched_at=datetime.utcnow(),
    )

@pytest.fixture
def fund_fpt_weak():
    return FundamentalSnapshot(
        asset_symbol="FPT", period="2025-Q4", period_end=date(2025,12,31),
        roa=0.02, roe=0.04, pe=35.0, pb=4.0, source="vnstock",
        fetched_at=datetime.utcnow(),
    )

@pytest.fixture
def industry_tech():
    return IndustryAverage(
        industry_code="tech", period="2025-Q4",
        roa_avg=0.10, roa_median=0.08,
        roe_avg=0.18, roe_median=0.15,
        pe_avg=18.0,  pe_median=16.0,
        pb_avg=3.0,   pb_median=2.5,
        n_companies=12, source="vnstock_screen",
    )

@pytest.fixture
def news_positive():
    return [NewsBrief(title="FPT báo lãi kỷ lục Q4", source="cafef",
                      published_at=datetime.utcnow(), summary="lợi nhuận tăng 35%",
                      lang="vi")]

@pytest.fixture
def news_negative():
    return [NewsBrief(title="FPT bị phạt vì vi phạm công bố thông tin", source="vietstock",
                      published_at=datetime.utcnow(), summary="UBCKNN xử phạt 100 triệu",
                      lang="vi")]

@pytest.fixture
def mock_sentiment_llm(monkeypatch):
    """Patch LLM sentiment scorer to deterministic mapping."""
    def fake_sentiment(news):
        if "lãi" in news.title or "kỷ lục" in news.title: return +0.8
        if "phạt" in news.title or "vi phạm" in news.title: return -0.7
        return 0.0
    monkeypatch.setattr("finance_bot.analysis.evaluation_micro._llm_sentiment",
                        fake_sentiment)
```

---

## TC-MICRO-01: Fundamentals strong + industry avg → score dương cao

- **Scope:** Tất cả 4 ratio của FPT vượt industry median.
- **Precondition:** `fund_fpt_strong`, `industry_tech`, news rỗng, `mock_sentiment_llm`.
- **Steps:**
  1. Call `result = compute_micro_score(asset_fpt, fund_fpt_strong, industry_tech, [])`.
- **Expected:**
  - `result.score > 0.3`
  - `result.breakdown["fundamental_vs_industry"] > 0`
  - `result.breakdown["fundamental_absolute"] > 0`
  - `"news_sentiment" not in result.breakdown` (news rỗng)
  - `result.news_against == False`

## TC-MICRO-02: Fundamentals weak + industry → score âm

- **Scope:** Ratio dưới ngành + dưới benchmark tuyệt đối.
- **Precondition:** `fund_fpt_weak`, `industry_tech`, news rỗng.
- **Steps:**
  1. Call `result = compute_micro_score(asset_fpt, fund_fpt_weak, industry_tech, [])`.
- **Expected:**
  - `result.score < -0.3`
  - `result.breakdown["fundamental_vs_industry"] < 0`
  - `result.breakdown["fundamental_absolute"] < 0`

## TC-MICRO-03: No fundamentals + news positive → score dương vừa

- **Scope:** Asset không phải vn_stock (crypto): không có fundamentals.
- **Precondition:** `fundamentals=None`, `industry_avg=None`, `news_positive`, `mock_sentiment_llm`.
- **Steps:**
  1. Call `result = compute_micro_score(asset_crypto, None, None, news_positive)`.
- **Expected:**
  - `result.score == pytest.approx(0.8, abs=0.05)` (chỉ news, sentiment=0.8, weight tự re-norm)
  - `result.breakdown == {"news_sentiment": pytest.approx(0.8, abs=0.05)}`
  - `result.news_against == False`

## TC-MICRO-04: No fundamentals + news negative → news_against = True

- **Scope:** Flag news_against bật khi sentiment < −0.3.
- **Precondition:** `fundamentals=None`, `news_negative`, mock sentiment trả −0.7.
- **Steps:**
  1. Call `result = compute_micro_score(asset_fpt, None, None, news_negative)`.
- **Expected:**
  - `result.score < -0.5`
  - `result.news_against == True`
  - `result.reason` chứa lý do tiếng Việt

## TC-MICRO-05: Cả 3 component có data → weighted aggregate

- **Scope:** Aggregation formula §11.5.4.
- **Precondition:** `fund_fpt_strong`, `industry_tech`, `news_positive`, mock sentiment +0.8.
- **Steps:**
  1. Call `result = compute_micro_score(asset_fpt, fund_fpt_strong, industry_tech, news_positive)`.
- **Expected:**
  - `result.score > 0.5`
  - `len(result.breakdown) == 3`
  - Manual recompute: `expected = (0.5*ratio_vs + 0.3*ratio_abs + 0.2*0.8) / (0.5+0.3+0.2)` — assert `abs(result.score - expected) < 0.05`

## TC-MICRO-06: Industry avg = None → fallback chỉ absolute + news

- **Scope:** Asset chưa map industry code.
- **Precondition:** `fund_fpt_strong`, `industry_avg=None`, `news_positive`.
- **Steps:**
  1. Call `result = compute_micro_score(asset_fpt, fund_fpt_strong, None, news_positive)`.
- **Expected:**
  - `"fundamental_vs_industry" not in result.breakdown`
  - `"fundamental_absolute" in result.breakdown`
  - `"news_sentiment" in result.breakdown`
  - `result.score > 0` (FPT tốt tuyệt đối + news tích cực)
  - `result.reason` có thể chứa "industry chưa map" hoặc tương đương

## TC-MICRO-07: No data anywhere → score None

- **Scope:** Graceful degradation cực biên.
- **Precondition:** `fundamentals=None`, `industry_avg=None`, news rỗng.
- **Steps:**
  1. Call `result = compute_micro_score(asset_fpt, None, None, [])`.
- **Expected:**
  - `result.score is None`
  - `"no_micro_data" in result.reason`

## TC-MICRO-08: Partial ratio (chỉ ROA, không có P/E) → vẫn tính được

- **Scope:** Tolerate missing fields trong FundamentalSnapshot.
- **Precondition:** `FundamentalSnapshot(roa=0.15, roe=None, pe=None, pb=None, ...)`, `industry_tech`, news rỗng.
- **Steps:**
  1. Call `result = compute_micro_score(asset_fpt, fund_partial, industry_tech, [])`.
- **Expected:**
  - `result.score is not None`
  - `result.score > 0` (ROA 15% vs industry median 8% → +ve)
  - KHÔNG raise

## TC-MICRO-09: News_against threshold đúng −0.3

- **Scope:** Boundary của news_against flag.
- **Precondition:** Mock sentiment trả đúng `−0.29` (chưa đủ tiêu cực).
- **Steps:**
  1. Call `result = compute_micro_score(asset_fpt, None, None, [neutral_news])`.
- **Expected:**
  - `result.news_against == False` (vì `-0.29 >= -0.30`)

## TC-MICRO-10: LLM sentiment crash → fallback rule-based hoặc neutral

- **Scope:** Resilience khi Ollama down.
- **Precondition:** `mock_sentiment_llm` raise `ConnectionError`.
- **Steps:**
  1. Patch `_llm_sentiment` raise.
  2. Call `result = compute_micro_score(asset_fpt, None, None, news_positive)`.
- **Expected:**
  - KHÔNG raise — fallback kicks in
  - `result.score is not None` hoặc `result.news_count > 0`
  - `result.reason` chứa note "sentiment fallback" hoặc tương đương

## TC-MICRO-11: Score bounded [-1, +1] với extreme fundamentals

- **Scope:** Clamp ở `ratio_score`.
- **Precondition:** `FundamentalSnapshot(roa=10.0, ...)` (ROA 1000% bất thường) vs industry median 0.08.
- **Steps:**
  1. Call `result = compute_micro_score(asset_fpt, fund_extreme, industry_tech, [])`.
- **Expected:**
  - `-1.0 <= result.score <= 1.0`
  - Sub-score component cũng nằm trong [-1, +1]

## TC-MICRO-12: Determinism — fixed sentiment cache

- **Scope:** Pure-given-sentiment-cache.
- **Precondition:** Mock sentiment với fixed mapping.
- **Steps:**
  1. `r1 = compute_micro_score(asset_fpt, fund_fpt_strong, industry_tech, news_positive)`
  2. `r2 = compute_micro_score(asset_fpt, fund_fpt_strong, industry_tech, news_positive)`
- **Expected:**
  - `r1.score == r2.score`
  - `r1.breakdown == r2.breakdown`

## TC-MICRO-13: Reason tiếng Việt + có thông tin chính

- **Scope:** Human-readable.
- **Precondition:** `fund_fpt_strong`, `industry_tech`, `news_positive`.
- **Steps:**
  1. Call `result = compute_micro_score(asset_fpt, fund_fpt_strong, industry_tech, news_positive)`.
- **Expected:**
  - `len(result.reason) <= 200`
  - Chứa thông tin về fundamentals và/hoặc news
  - Tiếng Việt có dấu (vd "trên trung bình ngành", "tích cực")
