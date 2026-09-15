from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, is_admin
from ..config import settings
from ..db import get_db
from ..models import (
    InternalTaskAssignmentMode,
    InternalTaskDefinition,
    InternalTaskDefinitionState,
    InternalTaskScheduleKind,
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


def _validate_assignment(payload: InternalTaskCreateNowPayload, db: Session, company_id: str) -> str | None:
    if payload.assignment_mode == InternalTaskAssignmentMode.DIRECT.value:
        return _direct_assignee(db, company_id, payload.assignee_user_id).id
    if payload.assignment_mode == InternalTaskAssignmentMode.DISTRIBUTION.value:
        if payload.assignee_user_id is not None:
            _error(422, "assignee_not_allowed", "При распределении assignee_user_id должен быть пустым")
        return None
    _error(422, "unsupported_assignment_mode", "Неподдерживаемый режим назначения")


def _validate_deadline(payload: InternalTaskCreateNowPayload) -> datetime | None:
    if payload.deadline_enabled and payload.deadline_at is None:
        _error(422, "deadline_required", "Для включённого дедлайна нужен deadline_at")
    if not payload.deadline_enabled and payload.deadline_at is not None:
        _error(422, "deadline_not_allowed", "При выключенном дедлайне deadline_at должен быть пустым")
    return _normalize_datetime(payload.deadline_at)


def _validate_text(payload: InternalTaskCreateNowPayload) -> None:
    if not payload.title:
        _error(422, "title_required", "Название задачи обязательно")
    if len(payload.title) > 255:
        _error(422, "title_too_long", "Название задачи слишком длинное")
    if not payload.description:
        _error(422, "description_required", "Описание задачи обязательно")


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
