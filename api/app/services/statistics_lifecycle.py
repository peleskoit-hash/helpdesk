import uuid
from datetime import datetime

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..models import StatsCompletion, StatsUserStatusInterval, StatsWorkInterval, Ticket, TicketStatus, User
from .statistics_foundation import _object_identity


STATUS_HISTORY_ROLES = {"agent", "trainee"}
AVAILABILITY_STATUS_FALLBACK = "online"
WORK_ROLES = {"agent", "trainee"}
WORK_END_REASSIGNMENT = "reassignment"
WORK_END_UNASSIGNMENT = "unassignment"
WORK_END_FREEZE = "freeze"
WORK_END_CLOSE = "close"


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


def _work_identity(ticket: Ticket) -> tuple[str, str | None] | None:
    return _object_identity(ticket)


def _completion_identity(ticket: Ticket) -> tuple[str, str | None] | None:
    return _object_identity(ticket)


def _completion_row(db: Session, ticket: Ticket) -> StatsCompletion | None:
    identity = _completion_identity(ticket)
    if identity is None:
        return None
    object_type, _task_kind = identity
    return db.execute(
        select(StatsCompletion).where(
            and_(
                StatsCompletion.company_id == ticket.company_id,
                StatsCompletion.object_type == object_type,
                StatsCompletion.object_id == ticket.id,
            )
        )
    ).scalar_one_or_none()


def sync_completion_after_close(
    db: Session,
    ticket: Ticket,
    *,
    completed_by_user_id: str | None,
    completed_at: datetime,
) -> bool:
    identity = _completion_identity(ticket)
    if identity is None:
        return False
    object_type, task_kind = identity
    row = _completion_row(db, ticket)
    now = _now(None)
    if row is None:
        row = StatsCompletion(
            id=str(uuid.uuid4()),
            company_id=ticket.company_id,
            object_id=ticket.id,
            object_type=object_type,
            task_kind=task_kind,
            completed_by_user_id=completed_by_user_id,
            completed_at=completed_at,
            is_completed=True,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.task_kind = task_kind
        row.completed_by_user_id = completed_by_user_id
        row.completed_at = completed_at
        row.is_completed = True
        row.updated_at = now
    db.flush()
    return True


def sync_completion_after_reopen(db: Session, ticket: Ticket) -> None:
    identity = _completion_identity(ticket)
    if identity is None:
        return
    object_type, task_kind = identity
    row = _completion_row(db, ticket)
    now = _now(None)
    if row is None:
        row = StatsCompletion(
            id=str(uuid.uuid4()),
            company_id=ticket.company_id,
            object_id=ticket.id,
            object_type=object_type,
            task_kind=task_kind,
            completed_by_user_id=None,
            completed_at=None,
            is_completed=False,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.task_kind = task_kind
        row.completed_by_user_id = None
        row.completed_at = None
        row.is_completed = False
        row.updated_at = now
    db.flush()


def _work_assignee(db: Session, ticket: Ticket) -> User | None:
    if not ticket.assignee_id:
        return None
    return db.get(User, ticket.assignee_id)


def _is_work_assignee(user: User | None) -> bool:
    return user is not None and user.role in WORK_ROLES


def _open_work_intervals(db: Session, ticket: Ticket) -> list[StatsWorkInterval]:
    return db.execute(
        select(StatsWorkInterval).where(
            StatsWorkInterval.company_id == ticket.company_id,
            StatsWorkInterval.object_id == ticket.id,
            StatsWorkInterval.ended_at.is_(None),
        )
    ).scalars().all()


def _has_open_work_interval(db: Session, ticket: Ticket, user_id: str) -> bool:
    identity = _work_identity(ticket)
    if identity is None:
        return False
    object_type, _task_kind = identity
    return db.execute(
        select(StatsWorkInterval.id)
        .where(
            and_(
                StatsWorkInterval.company_id == ticket.company_id,
                StatsWorkInterval.object_id == ticket.id,
                StatsWorkInterval.object_type == object_type,
                StatsWorkInterval.user_id == user_id,
                StatsWorkInterval.ended_at.is_(None),
            )
        )
        .limit(1)
    ).first() is not None


def close_open_work_intervals(
    db: Session,
    ticket: Ticket,
    *,
    end_reason: str,
    now: datetime | None = None,
) -> datetime:
    ended_at = _now(now)
    closed_any = False
    for interval in _open_work_intervals(db, ticket):
        interval.ended_at = ended_at
        interval.end_reason = end_reason
        closed_any = True
    if closed_any:
        db.flush()
    return ended_at


def open_work_interval_if_current(
    db: Session,
    ticket: Ticket,
    *,
    now: datetime | None = None,
) -> bool:
    identity = _work_identity(ticket)
    if identity is None:
        return False
    if ticket.status == TicketStatus.CLOSED.value or bool(ticket.is_frozen):
        return False
    assignee = _work_assignee(db, ticket)
    if not _is_work_assignee(assignee):
        return False
    if _has_open_work_interval(db, ticket, assignee.id):
        return False

    started_at = _now(now)
    object_type, task_kind = identity
    db.add(
        StatsWorkInterval(
            id=str(uuid.uuid4()),
            company_id=ticket.company_id,
            object_id=ticket.id,
            object_type=object_type,
            task_kind=task_kind,
            user_id=assignee.id,
            started_at=started_at,
            ended_at=None,
            end_reason=None,
            created_at=started_at,
        )
    )
    db.flush()
    return True


def sync_work_interval_after_assignment_change(
    db: Session,
    ticket: Ticket,
    *,
    old_assignee_id: str | None,
    new_assignee_id: str | None,
    now: datetime | None = None,
) -> datetime:
    changed_at = _now(now)
    if old_assignee_id == new_assignee_id:
        open_work_interval_if_current(db, ticket, now=changed_at)
        return changed_at
    close_reason = WORK_END_UNASSIGNMENT if new_assignee_id is None else WORK_END_REASSIGNMENT
    close_open_work_intervals(db, ticket, end_reason=close_reason, now=changed_at)
    open_work_interval_if_current(db, ticket, now=changed_at)
    return changed_at


def sync_work_interval_after_freeze(db: Session, ticket: Ticket, *, now: datetime | None = None) -> datetime:
    return close_open_work_intervals(db, ticket, end_reason=WORK_END_FREEZE, now=now)


def sync_work_interval_after_unfreeze(db: Session, ticket: Ticket, *, now: datetime | None = None) -> datetime:
    changed_at = _now(now)
    open_work_interval_if_current(db, ticket, now=changed_at)
    return changed_at


def sync_work_interval_after_close(db: Session, ticket: Ticket, *, now: datetime | None = None) -> datetime:
    return close_open_work_intervals(db, ticket, end_reason=WORK_END_CLOSE, now=now)


def sync_work_interval_after_reopen(db: Session, ticket: Ticket, *, now: datetime | None = None) -> datetime:
    changed_at = _now(now)
    open_work_interval_if_current(db, ticket, now=changed_at)
    return changed_at
