from __future__ import annotations

import asyncio
import logging

import redis.asyncio as aioredis
from aiogram import Bot
from aiogram import Dispatcher
from aiogram.client.default import DefaultBotProperties

from config import settings
from handlers import (
    admin_router,
    analytics_router,
    info_router,
    locks_router,
    moderation_router,
    protection_router,
    settings_router,
    welcome_router,
)
from middlewares import BanCheckMiddleware
from middlewares import FloodMiddleware
from middlewares import RequestLoggingMiddleware
from services.scheduler import start_workers


async def on_startup(bot: Bot, **kwargs) -> None:
    redis = kwargs["redis"]
    await start_workers(bot, redis)


async def main() -> None:
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not configured.")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )

    redis = aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
    )
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp["redis"] = redis

    dp.update.outer_middleware(RequestLoggingMiddleware())
    dp.message.outer_middleware(BanCheckMiddleware())
    dp.message.outer_middleware(FloodMiddleware(redis))

    dp.include_router(welcome_router)
    dp.include_router(protection_router)
    dp.include_router(analytics_router)
    dp.include_router(moderation_router)
    dp.include_router(admin_router)
    dp.include_router(info_router)
    dp.include_router(locks_router)
    dp.include_router(settings_router)

    dp.startup.register(on_startup)

    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            drop_pending_updates=True,
        )
    finally:
        await dp.storage.close()
        await bot.session.close()
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())