from __future__ import annotations

from aiogram import Bot
from aiogram import F
from aiogram import Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import Message
from sqlalchemy import select

from database import AsyncSessionLocal
from database.models import GroupMember
from database.models import GroupMemberRole
from handlers.moderation import require_moderator
from services.group_settings import ensure_group
from services.warns import count_active_warnings
from services.warns import ensure_db_user_from_telegram
from services.warns import get_user_by_username


router = Router(name="info")


@router.message(Command("id"))
async def id_command(message: Message) -> None:
    if message.from_user is None:
        return

    if message.reply_to_message and message.reply_to_message.from_user:
        user = message.reply_to_message.from_user
        username = f" (@{user.username})" if user.username else ""
        await message.answer(
            f"User: {user.full_name}{username}\nID: <code>{user.id}</code>"
        )
    elif message.chat.type == ChatType.PRIVATE:
        await message.answer(f"Your ID: <code>{message.from_user.id}</code>")
    else:
        await message.answer(
            f"Your ID: <code>{message.from_user.id}</code>\n"
            f"Chat ID: <code>{message.chat.id}</code>"
        )


@router.message(Command("info"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def info_command(message: Message, command: CommandObject) -> None:
    if message.from_user is None:
        return

    async with AsyncSessionLocal() as session:
        if message.reply_to_message and message.reply_to_message.from_user:
            target_user = await ensure_db_user_from_telegram(
                session, message.reply_to_message.from_user
            )
        elif command.args:
            target_user = await get_user_by_username(session, command.args.split()[0])
        else:
            target_user = await ensure_db_user_from_telegram(session, message.from_user)

        if not target_user:
            await message.answer("User not found.")
            return

        membership = await session.get(GroupMember, (message.chat.id, target_user.user_id))
        role = membership.role if membership else GroupMemberRole.MEMBER
        active_warns = await count_active_warnings(
            session, group_id=message.chat.id, user_id=target_user.user_id
        )

    await message.answer(
        "\n".join([
            "<b>User Info</b>",
            f"ID: <code>{target_user.user_id}</code>",
            f"Name: {target_user.first_name or 'N/A'}",
            f"Username: @{target_user.username}" if target_user.username else "Username: N/A",
            f"Role: {role.value.capitalize()}",
            f"Active Warnings: {active_warns}",
            f"Banned: {'Yes' if target_user.is_banned else 'No'}",
        ])
    )


@router.message(Command("admins"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def admins_command(message: Message, bot: Bot) -> None:
    try:
        admins = await bot.get_chat_administrators(message.chat.id)
        lines = [f"<b>Admins ({len(admins)})</b>"]
        for admin in admins:
            user = admin.user
            status = "Creator" if admin.status.value == "creator" else "Admin"
            username = f" (@{user.username})" if user.username else ""
            lines.append(f"• {user.full_name}{username} — {status}")
        await message.answer("\n".join(lines))
    except Exception as e:
        await message.answer(f"Failed to get admin list: {e}")


@router.message(Command("staff"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def staff_command(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(GroupMember).where(
                GroupMember.group_id == message.chat.id,
                GroupMember.role != GroupMemberRole.MEMBER,
            ).join(GroupMember.user)
        )
        members = result.scalars().all()

    if not members:
        await message.answer("No custom staff roles assigned.")
        return

    lines = ["<b>Custom Staff Roles</b>"]
    for m in members:
        username = f"@{m.user.username}" if m.user.username else f"<code>{m.user_id}</code>"
        lines.append(f"• {m.user.first_name} ({username}) — {m.role.value.capitalize()}")
    await message.answer("\n".join(lines))


@router.message(Command("rules"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def rules_command(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        group = await ensure_group(session, message.chat.id, message.chat.title)
    rules = group.settings.get("rules")
    if not rules:
        await message.answer("No rules have been set for this group.")
    else:
        await message.answer(rules)


@router.message(Command("setrules"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def setrules_command(message: Message, command: CommandObject, bot: Bot) -> None:
    if not await require_moderator(message, bot):
        return
    text = command.args
    if not text:
        await message.answer("Usage: /setrules [rules text]")
        return
    async with AsyncSessionLocal() as session:
        group = await ensure_group(session, message.chat.id, message.chat.title)
        settings = dict(group.settings)
        settings["rules"] = text
        group.settings = settings
        await session.commit()
    await message.answer("Group rules updated.")


@router.message(Command("reload"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def reload_command(message: Message, bot: Bot) -> None:
    if not await require_moderator(message, bot):
        return
    try:
        await bot.get_chat_administrators(message.chat.id)
        await message.answer("Admin list refreshed from Telegram.")
    except Exception as e:
        await message.answer(f"Failed to refresh: {e}")


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    help_text = (
        "<b>GuardBot Commands:</b>\n\n"
        
        "<b>Moderation</b>\n"
        "  <code>/ban</code>, <code>/unban</code>, <code>/kick</code>, <code>/mute</code>, <code>/unmute</code>, <code>/tmute</code>, <code>/tban</code>, <code>/warn</code>, <code>/unwarn</code>, <code>/warns</code>, <code>/resetwarns</code>, <code>/del</code>, <code>/purge</code>, <code>/pin</code>, <code>/silentpin</code>, <code>/unpin</code>\n\n"
        
        "<b>Info</b>\n"
        "  <code>/id</code>, <code>/info</code>, <code>/admins</code>, <code>/staff</code>, <code>/rules</code>, <code>/setrules</code>, <code>/reload</code>\n\n"
        
        "<b>Locks</b>\n"
        "  <code>/lock</code>, <code>/unlock</code>, <code>/lockall</code>, <code>/locklist</code>\n\n"
        
        "<b>Settings</b>\n"
        "  <code>/settings</code>\n\n"
        
        "<b>Welcome</b>\n"
        "  <code>/setwelcome</code>, <code>/setgoodbye</code>\n\n"
        
        "<b>Admin</b>\n"
        "  <code>/setlogchannel</code>, <code>/addrole</code>, <code>/removerole</code>, <code>/listroles</code>\n\n"
        
        "<b>Analytics</b>\n"
        "  <code>/top10</code>, <code>/stat</code>, <code>/trend</code>, <code>/inactives</code>\n\n"
        "For more details on a specific command, refer to the documentation or try the command with no arguments."
    )
    await message.answer(help_text, parse_mode="HTML"
