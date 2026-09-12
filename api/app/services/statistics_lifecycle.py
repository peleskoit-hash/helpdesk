import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import StatsUserStatusInterval, User


STATUS_HISTORY_ROLES = {"agent", "trainee"}
AVAILABILITY_STATUS_FALLBACK = "online"


def is_status_history_trackable(role: str | None, is_active: bool | None) -> bool:
    return bool(is_active) and role in STATUS_HISTORY_ROLES


def _now(now: datetime | None) -> datetime:
    return now or datetime.utcnow()


def _normalized_status(status: str | None) -> str:
    return (status or "").strip() or AVAILABILITY_STATUS_FALLBACK


def _open_intervals(db: Session, user: User) -> list[StatsUserStatusInterval]:
    return db.execute(
        select(StatsUserStatusInterval).where(
            StatsUserStatusInterval.company_id == user.company_id,
            StatsUserStatusInterval.user_id == user.id,
            StatsUserStatusInterval.ended_at.is_(None),
        )
    ).scalars().all()


def _close_open_intervals(db: Session, user: User, ended_at: datetime) -> None:
    for interval in _open_intervals(db, user):
        interval.ended_at = ended_at


def _open_status_interval(db: Session, user: User, status: str, started_at: datetime) -> None:
    db.add(
        StatsUserStatusInterval(
            id=str(uuid.uuid4()),
            company_id=user.company_id,
            user_id=user.id,
            status=_normalized_status(status),
            started_at=started_at,
            ended_at=None,
            created_at=started_at,
        )
    )
    db.flush()


def initialize_user_status_history(db: Session, user: User, *, now: datetime | None = None) -> None:
    if not is_status_history_trackable(user.role, user.is_active):
        return
    started_at = _now(now)
    _open_status_interval(db, user, user.availability_status, started_at)


def set_user_availability_status(
    db: Session,
    user: User,
    status: str,
    *,
    now: datetime | None = None,
    touch_updated_at: bool = True,
) -> datetime:
    changed_at = _now(now)
    old_status = _normalized_status(user.availability_status)
    new_status = _normalized_status(status)

    if is_status_history_trackable(user.role, user.is_active) and old_status != new_status:
        _close_open_intervals(db, user, changed_at)
        _open_status_interval(db, user, new_status, changed_at)

    user.availability_status = status
    if touch_updated_at:
        user.updated_at = changed_at
    return changed_at


def sync_user_status_history_after_profile_update(
    db: Session,
    user: User,
    *,
    previous_role: str | None,
    previous_is_active: bool | None,
    previous_availability_status: str | None,
    now: datetime | None = None,
) -> datetime:
    changed_at = _now(now)
    was_trackable = is_status_history_trackable(previous_role, previous_is_active)
    is_trackable = is_status_history_trackable(user.role, user.is_active)
    status_changed = _normalized_status(previous_availability_status) != _normalized_status(user.availability_status)

    if was_trackable and not is_trackable:
        _close_open_intervals(db, user, changed_at)
    elif not was_trackable and is_trackable:
        _close_open_intervals(db, user, changed_at)
        _open_status_interval(db, user, user.availability_status, changed_at)
    elif was_trackable and is_trackable and status_changed:
        _close_open_intervals(db, user, changed_at)
        _open_status_interval(db, user, user.availability_status, changed_at)

    return changed_at
