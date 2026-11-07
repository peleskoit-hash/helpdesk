from datetime import datetime, time
from zoneinfo import ZoneInfo


MOSCOW = ZoneInfo("Europe/Moscow")


def is_telegram_intake_open(at: datetime | None = None) -> bool:
    """Return whether Telegram intake is open at an aware datetime."""
    if at is None:
        at = datetime.now(MOSCOW)
    elif at.utcoffset() is None:
        raise ValueError("at must be a timezone-aware datetime")
    local = at.astimezone(MOSCOW)
    return local.weekday() < 6 and time(7, 50) <= local.time() < time(19)
