from __future__ import annotations

from aiogram import F
from aiogram import Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.fsm.state import StatesGroup
from aiogram.types import CallbackQuery
from aiogram.types import Message

from database import AsyncSessionLocal
from keyboards.settings_kb import SettingsAction
from keyboards.settings_kb import SettingsMenuCallback
from keyboards.settings_kb import build_settings_main_keyboard
from keyboards.settings_kb import build_settings_section_keyboard
from services.group_settings import SETTINGS_LABELS
from services.group_settings import SETTINGS_MENU_ORDER
from services.group_settings import SettingsSection
from services.group_settings import format_status
from services.group_settings import get_group_statuses
from services.group_settings import get_section_status
from services.group_settings import toggle_section_status


router = Router(name="settings")


class SettingsMenuState(StatesGroup):
    browsing = State()


def render_main_menu_text(group_title: str | None, enabled_count: int) -> str:
    return (
        f"Settings for {group_title or 'this group'}\n\n"
        "Choose a section to configure.\n"
        f"Enabled modules: {enabled_count}/{len(SETTINGS_MENU_ORDER)}"
    )


def render_section_text(section: SettingsSection, enabled: bool) -> str:
    label = SETTINGS_LABELS[section]
    return (
        f"{label}\n\n"
        f"Current status: {format_status(enabled)}\n"
        "Use the button below to toggle this module, or go back."
    )


async def show_main_menu(message: Message, state: FSMContext) -> None:
    async with AsyncSessionLocal() as session:
        statuses = await get_group_statuses(
            session,
            chat_id=message.chat.id,
            title=message.chat.title,
        )

    sent_message = await message.answer(
        render_main_menu_text(
            group_title=message.chat.title,
            enabled_count=sum(statuses.values()),
        ),
        reply_markup=build_settings_main_keyboard(message.chat.id),
    )
    await state.set_state(SettingsMenuState.browsing)
    await state.update_data(
        settings_chat_id=message.chat.id,
        settings_message_id=sent_message.message_id,
        current_section="main",
    )


async def edit_main_menu(message: Message, state: FSMContext) -> None:
    async with AsyncSessionLocal() as session:
        statuses = await get_group_statuses(
            session,
            chat_id=message.chat.id,
            title=message.chat.title,
        )

    await message.edit_text(
        render_main_menu_text(
            group_title=message.chat.title,
            enabled_count=sum(statuses.values()),
        ),
        reply_markup=build_settings_main_keyboard(message.chat.id),
    )
    await state.set_state(SettingsMenuState.browsing)
    await state.update_data(
        settings_chat_id=message.chat.id,
        settings_message_id=message.message_id,
        current_section="main",
    )


async def edit_section_menu(
    message: Message,
    state: FSMContext,
    section: SettingsSection,
) -> None:
    async with AsyncSessionLocal() as session:
        enabled = await get_section_status(
            session,
            chat_id=message.chat.id,
            title=message.chat.title,
            section=section,
        )

    await message.edit_text(
        render_section_text(section, enabled),
        reply_markup=build_settings_section_keyboard(
            chat_id=message.chat.id,
            section=section,
            enabled=enabled,
        ),
    )
    await state.set_state(SettingsMenuState.browsing)
    await state.update_data(
        settings_chat_id=message.chat.id,
        settings_message_id=message.message_id,
        current_section=section.value,
    )


def get_callback_message(callback: CallbackQuery) -> Message | None:
    return callback.message if isinstance(callback.message, Message) else None


async def is_active_settings_message(state: FSMContext, message: Message) -> bool:
    data = await state.get_data()
    stored_chat_id = data.get("settings_chat_id")
    stored_message_id = data.get("settings_message_id")

    if stored_chat_id is None or stored_message_id is None:
        return True

    return stored_chat_id == message.chat.id and stored_message_id == message.message_id


@router.message(
    Command("settings"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def settings_command(message: Message, state: FSMContext) -> None:
    await show_main_menu(message, state)


@router.callback_query(SettingsMenuCallback.filter(F.action == SettingsAction.OPEN))
async def open_settings_section(
    callback: CallbackQuery,
    callback_data: SettingsMenuCallback,
    state: FSMContext,
) -> None:
    message = get_callback_message(callback)
    if message is None:
        await callback.answer()
        return

    if message.chat.id != callback_data.chat_id:
        await callback.answer("This settings menu belongs to another chat.", show_alert=True)
        return

    if not await is_active_settings_message(state, message):
        await callback.answer("Open /settings again to continue from your latest menu.", show_alert=True)
        return

    await edit_section_menu(message, state, callback_data.section)
    await callback.answer()


@router.callback_query(SettingsMenuCallback.filter(F.action == SettingsAction.TOGGLE))
async def toggle_settings_section(
    callback: CallbackQuery,
    callback_data: SettingsMenuCallback,
    state: FSMContext,
) -> None:
    message = get_callback_message(callback)
    if message is None:
        await callback.answer()
        return

    if message.chat.id != callback_data.chat_id:
        await callback.answer("This settings menu belongs to another chat.", show_alert=True)
        return

    if not await is_active_settings_message(state, message):
        await callback.answer("Open /settings again to continue from your latest menu.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        enabled = await toggle_section_status(
            session,
            chat_id=message.chat.id,
            title=message.chat.title,
            section=callback_data.section,
        )

    await message.edit_text(
        render_section_text(callback_data.section, enabled),
        reply_markup=build_settings_section_keyboard(
            chat_id=message.chat.id,
            section=callback_data.section,
            enabled=enabled,
        ),
    )
    await state.set_state(SettingsMenuState.browsing)
    await state.update_data(
        settings_chat_id=message.chat.id,
        settings_message_id=message.message_id,
        current_section=callback_data.section.value,
    )
    await callback.answer(
        f"{SETTINGS_LABELS[callback_data.section]} is now {format_status(enabled)}."
    )


@router.callback_query(SettingsMenuCallback.filter(F.action == SettingsAction.BACK))
async def back_to_settings_main(
    callback: CallbackQuery,
    callback_data: SettingsMenuCallback,
    state: FSMContext,
) -> None:
    message = get_callback_message(callback)
    if message is None:
        await callback.answer()
        return

    if message.chat.id != callback_data.chat_id:
        await callback.answer("This settings menu belongs to another chat.", show_alert=True)
        return

    if not await is_active_settings_message(state, message):
        await callback.answer("Open /settings again to continue from your latest menu.", show_alert=True)
        return

    await edit_main_menu(message, state)
    await callback.answer()
