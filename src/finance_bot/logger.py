"""Loguru-based logger setup."""
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

from finance_bot.settings import PROJECT_ROOT, get_settings


def setup_logger() -> None:
    settings = get_settings()
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>",
    )
    logs_dir: Path = PROJECT_ROOT / "logs"
    logs_dir.mkdir(exist_ok=True)
    logger.add(
        logs_dir / "finance_bot.log",
        level=settings.log_level,
        rotation="10 MB",
        retention="14 days",
        encoding="utf-8",
    )


__all__ = ["logger", "setup_logger"]
