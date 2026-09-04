from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, is_admin
from ..db import get_db
from ..models import IntegrationLog


router = APIRouter(prefix="/api/v1/management/integrations", tags=["management-integrations"])

EventType = Literal["task_create", "telegram_send"]
Result = Literal["success", "duplicate", "error"]


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


def _item_out(row: IntegrationLog) -> dict:
    return {
        "id": row.id,
        "created_at": row.created_at,
        "source": row.source,
        "event_type": row.event_type,
        "result": row.result,
        "code": row.code,
        "description": row.description,
        "deal_id": row.deal_id,
        "task_id": row.task_id,
        "bitrix_id": row.bitrix_id,
        "call_time": row.call_time,
        "message_text": row.message_text,
        "telegram_message_id": row.telegram_message_id,
    }


def _filters(
    *,
    event_type: EventType | None,
    result: Result | None,
    created_from: datetime | None,
    created_to: datetime | None,
) -> list:
    clauses = []
    if event_type is not None:
        clauses.append(IntegrationLog.event_type == event_type)
    if result is not None:
        clauses.append(IntegrationLog.result == result)
    created_from = _normalize_datetime(created_from)
    created_to = _normalize_datetime(created_to)
    if created_from is not None:
        clauses.append(IntegrationLog.created_at >= created_from)
    if created_to is not None:
        clauses.append(IntegrationLog.created_at < created_to)
    return clauses


@router.get("/logs")
def list_integration_logs(
    event_type: Annotated[EventType | None, Query()] = None,
    result: Annotated[Result | None, Query()] = None,
    created_from: Annotated[datetime | None, Query()] = None,
    created_to: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    clauses = _filters(
        event_type=event_type,
        result=result,
        created_from=created_from,
        created_to=created_to,
    )
    total = db.execute(select(func.count()).select_from(IntegrationLog).where(*clauses)).scalar_one()
    rows = db.execute(
        select(IntegrationLog)
        .where(*clauses)
        .order_by(IntegrationLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).scalars().all()
    return {
        "items": [_item_out(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
