from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.types import ChatPermissions
from aiogram.types import User as TelegramUser
from sqlalchemy import delete
from sqlalchemy import desc
from sqlalchemy import func
from sqlalchemy import or_
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import GroupMember
from database.models import GroupMemberRole
from database.models import User
from database.models import Warning
from services.group_settings import WarnAction
from services.group_settings import WarnSettings


MODERATOR_ROLES: frozenset[GroupMemberRole] = frozenset(
    {
        GroupMemberRole.OWNER,
        GroupMemberRole.ADMIN,
        GroupMemberRole.MOD,
    }
)
PROTECTED_ROLES: frozenset[GroupMemberRole] = frozenset(
    {
        GroupMemberRole.OWNER,
        GroupMemberRole.ADMIN,
        GroupMemberRole.TRUSTED,
    }
)


@dataclass(frozen=True, slots=True)
class WarnSubject:
    user_id: int
    username: str | None
    first_name: str


@dataclass(frozen=True, slots=True)
class WarnHistoryEntry:
    id: int
    reason: str
    created_at: datetime
    expires_at: datetime | None
    given_by: int
    active: bool


@dataclass(frozen=True, slots=True)
class WarnActionResult:
    action: WarnAction
    description: str


@dataclass(frozen=True, slots=True)
class AddWarnResult:
    warning: Warning
    active_count: int
    auto_action: WarnActionResult | None


@dataclass(frozen=True, slots=True)
class RemoveWarnResult:
    removed_warning: Warning
    active_count: int


@dataclass(frozen=True, slots=True)
class ResetWarnResult:
    removed_count: int


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def format_subject(subject: WarnSubject) -> str:
    if subject.username:
        return f"@{subject.username}"
    return f"{subject.first_name} ({subject.user_id})"


async def ensure_db_user_from_telegram(
    session: AsyncSession,
    user: TelegramUser,
) -> User:
    db_user = await session.get(User, user.id)
    if db_user is None:
        db_user = User(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
        )
        session.add(db_user)
        await session.flush()
        return db_user

    db_user.username = user.username
    db_user.first_name = user.first_name
    await session.flush()
    return db_user


def subject_from_db_user(user: User) -> WarnSubject:
    return WarnSubject(
        user_id=user.user_id,
        username=user.username,
        first_name=user.first_name or user.username or str(user.user_id),
    )


async def ensure_db_user(
    session: AsyncSession,
    user_id: int,
    username: str | None,
    first_name: str,
) -> User:
    db_user = await session.get(User, user_id)
    if db_user is None:
        db_user = User(
            user_id=user_id,
            username=username,
            first_name=first_name,
        )
        session.add(db_user)
        await session.flush()
        return db_user

    db_user.username = username
    db_user.first_name = first_name
    await session.flush()
    return db_user


async def get_user_by_username(
    session: AsyncSession,
    username: str,
) -> User | None:
    normalized = username.lstrip("@").strip().lower()
    if not normalized:
        return None

    result = await session.execute(
        select(User).where(func.lower(User.username) == normalized)
    )
    return result.scalar_one_or_none()


async def get_local_role(
    session: AsyncSession,
    group_id: int,
    user_id: int,
) -> GroupMemberRole | None:
    membership = await session.get(GroupMember, (group_id, user_id))
    return membership.role if membership is not None else None


async def is_group_moderator(
    session: AsyncSession,
    bot: Bot,
    group_id: int,
    user_id: int,
) -> bool:
    local_role = await get_local_role(session, group_id, user_id)
    if local_role in MODERATOR_ROLES:
        return True

    member = await bot.get_chat_member(group_id, user_id)
    return member.status in {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}


async def is_protected_user(
    session: AsyncSession,
    bot: Bot,
    group_id: int,
    user_id: int,
) -> bool:
    local_role = await get_local_role(session, group_id, user_id)
    if local_role in PROTECTED_ROLES:
        return True

    member = await bot.get_chat_member(group_id, user_id)
    return member.status in {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}


def build_warn_expiry(expiry_days: int) -> datetime:
    return now_utc() + timedelta(days=expiry_days)


def is_warning_active(warning: Warning, reference_time: datetime | None = None) -> bool:
    current_time = reference_time or now_utc()
    return warning.expires_at is None or warning.expires_at > current_time


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


async def sync_user_warning_summary(session: AsyncSession, user_id: int) -> None:
    current_time = now_utc()
    total_count_result = await session.execute(
        select(func.count(Warning.id)).where(
            Warning.user_id == user_id,
            or_(Warning.expires_at.is_(None), Warning.expires_at > current_time),
        )
    )
    latest_expiry_result = await session.execute(
        select(func.max(Warning.expires_at)).where(
            Warning.user_id == user_id,
            or_(Warning.expires_at.is_(None), Warning.expires_at > current_time),
        )
    )
    user = await session.get(User, user_id)
    if user is None:
        return

    user.warn_count = int(total_count_result.scalar_one() or 0)
    user.warn_expiry = latest_expiry_result.scalar_one()
    await session.flush()


async def count_active_warnings(
    session: AsyncSession,
    group_id: int,
    user_id: int,
) -> int:
    current_time = now_utc()
    result = await session.execute(
        select(func.count(Warning.id)).where(
            Warning.group_id == group_id,
            Warning.user_id == user_id,
            or_(Warning.expires_at.is_(None), Warning.expires_at > current_time),
        )
    )
    return int(result.scalar_one() or 0)


async def get_warn_history(
    session: AsyncSession,
    group_id: int,
    user_id: int,
) -> list[WarnHistoryEntry]:
    current_time = now_utc()
    result = await session.execute(
        select(Warning)
        .where(Warning.group_id == group_id, Warning.user_id == user_id)
        .order_by(desc(Warning.created_at))
    )
    warnings = result.scalars().all()
    return [
        WarnHistoryEntry(
            id=warning.id,
            reason=warning.reason,
            created_at=warning.created_at,
            expires_at=warning.expires_at,
            given_by=warning.given_by,
            active=is_warning_active(warning, current_time),
        )
        for warning in warnings
    ]


async def add_warning(
    session: AsyncSession,
    group_id: int,
    subject: WarnSubject,
    issuer: WarnSubject,
    reason: str,
    warn_settings: WarnSettings,
) -> AddWarnResult:
    warning = Warning(
        user_id=subject.user_id,
        group_id=group_id,
        reason=reason,
        given_by=issuer.user_id,
        expires_at=build_warn_expiry(warn_settings.expiry_days),
    )
    session.add(warning)
    await session.flush()

    await sync_user_warning_summary(session, subject.user_id)
    active_count = await count_active_warnings(session, group_id, subject.user_id)

    return AddWarnResult(
        warning=warning,
        active_count=active_count,
        auto_action=None,
    )


async def remove_latest_active_warning(
    session: AsyncSession,
    group_id: int,
    user_id: int,
) -> RemoveWarnResult | None:
    current_time = now_utc()
    result = await session.execute(
        select(Warning)
        .where(
            Warning.group_id == group_id,
            Warning.user_id == user_id,
            or_(Warning.expires_at.is_(None), Warning.expires_at > current_time),
        )
        .order_by(desc(Warning.created_at), desc(Warning.id))
        .limit(1)
    )
    warning = result.scalar_one_or_none()
    if warning is None:
        return None

    await session.delete(warning)
    await session.flush()
    await sync_user_warning_summary(session, user_id)
    active_count = await count_active_warnings(session, group_id, user_id)
    return RemoveWarnResult(removed_warning=warning, active_count=active_count)


async def reset_warnings(
    session: AsyncSession,
    group_id: int,
    user_id: int,
) -> ResetWarnResult:
    result = await session.execute(
        delete(Warning).where(Warning.group_id == group_id, Warning.user_id == user_id)
    )
    removed_count = int(result.rowcount or 0)
    await sync_user_warning_summary(session, user_id)
    return ResetWarnResult(removed_count=removed_count)


async def execute_warn_threshold_action(
    bot: Bot,
    group_id: int,
    subject: WarnSubject,
    warn_settings: WarnSettings,
) -> WarnActionResult:
    if warn_settings.action == WarnAction.MUTE:
        until_date = now_utc() + timedelta(seconds=warn_settings.mute_seconds)
        await bot.restrict_chat_member(
            chat_id=group_id,
            user_id=subject.user_id,
            permissions=build_restricted_permissions(),
            use_independent_chat_permissions=True,
            until_date=until_date,
        )
        return WarnActionResult(
            action=WarnAction.MUTE,
            description=f"muted for {warn_settings.mute_seconds} seconds",
        )

    if warn_settings.action == WarnAction.BAN:
        await bot.ban_chat_member(chat_id=group_id, user_id=subject.user_id)
        return WarnActionResult(
            action=WarnAction.BAN,
            description="banned from the group",
        )

    await bot.ban_chat_member(chat_id=group_id, user_id=subject.user_id)
    await bot.unban_chat_member(
        chat_id=group_id,
        user_id=subject.user_id,
        only_if_banned=True,
    )
    return WarnActionResult(
        action=WarnAction.KICK,
        description="kicked from the group",
    )
