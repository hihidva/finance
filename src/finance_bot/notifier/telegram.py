"""Telegram notifier — text alert kèm 2 inline buttons để user feedback.

Pipeline feedback:
  1. send_alert() gửi message + inline keyboard, trả về message_id để lưu vào DB.
  2. User bấm "Đã vào lệnh" / "Bỏ qua" → Telegram lưu callback vào hàng đợi.
  3. Job `process-feedback` (cron mỗi vài phút) gọi getUpdates → cập nhật user_decision.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from finance_bot.analysis.signal import SignalDecision
from finance_bot.logger import logger
from finance_bot.settings import get_settings

_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _fmt_price(value: float | None, asset_class: str) -> str:
    if value is None:
        return "-"
    if asset_class == "vn_stock":
        return f"{value:,.0f}"
    if asset_class == "crypto":
        return f"{value:,.2f}"
    return f"{value:,.4f}"


def _fmt_local(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    return dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(_VN_TZ).strftime("%Y-%m-%d %H:%M")


def format_alert(decision: SignalDecision) -> str:
    a = decision.asset
    side_label = {"buy": "MUA", "sell": "BÁN", "hold": "GIỮ"}[decision.side]
    arrow = {"buy": "▲", "sell": "▼", "hold": "■"}[decision.side]

    lines = [
        f"{arrow} TIER A · {side_label} {a.symbol}",
        f"   {a.name}  ({a.asset_class})",
        f"   Giá hiện tại: {_fmt_price(decision.price_at_signal, a.asset_class)}",
        f"   Confidence:   {decision.confidence:.2f}",
    ]

    if decision.snapshot:
        lines.append(
            f"   Indicators:   {decision.snapshot.buy_count} mua / "
            f"{decision.snapshot.sell_count} bán / "
            f"{7 - decision.snapshot.buy_count - decision.snapshot.sell_count} trung tính"
        )

    if decision.risk:
        r = decision.risk
        lines.append(
            f"   Vào: {_fmt_price(r.entry, a.asset_class)}   "
            f"SL: {_fmt_price(r.stop_loss, a.asset_class)} ({r.sl_basis})   "
            f"TP: {_fmt_price(r.take_profit, a.asset_class)}   "
            f"R:R 1:{r.rr_ratio:.1f}"
        )

    if decision.entry_window == "ato_next_session":
        lines.append(
            f"   Khớp lệnh dự kiến: ATO phiên kế tiếp ({_fmt_local(decision.expected_entry_at)})"
        )
    else:
        lines.append("   Khớp lệnh: ngay")

    if decision.snapshot:
        votes = ", ".join(
            f"{v.name}={v.side[0].upper()}({v.strength:.2f})"
            for v in decision.snapshot.votes
        )
        lines.append(f"   ({votes})")

    return "\n".join(lines)


def build_callback_data(action: str, signal_id: int) -> str:
    """Telegram callback_data ≤ 64 bytes; format: act:enter:<id> | act:skip:<id>."""
    return f"act:{action}:{signal_id}"


def parse_callback_data(data: str) -> tuple[str, int] | None:
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "act":
        return None
    if parts[1] not in ("enter", "skip"):
        return None
    try:
        return parts[1], int(parts[2])
    except ValueError:
        return None


class TelegramNotifier:
    def __init__(self) -> None:
        s = get_settings()
        self._token = s.telegram_bot_token
        self._chat_id = s.telegram_chat_id

    @property
    def configured(self) -> bool:
        return bool(self._token and self._chat_id)

    # ------------------------------------------------------------------
    # Send alert with inline-keyboard feedback buttons
    # ------------------------------------------------------------------
    def send_alert(self, message: str, signal_id: int) -> int | None:
        """Return Telegram message_id on success, else None."""
        if not self.configured:
            logger.warning("Telegram not configured (missing token/chat_id) — skipping send")
            return None
        try:
            return asyncio.run(self._send_alert_async(message, signal_id))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self._send_alert_async(message, signal_id))
            finally:
                loop.close()
        except Exception:
            logger.exception("Telegram send_alert failed")
            return None

    async def _send_alert_async(self, message: str, signal_id: int) -> int:
        from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Đã vào lệnh",
                                 callback_data=build_callback_data("enter", signal_id)),
            InlineKeyboardButton("⏭️ Bỏ qua",
                                 callback_data=build_callback_data("skip", signal_id)),
        ]])
        bot = Bot(token=self._token)
        msg = await bot.send_message(
            chat_id=self._chat_id, text=message, reply_markup=keyboard,
        )
        return int(msg.message_id)

    # ------------------------------------------------------------------
    # Plain text (used by other modules / debugging)
    # ------------------------------------------------------------------
    def send(self, message: str) -> bool:
        if not self.configured:
            logger.warning("Telegram not configured — skipping send")
            return False
        try:
            asyncio.run(self._send_async(message))
            return True
        except Exception:
            logger.exception("Telegram send failed")
            return False

    async def _send_async(self, message: str) -> None:
        from telegram import Bot

        bot = Bot(token=self._token)
        await bot.send_message(chat_id=self._chat_id, text=message)
