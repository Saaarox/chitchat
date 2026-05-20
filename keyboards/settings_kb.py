from __future__ import annotations

from enum import Enum

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from services.group_settings import SETTINGS_LABELS
from services.group_settings import SETTINGS_MENU_ORDER
from services.group_settings import SettingsSection


class SettingsAction(str, Enum):
    OPEN = "open"
    TOGGLE = "toggle"
    BACK = "back"


class SettingsMenuCallback(CallbackData, prefix="settings"):
    action: SettingsAction
    section: SettingsSection
    chat_id: int


def build_settings_main_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for section in SETTINGS_MENU_ORDER:
        builder.button(
            text=SETTINGS_LABELS[section],
            callback_data=SettingsMenuCallback(
                action=SettingsAction.OPEN,
                section=section,
                chat_id=chat_id,
            ).pack(),
        )

    builder.adjust(2)
    return builder.as_markup()


def build_settings_section_keyboard(
    chat_id: int,
    section: SettingsSection,
    enabled: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"Turn {'OFF' if enabled else 'ON'}",
        callback_data=SettingsMenuCallback(
            action=SettingsAction.TOGGLE,
            section=section,
            chat_id=chat_id,
        ).pack(),
    )
    builder.button(
        text="Back",
        callback_data=SettingsMenuCallback(
            action=SettingsAction.BACK,
            section=section,
            chat_id=chat_id,
        ).pack(),
    )
    builder.adjust(1)
    return builder.as_markup()
