# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Tổng quan dự án

`finance-bot` là một CLI Python (không có daemon dài) chạy bằng `cron` trên Mac. Nó:

1. Sync OHLCV + VN flows + corporate events + RSS news vào MySQL.
2. Chạy rule-engine (15 indicator) trên khung 1D để ra `SignalDecision` draft.
3. Gọi Claude (qua Claude Code CLI local — `claude --print`) làm **final-arbiter** — chỉ được CONFIRM hoặc HẠ tier (không bao giờ up-tier, không flip side; nếu LLM flip side → ép `hold`/Tier C).
4. Chỉ alert Telegram cho **Tier A** (`agree_ratio ≥ 0.60` ~ ≥60% indicators agree, `confidence ≥ 0.75`, `news không ngược chiều`), với cooldown 1 alert/ticker/24h.
5. Học từ `outcomes` (P&L 1d/3d/7d/30d) + `user_decision` (callback button) bằng cách re-embed vào ChromaDB → run kế tiếp retrieve case lịch sử tương tự cho LLM.

Stack: Python 3.12 (`uv` + `hatchling`), MySQL 5.7 (SQLAlchemy 2.x ORM), Claude Code CLI (`claude-opus-4-7`, reuses local Claude login — không cần API key), ChromaDB, sentence-transformers (`paraphrase-multilingual-MiniLM-L12-v2`).

## Lệnh thường dùng

Mọi lệnh CLI đều đi qua `main.py` (xem [main.py](main.py) — `build_parser`):

```bash
# Setup
uv sync
uv run python main.py db-init           # SQLAlchemy create_all (idempotent)
uv run python main.py show-config       # dump effective .env + watchlist.yaml
uv run python main.py llm-health        # check `claude` CLI installed & runnable

# Data ingestion
uv run python main.py sync-prices [--symbol FPT]   # OHLCV + vn_flows + corporate_events
uv run python main.py sync-news                    # RSS → news table

# Signal pipeline (rule engine → news/macro → LLM arbiter → Telegram)
uv run python main.py run-signals [--symbol FPT]

# Closed-loop learning
uv run python main.py eval-outcomes      # P&L 1d/3d/7d/30d → outcomes table → re-embed
uv run python main.py process-feedback   # poll Telegram callback queue → user_decision
                                         # (cũng được gọi tự động ở đầu run-signals)

# Knowledge (open-loop user-fed RAG)
uv run python main.py add-knowledge --title "..." --body "..." --tags xau,dxy,macro
uv run python main.py list-knowledge
uv run python main.py sync-knowledge     # re-embed knowledge active vào ChromaDB
uv run python main.py rag-status         # đếm document mỗi collection

# Backtest
uv run python main.py backtest --start 2024-01-01 --end 2026-04-30 [--symbols FPT,HPG] [--output bt.csv]

# Web dashboard (Module 7) — quản lý watchlist + chart + signals qua trình duyệt
uv run python main.py seed-watchlist [--force]    # YAML → watchlist_entries (DB-backed)
# Khởi server ở TERMINAL RIÊNG (không qua chat — global rule):
./run.sh start                                     # normal — .env, MySQL, API :4030 + FE :4031
./run.sh start_test                                # sandbox — .env+.env.test, SQLite, :5030/:5031

# Test mode init schema (chỉ cần 1 lần, SQLite auto-tạo):
APP_ENV=test uv run python main.py db-init
APP_ENV=test uv run python main.py seed-watchlist
```

⚠️ **Test mode giới hạn**: SQLite không hỗ trợ `INSERT ... ON DUPLICATE KEY UPDATE` mà `repositories.bulk_upsert_*` đang dùng → các batch job (`sync-prices`, `sync-news`, `eval-outcomes`) sẽ **fail** khi `APP_ENV=test`. Test mode chỉ phục vụ **web layer + watchlist CRUD** (= scope đủ cho Playwright UI test).

Cron không gọi `main.py` trực tiếp — luôn qua wrapper [bin/run-cron.sh](bin/run-cron.sh), tự `cd` vào project, load `.env`, `caffeinate -i` để Mac không idle sleep, log vào `logs/cron.log`. Schedule mẫu trong [cron.example](cron.example).

### Dev tooling

```bash
uv run ruff check .                 # lint (E,F,I,B,UP,SIM, line-length=100, target=py312)
uv run mypy src/finance_bot         # type-check
uv run pytest                       # tests (asyncio_mode=auto, testpaths=tests). Hiện tests/ trống.
uv run pytest tests/path/test_x.py::test_name   # single test
```

Không dùng `python` trực tiếp — luôn `uv run python ...` để dùng `.venv` của project.

## Kiến trúc

Layout dạng layered (1 chiều phụ thuộc, không gọi ngược):

```
data/      → fetcher (vnstock / ccxt / yfinance / RSS / vn_flows / vn_events)
db/        → SQLAlchemy models + session + repositories + queries + schema.sql
analysis/  → technical (15 indicator votes) + signal (Tier A/B/C engine) + risk (ATR + S/R)
ai/        → llm (Claude CLI client) + embedding + rag (Chroma) + memory + prompt + arbiter
notifier/  → telegram (alert + inline keyboard "Đã vào lệnh / Bỏ qua")
jobs/      → orchestrators tương ứng với CLI subcommand
web/       → FastAPI app (Module 7) — JSON API trên repositories có sẵn
../web/    → Next.js 14 frontend (sibling của src/) — 3 trang: watchlist / charts / signals
```

`jobs/run_signals.py` là trung tâm: `_pull_feedback_safely()` (Telegram callback → user_decision) → load OHLCV → `analysis.signal.analyze` → build news + macro briefs → `ai.arbiter.arbitrate` (RAG retrieve) → `insert_signal` → maybe Telegram alert + cooldown.

### Settings & config

[src/finance_bot/settings.py](src/finance_bot/settings.py) là **single source of truth**:

- `Settings` (pydantic-settings, **multi-env**): đọc `.env` luôn, rồi nếu `APP_ENV` được set sẽ chồng `.env.${APP_ENV}` lên (override). Pattern giống Next.js / Rails. Currently used envs: `test` (→ `.env.test` chuyển sang SQLite sandbox). Cache: `@lru_cache get_settings()`. DB URL effective qua property `settings.db_url` (đọc `DATABASE_URL` nếu set, fallback build từ `MYSQL_*`).
- `Watchlist` (YAML + DB hybrid): `assets` đọc từ bảng `watchlist_entries` nếu có ≥1 active row (web dashboard CRUD), còn lại (news_sources / signal / risk / schedule) vẫn từ `config/watchlist.yaml`. Khi DB rỗng (lần boot đầu tiên hoặc chưa `seed-watchlist`) → fallback YAML hoàn toàn. Cache: `@lru_cache get_watchlist()`; web layer gọi `reload_watchlist_cache()` sau mọi mutation.

Khi sửa schema config → cập nhật cả pydantic model + YAML; cache được lru hoá nên test phải reset.

### Invariants quan trọng (đừng phá)

- **LLM final-arbiter** ([ai/arbiter.py](src/finance_bot/ai/arbiter.py)): áp `_TIER_RANK` để reject up-tier; nếu `llm_side != draft.side` → ép `hold`/`C`. Nếu Claude CLI không có / exit non-zero / trả JSON sai → giữ nguyên rule-engine draft, ghi lý do vào `llm_reasoning`. Mọi thay đổi logic arbitration phải giữ ràng buộc này.
- **Cooldown**: `latest_alerted_signal(within_hours=cooldown_hours_per_ticker)` chặn alert thứ 2 trong window — kể cả side ngược chiều. Tier B/C luôn ghi DB nhưng không alert (training data cho RAG sau).
- **`context_only` asset** (DXY, WTI): `arbitrate()` short-circuit, `analyze()` cũng dừng ở Tier C. Không bao giờ alert. Nhưng `sync-prices` vẫn fetch.
- **VN entry window**: signal cho `vn_stock` trả `entry_window="ato_next_session"` + `expected_entry_at = next VN open` (`_next_vn_ato_at` trong [analysis/signal.py](src/finance_bot/analysis/signal.py)). Asset class khác → `"immediate"`.
- **DB session**: dùng `with get_session() as session` ([db/session.py](src/finance_bot/db/session.py)); auto-commit khi exit, rollback on raise. LLM call (slow) phải nằm **ngoài** session — `run_for` mở 2 session riêng cho lý do này.

### RAG / closed-loop học

2 ChromaDB collection ([ai/rag.py](src/finance_bot/ai/rag.py)):

- `signals_history`: 1 document = 1 signal có ≥1 outcome. `eval-outcomes` upsert lại sau khi tính P&L. Arbiter retrieve `n=5` case tương tự đưa vào LLM prompt.
- `knowledge`: user-fed kiến thức (`add-knowledge`). Soft delete = `is_active=False` + xoá khỏi Chroma.

Knowledge entry trong MySQL có `chroma_id` để sync 2 chiều — khi update phải gọi `update_knowledge_chroma_id` ngay sau `learn_knowledge`.

## DB schema

`db-init` chạy `Base.metadata.create_all()` từ [db/models.py](src/finance_bot/db/models.py). File [db/schema.sql](src/finance_bot/db/schema.sql) là DDL canonical (đồng bộ với ORM). Bảng cốt lõi: `assets`, `prices`, `vn_flows`, `corporate_events`, `news`, `signals`, `outcomes`, `knowledge`, `fetch_log`, `watchlist_entries`. Mọi quyết định + LLM reasoning + RAG context đều ghi nguyên vào `signals` (cột JSON) — đừng tách ra schema phụ trừ khi có lý do hiệu năng cụ thể.

> `watchlist_entries` là source-of-truth cho danh sách asset cron theo dõi (CRUD qua web Module 7). `assets` là physical catalog đã từng sync ít nhất 1 lần (entity gắn FK với `prices`/`signals`).

## Quy ước

- Tất cả file dùng `from __future__ import annotations`, type hints bắt buộc.
- Reasoning + alert text dành cho user là **tiếng Việt**; code + comment + log + identifier giữ tiếng Anh.
- Logging: `from finance_bot.logger import logger` (loguru) — không `print` trừ trong CLI handler.
- Đừng start dev server / daemon dài; bot là one-shot CLI invoked by cron.
