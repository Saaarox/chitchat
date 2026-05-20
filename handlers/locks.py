from __future__ import annotations

from aiogram import Bot
from aiogram import F
from aiogram import Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import ChatPermissions
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal
from database.models import Group
from handlers.moderation import require_moderator
from services.group_settings import ensure_group


router = Router(name="locks")

VALID_LOCK_TYPES = {
    "links", "stickers", "gifs", "photos", "videos",
    "voice", "audio", "documents", "polls", "forwards",
}


async def _save_locks(session: AsyncSession, group: Group, locks: dict) -> None:
    settings = dict(group.settings)
    settings["locks"] = locks
    group.settings = settings
    await session.commit()


@router.message(Command("lock"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def lock_command(message: Message, command: CommandObject, bot: Bot) -> None:
    if not await require_moderator(message, bot):
        return
    type_str = (command.args or "").strip().lower()
    if not type_str or (type_str != "all" and type_str not in VALID_LOCK_TYPES):
        await message.answer(
            f"Usage: /lock [type]\nValid types: all, {', '.join(sorted(VALID_LOCK_TYPES))}"
        )
        return
    async with AsyncSessionLocal() as session:
        group = await ensure_group(session, message.chat.id, message.chat.title)
        locks = dict(group.settings.get("locks", {}))
        if type_str == "all":
            for t in VALID_LOCK_TYPES:
                locks[t] = True
        else:
            locks[type_str] = True
        await _save_locks(session, group, locks)
    await message.answer(f"🔒 <b>{type_str.capitalize()}</b> locked.")


@router.message(Command("unlock"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def unlock_command(message: Message, command: CommandObject, bot: Bot) -> None:
    if not await require_moderator(message, bot):
        return
    type_str = (command.args or "").strip().lower()
    if not type_str or (type_str != "all" and type_str not in VALID_LOCK_TYPES):
        await message.answer(
            f"Usage: /unlock [type]\nValid types: all, {', '.join(sorted(VALID_LOCK_TYPES))}"
        )
        return
    async with AsyncSessionLocal() as session:
        group = await ensure_group(session, message.chat.id, message.chat.title)
        locks = dict(group.settings.get("locks", {}))
        if type_str == "all":
            for t in VALID_LOCK_TYPES:
                locks[t] = False
        else:
            locks[type_str] = False
        await _save_locks(session, group, locks)
    await message.answer(f"🔓 <b>{type_str.capitalize()}</b> unlocked.")


@router.message(Command("lockall"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def lockall_command(message: Message, bot: Bot) -> None:
    if not await require_moderator(message, bot):
        return
    async with AsyncSessionLocal() as session:
        group = await ensure_group(session, message.chat.id, message.chat.title)
        locks = {t: True for t in VALID_LOCK_TYPES}
        await _save_locks(session, group, locks)
    try:
        await bot.set_chat_permissions(
            message.chat.id,
            permissions=ChatPermissions(
                can_send_messages=False,
                can_send_audios=False,
                can_send_documents=False,
                can_send_photos=False,
                can_send_videos=False,
                can_send_other_messages=False,
                can_add_web_page_previews=False,
                can_invite_users=False,
            ),
        )
    except Exception as exc:
        await message.answer(f"Warning: could not set Telegram permissions: {exc}")
    await message.answer("🔒 All message types locked.")


@router.message(Command("locklist"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def locklist_command(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        group = await ensure_group(session, message.chat.id, message.chat.title)
    locks = group.settings.get("locks", {})
    lines = ["<b>Lock Status</b>"]
    for t in sorted(VALID_LOCK_TYPES):
        icon = "🔒" if locks.get(t) else "🔓"
        status = "ON" if locks.get(t) else "OFF"
        lines.append(f"{icon} {t.capitalize()}: {status}")
    await message.answer("\n".join(lines))