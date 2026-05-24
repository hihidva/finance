#!/usr/bin/env bash
# Wrapper script chạy 1 finance-bot CLI subcommand từ cron.
# - cd vào project root để uv tìm được .venv + pyproject.toml
# - load .env vào environment
# - dùng `caffeinate -i` để giữ Mac không sleep trong lúc chạy command
# - log stdout+stderr vào logs/cron.log với timestamp
#
# Usage:
#   bin/run-cron.sh sync-prices
#   bin/run-cron.sh run-signals
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

# Load .env nếu tồn tại (export tất cả non-comment lines)
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

mkdir -p logs

# Khi cron khởi động, PATH thường chỉ có /usr/bin:/bin → bổ sung Homebrew + uv
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

ts() { date "+%Y-%m-%d %H:%M:%S"; }

CMD="$*"

# `schedule-run` ticks every minute — far too noisy to log every empty tick,
# and we deliberately skip `caffeinate` for it (see feedback Q6). The Python
# scheduler logs its own non-empty ticks into the same file.
if [[ "$1" == "schedule-run" ]]; then
  uv run python main.py "$@" >>logs/cron.log 2>&1
  exit $?
fi

echo "[$(ts)] >>> $CMD" >>logs/cron.log

# caffeinate -i: ngăn idle sleep nhưng cho phép display sleep (đỡ tốn pin)
if command -v caffeinate >/dev/null 2>&1; then
  caffeinate -i uv run python main.py "$@" >>logs/cron.log 2>&1
else
  uv run python main.py "$@" >>logs/cron.log 2>&1
fi

echo "[$(ts)] <<< $CMD (exit=$?)" >>logs/cron.log
