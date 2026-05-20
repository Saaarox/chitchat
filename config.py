from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


def _get_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _fix_db_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://") and "+asyncpg" not in url:
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    database_url: str = _fix_db_url(
        os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@localhost:5432/guardbot",
        )
    )
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    sql_echo: bool = _get_bool_env("SQL_ECHO", default=False)


settings = Settings()
