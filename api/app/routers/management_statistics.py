from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, is_admin
from ..db import get_db
from ..models import StatsCompletion, User
from ..schemas import CompletionPeriodStats, ManagementCompletedStatsItem, ManagementCompletedStatsOut


router = APIRouter(prefix="/api/v1/management/statistics", tags=["management-statistics"])


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _require_admin(user: CurrentUser) -> None:
    if not is_admin(user):
        _error(403, "admin_only", "Доступ только для администратора")


@router.get("/completed", response_model=ManagementCompletedStatsOut)
def get_completed_statistics(
    user_id: Annotated[str | None, Query()] = None,
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    if date_from is not None and date_to is not None and date_to <= date_from:
        _error(400, "invalid_date_range", "date_to должен быть позже date_from")

    admin = db.get(User, user.id)
    if admin is None:
        _error(404, "user_not_found", "Пользователь не найден")
    company_id = admin.company_id

    filters = [
        StatsCompletion.is_completed.is_(True),
        StatsCompletion.completed_by_user_id.is_not(None),
        StatsCompletion.completed_at.is_not(None),
        StatsCompletion.object_type.in_(["ticket", "task"]),
    ]
    if company_id is not None:
        filters.append(StatsCompletion.company_id == company_id)
    if user_id is not None:
        filters.append(StatsCompletion.completed_by_user_id == user_id)
    if date_from is not None:
        filters.append(StatsCompletion.completed_at >= date_from)
    if date_to is not None:
        filters.append(StatsCompletion.completed_at < date_to)

    rows = db.execute(select(StatsCompletion).where(*filters)).scalars().all()
    stats_by_user: dict[str, dict[str, int]] = {}
    for row in rows:
        completed_by = row.completed_by_user_id
        if completed_by is None:
            continue
        stats = stats_by_user.setdefault(completed_by, {"tickets": 0, "tasks": 0})
        if row.object_type == "ticket":
            stats["tickets"] += 1
        elif row.object_type == "task":
            stats["tasks"] += 1

    users_by_id: dict[str, User] = {}
    if stats_by_user:
        user_filters = [User.id.in_(stats_by_user.keys())]
        user_filters.append(User.company_id == company_id)
        users_by_id = {item.id: item for item in db.execute(select(User).where(*user_filters)).scalars().all()}

    items = []
    for completed_by, stats in stats_by_user.items():
        found_user = users_by_id.get(completed_by)
        tickets = stats["tickets"]
        tasks = stats["tasks"]
        items.append(
            ManagementCompletedStatsItem(
                user_id=completed_by,
                full_name=found_user.full_name if found_user is not None else None,
                is_active=found_user.is_active if found_user is not None else None,
                tickets=tickets,
                tasks=tasks,
                total=tickets + tasks,
            )
        )

    items.sort(key=lambda item: (-item.total, item.full_name or item.user_id, item.user_id))
    total_tickets = sum(item.tickets for item in items)
    total_tasks = sum(item.tasks for item in items)
    return ManagementCompletedStatsOut(
        items=items,
        total=CompletionPeriodStats(total=total_tickets + total_tasks, tickets=total_tickets, tasks=total_tasks),
    )
