from __future__ import annotations

import logging
from typing import Any
from typing import Awaitable
from typing import Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from aiogram.types import Update

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        update_type = "unknown"
        chat_id = "N/A"
        user_id = "N/A"

        if isinstance(event, Update):
            update_type = event.event_type
            if event.message:
                chat_id = event.message.chat.id
                if event.message.from_user:
                    user_id = event.message.from_user.id
            elif event.callback_query:
                if event.callback_query.message:
                    chat_id = event.callback_query.message.chat.id
                user_id = event.callback_query.from_user.id

        logger.debug("[UPDATE] type=%s chat=%s user=%s", update_type, chat_id, user_id)

        try:
            return await handler(event, data)
        except Exception as exc:
            logger.error(
                "[ERROR] %s: %s",
                exc.__class__.__name__,
                exc,
                exc_info=True,
            )
            raise