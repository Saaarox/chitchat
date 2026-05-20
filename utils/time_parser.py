from __future__ import annotations

import re
from datetime import timedelta


# This pattern finds all duration components like "1d", "12h", "30m"
_DURATION_PATTERN = re.compile(r"(\d+)\s*([smhdw])", re.IGNORECASE)
_UNIT_SECONDS = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 604800,
}
_MAX_SECONDS = timedelta(days=365).total_seconds()


def parse_duration(text: str) -> timedelta | None:
    """
    Parses a duration string like "1d12h30m" into a timedelta object.
    Returns None if the format is invalid, the duration is zero/negative,
    or it exceeds 365 days.
    """
    text = text.strip().lower()
    if not text:
        return None

    total_seconds = 0
    last_end = 0
    matches_found = 0

    for match in _DURATION_PATTERN.finditer(text):
        # Ensure there are no gaps or invalid characters between matches
        if match.start() != last_end:
            return None

        matches_found += 1
        amount = int(match.group(1))
        unit = match.group(2)  # Already lowercased

        total_seconds += amount * _UNIT_SECONDS[unit]
        last_end = match.end()

    # Ensure the entire string was consumed by the pattern and at least one match was found
    if last_end != len(text) or matches_found == 0:
        return None

    if total_seconds <= 0:
        return None

    if total_seconds > _MAX_SECONDS:
        return None

    return timedelta(seconds=total_seconds)
