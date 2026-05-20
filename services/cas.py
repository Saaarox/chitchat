from __future__ import annotations

import logging

import aiohttp

logger = logging.getLogger(__name__)


async def is_cas_banned(user_id: int) -> bool:
    params = {"user_id": str(user_id)}
    timeout = aiohttp.ClientTimeout(total=3)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get("https://api.cas.chat/check", params=params) as response:
                if response.status != 200:
                    return False
                data = await response.json()
                return data.get("ok") is True
    except Exception as e:
        logger.error("CAS check for user %d failed: %s", user_id, e)
        return False