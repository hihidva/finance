"""Job: pull latest candles for every asset × timeframe and persist to MySQL.

Cho cổ phiếu VN, fetch thêm khối ngoại / tự doanh / margin (vn_flows) và
corporate events (GDKHQ, cổ tức, …) cùng lúc. Asset có context_only=True
vẫn được fetch giá để feed cho LLM, nhưng không sinh signal độc lập.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta

from finance_bot.data.registry import fetcher_for
from finance_bot.data.vn_events import VnEventsFetcher
from finance_bot.data.vn_flows import VnFlowsFetcher
from finance_bot.db.repositories import (
    bulk_upsert_corporate_events,
    bulk_upsert_prices,
    bulk_upsert_vn_flows,
    latest_price_ts,
    upsert_asset,
    write_fetch_log,
)
from finance_bot.db.session import get_session
from finance_bot.logger import logger
from finance_bot.settings import AssetConfig, get_watchlist


def _since_for(last_ts: datetime | None, timeframe: str) -> datetime | None:
    if last_ts is None:
        return None
    overlap = {
        "15m": timedelta(hours=4),
        "1h": timedelta(days=1),
        "4h": timedelta(days=3),
        "1d": timedelta(days=7),
    }
    return last_ts - overlap.get(timeframe, timedelta(days=1))


def _sync_prices(asset_cfg: AssetConfig) -> None:
    fetcher = fetcher_for(asset_cfg.source)
    for timeframe in asset_cfg.timeframes:
        started = datetime.utcnow()
        rows_inserted = 0
        status = "ok"
        error: str | None = None

        try:
            with get_session() as session:
                asset = upsert_asset(session, asset_cfg)
                last_ts = latest_price_ts(session, asset.id, timeframe)
                since = _since_for(last_ts, timeframe)

                candles = fetcher.fetch(asset_cfg, timeframe, since=since)
                rows_inserted = bulk_upsert_prices(session, asset.id, timeframe, candles)
                logger.info(
                    "PRICE  {:>10}  {:<3}  fetched={}  inserted={}  last_ts={}",
                    asset_cfg.symbol, timeframe, len(candles), rows_inserted, last_ts,
                )
        except Exception as exc:
            status = "error"
            error = repr(exc)
            logger.exception("price sync failed for {} {}", asset_cfg.symbol, timeframe)

        with get_session() as session:
            asset = upsert_asset(session, asset_cfg)
            write_fetch_log(
                session,
                asset_id=asset.id,
                source=asset_cfg.source,
                kind="price",
                timeframe=timeframe,
                started_at=started,
                finished_at=datetime.utcnow(),
                status=status,
                rows_inserted=rows_inserted,
                error_message=error,
            )


def _sync_vn_flows(asset_cfg: AssetConfig, days: int = 90) -> None:
    started = datetime.utcnow()
    rows_inserted = 0
    status = "ok"
    error: str | None = None
    try:
        flows = VnFlowsFetcher().fetch(asset_cfg.symbol, days=days)
        with get_session() as session:
            asset = upsert_asset(session, asset_cfg)
            payload = [
                {k: v for k, v in asdict(r).items() if k != "raw"}
                | ({"raw_payload": r.raw} if r.raw else {})
                for r in flows
            ]
            rows_inserted = bulk_upsert_vn_flows(session, asset.id, payload)
        logger.info("FLOW   {:>10}      fetched={}  inserted={}",
                    asset_cfg.symbol, len(flows), rows_inserted)
    except Exception as exc:
        status = "error"
        error = repr(exc)
        logger.exception("vn_flows sync failed for {}", asset_cfg.symbol)

    with get_session() as session:
        asset = upsert_asset(session, asset_cfg)
        write_fetch_log(
            session,
            asset_id=asset.id,
            source="vnstock",
            kind="vn_flow",
            timeframe=None,
            started_at=started,
            finished_at=datetime.utcnow(),
            status=status,
            rows_inserted=rows_inserted,
            error_message=error,
        )


def _sync_corporate_events(asset_cfg: AssetConfig) -> None:
    started = datetime.utcnow()
    rows_inserted = 0
    status = "ok"
    error: str | None = None
    try:
        events = VnEventsFetcher().fetch(asset_cfg.symbol)
        with get_session() as session:
            asset = upsert_asset(session, asset_cfg)
            payload = [
                {k: v for k, v in asdict(e).items() if k != "raw"}
                | ({"raw_payload": e.raw} if e.raw else {})
                for e in events
            ]
            rows_inserted = bulk_upsert_corporate_events(session, asset.id, payload)
        logger.info("EVENT  {:>10}      fetched={}  inserted={}",
                    asset_cfg.symbol, len(events), rows_inserted)
    except Exception as exc:
        status = "error"
        error = repr(exc)
        logger.exception("corporate events sync failed for {}", asset_cfg.symbol)

    with get_session() as session:
        asset = upsert_asset(session, asset_cfg)
        write_fetch_log(
            session,
            asset_id=asset.id,
            source="vnstock",
            kind="corp_event",
            timeframe=None,
            started_at=started,
            finished_at=datetime.utcnow(),
            status=status,
            rows_inserted=rows_inserted,
            error_message=error,
        )


def sync_one(asset_cfg: AssetConfig) -> None:
    """Full sync for a single asset: prices + (if vn_stock) flows & events."""
    _sync_prices(asset_cfg)
    if asset_cfg.asset_class == "vn_stock":
        _sync_vn_flows(asset_cfg)
        _sync_corporate_events(asset_cfg)


def sync_all() -> None:
    wl = get_watchlist()
    logger.info("sync_all: starting for {} assets ({} primary, {} context-only)",
                len(wl.assets), len(wl.primary_assets), len(wl.context_assets))
    for asset_cfg in wl.assets:
        sync_one(asset_cfg)
    logger.info("sync_all: done")
