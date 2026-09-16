from __future__ import annotations

import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import and_, or_, select
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


def _validate_assignment(payload: InternalTaskCreateNowPayload | InternalTaskScheduledPayload, db: Session, company_id: str) -> str | None:
    if payload.assignment_mode == InternalTaskAssignmentMode.DIRECT.value:
        return _direct_assignee(db, company_id, payload.assignee_user_id).id
    if payload.assignment_mode == InternalTaskAssignmentMode.DISTRIBUTION.value:
        if payload.assignee_user_id is not None:
            _error(422, "assignee_not_allowed", "При распределении assignee_user_id должен быть пустым")
        return None
    _error(422, "unsupported_assignment_mode", "Неподдерживаемый режим назначения")


def _validate_deadline(
    payload: InternalTaskCreateNowPayload | InternalTaskScheduledPayload,
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


def _validate_text(payload: InternalTaskCreateNowPayload | InternalTaskScheduledPayload) -> None:
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

    materialized_filters = [
        InternalTaskDefinition.company_id == actor.company_id,
        InternalTaskDefinition.schedule_kind.in_([
            InternalTaskScheduleKind.IMMEDIATE.value,
            InternalTaskScheduleKind.SCHEDULED.value,
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
        "recurring": [],
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
