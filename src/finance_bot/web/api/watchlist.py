"""Watchlist CRUD endpoints."""
from __future__ import annotations

import io

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from finance_bot.db.models import Signal
from finance_bot.db.repositories import (
    delete_watchlist_entry,
    get_watchlist_entry,
    get_watchlist_entry_by_symbol,
    insert_watchlist_entry,
    list_watchlist_entries,
    update_watchlist_entry,
)
from finance_bot.settings import reload_watchlist_cache
from finance_bot.web.deps import db_session
from finance_bot.web.schemas import (
    WatchlistEntryCreate,
    WatchlistEntryOut,
    WatchlistEntryPatch,
)

router = APIRouter()


@router.get("", response_model=list[WatchlistEntryOut])
def list_entries(
    only_active: bool = Query(False),
    asset_class: str | None = Query(None),
    session: Session = Depends(db_session),
) -> list[WatchlistEntryOut]:
    rows = list_watchlist_entries(
        session, only_active=only_active, asset_class=asset_class
    )
    return [WatchlistEntryOut.model_validate(r) for r in rows]


@router.post("", response_model=WatchlistEntryOut, status_code=201)
def create_entry(
    payload: WatchlistEntryCreate,
    session: Session = Depends(db_session),
) -> WatchlistEntryOut:
    _validate_source_for_class(payload.asset_class, payload.source)

    existing = get_watchlist_entry_by_symbol(session, payload.symbol)
    if existing:
        raise HTTPException(status_code=409, detail="Symbol đã tồn tại trong watchlist")

    entry = insert_watchlist_entry(session, **payload.model_dump())
    reload_watchlist_cache()
    return WatchlistEntryOut.model_validate(entry)


@router.get("/export", response_class=PlainTextResponse)
def export_yaml(session: Session = Depends(db_session)) -> str:
    """Dump current DB watchlist as YAML — useful for backup / version control."""
    rows = list_watchlist_entries(session, only_active=False)
    payload = {
        "assets": [
            {
                "symbol": r.symbol,
                "name": r.name,
                "asset_class": r.asset_class,
                "source": r.source,
                **({"exchange": r.exchange} if r.exchange else {}),
                "timeframes": list(r.timeframes) if r.timeframes else ["1d"],
                **({"context_only": True} if r.context_only else {}),
            }
            for r in rows
        ],
    }
    buf = io.StringIO()
    yaml.safe_dump(payload, buf, sort_keys=False, allow_unicode=True)
    return buf.getvalue()


@router.get("/{entry_id}", response_model=WatchlistEntryOut)
def get_entry(
    entry_id: int,
    session: Session = Depends(db_session),
) -> WatchlistEntryOut:
    entry = get_watchlist_entry(session, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Watchlist entry không tồn tại")
    return WatchlistEntryOut.model_validate(entry)


@router.patch("/{entry_id}", response_model=WatchlistEntryOut)
def patch_entry(
    entry_id: int,
    payload: WatchlistEntryPatch,
    session: Session = Depends(db_session),
) -> WatchlistEntryOut:
    fields = payload.model_dump(exclude_unset=True)
    if "asset_class" in fields and "source" in fields:
        _validate_source_for_class(fields["asset_class"], fields["source"])

    entry = update_watchlist_entry(session, entry_id, **fields)
    if entry is None:
        raise HTTPException(status_code=404, detail="Watchlist entry không tồn tại")
    reload_watchlist_cache()
    return WatchlistEntryOut.model_validate(entry)


@router.delete("/{entry_id}", status_code=204)
def remove_entry(
    entry_id: int,
    session: Session = Depends(db_session),
) -> None:
    entry = get_watchlist_entry(session, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Watchlist entry không tồn tại")

    # Soft guard: chặn xoá nếu symbol đã có signal lịch sử
    from sqlalchemy import select

    from finance_bot.db.models import Asset

    asset_id = session.scalars(
        select(Asset.id).where(Asset.symbol == entry.symbol)
    ).one_or_none()
    if asset_id is not None:
        has_signal = session.scalars(
            select(Signal.id).where(Signal.asset_id == asset_id).limit(1)
        ).one_or_none()
        if has_signal is not None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Symbol đã có signal lịch sử — không thể xoá. "
                    "Hãy `pause` (is_active=false) thay vì xoá để giữ lịch sử."
                ),
            )

    delete_watchlist_entry(session, entry_id)
    reload_watchlist_cache()


def _validate_source_for_class(asset_class: str, source: str) -> None:
    """Mirror business rule from doc §7.4.1."""
    allowed = {
        "vn_stock": {"vnstock"},
        "crypto": {"ccxt"},
        "commodity": {"yfinance"},
        "fx_index": {"yfinance"},
    }.get(asset_class, set())
    if source not in allowed:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Source {source!r} không hợp lệ cho asset_class {asset_class!r}. "
                f"Hợp lệ: {sorted(allowed)}"
            ),
        )
