from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime, timezone
from statistics import median
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session, aliased

from ..auth import CurrentUser, get_current_user, is_admin
from ..db import get_db
from ..models import StatsCompletion, StatsUserStatusInterval, StatsWorkInterval, User
from ..schemas import (
    CompletionPeriodStats,
    ManagementCompletedStatsItem,
    ManagementCompletedStatsOut,
    ManagementStatusStatsItem,
    ManagementStatusStatsOut,
    ManagementStatusStatsTotal,
    ManagementStatusTransitionItem,
    ManagementStatusTransitionsOut,
    ManagementWorkTimeStatsItem,
    ManagementWorkTimeStatsOut,
    ManagementWorkTimeStatsTotal,
)


router = APIRouter(prefix="/api/v1/management/statistics", tags=["management-statistics"])
STATUS_STATS_FIELDS = {
    "offline": "offline_seconds",
    "online": "online_seconds",
    "on_ticket": "on_ticket_seconds",
    "waiting": "waiting_seconds",
    "lunch": "lunch_seconds",
}
STATUS_TRANSITIONS_DEFAULT_LIMIT = 15
STATUS_TRANSITIONS_MAX_LIMIT = 100


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _require_admin(user: CurrentUser) -> None:
    if not is_admin(user):
        _error(403, "admin_only", "Доступ только для администратора")


def _utcnow() -> datetime:
    return datetime.utcnow()


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _numeric_seconds(value: float) -> int | float:
    return int(value) if value.is_integer() else value


def _encode_status_transition_cursor(started_at: datetime, interval_id: str) -> str:
    payload = {"started_at": _naive_utc(started_at).isoformat(), "id": interval_id}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_status_transition_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        started_at = _naive_utc(datetime.fromisoformat(payload["started_at"]))
        interval_id = payload["id"]
    except (binascii.Error, KeyError, TypeError, ValueError, json.JSONDecodeError):
        _error(400, "invalid_cursor", "Некорректный cursor")
    if not isinstance(interval_id, str) or not interval_id:
        _error(400, "invalid_cursor", "Некорректный cursor")
    return started_at, interval_id


def _stats_total(observations: list[tuple[str, int]]) -> ManagementWorkTimeStatsTotal:
    tickets = sum(1 for object_type, _seconds in observations if object_type == "ticket")
    tasks = sum(1 for object_type, _seconds in observations if object_type == "task")
    durations = [seconds for _object_type, seconds in observations]
    total_seconds = sum(durations)
    average_seconds = _numeric_seconds(total_seconds / len(durations)) if durations else 0
    median_seconds = _numeric_seconds(float(median(durations))) if durations else 0
    return ManagementWorkTimeStatsTotal(
        tickets=tickets,
        tasks=tasks,
        objects=tickets + tasks,
        average_seconds=average_seconds,
        median_seconds=median_seconds,
    )


def _status_stats_total(status_seconds: dict[str, int]) -> ManagementStatusStatsTotal:
    values = {field: int(status_seconds.get(status, 0)) for status, field in STATUS_STATS_FIELDS.items()}
    values["total_seconds"] = sum(values.values())
    return ManagementStatusStatsTotal(**values)


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


@router.get("/work-time", response_model=ManagementWorkTimeStatsOut)
def get_work_time_statistics(
    user_id: Annotated[str | None, Query()] = None,
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    if date_from is not None:
        date_from = _naive_utc(date_from)
    if date_to is not None:
        date_to = _naive_utc(date_to)
    if date_from is not None and date_to is not None and date_to <= date_from:
        _error(400, "invalid_date_range", "date_to должен быть позже date_from")

    admin = db.get(User, user.id)
    if admin is None:
        _error(404, "user_not_found", "Пользователь не найден")
    company_id = admin.company_id
    now = _naive_utc(_utcnow())

    filters = [
        StatsWorkInterval.company_id == company_id,
        StatsWorkInterval.object_type.in_(["ticket", "task"]),
    ]
    if user_id is not None:
        filters.append(StatsWorkInterval.user_id == user_id)
    if date_from is not None:
        filters.append(or_(StatsWorkInterval.ended_at.is_(None), StatsWorkInterval.ended_at > date_from))
    if date_to is not None:
        filters.append(StatsWorkInterval.started_at < date_to)

    intervals = db.execute(select(StatsWorkInterval).where(*filters)).scalars().all()
    observation_seconds: dict[tuple[str, str, str], float] = {}
    for interval in intervals:
        started_at = _naive_utc(interval.started_at)
        interval_end = _naive_utc(interval.ended_at) if interval.ended_at is not None else now
        effective_start = max(started_at, date_from) if date_from is not None else started_at
        effective_end = min(interval_end, date_to) if date_to is not None else interval_end
        if effective_end <= effective_start:
            continue
        key = (interval.user_id, interval.object_type, interval.object_id)
        observation_seconds[key] = observation_seconds.get(key, 0.0) + (effective_end - effective_start).total_seconds()

    observations_by_user: dict[str, list[tuple[str, int]]] = {}
    for (observed_user_id, object_type, _object_id), seconds in observation_seconds.items():
        whole_seconds = int(seconds)
        if whole_seconds <= 0:
            continue
        observations_by_user.setdefault(observed_user_id, []).append((object_type, whole_seconds))

    users_by_id: dict[str, User] = {}
    if observations_by_user:
        users_by_id = {
            item.id: item
            for item in db.execute(
                select(User).where(User.id.in_(observations_by_user.keys()), User.company_id == company_id)
            ).scalars().all()
        }

    items = []
    for observed_user_id, observations in observations_by_user.items():
        found_user = users_by_id.get(observed_user_id)
        total = _stats_total(observations)
        items.append(
            ManagementWorkTimeStatsItem(
                user_id=observed_user_id,
                full_name=found_user.full_name if found_user is not None else None,
                is_active=found_user.is_active if found_user is not None else None,
                **total.model_dump(),
            )
        )

    items.sort(key=lambda item: (-sum(seconds for _type, seconds in observations_by_user[item.user_id]), item.full_name or item.user_id, item.user_id))
    all_observations = [
        (object_type, seconds)
        for observations in observations_by_user.values()
        for object_type, seconds in observations
    ]
    return ManagementWorkTimeStatsOut(items=items, total=_stats_total(all_observations))


@router.get("/statuses", response_model=ManagementStatusStatsOut)
def get_status_statistics(
    user_id: Annotated[str | None, Query()] = None,
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    if date_from is not None:
        date_from = _naive_utc(date_from)
    if date_to is not None:
        date_to = _naive_utc(date_to)
    if date_from is not None and date_to is not None and date_to <= date_from:
        _error(400, "invalid_date_range", "date_to должен быть позже date_from")

    admin = db.get(User, user.id)
    if admin is None:
        _error(404, "user_not_found", "Пользователь не найден")
    company_id = admin.company_id
    now = _naive_utc(_utcnow())

    filters = [
        StatsUserStatusInterval.company_id == company_id,
    ]
    if user_id is not None:
        filters.append(StatsUserStatusInterval.user_id == user_id)
    if date_from is not None:
        filters.append(or_(StatsUserStatusInterval.ended_at.is_(None), StatsUserStatusInterval.ended_at > date_from))
    if date_to is not None:
        filters.append(StatsUserStatusInterval.started_at < date_to)

    intervals = db.execute(select(StatsUserStatusInterval).where(*filters)).scalars().all()
    seconds_by_user_status: dict[tuple[str, str], float] = {}
    for interval in intervals:
        if interval.status not in STATUS_STATS_FIELDS:
            continue
        started_at = _naive_utc(interval.started_at)
        interval_end = _naive_utc(interval.ended_at) if interval.ended_at is not None else now
        effective_start = max(started_at, date_from) if date_from is not None else started_at
        effective_end = min(interval_end, date_to) if date_to is not None else interval_end
        if effective_end <= effective_start:
            continue
        key = (interval.user_id, interval.status)
        seconds_by_user_status[key] = seconds_by_user_status.get(key, 0.0) + (effective_end - effective_start).total_seconds()

    status_seconds_by_user: dict[str, dict[str, int]] = {}
    for (observed_user_id, status), seconds in seconds_by_user_status.items():
        whole_seconds = int(seconds)
        if whole_seconds <= 0:
            continue
        status_seconds_by_user.setdefault(observed_user_id, {})[status] = whole_seconds

    users_by_id: dict[str, User] = {}
    if status_seconds_by_user:
        users_by_id = {
            item.id: item
            for item in db.execute(
                select(User).where(User.id.in_(status_seconds_by_user.keys()), User.company_id == company_id)
            ).scalars().all()
        }

    items = []
    for observed_user_id, status_seconds in status_seconds_by_user.items():
        found_user = users_by_id.get(observed_user_id)
        total = _status_stats_total(status_seconds)
        items.append(
            ManagementStatusStatsItem(
                user_id=observed_user_id,
                full_name=found_user.full_name if found_user is not None else None,
                is_active=found_user.is_active if found_user is not None else None,
                **total.model_dump(),
            )
        )

    items.sort(key=lambda item: (-item.total_seconds, item.full_name or item.user_id, item.user_id))
    total_status_seconds = {
        status: sum(getattr(item, field) for item in items)
        for status, field in STATUS_STATS_FIELDS.items()
    }
    return ManagementStatusStatsOut(items=items, total=_status_stats_total(total_status_seconds))


@router.get("/status-transitions", response_model=ManagementStatusTransitionsOut)
def get_status_transitions(
    user_id: Annotated[str | None, Query()] = None,
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=STATUS_TRANSITIONS_MAX_LIMIT)] = STATUS_TRANSITIONS_DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
    sort_order: Annotated[Literal["asc", "desc"], Query()] = "desc",
    include_total: Annotated[bool, Query()] = False,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    if date_from is not None:
        date_from = _naive_utc(date_from)
    if date_to is not None:
        date_to = _naive_utc(date_to)
    if date_from is not None and date_to is not None and date_to <= date_from:
        _error(400, "invalid_date_range", "date_to должен быть позже date_from")

    admin = db.get(User, user.id)
    if admin is None:
        _error(404, "user_not_found", "Пользователь не найден")
    company_id = admin.company_id

    CurrentStatusInterval = aliased(StatsUserStatusInterval)
    PreviousStatusInterval = aliased(StatsUserStatusInterval)
    OtherPreviousStatusInterval = aliased(StatsUserStatusInterval)

    filters = [CurrentStatusInterval.company_id == company_id]
    if user_id is not None:
        filters.append(CurrentStatusInterval.user_id == user_id)
    if date_from is not None:
        filters.append(CurrentStatusInterval.started_at >= date_from)
    if date_to is not None:
        filters.append(CurrentStatusInterval.started_at < date_to)
    cursor_filters = []
    if cursor is not None:
        cursor_started_at, cursor_id = _decode_status_transition_cursor(cursor)
        cursor_filters.append(
            or_(
                CurrentStatusInterval.started_at > cursor_started_at if sort_order == "asc" else CurrentStatusInterval.started_at < cursor_started_at,
                and_(CurrentStatusInterval.started_at == cursor_started_at, CurrentStatusInterval.id > cursor_id if sort_order == "asc" else CurrentStatusInterval.id < cursor_id),
            )
        )

    other_previous_exists = exists(
        select(OtherPreviousStatusInterval.id).where(
            OtherPreviousStatusInterval.company_id == CurrentStatusInterval.company_id,
            OtherPreviousStatusInterval.user_id == CurrentStatusInterval.user_id,
            OtherPreviousStatusInterval.ended_at == CurrentStatusInterval.started_at,
            OtherPreviousStatusInterval.id != CurrentStatusInterval.id,
            OtherPreviousStatusInterval.id != PreviousStatusInterval.id,
        )
    )

    query = (
        select(CurrentStatusInterval, PreviousStatusInterval.status)
        .join(
            PreviousStatusInterval,
            and_(
                PreviousStatusInterval.company_id == CurrentStatusInterval.company_id,
                PreviousStatusInterval.user_id == CurrentStatusInterval.user_id,
                PreviousStatusInterval.ended_at == CurrentStatusInterval.started_at,
                PreviousStatusInterval.id != CurrentStatusInterval.id,
                PreviousStatusInterval.status != CurrentStatusInterval.status,
            ),
        )
        .where(*filters, ~other_previous_exists)
    )
    total_count = db.scalar(select(func.count()).select_from(query.subquery())) if include_total else None
    order = (CurrentStatusInterval.started_at.asc(), CurrentStatusInterval.id.asc()) if sort_order == "asc" else (CurrentStatusInterval.started_at.desc(), CurrentStatusInterval.id.desc())
    rows = db.execute(query.where(*cursor_filters).order_by(*order).limit(limit + 1)).all()

    page_rows = rows[:limit]
    has_more = len(rows) > limit
    next_cursor = _encode_status_transition_cursor(page_rows[-1][0].started_at, page_rows[-1][0].id) if has_more and page_rows else None
    transition_user_ids = set()
    for current, _from_status in page_rows:
        transition_user_ids.add(current.user_id)

    users_by_id: dict[str, User] = {}
    if transition_user_ids:
        users_by_id = {
            item.id: item
            for item in db.execute(
                select(User).where(User.id.in_(transition_user_ids), User.company_id == company_id)
            ).scalars().all()
        }

    items = []
    for current, from_status in page_rows:
        found_user = users_by_id.get(current.user_id)
        items.append(
            ManagementStatusTransitionItem(
                user_id=current.user_id,
                full_name=found_user.full_name if found_user is not None else None,
                is_active=found_user.is_active if found_user is not None else None,
                from_status=from_status,
                to_status=current.status,
                changed_at=current.started_at,
            )
        )
    return ManagementStatusTransitionsOut(items=items, next_cursor=next_cursor, has_more=has_more, total_count=total_count)
