"""finance-bot batch schedule — Laravel-style declarative config.

The cron entry `* * * * * .../bin/run-cron.sh schedule-run` ticks every
minute. At each tick the scheduler imports this file and dispatches every
task whose cron expression matches the current local time
(`Asia/Ho_Chi_Minh`, since the Mac local time is set to that).

Edit THIS FILE to add / remove batches. The crontab line never changes.

Helpers:
    .cron("m h dom mon dow")  — raw 5-field cron expression
    .daily_at("06:00")        — every day at 06:00
    .weekdays_at("16:00")     — Mon–Fri at 16:00
    .weekly_on("monday", "08:00") — every Monday at 08:00
"""
from __future__ import annotations

from finance_bot.jobs.scheduler import Schedule

schedule = Schedule()

# ----------------------------------------------------------------------
# Morning routine (06:00 — pre-open data + learning loop)
# Order matters: data first, then RAG re-embed last so it sees today's news.
# ----------------------------------------------------------------------
schedule.command("sync-prices").cron("0 6 * * 1-7")
schedule.command("sync-news").cron("2 6 * * 1-7")
schedule.command("eval-outcomes").cron("5 6 * * 1-7")
schedule.command("sync-knowledge").cron("6 6 * * 1-7")

# ----------------------------------------------------------------------
# VN EOD (15:15 — after HOSE/HNX close 14:45)
# ----------------------------------------------------------------------
schedule.command("sync-prices").cron("15 15 * * 1-5")
schedule.command("sync-news").cron("17 15 * * 1-5")

# ----------------------------------------------------------------------
# Signal engine (16:00 — after VN data settled)
# Note: run-signals pulls Telegram callback feedback at the start of its
# pipeline, so no separate process-feedback entry is needed.
# ----------------------------------------------------------------------
schedule.command("run-signals").weekdays_at("16:00")

# ----------------------------------------------------------------------
# Fundamentals (Phase 2 — vn_stock only)
# Daily after-hours refresh; weekly industry-wide aggregate on Monday.
# ----------------------------------------------------------------------
schedule.command("sync-fundamentals").cron("30 17 * * 1-5")
schedule.command("sync-industry-averages").weekly_on("monday", "08:00")

# ----------------------------------------------------------------------
# Weekly backtest sanity (Sunday 09:00)
# args computed at dispatch time? Keep static for now — extend Scheduler
# if/when truly dynamic args become necessary.
# ----------------------------------------------------------------------
schedule.command(
    "backtest",
    args=["--start", "2025-01-01", "--end", "2026-05-31"],
).weekly_on("sunday", "09:00")
