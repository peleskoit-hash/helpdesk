from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, is_admin, is_readonly_chiki_piki, is_support_user
from ..config import settings
from ..db import get_db
from ..models import TicketReview, User
from ..schemas import (
    ReviewCurrentRowOut,
    ReviewEmployeeOut,
    ReviewHistoryResponse,
    ReviewHistoryRowOut,
    ReviewListResponse,
)


router = APIRouter(prefix="/api/v1/reviews", tags=["reviews"])

SUPPORT_ROLES = {"agent", "trainee"}


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _require_review_access(user: CurrentUser) -> None:
    if is_readonly_chiki_piki(user) or not (is_admin(user) or is_support_user(user)):
        _error(403, "forbidden", "Нет доступа к отзывам")


def _day_start_utc(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=ZoneInfo(settings.app_timezone)).astimezone(timezone.utc)


def _current_review_filters(date_from: date | None, date_to: date | None) -> list:
    clauses = [
        TicketReview.is_current.is_(True),
    ]
    if date_from is not None:
        clauses.append(TicketReview.rated_at >= _day_start_utc(date_from))
    if date_to is not None:
        clauses.append(TicketReview.rated_at < _day_start_utc(date_to + timedelta(days=1)))
    return clauses


def _employee_or_404(db: Session, employee_id: str) -> User:
    employee = db.execute(
        select(User).where(
            User.id == employee_id,
            User.role.in_(list(SUPPORT_ROLES)),
        )
    ).scalar_one_or_none()
    if employee is None:
        _error(404, "employee_not_found", "Сотрудник не найден")
    return employee


def _row_out(row: TicketReview, history_pairs: set[tuple[str, str]], agent_full_name: str | None) -> ReviewCurrentRowOut:
    return ReviewCurrentRowOut(
        review_id=row.id,
        ticket_id=row.ticket_id,
        agent_id=row.agent_id,
        agent_full_name=agent_full_name,
        rated_at=row.rated_at,
        client_name_snapshot=row.client_name_snapshot,
        ticket_subject_snapshot=row.ticket_subject_snapshot,
        rating=row.rating,
        review_text=row.review_text,
        version_number=row.version_number,
        has_history=(row.ticket_id, row.agent_id) in history_pairs,
    )


@router.get("/employees", response_model=list[ReviewEmployeeOut])
def list_review_employees(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    if not is_admin(user):
        _error(403, "admin_only", "Доступ только для администратора")
    rows = db.execute(
        select(User)
        .where(
            User.role.in_(list(SUPPORT_ROLES)),
            (
                User.is_active.is_(True)
                | exists(
                    select(TicketReview.id)
                    .where(TicketReview.agent_id == User.id)
                    .limit(1)
                )
            ),
        )
        .order_by(User.full_name.asc())
    ).scalars().all()
    return [
        ReviewEmployeeOut(
            employee_id=row.id,
            display_name=row.full_name,
            role=row.role,
            is_active=bool(row.is_active),
        )
        for row in rows
    ]


@router.get("", response_model=ReviewListResponse)
def list_reviews(
    employee_id: Annotated[str | None, Query()] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 30,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_review_access(user)

    if is_admin(user):
        selected_employee_id = employee_id
        if selected_employee_id:
            _employee_or_404(db, selected_employee_id)
    else:
        selected_employee_id = user.id

    employee_filter = (
        TicketReview.agent_id == selected_employee_id
        if selected_employee_id
        else TicketReview.agent_id.in_(select(User.id).where(User.role.in_(list(SUPPORT_ROLES))))
    )
    clauses = [employee_filter, *_current_review_filters(date_from, date_to)]
    total_count = int(db.execute(select(func.count()).select_from(TicketReview).where(*clauses)).scalar_one() or 0)
    average = db.execute(select(func.avg(TicketReview.rating)).where(*clauses)).scalar_one()
    average_rating = round(float(average), 2) if average is not None else None
    min_rated_at = db.execute(
        select(func.min(TicketReview.rated_at)).where(employee_filter)
    ).scalar_one()
    min_review_date = None
    if min_rated_at is not None:
        if min_rated_at.tzinfo is None:
            min_rated_at = min_rated_at.replace(tzinfo=timezone.utc)
        min_review_date = min_rated_at.astimezone(ZoneInfo(settings.app_timezone)).date()

    pages = max(1, (total_count + page_size - 1) // page_size)
    if page > pages:
        page = pages
    offset = (page - 1) * page_size
    rows = db.execute(
        select(TicketReview)
        .where(*clauses)
        .order_by(TicketReview.rated_at.desc(), TicketReview.id.desc())
        .offset(offset)
        .limit(page_size)
    ).scalars().all()

    employee_names = dict(db.execute(
        select(User.id, User.full_name).where(User.id.in_({row.agent_id for row in rows}))
    ).all()) if rows else {}

    history_pairs: set[tuple[str, str]] = set()
    if rows:
        pair_clauses = [
            and_(
                TicketReview.ticket_id == row.ticket_id,
                TicketReview.agent_id == row.agent_id,
                TicketReview.is_current.is_(False),
            )
            for row in rows
        ]
        history_rows = db.execute(
            select(TicketReview.ticket_id, TicketReview.agent_id)
            .where(or_(*pair_clauses))
        ).all()
        history_pairs = {(ticket_id, agent_id) for ticket_id, agent_id in history_rows}

    return ReviewListResponse(
        items=[_row_out(row, history_pairs, employee_names.get(row.agent_id)) for row in rows],
        total_count=total_count,
        average_rating=average_rating,
        min_review_date=min_review_date,
        page=page,
        pages=pages,
        page_size=page_size,
    )


@router.get("/{review_id}/history", response_model=ReviewHistoryResponse)
def get_review_history(
    review_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_review_access(user)
    current_review = db.execute(
        select(TicketReview).where(
            TicketReview.id == review_id,
            TicketReview.is_current.is_(True),
        )
    ).scalar_one_or_none()
    if current_review is None:
        _error(404, "review_not_found", "Отзыв не найден")
    if not is_admin(user) and current_review.agent_id != user.id:
        _error(403, "forbidden", "Нет доступа к отзыву")

    rows = db.execute(
        select(TicketReview)
        .where(
            TicketReview.ticket_id == current_review.ticket_id,
            TicketReview.agent_id == current_review.agent_id,
            TicketReview.is_current.is_(False),
        )
        .order_by(TicketReview.version_number.desc(), TicketReview.rated_at.desc(), TicketReview.id.desc())
    ).scalars().all()
    return ReviewHistoryResponse(
        items=[
            ReviewHistoryRowOut(
                review_id=row.id,
                rated_at=row.rated_at,
                rating=row.rating,
                review_text=row.review_text,
                version_number=row.version_number,
            )
            for row in rows
        ]
    )
