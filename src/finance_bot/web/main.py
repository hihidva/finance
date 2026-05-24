"""FastAPI app — entry point for the dashboard backend.

Run via run.sh (preferred):
    ./run.sh start         # API :4030, frontend :4031, DB=finance_bot
    ./run.sh start_test    # API :5030, frontend :5031, DB=finance_test

Or directly:
    uv run uvicorn finance_bot.web.main:app --host 127.0.0.1 --port 4030 --reload
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from finance_bot.logger import setup_logger
from finance_bot.web.api import health, indicators, prices, signals, watchlist


def create_app() -> FastAPI:
    setup_logger()
    app = FastAPI(
        title="finance-bot dashboard API",
        version="0.1.0",
        description="Local-only JSON API serving the Next.js dashboard.",
    )

    # Bind 127.0.0.1 only — Next.js dev runs at a different origin so CORS
    # is still needed. Allow both normal (:4031) and test-mode (:5031) frontends.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:4031",
            "http://127.0.0.1:4031",
            "http://localhost:5031",
            "http://127.0.0.1:5031",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    app.include_router(health.router, prefix="/api", tags=["meta"])
    app.include_router(watchlist.router, prefix="/api/watchlist", tags=["watchlist"])
    app.include_router(prices.router, prefix="/api/prices", tags=["prices"])
    app.include_router(indicators.router, prefix="/api/indicators", tags=["indicators"])
    app.include_router(signals.router, prefix="/api/signals", tags=["signals"])

    return app


app = create_app()
