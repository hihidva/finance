# Module 7: Web Dashboard (Quản lý bot qua trình duyệt)

> Một file = một domain. Mọi section dưới đây thuộc cùng domain "Web Dashboard".

## 7.1 Mục đích

Cung cấp giao diện web cục bộ để chủ bot quản lý watchlist, quan sát giá + chỉ báo kỹ thuật trực quan, và tra cứu lịch sử tín hiệu — thay cho việc edit `config/watchlist.yaml` bằng tay và đọc DB qua MySQL CLI.

**Đầu vào:**
- `assets`, `prices`, `signals`, `outcomes`, `news` từ MySQL (Module 1, 2, 3)
- Indicator computation tái dùng [analysis/technical.py](../../src/finance_bot/analysis/technical.py) (RSI, MACD, EMA, Bollinger, ATR…)
- Watchlist seed từ [config/watchlist.yaml](../../config/watchlist.yaml) trong lần boot đầu tiên

**Đầu ra:**
- Bảng `watchlist_entries` mới (DB-backed thay cho YAML — xem §7.3)
- HTTP API JSON (FastAPI) phục vụ frontend
- Next.js app render 3 màn hình chính (xem §7.4)

**Không thuộc scope:**
- Multi-user / authentication / RBAC — bot vẫn single-user localhost
- Realtime push (websocket) — phase 1 dùng polling đơn giản
- Trading execution — dashboard chỉ là view + config, không đặt lệnh

## 7.2 Architecture

```
┌──────────────────────────┐     HTTP/JSON      ┌──────────────────────────┐
│  Next.js 14 (App Router) │ ─────────────────▶ │  FastAPI                 │
│  web/                    │                    │  src/finance_bot/web/    │
│  - app/watchlist/        │                    │  - api/watchlist.py      │
│  - app/charts/[symbol]/  │                    │  - api/prices.py         │
│  - app/signals/          │                    │  - api/signals.py        │
│  - lib/api-client.ts     │                    │  - main.py (uvicorn)     │
└──────────────────────────┘                    └──────────┬───────────────┘
                                                           │ SQLAlchemy
                                                           ▼
                                                ┌──────────────────────────┐
                                                │  MySQL (existing schema  │
                                                │  + watchlist_entries)    │
                                                └──────────────────────────┘
```

- **Backend**: FastAPI ở port **4030** (normal) hoặc **5030** (test mode) — sibling process với cron jobs, share cùng DB.
- **Frontend**: Next.js ở port **4031** (normal) hoặc **5031** (test mode), proxy `/api/*` sang backend (Next.js rewrites).
- Hai mode `start` / `start_test` chia sẻ codebase, khác nhau ở: (1) port (block `4030-4039` vs mirror `5030-5039` — convention port-allocator), (2) DB name (`finance_bot` vs `finance_test`).
- Cả hai cùng host localhost — không cần CORS phức tạp; FastAPI allow-list cả 2 origin frontend (4031 + 5031).
- Tái sử dụng `get_session()` từ [db/session.py](../../src/finance_bot/db/session.py) và toàn bộ repositories có sẵn — không viết lại data access.

## 7.3 Entities

### `watchlist_entries` (mới)

Thay thế vai trò "source of truth của watchlist" từ `config/watchlist.yaml` sang DB để web UI có thể CRUD an toàn (tránh race với cron đang đọc YAML).

| Trường | Kiểu | Mô tả |
|---|---|---|
| `id` | int PK | Auto-increment |
| `symbol` | varchar(32) UNIQUE | `FPT`, `BTC/USDT`, … (đồng bộ key với `assets.symbol`) |
| `name` | varchar(128) | Human-readable, dùng trên UI |
| `asset_class` | enum | `vn_stock`, `crypto`, `commodity`, `macro` |
| `source` | varchar(32) | `vnstock`, `ccxt`, `yfinance` |
| `exchange` | varchar(32) nullable | HOSE / HNX / UPCOM (chỉ VN) |
| `timeframes` | JSON | `["1d"]` — list, scaffold cho tương lai |
| `context_only` | boolean | True = chỉ feed LLM, không sinh signal |
| `is_active` | boolean | False = pause (skip ở `sync-prices` + `run-signals` mà không cần xoá) |
| `note` | text nullable | Ghi chú free-text của user |
| `created_at`, `updated_at` | datetime | Audit |

**Migration path** (xem §7.7 Edge cases để biết rủi ro):

1. `db-init` tạo bảng trống.
2. Lệnh mới `seed-watchlist` (Module 6) đọc `watchlist.yaml` upsert vào `watchlist_entries`.
3. Settings loader chuyển từ `get_watchlist()` đọc YAML → đọc DB (có fallback YAML nếu DB trống — backward-compat lần boot đầu).
4. YAML giữ lại như **snapshot khởi tạo / backup**, không còn là source of truth sau khi seed.

### Re-dùng entities có sẵn

| Entity | Module gốc | Dùng cho màn hình |
|---|---|---|
| `assets`, `prices` | 1 | Charts (§7.4.2) |
| `signals` | 2 | Signal history (§7.4.3) |
| `outcomes` | 3 | Signal history (cột P&L 1d/3d/7d/30d) |
| `news` | 1 | Có thể hiện kèm trong detail signal (phase 2) |

## 7.4 Màn hình

### 7.4.1 Cấu hình mã theo dõi (`/watchlist`)

CRUD trên `watchlist_entries`.

**Cột bảng list:**

| Cột | Hiển thị |
|---|---|
| Symbol | `FPT` (bold) |
| Tên | `FPT Corporation` |
| Asset class | badge màu (`vn_stock` xanh, `crypto` cam, `commodity` vàng, `macro` xám) |
| Source | `vnstock` / `ccxt` / `yfinance` |
| Context only | toggle (read-only nếu không sửa) |
| Trạng thái | `Active` / `Paused` (toggle inline) |
| Thao tác | `Edit`, `Delete` |

**Form thêm / sửa:**

- Symbol (text, required, regex `^[A-Z0-9./-]{1,32}$`)
- Tên (text, optional — default = symbol)
- Asset class (select, required)
- Source (select dependent on asset_class — `vn_stock` → `vnstock` only, `crypto` → `ccxt`, `commodity`/`macro` → `yfinance`)
- Exchange (chỉ hiện khi `vn_stock`)
- Context only (checkbox, default false)
- Note (textarea, optional)

**Actions ngoài CRUD:**

- `Sync now` (gọi `POST /api/watchlist/{id}/sync` → background task `sync-prices --symbol X`).
- `Export YAML` (tải về snapshot YAML tương đương `watchlist.yaml` từ DB hiện tại — để backup / version control).

### 7.4.2 Đồ thị giá + chỉ báo (`/charts/[symbol]`)

Candlestick chart 1D + overlay indicator.

**Layout:**

```
┌────────────────────────────────────────────────────────────────┐
│  FPT — FPT Corporation                            [Sync now]   │
│  Last close: 125,300đ  (+0.8%)  ·  Volume: 4.2M  ·  2026-05-22 │
├────────────────────────────────────────────────────────────────┤
│  Timeframe: [1D]   Lookback: [3M] [6M] [1Y] [All]              │
│  Indicators: ☑ EMA20  ☑ EMA50  ☑ EMA200  ☐ Bollinger  ☐ RSI    │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│     [candlestick chart + EMA lines overlay]                    │
│                                                                │
├────────────────────────────────────────────────────────────────┤
│     [volume bars]                                              │
├────────────────────────────────────────────────────────────────┤
│     [RSI panel — chỉ hiện khi tick RSI]                        │
└────────────────────────────────────────────────────────────────┘
```

**Indicator có sẵn (tái dùng `analysis/technical.py`):**

| Toggle | Tính bằng | Vẽ ở |
|---|---|---|
| EMA20 / EMA50 / EMA200 | `ema(close, n)` | Main pane |
| Bollinger Bands(20, 2σ) | `bollinger(close, 20, 2)` | Main pane (3 line) |
| RSI(14) | `rsi(close, 14)` | Sub pane |
| MACD(12,26,9) | `macd(close)` | Sub pane (line + signal + histogram) |
| ATR(14) | `atr(high, low, close, 14)` | Sub pane (line) |
| Volume MA(20) | `sma(volume, 20)` | Volume pane overlay |

**Markers trên chart**: render dot/arrow tại các bar đã có signal (`buy` xanh ↑, `sell` đỏ ↓, `hold` xám ○) — click marker mở popover summary signal.

**Library**: `lightweight-charts` (TradingView) — free, performant, native candlestick + multi-pane.

### 7.4.3 Lịch sử tín hiệu (`/signals`)

Bảng tra cứu signals + outcomes.

**Filters trên header:**

- Symbol (multi-select từ `watchlist_entries`)
- Tier (multi: A / B / C)
- Side (multi: buy / sell / hold)
- Date range (default 30 ngày gần nhất)
- User decision (`Tất cả` / `Đã vào lệnh` / `Bỏ qua` / `Chưa phản hồi`)
- Notified only (checkbox — default off, vì Tier B/C không alert nhưng vẫn quan trọng để xem)

**Cột bảng:**

| Cột | Format |
|---|---|
| Time | `2026-05-22 16:01` |
| Symbol | `FPT` (link tới `/charts/FPT` với date marker) |
| Tier | badge (A xanh đậm, B vàng, C xám) |
| Side | `BUY` xanh / `SELL` đỏ / `HOLD` xám |
| Confidence | `0.82` |
| Indicators agree | `4/7 buy` |
| Entry / SL / TP | `125,300 / 119,800 / 138,750` |
| PnL 1d / 3d / 7d / 30d | `+1.2% / +3.8% / +5.1% / +8.9%` (xám nếu chưa đủ horizon) |
| User decision | icon ✅ Đã vào / ⏭ Bỏ qua / `—` |
| Notified | ✅ / ❌ |
| Detail | nút `View` mở drawer |

**Drawer chi tiết** (click `View`):

- Toàn bộ JSON `indicators`, `news_context`, `rag_context`
- `llm_reasoning` (tiếng Việt, do Claude trả)
- Danh sách `outcomes` đã ghi
- Link xem chart tại thời điểm signal
- Nút `Mark as entered` / `Mark as skipped` — cho phép override `user_decision` thủ công (nếu user bỏ lỡ Telegram button)

## 7.5 API endpoints

Tất cả ở scope **Local Admin**, không có public scope. Prefix `/api`.

```
# Watchlist
GET    /api/watchlist                       — list (filter ?asset_class=&is_active=)
POST   /api/watchlist                       — create entry
GET    /api/watchlist/{id}                  — detail
PATCH  /api/watchlist/{id}                  — partial update (toggle is_active, edit note, …)
DELETE /api/watchlist/{id}                  — hard delete (chặn nếu còn signal/prices liên quan — xem §7.6)
POST   /api/watchlist/{id}/sync             — trigger sync-prices background (returns job_id)
GET    /api/watchlist/export                — trả YAML snapshot (text/yaml)

# Prices & indicators
GET    /api/prices/{symbol}                 — OHLCV (params: ?timeframe=1d&lookback=180)
GET    /api/indicators/{symbol}             — computed indicators
                                              (params: ?names=ema20,ema50,rsi&lookback=180)

# Signals
GET    /api/signals                         — list with filters
                                              (?symbols=FPT,HPG&tiers=A,B&sides=buy,sell
                                               &from=2026-04-01&to=2026-05-22
                                               &user_decision=entered&notified=true
                                               &page=1&page_size=50)
GET    /api/signals/{id}                    — detail with outcomes + JSON contexts
PATCH  /api/signals/{id}/user-decision      — body: {decision: "entered"|"skipped"|null}

# Meta
GET    /api/health                          — pings DB + Claude CLI (reuse llm-health logic)
GET    /api/stats                           — counters (assets, signals last 30d, alerts last 30d, …)
                                              dùng trên trang chủ dashboard nếu thêm sau
```

Response chung dùng JSON với `snake_case` (match Python convention). Time ở `published_at`, `ts`, `created_at` luôn là **UTC ISO 8601** — frontend chịu trách nhiệm convert sang `Asia/Ho_Chi_Minh` để hiển thị.

## 7.6 Validation rules

**Watchlist:**

- `symbol` UNIQUE (`watchlist_entries.symbol`) — `POST` duplicate → 409 + message "Symbol đã tồn tại".
- Khi sửa `symbol` mà đã có row trong `prices` / `signals` reference → **không cho đổi** (raise 422 "Symbol đã có dữ liệu lịch sử, hãy tạo entry mới và pause cái cũ").
- `DELETE` chặn nếu còn `signals.asset_id` reference. Trả 422 với hint "Pause thay vì xoá để giữ lịch sử".
- `context_only=true` không cần `exchange` (kể cả VN macro).
- `source` phải match `asset_class` (mapping ở §7.4.1 form).

**Charts:**

- `lookback` tối đa 730 ngày (~2 năm). Vượt → clamp về 730 + warning header `X-Lookback-Clamped: true`.
- Symbol không có trong `assets` → 404. Symbol có nhưng chưa có `prices` → trả `[]` + 200 (không phải lỗi, chỉ là chưa sync).

**Signals:**

- Filter `from > to` → 422.
- `page_size` ≤ 200, default 50.
- `user_decision` chỉ accept enum `entered` / `skipped` / `null` (clear). Giá trị khác → 422.
- `PATCH /api/signals/{id}/user-decision` set timestamp `user_decision_at = now()` — KHÔNG cho client truyền timestamp.

## 7.7 Edge cases

- **Lần đầu chạy dashboard, `watchlist_entries` còn rỗng**: API `GET /api/watchlist` trả `[]`. UI hiện empty state + nút "Seed từ watchlist.yaml" gọi endpoint `POST /api/watchlist/seed-from-yaml` (chạy lệnh `seed-watchlist` server-side).
- **Cron đang chạy `sync-prices` thì user click `Sync now`**: API kiểm tra `fetch_log` row gần nhất status=running cho symbol đó → trả 409 "Đang sync, đợi tối đa N phút". Không launch song song.
- **Claude CLI không khả dụng khi mở `/charts/[symbol]`**: chart vẫn render bình thường (chỉ cần `prices`); chỉ `GET /api/health` báo `llm: false`. Không block UI.
- **Frontend gọi indicator chưa support**: API trả 422 với list `available` names — UI fallback bỏ checkbox đó.
- **DB schema lệch với code** (vd vừa thêm cột mới, chưa migrate): SQLAlchemy raise → FastAPI trả 500. Hint trong response: "Run `db-init` hoặc apply migration". Không cố che lỗi.
- **Watchlist YAML và DB lệch nhau** (sau khi web đã edit, user lại sửa YAML thủ công): YAML không còn là source of truth — cron đọc DB. Tài liệu README nói rõ điều này. Nút `Export YAML` để re-sync nếu cần.
- **Concurrent edit cùng entry** (2 tab cùng mở): dùng `If-Match: updated_at` header (optimistic locking). Conflict → 412, UI prompt user reload.
- **Browser timezone khác `Asia/Ho_Chi_Minh`**: UI vẫn convert UTC sang ICT (hardcode), KHÔNG dùng `navigator.timezone` — đảm bảo nhất quán với cron schedule.

## 7.8 Dev / build commands

**Khởi server (preferred — wrapper [run.sh](../../run.sh)):**

```bash
./run.sh start         # normal:   API :4030 + FE :4031 → MySQL finance_bot
./run.sh start_test    # sandbox:  API :5030 + FE :5031 → SQLite ./.cache/finance_test.db
```

Wrapper start cả backend + frontend trong cùng process tree, forward log ra stdout, Ctrl-C kill cả 2. Pre-flight check `lsof` chặn launch khi port đang bị chiếm.

**Multi-env config (APP_ENV)**:

| Mode | `APP_ENV` | Env files (theo thứ tự load) | DB engine |
|---|---|---|---|
| `start` | unset | `.env` | MySQL (`MYSQL_*` fields) |
| `start_test` | `test` | `.env` (base) → `.env.test` (override) | SQLite (`DATABASE_URL`) |

`pydantic-settings` (đã có) hỗ trợ native `env_file = list` — file sau override file trước. `.env.test` blank `MYSQL_PASSWORD` + `TELEGRAM_*` để defensive (fail loud thay vì silently hit dev MySQL nếu code bypass `DATABASE_URL`).

⚠️ **Giới hạn SQLite test mode**: `repositories.bulk_upsert_*` dùng `mysql_insert + on_duplicate_key_update` (MySQL-specific) → các batch job (`sync-prices`, `sync-news`, `eval-outcomes`) **sẽ fail** khi `APP_ENV=test`. Test mode chỉ phục vụ web layer + watchlist CRUD — đủ cho Playwright UI test. Muốn test full pipeline → dùng MySQL test DB riêng (chuyển `DATABASE_URL` trong `.env.test` sang `mysql+pymysql://...finance_test`).

**Khởi thủ công (debug từng layer):**

```bash
uv add fastapi uvicorn[standard]               # dependency mới (1 lần)
uv run uvicorn finance_bot.web.main:app \
    --host 127.0.0.1 --port 4030 --reload      # backend dev
cd web && pnpm install && pnpm dev             # frontend dev (đọc NEXT_PUBLIC_API_BASE)
```

**Seed data lần đầu:**

```bash
uv run python main.py db-init                  # tạo bảng (idempotent)
uv run python main.py seed-watchlist           # YAML → DB
# Test DB: MYSQL_DATABASE=finance_test uv run python main.py db-init
```

Cả hai chạy ở **terminal riêng** — KHÔNG khởi từ cron (xem CLAUDE.md global rule cấm dev server qua bash chat). Nếu deploy production local, dùng `launchd` (macOS) hoặc viết script wrapper tương tự [bin/run-cron.sh](../../bin/run-cron.sh).

**CLI mới (Module 6 extend):**

```bash
seed-watchlist [--force]    # đọc watchlist.yaml → upsert watchlist_entries
                            # --force overwrite mọi entry; không có flag → chỉ insert mới
```

## 7.9 Phân quyền

Single-user localhost — không có RBAC. Bảo vệ bằng cách bind **chỉ `127.0.0.1`** (`--host 127.0.0.1`), không expose ra LAN. Nếu sau này cần share LAN / cloud:

- Thêm middleware basic-auth ở FastAPI (user/pass từ `.env`).
- Frontend đọc cùng credential qua server component / Next.js middleware.
- KHÔNG dùng dashboard này như production multi-tenant — kiến trúc không thiết kế cho RBAC.
