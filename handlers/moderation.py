from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any

from aiogram import Bot
from aiogram import F
from aiogram import Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import ChatPermissions
from aiogram.types import Message

from database import AsyncSessionLocal
from services.group_settings import get_warn_settings
from services.scheduler import schedule_temp_ban, schedule_temp_mute
from services.log_channel import log_to_group_channel
from services.warns import WarnActionResult
from services.warns import WarnSubject
from services.warns import add_warning
from services.warns import count_active_warnings
from services.warns import ensure_db_user_from_telegram
from services.warns import execute_warn_threshold_action
from services.warns import format_subject
from services.warns import get_user_by_username
from services.warns import get_warn_history
from services.warns import is_group_moderator
from services.warns import is_protected_user
from services.warns import remove_latest_active_warning
from services.warns import reset_warnings
import redis.asyncio as aioredis
from services.warns import subject_from_db_user
from utils.helpers import chunk_list
from utils.time_parser import parse_duration


router = Router(name="moderation")

HISTORY_PREVIEW_LIMIT = 10


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    subject: WarnSubject


@dataclass(frozen=True, slots=True)
class ParsedModerationArgs:
    duration: timedelta | None
    duration_text: str | None
    reason: str


def format_timestamp(value: datetime | None) -> str:
    if value is None:
        return "never"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def build_history_text(subject: WarnSubject, entries_count: int, active_count: int, lines: list[str]) -> str:
    header = [
        f"Warnings for {format_subject(subject)}",
        f"Active warnings: {active_count}",
        f"Total history entries: {entries_count}",
    ]
    if not lines:
        return "\n".join(header + ["", "No warnings found for this user in this group."])
    return "\n".join(header + ["", *lines])


def build_log_text(title: str | None, action: str, moderator: WarnSubject, target: WarnSubject, details: list[str]) -> str:
    group_name = title or "Unknown group"
    base_lines = [
        f"[{action}] {group_name}",
        f"Moderator: {format_subject(moderator)}",
        f"Target: {format_subject(target)}",
    ]
    return "\n".join(base_lines + details)


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


def get_post_target_args(message: Message, command: CommandObject) -> str:
    args = (command.args or "").strip()
    if message.reply_to_message and message.reply_to_message.from_user is not None:
        return args

    if not args:
        return ""

    parts = args.split(maxsplit=1)
    if len(parts) < 2:
        return ""

    return parts[1].strip()


def get_reason_from_command(message: Message, command: CommandObject) -> str | None:
    remaining = get_post_target_args(message, command)
    if not remaining:
        if message.reply_to_message and message.reply_to_message.from_user is not None:
            return "No reason provided."
        return None

    return remaining


def get_optional_reason(message: Message, command: CommandObject) -> str:
    return get_post_target_args(message, command) or "No reason provided."


def parse_duration_and_reason(
    message: Message,
    command: CommandObject,
    *,
    duration_required: bool,
) -> tuple[ParsedModerationArgs | None, str | None]:
    remaining = get_post_target_args(message, command)
    if not remaining:
        if duration_required:
            return None, "A duration is required. Use formats like 30s, 5m, 2h, 1d, or 1w."
        return ParsedModerationArgs(duration=None, duration_text=None, reason="No reason provided."), None

    parts = remaining.split(maxsplit=1)
    duration = parse_duration(parts[0])
    if duration is None:
        if duration_required:
            return None, "A valid duration is required. Use formats like 30s, 5m, 2h, 1d, or 1w."
        return ParsedModerationArgs(duration=None, duration_text=None, reason=remaining), None

    reason = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "No reason provided."
    return (
        ParsedModerationArgs(
            duration=duration,
            duration_text=parts[0],
            reason=reason,
        ),
        None,
    )


def build_until_date(duration: timedelta) -> datetime:
    return datetime.now(timezone.utc) + duration


async def require_moderator(
    message: Message,
    bot: Bot,
) -> bool:
    if message.from_user is None:
        return False

    async with AsyncSessionLocal() as session:
        allowed = await is_group_moderator(
            session,
            bot=bot,
            group_id=message.chat.id,
            user_id=message.from_user.id,
        )

    if not allowed:
        await message.answer("Only moderators can use this command.")

    return allowed


async def resolve_target(
    message: Message,
    command: CommandObject,
) -> tuple[ResolvedTarget | None, str | None]:
    async with AsyncSessionLocal() as session:
        if message.reply_to_message and message.reply_to_message.from_user is not None:
            reply_user = message.reply_to_message.from_user
            db_user = await ensure_db_user_from_telegram(session, reply_user)
            await session.commit()
            return (
                ResolvedTarget(
                    subject=subject_from_db_user(db_user),
                ),
                None,
            )

        args = (command.args or "").strip()
        if not args:
            return None, "Reply to a user or pass @username."

        username_token = args.split(maxsplit=1)[0].strip()
        if not username_token.startswith("@"):
            return None, "When not replying, specify the target as @username."

        db_user = await get_user_by_username(session, username_token)
        if db_user is None:
            return None, "That username was not found in the database yet."

        return (
            ResolvedTarget(
                subject=subject_from_db_user(db_user),
            ),
            None,
        )


async def validate_target(
    message: Message,
    bot: Bot,
    target: ResolvedTarget,
) -> bool:
    if message.from_user is None:
        return False

    if target.subject.user_id == message.from_user.id:
        await message.answer("You can't use warn actions on yourself.")
        return False

    async with AsyncSessionLocal() as session:
        protected = await is_protected_user(
            session,
            bot=bot,
            group_id=message.chat.id,
            user_id=target.subject.user_id,
        )

    if protected:
        await message.answer("That user is protected and can't be targeted by warn actions.")
        return False

    return True


async def get_moderator_subject(message: Message) -> WarnSubject:
    if message.from_user is None:
        raise RuntimeError("Message author is required for moderation commands.")

    async with AsyncSessionLocal() as session:
        moderator = await ensure_db_user_from_telegram(session, message.from_user)
        await session.commit()
        return subject_from_db_user(moderator)


async def log_moderation_action(
    message: Message,
    bot: Bot,
    moderator: WarnSubject,
    target: WarnSubject,
    action: str,
    details: list[str],
) -> None:
    async with AsyncSessionLocal() as session:
        await log_to_group_channel(
            session,
            bot=bot,
            group_id=message.chat.id,
            title=message.chat.title,
            text=build_log_text(
                message.chat.title,
                action,
                moderator,
                target,
                details,
            ),
        )


async def safe_telegram_action(
    message: Message,
    coro: Any,
) -> bool:
    try:
        await coro
        return True
    except Exception as exc:
        await message.answer(
            f"Action failed: {exc.__class__.__name__}: {exc}"
        )
        return False


@router.message(
    Command("warn"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def warn_command(
    message: Message,
    bot: Bot,
    command: CommandObject,
) -> None:
    if not await require_moderator(message, bot):
        return

    target, error = await resolve_target(message, command)
    if target is None:
        await message.answer(error or "Reply to a user or pass @username.")
        return

    if not await validate_target(message, bot, target):
        return

    reason = get_reason_from_command(message, command)
    if reason is None:
        await message.answer("Usage: /warn [reply or @user] [reason]")
        return

    moderator = await get_moderator_subject(message)

    async with AsyncSessionLocal() as session:
        warn_settings = await get_warn_settings(
            session,
            chat_id=message.chat.id,
            title=message.chat.title,
        )
        add_result = await add_warning(
            session,
            group_id=message.chat.id,
            subject=target.subject,
            issuer=moderator,
            reason=reason,
            warn_settings=warn_settings,
        )

        auto_action_result: WarnActionResult | None = None
        if add_result.active_count == warn_settings.max_warns:
            try:
                auto_action_result = await execute_warn_threshold_action(
                    bot=bot,
                    group_id=message.chat.id,
                    subject=target.subject,
                    warn_settings=warn_settings,
                )
            except Exception as exc:
                auto_action_result = WarnActionResult(
                    action=warn_settings.action,
                    description=f"failed to execute automatically: {exc.__class__.__name__}",
                )

        await session.commit()

        response_lines = [
            f"Warn added to {format_subject(target.subject)}.",
            f"Reason: {reason}",
            f"Active warnings: {add_result.active_count}/{warn_settings.max_warns}",
            f"Expires: {format_timestamp(add_result.warning.expires_at)}",
        ]
        if auto_action_result is not None:
            response_lines.append(f"Auto-action: {auto_action_result.description}.")

        await message.answer("\n".join(response_lines))

        log_lines = [
            f"Reason: {reason}",
            f"Active warnings: {add_result.active_count}/{warn_settings.max_warns}",
            f"Expires: {format_timestamp(add_result.warning.expires_at)}",
        ]
        if auto_action_result is not None:
            log_lines.append(f"Auto-action: {auto_action_result.description}")

        await log_to_group_channel(
            session,
            bot=bot,
            group_id=message.chat.id,
            title=message.chat.title,
            text=build_log_text(
                message.chat.title,
                "WARN",
                moderator,
                target.subject,
                log_lines,
            ),
        )


@router.message(
    Command("unwarn"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def unwarn_command(
    message: Message,
    bot: Bot,
    command: CommandObject,
) -> None:
    if not await require_moderator(message, bot):
        return

    target, error = await resolve_target(message, command)
    if target is None:
        await message.answer(error or "Reply to a user or pass @username.")
        return

    moderator = await get_moderator_subject(message)

    async with AsyncSessionLocal() as session:
        result = await remove_latest_active_warning(
            session,
            group_id=message.chat.id,
            user_id=target.subject.user_id,
        )
        if result is None:
            await message.answer("That user has no active warnings to remove.")
            return

        await session.commit()

        await message.answer(
            "\n".join(
                [
                    f"Removed one active warning from {format_subject(target.subject)}.",
                    f"Reason removed: {result.removed_warning.reason}",
                    f"Remaining active warnings: {result.active_count}",
                ]
            )
        )

        await log_to_group_channel(
            session,
            bot=bot,
            group_id=message.chat.id,
            title=message.chat.title,
            text=build_log_text(
                message.chat.title,
                "UNWARN",
                moderator,
                target.subject,
                [
                    f"Removed reason: {result.removed_warning.reason}",
                    f"Remaining active warnings: {result.active_count}",
                ],
            ),
        )


@router.message(
    Command("warns"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def warns_command(
    message: Message,
    bot: Bot,
    command: CommandObject,
) -> None:
    if not await require_moderator(message, bot):
        return

    target, error = await resolve_target(message, command)
    if target is None:
        await message.answer(error or "Reply to a user or pass @username.")
        return

    async with AsyncSessionLocal() as session:
        history = await get_warn_history(
            session,
            group_id=message.chat.id,
            user_id=target.subject.user_id,
        )
        active_count = await count_active_warnings(
            session,
            group_id=message.chat.id,
            user_id=target.subject.user_id,
        )

    lines: list[str] = []
    for entry in history[:HISTORY_PREVIEW_LIMIT]:
        status = "ACTIVE" if entry.active else "EXPIRED"
        lines.append(
            "\n".join(
                [
                    f"#{entry.id} {status}",
                    f"Reason: {entry.reason}",
                    f"Created: {format_timestamp(entry.created_at)}",
                    f"Expires: {format_timestamp(entry.expires_at)}",
                    f"Issuer ID: {entry.given_by}",
                ]
            )
        )

    if len(history) > HISTORY_PREVIEW_LIMIT:
        lines.append(f"...and {len(history) - HISTORY_PREVIEW_LIMIT} older entries.")

    await message.answer(
        build_history_text(
            subject=target.subject,
            entries_count=len(history),
            active_count=active_count,
            lines=lines,
        )
    )


@router.message(
    Command("resetwarns"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def resetwarns_command(
    message: Message,
    bot: Bot,
    command: CommandObject,
) -> None:
    if not await require_moderator(message, bot):
        return

    target, error = await resolve_target(message, command)
    if target is None:
        await message.answer(error or "Reply to a user or pass @username.")
        return

    moderator = await get_moderator_subject(message)

    async with AsyncSessionLocal() as session:
        result = await reset_warnings(
            session,
            group_id=message.chat.id,
            user_id=target.subject.user_id,
        )
        await session.commit()

        await message.answer(
            f"Cleared {result.removed_count} warning entries for {format_subject(target.subject)}."
        )

        await log_to_group_channel(
            session,
            bot=bot,
            group_id=message.chat.id,
            title=message.chat.title,
            text=build_log_text(
                message.chat.title,
                "RESETWARNS",
                moderator,
                target.subject,
                [f"Removed warning entries: {result.removed_count}"],
            ),
        )


@router.message(
    Command("ban"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def ban_command(
    message: Message,
    bot: Bot,
    command: CommandObject,
) -> None:
    if not await require_moderator(message, bot):
        return

    target, error = await resolve_target(message, command)
    if target is None:
        await message.answer(error or "Reply to a user or pass @username.")
        return

    if not await validate_target(message, bot, target):
        return

    reason = get_optional_reason(message, command)
    moderator = await get_moderator_subject(message)

    try:
        await bot.ban_chat_member(
            chat_id=message.chat.id,
            user_id=target.subject.user_id,
        )
    except Exception as exc:
        await message.answer(f"Action failed: {exc.__class__.__name__}: {exc}")
        return

    await message.answer(
        f"{format_subject(target.subject)} has been banned permanently.\n"
        f"Reason: {reason}"
    )

    await log_moderation_action(
        message,
        bot,
        moderator,
        target.subject,
        "BAN",
        [
            "Action: permanent ban",
            f"Reason: {reason}",
        ],
    )


@router.message(
    Command("unban"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def unban_command(
    message: Message,
    bot: Bot,
    command: CommandObject,
) -> None:
    if not await require_moderator(message, bot):
        return

    target, error = await resolve_target(message, command)
    if target is None:
        await message.answer(error or "Reply to a user or pass @username.")
        return

    if not await validate_target(message, bot, target):
        return

    moderator = await get_moderator_subject(message)

    try:
        await bot.unban_chat_member(
            chat_id=message.chat.id,
            user_id=target.subject.user_id,
            only_if_banned=True,
        )
    except Exception as exc:
        await message.answer(f"Action failed: {exc.__class__.__name__}: {exc}")
        return

    await message.answer(f"{format_subject(target.subject)} has been unbanned.")

    await log_moderation_action(
        message,
        bot,
        moderator,
        target.subject,
        "UNBAN",
        ["Action: unban"],
    )


@router.message(
    Command("kick"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def kick_command(
    message: Message,
    bot: Bot,
    command: CommandObject,
) -> None:
    if not await require_moderator(message, bot):
        return

    target, error = await resolve_target(message, command)
    if target is None:
        await message.answer(error or "Reply to a user or pass @username.")
        return

    if not await validate_target(message, bot, target):
        return

    reason = get_optional_reason(message, command)
    moderator = await get_moderator_subject(message)

    try:
        await bot.ban_chat_member(
            chat_id=message.chat.id,
            user_id=target.subject.user_id,
        )
    except Exception as exc:
        await message.answer(f"Ban part of kick failed: {exc.__class__.__name__}: {exc}")
        return

    try:
        await bot.unban_chat_member(
            chat_id=message.chat.id,
            user_id=target.subject.user_id,
            only_if_banned=True,
        )
    except Exception as exc:
        await message.answer(f"Unban part of kick failed: {exc.__class__.__name__}: {exc}")
        return

    await message.answer(
        "\n".join(
            [
                f"{format_subject(target.subject)} has been kicked.",
                f"Reason: {reason}",
            ]
        )
    )

    await log_moderation_action(
        message,
        bot,
        moderator,
        target.subject,
        "KICK",
        [
            "Action: kick (ban then immediate unban)",
            f"Reason: {reason}",
        ],
    )


@router.message(
    Command("mute"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def mute_command(
    message: Message,
    bot: Bot,
    command: CommandObject,
) -> None:
    if not await require_moderator(message, bot):
        return

    target, error = await resolve_target(message, command)
    if target is None:
        await message.answer(error or "Reply to a user or pass @username.")
        return

    if not await validate_target(message, bot, target):
        return

    parsed_args, parse_error = parse_duration_and_reason(
        message,
        command,
        duration_required=False,
    )
    if parsed_args is None:
        await message.answer(parse_error or "Usage: /mute [reply or @user] [duration?] [reason]")
        return

    moderator = await get_moderator_subject(message)
    until_date = build_until_date(parsed_args.duration) if parsed_args.duration is not None else None

    try:
        await bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=target.subject.user_id,
            permissions=build_restricted_permissions(),
            use_independent_chat_permissions=True,
            until_date=until_date,
        )
    except Exception as exc:
        await message.answer(f"Action failed: {exc.__class__.__name__}: {exc}")
        return

    response_lines = [
        f"{format_subject(target.subject)} has been muted.",
        f"Reason: {parsed_args.reason}",
    ]
    log_lines = [f"Reason: {parsed_args.reason}"]
    if until_date is None:
        response_lines.append("Duration: until manually unmuted")
        log_lines.append("Duration: until manually unmuted")
    else:
        response_lines.append(f"Until: {format_timestamp(until_date)}")
        log_lines.append(f"Duration: {parsed_args.duration_text}")
        log_lines.append(f"Until: {format_timestamp(until_date)}")

    await message.answer("\n".join(response_lines))

    await log_moderation_action(
        message,
        bot,
        moderator,
        target.subject,
        "MUTE",
        log_lines,
    )


@router.message(
    Command("unmute"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def unmute_command(
    message: Message,
    bot: Bot,
    command: CommandObject,
) -> None:
    if not await require_moderator(message, bot):
        return

    target, error = await resolve_target(message, command)
    if target is None:
        await message.answer(error or "Reply to a user or pass @username.")
        return

    if not await validate_target(message, bot, target):
        return

    moderator = await get_moderator_subject(message)

    try:
        await bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=target.subject.user_id,
            permissions=build_unrestricted_permissions(),
            use_independent_chat_permissions=True,
        )
    except Exception as exc:
        await message.answer(f"Action failed: {exc.__class__.__name__}: {exc}")
        return

    await message.answer(f"{format_subject(target.subject)} has been unmuted.")

    await log_moderation_action(
        message,
        bot,
        moderator,
        target.subject,
        "UNMUTE",
        ["Action: restored full permissions"],
    )


@router.message(
    Command("tmute"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def tmute_command(
    message: Message,
    bot: Bot,
    command: CommandObject,
    redis: aioredis.Redis,
) -> None:
    if not await require_moderator(message, bot):
        return

    target, error = await resolve_target(message, command)
    if target is None:
        await message.answer(error or "Reply to a user or pass @username.")
        return

    if not await validate_target(message, bot, target):
        return

    parsed_args, parse_error = parse_duration_and_reason(
        message,
        command,
        duration_required=True,
    )
    if parsed_args is None or parsed_args.duration is None:
        await message.answer(parse_error or "Usage: /tmute [reply or @user] [duration] [reason]")
        return

    moderator = await get_moderator_subject(message)
    until_date = build_until_date(parsed_args.duration)

    try:
        await bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=target.subject.user_id,
            permissions=build_restricted_permissions(),
            use_independent_chat_permissions=True,
            until_date=until_date,
        )
    except Exception as exc:
        await message.answer(f"Action failed: {exc.__class__.__name__}: {exc}")
        return

    await schedule_temp_mute(redis, message.chat.id, target.subject.user_id, until_date)


    await message.answer(
        "\n".join(
            [
                f"{format_subject(target.subject)} has been temporarily muted.",
                f"Reason: {parsed_args.reason}",
                f"Until: {format_timestamp(until_date)}",
            ]
        )
    )

    await log_moderation_action(
        message,
        bot,
        moderator,
        target.subject,
        "TMUTE",
        [
            f"Reason: {parsed_args.reason}",
            f"Duration: {parsed_args.duration_text}",
            f"Until: {format_timestamp(until_date)}",
        ],
    )


@router.message(
    Command("tban"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def tban_command(
    message: Message,
    bot: Bot,
    command: CommandObject,
    redis: aioredis.Redis,
) -> None:
    if not await require_moderator(message, bot):
        return

    target, error = await resolve_target(message, command)
    if target is None:
        await message.answer(error or "Reply to a user or pass @username.")
        return

    if not await validate_target(message, bot, target):
        return

    parsed_args, parse_error = parse_duration_and_reason(
        message,
        command,
        duration_required=True,
    )
    if parsed_args is None or parsed_args.duration is None:
        await message.answer(parse_error or "Usage: /tban [reply or @user] [duration] [reason]")
        return

    moderator = await get_moderator_subject(message)
    until_date = build_until_date(parsed_args.duration)

    try:
        await bot.ban_chat_member(
            chat_id=message.chat.id,
            user_id=target.subject.user_id,
            until_date=until_date,
        )
    except Exception as exc:
        await message.answer(f"Action failed: {exc.__class__.__name__}: {exc}")
        return

    await schedule_temp_ban(redis, message.chat.id, target.subject.user_id, until_date)

    await message.answer(
        "\n".join(
            [
                f"{format_subject(target.subject)} has been temporarily banned.",
                f"Reason: {parsed_args.reason}",
                f"Until: {format_timestamp(until_date)}",
            ]
        )
    )

    await log_moderation_action(
        message,
        bot,
        moderator,
        target.subject,
        "TBAN",
        [
            f"Reason: {parsed_args.reason}",
            f"Duration: {parsed_args.duration_text}",
            f"Until: {format_timestamp(until_date)}",
        ],
    )


@router.message(
    Command("del"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def del_command(message: Message, bot: Bot) -> None:
    if not await require_moderator(message, bot):
        return

    if not message.reply_to_message:
        await message.answer("Reply to a message to delete it.")
        return

    try:
        await message.reply_to_message.delete()
    except TelegramAPIError:
        await message.answer("Could not delete that message (it might be too old or I lack permissions).")

    try:
        await message.delete()
    except TelegramAPIError:
        pass


@router.message(
    Command("purge"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def purge_command(message: Message, bot: Bot, command: CommandObject) -> None:
    if not await require_moderator(message, bot):
        return

    try:
        count = int(command.args) if command.args else 10
    except (ValueError, TypeError):
        await message.answer("Usage: /purge [number of messages]")
        return

    if not 1 <= count <= 100:
        await message.answer("Please provide a number between 1 and 100.")
        return

    # We delete from (current - N) up to (current), so N+1 messages total
    to_delete = list(range(message.message_id - count, message.message_id + 1))

    for chunk in chunk_list(to_delete, 100):
        try:
            await bot.delete_messages(message.chat.id, chunk)
        except TelegramAPIError:
            # Some messages might be too old or already deleted
            pass

    # This confirmation message will also be deleted, which is fine.
    # A better approach might be to send a temporary message that self-destructs.
    # For now, this is sufficient.


@router.message(
    Command("pin"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def pin_command(message: Message, bot: Bot) -> None:
    if not await require_moderator(message, bot):
        return

    if not message.reply_to_message:
        await message.answer("Reply to a message to pin it.")
        return

    try:
        await bot.pin_chat_message(
            chat_id=message.chat.id,
            message_id=message.reply_to_message.message_id,
            disable_notification=False,
        )
        moderator = await get_moderator_subject(message)
        # A proper target subject would require fetching the user, but for a pin, this is okay.
        await log_moderation_action(message, bot, moderator, moderator, "PIN", [f"Pinned message ID: {message.reply_to_message.message_id}"])
    except Exception as e:
        await message.answer(f"Could not pin message: {e}")


@router.message(
    Command("silentpin"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def silentpin_command(message: Message, bot: Bot) -> None:
    if not await require_moderator(message, bot):
        return

    if not message.reply_to_message:
        await message.answer("Reply to a message to pin it.")
        return

    try:
        await bot.pin_chat_message(
            chat_id=message.chat.id,
            message_id=message.reply_to_message.message_id,
            disable_notification=True,
        )
    except Exception as e:
        await message.answer(f"Could not pin message: {e}")


@router.message(
    Command("unpin"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def unpin_command(message: Message, bot: Bot) -> None:
    if not await require_moderator(message, bot):
        return

    try:
        if message.reply_to_message:
            await bot.unpin_chat_message(message.chat.id, message.reply_to_message.message_id)
        else:
            await bot.unpin_chat_message(message.chat.id)
    except Exception as e:
        await message.answer(f"Could not unpin message: {e}")
