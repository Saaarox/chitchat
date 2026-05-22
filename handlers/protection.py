from __future__ import annotations

import logging
import re

from aiogram import Bot
from aiogram import F
from aiogram import Router
from aiogram.enums import ChatType
from aiogram.types import Message

from database import AsyncSessionLocal
from database.models import User as DbUser
from services.cas import is_cas_banned
from services.group_settings import ensure_group
from services.group_settings import get_warn_settings
from services.group_settings import is_exempt_group_member
from services.log_channel import log_to_group_channel
from services.warns import add_warning
from services.warns import ensure_db_user_from_telegram
from services.warns import execute_warn_threshold_action
from services.warns import subject_from_db_user

logger = logging.getLogger(__name__)
router = Router(name="protection")

URL_PATTERN = re.compile(r"(https?://\S+|t\.me/\S+|@\w+)", re.IGNORECASE)

MEDIA_TYPE_CHECKS = {
    "stickers": lambda m: m.sticker is not None,
    "gifs": lambda m: m.animation is not None,
    "photos": lambda m: m.photo is not None,
    "videos": lambda m: m.video is not None,
    "voice": lambda m: m.voice is not None,
    "audio": lambda m: m.audio is not None,
    "documents": lambda m: m.document is not None,
    "polls": lambda m: m.poll is not None,
    "forwards": lambda m: m.forward_origin is not None,
}


@router.message(
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
    F.new_chat_members,
)
async def cas_check_new_members(message: Message, bot: Bot) -> None:
    for member in message.new_chat_members or []:
        if member.is_bot:
            continue
        try:
            if await is_cas_banned(member.id):
                try:
                    await bot.ban_chat_member(message.chat.id, member.id)
                    await message.answer(
                        f"🚫 {member.full_name} was auto-banned (CAS database match)."
                    )
                    async with AsyncSessionLocal() as session:
                        await log_to_group_channel(
                            session,
                            bot=bot,
                            group_id=message.chat.id,
                            title=message.chat.title,
                            text=(
                                f"[CAS-BAN] {message.chat.title}\n"
                                f"User: {member.full_name} ({member.id})\n"
                                f"Reason: CAS database match"
                            ),
                        )
                except Exception as e:
                    logger.warning("CAS ban failed for %d: %s", member.id, e)
        except Exception as e:
            logger.warning("CAS check failed for %d: %s", member.id, e)


@router.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def protection_handler(message: Message, bot: Bot) -> None:
    if message.from_user is None or message.from_user.is_bot:
        return

    async with AsyncSessionLocal() as session:
        group = await ensure_group(session, message.chat.id, message.chat.title)
        exempt = await is_exempt_group_member(
            session, message.chat.id, message.from_user.id
        )
        if exempt:
            return

        locks = group.settings.get("locks", {})
        banned_words = group.settings.get("banned_words", [])
        max_length = group.settings.get("max_message_length")
        warn_settings = await get_warn_settings(
            session, message.chat.id, message.chat.title
        )

    async def silent_delete() -> None:
        try:
            await message.delete()
        except Exception:
            pass

    async def delete_and_warn(reason: str) -> None:
        await silent_delete()
        bot_me = await bot.get_me()
        async with AsyncSessionLocal() as session:
            bot_user = await ensure_db_user_from_telegram(session, bot_me)
            issuer = subject_from_db_user(bot_user)
            db_user = await session.get(DbUser, message.from_user.id)
            if db_user is None:
                return
            subject = subject_from_db_user(db_user)
            add_result = await add_warning(
                session, message.chat.id, subject, issuer, reason, warn_settings
            )
            if add_result.active_count >= warn_settings.max_warns:
                try:
                    await execute_warn_threshold_action(
                        bot, message.chat.id, subject, warn_settings
                    )
                except Exception as e:
                    logger.warning("Threshold action failed: %s", e)
            await session.commit()

    text = message.text or message.caption or ""

    # 1. LINK PROTECTION
    if group.settings.get("links"): # Check if link protection is enabled
        if locks.get("links") and URL_PATTERN.search(text):
            await delete_and_warn("Link protection triggered.")
            return

    # 2. MEDIA LOCKS
    if group.settings.get("media"): # Check if media locks are enabled
        for media_type, check_fn in MEDIA_TYPE_CHECKS.items():
            if locks.get(media_type) and check_fn(message):
                await silent_delete()
                return

    # 3. BANNED WORDS
    if group.settings.get("anti_spam"): # Check if anti-spam (banned words) is enabled
        if banned_words and text:
            text_lower = text.lower()
            for word in banned_words:
                try:
                    if re.search(word, text_lower, re.IGNORECASE):
                        await delete_and_warn("Banned word filter triggered.")
                        return
                except re.error:
                    # Fallback for simple string match if regex is invalid
                    if word.lower() in text_lower:
                        await delete_and_warn("Banned word filter triggered.")
                        return

    # 4. MAX MESSAGE LENGTH
    if max_length and isinstance(max_length, int) and len(text) > max_length:
        await silent_delete()
        return