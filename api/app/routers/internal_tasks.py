from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, is_admin
from ..config import settings
from ..db import get_db
from ..models import (
    InternalTaskAssignmentMode,
    InternalTaskDefinition,
    InternalTaskDefinitionState,
    InternalTaskOccurrence,
    InternalTaskScheduleKind,
    Ticket,
    TicketStatus,
    User,
)
from ..services.internal_tasks import materialize_internal_task
from ..services.ticket_admin_actions import _delete_ticket_hard
from ..services.internal_task_recurrence import (
    CYCLE_END_AT,
    CYCLE_END_COUNT,
    CYCLE_END_NEVER,
    RECURRENCE_WEEKLY,
    calculate_recurrence_occurrences,
    normalize_recurrence_config,
    validate_recurrence_end_rule,
)


router = APIRouter(prefix="/api/v1/management/internal-tasks", tags=["management-internal-tasks"])


class InternalTaskCreateNowPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(...)
    description: str = Field(...)
    assignment_mode: str
    assignee_user_id: str | None = None
    display_author: bool
    responsible_admin_id: str | None = None
    notify_on_completion: bool
    deadline_enabled: bool
    deadline_at: datetime | None = None

    @field_validator("title", "description", mode="before")
    @classmethod
    def _strip_text(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


class InternalTaskScheduledPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(...)
    description: str = Field(...)
    assignment_mode: str
    assignee_user_id: str | None = None
    display_author: bool
    responsible_admin_id: str | None = None
    notify_on_completion: bool
    scheduled_for: datetime
    deadline_enabled: bool
    deadline_at: datetime | None = None

    @field_validator("title", "description", mode="before")
    @classmethod
    def _strip_text(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


class InternalTaskRecurringPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(...)
    description: str = Field(...)
    assignment_mode: str
    assignee_user_id: str | None = None
    display_author: bool
    responsible_admin_id: str | None = None
    notify_on_completion: bool
    deadline_enabled: bool
    deadline_at: datetime | None = None
    recurrence_mode: str
    recurrence_config: dict
    cycle_start_at: datetime
    cycle_end_mode: str
    cycle_end_at: datetime | None = None
    cycle_max_occurrences: int | None = None

    @field_validator("title", "description", mode="before")
    @classmethod
    def _strip_text(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _require_admin(user: CurrentUser) -> None:
    if not is_admin(user):
        _error(403, "admin_only", "Доступ только для администратора")


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalize_app_local_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=ZoneInfo(settings.app_timezone)).astimezone(timezone.utc)
    return value.astimezone(timezone.utc)


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _current_admin(db: Session, user: CurrentUser) -> User:
    admin = db.execute(
        select(User)
        .where(and_(User.id == user.id, User.role == "admin"))
        .limit(1)
    ).scalar_one_or_none()
    if admin is None:
        _error(403, "admin_only", "Доступ только для администратора")
    return admin


def _responsible_admin(db: Session, company_id: str, responsible_admin_id: str | None, actor: User) -> User:
    if responsible_admin_id is None:
        return actor
    responsible = db.execute(
        select(User)
        .where(and_(User.id == responsible_admin_id, User.company_id == company_id))
        .limit(1)
    ).scalar_one_or_none()
    if responsible is None:
        _error(404, "responsible_admin_not_found", "Ответственный администратор не найден")
    if responsible.role != "admin":
        _error(422, "responsible_admin_invalid_role", "Ответственным может быть только администратор")
    return responsible


def _direct_assignee(db: Session, company_id: str, assignee_user_id: str | None) -> User:
    if not assignee_user_id:
        _error(422, "assignee_required", "Для прямого назначения нужен assignee_user_id")
    assignee = db.execute(
        select(User)
        .where(and_(User.id == assignee_user_id, User.company_id == company_id))
        .limit(1)
    ).scalar_one_or_none()
    if assignee is None:
        _error(404, "assignee_not_found", "Исполнитель не найден")
    if assignee.role not in {"agent", "trainee"}:
        _error(422, "assignee_invalid_role", "Исполнителем может быть только агент или стажёр")
    return assignee


def _validate_assignment(
    payload: InternalTaskCreateNowPayload | InternalTaskScheduledPayload | InternalTaskRecurringPayload,
    db: Session,
    company_id: str,
) -> str | None:
    if payload.assignment_mode == InternalTaskAssignmentMode.DIRECT.value:
        return _direct_assignee(db, company_id, payload.assignee_user_id).id
    if payload.assignment_mode == InternalTaskAssignmentMode.DISTRIBUTION.value:
        if payload.assignee_user_id is not None:
            _error(422, "assignee_not_allowed", "При распределении assignee_user_id должен быть пустым")
        return None
    _error(422, "unsupported_assignment_mode", "Неподдерживаемый режим назначения")


def _validate_deadline(
    payload: InternalTaskCreateNowPayload | InternalTaskScheduledPayload | InternalTaskRecurringPayload,
    *,
    app_local_naive: bool = False,
) -> datetime | None:
    if payload.deadline_enabled and payload.deadline_at is None:
        _error(422, "deadline_required", "Для включённого дедлайна нужен deadline_at")
    if not payload.deadline_enabled and payload.deadline_at is not None:
        _error(422, "deadline_not_allowed", "При выключенном дедлайне deadline_at должен быть пустым")
    if app_local_naive:
        return _normalize_app_local_datetime(payload.deadline_at)
    return _normalize_datetime(payload.deadline_at)


def _validate_text(payload: InternalTaskCreateNowPayload | InternalTaskScheduledPayload | InternalTaskRecurringPayload) -> None:
    if not payload.title:
        _error(422, "title_required", "Название задачи обязательно")
    if len(payload.title) > 255:
        _error(422, "title_too_long", "Название задачи слишком длинное")
    if not payload.description:
        _error(422, "description_required", "Описание задачи обязательно")


def _validate_scheduled_for(payload: InternalTaskScheduledPayload) -> datetime:
    scheduled_for_utc = _normalize_app_local_datetime(payload.scheduled_for)
    if scheduled_for_utc is None:
        _error(422, "scheduled_for_required", "Укажите дату и время создания")
    comparable = _utc_naive(scheduled_for_utc)
    if comparable is None or comparable <= datetime.utcnow():
        _error(422, "scheduled_for_past", "Запланированное время должно быть в будущем")
    return scheduled_for_utc


def _local_datetime_parts(value: datetime) -> tuple[int, int, int, int, int]:
    local = _normalize_app_local_datetime(value)
    if local is None:
        _error(422, "cycle_start_required", "Укажите начало расписания")
    local_value = local.astimezone(ZoneInfo(settings.app_timezone))
    return local_value.year, local_value.month, local_value.day, local_value.hour, local_value.minute


def _first_weekly_start_at_utc(cycle_start_at: datetime, time_value: str) -> datetime:
    config = normalize_recurrence_config(RECURRENCE_WEEKLY, {"time": time_value})
    weekday = _positive_weekday(cycle_start_at)
    year, month, day, _, _ = _local_datetime_parts(cycle_start_at)
    tz = ZoneInfo(settings.app_timezone)
    local_start = datetime(year, month, day, 0, 0, tzinfo=tz)
    days_ahead = (weekday - local_start.isoweekday()) % 7
    hour, minute = (int(part) for part in config["time"].split(":", 1))
    candidate = (local_start + timedelta(days=days_ahead)).replace(hour=hour, minute=minute)
    lower_bound = _normalize_app_local_datetime(cycle_start_at)
    if lower_bound is not None and candidate.astimezone(timezone.utc) < lower_bound:
        candidate += timedelta(days=7)
    return candidate.astimezone(timezone.utc)


def _positive_weekday(value: datetime) -> int:
    local = _normalize_app_local_datetime(value)
    if local is None:
        _error(422, "cycle_start_required", "Укажите начало расписания")
    return local.astimezone(ZoneInfo(settings.app_timezone)).isoweekday()


def _validate_recurrence(payload: InternalTaskRecurringPayload) -> tuple[str, dict, datetime, str, datetime | None, int | None]:
    try:
        raw_config = dict(payload.recurrence_config or {})
        if payload.recurrence_mode == RECURRENCE_WEEKLY and "weekday" in raw_config:
            weekday = raw_config.pop("weekday")
            if isinstance(weekday, bool) or not isinstance(weekday, int) or weekday < 1 or weekday > 7:
                raise ValueError("Invalid recurrence weekday")
            normalized_config = normalize_recurrence_config(payload.recurrence_mode, raw_config)
            cycle_start_at_utc = _first_weekly_start_at_utc(payload.cycle_start_at, normalized_config["time"])
            configured_weekday = cycle_start_at_utc.astimezone(ZoneInfo(settings.app_timezone)).isoweekday()
            if configured_weekday != weekday:
                year, month, day, _, _ = _local_datetime_parts(payload.cycle_start_at)
                tz = ZoneInfo(settings.app_timezone)
                anchor = datetime(year, month, day, 0, 0, tzinfo=tz)
                days_ahead = (weekday - anchor.isoweekday()) % 7
                hour, minute = (int(part) for part in normalized_config["time"].split(":", 1))
                candidate = (anchor + timedelta(days=days_ahead)).replace(hour=hour, minute=minute)
                lower_bound = _normalize_app_local_datetime(payload.cycle_start_at)
                if lower_bound is not None and candidate.astimezone(timezone.utc) < lower_bound:
                    candidate += timedelta(days=7)
                cycle_start_at_utc = candidate.astimezone(timezone.utc)
        else:
            normalized_config = normalize_recurrence_config(payload.recurrence_mode, payload.recurrence_config)
            cycle_start_at_utc = _normalize_app_local_datetime(payload.cycle_start_at)
        cycle_end_at_utc = _normalize_app_local_datetime(payload.cycle_end_at)
        validate_recurrence_end_rule(
            cycle_end_mode=payload.cycle_end_mode,
            cycle_end_at_utc=cycle_end_at_utc,
            cycle_max_occurrences=payload.cycle_max_occurrences,
        )
    except ValueError as exc:
        _error(422, "recurrence_invalid", str(exc))
    if cycle_start_at_utc is None:
        _error(422, "cycle_start_required", "Укажите начало расписания")
    return (
        payload.recurrence_mode,
        normalized_config,
        cycle_start_at_utc,
        payload.cycle_end_mode,
        cycle_end_at_utc,
        payload.cycle_max_occurrences,
    )


def _successful_occurrence_count(db: Session, definition_id: str) -> int:
    return db.execute(
        select(func.count())
        .select_from(InternalTaskOccurrence)
        .where(InternalTaskOccurrence.definition_id == definition_id)
    ).scalar_one()


def _first_recurring_candidate_at_or_after(definition: InternalTaskDefinition, *, window_start: datetime) -> datetime | None:
    cycle_start_at_utc = _utc_naive(definition.cycle_start_at_utc)
    window_start = _utc_naive(window_start)
    cycle_end_at_utc = _utc_naive(definition.cycle_end_at_utc)
    if cycle_start_at_utc is None or window_start is None:
        return None
    window_start = max(cycle_start_at_utc, window_start)
    window_end = min(
        cycle_end_at_utc or (window_start + timedelta(days=366 * 5)),
        window_start + timedelta(days=366 * 5),
    )
    if window_end < window_start:
        return None
    try:
        candidates = calculate_recurrence_occurrences(
            recurrence_mode=definition.recurrence_mode,
            recurrence_config=definition.recurrence_config,
            timezone_name=definition.timezone_name,
            cycle_start_at_utc=cycle_start_at_utc,
            window_start_utc=window_start,
            window_end_utc=window_end,
            cycle_end_mode=definition.cycle_end_mode,
            cycle_end_at_utc=cycle_end_at_utc,
        )
    except ValueError:
        return None
    return candidates[0] if candidates else None


def _next_recurring_at(db: Session, definition: InternalTaskDefinition) -> datetime | None:
    if definition.state != InternalTaskDefinitionState.ACTIVE.value or definition.cycle_start_at_utc is None:
        return None
    successful_count = _successful_occurrence_count(db, definition.id)
    if definition.cycle_end_mode == CYCLE_END_COUNT and definition.cycle_max_occurrences is not None and successful_count >= definition.cycle_max_occurrences:
        return None
    now = datetime.utcnow()
    cycle_start_at_utc = _utc_naive(definition.cycle_start_at_utc)
    generation_not_before_utc = _utc_naive(definition.generation_not_before_utc)
    cycle_end_at_utc = _utc_naive(definition.cycle_end_at_utc)
    if cycle_start_at_utc is None:
        return None
    if definition.cycle_end_mode == CYCLE_END_AT and cycle_end_at_utc is not None and cycle_end_at_utc < now:
        return None
    window_start = max(
        cycle_start_at_utc,
        generation_not_before_utc or cycle_start_at_utc,
        now,
    )
    return _first_recurring_candidate_at_or_after(definition, window_start=window_start)


def _user_out(user: User | None) -> dict | None:
    if user is None:
        return None
    return {
        "id": user.id,
        "full_name": user.full_name,
        "login": user.login,
        "role": user.role,
        "is_active": bool(user.is_active),
        "availability_status": user.availability_status,
    }


def _definition_has_occurrence(db: Session, definition_id: str) -> bool:
    return db.execute(
        select(InternalTaskOccurrence.id)
        .where(InternalTaskOccurrence.definition_id == definition_id)
        .limit(1)
    ).scalar_one_or_none() is not None


def _scheduled_definition_out(db: Session, definition: InternalTaskDefinition) -> dict:
    return {
        "kind": "scheduled",
        "definition_id": definition.id,
        "title": definition.title,
        "description": definition.description,
        "creator_admin": _user_out(db.get(User, definition.creator_admin_id)),
        "responsible_admin": _user_out(db.get(User, definition.responsible_admin_id)),
        "assignment_mode": definition.assignment_mode,
        "assignee": _user_out(db.get(User, definition.assignee_user_id)) if definition.assignee_user_id else None,
        "display_author": bool(definition.display_author),
        "notify_on_completion": bool(definition.notify_on_completion),
        "scheduled_for": definition.scheduled_for_utc,
        "deadline_at": definition.deadline_at_utc,
        "state": definition.state,
        "schedule_kind": definition.schedule_kind,
        "created_at": definition.created_at,
    }


def _recurring_definition_out(db: Session, definition: InternalTaskDefinition) -> dict:
    return {
        "kind": "recurring",
        "definition_id": definition.id,
        "title": definition.title,
        "description": definition.description,
        "creator_admin": _user_out(db.get(User, definition.creator_admin_id)),
        "responsible_admin": _user_out(db.get(User, definition.responsible_admin_id)),
        "assignment_mode": definition.assignment_mode,
        "assignee": _user_out(db.get(User, definition.assignee_user_id)) if definition.assignee_user_id else None,
        "display_author": bool(definition.display_author),
        "notify_on_completion": bool(definition.notify_on_completion),
        "deadline_at": definition.deadline_at_utc,
        "state": definition.state,
        "schedule_kind": definition.schedule_kind,
        "timezone_name": definition.timezone_name,
        "recurrence_mode": definition.recurrence_mode,
        "recurrence_config": definition.recurrence_config,
        "cycle_start_at": definition.cycle_start_at_utc,
        "cycle_end_mode": definition.cycle_end_mode,
        "cycle_end_at": definition.cycle_end_at_utc,
        "cycle_max_occurrences": definition.cycle_max_occurrences,
        "generation_not_before": definition.generation_not_before_utc,
        "next_occurrence_at": _next_recurring_at(db, definition),
        "created_at": definition.created_at,
        "updated_at": definition.updated_at,
    }


def _materialized_task_out(
    db: Session,
    *,
    definition: InternalTaskDefinition,
    occurrence: InternalTaskOccurrence,
    ticket: Ticket,
) -> dict:
    current_assignee = db.get(User, ticket.assignee_id) if ticket.assignee_id else None
    return {
        "kind": "materialized",
        "definition_id": definition.id,
        "occurrence_id": occurrence.id,
        "ticket_id": ticket.id,
        "title": occurrence.title_snapshot,
        "description": occurrence.description_snapshot,
        "creator_admin": _user_out(db.get(User, occurrence.creator_admin_id)),
        "responsible_admin": _user_out(db.get(User, occurrence.responsible_admin_id)),
        "effective_assignment_mode": occurrence.effective_assignment_mode,
        "effective_assignee": _user_out(db.get(User, occurrence.effective_assignee_user_id)) if occurrence.effective_assignee_user_id else None,
        "current_assignee": _user_out(current_assignee),
        "ticket_status": ticket.status,
        "materialized_at": occurrence.materialized_at_utc,
        "closed_at": ticket.closed_at,
        "notify_on_completion": bool(occurrence.notify_on_completion),
        "deadline_at": occurrence.deadline_at_utc,
        "schedule_kind": definition.schedule_kind,
    }


def _create_scheduled_definition(
    db: Session,
    *,
    payload: InternalTaskScheduledPayload,
    actor: User,
    responsible: User,
    assignee_user_id: str | None,
    scheduled_for_utc: datetime,
    deadline_at_utc: datetime | None,
) -> InternalTaskDefinition:
    now = datetime.utcnow()
    definition = InternalTaskDefinition(
        id=str(uuid.uuid4()),
        company_id=actor.company_id,
        title=payload.title,
        description=payload.description,
        creator_admin_id=actor.id,
        responsible_admin_id=responsible.id,
        display_author=payload.display_author,
        assignment_mode=payload.assignment_mode,
        assignee_user_id=assignee_user_id,
        notify_on_completion=payload.notify_on_completion,
        deadline_at_utc=deadline_at_utc,
        schedule_kind=InternalTaskScheduleKind.SCHEDULED.value,
        timezone_name=settings.app_timezone,
        scheduled_for_utc=scheduled_for_utc,
        recurrence_mode=None,
        recurrence_config=None,
        cycle_start_at_utc=None,
        cycle_end_mode=None,
        cycle_end_at_utc=None,
        cycle_max_occurrences=None,
        state=InternalTaskDefinitionState.ACTIVE.value,
        generation_not_before_utc=None,
        created_at=now,
        updated_at=now,
    )
    db.add(definition)
    db.commit()
    db.refresh(definition)
    return definition


def _create_recurring_definition(
    db: Session,
    *,
    payload: InternalTaskRecurringPayload,
    actor: User,
    responsible: User,
    assignee_user_id: str | None,
    deadline_at_utc: datetime | None,
    recurrence_mode: str,
    recurrence_config: dict,
    cycle_start_at_utc: datetime,
    cycle_end_mode: str,
    cycle_end_at_utc: datetime | None,
    cycle_max_occurrences: int | None,
) -> InternalTaskDefinition:
    now = datetime.utcnow()
    definition = InternalTaskDefinition(
        id=str(uuid.uuid4()),
        company_id=actor.company_id,
        title=payload.title,
        description=payload.description,
        creator_admin_id=actor.id,
        responsible_admin_id=responsible.id,
        display_author=payload.display_author,
        assignment_mode=payload.assignment_mode,
        assignee_user_id=assignee_user_id,
        notify_on_completion=payload.notify_on_completion,
        deadline_at_utc=deadline_at_utc,
        schedule_kind=InternalTaskScheduleKind.RECURRING.value,
        timezone_name=settings.app_timezone,
        scheduled_for_utc=None,
        recurrence_mode=recurrence_mode,
        recurrence_config=recurrence_config,
        cycle_start_at_utc=cycle_start_at_utc,
        cycle_end_mode=cycle_end_mode,
        cycle_end_at_utc=cycle_end_at_utc,
        cycle_max_occurrences=cycle_max_occurrences,
        state=InternalTaskDefinitionState.ACTIVE.value,
        generation_not_before_utc=None,
        created_at=now,
        updated_at=now,
    )
    db.add(definition)
    db.commit()
    db.refresh(definition)
    return definition


@router.post("")
def create_internal_task_now(
    payload: InternalTaskCreateNowPayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    _validate_text(payload)
    actor = _current_admin(db, user)
    responsible = _responsible_admin(db, actor.company_id, payload.responsible_admin_id, actor)
    assignee_user_id = _validate_assignment(payload, db, actor.company_id)
    deadline_at_utc = _validate_deadline(payload)

    now = datetime.utcnow()
    definition = InternalTaskDefinition(
        id=str(uuid.uuid4()),
        company_id=actor.company_id,
        title=payload.title,
        description=payload.description,
        creator_admin_id=actor.id,
        responsible_admin_id=responsible.id,
        display_author=payload.display_author,
        assignment_mode=payload.assignment_mode,
        assignee_user_id=assignee_user_id,
        notify_on_completion=payload.notify_on_completion,
        deadline_at_utc=deadline_at_utc,
        schedule_kind=InternalTaskScheduleKind.IMMEDIATE.value,
        timezone_name=settings.app_timezone,
        scheduled_for_utc=None,
        recurrence_mode=None,
        recurrence_config=None,
        cycle_start_at_utc=None,
        cycle_end_mode=None,
        cycle_end_at_utc=None,
        cycle_max_occurrences=None,
        state=InternalTaskDefinitionState.ACTIVE.value,
        generation_not_before_utc=None,
        created_at=now,
        updated_at=now,
    )
    try:
        db.add(definition)
        db.flush()
        result = materialize_internal_task(db, definition, occurrence_at_utc=now)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "definition_id": definition.id,
        "occurrence_id": result.occurrence.id,
        "ticket_id": result.ticket.id,
        "created": result.created,
        "state": definition.state,
        "title": definition.title,
        "assignment_mode": definition.assignment_mode,
        "effective_assignment_mode": result.occurrence.effective_assignment_mode,
        "assignee_user_id": definition.assignee_user_id,
        "effective_assignee_user_id": result.occurrence.effective_assignee_user_id,
        "responsible_admin_id": definition.responsible_admin_id,
        "display_author": definition.display_author,
        "notify_on_completion": definition.notify_on_completion,
        "deadline_at": definition.deadline_at_utc,
        "created_at": definition.created_at,
    }


@router.get("/options")
def internal_task_options(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    actor = _current_admin(db, user)
    users = db.execute(
        select(User)
        .where(User.company_id == actor.company_id)
        .order_by(User.full_name.asc(), User.login.asc())
    ).scalars().all()
    return {
        "current_admin_id": actor.id,
        "app_timezone": settings.app_timezone,
        "assignees": [_user_out(item) for item in users if item.role in {"agent", "trainee"}],
        "admins": [_user_out(item) for item in users if item.role == "admin"],
    }


@router.get("/overview")
def internal_task_overview(
    scope: str = Query(default="all", pattern="^(all|mine)$"),
    limit: int = Query(default=100, ge=1, le=300),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    actor = _current_admin(db, user)
    mine_definition_filter = or_(
        InternalTaskDefinition.creator_admin_id == actor.id,
        InternalTaskDefinition.responsible_admin_id == actor.id,
    )
    mine_occurrence_filter = or_(
        InternalTaskOccurrence.creator_admin_id == actor.id,
        InternalTaskOccurrence.responsible_admin_id == actor.id,
    )

    scheduled_stmt = (
        select(InternalTaskDefinition)
        .join(InternalTaskOccurrence, InternalTaskOccurrence.definition_id == InternalTaskDefinition.id, isouter=True)
        .where(
            and_(
                InternalTaskDefinition.company_id == actor.company_id,
                InternalTaskDefinition.schedule_kind == InternalTaskScheduleKind.SCHEDULED.value,
                InternalTaskDefinition.state == InternalTaskDefinitionState.ACTIVE.value,
                InternalTaskOccurrence.id.is_(None),
            )
        )
        .order_by(InternalTaskDefinition.scheduled_for_utc.asc(), InternalTaskDefinition.created_at.asc())
        .limit(limit)
    )
    if scope == "mine":
        scheduled_stmt = scheduled_stmt.where(mine_definition_filter)

    recurring_stmt = (
        select(InternalTaskDefinition)
        .where(
            and_(
                InternalTaskDefinition.company_id == actor.company_id,
                InternalTaskDefinition.schedule_kind == InternalTaskScheduleKind.RECURRING.value,
                InternalTaskDefinition.state.in_([
                    InternalTaskDefinitionState.ACTIVE.value,
                    InternalTaskDefinitionState.PAUSED.value,
                    InternalTaskDefinitionState.FINISHED.value,
                ]),
            )
        )
        .order_by(InternalTaskDefinition.updated_at.desc(), InternalTaskDefinition.created_at.desc())
        .limit(limit)
    )
    if scope == "mine":
        recurring_stmt = recurring_stmt.where(mine_definition_filter)

    materialized_filters = [
        InternalTaskDefinition.company_id == actor.company_id,
        InternalTaskDefinition.schedule_kind.in_([
            InternalTaskScheduleKind.IMMEDIATE.value,
            InternalTaskScheduleKind.SCHEDULED.value,
            InternalTaskScheduleKind.RECURRING.value,
        ]),
    ]
    if scope == "mine":
        materialized_filters.append(mine_occurrence_filter)

    materialized_rows = db.execute(
        select(InternalTaskDefinition, InternalTaskOccurrence, Ticket)
        .join(InternalTaskOccurrence, InternalTaskOccurrence.definition_id == InternalTaskDefinition.id)
        .join(Ticket, Ticket.id == InternalTaskOccurrence.ticket_id)
        .where(and_(*materialized_filters))
        .order_by(InternalTaskOccurrence.materialized_at_utc.desc(), InternalTaskOccurrence.created_at.desc())
        .limit(limit * 2)
    ).all()
    active: list[dict] = []
    completed: list[dict] = []
    for definition, occurrence, ticket in materialized_rows:
        item = _materialized_task_out(db, definition=definition, occurrence=occurrence, ticket=ticket)
        if ticket.status == TicketStatus.CLOSED.value:
            completed.append(item)
        else:
            active.append(item)
    return {
        "scope": scope,
        "active": active[:limit],
        "scheduled": [_scheduled_definition_out(db, item) for item in db.execute(scheduled_stmt).scalars().all()],
        "completed": completed[:limit],
        "recurring": [_recurring_definition_out(db, item) for item in db.execute(recurring_stmt).scalars().all()],
    }


@router.post("/scheduled")
def create_internal_task_scheduled(
    payload: InternalTaskScheduledPayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    _validate_text(payload)
    actor = _current_admin(db, user)
    responsible = _responsible_admin(db, actor.company_id, payload.responsible_admin_id, actor)
    assignee_user_id = _validate_assignment(payload, db, actor.company_id)
    scheduled_for_utc = _validate_scheduled_for(payload)
    deadline_at_utc = _validate_deadline(payload, app_local_naive=True)
    definition = _create_scheduled_definition(
        db,
        payload=payload,
        actor=actor,
        responsible=responsible,
        assignee_user_id=assignee_user_id,
        scheduled_for_utc=scheduled_for_utc,
        deadline_at_utc=deadline_at_utc,
    )
    return _scheduled_definition_out(db, definition)


@router.patch("/scheduled/{definition_id}")
def update_internal_task_scheduled(
    definition_id: str,
    payload: InternalTaskScheduledPayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    _validate_text(payload)
    actor = _current_admin(db, user)
    definition = db.execute(
        select(InternalTaskDefinition)
        .where(
            and_(
                InternalTaskDefinition.id == definition_id,
                InternalTaskDefinition.company_id == actor.company_id,
                InternalTaskDefinition.schedule_kind == InternalTaskScheduleKind.SCHEDULED.value,
            )
        )
        .limit(1)
    ).scalar_one_or_none()
    if definition is None:
        _error(404, "scheduled_definition_not_found", "Запланированная задача не найдена")
    if definition.state != InternalTaskDefinitionState.ACTIVE.value or _definition_has_occurrence(db, definition.id):
        _error(409, "scheduled_definition_materialized", "Запланированная задача уже материализована")

    responsible = _responsible_admin(db, actor.company_id, payload.responsible_admin_id, actor)
    assignee_user_id = _validate_assignment(payload, db, actor.company_id)
    scheduled_for_utc = _validate_scheduled_for(payload)
    deadline_at_utc = _validate_deadline(payload, app_local_naive=True)

    definition.title = payload.title
    definition.description = payload.description
    definition.assignment_mode = payload.assignment_mode
    definition.assignee_user_id = assignee_user_id
    definition.display_author = payload.display_author
    definition.responsible_admin_id = responsible.id
    definition.notify_on_completion = payload.notify_on_completion
    definition.scheduled_for_utc = scheduled_for_utc
    definition.deadline_at_utc = deadline_at_utc
    definition.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(definition)
    return _scheduled_definition_out(db, definition)


def _recurring_definition(db: Session, company_id: str, definition_id: str) -> InternalTaskDefinition:
    definition = db.execute(
        select(InternalTaskDefinition)
        .where(
            and_(
                InternalTaskDefinition.id == definition_id,
                InternalTaskDefinition.company_id == company_id,
                InternalTaskDefinition.schedule_kind == InternalTaskScheduleKind.RECURRING.value,
            )
        )
        .limit(1)
    ).scalar_one_or_none()
    if definition is None:
        _error(404, "recurring_definition_not_found", "Регулярная задача не найдена")
    return definition


def _materialized_internal_task_row(db: Session, company_id: str, ticket_id: str) -> tuple[InternalTaskDefinition, InternalTaskOccurrence, Ticket]:
    row = db.execute(
        select(InternalTaskDefinition, InternalTaskOccurrence, Ticket)
        .join(InternalTaskOccurrence, InternalTaskOccurrence.definition_id == InternalTaskDefinition.id)
        .join(Ticket, Ticket.id == InternalTaskOccurrence.ticket_id)
        .where(
            and_(
                InternalTaskDefinition.company_id == company_id,
                InternalTaskOccurrence.ticket_id == ticket_id,
                Ticket.company_id == company_id,
            )
        )
        .limit(1)
    ).first()
    if row is None:
        _error(404, "internal_task_ticket_not_found", "Внутренняя задача не найдена")
    return row


def _disable_definition(definition: InternalTaskDefinition) -> None:
    now = datetime.utcnow()
    definition.state = InternalTaskDefinitionState.DISABLED.value
    definition.updated_at = now


def _apply_recurring_payload(
    db: Session,
    *,
    definition: InternalTaskDefinition,
    payload: InternalTaskRecurringPayload,
    actor: User,
) -> None:
    _validate_text(payload)
    responsible = _responsible_admin(db, actor.company_id, payload.responsible_admin_id, actor)
    assignee_user_id = _validate_assignment(payload, db, actor.company_id)
    deadline_at_utc = _validate_deadline(payload, app_local_naive=True)
    (
        recurrence_mode,
        recurrence_config,
        cycle_start_at_utc,
        cycle_end_mode,
        cycle_end_at_utc,
        cycle_max_occurrences,
    ) = _validate_recurrence(payload)

    now = datetime.utcnow()
    edit_boundary = None
    if definition.state == InternalTaskDefinitionState.ACTIVE.value:
        existing_generation_boundary = _utc_naive(definition.generation_not_before_utc)
        edit_boundary = max(
            existing_generation_boundary or now,
            now,
        )

    definition.title = payload.title
    definition.description = payload.description
    definition.assignment_mode = payload.assignment_mode
    definition.assignee_user_id = assignee_user_id
    definition.display_author = payload.display_author
    definition.responsible_admin_id = responsible.id
    definition.notify_on_completion = payload.notify_on_completion
    definition.deadline_at_utc = deadline_at_utc
    definition.recurrence_mode = recurrence_mode
    definition.recurrence_config = recurrence_config
    definition.cycle_start_at_utc = cycle_start_at_utc
    definition.cycle_end_mode = cycle_end_mode
    definition.cycle_end_at_utc = cycle_end_at_utc
    definition.cycle_max_occurrences = cycle_max_occurrences
    if edit_boundary is not None:
        definition.generation_not_before_utc = edit_boundary
    definition.updated_at = now


def _can_resume_recurring(db: Session, definition: InternalTaskDefinition, *, now: datetime) -> None:
    now = _utc_naive(now) or now
    cycle_end_at_utc = _utc_naive(definition.cycle_end_at_utc)
    if definition.cycle_end_mode == CYCLE_END_AT and cycle_end_at_utc is not None and cycle_end_at_utc <= now:
        _error(409, "recurring_end_at_elapsed", "Дата окончания расписания уже прошла")
    if (
        definition.cycle_end_mode == CYCLE_END_AT
        and cycle_end_at_utc is not None
        and _first_recurring_candidate_at_or_after(definition, window_start=now) is None
    ):
        _error(409, "recurring_no_future_occurrence", "В расписании нет будущего создания до даты окончания расписания")
    if (
        definition.cycle_end_mode == CYCLE_END_COUNT
        and definition.cycle_max_occurrences is not None
        and _successful_occurrence_count(db, definition.id) >= definition.cycle_max_occurrences
    ):
        _error(409, "recurring_count_exhausted", "Лимит повторений уже исчерпан")


@router.post("/recurring")
def create_internal_task_recurring(
    payload: InternalTaskRecurringPayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    _validate_text(payload)
    actor = _current_admin(db, user)
    responsible = _responsible_admin(db, actor.company_id, payload.responsible_admin_id, actor)
    assignee_user_id = _validate_assignment(payload, db, actor.company_id)
    deadline_at_utc = _validate_deadline(payload, app_local_naive=True)
    (
        recurrence_mode,
        recurrence_config,
        cycle_start_at_utc,
        cycle_end_mode,
        cycle_end_at_utc,
        cycle_max_occurrences,
    ) = _validate_recurrence(payload)
    definition = _create_recurring_definition(
        db,
        payload=payload,
        actor=actor,
        responsible=responsible,
        assignee_user_id=assignee_user_id,
        deadline_at_utc=deadline_at_utc,
        recurrence_mode=recurrence_mode,
        recurrence_config=recurrence_config,
        cycle_start_at_utc=cycle_start_at_utc,
        cycle_end_mode=cycle_end_mode,
        cycle_end_at_utc=cycle_end_at_utc,
        cycle_max_occurrences=cycle_max_occurrences,
    )
    return _recurring_definition_out(db, definition)


@router.patch("/recurring/{definition_id}")
def update_internal_task_recurring(
    definition_id: str,
    payload: InternalTaskRecurringPayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    actor = _current_admin(db, user)
    definition = _recurring_definition(db, actor.company_id, definition_id)
    _apply_recurring_payload(db, definition=definition, payload=payload, actor=actor)
    db.commit()
    db.refresh(definition)
    return _recurring_definition_out(db, definition)


@router.delete("/scheduled/{definition_id}")
def delete_internal_task_scheduled(
    definition_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    actor = _current_admin(db, user)
    definition = db.execute(
        select(InternalTaskDefinition)
        .where(
            and_(
                InternalTaskDefinition.id == definition_id,
                InternalTaskDefinition.company_id == actor.company_id,
                InternalTaskDefinition.schedule_kind == InternalTaskScheduleKind.SCHEDULED.value,
            )
        )
        .limit(1)
    ).scalar_one_or_none()
    if definition is None:
        _error(404, "scheduled_definition_not_found", "Запланированная задача не найдена")
    if definition.state != InternalTaskDefinitionState.ACTIVE.value or _definition_has_occurrence(db, definition.id):
        _error(409, "scheduled_definition_materialized", "Запланированная задача уже материализована")
    _disable_definition(definition)
    db.commit()
    return {"ok": True}


@router.post("/recurring/{definition_id}/pause")
def pause_internal_task_recurring(
    definition_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    actor = _current_admin(db, user)
    definition = _recurring_definition(db, actor.company_id, definition_id)
    if definition.state != InternalTaskDefinitionState.ACTIVE.value:
        _error(409, "recurring_not_active", "Поставить на паузу можно только активное расписание")
    now = datetime.utcnow()
    definition.state = InternalTaskDefinitionState.PAUSED.value
    definition.generation_not_before_utc = now
    definition.updated_at = now
    db.commit()
    db.refresh(definition)
    return _recurring_definition_out(db, definition)


@router.delete("/recurring/{definition_id}")
def delete_internal_task_recurring(
    definition_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    actor = _current_admin(db, user)
    definition = _recurring_definition(db, actor.company_id, definition_id)
    _disable_definition(definition)
    db.commit()
    return {"ok": True}


@router.post("/recurring/{definition_id}/skip-next")
def skip_next_internal_task_recurring(
    definition_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    actor = _current_admin(db, user)
    definition = _recurring_definition(db, actor.company_id, definition_id)
    if definition.state != InternalTaskDefinitionState.ACTIVE.value:
        _error(409, "recurring_not_active", "Пропустить можно только активное расписание")
    next_at = _next_recurring_at(db, definition)
    if next_at is None:
        _error(409, "recurring_no_next_occurrence", "Следующего создания задачи нет")
    now = datetime.utcnow()
    definition.generation_not_before_utc = (_utc_naive(next_at) or next_at) + timedelta(microseconds=1)
    definition.updated_at = now
    db.commit()
    db.refresh(definition)
    return {
        "ok": True,
        "skipped_occurrence_at": next_at,
        "definition": _recurring_definition_out(db, definition),
    }


@router.post("/recurring/{definition_id}/resume")
def resume_internal_task_recurring(
    definition_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    actor = _current_admin(db, user)
    definition = _recurring_definition(db, actor.company_id, definition_id)
    if definition.state not in {InternalTaskDefinitionState.PAUSED.value, InternalTaskDefinitionState.FINISHED.value}:
        _error(409, "recurring_not_resumable", "Возобновить можно только paused или finished расписание")
    now = datetime.utcnow()
    _can_resume_recurring(db, definition, now=now)
    definition.state = InternalTaskDefinitionState.ACTIVE.value
    definition.generation_not_before_utc = now
    definition.updated_at = now
    db.commit()
    db.refresh(definition)
    return _recurring_definition_out(db, definition)


@router.delete("/materialized/{ticket_id}")
def delete_internal_task_materialized(
    ticket_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    actor = _current_admin(db, user)
    _, _, ticket = _materialized_internal_task_row(db, actor.company_id, ticket_id)
    warnings = _delete_ticket_hard(db, ticket)
    db.commit()
    return {"ok": True, "file_delete_warnings": warnings}
