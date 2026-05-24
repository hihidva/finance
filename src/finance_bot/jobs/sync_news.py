"""Job: pull RSS feeds, normalize, persist into `news` table.

Sentiment analysis chưa làm ở đây (giữ nhẹ). Final-arbiter đọc trực tiếp tin
gần đây ở thời điểm chạy signal để đánh giá news_against trong cùng một LLM call.
"""
from __future__ import annotations

from datetime import datetime

from finance_bot.data.news import NewsItem, fetch_rss
from finance_bot.db.repositories import bulk_upsert_news, write_fetch_log
from finance_bot.db.session import get_session
from finance_bot.logger import logger
from finance_bot.settings import get_watchlist


def _to_row(item: NewsItem) -> dict:
    return {
        "source": item.source,
        "url": item.url[:768],
        "title": item.title[:512],
        "summary": item.summary,
        "published_at": item.published_at,
        "lang": item.lang,
        "tags": item.tags,
        "related_symbols": None,   # M4 sẽ fill khi RAG/embedding hoàn thiện
        "sentiment": None,
        "sentiment_label": None,
        "chroma_id": None,
    }


def sync_all_news() -> None:
    wl = get_watchlist()
    logger.info("sync_news: starting for {} sources", len(wl.news_sources))

    total_inserted = 0
    for src in wl.news_sources:
        started = datetime.utcnow()
        rows_inserted = 0
        status = "ok"
        error: str | None = None
        try:
            items = fetch_rss(src)
            payload = [_to_row(i) for i in items]
            with get_session() as session:
                rows_inserted = bulk_upsert_news(session, payload)
            total_inserted += rows_inserted
            logger.info("NEWS  {:<28}  fetched={}  inserted={}",
                        src.name, len(items), rows_inserted)
        except Exception as exc:
            status = "error"
            error = repr(exc)
            logger.exception("RSS sync failed for {}", src.name)

        with get_session() as session:
            write_fetch_log(
                session,
                asset_id=None,
                source=src.name,
                kind="news",
                timeframe=None,
                started_at=started,
                finished_at=datetime.utcnow(),
                status=status,
                rows_inserted=rows_inserted,
                error_message=error,
            )

    logger.info("sync_news: done — total inserted={}", total_inserted)
