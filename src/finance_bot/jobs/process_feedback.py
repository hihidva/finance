"""Job: poll Telegram getUpdates → ghi user_decision vào signals.

Vì user chọn cron-only (không daemon dài), ta dùng `getUpdates(timeout=0)` —
gọi nhanh, không long-poll. Mỗi lần chạy:
  1. Đọc `_telegram_offset` (last update_id đã xử lý) từ file local.
  2. Gọi getUpdates(offset=last+1, timeout=0).
  3. Với mỗi callback_query có data 'act:enter:<id>' / 'act:skip:<id>':
       - Update signals.user_decision.
       - answerCallbackQuery để Telegram tắt loading icon ở client.
       - editMessageReplyMarkup để xoá keyboard (tránh user click 2 lần).
  4. Lưu offset mới.

Cron đề xuất: chạy mỗi 5–10 phút.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from finance_bot.db.repositories import set_user_decision
from finance_bot.db.session import get_session
from finance_bot.logger import logger
from finance_bot.notifier.telegram import parse_callback_data
from finance_bot.settings import PROJECT_ROOT, get_settings

OFFSET_FILE = PROJECT_ROOT / ".telegram_offset.json"


def _read_offset() -> int:
    if not OFFSET_FILE.exists():
        return 0
    try:
        return int(json.loads(OFFSET_FILE.read_text(encoding="utf-8")).get("offset", 0))
    except Exception:
        return 0


def _write_offset(value: int) -> None:
    OFFSET_FILE.write_text(json.dumps({"offset": int(value)}), encoding="utf-8")


def _decision_label(action: str) -> str:
    return {"enter": "entered", "skip": "skipped"}[action]


def _ack_text(action: str) -> str:
    return {"enter": "Đã ghi nhận: vào lệnh", "skip": "Đã ghi nhận: bỏ qua"}[action]


async def _process_async() -> int:
    settings = get_settings()
    if not settings.telegram_bot_token:
        logger.warning("Telegram token chưa cấu hình — skip process_feedback")
        return 0

    from telegram import Bot

    bot = Bot(token=settings.telegram_bot_token)
    offset = _read_offset()
    try:
        updates = await bot.get_updates(
            offset=offset + 1 if offset else None,
            timeout=0,
            allowed_updates=["callback_query"],
        )
    except Exception:
        logger.exception("getUpdates failed")
        return 0

    processed = 0
    last_seen = offset

    for u in updates:
        last_seen = max(last_seen, int(u.update_id))
        cb = getattr(u, "callback_query", None)
        if cb is None:
            continue

        parsed = parse_callback_data(cb.data or "")
        if not parsed:
            try:
                await bot.answer_callback_query(cb.id, text="Callback không hợp lệ")
            except Exception:
                pass
            continue

        action, signal_id = parsed
        decision = _decision_label(action)

        with get_session() as session:
            updated = set_user_decision(session, signal_id, decision)

        if updated is None:
            logger.warning("Callback cho signal_id={} nhưng không tìm thấy trong DB",
                           signal_id)
            try:
                await bot.answer_callback_query(cb.id, text="Signal không còn tồn tại")
            except Exception:
                pass
            continue

        try:
            await bot.answer_callback_query(cb.id, text=_ack_text(action))
        except Exception:
            logger.exception("answer_callback_query failed")

        # Remove inline keyboard so user không bấm thêm
        try:
            chat_id = cb.message.chat.id
            msg_id = cb.message.message_id
            await bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=msg_id, reply_markup=None,
            )
        except Exception:
            logger.debug("edit_message_reply_markup failed (có thể message quá cũ)",
                         exc_info=True)

        processed += 1
        logger.info("FEEDBACK signal_id={} → {}", signal_id, decision)

    if last_seen > offset:
        _write_offset(last_seen)

    return processed


def process_feedback() -> int:
    try:
        return asyncio.run(_process_async())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_process_async())
        finally:
            loop.close()
