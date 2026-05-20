from __future__ import annotations

from aiogram import Bot
from aiogram import F
from aiogram import Router
from aiogram.enums import ChatMemberStatus
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import Message
from sqlalchemy import select

from database import AsyncSessionLocal
from database.models import GroupMember
from database.models import GroupMemberRole
from handlers.moderation import build_log_text
from handlers.moderation import get_moderator_subject
from handlers.moderation import resolve_target
from services.group_settings import ensure_group
from services.log_channel import log_to_group_channel
from services.warns import format_subject

router = Router(name="admin")


async def is_owner_or_admin(message: Message, bot: Bot) -> bool:
    if message.from_user is None:
        return False
    member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    return member.status in {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}


@router.message(
    Command("setlogchannel"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def setlogchannel_command(message: Message, command: CommandObject, bot: Bot) -> None:
    if not await is_owner_or_admin(message, bot):
        await message.answer("Only group owners or admins can use this command.")
        return

    channel_arg = command.args
    if not channel_arg:
        await message.answer("Usage: /setlogchannel [@channel_username or channel_id]")
        return

    try:
        channel = await bot.get_chat(channel_arg)

        bot_member = await bot.get_chat_member(channel.id, bot.id)
        if bot_member.status not in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR} or not bot_member.can_post_messages:
            await message.answer(
                f"I am not an admin in {channel.title} or I don't have permission to post messages there."
            )
            return

        async with AsyncSessionLocal() as session:
            group = await ensure_group(session, message.chat.id, message.chat.title)
            group.log_channel_id = channel.id
            await session.commit()

        await message.answer(f"Log channel set to {channel.title}.")

    except TelegramAPIError as e:
        await message.answer(f"Could not set log channel: {e.message}")
    except Exception as e:
        await message.answer(f"An unexpected error occurred: {e}")


VALID_ROLES = {role.value for role in GroupMemberRole if role not in {GroupMemberRole.OWNER, GroupMemberRole.ADMIN}}


@router.message(
    Command("addrole"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def addrole_command(message: Message, command: CommandObject, bot: Bot) -> None:
    if not await is_owner_or_admin(message, bot):
        await message.answer("Only group owners or admins can use this command.")
        return

    target, error = await resolve_target(message, command)
    if target is None:
        await message.answer(error or "Reply to a user or pass @username.")
        return

    args = (command.args or "").split()
    if len(args) < (1 if message.reply_to_message else 2):
        await message.answer("Usage: /addrole [reply or @user] [role]\nValid roles: mod, cleaner, trusted")
        return

    role_str = args[-1].lower()
    if role_str not in VALID_ROLES:
        await message.answer(f"Invalid role '{role_str}'. Valid roles are: {', '.join(sorted(list(VALID_ROLES)))}")
        return

    role_enum = GroupMemberRole(role_str)

    async with AsyncSessionLocal() as session:
        membership = await session.get(GroupMember, (message.chat.id, target.subject.user_id))
        if membership:
            membership.role = role_enum
        else:
            membership = GroupMember(
                group_id=message.chat.id,
                user_id=target.subject.user_id,
                role=role_enum,
            )
            session.add(membership)
        await session.commit()

        moderator = await get_moderator_subject(message)
        await message.answer(f"Role '{role_str}' assigned to {format_subject(target.subject)}.")

        await log_to_group_channel(
            session,
            bot=bot,
            group_id=message.chat.id,
            title=message.chat.title,
            text=build_log_text(
                message.chat.title,
                "ADDROLE",
                moderator,
                target.subject,
                [f"Assigned role: {role_str}"],
            ),
        )


@router.message(
    Command("removerole"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def removerole_command(message: Message, command: CommandObject, bot: Bot) -> None:
    if not await is_owner_or_admin(message, bot):
        await message.answer("Only group owners or admins can use this command.")
        return

    target, error = await resolve_target(message, command)
    if target is None:
        await message.answer(error or "Reply to a user or pass @username.")
        return

    async with AsyncSessionLocal() as session:
        membership = await session.get(GroupMember, (message.chat.id, target.subject.user_id))
        if not membership or membership.role == GroupMemberRole.MEMBER:
            await message.answer("That user has no custom role.")
            return

        membership.role = GroupMemberRole.MEMBER
        await session.commit()

        moderator = await get_moderator_subject(message)
        await message.answer(f"Custom role removed from {format_subject(target.subject)}.")

        await log_to_group_channel(
            session,
            bot=bot,
            group_id=message.chat.id,
            title=message.chat.title,
            text=build_log_text(
                message.chat.title,
                "REMOVEROLE",
                moderator,
                target.subject,
                ["Role reset to Member"],
            ),
        )


@router.message(
    Command("listroles"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def listroles_command(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        stmt = (
            select(GroupMember)
            .where(GroupMember.group_id == message.chat.id, GroupMember.role != GroupMemberRole.MEMBER)
            .join(GroupMember.user)
        )

        result = await session.execute(stmt)
        staff_members = result.scalars().all()

    if not staff_members:
        await message.answer("No custom roles assigned in this group.")
        return

    staff_lines = ["<b>Custom Roles in this Group</b>", ""]
    for member in staff_members:
        user = member.user
        username = f"(@{user.username})" if user.username else ""
        staff_lines.append(f"• {user.full_name} {username} — {member.role.value.capitalize()}")

    await message.answer("\n".join(staff_lines))
