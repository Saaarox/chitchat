from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

import redis.asyncio as aioredis
import pytz
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ChatPermissions

from services.warns import build_unrestricted_permissions

logger = logging.getLogger(__name__)

TEMP_MUTES_KEY = "guardbot:temp_mutes"
TEMP_BANS_KEY = "guardbot:temp_bans"
NIGHT_MODE_PREFIX = "guardbot:night:"
NIGHT_MODE_LAST_ACTION_PREFIX = "guardbot:night_last:"

LOCKED_PERMS = ChatPermissions(can_send_messages=False)
UNLOCKED_PERMS = ChatPermissions(can_send_messages=True)


async def schedule_temp_mute(
    redis: aioredis.Redis, chat_id: int, user_id: int, until_date: datetime
) -> None:
    score = int(until_date.timestamp())
    member = f"{chat_id}:{user_id}"
    await redis.zadd(TEMP_MUTES_KEY, {member: score})


async def schedule_temp_ban(
    redis: aioredis.Redis, chat_id: int, user_id: int, until_date: datetime
) -> None:
    score = int(until_date.timestamp())
    member = f"{chat_id}:{user_id}"
    await redis.zadd(TEMP_BANS_KEY, {member: score})


async def cancel_temp_mute(redis: aioredis.Redis, chat_id: int, user_id: int) -> None:
    member = f"{chat_id}:{user_id}"
    await redis.zrem(TEMP_MUTES_KEY, member)


async def cancel_temp_ban(redis: aioredis.Redis, chat_id: int, user_id: int) -> None:
    member = f"{chat_id}:{user_id}"
    await redis.zrem(TEMP_BANS_KEY, member)


async def punishment_worker(bot: Bot, redis: aioredis.Redis) -> None:
    logger.info("Punishment worker started")
    while True:
        try:
            await asyncio.sleep(15)
            now_ts = int(time.time())

            # Process expired mutes
            expired_mutes = await redis.zrange(TEMP_MUTES_KEY, 0, now_ts, byscore=True)
            if expired_mutes:
                for member in expired_mutes:
                    member_str = member if isinstance(member, str) else member.decode()
                    try:
                        chat_id_str, user_id_str = member_str.split(":")
                        chat_id, user_id = int(chat_id_str), int(user_id_str)
                        await bot.restrict_chat_member(
                            chat_id=chat_id,
                            user_id=user_id,
                            permissions=build_unrestricted_permissions(),
                            use_independent_chat_permissions=True,
                        )
                        logger.info("Unmuted user %d in chat %d", user_id, chat_id)
                    except TelegramAPIError as e:
                        logger.error("Failed to unmute %s: %s", member_str, e)
                    except (ValueError, TypeError) as e:
                        logger.error("Invalid member format in temp_mutes: %s (%s)", member_str, e)
                await redis.zrem(TEMP_MUTES_KEY, *expired_mutes)

            # Process expired bans
            expired_bans = await redis.zrange(TEMP_BANS_KEY, 0, now_ts, byscore=True)
            if expired_bans:
                for member in expired_bans:
                    member_str = member if isinstance(member, str) else member.decode()
                    try:
                        chat_id_str, user_id_str = member_str.split(":")
                        chat_id, user_id = int(chat_id_str), int(user_id_str)
                        await bot.unban_chat_member(
                            chat_id=chat_id,
                            user_id=user_id,
                            only_if_banned=True,
                        )
                        logger.info("Unbanned user %d in chat %d", user_id, chat_id)
                    except TelegramAPIError as e:
                        logger.error("Failed to unban %s: %s", member_str, e)
                    except (ValueError, TypeError) as e:
                        logger.error("Invalid member format in temp_bans: %s (%s)", member_str, e)
                await redis.zrem(TEMP_BANS_KEY, *expired_bans)

        except Exception as e:
            logger.exception("Critical error in punishment_worker: %s", e)
            await asyncio.sleep(60)  # Avoid fast crash loops


async def schedule_night_mode(
    redis: aioredis.Redis,
    group_id: int,
    lock_hour: int,
    lock_minute: int,
    unlock_hour: int,
    unlock_minute: int,
    timezone_str: str,
) -> None:
    key = f"{NIGHT_MODE_PREFIX}{group_id}"
    await redis.hset(
        key,
        mapping={
            "lock_time": f"{lock_hour:02d}:{lock_minute:02d}",
            "unlock_time": f"{unlock_hour:02d}:{unlock_minute:02d}",
            "timezone": timezone_str,
            "enabled": "1",
        },
    )


async def night_mode_worker(bot: Bot, redis: aioredis.Redis) -> None:
    logger.info("Night mode worker started")
    while True:
        try:
            await asyncio.sleep(60)
            async for key in redis.scan_iter(match=f"{NIGHT_MODE_PREFIX}*"):
                try:
                    group_id = int(key.split(":")[-1])
                    settings = await redis.hgetall(key)
                    if not settings or settings.get("enabled") != "1":
                        continue

                    tz = pytz.timezone(settings["timezone"])
                    now_local = datetime.now(tz)
                    lock_time_parts = list(map(int, settings["lock_time"].split(":")))
                    unlock_time_parts = list(map(int, settings["unlock_time"].split(":")))

                    lock_time = now_local.replace(hour=lock_time_parts[0], minute=lock_time_parts[1], second=0, microsecond=0)
                    unlock_time = now_local.replace(hour=unlock_time_parts[0], minute=unlock_time_parts[1], second=0, microsecond=0)

                    is_night = False
                    if lock_time < unlock_time:  # Same day window e.g., 09:00-17:00
                        if lock_time <= now_local < unlock_time:
                            is_night = True
                    else:  # Overnight window e.g., 22:00-06:00
                        if now_local >= lock_time or now_local < unlock_time:
                            is_night = True

                    last_action_key = f"{NIGHT_MODE_LAST_ACTION_PREFIX}{group_id}"
                    last_action = await redis.get(last_action_key)

                    if is_night and last_action != "locked":
                        await bot.set_chat_permissions(group_id, permissions=LOCKED_PERMS)
                        await redis.set(last_action_key, "locked")
                        logger.info("Night mode: locked group %d", group_id)
                    elif not is_night and last_action != "unlocked":
                        await bot.set_chat_permissions(group_id, permissions=UNLOCKED_PERMS)
                        await redis.set(last_action_key, "unlocked")
                        logger.info("Night mode: unlocked group %d", group_id)

                except (pytz.UnknownTimeZoneError, ValueError, TypeError) as e:
                    logger.error("Error processing night mode for key %s: %s", key, e)
                except TelegramAPIError as e:
                    logger.error("Telegram API error during night mode for key %s: %s", key, e)
        except Exception as e:
            logger.exception("Critical error in night_mode_worker: %s", e)
            await asyncio.sleep(60)


async def start_workers(bot: Bot, redis: aioredis.Redis) -> None:
    logger.info("Starting background workers")
    asyncio.create_task(punishment_worker(bot, redis))
    asyncio.create_task(night_mode_worker(bot, redis))
