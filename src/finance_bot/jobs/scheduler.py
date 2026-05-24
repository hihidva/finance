"""Laravel-style scheduler — single-cron-entry batch dispatcher.

The whole point: instead of N crontab lines (one per task), the user installs
ONE line — `* * * * * .../bin/run-cron.sh schedule-run` — which ticks every
minute. `run_due_tasks()` then loads `config/schedule.py`, evaluates each
task's cron expression against the current local time, and shells out to
`uv run python main.py <command> <args>` for whichever tasks are due.

Design notes:
  - No concurrency lock (per Q3): two ticks overlapping is acceptable; cron
    spacing in config/schedule.py keeps tasks far enough apart in practice.
  - No `caffeinate` for the tick itself (per Q6): we accept that a sleeping
    Mac misses ticks. Per-task `caffeinate` still applies via run-cron.sh
    when the user wires individual tasks differently.
  - Logging follows Q7: silent on empty ticks, one line per dispatch, one
    line per finish (with exit code).
"""
from __future__ import annotations

import importlib.util
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from croniter import croniter

from finance_bot.logger import logger

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCHEDULE_PATH = PROJECT_ROOT / "config" / "schedule.py"

_DAY_MAP = {
    "monday": 1, "mon": 1,
    "tuesday": 2, "tue": 2,
    "wednesday": 3, "wed": 3,
    "thursday": 4, "thu": 4,
    "friday": 5, "fri": 5,
    "saturday": 6, "sat": 6,
    "sunday": 0, "sun": 0,
}


def _hhmm_parts(hhmm: str) -> tuple[int, int]:
    h, m = hhmm.split(":")
    return int(h), int(m)


@dataclass
class ScheduledTask:
    """One batch entry. Built by Schedule.command(...) and configured fluently."""

    command: str
    args: list[str] = field(default_factory=list)
    cron_expr: str = ""

    # ---- Fluent configuration --------------------------------------
    def cron(self, expr: str) -> ScheduledTask:
        """Set a raw 5-field cron expression (minute hour dom month dow)."""
        self.cron_expr = expr
        return self

    def daily_at(self, hhmm: str) -> ScheduledTask:
        h, m = _hhmm_parts(hhmm)
        return self.cron(f"{m} {h} * * *")

    def weekdays_at(self, hhmm: str) -> ScheduledTask:
        """Run Mon–Fri at the given HH:MM."""
        h, m = _hhmm_parts(hhmm)
        return self.cron(f"{m} {h} * * 1-5")

    def weekly_on(self, day: str, hhmm: str) -> ScheduledTask:
        d = _DAY_MAP[day.lower()]
        h, m = _hhmm_parts(hhmm)
        return self.cron(f"{m} {h} * * {d}")

    # ---- Matching --------------------------------------------------
    def is_due(self, now: datetime) -> bool:
        """True if `now` matches this task's cron expression (minute-precision)."""
        if not self.cron_expr:
            return False
        # Match minute-by-minute: a tick is due when the prior minute's "next
        # fire time" is the current minute.
        try:
            return croniter.match(self.cron_expr, now)
        except Exception as exc:
            logger.warning("scheduler: bad cron expr {!r} for {}: {}",
                           self.cron_expr, self.command, exc)
            return False

    def describe(self) -> str:
        args = " ".join(self.args) if self.args else ""
        suffix = f" {args}" if args else ""
        return f"{self.command}{suffix}  [{self.cron_expr}]"


class Schedule:
    """Collection of ScheduledTask. `config/schedule.py` builds one of these."""

    def __init__(self) -> None:
        self.tasks: list[ScheduledTask] = []

    def command(self, name: str, args: list[str] | None = None) -> ScheduledTask:
        task = ScheduledTask(command=name, args=list(args) if args else [])
        self.tasks.append(task)
        return task

    def due_tasks(self, now: datetime) -> list[ScheduledTask]:
        return [t for t in self.tasks if t.is_due(now)]


# ----------------------------------------------------------------------
# Loader + dispatcher
# ----------------------------------------------------------------------
def load_schedule() -> Schedule:
    """Import `config/schedule.py` and return the declared `schedule`.

    Loaded by file path (not by package import) so the project root doesn't
    need a `config/__init__.py`.
    """
    spec = importlib.util.spec_from_file_location("config_schedule", SCHEDULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"config/schedule.py not found at {SCHEDULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "schedule"):
        raise RuntimeError(
            "config/schedule.py must declare a top-level `schedule = Schedule()`"
        )
    sched = module.schedule
    if not isinstance(sched, Schedule):
        raise RuntimeError("`schedule` in config/schedule.py is not a Schedule instance")
    return sched


def _dispatch(task: ScheduledTask) -> int:
    """Shell out to `uv run python main.py <command> <args>` for one task.

    Returns the subprocess exit code (0 = success).
    """
    cmd = [
        "uv", "run", "python", "main.py", task.command, *task.args,
    ]
    logger.info("scheduler: dispatching {}", task.describe())
    try:
        result = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            timeout=3600,  # 1h cap per task; longer jobs are a bug
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.error("scheduler: {} timed out after 1h", task.command)
        return 124
    except Exception as exc:
        logger.exception("scheduler: {} crashed: {}", task.command, exc)
        return 1

    if result.returncode != 0:
        logger.error("scheduler: {} exited {}", task.command, result.returncode)
    else:
        logger.info("scheduler: {} done", task.command)
    return result.returncode


def run_due_tasks(now: datetime | None = None) -> int:
    """Tick once: dispatch every task due at `now`. Returns count dispatched.

    `now` defaults to local time so users can reason about the cron
    expressions the same way as crontab itself.
    """
    when = now or datetime.now()
    schedule = load_schedule()
    due = schedule.due_tasks(when)
    if not due:
        # Silent on empty ticks (Q7). Use DEBUG so logs/cron.log isn't spammed
        # with 1440 lines/day; flip to INFO temporarily when debugging.
        logger.debug("scheduler tick {}: no tasks due", when.strftime("%Y-%m-%d %H:%M"))
        return 0

    logger.info("scheduler tick {}: {} task(s) due",
                when.strftime("%Y-%m-%d %H:%M"), len(due))
    for task in due:
        _dispatch(task)
    return len(due)


def list_schedule() -> list[ScheduledTask]:
    """Return all tasks in declaration order — used by `schedule-list` CLI."""
    return list(load_schedule().tasks)
