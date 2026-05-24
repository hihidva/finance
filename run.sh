#!/usr/bin/env bash
# finance-bot web dashboard launcher.
#
# Usage:
#   ./run.sh start         normal — .env, API :4030, frontend :4031, MySQL finance_bot
#   ./run.sh start_test    sandbox — .env + .env.test, API :5030, frontend :5031, SQLite
#
# How env files resolve (pydantic-settings + Next.js both read APP_ENV):
#   normal      → APP_ENV unset → .env only
#   start_test  → APP_ENV=test  → .env (base) + .env.test (override)
#
# Both modes start FastAPI backend + Next.js dev server in parallel and forward
# their logs to stdout. Ctrl-C kills both.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

usage() {
  cat <<EOF
Usage: $0 {start|start_test|migrate}

  start         normal — .env, API :4030, frontend :4031, MySQL finance_bot
  start_test    sandbox — .env + .env.test, API :5030, frontend :5031, SQLite
  migrate       run db-init + db-migrate for BOTH local (MySQL) and test (SQLite)

First-time setup (normal mode):
  uv sync
  ./run.sh migrate                          # creates tables + applies SQL migrations
  uv run python main.py seed-watchlist
  cd web && (pnpm install || npm install) && cp .env.example .env.local && cd ..

First-time setup (test mode — SQLite is auto-created on db-init):
  ./run.sh migrate                          # also covers test mode
  APP_ENV=test uv run python main.py seed-watchlist

Limitation of test mode: SQLite doesn't support \`ON DUPLICATE KEY UPDATE\`,
so batch jobs (sync-prices / sync-news / eval-outcomes) WILL FAIL.
Test mode is for web-layer + watchlist CRUD only.
EOF
}

mode="${1:-}"
case "$mode" in
  start)
    export API_PORT="${API_PORT:-4030}"
    export FRONTEND_PORT="${FRONTEND_PORT:-4031}"
    # APP_ENV not set → settings.py loads .env only
    ;;
  start_test)
    export APP_ENV=test                      # → settings.py also loads .env.test
    export API_PORT="${API_PORT:-5030}"
    export FRONTEND_PORT="${FRONTEND_PORT:-5031}"
    if [ ! -f .env.test ]; then
      echo "ERROR: .env.test missing. Create it (see README.md → Web Dashboard)." >&2
      exit 1
    fi
    ;;
  migrate)
    # Sync schema for both envs. Each subcommand is idempotent:
    #   db-init      → CREATE TABLE IF NOT EXISTS for every model in models.py
    #   db-migrate   → ALTER pending SQL files in migrations/ (MySQL only;
    #                  auto-skips on SQLite where db-init already covers schema)
    echo "==> Migrating LOCAL (MySQL — .env)"
    uv run python main.py db-init
    uv run python main.py db-migrate
    echo
    if [ -f .env.test ]; then
      echo "==> Migrating TEST (SQLite — .env + .env.test)"
      APP_ENV=test uv run python main.py db-init
      APP_ENV=test uv run python main.py db-migrate
    else
      echo "==> SKIP test migration: .env.test missing"
      echo "    (create .env.test to enable test mode)"
    fi
    echo
    echo "==> Done."
    exit 0
    ;;
  -h|--help|help|"")
    usage
    exit 0
    ;;
  *)
    echo "ERROR: unknown command '$mode'" >&2
    usage
    exit 64
    ;;
esac

# Front-end consumes this through Next.js rewrites; export so `pnpm dev` sees it.
export NEXT_PUBLIC_API_BASE="http://127.0.0.1:${API_PORT}"

# Pre-flight: don't launch if a process already owns either port.
for port in "$API_PORT" "$FRONTEND_PORT"; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "ERROR: port $port is already in use. Stop the other process first:" >&2
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >&2
    exit 1
  fi
done

# Resolve a human-readable DB label without sourcing .env files.
db_label="(MySQL — from .env)"
if [ "$mode" = "start_test" ]; then
  db_label="SQLite — $(grep -E '^DATABASE_URL=' .env.test | head -1 | cut -d= -f2-)"
fi

echo "==> Mode: $mode"
echo "    APP_ENV:  ${APP_ENV:-<unset>}"
echo "    API:      http://127.0.0.1:${API_PORT}"
echo "    Frontend: http://127.0.0.1:${FRONTEND_PORT}"
echo "    DB:       ${db_label}"
echo

pids=()

cleanup() {
  echo
  echo "==> Stopping (pids: ${pids[*]:-})"
  for pid in "${pids[@]:-}"; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# --- Backend ---------------------------------------------------------------
echo "==> Launching FastAPI backend on :${API_PORT}"
uv run uvicorn finance_bot.web.main:app \
  --host 127.0.0.1 --port "$API_PORT" --reload &
pids+=($!)

# --- Frontend --------------------------------------------------------------
# Launch next directly via node_modules/.bin/next so the script doesn't depend
# on a specific package manager (pnpm/npm/yarn) being on PATH. FRONTEND_PORT
# is already exported above; pass -p explicitly too for clarity.
echo "==> Launching Next.js frontend on :${FRONTEND_PORT}"
NEXT_BIN="web/node_modules/.bin/next"
if [ ! -x "$NEXT_BIN" ]; then
  echo "ERROR: $NEXT_BIN not found. Install web deps first:" >&2
  echo "  cd web && (pnpm install || npm install || yarn install)" >&2
  exit 1
fi
(cd web && "./node_modules/.bin/next" dev -p "$FRONTEND_PORT") &
pids+=($!)

wait
