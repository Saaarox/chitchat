from __future__ import annotations


def format_user_mention(user_id: int, full_name: str) -> str:
    """Formats a user mention as an HTML link."""
    return f'<a href="tg://user?id={user_id}">{full_name}</a>'


def chunk_list(lst: list, size: int) -> list[list]:
    """Yield successive n-sized chunks from a list."""
    return [lst[i : i + size] for i in range(0, len(lst), size)]


def truncate(text: str, max_len: int = 200) -> str:
    """Truncates a string if it exceeds the maximum length."""
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def safe_username(username: str | None, fallback: str) -> str:
    """Returns @username if it exists, otherwise a fallback string."""
    return f"@{username}" if username else fallback
