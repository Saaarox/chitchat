from __future__ import annotations

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.enums import ChatType
from aiogram.filters import BaseFilter
from aiogram.types import Message


class IsGroupAdmin(BaseFilter):
    async def __call__(self, message: Message, bot: Bot) -> bool:
        if message.from_user is None:
            return False
        if message.chat.type == ChatType.PRIVATE:
            return False
        try:
            member = await bot.get_chat_member(message.chat.id, message.from_user.id)
            return member.status in {
                ChatMemberStatus.CREATOR,
                ChatMemberStatus.ADMINISTRATOR,
            }
        except Exception:
            return False


class IsPrivateChat(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return message.chat.type == ChatType.PRIVATE


class IsGroupChat(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}
