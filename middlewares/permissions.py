from __future__ import annotations

from typing import Any
from typing import Awaitable
from typing import Callable

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import Message
from aiogram.types import TelegramObject

from database import AsyncSessionLocal
from database.models import User


class BanCheckMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)

        if event.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
            return await handler(event, data)

        if event.from_user is None or event.from_user.is_bot:
            return await handler(event, data)

        async with AsyncSessionLocal() as session:
            user = await session.get(User, event.from_user.id)

        if user is None:
            return await handler(event, data)

        if user.is_banned:
            try:
                await event.delete()
            except Exception:
                pass
            return None

        return await handler(event, data)