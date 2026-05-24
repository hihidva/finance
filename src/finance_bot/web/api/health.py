"""Health + meta endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from finance_bot.ai.llm import ClaudeClient
from finance_bot.db.repositories import list_watchlist_entries
from finance_bot.web.deps import db_session
from finance_bot.web.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health(session: Session = Depends(db_session)) -> HealthResponse:
    db_ok = False
    try:
        session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    llm = ClaudeClient()
    llm_ok = False
    try:
        llm_ok = llm.health()
    except Exception:
        llm_ok = False

    wl_count = 0
    if db_ok:
        try:
            wl_count = len(list_watchlist_entries(session, only_active=True))
        except Exception:
            wl_count = 0

    return HealthResponse(
        db=db_ok,
        llm=llm_ok,
        llm_model=llm.model,
        watchlist_count=wl_count,
    )
