from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import TicketReview, TicketReviewCycle, TicketReviewCycleState


TERMINAL_STATES = {
    TicketReviewCycleState.COMPLETED.value,
    TicketReviewCycleState.CANCELLED.value,
}
TEXT_ALLOWED_STATES = {
    TicketReviewCycleState.AWAITING_TEXT.value,
    TicketReviewCycleState.AWAITING_TEXT_AFTER_CAPTION.value,
}


def _now() -> datetime:
    return datetime.now(UTC)


def _ensure_not_terminal(cycle: TicketReviewCycle, *, action: str) -> None:
    if cycle.state in TERMINAL_STATES:
        raise ValueError(f"cannot {action} for terminal review cycle")


def create_review_cycle(
    db: Session,
    *,
    ticket_id: str,
    agent_id: str,
    client_id: str,
) -> TicketReviewCycle:
    cycle = TicketReviewCycle(
        id=str(uuid.uuid4()),
        ticket_id=ticket_id,
        agent_id=agent_id,
        client_id=client_id,
        state=TicketReviewCycleState.OFFERED.value,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(cycle)
    db.flush()
    return cycle


def save_review_rating(
    db: Session,
    *,
    cycle: TicketReviewCycle,
    rating: int,
    agent_name_snapshot: str,
    client_name_snapshot: str,
    ticket_subject_snapshot: str,
    rated_at: datetime | None = None,
) -> TicketReview:
    _ensure_not_terminal(cycle, action="save rating")

    if rating < 1 or rating > 5:
        raise ValueError("rating must be between 1 and 5")

    if cycle.review_id:
        existing_review = db.get(TicketReview, cycle.review_id)
        if existing_review is None:
            raise ValueError(f"review cycle {cycle.id} points to missing review {cycle.review_id}")
        return existing_review

    current_review = db.execute(
        select(TicketReview)
        .where(
            TicketReview.ticket_id == cycle.ticket_id,
            TicketReview.agent_id == cycle.agent_id,
            TicketReview.is_current.is_(True),
        )
        .limit(1)
    ).scalar_one_or_none()

    next_version_number = 1
    previous_version_id = None
    if current_review is not None:
        current_review.is_current = False
        current_review.updated_at = _now()
        previous_version_id = current_review.id
        next_version_number = current_review.version_number + 1
        db.flush()

    now = _now()
    review = TicketReview(
        id=str(uuid.uuid4()),
        ticket_id=cycle.ticket_id,
        agent_id=cycle.agent_id,
        client_id=cycle.client_id,
        cycle_id=cycle.id,
        previous_version_id=previous_version_id,
        version_number=next_version_number,
        agent_name_snapshot=agent_name_snapshot,
        client_name_snapshot=client_name_snapshot,
        ticket_subject_snapshot=ticket_subject_snapshot,
        rating=rating,
        review_text=None,
        rated_at=rated_at or now,
        text_updated_at=None,
        is_current=True,
        created_at=now,
        updated_at=now,
    )
    db.add(review)
    db.flush()

    cycle.review_id = review.id
    cycle.state = TicketReviewCycleState.AWAITING_TEXT.value
    cycle.updated_at = _now()
    db.flush()
    return review


def save_review_text(
    db: Session,
    *,
    cycle: TicketReviewCycle,
    review_text: str | None,
    text_updated_at: datetime | None = None,
) -> TicketReview:
    if cycle.state not in TEXT_ALLOWED_STATES:
        raise ValueError("review cycle is not awaiting text")

    if not cycle.review_id:
        raise ValueError("review cycle has no rating review")

    review = db.get(TicketReview, cycle.review_id)
    if review is None:
        raise ValueError(f"review cycle {cycle.id} points to missing review {cycle.review_id}")

    updated_at = text_updated_at or _now()
    review.review_text = review_text
    review.text_updated_at = updated_at
    review.updated_at = updated_at
    db.flush()
    return review


def save_review_caption(
    db: Session,
    *,
    cycle: TicketReviewCycle,
    caption: str,
    text_updated_at: datetime | None = None,
) -> TicketReview:
    if cycle.state not in TEXT_ALLOWED_STATES:
        raise ValueError("review cycle is not awaiting text")
    if not cycle.review_id:
        raise ValueError("review cycle has no rating review")

    review = save_review_text(db, cycle=cycle, review_text=caption, text_updated_at=text_updated_at)
    cycle.state = TicketReviewCycleState.AWAITING_TEXT_AFTER_CAPTION.value
    cycle.updated_at = review.text_updated_at or _now()
    db.flush()
    return review


def set_review_current_message_id(
    db: Session,
    *,
    cycle: TicketReviewCycle,
    message_id: str,
) -> TicketReviewCycle:
    _ensure_not_terminal(cycle, action="set current message")

    cycle.review_current_message_id = message_id
    cycle.updated_at = _now()
    db.flush()
    return cycle


def start_review_rating_cycle(db: Session, *, cycle: TicketReviewCycle) -> TicketReviewCycle:
    _ensure_not_terminal(cycle, action="start rating")
    if cycle.state != TicketReviewCycleState.OFFERED.value:
        raise ValueError("review cycle is not offered")
    if cycle.review_id:
        raise ValueError("review cycle already has a review")

    cycle.state = TicketReviewCycleState.AWAITING_RATING.value
    cycle.updated_at = _now()
    db.flush()
    return cycle


def return_review_cycle_to_offered(db: Session, *, cycle: TicketReviewCycle) -> TicketReviewCycle:
    _ensure_not_terminal(cycle, action="return to offered")
    if cycle.state != TicketReviewCycleState.AWAITING_RATING.value:
        raise ValueError("review cycle is not awaiting rating")
    if cycle.review_id:
        raise ValueError("review cycle already has a review")

    cycle.state = TicketReviewCycleState.OFFERED.value
    cycle.review_current_message_id = None
    cycle.updated_at = _now()
    db.flush()
    return cycle


def clear_review_current_message_id(db: Session, *, cycle: TicketReviewCycle) -> TicketReviewCycle:
    cycle.review_current_message_id = None
    cycle.updated_at = _now()
    db.flush()
    return cycle


def complete_review_cycle(
    db: Session,
    *,
    cycle: TicketReviewCycle,
    completed_at: datetime | None = None,
    preserve_current_message_id: bool = False,
) -> TicketReviewCycle:
    if cycle.state == TicketReviewCycleState.COMPLETED.value:
        return cycle
    if cycle.state == TicketReviewCycleState.CANCELLED.value:
        raise ValueError("cancelled review cycle cannot be completed")

    finished_at = completed_at or _now()
    cycle.state = TicketReviewCycleState.COMPLETED.value
    cycle.completed_at = finished_at
    if not preserve_current_message_id:
        cycle.review_current_message_id = None
    cycle.updated_at = finished_at
    db.flush()
    return cycle


def cancel_review_cycle(
    db: Session,
    *,
    cycle: TicketReviewCycle,
    cancelled_at: datetime | None = None,
) -> TicketReviewCycle:
    if cycle.state == TicketReviewCycleState.CANCELLED.value:
        return cycle
    if cycle.state == TicketReviewCycleState.COMPLETED.value:
        raise ValueError("completed review cycle cannot be cancelled")

    finished_at = cancelled_at or _now()
    cycle.state = TicketReviewCycleState.CANCELLED.value
    cycle.cancelled_at = finished_at
    cycle.review_current_message_id = None
    cycle.updated_at = finished_at
    db.flush()
    return cycle
