# Module 11: Micro Evaluation Service (Service đánh giá vi mô)

> Một file = một domain. Domain "Micro Evaluation" định nghĩa **service tổng hợp 3 nguồn vi mô — fundamentals (ROA/ROE/P/E/P/B), industry averages, và news sentiment — thành 1 score chuẩn hoá `MicroScore ∈ [-1, +1]`**. Là 1 trong 3 thước đo đầu vào của Composite Score Alert Engine (xem [Module 2 §2.11](2-signal-pipeline.md)).

## 11.1 Mục đích

Phần "doanh nghiệp có gì bất thường ở mức cơ bản" — đối nghịch với Module 9 (giá nói gì) và Module 10 (môi trường vĩ mô). Hợp nhất 3 sub-component vào 1 score duy nhất vì cùng phục vụ câu hỏi "fundamental health của asset này".

**Đầu vào:**
- `AssetConfig`
- `FundamentalSnapshot` — ROA, ROE, P/E, P/B của asset (per-period, mới nhất)
- `IndustryAverage` — trung bình ngành tương ứng (ROA, ROE, P/E, P/B avg của các stock cùng ngành)
- `list[NewsBrief]` — tin 48h gần nhất (đã có ở Module 1)

**Đầu ra:**
- `MicroScore` object — `score ∈ [-1, +1]`, `breakdown` (3 component), `news_against` (bool), `reason`
- `score = None` khi cả fundamentals + news đều thiếu

**Lưu ý**: `news_against` flag VẪN được giữ riêng (không chỉ là 1 phần của score) vì Module 2 cooldown rule + LLM arbiter cần đọc trực tiếp.

## 11.2 Service contract

```python
def compute_micro_score(
    asset: AssetConfig,
    fundamentals: FundamentalSnapshot | None,
    industry_avg: IndustryAverage | None,
    news: list[NewsBrief],
) -> MicroScore:
    """
    Pure function.
    - fundamentals = None VÀ news rỗng: score = None
    - fundamentals = None: chỉ news → score chỉ phản ánh sentiment
    - news rỗng nhưng có fundamentals: score chỉ phản ánh ratio (no news_against)
    - Cả hai: aggregate theo §11.5
    """
```

`MicroScore` dataclass:

| Trường | Kiểu | Mô tả |
|---|---|---|
| `score` | `float \| None` | Composite ∈ [-1, +1] |
| `breakdown` | `dict[str, float]` | `{"fundamental_vs_industry": ..., "fundamental_absolute": ..., "news_sentiment": ...}` |
| `news_against` | `bool` | True nếu sentiment news ngược chiều với fundamentals (Module 2 đọc để gác Tier A) |
| `reason` | `str` | Tiếng Việt ngắn (≤ 200 ký tự) |
| `fundamentals_used` | `FundamentalSnapshot \| None` | Echo input, ghi DB |
| `news_count` | `int` | Số news brief đã xử lý |

## 11.3 Entity — FundamentalSnapshot

| Trường | Kiểu | Mô tả |
|---|---|---|
| `asset_symbol` | string | `"FPT"` |
| `period` | string | `"2025-Q4"`, `"2025-FY"` (annual) |
| `period_end` | date | Ngày kết thúc kỳ |
| `roa` | `float \| None` | Return on Assets (%) — vd 0.085 = 8.5% |
| `roe` | `float \| None` | Return on Equity (%) |
| `pe` | `float \| None` | Price/Earnings ratio |
| `pb` | `float \| None` | Price/Book ratio |
| `source` | string | `"vnstock"`, `"manual"`, … |
| `fetched_at` | datetime | Lúc fetch |

Tất cả ratio nullable — vnstock không phải lúc nào cũng có đủ. Trống → bỏ qua trong scoring.

### Trạng thái

```
fetched → active ──new_period──> superseded
                 ──manual_corrected──> overridden
```

| Trạng thái | Mô tả |
|---|---|
| `active` | Snapshot mới nhất, dùng cho scoring |
| `superseded` | Đã có period mới hơn cho cùng asset |
| `overridden` | Có manual entry đè lên (vd analyst fix bug vnstock) |

Module 11 chỉ đọc snapshot ở trạng thái `active` cho asset đó.

## 11.4 Entity — IndustryAverage

| Trường | Kiểu | Mô tả |
|---|---|---|
| `industry_code` | string | `"banking"`, `"real_estate"`, `"tech"`, `"steel"`, … |
| `period` | string | Cùng convention với FundamentalSnapshot |
| `roa_avg`, `roe_avg`, `pe_avg`, `pb_avg` | `float \| None` | Trung bình toàn ngành |
| `roa_median`, `roe_median`, … | `float \| None` | Median (chống outlier) — dùng cho scoring chính, avg cho display |
| `n_companies` | int | Số công ty tính trung bình (≥ 5 mới reliable) |
| `source` | string | `"vnstock_screen"`, `"manual"` |

Mapping `asset_symbol → industry_code` lưu ở table `assets.industry_code` (extend Module 1 schema).

## 11.5 Aggregation formula

Score `MicroScore.score` = weighted sum của 3 component (weight nội bộ trong Module 11, độc lập với weight 1/3 cấp ngoài):

```
W_RATIO_VS_INDUSTRY = 0.50   # so với ngành — quan trọng nhất (relative)
W_RATIO_ABSOLUTE    = 0.30   # so với benchmark cứng (vd P/E < 15 = rẻ tuyệt đối)
W_NEWS_SENTIMENT    = 0.20   # tin tức ngắn hạn
```

### 11.5.1 Sub-score: ratio_vs_industry

So 4 ratio (ROA, ROE, P/E, P/B) với median ngành:

```
def ratio_score(asset_val, industry_median, higher_is_better):
    if asset_val is None or industry_median is None or industry_median == 0:
        return None
    delta = (asset_val - industry_median) / abs(industry_median)
    score = clamp(delta * direction, -1, +1)  # direction = +1 nếu higher_is_better else -1
    return score

roa_score = ratio_score(asset.roa, industry.roa_median, higher_is_better=True)
roe_score = ratio_score(asset.roe, industry.roe_median, higher_is_better=True)
pe_score  = ratio_score(asset.pe,  industry.pe_median,  higher_is_better=False)  # P/E thấp = rẻ
pb_score  = ratio_score(asset.pb,  industry.pb_median,  higher_is_better=False)

valid = [s for s in (roa_score, roe_score, pe_score, pb_score) if s is not None]
ratio_vs_industry = mean(valid) if valid else None
```

### 11.5.2 Sub-score: ratio_absolute

Benchmark cứng theo nguyên tắc value investing:

| Ratio | "Tốt" tuyệt đối | "Xấu" tuyệt đối | Direction |
|---|---|---|---|
| ROA | > 10% | < 3% | higher_is_better |
| ROE | > 15% | < 5% | higher_is_better |
| P/E | < 12 | > 25 | lower_is_better |
| P/B | < 1.5 | > 3.0 | lower_is_better |

Linear mapping: ROA 10% → +1, 3% → −1, ở giữa nội suy tuyến tính.

### 11.5.3 Sub-score: news_sentiment

Dùng LLM (Ollama `qwen2.5:7b-instruct` đã có trong project) chấm điểm sentiment **per news**:

```python
sentiment(news_brief) -> float in [-1, +1]
```

Sau đó:

```
news_sentiment = mean(sentiment(n) for n in news[:8])
news_against = news_sentiment < -0.3   # threshold tiêu cực mạnh
```

**Lưu ý**: gọi LLM ở đây làm Module 11 trở thành **slow + I/O dependent**. Service vẫn được khai báo "pure" về business logic (deterministic given LLM cache hit) nhưng caller cần:
- Cache sentiment per `news.id` (đã có DB row → có ID ổn định)
- Fallback rule-based khi LLM down (vd keyword list "phạt", "điều tra", "âm" → −0.5; "kỷ lục", "tăng trưởng" → +0.5)

### 11.5.4 Final aggregation

```
components = []
if ratio_vs_industry is not None: components.append((W_RATIO_VS_INDUSTRY, ratio_vs_industry))
if ratio_absolute    is not None: components.append((W_RATIO_ABSOLUTE,    ratio_absolute))
if news_sentiment    is not None: components.append((W_NEWS_SENTIMENT,    news_sentiment))

if not components:
    return MicroScore(score=None, reason="no_micro_data", ...)

total_w = sum(w for w, _ in components)
score = sum(w * s for w, s in components) / total_w
```

→ score luôn nằm `[-1, +1]` và **không penalty khi thiếu sub-component** (weight tự re-normalize).

## 11.6 Edge cases

| Edge case | Hành vi |
|---|---|
| Asset không phải `vn_stock` (vd `crypto`, `commodity`) | Fundamentals + industry không tồn tại → `fundamentals = None, industry_avg = None`. Score chỉ phản ánh news sentiment. |
| Industry code chưa map cho asset | `industry_avg = None`. `ratio_vs_industry = None`. Score lui về absolute + news. |
| `n_companies < 5` cho industry | Industry avg có nhưng warning trong reason: "industry sample < 5". Vẫn dùng. |
| News rỗng + fundamentals đầy đủ | `news_sentiment = None`, `news_against = False`. Score chỉ từ ratio. |
| Tất cả ratio = None + news rỗng | `score = None`, `reason = "no_micro_data"`. |
| LLM sentiment fail toàn bộ | Fallback rule-based; nếu cả rule-based cũng không match → `news_sentiment = 0.0` (neutral), không `None`. |
| News chứa ticker khác chen vào (vd "FPT" trong tin về "FPTS") | Caller (Module 1 hoặc Module 2) phải filter trước. Module 11 trust input. |
| Same news lặp (cùng URL khác source) | Caller dedupe; Module 11 trust input — không tự dedupe. |

## 11.7 Phụ thuộc dữ liệu (Module 1 phải mở rộng)

Hiện Module 1 **chưa fetch** fundamentals + industry. Phải bổ sung trước khi enable Module 11:

| Nguồn | Endpoint | Tần suất | Bảng DB mới |
|---|---|---|---|
| vnstock `Finance().ratio()` | per-symbol per-period | Hàng ngày sau 17:00 ICT (khi báo cáo có cập nhật) | `fundamental_snapshots` |
| vnstock `Listing().industries_icb()` + screener | per-industry-code | Tuần | `industry_averages` |
| Industry code mapping | vnstock symbol metadata | Khi sync-prices | extend `assets.industry_code` |

CLI mới (Module 1 phụ trách):

```bash
uv run python main.py sync-fundamentals [--symbol FPT]
uv run python main.py sync-industry-averages
```

## 11.8 Versioning

- Đổi weight nội bộ (0.5 / 0.3 / 0.2) → bump `evaluation_version`. RAG có thể vẫn ổn vì similarity nhìn pattern, nhưng nên backtest trước.
- Đổi benchmark tuyệt đối (vd P/E < 15) → cùng bump.
- Đổi sentiment LLM model (Ollama → Claude) → backtest đối chiếu, vì sentiment per-news khác model là khác hẳn.

## 11.9 Không thuộc phạm vi Module 11

- ❌ Fetch fundamental — Module 1.
- ❌ Quyết định Tier — Module 2.
- ❌ Macro indicators (DXY, WTI) — Module 10.
- ❌ Technical indicators — Module 9.
- ❌ User-fed knowledge — Module 4.

## API endpoints

Module 11 KHÔNG expose HTTP trực tiếp.

- Code: `from finance_bot.analysis.evaluation_micro import compute_micro_score`
- Doc: file này
- Test: `tests/analysis/test_evaluation_micro.py`

## Phân quyền (Capacities)

| Capacity | Chức năng |
|---|---|
| `evaluation.micro.run` | Reserved cho HTTP expose tương lai |
| `evaluation.micro.config` | Reserved cho admin chỉnh weight nội bộ qua web |
| `fundamentals.read` | Web dashboard hiển thị `FundamentalSnapshot` cho user |
| `fundamentals.manual_override` | Admin nhập thủ công khi vnstock thiếu/sai (status → `overridden`) |
