from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Group
from database.models import GroupMember
from database.models import GroupMemberRole


class SettingsSection(str, Enum):
    MODERATION = "moderation"
    ANTI_SPAM = "anti_spam"
    ANTI_FLOOD = "anti_flood"
    CAPTCHA = "captcha"
    LINKS = "links"
    MEDIA = "media"
    WELCOME = "welcome"
    NIGHT_MODE = "night_mode"
    ROLES = "roles"
    ANALYTICS = "analytics"


class FloodAction(str, Enum):
    MUTE = "mute"
    DELETE = "delete"
    WARN = "warn"


class WarnAction(str, Enum):
    MUTE = "mute"
    BAN = "ban"
    KICK = "kick"


@dataclass(frozen=True, slots=True)
class FloodSettings:
    enabled: bool
    max_messages: int
    window_seconds: int
    action: FloodAction
    mute_seconds: int


@dataclass(frozen=True, slots=True)
class WarnSettings:
    max_warns: int
    expiry_days: int
    action: WarnAction
    mute_seconds: int


SETTINGS_MENU_ORDER: tuple[SettingsSection, ...] = (
    SettingsSection.MODERATION,
    SettingsSection.ANTI_SPAM,
    SettingsSection.ANTI_FLOOD,
    SettingsSection.CAPTCHA,
    SettingsSection.LINKS,
    SettingsSection.MEDIA,
    SettingsSection.WELCOME,
    SettingsSection.NIGHT_MODE,
    SettingsSection.ROLES,
    SettingsSection.ANALYTICS,
)

SETTINGS_LABELS: dict[SettingsSection, str] = {
    SettingsSection.MODERATION: "Moderation",
    SettingsSection.ANTI_SPAM: "Anti-Spam",
    SettingsSection.ANTI_FLOOD: "Anti-Flood",
    SettingsSection.CAPTCHA: "Captcha",
    SettingsSection.LINKS: "Links",
    SettingsSection.MEDIA: "Media",
    SettingsSection.WELCOME: "Welcome",
    SettingsSection.NIGHT_MODE: "Night Mode",
    SettingsSection.ROLES: "Roles",
    SettingsSection.ANALYTICS: "Analytics",
}

DEFAULT_ANTI_FLOOD_LIMIT = 5
DEFAULT_ANTI_FLOOD_WINDOW_SECONDS = 10
DEFAULT_ANTI_FLOOD_ACTION = FloodAction.DELETE.value
DEFAULT_ANTI_FLOOD_MUTE_SECONDS = 300
DEFAULT_MAX_WARNS = 3
DEFAULT_WARN_EXPIRY_DAYS = 7
DEFAULT_WARN_ACTION = WarnAction.MUTE.value
DEFAULT_WARN_MUTE_SECONDS = 86400
EXEMPT_GROUP_ROLES: frozenset[GroupMemberRole] = frozenset(
    {
        GroupMemberRole.OWNER,
        GroupMemberRole.ADMIN,
        GroupMemberRole.TRUSTED,
    }
)


def build_default_group_settings() -> dict[str, Any]:
    defaults: dict[str, Any] = {section.value: False for section in SETTINGS_MENU_ORDER}
    defaults.update(
        {
            "anti_flood_limit": DEFAULT_ANTI_FLOOD_LIMIT,
            "anti_flood_window_seconds": DEFAULT_ANTI_FLOOD_WINDOW_SECONDS,
            "anti_flood_action": DEFAULT_ANTI_FLOOD_ACTION,
            "anti_flood_mute_seconds": DEFAULT_ANTI_FLOOD_MUTE_SECONDS,
            "max_warns": DEFAULT_MAX_WARNS,
            "warn_expiry_days": DEFAULT_WARN_EXPIRY_DAYS,
            "warn_action": DEFAULT_WARN_ACTION,
            "warn_mute_seconds": DEFAULT_WARN_MUTE_SECONDS,
        }
    )
    return defaults


def _coerce_positive_int(value: object, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback

    return parsed if parsed > 0 else fallback


def normalize_group_settings(raw_settings: object) -> dict[str, Any]:
    normalized = dict(raw_settings) if isinstance(raw_settings, dict) else {}
    defaults = build_default_group_settings()

    for key, value in defaults.items():
        normalized.setdefault(key, value)

    if not isinstance(raw_settings, dict):
        return normalized

    for section in SETTINGS_MENU_ORDER:
        normalized[section.value] = bool(normalized.get(section.value, False))

    normalized["anti_flood_limit"] = _coerce_positive_int(
        normalized.get("anti_flood_limit"),
        DEFAULT_ANTI_FLOOD_LIMIT,
    )
    normalized["anti_flood_window_seconds"] = _coerce_positive_int(
        normalized.get("anti_flood_window_seconds"),
        DEFAULT_ANTI_FLOOD_WINDOW_SECONDS,
    )
    normalized["anti_flood_mute_seconds"] = _coerce_positive_int(
        normalized.get("anti_flood_mute_seconds"),
        DEFAULT_ANTI_FLOOD_MUTE_SECONDS,
    )
    normalized["max_warns"] = _coerce_positive_int(
        normalized.get("max_warns"),
        DEFAULT_MAX_WARNS,
    )
    normalized["warn_expiry_days"] = _coerce_positive_int(
        normalized.get("warn_expiry_days"),
        DEFAULT_WARN_EXPIRY_DAYS,
    )
    normalized["warn_mute_seconds"] = _coerce_positive_int(
        normalized.get("warn_mute_seconds"),
        DEFAULT_WARN_MUTE_SECONDS,
    )

    action_value = str(normalized.get("anti_flood_action", DEFAULT_ANTI_FLOOD_ACTION)).lower()
    if action_value not in {action.value for action in FloodAction}:
        action_value = DEFAULT_ANTI_FLOOD_ACTION
    normalized["anti_flood_action"] = action_value

    warn_action_value = str(normalized.get("warn_action", DEFAULT_WARN_ACTION)).lower()
    if warn_action_value not in {action.value for action in WarnAction}:
        warn_action_value = DEFAULT_WARN_ACTION
    normalized["warn_action"] = warn_action_value

    return normalized


def format_status(enabled: bool) -> str:
    return "ON" if enabled else "OFF"


async def ensure_group(
    session: AsyncSession,
    chat_id: int,
    title: str | None,
) -> Group:
    group = await session.get(Group, chat_id)
    group_title = title or f"Group {chat_id}"

    if group is None:
        group = Group(
            chat_id=chat_id,
            title=group_title,
            settings=build_default_group_settings(),
        )
        session.add(group)
        await session.commit()
        return group

    needs_commit = False
    normalized_settings = normalize_group_settings(group.settings)

    if group.title != group_title:
        group.title = group_title
        needs_commit = True

    if group.settings != normalized_settings:
        group.settings = normalized_settings
        needs_commit = True

    if needs_commit:
        await session.commit()

    return group


async def get_group_statuses(
    session: AsyncSession,
    chat_id: int,
    title: str | None,
) -> dict[SettingsSection, bool]:
    group = await ensure_group(session, chat_id=chat_id, title=title)
    settings_map = normalize_group_settings(group.settings)
    return {section: settings_map[section.value] for section in SETTINGS_MENU_ORDER}


async def get_section_status(
    session: AsyncSession,
    chat_id: int,
    title: str | None,
    section: SettingsSection,
) -> bool:
    statuses = await get_group_statuses(session, chat_id=chat_id, title=title)
    return statuses[section]


async def toggle_section_status(
    session: AsyncSession,
    chat_id: int,
    title: str | None,
    section: SettingsSection,
) -> bool:
    group = await ensure_group(session, chat_id=chat_id, title=title)
    settings_map = normalize_group_settings(group.settings)
    new_status = not settings_map[section.value]
    settings_map[section.value] = new_status
    group.settings = settings_map
    await session.commit()
    return new_status


async def get_flood_settings(
    session: AsyncSession,
    chat_id: int,
    title: str | None,
) -> FloodSettings:
    group = await ensure_group(session, chat_id=chat_id, title=title)
    settings_map = normalize_group_settings(group.settings)
    return FloodSettings(
        enabled=bool(settings_map[SettingsSection.ANTI_FLOOD.value]),
        max_messages=int(settings_map["anti_flood_limit"]),
        window_seconds=int(settings_map["anti_flood_window_seconds"]),
        action=FloodAction(str(settings_map["anti_flood_action"])),
        mute_seconds=int(settings_map["anti_flood_mute_seconds"]),
    )


async def get_warn_settings(
    session: AsyncSession,
    chat_id: int,
    title: str | None,
) -> WarnSettings:
    group = await ensure_group(session, chat_id=chat_id, title=title)
    settings_map = normalize_group_settings(group.settings)
    return WarnSettings(
        max_warns=int(settings_map["max_warns"]),
        expiry_days=int(settings_map["warn_expiry_days"]),
        action=WarnAction(str(settings_map["warn_action"])),
        mute_seconds=int(settings_map["warn_mute_seconds"]),
    )


async def is_exempt_group_member(
    session: AsyncSession,
    group_id: int,
    user_id: int,
) -> bool:
    membership = await session.get(GroupMember, (group_id, user_id))
    if membership is None:
        return False

    return membership.role in EXEMPT_GROUP_ROLES
