from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


class ConfirmActionCallback(CallbackData, prefix="confirm"):
    action: str
    target_id: int
    chat_id: int
    confirmed: bool


def build_confirm_keyboard(
    action: str, target_id: int, chat_id: int
) -> InlineKeyboardMarkup:
    """Builds a confirmation keyboard for a moderation action."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Confirm",
        callback_data=ConfirmActionCallback(
            action=action, target_id=target_id, chat_id=chat_id, confirmed=True
        ).pack(),
    )
    builder.button(
        text="❌ Cancel",
        callback_data=ConfirmActionCallback(
            action=action, target_id=target_id, chat_id=chat_id, confirmed=False
        ).pack(),
    )
    builder.adjust(2)
    return builder.as_markup()
