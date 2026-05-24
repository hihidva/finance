"""Seed one sample signal with a v2 checklist_report payload.

Run in test mode so the row lands in SQLite (the local web sandbox), not
your real MySQL:

    APP_ENV=test uv run python main.py db-init
    APP_ENV=test uv run python main.py seed-watchlist
    APP_ENV=test uv run python bin/seed_checklist_signal.py

Then start the test stack in a separate terminal:

    ./run.sh start_test    # API :5030, FE :5031

Open http://localhost:5031/signals, click View on the seeded signal,
verify the "Checklist vi mô" panel renders the 7 sections.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from decimal import Decimal

from finance_bot.analysis.evaluation_micro import (
    FundamentalSnapshot,
    compute_micro_score,
)
from finance_bot.db.models import Asset, Signal
from finance_bot.db.session import get_session
from finance_bot.settings import AssetConfig, get_settings


def _build_sample_history() -> list[FundamentalSnapshot]:
    """5Q of realistic fundamentals — passes most checks (good company)."""
    return [
        FundamentalSnapshot(
            asset_symbol="FPT", period="2025-Q4", period_end=date(2025, 12, 31),
            roa=0.10, roe=0.22, pe=15.0, pb=2.5, ps=1.8, ev_ebitda=8.5,
            roic=0.18, gross_margin=0.35, net_margin=0.18,
            revenue=12000.0, gross_profit=4200.0, net_profit=2160.0, eps=2800.0,
            cash_and_equivalents=3000.0, total_assets=18000.0, total_debt=4000.0,
            total_equity=10000.0, inventory=1200.0, receivables=2500.0,
            current_assets=7500.0, current_liabilities=4000.0,
            cfo=2800.0, capex=600.0, cff=-400.0, fcf=2200.0,
            de_ratio=0.4, current_ratio=1.875, quick_ratio=1.575,
            interest_coverage=12.0, inventory_days=18.0, receivable_days=37.5,
        ),
        FundamentalSnapshot(
            asset_symbol="FPT", period="2025-Q3", period_end=date(2025, 9, 30),
            revenue=11500.0, gross_margin=0.34, net_margin=0.175, cfo=2600.0,
        ),
        FundamentalSnapshot(
            asset_symbol="FPT", period="2025-Q2", period_end=date(2025, 6, 30),
            revenue=11000.0, gross_margin=0.33, net_margin=0.17, cfo=2400.0,
        ),
        FundamentalSnapshot(
            asset_symbol="FPT", period="2025-Q1", period_end=date(2025, 3, 31),
            revenue=10700.0, gross_margin=0.33, net_margin=0.165, cfo=2200.0,
        ),
        FundamentalSnapshot(
            asset_symbol="FPT", period="2024-Q4", period_end=date(2024, 12, 31),
            revenue=10500.0, gross_margin=0.32, net_margin=0.16,
            net_profit=1680.0, eps=2200.0, total_equity=9000.0,
            receivables=2200.0, inventory=1100.0, cfo=2300.0,
            inventory_days=20.0, receivable_days=42.0,
        ),
    ]


def main() -> int:
    settings = get_settings()
    print(f"APP_ENV={settings.app_env} db_url={settings.db_url}")
    if "sqlite" not in settings.db_url:
        print("WARNING: not running in test mode (DB is not SQLite). Aborting to "
              "avoid polluting your real DB. Set APP_ENV=test and retry.")
        return 1

    asset_cfg = AssetConfig(
        symbol="FPT", name="FPT Corporation", asset_class="vn_stock",
        source="vnstock", exchange="HOSE", timeframes=["1d"], context_only=False,
    )
    history = _build_sample_history()
    micro = compute_micro_score(asset_cfg, history[0], None, [], history=history)
    print(f"micro.score = {micro.score:+.3f}")
    print("Section results:")
    for k in ["section_1_1", "section_1_2", "section_1_3", "section_2_1",
              "section_2_2", "section_2_3", "section_2_4"]:
        s = micro.checklist_report[k]
        print(f"  {s['code']} {s['name']:25s}  pass={s['passed']} fail={s['failed']} na={s['n_a']}")

    indicators_payload = {
        "evaluation_version": "v2",
        "composite": float(micro.score or 0.0),
        "micro_score": {
            "score": micro.score,
            "breakdown": micro.breakdown,
            "news_against": micro.news_against,
            "reason": micro.reason,
            "checklist_report": micro.checklist_report,
        },
    }

    # SQLite doesn't autoincrement BigInteger PKs, so we assign ids manually.
    # MySQL ignores explicit id when the column is AUTO_INCREMENT — safe both ways.
    with get_session() as session:
        existing = session.query(Asset).filter(
            Asset.symbol == "FPT", Asset.asset_class == "vn_stock",
        ).one_or_none()
        if existing is None:
            next_asset_id = (
                session.query(Asset.id).order_by(Asset.id.desc()).first()
            )
            asset = Asset(
                id=(next_asset_id[0] + 1) if next_asset_id else 1,
                symbol="FPT", name="FPT Corporation", asset_class="vn_stock",
                source="vnstock", exchange="HOSE", is_active=True,
            )
            session.add(asset)
            session.flush()
        else:
            asset = existing

        next_signal_id_row = (
            session.query(Signal.id).order_by(Signal.id.desc()).first()
        )
        next_signal_id = (next_signal_id_row[0] + 1) if next_signal_id_row else 1
        signal = Signal(
            id=next_signal_id,
            asset_id=asset.id,
            timeframe="1d",
            ts=datetime.utcnow(),
            side="buy",
            tier="A",
            confidence=Decimal("0.85"),
            price_at_signal=Decimal("125.50"),
            entry_window="ato_next_session",
            indicators=indicators_payload,
            llm_model="seed",
            llm_reasoning="Sample seeded signal for verifying the checklist panel.",
            notified=False,
        )
        session.add(signal)
        session.flush()
        signal_id = signal.id

    print(f"\nSeeded signal id={signal_id} for FPT — open http://localhost:5031/signals and click 'View'.")
    print(json.dumps({"signal_id": signal_id}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
