from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession

from services.group_settings import ensure_group


async def get_log_channel_id(
    session: AsyncSession,
    group_id: int,
    title: str | None,
) -> int | None:
    group = await ensure_group(session, chat_id=group_id, title=title)
    return group.log_channel_id


async def log_to_group_channel(
    session: AsyncSession,
    bot: Bot,
    group_id: int,
    title: str | None,
    text: str,
) -> bool:
    log_channel_id = await get_log_channel_id(session, group_id=group_id, title=title)
    if log_channel_id is None:
        return False

    try:
        await bot.send_message(chat_id=log_channel_id, text=text)
    except (TelegramBadRequest, TelegramForbiddenError):
        return False

    return True
