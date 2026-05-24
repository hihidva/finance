# Business Documentation — finance-bot

Tài liệu nghiệp vụ cho từng bounded context của `finance-bot`. Mỗi module tương ứng 1 nhóm CLI subcommand cùng domain.

| # | Module | Phạm vi | Tài liệu |
|---|---|---|---|
| 1 | Data Ingestion | OHLCV + VN flows + corporate events + RSS news | [1-data-ingestion.md](business/1-data-ingestion.md) |
| 2 | Signal Pipeline | Rule engine → LLM arbiter → Telegram alert | [2-signal-pipeline.md](business/2-signal-pipeline.md) |
| 3 | Closed-loop Learning | Outcomes + Telegram feedback + RAG re-embed | [3-closed-loop-learning.md](business/3-closed-loop-learning.md) |
| 4 | Knowledge Base | User-fed RAG (open-loop) | [4-knowledge-base.md](business/4-knowledge-base.md) |
| 5 | Backtest | Sanity check rule engine trên window lịch sử | [5-backtest.md](business/5-backtest.md) |
| 6 | System Operations | db-init, config, health, cron wrapper | [6-system-operations.md](business/6-system-operations.md) |
| 7 | Web Dashboard | Quản lý watchlist + chart + lịch sử signal qua trình duyệt | [7-web-dashboard.md](business/7-web-dashboard.md) |
| 8 | Indicators Catalog | Catalog 14 chỉ báo active + vote-engine contract + lifecycle | [8-indicators-catalog.md](business/8-indicators-catalog.md) |
| 9 | Technical Evaluation | Service tổng hợp 14 indicator vote → `TechScore ∈ [-1, +1]` (1/3 thước đo) | [9-technical-evaluation.md](business/9-technical-evaluation.md) |
| 10 | Macro Evaluation | Service tổng hợp DXY/WTI/FFR/10Y theo sensitivity per-asset-class → `MacroScore ∈ [-1, +1]` (1/3 thước đo) | [10-macro-evaluation.md](business/10-macro-evaluation.md) |
| 11 | Micro Evaluation | Service tổng hợp fundamentals (ROA/ROE/P/E/P/B) + industry avg + news sentiment → `MicroScore ∈ [-1, +1]` (1/3 thước đo) | [11-micro-evaluation.md](business/11-micro-evaluation.md) |

## Cross-cutting concerns

- **Strategy đã chốt**: 1D timeframe, Tier A only. **Cách tính Tier** đã nâng cấp: composite score = `(tech + macro + micro) / 3` với trọng số bằng nhau; Tier A khi `|composite| ≥ 0.60` + ≥ 2 service đồng thuận + `news_against = False`. Cooldown 1 alert/ticker/24h.
- **LLM invariant**: Final arbiter chỉ confirm hoặc HẠ tier — không bao giờ up-tier, không flip side.
- **Asset classes**: `vn_stock` (FPT, HPG, MSN, MBS, MBB), `crypto` (BTC/USDT), `commodity` (XAU/USD), `macro` (WTI, DXY — `context_only=True`).
- **Watchlist**: single source of truth ở [config/watchlist.yaml](../config/watchlist.yaml).
- **Chạy bằng cron**, không daemon dài. Schedule mẫu: [cron.example](../cron.example).
