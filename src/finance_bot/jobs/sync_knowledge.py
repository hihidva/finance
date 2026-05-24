"""Sync knowledge entries between MySQL ↔ ChromaDB.

Vai trò: cho phép user thêm/sửa/xoá knowledge ở MySQL (CLI hoặc tay), rồi
chạy job này để re-embed những entry chưa có chroma_id hoặc đã được update.
Đây là cách bot "được cập nhật kiến thức mới" mà user yêu cầu.
"""
from __future__ import annotations

from finance_bot.ai.memory import forget_knowledge, learn_knowledge
from finance_bot.db.models import Knowledge
from finance_bot.db.repositories import update_knowledge_chroma_id
from finance_bot.db.session import get_session
from finance_bot.logger import logger
from sqlalchemy import select


def sync_all_knowledge() -> tuple[int, int]:
    """(re-)embed mọi knowledge active vào Chroma; xoá embedding của bản inactive.

    Returns: (embedded, deactivated)
    """
    embedded = 0
    deactivated = 0

    with get_session() as session:
        all_kb = list(session.scalars(select(Knowledge)).all())
        for kb in all_kb:
            if not kb.is_active:
                if kb.chroma_id:
                    forget_knowledge(kb.id)
                    kb.chroma_id = None
                    deactivated += 1
                continue
            chroma_id = learn_knowledge(
                kb.id, kb.title, kb.body, tags=kb.tags or [], source=kb.source
            )
            if kb.chroma_id != chroma_id:
                update_knowledge_chroma_id(session, kb.id, chroma_id)
            embedded += 1

    logger.info("sync_knowledge: embedded={} deactivated={}", embedded, deactivated)
    return embedded, deactivated
