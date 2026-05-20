from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


class CaptchaAnswerCallback(CallbackData, prefix="captcha"):
    chat_id: int
    user_id: int
    answer: int


def build_captcha_keyboard(
    chat_id: int,
    user_id: int,
    options: tuple[int, int, int, int],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for option in options:
        builder.button(
            text=str(option),
            callback_data=CaptchaAnswerCallback(
                chat_id=chat_id,
                user_id=user_id,
                answer=option,
            ).pack(),
        )

    builder.adjust(2)
    return builder.as_markup()
