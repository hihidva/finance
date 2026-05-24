"""Unit tests cho settings.py — TC-SYS-01..04, TC-SYS-13."""
from __future__ import annotations

from finance_bot.settings import get_settings, get_watchlist


# ----------------------------------------------------------------------
# Settings (.env)
# ----------------------------------------------------------------------
def test_settings_load_with_env_vars(monkeypatch):
    """TC-SYS-01: env vars override defaults."""
    monkeypatch.setenv("MYSQL_HOST", "db.example.com")
    monkeypatch.setenv("MYSQL_PORT", "3307")
    monkeypatch.setenv("MYSQL_USER", "fbot")
    monkeypatch.setenv("MYSQL_PASSWORD", "secret123")
    monkeypatch.setenv("MYSQL_DATABASE", "myfinance")
    monkeypatch.setenv("CLAUDE_BINARY", "/opt/homebrew/bin/claude")
    monkeypatch.setenv("CLAUDE_MODEL", "claude-sonnet-4-6")
    monkeypatch.setenv("CLAUDE_TIMEOUT_SECONDS", "60")
    get_settings.cache_clear()

    s = get_settings()
    assert s.mysql_host == "db.example.com"
    assert s.mysql_port == 3307
    assert s.mysql_user == "fbot"
    assert s.mysql_password == "secret123"
    assert s.mysql_database == "myfinance"
    assert s.claude_binary == "/opt/homebrew/bin/claude"
    assert s.claude_model == "claude-sonnet-4-6"
    assert s.claude_timeout_seconds == 60


def test_settings_mysql_url_built_correctly(monkeypatch):
    """mysql_url property builds DSN with charset=utf8mb4."""
    monkeypatch.setenv("MYSQL_HOST", "localhost")
    monkeypatch.setenv("MYSQL_PORT", "3306")
    monkeypatch.setenv("MYSQL_USER", "root")
    monkeypatch.setenv("MYSQL_PASSWORD", "pw")
    monkeypatch.setenv("MYSQL_DATABASE", "db")
    get_settings.cache_clear()

    s = get_settings()
    assert s.mysql_url == (
        "mysql+pymysql://root:pw@localhost:3306/db?charset=utf8mb4"
    )


def test_settings_defaults_applied_when_env_missing(monkeypatch):
    """TC-SYS-02: pydantic-settings doesn't raise when password missing — falls back to default ''."""
    for k in (
        "MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE",
        "CLAUDE_BINARY", "CLAUDE_MODEL", "CLAUDE_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(k, raising=False)
    get_settings.cache_clear()

    s = get_settings()
    # Defaults are sane (note: project chose empty-string defaults rather than required).
    assert s.mysql_host == "127.0.0.1"
    assert s.mysql_port == 3306
    assert s.mysql_password == ""
    assert s.claude_binary == "claude"
    assert s.claude_model == "claude-opus-4-7"
    assert s.claude_timeout_seconds == 120


# ----------------------------------------------------------------------
# Watchlist (config/watchlist.yaml)
# ----------------------------------------------------------------------
def test_watchlist_loaded_from_yaml():
    """TC-SYS-03: load watchlist.yaml in repo, basic shape."""
    wl = get_watchlist()
    assert len(wl.assets) > 0
    # signal/risk/schedule có default sensible
    assert 0 < wl.signal.tier_a.min_agree_ratio <= 1
    assert wl.signal.tier_a.min_agree_ratio > wl.signal.tier_b.min_agree_ratio
    assert wl.signal.tier_a.min_confidence > 0
    assert wl.risk.atr_period >= 1
    assert wl.risk.take_profit_rr > 0
    assert wl.schedule.timezone == "Asia/Ho_Chi_Minh"


def test_watchlist_primary_vs_context_partition():
    """TC-SYS-04: primary_assets and context_assets are disjoint and exhaustive."""
    wl = get_watchlist()
    primary = {a.symbol for a in wl.primary_assets}
    context = {a.symbol for a in wl.context_assets}
    assert primary.isdisjoint(context), "asset cannot be both primary and context"
    assert primary | context == {a.symbol for a in wl.assets}
    # All context assets should have context_only=True; all primary False.
    assert all(not a.context_only for a in wl.primary_assets)
    assert all(a.context_only for a in wl.context_assets)


def test_lru_cache_returns_same_instance_until_cleared():
    """TC-SYS-13: get_settings cached, returns same object on repeat calls."""
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2

    get_settings.cache_clear()
    s3 = get_settings()
    assert s3 is not s1
