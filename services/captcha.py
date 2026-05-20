from __future__ import annotations

import asyncio
import json
import random
from dataclasses import asdict
from dataclasses import dataclass

import redis.asyncio as aioredis
from aiogram import Bot
from aiogram.types import ChatPermissions


CAPTCHA_TTL_SECONDS = 60
_timeout_tasks: dict[str, asyncio.Task[None]] = {}


@dataclass(frozen=True, slots=True)
class CaptchaChallenge:
    question: str
    correct_answer: int
    options: tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class PendingCaptcha:
    chat_id: int
    user_id: int
    group_title: str | None
    full_name: str
    question: str
    correct_answer: int
    options: tuple[int, int, int, int]


def build_captcha_key(chat_id: int, user_id: int) -> str:
    return f"captcha:{chat_id}:{user_id}"


def build_captcha_prompt(pending: PendingCaptcha) -> str:
    group_name = pending.group_title or "your group"
    return (
        f"You joined {group_name}.\n\n"
        f"Solve this captcha within {CAPTCHA_TTL_SECONDS} seconds:\n"
        f"{pending.question}"
    )


def generate_math_captcha() -> CaptchaChallenge:
    left = random.randint(1, 9)
    right = random.randint(1, 9)
    correct_answer = left + right

    wrong_answers: set[int] = set()
    while len(wrong_answers) < 3:
        offset = random.randint(-8, 8)
        candidate = correct_answer + offset
        if candidate < 0 or candidate == correct_answer:
            continue
        wrong_answers.add(candidate)

    options = list(wrong_answers)
    options.append(correct_answer)
    random.shuffle(options)

    return CaptchaChallenge(
        question=f"What is {left} + {right}?",
        correct_answer=correct_answer,
        options=(options[0], options[1], options[2], options[3]),
    )


async def store_pending_captcha(
    redis: aioredis.Redis,
    pending: PendingCaptcha,
) -> None:
    key = build_captcha_key(pending.chat_id, pending.user_id)
    await redis.set(
        key,
        json.dumps(asdict(pending)),
        ex=CAPTCHA_TTL_SECONDS,
    )


async def load_pending_captcha(
    redis: aioredis.Redis,
    chat_id: int,
    user_id: int,
) -> PendingCaptcha | None:
    key = build_captcha_key(chat_id, user_id)
    raw_value = await redis.get(key)
    if raw_value is None:
        return None

    if isinstance(raw_value, bytes):
        raw_value = raw_value.decode("utf-8")

    payload = json.loads(raw_value)
    return PendingCaptcha(
        chat_id=int(payload["chat_id"]),
        user_id=int(payload["user_id"]),
        group_title=payload.get("group_title"),
        full_name=str(payload["full_name"]),
        question=str(payload["question"]),
        correct_answer=int(payload["correct_answer"]),
        options=tuple(int(option) for option in payload["options"]),
    )


async def clear_pending_captcha(
    redis: aioredis.Redis,
    chat_id: int,
    user_id: int,
) -> None:
    key = build_captcha_key(chat_id, user_id)
    await redis.delete(key)


async def find_pending_captcha_for_user(
    redis: aioredis.Redis,
    user_id: int,
) -> PendingCaptcha | None:
    pattern = f"captcha:*:{user_id}"
    async for key in redis.scan_iter(match=pattern):
        if isinstance(key, bytes):
            key = key.decode("utf-8")

        raw_value = await redis.get(key)
        if raw_value is None:
            continue

        if isinstance(raw_value, bytes):
            raw_value = raw_value.decode("utf-8")

        payload = json.loads(raw_value)
        return PendingCaptcha(
            chat_id=int(payload["chat_id"]),
            user_id=int(payload["user_id"]),
            group_title=payload.get("group_title"),
            full_name=str(payload["full_name"]),
            question=str(payload["question"]),
            correct_answer=int(payload["correct_answer"]),
            options=tuple(int(option) for option in payload["options"]),
        )

    return None


def cancel_captcha_timeout(chat_id: int, user_id: int) -> None:
    key = build_captcha_key(chat_id, user_id)
    task = _timeout_tasks.pop(key, None)
    if task is not None:
        task.cancel()


def schedule_captcha_timeout(
    redis: aioredis.Redis,
    bot: Bot,
    pending: PendingCaptcha,
) -> None:
    key = build_captcha_key(pending.chat_id, pending.user_id)
    cancel_captcha_timeout(pending.chat_id, pending.user_id)
    _timeout_tasks[key] = asyncio.create_task(
        _run_captcha_timeout(redis=redis, bot=bot, pending=pending, key=key)
    )


async def _run_captcha_timeout(
    redis: aioredis.Redis,
    bot: Bot,
    pending: PendingCaptcha,
    key: str,
) -> None:
    try:
        await asyncio.sleep(CAPTCHA_TTL_SECONDS)
        if not await redis.exists(key):
            return

        await clear_pending_captcha(redis, pending.chat_id, pending.user_id)
        await kick_member(bot, pending.chat_id, pending.user_id)
        await bot.send_message(
            pending.chat_id,
            f"{pending.full_name} did not solve the captcha in time and was removed.",
        )
    except asyncio.CancelledError:
        raise
    finally:
        _timeout_tasks.pop(key, None)


async def restrict_member(bot: Bot, chat_id: int, user_id: int) -> None:
    await bot.restrict_chat_member(
        chat_id=chat_id,
        user_id=user_id,
        permissions=build_restricted_permissions(),
        use_independent_chat_permissions=True,
    )


async def unrestrict_member(bot: Bot, chat_id: int, user_id: int) -> None:
    await bot.restrict_chat_member(
        chat_id=chat_id,
        user_id=user_id,
        permissions=build_unrestricted_permissions(),
        use_independent_chat_permissions=True,
    )


async def kick_member(bot: Bot, chat_id: int, user_id: int) -> None:
    await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
    await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)


def build_restricted_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=False,
        can_send_audios=False,
        can_send_documents=False,
        can_send_photos=False,
        can_send_videos=False,
        can_send_video_notes=False,
        can_send_voice_notes=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
        can_change_info=False,
        can_invite_users=False,
        can_pin_messages=False,
        can_manage_topics=False,
    )


def build_unrestricted_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=True,
        can_send_audios=True,
        can_send_documents=True,
        can_send_photos=True,
        can_send_videos=True,
        can_send_video_notes=True,
        can_send_voice_notes=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
        can_change_info=True,
        can_invite_users=True,
        can_pin_messages=True,
        can_manage_topics=True,
    )
