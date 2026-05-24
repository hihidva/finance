"""Backtest engine — chạy lại rule-engine trên window lịch sử để đo win-rate.

Strategy:
  - Iterate ngày-by-ngày trong window [start, end] (chỉ ngày có nến daily).
  - Tại mỗi ngày T: dùng dữ liệu OHLCV ≤ T (no future leak), compute snapshot,
    apply Tier rule (giữ nguyên cấu hình production).
  - Skip LLM (quá chậm cho hàng nghìn iteration; giả định LLM thường confirm).
  - Tính outcome cho từng (signal, horizon=24/72/168/720h) bằng dữ liệu ngày T+...
    có sẵn trong DB.
  - Aggregate: win-rate, avg P&L, max drawdown, expectancy.

Kết quả KHÔNG được ghi vào bảng `signals` (tránh bẩn data thật) — chỉ in stdout
hoặc CSV qua --output.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from finance_bot.analysis.signal import analyze
from finance_bot.db.queries import load_ohlcv_df
from finance_bot.db.repositories import upsert_asset
from finance_bot.db.session import get_session
from finance_bot.logger import logger
from finance_bot.settings import AssetConfig, Watchlist, get_watchlist


HORIZONS = (24, 72, 168, 720)  # 1d, 3d, 7d, 30d


@dataclass
class BacktestRow:
    asset: str
    asset_class: str
    signal_ts: datetime
    side: str
    tier: str
    confidence: float
    entry_price: float
    pnl_by_horizon: dict[int, float] = field(default_factory=dict)


@dataclass
class TickerStats:
    asset: str
    n_tier_a: int = 0
    n_tier_a_buy: int = 0
    n_tier_a_sell: int = 0
    win_rate_by_horizon: dict[int, float] = field(default_factory=dict)
    avg_pnl_by_horizon: dict[int, float] = field(default_factory=dict)
    max_drawdown: float = 0.0     # worst single-trade loss across horizons
    expectancy_7d: float = 0.0    # avg PnL after 7d


# ----------------------------------------------------------------------
# Core backtest loop
# ----------------------------------------------------------------------
def _entry_price(asset_class: str, df: pd.DataFrame, t_idx: int) -> float | None:
    """Entry price reproducible với production:
       - VN ATO next session: open của nến T+1 (nếu có)
       - khác: close của nến T
    """
    if asset_class == "vn_stock":
        if t_idx + 1 >= len(df):
            return None
        return float(df["open"].iloc[t_idx + 1])
    return float(df["close"].iloc[t_idx])


def _pnl_at_horizon(
    asset_class: str, df: pd.DataFrame, t_idx: int, side: str,
    horizon_hours: int, entry: float,
) -> float | None:
    target_ts = df.index[t_idx] + timedelta(hours=horizon_hours)
    future = df.iloc[t_idx + 1:]
    if future.empty or future.index[-1] < target_ts:
        return None
    candidates = future[future.index >= target_ts]
    if candidates.empty:
        return None
    price_then = float(candidates["close"].iloc[0])
    if entry == 0:
        return None
    raw = (price_then - entry) / entry * 100.0
    return raw if side == "buy" else -raw


def _backtest_one(asset_cfg: AssetConfig, wl: Watchlist,
                  df: pd.DataFrame, start: date, end: date,
                  warmup_bars: int = 60) -> list[BacktestRow]:
    """Run rule-engine on rolling window for one asset."""
    rows: list[BacktestRow] = []
    if len(df) <= warmup_bars:
        return rows

    df_index_dates = pd.to_datetime(df.index).date

    for i in range(warmup_bars, len(df) - 1):  # leave last bar for outcome lookup
        bar_date = df_index_dates[i]
        if bar_date < start or bar_date > end:
            continue

        window = df.iloc[: i + 1]
        try:
            decision = analyze(asset_cfg, window, wl,
                               news_against=False,  # không có news lịch sử ở backtest
                               now_utc=df.index[i].to_pydatetime())
        except ValueError:
            continue

        if decision.tier != "A" or decision.side == "hold":
            continue

        entry = _entry_price(asset_cfg.asset_class, df, i)
        if entry is None:
            continue

        pnl_map: dict[int, float] = {}
        for h in HORIZONS:
            pnl = _pnl_at_horizon(asset_cfg.asset_class, df, i, decision.side, h, entry)
            if pnl is not None:
                pnl_map[h] = pnl

        rows.append(
            BacktestRow(
                asset=asset_cfg.symbol,
                asset_class=asset_cfg.asset_class,
                signal_ts=df.index[i].to_pydatetime(),
                side=decision.side,
                tier=decision.tier,
                confidence=decision.confidence,
                entry_price=entry,
                pnl_by_horizon=pnl_map,
            )
        )
    return rows


# ----------------------------------------------------------------------
# Aggregation
# ----------------------------------------------------------------------
def _aggregate(rows: list[BacktestRow]) -> TickerStats:
    if not rows:
        return TickerStats(asset="?", n_tier_a=0)

    stats = TickerStats(asset=rows[0].asset)
    stats.n_tier_a = len(rows)
    stats.n_tier_a_buy = sum(1 for r in rows if r.side == "buy")
    stats.n_tier_a_sell = sum(1 for r in rows if r.side == "sell")

    for h in HORIZONS:
        pnl_list = [r.pnl_by_horizon[h] for r in rows if h in r.pnl_by_horizon]
        if pnl_list:
            stats.win_rate_by_horizon[h] = sum(1 for p in pnl_list if p > 0) / len(pnl_list)
            stats.avg_pnl_by_horizon[h] = sum(pnl_list) / len(pnl_list)
        else:
            stats.win_rate_by_horizon[h] = 0.0
            stats.avg_pnl_by_horizon[h] = 0.0

    all_pnl = [p for r in rows for p in r.pnl_by_horizon.values()]
    stats.max_drawdown = min(all_pnl) if all_pnl else 0.0
    stats.expectancy_7d = stats.avg_pnl_by_horizon.get(168, 0.0)
    return stats


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------
def run_backtest(
    start: date,
    end: date,
    symbols: list[str] | None = None,
    output_csv: Path | None = None,
) -> list[TickerStats]:
    wl = get_watchlist()
    targets = wl.primary_assets
    if symbols:
        targets = [a for a in targets if a.symbol in set(symbols)]

    all_rows: list[BacktestRow] = []
    all_stats: list[TickerStats] = []

    logger.info("backtest: window {} → {}, {} assets",
                start, end, len(targets))

    for asset_cfg in targets:
        with get_session() as session:
            asset = upsert_asset(session, asset_cfg)
            df = load_ohlcv_df(session, asset.id, "1d", limit=5000)
        if df.empty:
            logger.warning("backtest: {} không có data, skip", asset_cfg.symbol)
            continue

        rows = _backtest_one(asset_cfg, wl, df, start, end)
        stats = _aggregate(rows) if rows else TickerStats(asset=asset_cfg.symbol)
        stats.asset = asset_cfg.symbol
        all_rows.extend(rows)
        all_stats.append(stats)
        logger.info(
            "  {:>10}  tier_a={:<3}  win7d={:.1%}  avg7d={:+.2f}%  exp7d={:+.2f}%",
            asset_cfg.symbol, stats.n_tier_a,
            stats.win_rate_by_horizon.get(168, 0.0),
            stats.avg_pnl_by_horizon.get(168, 0.0),
            stats.expectancy_7d,
        )

    if output_csv is not None and all_rows:
        with output_csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["asset", "asset_class", "signal_ts", "side", "tier",
                        "confidence", "entry_price",
                        "pnl_24h", "pnl_72h", "pnl_168h", "pnl_720h"])
            for r in all_rows:
                w.writerow([
                    r.asset, r.asset_class, r.signal_ts.isoformat(),
                    r.side, r.tier, f"{r.confidence:.3f}", f"{r.entry_price:.4f}",
                    f"{r.pnl_by_horizon.get(24, ''):.4f}" if 24 in r.pnl_by_horizon else "",
                    f"{r.pnl_by_horizon.get(72, ''):.4f}" if 72 in r.pnl_by_horizon else "",
                    f"{r.pnl_by_horizon.get(168, ''):.4f}" if 168 in r.pnl_by_horizon else "",
                    f"{r.pnl_by_horizon.get(720, ''):.4f}" if 720 in r.pnl_by_horizon else "",
                ])
        logger.info("backtest CSV → {}", output_csv)

    return all_stats


def print_summary(stats_list: list[TickerStats]) -> None:
    print("\n=== Backtest summary ===")
    print(f"{'Ticker':<10} {'#A':>4} {'B/S':>6}  "
          f"{'win1d':>7} {'win3d':>7} {'win7d':>7} {'win30d':>7}  "
          f"{'avg7d':>8} {'exp7d':>8} {'maxDD':>8}")
    print("-" * 100)
    for s in stats_list:
        b_s = f"{s.n_tier_a_buy}/{s.n_tier_a_sell}"
        print(
            f"{s.asset:<10} {s.n_tier_a:>4} {b_s:>6}  "
            f"{s.win_rate_by_horizon.get(24, 0):>7.1%} "
            f"{s.win_rate_by_horizon.get(72, 0):>7.1%} "
            f"{s.win_rate_by_horizon.get(168, 0):>7.1%} "
            f"{s.win_rate_by_horizon.get(720, 0):>7.1%}  "
            f"{s.avg_pnl_by_horizon.get(168, 0):>+7.2f}% "
            f"{s.expectancy_7d:>+7.2f}% "
            f"{s.max_drawdown:>+7.2f}%"
        )
