"""CLI entrypoint for finance-bot.

Usage:
    uv run python main.py sync-prices            # one-shot price sync
    uv run python main.py sync-prices --symbol FPT
    uv run python main.py db-init                # apply schema.sql via SQLAlchemy create_all
    uv run python main.py show-config            # dump effective settings + watchlist
"""
from __future__ import annotations

import argparse
import sys

from finance_bot.logger import setup_logger


def cmd_sync_prices(args: argparse.Namespace) -> int:
    from finance_bot.jobs.sync_prices import sync_all, sync_one
    from finance_bot.settings import get_watchlist

    if args.symbol:
        wl = get_watchlist()
        target = next((a for a in wl.assets if a.symbol == args.symbol), None)
        if not target:
            print(f"Symbol {args.symbol!r} not in watchlist.", file=sys.stderr)
            return 2
        sync_one(target)
    else:
        sync_all()
    return 0


def cmd_sync_news(_: argparse.Namespace) -> int:
    from finance_bot.jobs.sync_news import sync_all_news

    sync_all_news()
    return 0


def cmd_llm_health(_: argparse.Namespace) -> int:
    from finance_bot.ai.llm import ClaudeClient

    client = ClaudeClient()
    ok = client.health()
    print(f"Claude binary:  {client.binary}")
    print(f"Model:          {client.model}")
    print(f"Health:         {'OK' if ok else 'FAILED'}")
    return 0 if ok else 1


def cmd_eval_outcomes(_: argparse.Namespace) -> int:
    from finance_bot.jobs.eval_outcomes import evaluate_all

    n = evaluate_all()
    print(f"Inserted {n} new outcome rows.")
    return 0


def cmd_process_feedback(_: argparse.Namespace) -> int:
    from finance_bot.jobs.process_feedback import process_feedback

    n = process_feedback()
    print(f"Processed {n} feedback callback(s).")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    from datetime import date as _date
    from pathlib import Path as _Path

    from finance_bot.jobs.backtest import print_summary, run_backtest

    start = _date.fromisoformat(args.start)
    end = _date.fromisoformat(args.end)
    symbols = [s.strip() for s in (args.symbols or "").split(",") if s.strip()] or None
    output = _Path(args.output) if args.output else None
    stats = run_backtest(start, end, symbols=symbols, output_csv=output)
    print_summary(stats)
    return 0


def cmd_add_knowledge(args: argparse.Namespace) -> int:
    from finance_bot.ai.memory import learn_knowledge
    from finance_bot.db.repositories import insert_knowledge, update_knowledge_chroma_id
    from finance_bot.db.session import get_session

    if args.body_file:
        with open(args.body_file, encoding="utf-8") as f:
            body = f.read().strip()
    elif args.body:
        body = args.body
    else:
        print("Nhập body (Ctrl-D để kết thúc):", file=sys.stderr)
        body = sys.stdin.read().strip()

    if not body:
        print("Body rỗng, hủy.", file=sys.stderr)
        return 2

    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]

    with get_session() as session:
        kb = insert_knowledge(session, title=args.title, body=body,
                              tags=tags or None, source="user")
        kb_id = kb.id

    chroma_id = learn_knowledge(kb_id, args.title, body, tags=tags, source="user")

    with get_session() as session:
        update_knowledge_chroma_id(session, kb_id, chroma_id)

    print(f"OK: knowledge id={kb_id}, chroma_id={chroma_id}")
    return 0


def cmd_list_knowledge(_: argparse.Namespace) -> int:
    from finance_bot.db.repositories import list_knowledge
    from finance_bot.db.session import get_session

    with get_session() as session:
        items = list_knowledge(session, only_active=False)

    if not items:
        print("(chưa có knowledge nào)")
        return 0

    for kb in items:
        flag = " " if kb.is_active else "X"
        tag_str = ",".join(kb.tags) if kb.tags else "-"
        body_preview = kb.body.replace("\n", " ")[:60]
        print(f"  [{flag}] #{kb.id:<4} {kb.created_at:%Y-%m-%d}  "
              f"[{kb.source:<8}] tags={tag_str}")
        print(f"          {kb.title}")
        print(f"          → {body_preview}…")
    return 0


def cmd_sync_knowledge(_: argparse.Namespace) -> int:
    from finance_bot.jobs.sync_knowledge import sync_all_knowledge

    embedded, deactivated = sync_all_knowledge()
    print(f"OK: embedded={embedded}, deactivated={deactivated}")
    return 0


def cmd_rag_status(_: argparse.Namespace) -> int:
    from finance_bot.ai.rag import status_summary

    summary = status_summary()
    print("=== RAG status ===")
    for name, count in summary.items():
        print(f"  {name:<22} {count} documents")
    return 0


def cmd_run_signals(args: argparse.Namespace) -> int:
    from finance_bot.jobs.run_signals import run_all, run_for
    from finance_bot.settings import get_watchlist

    if args.symbol:
        wl = get_watchlist()
        target = next((a for a in wl.assets if a.symbol == args.symbol), None)
        if not target:
            print(f"Symbol {args.symbol!r} not in watchlist.", file=sys.stderr)
            return 2
        if target.context_only:
            print(f"Symbol {args.symbol!r} is context-only — không sinh signal.",
                  file=sys.stderr)
            return 2
        run_for(target)
    else:
        run_all()
    return 0


def cmd_schedule_run(_: argparse.Namespace) -> int:
    """Single-cron-tick dispatcher: trigger every task due at `now`."""
    from finance_bot.jobs.scheduler import run_due_tasks
    run_due_tasks()
    return 0


def cmd_schedule_list(_: argparse.Namespace) -> int:
    """Print every task in config/schedule.py — useful for sanity checks."""
    from finance_bot.jobs.scheduler import list_schedule
    tasks = list_schedule()
    if not tasks:
        print("(config/schedule.py không có task nào)")
        return 0
    print(f"=== {len(tasks)} task(s) in config/schedule.py ===")
    for t in tasks:
        print(f"  {t.describe()}")
    return 0


def cmd_sync_fundamentals(args: argparse.Namespace) -> int:
    from finance_bot.jobs.sync_fundamentals import run_all, sync_one
    from finance_bot.settings import get_watchlist

    if args.symbol:
        wl = get_watchlist()
        target = next((a for a in wl.assets if a.symbol == args.symbol), None)
        if not target or target.asset_class != "vn_stock":
            print(f"Symbol {args.symbol!r} not a vn_stock in watchlist.", file=sys.stderr)
            return 2
        sync_one(target)
    else:
        run_all()
    return 0


def cmd_sync_industry_averages(_: argparse.Namespace) -> int:
    from finance_bot.jobs.sync_industry_averages import run_all
    run_all()
    return 0


def cmd_show_scores(args: argparse.Namespace) -> int:
    """Dump the 3 evaluation scores + composite for one symbol without persisting."""
    from finance_bot.analysis.signal import analyze_composite
    from finance_bot.db.queries import (
        load_macro_close_series,
        load_ohlcv_df,
        load_recent_news,
    )
    from finance_bot.db.repositories import upsert_asset
    from finance_bot.db.session import get_session
    from finance_bot.jobs.run_signals import (
        _build_macro_briefs,
        _build_micro_inputs,
        _build_news_briefs,
    )
    from finance_bot.settings import get_watchlist

    del load_macro_close_series, load_ohlcv_df, load_recent_news  # only for symmetry

    wl = get_watchlist()
    target = next((a for a in wl.assets if a.symbol == args.symbol), None)
    if not target:
        print(f"Symbol {args.symbol!r} not in watchlist.", file=sys.stderr)
        return 2

    with get_session() as session:
        from finance_bot.db.queries import load_ohlcv_df as _load_df  # noqa: PLR0915
        asset = upsert_asset(session, target)
        df = _load_df(session, asset.id, "1d", limit=500)
        if len(df) < 60:
            print(f"{target.symbol}: chỉ có {len(df)} nến — cần ≥60, "
                  "chạy `sync-prices` trước.", file=sys.stderr)
            return 2
        news = _build_news_briefs(session, target)
        macro = _build_macro_briefs(session, wl)
        fundamentals, industry_avg = _build_micro_inputs(session, target)

    draft = analyze_composite(
        target, df, wl,
        macro_briefs=macro, news_briefs=news,
        fundamentals=fundamentals, industry_avg=industry_avg,
    )
    comp = draft.composite
    print(f"=== {target.symbol} ({target.asset_class}) ===")
    print(f"  draft side={draft.side}  tier={draft.tier}  "
          f"confidence={draft.confidence:.2f}  price={draft.price_at_signal}")
    if comp is None:
        print("  (no composite — legacy fallback)")
        return 0
    print(f"  composite={comp.composite:+.2f}  "
          f"agreeing={comp.agreeing_services}/3  news_against={comp.news_against}")
    print(f"  tech : {comp.tech_score.score}   reason={comp.tech_score.reason}")
    print(f"  macro: {comp.macro_score.score}  reason={comp.macro_score.reason}")
    print(f"  micro: {comp.micro_score.score}  reason={comp.micro_score.reason}")
    return 0


def cmd_seed_watchlist(args: argparse.Namespace) -> int:
    """Seed watchlist_entries table from config/watchlist.yaml."""
    from finance_bot.db.repositories import upsert_watchlist_entry_from_cfg
    from finance_bot.db.session import get_session
    from finance_bot.settings import _load_watchlist_from_yaml, reload_watchlist_cache

    wl = _load_watchlist_from_yaml()
    inserted = 0
    updated = 0
    with get_session() as session:
        for cfg in wl.assets:
            _entry, was_new = upsert_watchlist_entry_from_cfg(
                session, cfg, overwrite=args.force
            )
            if was_new:
                inserted += 1
            elif args.force:
                updated += 1

    reload_watchlist_cache()
    print(f"OK: seeded watchlist_entries — inserted={inserted}, "
          f"updated={updated} (force={args.force})")
    return 0


def cmd_db_init(_: argparse.Namespace) -> int:
    """Create tables via SQLAlchemy. On a fresh DB, also stamps every pending
    migration as "applied" — since `create_all` already produces the schema
    those migrations describe, running them again would duplicate columns.
    """
    from sqlalchemy import inspect

    from finance_bot.db.migrations import stamp_pending
    from finance_bot.db.models import Base
    from finance_bot.db.session import get_engine

    engine = get_engine()
    before = set(inspect(engine).get_table_names())
    Base.metadata.create_all(engine)
    after = set(inspect(engine).get_table_names())

    if not before:
        # Truly fresh DB — the brand-new tables already reflect every
        # migration file. Stamp them so `db-migrate` won't try to ALTER
        # columns that create_all just installed.
        stamped = stamp_pending(engine=engine)
        if stamped:
            print(f"OK: created {len(after)} tables on fresh DB. "
                  f"Stamped {len(stamped)} migration(s) as applied:")
            for name in stamped:
                print(f"  - {name}")
        else:
            print(f"OK: created {len(after)} tables on fresh DB.")
    else:
        new_tables = after - before
        if new_tables:
            print(f"OK: created {len(new_tables)} new table(s): "
                  f"{', '.join(sorted(new_tables))}")
        else:
            print("OK: tables already exist.")
    return 0


def cmd_db_stamp(_: argparse.Namespace) -> int:
    """Mark all pending migrations as applied WITHOUT running their SQL.

    Use only when the schema is already up-to-date — typically after a
    manual `db-init` on a fresh DB (before this command's auto-stamp logic
    existed) or after `mysql < file.sql` outside the runner.
    """
    from finance_bot.db.migrations import stamp_pending

    stamped = stamp_pending()
    if not stamped:
        print("No pending migrations to stamp.")
        return 0
    print(f"Stamped {len(stamped)} migration(s) as applied (SQL NOT executed):")
    for name in stamped:
        print(f"  - {name}")
    return 0


def cmd_db_migrate(_: argparse.Namespace) -> int:
    """Run every SQL file in migrations/ not yet recorded in schema_migrations.

    Idempotent — re-running after success is a no-op. SQLite (test mode) is
    skipped because test schema is built fresh from models.py via db-init.
    """
    from finance_bot.db.migrations import run_pending

    newly, already, skipped_sqlite = run_pending()

    if skipped_sqlite:
        print(f"SKIPPED (SQLite test mode): {len(skipped_sqlite)} file(s)")
        for name in skipped_sqlite:
            print(f"  -  {name}")
        print("Tip: APP_ENV=test uses SQLite — schema comes from db-init, not migrations.")
        return 0

    if not newly and not already:
        print("No migration files found in migrations/.")
        return 0

    for name in already:
        print(f"  already:  {name}")
    for name in newly:
        print(f"  APPLIED:  {name}")
    print(f"\nOK: {len(newly)} newly applied, {len(already)} already up to date.")
    return 0


def cmd_show_config(_: argparse.Namespace) -> int:
    from finance_bot.settings import get_settings, get_watchlist

    s = get_settings()
    wl = get_watchlist()
    print("=== settings ===")
    print(f"  mysql_url       = {s.mysql_url}")
    print(f"  claude_binary   = {s.claude_binary}")
    print(f"  claude_model    = {s.claude_model}")
    print(f"  claude_timeout  = {s.claude_timeout_seconds}s")
    print(f"  embedding_model = {s.embedding_model}")
    print(f"  log_level       = {s.log_level}")

    print(f"\n=== watchlist ({len(wl.assets)} assets, "
          f"{len(wl.primary_assets)} primary / {len(wl.context_assets)} context) ===")
    for a in wl.assets:
        flag = "ctx" if a.context_only else "   "
        print(f"  [{flag}] {a.symbol:<10} [{a.asset_class:<9}] "
              f"src={a.source:<8} tfs={a.timeframes}")

    print(f"\n=== news_sources ({len(wl.news_sources)}) ===")
    for n in wl.news_sources:
        print(f"  - {n.name} ({n.lang}) tags={n.tags}")

    print("\n=== signal ===")
    print(f"  Tier A: agree>={wl.signal.tier_a.min_agree_ratio:.0%}, "
          f"conf>={wl.signal.tier_a.min_confidence}, "
          f"news_not_against={wl.signal.tier_a.require_news_not_against}")
    print(f"  Tier B: agree>={wl.signal.tier_b.min_agree_ratio:.0%}, "
          f"conf>={wl.signal.tier_b.min_confidence}")
    print(f"  cooldown_hours_per_ticker = {wl.signal.cooldown_hours_per_ticker}")
    print(f"  default_horizon_days      = {wl.signal.default_horizon_days}")

    print("\n=== risk ===")
    print(f"  ATR period           = {wl.risk.atr_period}")
    print(f"  SL = entry ± {wl.risk.stop_loss_atr_mult}*ATR")
    print(f"  TP target R:R        = 1:{wl.risk.take_profit_rr}")

    print(f"\n=== schedule ({wl.schedule.timezone}) ===")
    print(f"  vn_eod_close_local = {wl.schedule.vn_eod_close_local}")
    print(f"  global_eod_local   = {wl.schedule.global_eod_local}")
    print(f"  signal_run_local   = {wl.schedule.signal_run_local}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="finance-bot")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_sync = sub.add_parser("sync-prices", help="Fetch & upsert OHLCV for watchlist")
    p_sync.add_argument("--symbol", help="Only sync this symbol")
    p_sync.set_defaults(func=cmd_sync_prices)

    p_news = sub.add_parser("sync-news", help="Fetch RSS feeds & upsert into news table")
    p_news.set_defaults(func=cmd_sync_news)

    p_llm = sub.add_parser("llm-health", help="Check Claude CLI is installed & runnable")
    p_llm.set_defaults(func=cmd_llm_health)

    p_eval = sub.add_parser("eval-outcomes",
                            help="Eval P&L for past signals + re-embed into RAG")
    p_eval.set_defaults(func=cmd_eval_outcomes)

    p_pf = sub.add_parser("process-feedback",
                          help="Poll Telegram callback queue, ghi user_decision")
    p_pf.set_defaults(func=cmd_process_feedback)

    p_bt = sub.add_parser("backtest",
                          help="Backtest rule engine trên window lịch sử")
    p_bt.add_argument("--start", required=True, help="YYYY-MM-DD")
    p_bt.add_argument("--end", required=True, help="YYYY-MM-DD")
    p_bt.add_argument("--symbols", help="Comma-separated symbols (default: all primary)")
    p_bt.add_argument("--output", help="Optional CSV path để dump tất cả signals")
    p_bt.set_defaults(func=cmd_backtest)

    p_add = sub.add_parser("add-knowledge",
                           help="Thêm knowledge entry để bot học (RAG)")
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--body", help="Body text (else stdin or --body-file)")
    p_add.add_argument("--body-file", help="Đọc body từ file")
    p_add.add_argument("--tags", help="Comma-separated tags, vd: xau,dxy,macro")
    p_add.set_defaults(func=cmd_add_knowledge)

    p_lk = sub.add_parser("list-knowledge", help="Liệt kê knowledge entries")
    p_lk.set_defaults(func=cmd_list_knowledge)

    p_sk = sub.add_parser("sync-knowledge",
                          help="Re-embed mọi knowledge active vào ChromaDB")
    p_sk.set_defaults(func=cmd_sync_knowledge)

    p_rs = sub.add_parser("rag-status",
                          help="Hiển thị số document trong mỗi RAG collection")
    p_rs.set_defaults(func=cmd_rag_status)

    p_run = sub.add_parser("run-signals", help="Compute signals + alert Tier A")
    p_run.add_argument("--symbol", help="Only run for this symbol")
    p_run.set_defaults(func=cmd_run_signals)

    p_sr = sub.add_parser("schedule-run",
                          help="Tick the scheduler — dispatch all tasks due now")
    p_sr.set_defaults(func=cmd_schedule_run)

    p_sl = sub.add_parser("schedule-list",
                          help="List every task declared in config/schedule.py")
    p_sl.set_defaults(func=cmd_schedule_list)

    p_sf = sub.add_parser("sync-fundamentals",
                          help="Fetch ROA/ROE/P/E/P/B + industry_code (vn_stock only)")
    p_sf.add_argument("--symbol", help="Only sync this symbol")
    p_sf.set_defaults(func=cmd_sync_fundamentals)

    p_si = sub.add_parser("sync-industry-averages",
                          help="Aggregate per-industry ratio medians (weekly job)")
    p_si.set_defaults(func=cmd_sync_industry_averages)

    p_score = sub.add_parser("show-scores",
                             help="Dump 3 evaluation scores + composite for 1 symbol (no DB write)")
    p_score.add_argument("--symbol", required=True)
    p_score.set_defaults(func=cmd_show_scores)

    p_init = sub.add_parser("db-init", help="Create MySQL tables via SQLAlchemy")
    p_init.set_defaults(func=cmd_db_init)

    p_mig = sub.add_parser(
        "db-migrate",
        help="Apply pending SQL migrations from migrations/ (idempotent; MySQL only)",
    )
    p_mig.set_defaults(func=cmd_db_migrate)

    p_stamp = sub.add_parser(
        "db-stamp",
        help="Mark all pending migrations as applied WITHOUT running SQL "
             "(recovery for schema-already-up-to-date case)",
    )
    p_stamp.set_defaults(func=cmd_db_stamp)

    p_seed = sub.add_parser("seed-watchlist",
                            help="Seed watchlist_entries from watchlist.yaml")
    p_seed.add_argument("--force", action="store_true",
                        help="Overwrite existing rows (default: only insert new)")
    p_seed.set_defaults(func=cmd_seed_watchlist)

    p_cfg = sub.add_parser("show-config", help="Print effective config")
    p_cfg.set_defaults(func=cmd_show_config)

    return p


def main() -> int:
    setup_logger()
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
