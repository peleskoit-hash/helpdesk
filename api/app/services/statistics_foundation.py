from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, delete, select
from sqlalchemy.orm import Session

from ..models import (
    StatsCompletion,
    StatsUserStatusInterval,
    StatsWorkInterval,
    Ticket,
    TicketStatus,
    TicketTaskKind,
    TicketWorkflowType,
    User,
)


SUPPORT_ROLES = {"agent", "trainee"}
AVAILABILITY_STATUS_FALLBACK = "online"


@dataclass(frozen=True)
class StatisticsResetResult:
    completions_deleted: int
    work_intervals_deleted: int
    user_status_intervals_deleted: int


@dataclass(frozen=True)
class StatisticsBootstrapResult:
    user_status_intervals_created: int
    work_intervals_created: int


def _now() -> datetime:
    return datetime.utcnow()


def reset_statistics(db: Session, company_id: str) -> StatisticsResetResult:
    completions_deleted = db.execute(
        delete(StatsCompletion).where(StatsCompletion.company_id == company_id)
    ).rowcount
    work_intervals_deleted = db.execute(
        delete(StatsWorkInterval).where(StatsWorkInterval.company_id == company_id)
    ).rowcount
    user_status_intervals_deleted = db.execute(
        delete(StatsUserStatusInterval).where(StatsUserStatusInterval.company_id == company_id)
    ).rowcount
    return StatisticsResetResult(
        completions_deleted=int(completions_deleted or 0),
        work_intervals_deleted=int(work_intervals_deleted or 0),
        user_status_intervals_deleted=int(user_status_intervals_deleted or 0),
    )


def _active_status_users(db: Session, company_id: str) -> list[User]:
    return db.execute(
        select(User)
        .where(
            and_(
                User.company_id == company_id,
                User.role.in_(SUPPORT_ROLES),
                User.is_active.is_(True),
            )
        )
        .order_by(User.id.asc())
    ).scalars().all()


def _has_open_status_interval(db: Session, *, company_id: str, user_id: str) -> bool:
    return db.execute(
        select(StatsUserStatusInterval.id)
        .where(
            and_(
                StatsUserStatusInterval.company_id == company_id,
                StatsUserStatusInterval.user_id == user_id,
                StatsUserStatusInterval.ended_at.is_(None),
            )
        )
        .limit(1)
    ).first() is not None


def _bootstrap_user_status_intervals(db: Session, *, company_id: str, now: datetime) -> int:
    created = 0
    for user in _active_status_users(db, company_id):
        if _has_open_status_interval(db, company_id=company_id, user_id=user.id):
            continue
        db.add(
            StatsUserStatusInterval(
                id=str(uuid.uuid4()),
                company_id=company_id,
                user_id=user.id,
                status=(user.availability_status or "").strip() or AVAILABILITY_STATUS_FALLBACK,
                started_at=now,
                ended_at=None,
                created_at=now,
            )
        )
        created += 1
    return created


def _object_identity(ticket: Ticket) -> tuple[str, str | None] | None:
    workflow_type = ticket.workflow_type or TicketWorkflowType.TICKET.value
    if workflow_type == TicketWorkflowType.TICKET.value:
        return "ticket", None
    if workflow_type != TicketWorkflowType.TASK.value:
        return None
    task_kind = ticket.task_kind or TicketTaskKind.NORMAL.value
    if task_kind not in {TicketTaskKind.NORMAL.value, TicketTaskKind.GROUP_CHILD.value}:
        return None
    return "task", task_kind


def _current_work_tickets(db: Session, company_id: str) -> list[Ticket]:
    return db.execute(
        select(Ticket)
        .join(User, User.id == Ticket.assignee_id)
        .where(
            and_(
                Ticket.company_id == company_id,
                Ticket.assignee_id.is_not(None),
                Ticket.status != TicketStatus.CLOSED.value,
                Ticket.is_frozen.is_(False),
                User.role.in_(SUPPORT_ROLES),
            )
        )
        .order_by(Ticket.id.asc())
    ).scalars().all()


def _has_open_work_interval(
    db: Session,
    *,
    company_id: str,
    user_id: str,
    object_type: str,
    object_id: str,
) -> bool:
    return db.execute(
        select(StatsWorkInterval.id)
        .where(
            and_(
                StatsWorkInterval.company_id == company_id,
                StatsWorkInterval.user_id == user_id,
                StatsWorkInterval.object_type == object_type,
                StatsWorkInterval.object_id == object_id,
                StatsWorkInterval.ended_at.is_(None),
            )
        )
        .limit(1)
    ).first() is not None


def _bootstrap_work_intervals(db: Session, *, company_id: str, now: datetime) -> int:
    created = 0
    for ticket in _current_work_tickets(db, company_id):
        identity = _object_identity(ticket)
        if identity is None or not ticket.assignee_id:
            continue
        object_type, task_kind = identity
        if _has_open_work_interval(
            db,
            company_id=company_id,
            user_id=ticket.assignee_id,
            object_type=object_type,
            object_id=ticket.id,
        ):
            continue
        db.add(
            StatsWorkInterval(
                id=str(uuid.uuid4()),
                company_id=company_id,
                object_id=ticket.id,
                object_type=object_type,
                task_kind=task_kind,
                user_id=ticket.assignee_id,
                started_at=now,
                ended_at=None,
                end_reason=None,
                created_at=now,
            )
        )
        created += 1
    return created


def bootstrap_statistics(db: Session, company_id: str, now: datetime | None = None) -> StatisticsBootstrapResult:
    started_at = now or _now()
    status_created = _bootstrap_user_status_intervals(db, company_id=company_id, now=started_at)
    work_created = _bootstrap_work_intervals(db, company_id=company_id, now=started_at)
    if status_created or work_created:
        db.flush()
    return StatisticsBootstrapResult(
        user_status_intervals_created=status_created,
        work_intervals_created=work_created,
    )
