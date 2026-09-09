from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, is_admin
from ..config import settings
from ..db import get_db
from ..models import Ticket, TicketFeedback
from ..schemas import ManagementFeedbackListResponse, ManagementFeedbackRowOut


router = APIRouter(prefix="/api/v1/management/feedback", tags=["management-feedback"])


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _require_admin(user: CurrentUser) -> None:
    if not is_admin(user):
        _error(403, "admin_only", "Доступ только для администратора")


def _day_start_utc(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=ZoneInfo(settings.app_timezone)).astimezone(timezone.utc)


def _filters(*, date_from: date | None, date_to: date | None, query: str | None) -> list:
    clauses = []
    if date_from is not None:
        clauses.append(TicketFeedback.submitted_at >= _day_start_utc(date_from))
    if date_to is not None:
        clauses.append(TicketFeedback.submitted_at < _day_start_utc(date_to + timedelta(days=1)))
    q = (query or "").strip()
    if q:
        pattern = f"%{q}%"
        clauses.append(
            or_(
                TicketFeedback.client_name_snapshot.ilike(pattern),
                TicketFeedback.ticket_title_snapshot.ilike(pattern),
                TicketFeedback.text.ilike(pattern),
                cast(Ticket.ticket_number, String).ilike(pattern),
            )
        )
    return clauses


def _row_out(feedback: TicketFeedback, ticket: Ticket | None) -> ManagementFeedbackRowOut:
    return ManagementFeedbackRowOut(
        feedback_id=feedback.id,
        submitted_at=feedback.submitted_at,
        client_name_snapshot=feedback.client_name_snapshot,
        ticket_title_snapshot=feedback.ticket_title_snapshot,
        text=feedback.text,
        ticket_available=ticket is not None,
        ticket_id=feedback.ticket_id,
        ticket_number=ticket.ticket_number if ticket is not None else None,
        workflow_type=(ticket.workflow_type if ticket is not None else None),
    )


@router.get("", response_model=ManagementFeedbackListResponse)
def list_feedback(
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    query: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 30,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)

    outer = TicketFeedback.__table__.outerjoin(Ticket.__table__, Ticket.id == TicketFeedback.ticket_id)
    clauses = _filters(date_from=date_from, date_to=date_to, query=query)
    total_count = int(db.execute(select(func.count()).select_from(outer).where(*clauses)).scalar_one() or 0)
    min_submitted_at = db.execute(select(func.min(TicketFeedback.submitted_at))).scalar_one()
    min_feedback_date = None
    if min_submitted_at is not None:
        if min_submitted_at.tzinfo is None:
            min_submitted_at = min_submitted_at.replace(tzinfo=timezone.utc)
        min_feedback_date = min_submitted_at.astimezone(ZoneInfo(settings.app_timezone)).date()

    pages = max(1, (total_count + page_size - 1) // page_size)
    if page > pages:
        page = pages
    offset = (page - 1) * page_size

    rows = db.execute(
        select(TicketFeedback, Ticket)
        .select_from(outer)
        .where(*clauses)
        .order_by(TicketFeedback.submitted_at.desc(), TicketFeedback.id.desc())
        .offset(offset)
        .limit(page_size)
    ).all()

    return ManagementFeedbackListResponse(
        items=[_row_out(feedback, ticket) for feedback, ticket in rows],
        total_count=total_count,
        min_feedback_date=min_feedback_date,
        page=page,
        pages=pages,
        page_size=page_size,
    )
