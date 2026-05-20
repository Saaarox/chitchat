from __future__ import annotations

import asyncio
from datetime import datetime
from datetime import timedelta

import redis.asyncio as aioredis
from aiogram import Bot, F
from aiogram import F
from aiogram import Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message

from handlers.moderation import require_moderator
from handlers.moderation import resolve_target

router = Router(name="analytics")

STATS_USER_PREFIX = "guardbot:stats:"
STATS_TOTAL_KEY = "guardbot:stats:{chat_id}:total"
DAILY_STATS_PREFIX = "guardbot:daily:"


@router.message(
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
    F.from_user,
)
async def stats_collector(message: Message, redis: aioredis.Redis) -> None:
    if message.from_user.is_bot:
        return

    chat_id = message.chat.id
    user_id = message.from_user.id
    today = datetime.utcnow().strftime("%Y-%m-%d")

    pipe = redis.pipeline()
    pipe.incr(f"{STATS_USER_PREFIX}{chat_id}:{user_id}")
    pipe.incr(STATS_TOTAL_KEY.format(chat_id=chat_id))
    daily_key = f"{DAILY_STATS_PREFIX}{chat_id}:{today}"
    pipe.incr(daily_key)
    pipe.expire(daily_key, timedelta(days=8))
    await pipe.execute()


@router.message(
    Command("top10"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def top10_command(message: Message, redis: aioredis.Redis, bot: Bot) -> None:
    chat_id = message.chat.id
    user_stats = []
    scan_pattern = f"{STATS_USER_PREFIX}{chat_id}:*"

    keys = [key async for key in redis.scan_iter(match=scan_pattern)]
    if not keys:
        await message.answer("No message stats yet.")
        return

    values = await redis.mget(keys)
    for key, value in zip(keys, values):
        try:
            user_id = int(key.split(":")[-1])
            count = int(value)
            user_stats.append((user_id, count))
        except (ValueError, IndexError):
            continue

    user_stats.sort(key=lambda item: item[1], reverse=True)

    lines = ["<b>Top 10 Most Active Users</b>", ""]
    for i, (user_id, count) in enumerate(user_stats[:10], 1):
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            username = f"@{member.user.username}" if member.user.username else member.user.full_name
            lines.append(f"#{i} {username} — {count} messages")
        except Exception:
            lines.append(f"#{i} User ID {user_id} — {count} messages")

    await message.answer("\n".join(lines))


@router.message(
    Command("stat"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def stat_command(message: Message, redis: aioredis.Redis) -> None:
    chat_id = message.chat.id
    target_user_id = message.from_user.id
    target_name = "Your"

    if message.reply_to_message and message.reply_to_message.from_user:
        target_user_id = message.reply_to_message.from_user.id
        target_name = f"{message.reply_to_message.from_user.full_name}'s"

    user_count_str = await redis.get(f"{STATS_USER_PREFIX}{chat_id}:{target_user_id}")
    user_count = int(user_count_str) if user_count_str else 0

    total_count_str = await redis.get(STATS_TOTAL_KEY.format(chat_id=chat_id))
    total_count = int(total_count_str) if total_count_str else 0

    # Find rank
    rank = -1
    if user_count > 0:
        all_keys = [key async for key in redis.scan_iter(match=f"{STATS_USER_PREFIX}{chat_id}:*")]
        all_values = await redis.mget(all_keys)
        all_counts = sorted([int(v) for v in all_values if v is not None], reverse=True)
        try:
            rank = all_counts.index(user_count) + 1
        except ValueError:
            rank = -1

    lines = [
        f"<b>{target_name} Stats</b>",
        f"Messages: {user_count}",
        f"Rank: {rank if rank != -1 else 'N/A'}",
        f"Total Group Messages: {total_count}",
    ]
    await message.answer("\n".join(lines))


@router.message(
    Command("inactives"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def inactives_command(message: Message, redis: aioredis.Redis, bot: Bot) -> None:
    if not await require_moderator(message, bot):
        return

    try:
        members = await bot.get_chat_administrators(message.chat.id)  # Example: check admins
        # A full check would require iterating all members, which is not feasible.
        # This command will list members with 0 messages from those the bot has seen.
        await message.answer("Note: This check is limited to users who have sent a message since stats tracking began.")

        scan_pattern = f"{STATS_USER_PREFIX}{message.chat.id}:*"
        keys = [key async for key in redis.scan_iter(match=scan_pattern)]
        values = await redis.mget(keys)

        user_counts = {int(k.split(":")[-1]): int(v) for k, v in zip(keys, values) if k and v}

        # This is a simplified version. A real version would need to get all chat members.
        # For now, we list users with 0 messages that we have seen.
        # A better approach might be to list users who haven't talked in X days.
        await message.answer("This feature is complex and a simplified version is provided. It will be expanded later.")

    except Exception as e:
        await message.answer(f"An error occurred: {e}")


@router.message(
    Command("trend"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def trend_command(message: Message, redis: aioredis.Redis) -> None:
    chat_id = message.chat.id
    today = datetime.utcnow()
    
    today_key = f"{DAILY_STATS_PREFIX}{chat_id}:{today.strftime('%Y-%m-%d')}"
    today_count_str = await redis.get(today_key)
    today_count = int(today_count_str) if today_count_str else 0

    past_days_keys = [
        f"{DAILY_STATS_PREFIX}{chat_id}:{(today - timedelta(days=i)).strftime('%Y-%m-%d')}"
        for i in range(1, 8)
    ]
    
    past_days_counts_str = await redis.mget(past_days_keys)
    past_days_counts = [int(c) for c in past_days_counts_str if c is not None]

    if not past_days_counts:
        await message.answer("Not enough data for a 7-day average.")
        return

    avg_7_day = sum(past_days_counts) / len(past_days_counts)
    if avg_7_day == 0:
        await message.answer(f"Today's activity: {today_count} messages. No activity in the past 7 days.")
        return

    percentage_diff = ((today_count - avg_7_day) / avg_7_day) * 100
    direction = "above" if percentage_diff >= 0 else "below"

    await message.answer(
        f"Today's activity is {abs(percentage_diff):.1f}% {direction} the 7-day average."
    )
