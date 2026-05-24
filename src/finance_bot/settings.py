"""Centralized settings loaded from .env (+ optional .env.${APP_ENV}) + watchlist YAML.

Multi-environment loading:
    pydantic-settings reads `env_file` as a list, with later files overriding earlier ones.
    We always load `.env` (base), then layer `.env.${APP_ENV}` on top when APP_ENV is set.
    Pattern mirrors Next.js / Rails / dotenv conventions.

    Examples:
        $ uv run python main.py show-config            → .env only
        $ APP_ENV=test uv run python main.py db-init   → .env + .env.test
        $ ./run.sh start_test                          → APP_ENV=test → SQLite test sandbox

    `APP_ENV` is read at import time, so external callers (CLI / uvicorn / run.sh)
    must export it before the first `from finance_bot...` import.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"

APP_ENV = os.environ.get("APP_ENV", "").strip().lower()


def _env_files() -> tuple[str, ...]:
    """Return ordered env-file paths. Later files override earlier ones.

    Missing files are silently skipped by pydantic-settings — safe to list
    `.env.test` even when the user hasn't created it yet.
    """
    files: list[str] = [str(PROJECT_ROOT / ".env")]
    if APP_ENV:
        files.append(str(PROJECT_ROOT / f".env.{APP_ENV}"))
    return tuple(files)


class Settings(BaseSettings):
    """Environment-backed settings."""

    model_config = SettingsConfigDict(
        env_file=_env_files(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    # Generic SQLAlchemy URL override — when set, beats the MYSQL_* fields.
    # Use this for SQLite test sandbox (e.g. sqlite:///./.cache/finance_test.db).
    database_url: str | None = None

    # MySQL (production / normal-mode default)
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "finance_bot"
    mysql_password: str = ""
    mysql_database: str = "finance_bot"

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Claude Code CLI (LLM arbiter — used to confirm / down-tier rule-engine drafts)
    claude_binary: str = "claude"
    claude_model: str = "claude-opus-4-7"
    claude_timeout_seconds: int = 120

    # RAG
    embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
    chroma_dir: str = "./.chroma"

    log_level: str = "INFO"

    @property
    def app_env(self) -> str:
        """Resolved APP_ENV value (lower-cased, empty string when unset)."""
        return APP_ENV

    @property
    def db_url(self) -> str:
        """Effective SQLAlchemy URL — `database_url` if set, otherwise built from MYSQL_*."""
        if self.database_url:
            return self.database_url
        return self.mysql_url

    @property
    def mysql_url(self) -> str:
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}?charset=utf8mb4"
        )


# ----------------------------------------------------------------------
# Watchlist (config/watchlist.yaml)
# ----------------------------------------------------------------------
AssetClass = Literal["vn_stock", "crypto", "commodity", "fx_index"]
SourceName = Literal["vnstock", "ccxt", "yfinance"]


class AssetConfig(BaseModel):
    symbol: str
    name: str
    asset_class: AssetClass
    source: SourceName
    exchange: str | None = None
    timeframes: list[str] = Field(default_factory=lambda: ["1d"])
    context_only: bool = False  # True → fetched for macro context, never sourced as primary signal


class NewsSourceConfig(BaseModel):
    name: str
    url: str
    lang: str = "vi"
    tags: list[str] = Field(default_factory=list)


class TierAConfig(BaseModel):
    min_agree_ratio: float = 0.60
    min_confidence: float = 0.75
    require_news_not_against: bool = True


class TierBConfig(BaseModel):
    min_agree_ratio: float = 0.45
    min_confidence: float = 0.60


class SignalConfig(BaseModel):
    tier_a: TierAConfig = Field(default_factory=TierAConfig)
    tier_b: TierBConfig = Field(default_factory=TierBConfig)
    cooldown_hours_per_ticker: int = 24
    default_horizon_days: int = 30


class RiskConfig(BaseModel):
    atr_period: int = 14
    stop_loss_atr_mult: float = 2.0
    take_profit_rr: float = 2.5
    use_recent_swing_for_sr: bool = True


class ScheduleConfig(BaseModel):
    vn_eod_close_local: str = "15:15"
    global_eod_local: str = "06:00"
    signal_run_local: str = "16:00"
    timezone: str = "Asia/Ho_Chi_Minh"


class Watchlist(BaseModel):
    assets: list[AssetConfig]
    news_sources: list[NewsSourceConfig]
    signal: SignalConfig = Field(default_factory=SignalConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)

    @property
    def primary_assets(self) -> list[AssetConfig]:
        """Assets that can produce their own buy/sell signals."""
        return [a for a in self.assets if not a.context_only]

    @property
    def context_assets(self) -> list[AssetConfig]:
        """Macro context assets (DXY, WTI…) — fed into LLM prompt only."""
        return [a for a in self.assets if a.context_only]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def _load_watchlist_from_yaml() -> Watchlist:
    path = CONFIG_DIR / "watchlist.yaml"
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return Watchlist.model_validate(raw)


def _try_load_assets_from_db() -> list[AssetConfig] | None:
    """Read active watchlist_entries from DB. Return None if DB unreachable or empty.

    Imported lazily so this module stays importable without a live MySQL connection
    (used by tests, show-config in CI, etc.).
    """
    try:
        from finance_bot.db.repositories import list_watchlist_entries
        from finance_bot.db.session import get_session
    except Exception:  # pragma: no cover — circular/import error in degraded envs
        return None

    try:
        with get_session() as session:
            rows = list_watchlist_entries(session, only_active=True)
    except Exception:
        return None

    if not rows:
        return None

    return [
        AssetConfig(
            symbol=r.symbol,
            name=r.name,
            asset_class=r.asset_class,
            source=r.source,
            exchange=r.exchange,
            timeframes=list(r.timeframes) if r.timeframes else ["1d"],
            context_only=r.context_only,
        )
        for r in rows
    ]


@lru_cache(maxsize=1)
def get_watchlist() -> Watchlist:
    """Watchlist source-of-truth resolution:

    1. DB table `watchlist_entries` if it has ≥1 active row → use DB rows for `assets`,
       keep YAML for the non-asset blocks (news_sources / signal / risk / schedule).
    2. Otherwise fall back to YAML for everything (initial boot, before seed-watchlist).
    """
    yaml_wl = _load_watchlist_from_yaml()
    db_assets = _try_load_assets_from_db()
    if db_assets is not None:
        return Watchlist(
            assets=db_assets,
            news_sources=yaml_wl.news_sources,
            signal=yaml_wl.signal,
            risk=yaml_wl.risk,
            schedule=yaml_wl.schedule,
        )
    return yaml_wl


def reload_watchlist_cache() -> None:
    """Drop the lru_cache so the next get_watchlist() re-reads DB+YAML.

    Call this from the web layer after a watchlist mutation, or whenever you've
    just run `seed-watchlist`.
    """
    get_watchlist.cache_clear()
