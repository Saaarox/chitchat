from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any
from typing import Awaitable
from typing import Callable

import redis.asyncio as aioredis
from aiogram import BaseMiddleware
from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.types import ChatPermissions
from aiogram.types import Message
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import AsyncSessionLocal
from database.models import User
from database.models import Warning
from services.group_settings import FloodAction
from services.group_settings import FloodSettings
from services.group_settings import get_flood_settings
from services.group_settings import is_exempt_group_member


class FloodMiddleware(BaseMiddleware):
    def __init__(
        self,
        redis: aioredis.Redis,
        session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
    ) -> None:
        self.redis = redis
        self.session_factory = session_factory

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

        async with self.session_factory() as session:
            flood_settings = await get_flood_settings(
                session,
                chat_id=event.chat.id,
                title=event.chat.title,
            )
            if not flood_settings.enabled:
                return await handler(event, data)

            if await is_exempt_group_member(
                session,
                group_id=event.chat.id,
                user_id=event.from_user.id,
            ):
                return await handler(event, data)

        counter_key = f"flood:{event.chat.id}:{event.from_user.id}"
        message_count = await self.redis.incr(counter_key)
        if message_count == 1:
            await self.redis.expire(counter_key, flood_settings.window_seconds)

        data["flood_settings"] = flood_settings
        data["flood_message_count"] = int(message_count)

        if int(message_count) <= flood_settings.max_messages:
            return await handler(event, data)

        await self.apply_flood_action(
            message=event,
            data=data,
            flood_settings=flood_settings,
        )
        await self.redis.delete(counter_key)
        return None

    async def apply_flood_action(
        self,
        message: Message,
        data: dict[str, Any],
        flood_settings: FloodSettings,
    ) -> None:
        bot = self._resolve_bot(message=message, data=data)

        if flood_settings.action == FloodAction.DELETE:
            await self.safe_delete_message(message)
            return

        if flood_settings.action == FloodAction.MUTE:
            await bot.restrict_chat_member(
                chat_id=message.chat.id,
                user_id=message.from_user.id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=datetime.now(timezone.utc) + timedelta(seconds=flood_settings.mute_seconds),
            )
            return

        await self.issue_warning(
            bot=bot,
            message=message,
            flood_settings=flood_settings,
        )

    async def issue_warning(
        self,
        bot: Bot,
        message: Message,
        flood_settings: FloodSettings,
    ) -> None:
        bot_user = await bot.get_me()
        reason = (
            "Anti-flood triggered: "
            f"more than {flood_settings.max_messages} messages "
            f"in {flood_settings.window_seconds} seconds."
        )

        async with self.session_factory() as session:
            offender = await self.ensure_user_record(
                session=session,
                user_id=message.from_user.id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
            )
            issuer = await self.ensure_user_record(
                session=session,
                user_id=bot_user.id,
                username=bot_user.username,
                first_name=bot_user.first_name,
            )

            offender.warn_count += 1
            session.add(
                Warning(
                    user_id=offender.user_id,
                    group_id=message.chat.id,
                    reason=reason,
                    given_by=issuer.user_id,
                    expires_at=None,
                )
            )
            await session.commit()

    async def ensure_user_record(
        self,
        session: AsyncSession,
        user_id: int,
        username: str | None,
        first_name: str | None,
    ) -> User:
        user = await session.get(User, user_id)
        if user is None:
            user = User(
                user_id=user_id,
                username=username,
                first_name=first_name,
            )
            session.add(user)
            await session.flush()
            return user

        user.username = username
        user.first_name = first_name
        await session.flush()
        return user

    @staticmethod
    async def safe_delete_message(message: Message) -> None:
        try:
            await message.delete()
        except Exception:
            return

    @staticmethod
    def _resolve_bot(message: Message, data: dict[str, Any]) -> Bot:
        bot = data.get("bot")
        if isinstance(bot, Bot):
            return bot
        return message.bot
