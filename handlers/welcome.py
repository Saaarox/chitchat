from __future__ import annotations

import redis.asyncio as aioredis
from aiogram import F
from aiogram import Bot
from aiogram import Router
from aiogram.enums import ChatMemberStatus
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import ChatMemberUpdatedFilter
from aiogram.filters import Command
from aiogram.filters import CommandStart
from aiogram.filters.command import CommandObject
from aiogram.types import CallbackQuery
from aiogram.types import ChatMemberUpdated
from aiogram.types import Message
from aiogram.types import User

from keyboards.captcha_kb import CaptchaAnswerCallback
from keyboards.captcha_kb import build_captcha_keyboard
from services.captcha import CaptchaChallenge
from services.captcha import PendingCaptcha
from services.captcha import build_captcha_prompt
from services.captcha import cancel_captcha_timeout
from services.captcha import clear_pending_captcha
from services.captcha import find_pending_captcha_for_user
from services.captcha import generate_math_captcha
from services.captcha import load_pending_captcha
from services.captcha import restrict_member
from services.captcha import schedule_captcha_timeout
from services.captcha import store_pending_captcha
from services.captcha import unrestrict_member
from database import AsyncSessionLocal
from handlers.moderation import require_moderator
from services.group_settings import ensure_group


router = Router(name="welcome")


def get_callback_message(callback: CallbackQuery) -> Message | None:
    return callback.message if isinstance(callback.message, Message) else None


def build_pending_captcha(
    member: User,
    message: Message,
) -> tuple[PendingCaptcha, CaptchaChallenge]:
    challenge = generate_math_captcha()
    pending = PendingCaptcha(
        chat_id=message.chat.id,
        user_id=member.id,
        group_title=message.chat.title,
        full_name=member.full_name,
        question=challenge.question,
        correct_answer=challenge.correct_answer,
        options=challenge.options,
    )
    return pending, challenge


@router.message(
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
    F.new_chat_members,
)
async def handle_new_members(
    message: Message,
    bot: Bot,
    redis: aioredis.Redis,
) -> None:
    if not message.new_chat_members:
        return

    # Part 1: CAPTCHA
    for member in message.new_chat_members:
        if member.is_bot:
            continue

        pending, challenge = build_pending_captcha(member, message)

        await restrict_member(bot, message.chat.id, member.id)
        await store_pending_captcha(redis, pending)
        schedule_captcha_timeout(redis, bot, pending)

        try:
            await bot.send_message(
                chat_id=member.id,
                text=build_captcha_prompt(pending),
                reply_markup=build_captcha_keyboard(
                    chat_id=message.chat.id,
                    user_id=member.id,
                    options=challenge.options,
                ),
            )
        except TelegramForbiddenError:
            await message.answer(
                f"{member.full_name}, I couldn't send your captcha in private. "
                "Start the bot in a private chat and send /start within 60 seconds or you will be removed."
            )
    
    # Part 2: Welcome Message (sent after captcha is solved, but for simplicity here we send it on join)
    # A better implementation would be to send this from `solve_captcha`
    async with AsyncSessionLocal() as session:
        group = await ensure_group(session, message.chat.id, message.chat.title)
        welcome_text = group.settings.get("welcome_text")
        welcome_photo = group.settings.get("welcome_photo")

    if welcome_text:
        # This part will run for the list of new members.
        # We'll format for the first new member for simplicity.
        member = message.new_chat_members[0]
        
        count = await bot.get_chat_member_count(message.chat.id)
        
        formatted_text = welcome_text.format(
            name=member.full_name,
            username=f"@{member.username}" if member.username else member.full_name,
            group=message.chat.title or "this group",
            count=count,
        )

        last_welcome_key = f"guardbot:last_welcome:{message.chat.id}"
        old_msg_id = await redis.get(last_welcome_key)
        if old_msg_id:
            try:
                await bot.delete_message(message.chat.id, int(old_msg_id))
            except Exception:
                pass # Ignore if message not found

        if welcome_photo:
            sent_message = await bot.send_photo(message.chat.id, photo=welcome_photo, caption=formatted_text)
        else:
            sent_message = await bot.send_message(message.chat.id, text=formatted_text)
        
        await redis.set(last_welcome_key, sent_message.message_id, ex=86400) # 24h expiry


@router.callback_query(CaptchaAnswerCallback.filter())
async def solve_captcha(
    callback: CallbackQuery,
    callback_data: CaptchaAnswerCallback,
    bot: Bot,
    redis: aioredis.Redis,
) -> None:
    if callback.from_user.id != callback_data.user_id:
        await callback.answer("This captcha is not for you.", show_alert=True)
        return

    pending = await load_pending_captcha(
        redis,
        chat_id=callback_data.chat_id,
        user_id=callback_data.user_id,
    )
    if pending is None:
        await callback.answer("This captcha expired or was already completed.", show_alert=True)
        return

    if callback_data.answer != pending.correct_answer:
        await callback.answer("Wrong answer. Try again before the timer ends.", show_alert=True)
        return

    await clear_pending_captcha(redis, pending.chat_id, pending.user_id)
    cancel_captcha_timeout(pending.chat_id, pending.user_id)
    await unrestrict_member(bot, pending.chat_id, pending.user_id)
    await bot.send_message(pending.chat_id, f"Welcome, {pending.full_name}!")

    message = get_callback_message(callback)
    if message is not None:
        await message.edit_text("Captcha solved. You're now verified and can chat.")

    await callback.answer("Verification complete.")


@router.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
async def private_captcha_start(
    message: Message,
    redis: aioredis.Redis,
) -> None:
    pending = await find_pending_captcha_for_user(redis, message.from_user.id)
    if pending is None:
        await message.answer("No active captcha challenge was found for your account.")
        return

    await message.answer(
        build_captcha_prompt(pending),
        reply_markup=build_captcha_keyboard(
            chat_id=pending.chat_id,
            user_id=pending.user_id,
            options=pending.options,
        ),
    )


@router.message(Command("setwelcome"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def set_welcome_command(message: Message, command: CommandObject, bot: Bot) -> None:
    if not await require_moderator(message, bot):
        return

    text = command.args
    photo_id = None

    if message.reply_to_message and message.reply_to_message.photo:
        photo_id = message.reply_to_message.photo[-1].file_id
        if not text: # Allow setting welcome from reply caption
            text = message.reply_to_message.caption

    async with AsyncSessionLocal() as session:
        group = await ensure_group(session, message.chat.id, message.chat.title)
        settings = group.settings.copy()

        if text and text.lower() == "off":
            settings["welcome_text"] = None
            settings["welcome_photo"] = None
        else:
            settings["welcome_text"] = text
            if photo_id:
                settings["welcome_photo"] = photo_id
        
        group.settings = settings
        await session.commit()

    await message.answer("Welcome message updated.")


@router.message(Command("setgoodbye"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def set_goodbye_command(message: Message, command: CommandObject, bot: Bot) -> None:
    if not await require_moderator(message, bot):
        return

    text = command.args

    async with AsyncSessionLocal() as session:
        group = await ensure_group(session, message.chat.id, message.chat.title)
        settings = group.settings.copy()

        if text and text.lower() == "off":
            settings["goodbye_text"] = None
        else:
            settings["goodbye_text"] = text
        
        group.settings = settings
        await session.commit()

    await message.answer("Goodbye message updated.")


@router.chat_member(ChatMemberUpdatedFilter(member_status_changed=(F.status == ChatMemberStatus.LEFT) | (F.status == ChatMemberStatus.KICKED)))
async def on_member_leave(event: ChatMemberUpdated, bot: Bot) -> None:
    async with AsyncSessionLocal() as session:
        group = await ensure_group(session, event.chat.id, event.chat.title)
        goodbye_text = group.settings.get("goodbye_text")

    if goodbye_text:
        member = event.old_chat_member.user
        formatted_text = goodbye_text.format(
            name=member.full_name,
            username=f"@{member.username}" if member.username else member.full_name,
            group=event.chat.title or "this group",
        )
        try:
            await bot.send_message(event.chat.id, formatted_text)
        except Exception:
            pass # Don't crash if we can't send for some reason
