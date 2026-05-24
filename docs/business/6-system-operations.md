# Module 6: System Operations (Health, Config, Cron wrapper)

## 6.1 Mục đích

Operational tooling để setup và verify bot hoạt động. Không có business logic riêng — chỉ là utilities + cron orchestration.

## 6.2 CLI commands

### `db-init`

[main.py cmd_db_init](../../main.py):

```python
Base.metadata.create_all(engine)   # SQLAlchemy idempotent
```

Idempotent: chạy nhiều lần không tạo duplicate. Không drop hay alter — chỉ create nếu chưa có.

**Alternative**: `mysql -u root -p < src/finance_bot/db/schema.sql` — DDL canonical (đồng bộ với ORM).

### `show-config`

In ra effective settings + watchlist:

```
=== settings ===
  mysql_url       = mysql+pymysql://...
  claude_binary   = claude
  claude_model    = claude-opus-4-7
  claude_timeout  = 120s
  embedding_model = paraphrase-multilingual-MiniLM-L12-v2
  log_level       = INFO

=== watchlist (9 assets, 7 primary / 2 context) ===
  [   ] FPT        [vn_stock] src=vnstock  tfs=['1d']
  [   ] HPG        [vn_stock] src=vnstock  ...
  [ctx] DX-Y.NYB  [macro   ] src=yfinance ...

=== news_sources (5) ===
  - CafeF (vi) tags=['vn_stock', 'macro_vn']
  ...

=== signal ===
  Tier A: agree>=4, conf>=0.75, news_not_against=True
  Tier B: agree>=3, conf>=0.60
  cooldown_hours_per_ticker = 24
  default_horizon_days      = 7

=== risk ===
  ATR period           = 14
  SL = entry ± 2.0*ATR
  TP target R:R        = 1:2.5

=== schedule (Asia/Ho_Chi_Minh) ===
  vn_eod_close_local = 15:15
  global_eod_local   = 23:00
  signal_run_local   = 16:00
```

Dùng để debug khi deployment lệch giữa `.env` thật và state code đang chạy.

### `llm-health`

Check Claude CLI is installed & runnable on the local machine:

```
Claude binary:  /opt/homebrew/bin/claude
Model:          claude-opus-4-7
Health:         OK | FAILED
```

Exit code: 0 nếu OK, 1 nếu FAILED. Chỉ chạy `claude --version`, không gọi API thật.

### `rag-status`

Đếm document mỗi ChromaDB collection:

```
=== RAG status ===
  signals_history        42 documents
  knowledge              7 documents
```

Dùng để verify `eval-outcomes` và `add-knowledge` đã embed thành công.

## 6.3 Cron wrapper

[bin/run-cron.sh](../../bin/run-cron.sh) — bash script xử lý mọi việc cron không tự lo:

```bash
#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

# Load .env
[[ -f .env ]] && { set -a; source .env; set +a; }

mkdir -p logs
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

ts() { date "+%Y-%m-%d %H:%M:%S"; }
echo "[$(ts)] >>> $*" >>logs/cron.log

# caffeinate -i: ngăn idle sleep, vẫn cho display sleep
if command -v caffeinate >/dev/null 2>&1; then
  caffeinate -i uv run python main.py "$@" >>logs/cron.log 2>&1
else
  uv run python main.py "$@" >>logs/cron.log 2>&1
fi

echo "[$(ts)] <<< $* (exit=$?)" >>logs/cron.log
```

**Trách nhiệm:**
- `cd` vào project root → `uv` tìm được `.venv` + `pyproject.toml`
- `source .env` → MYSQL_PASSWORD, TELEGRAM_BOT_TOKEN, ... vào env
- Bổ sung PATH (cron khởi động chỉ có `/usr/bin:/bin`)
- `caffeinate -i` giữ Mac không idle sleep TRONG LÚC chạy
- Log timestamp + exit code vào `logs/cron.log`

## 6.4 Scheduler (Laravel-style — 1 cron entry, mọi batch khai báo trong code)

Trước đây mỗi batch là 1 dòng crontab riêng → user phải sửa crontab mỗi lần thêm job. Đã chuyển sang **single-tick scheduler** kiểu Laravel: crontab chỉ có **1 dòng** duy nhất, ticking mỗi phút; danh sách batch khai báo trong [config/schedule.py](../../config/schedule.py).

### 6.4.1 Crontab

```cron
* * * * * /Users/pc7/Desktop/code/finance/bin/run-cron.sh schedule-run
```

Một dòng. Không bao giờ thay đổi khi thêm batch mới. Xem [cron.example](../../cron.example).

### 6.4.2 Config — `config/schedule.py`

Declarative DSL — fluent API trên class `Schedule`:

```python
from finance_bot.jobs.scheduler import Schedule
schedule = Schedule()

schedule.command("sync-prices").cron("0 6 * * 1-7")           # raw cron
schedule.command("run-signals").weekdays_at("16:00")           # T2-T6 16:00
schedule.command("sync-industry-averages").weekly_on("monday", "08:00")
schedule.command("backtest", args=["--start", "2025-01-01", "--end", "2026-05-31"]) \
        .weekly_on("sunday", "09:00")
```

Helpers trên `ScheduledTask`:

| Method | Cron tương đương |
|---|---|
| `.cron("0 6 * * 1-7")` | raw 5-field |
| `.daily_at("06:00")` | `0 6 * * *` |
| `.weekdays_at("16:00")` | `0 16 * * 1-5` |
| `.weekly_on("monday", "08:00")` | `0 8 * * 1` |

### 6.4.3 Engine — `src/finance_bot/jobs/scheduler.py`

Pipeline:

```
crontab → run-cron.sh schedule-run → cmd_schedule_run → run_due_tasks()
                                                          ├─ load_schedule()  (import config/schedule.py)
                                                          ├─ now = datetime.now()
                                                          ├─ due = [t for t in tasks if croniter.match(t.cron_expr, now)]
                                                          └─ for t in due: subprocess(`uv run python main.py <command> <args>`)
```

Cron matching dùng `croniter.match()` (5-field, accent local time như crontab).

### 6.4.4 Quyết định thiết kế

| Quyết định | Lý do |
|---|---|
| Không lock concurrency | Cron spacing trong `config/schedule.py` đủ tách — 2 task overlap rất hiếm; lock thêm complexity không xứng. |
| Không `caffeinate -i` cho `schedule-run` | Tick mỗi phút → spawn `caffeinate` mỗi tick wasteful. Mac sleep → miss tick, chấp nhận. Per-task `caffeinate` vẫn áp dụng cho các batch dispatched từ scheduler. |
| Silent ticks (DEBUG only) | 1440 tick/ngày — INFO log mỗi tick rỗng sẽ spam `logs/cron.log`. Tick có task → INFO; rỗng → DEBUG (skip). |
| 1h subprocess timeout | Mọi batch hiện tại < 5 phút. 1h là safety net cho job bị treo. |

### 6.4.5 CLI utilities

```bash
uv run python main.py schedule-list   # In ra mọi task + cron expression
uv run python main.py schedule-run    # Dispatch task đến hạn ngay (dùng cho test manual)
```

### 6.4.6 Lưu ý setup

- Mac đăng nhập sẵn; cron chạy ngay cả khi screen lock.
- Nếu Mac suspend sâu → tick miss. Lựa chọn:
  - Energy Saver → "Prevent automatic sleeping..."
  - `sudo pmset -a sleep 0` (chỉ dùng nếu cắm sạc)
- Cron escape `%`: không còn vấn đề vì args nằm trong `config/schedule.py` (Python, không phải shell).

## 6.5 Logging

[finance_bot/logger.py](../../src/finance_bot/logger.py) dùng loguru:

```python
from finance_bot.logger import logger
logger.info("...")
```

Output: stderr, format `<time> <level> <message>`. Cron wrapper redirect stdout+stderr vào `logs/cron.log` (append).

Log level từ `.env` `LOG_LEVEL` (default INFO). DEBUG nếu cần trace SQL/HTTP.

## 6.6 Settings (single source of truth)

[src/finance_bot/settings.py](../../src/finance_bot/settings.py):

### `Settings` (pydantic-settings, đọc `.env`)

| Key | Default | Mô tả |
|---|---|---|
| `MYSQL_HOST` | `127.0.0.1` | |
| `MYSQL_PORT` | `3306` | |
| `MYSQL_USER` | `root` | |
| `MYSQL_PASSWORD` | (required) | |
| `MYSQL_DATABASE` | `finance_bot` | |
| `CLAUDE_BINARY` | `claude` | Path to the `claude` CLI; resolved via `$PATH`. |
| `CLAUDE_MODEL` | `claude-opus-4-7` | Model passed to `claude --model`. |
| `CLAUDE_TIMEOUT_SECONDS` | `120` | Subprocess hard-timeout for each arbiter call. |
| `EMBEDDING_MODEL` | `paraphrase-multilingual-MiniLM-L12-v2` | |
| `CHROMA_DIR` | `.chroma` | |
| `TELEGRAM_BOT_TOKEN` | (required cho alert) | |
| `TELEGRAM_CHAT_ID` | (required cho alert) | |
| `LOG_LEVEL` | `INFO` | |

### `Watchlist` (YAML — `config/watchlist.yaml`)

- `assets` list: symbol, name, asset_class, source, timeframes, context_only
- `news_sources` list: name, url, lang, tags
- `signal`: tier_a/tier_b thresholds, cooldown_hours_per_ticker, default_horizon_days
- `risk`: atr_period, stop_loss_atr_mult, take_profit_rr
- `schedule`: timezone, vn_eod_close_local, global_eod_local, signal_run_local

Cache: `@lru_cache get_settings()`, `get_watchlist()`. Test phải reset cache nếu mock.

## 6.7 Edge cases

- **`.env` thiếu password**: pydantic raise tại `get_settings()` lần đầu — bot fail-fast.
- **MySQL down**: SQLAlchemy raise OperationalError khi `get_session()` — log + exit non-zero. Cron sẽ thử lại lần kế.
- **Claude CLI chưa cài / chưa login**: `llm-health` báo FAILED. `arbitrate()` sẽ fallback giữ rule-engine draft và ghi reason vào `llm_reasoning`.
- **ChromaDB lock**: hiếm; nếu xảy ra, restart bot hoặc xoá `.chroma/` rồi `sync-knowledge` rebuild.
- **`logs/cron.log` lớn dần**: chưa có rotation. TODO: dùng `logrotate` hoặc rotate trong `run-cron.sh`.
