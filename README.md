# finance-bot

Local AI-powered financial signal bot — phân tích đa khung (kỹ thuật + vĩ mô + tin tức + RAG) để bắn alert vào Telegram khi có tín hiệu **Tier A**, đồng thời học dần từ kết quả thực tế và phản hồi của user.

## Strategy đã chốt

- **Khung phân tích**: 1D (daily). Vị thế swing/position vài tuần đến vài tháng.
- **Tín hiệu Tier A** (chỉ Tier A mới alert): ≥ 4 indicators đồng thuận, confidence ≥ 0.75, news không ngược chiều.
- **Cooldown**: 1 alert / ticker / ngày, bất kể side.
- **LLM**: Final arbiter (chỉ confirm hoặc HẠ tier, không up-tier).
- **Risk**: SL = entry ± 2 × ATR (hoặc swing tighter), TP với R:R = 1 : 2.5.
- **Telegram**: text-only kèm 2 button "✅ Đã vào lệnh" / "⏭ Bỏ qua" → bot học từ phản hồi.

## Watchlist

| Loại | Symbols |
|---|---|
| **Primary signal targets** | FPT, HPG, MSN, MBS, MBB, BTC/USDT, XAU/USD |
| **Macro context only** | WTI (CL=F), DXY (DX-Y.NYB) |

## Stack

Python 3.12 · MySQL 5.7 · Claude Code CLI (`claude-opus-4-7`, reuses local login) · ChromaDB · pandas-ta · python-telegram-bot · vnstock 3.x · ccxt · yfinance · sentence-transformers (multilingual MiniLM)

## Setup một lần

```bash
# 1. Cài deps
uv sync

# 2. MySQL
mysql -u root -p < src/finance_bot/db/schema.sql
# hoặc:
uv run python main.py db-init

# 3. Configure
cp .env.example .env
# Sửa: MYSQL_PASSWORD, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# 4. Claude Code CLI (đăng nhập 1 lần, không cần API key riêng cho project)
# Cài Claude Code (chỉ cần làm 1 lần trên máy): https://docs.claude.com/en/docs/claude-code/quickstart
# Sau khi cài + `claude login`, kiểm tra:
uv run python main.py llm-health

# 5. Smoke-test
uv run python main.py show-config
uv run python main.py sync-prices --symbol FPT
uv run python main.py sync-news
uv run python main.py run-signals --symbol FPT
```

## CLI tham khảo

```bash
# Data
sync-prices [--symbol X]    # OHLCV + (vn_stock) flows + corporate events
sync-news                   # RSS feeds → news table

# Signal pipeline
run-signals [--symbol X]    # rule engine → news/macro → LLM arbiter → Telegram

# Học
eval-outcomes               # tính P&L 1d/3d/7d/30d → re-embed vào RAG
process-feedback            # poll Telegram callback → ghi user_decision

# Knowledge base (cập nhật kiến thức cho bot)
add-knowledge --title "..." --body "..." --tags ...
list-knowledge
sync-knowledge              # re-embed tất cả vào ChromaDB
rag-status                  # đếm document mỗi collection

# Backtest
backtest --start 2024-01-01 --end 2026-04-30 [--symbols FPT,HPG] [--output bt.csv]

# Health & config
db-init
show-config
llm-health
```

## Scheduling (cron, không daemon dài)

```bash
# 1. Sửa PROJECT path trong cron.example, rồi:
crontab -e
# paste nội dung cron.example đã chỉnh.
crontab -l        # verify

# 2. (khuyến nghị) Mac không sleep ban đêm để job 06:00 không miss:
#    System Settings → Energy Saver → "Prevent automatic sleeping..."
#    hoặc khi cắm sạc:
sudo pmset -a sleep 0
```

Lịch trong [`cron.example`](cron.example):

| Local time | Job | Vì sao |
|---|---|---|
| 06:00 | sync-prices, sync-news, eval-outcomes, sync-knowledge | Học từ kết quả ≥1d cũ + chuẩn bị data sáng |
| 15:15 (T2-T6) | sync-prices, sync-news | HOSE đóng cửa 14:45 → fetch EOD VN |
| 16:00 (T2-T6) | run-signals | Tier A alert + tự pull Telegram feedback ở đầu pipeline |
| CN 09:00 | backtest 1 năm | Sanity check |

`run-signals` tự gọi `process-feedback` ở đầu pipeline → không cần cron entry riêng cho feedback. Nếu muốn DB có `user_decision` sớm hơn (vd để query/báo cáo trong ngày), uncomment dòng `*/15` trong `cron.example`.

Wrapper [`bin/run-cron.sh`](bin/run-cron.sh) lo: `cd` vào project, load `.env`, set PATH, `caffeinate -i` để Mac không idle sleep, log vào `logs/cron.log`.

## Vòng học của bot — "thông minh hơn từng ngày"

**Closed-loop (tự động)**

```
run-signals → signal vào DB
               │
               ├─► Telegram alert + 2 button
               │       │
               │       └─► user click → process-feedback → user_decision
               │
               ▼ (đợi 1d/3d/7d/30d)
eval-outcomes → tính P&L thực tế → outcomes table
               │
               ▼
       re-embed signal+outcomes+user_decision → ChromaDB.signals_history
               │
               ▼
next run-signals → arbiter retrieve case lịch sử tương tự → LLM rút kinh nghiệm
```

**Open-loop (user feed kiến thức mới)**

```
add-knowledge "DXY > 105 ép vàng giảm" tags=xau,dxy,macro
        │
        └─► MySQL.knowledge + ChromaDB.knowledge
                    │
                    ▼
        next run-signals retrieve → LLM tham khảo
```

## DB cores

| Bảng | Vai trò |
|---|---|
| `assets` | Catalog (có `context_only`) |
| `prices` | OHLCV theo `(asset, timeframe, ts)` |
| `vn_flows` | Khối ngoại / tự doanh / margin theo ngày (chỉ vn_stock) |
| `corporate_events` | GDKHQ, cổ tức, phát hành thêm |
| `news` | RSS đã chuẩn hoá |
| `signals` | Mọi quyết định + indicators + LLM reasoning + RAG context + user_decision |
| `outcomes` | P&L thực tế sau 1d/3d/7d/30d → feedback cho RAG |
| `knowledge` | Kiến thức user-fed (canonical), gắn `chroma_id` |
| `fetch_log` | Debug & gap-detection |

## Layout

```
src/finance_bot/
  data/        # price + flow + event + news fetchers
  db/          # SQLAlchemy models, schema.sql, repositories, queries
  analysis/    # technical, risk, signal engine
  ai/          # llm, embedding, rag, memory, arbiter, prompt
  notifier/    # telegram (alert + inline keyboard)
  jobs/        # sync_prices, sync_news, sync_knowledge,
               # run_signals, eval_outcomes, process_feedback, backtest
config/
  watchlist.yaml
bin/
  run-cron.sh
cron.example
```

## Web Dashboard (Module 7)

Giao diện web cục bộ để quản lý watchlist + xem chart + tra cứu signals — chi tiết trong [docs/business/7-web-dashboard.md](docs/business/7-web-dashboard.md).

```bash
# 1. Setup 1 lần
uv sync                                                 # cài fastapi + uvicorn
uv run python main.py db-init                           # tạo bảng watchlist_entries (MySQL)
uv run python main.py seed-watchlist                    # nạp YAML → DB lần đầu
cd web && (pnpm install || npm install) && cp .env.example .env.local && cd ..

# 2. Chạy (terminal riêng — không qua chat, global rule)
./run.sh start                                          # normal:   API :4030 + FE :4031 → MySQL finance_bot
./run.sh start_test                                     # sandbox:  API :5030 + FE :5031 → SQLite throwaway

# 3. (lần đầu chạy start_test) init schema SQLite — auto-tạo file .cache/finance_test.db
APP_ENV=test uv run python main.py db-init
APP_ENV=test uv run python main.py seed-watchlist
```

Ports:
- **start**: backend **4030**, frontend **4031** (non-ACM block `4030-4039`)
- **start_test**: backend **5030**, frontend **5031** (sandbox `5030-5039` — mirror offset theo convention port-allocator)

3 màn hình chính:
- `/watchlist` — CRUD mã theo dõi, pause/active, export YAML
- `/charts/[symbol]` — candlestick + EMA / Bollinger / RSI / MACD + signal markers
- `/signals` — filter theo tier / side / decision, drawer detail kèm outcomes + LLM reasoning

### Multi-env config (`APP_ENV`)

`pydantic-settings` (đã có sẵn trong deps) đọc nhiều env file theo thứ tự:

| Mode | `APP_ENV` | Files load | DB |
|---|---|---|---|
| `./run.sh start` | unset | `.env` | MySQL `finance_bot` |
| `./run.sh start_test` | `test` | `.env` → `.env.test` (override) | SQLite `./.cache/finance_test.db` |

`.env.test` (commit được, không chứa secret) blank `MYSQL_PASSWORD` + `TELEGRAM_*` để defensive: nếu code accidentally bypass `DATABASE_URL` trong test mode sẽ fail loud thay vì im lặng hit dev MySQL hoặc spam Telegram thật.

⚠️ **Giới hạn test mode**: SQLite không hỗ trợ `INSERT ... ON DUPLICATE KEY UPDATE` mà `repositories.bulk_upsert_*` đang dùng. Các batch job (`sync-prices`, `sync-news`, `eval-outcomes`) **sẽ fail** khi chạy với `APP_ENV=test`. Test mode chỉ phục vụ web layer + watchlist CRUD (= scope đủ cho Playwright UI test). Để test batch jobs cần dùng MySQL test DB riêng (không trong scope hiện tại).

## Roadmap

- [x] **M1** scaffold, schema, fetchers (vnstock + flows + events, ccxt, yfinance, RSS)
- [x] **M2** technical indicators + Tier A rule engine + Telegram alert
- [x] **M3** Claude CLI LLM final-arbiter + news + macro context (DXY, WTI)
- [x] **M4** RAG (Chroma) + outcomes loop + knowledge base (cập nhật kiến thức mới)
- [x] **M5** backtest + cron-style schedule + Telegram feedback loop (`user_decision`)
- [x] **M6** web dashboard (FastAPI + Next.js) — watchlist CRUD, charts, signal history
