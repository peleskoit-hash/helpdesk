"""Daily support availability reset in the 19:01 Moscow scheduler window."""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from ..db import SessionLocal
from ..models import User
from .statistics_lifecycle import set_user_availability_status


MOSCOW = ZoneInfo("Europe/Moscow")
_last_processed_date: date | None = None


def run_employee_auto_offline_once(*, at: datetime | None = None, session_factory=None) -> int:
    global _last_processed_date
    if at is None:
        at = datetime.now(MOSCOW)
    elif at.utcoffset() is None:
        raise ValueError("at must be a timezone-aware datetime")
    local = at.astimezone(MOSCOW)
    if local.hour != 19 or local.minute != 1 or _last_processed_date == local.date():
        return 0
    session_factory = session_factory or SessionLocal
    # Status history elsewhere uses UTC timestamps, never Moscow wall time.
    now = local.astimezone(timezone.utc).replace(tzinfo=None)
    with session_factory() as db:
        users = db.scalars(select(User).where(
            User.is_active.is_(True), User.role.in_(["agent", "trainee"]), User.availability_status != "offline",
        )).all()
        for user in users:
            set_user_availability_status(db, user, "offline", now=now, changed_by_admin=False)
        db.commit()
    _last_processed_date = local.date()
    return len(users)
