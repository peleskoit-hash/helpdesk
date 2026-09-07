from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import Client, Company, Ticket, TicketReview, TicketReviewCycleState, User
from api.app.services import schema_evolution
from api.app.services.ticket_reviews import (
    cancel_review_cycle,
    clear_review_current_message_id,
    complete_review_cycle,
    create_review_cycle,
    return_review_cycle_to_offered,
    save_review_caption,
    save_review_rating,
    save_review_text,
    set_review_current_message_id,
    start_review_rating_cycle,
)


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = Session()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def review_context(session):
    company = Company(id=str(uuid.uuid4()), name="ACME")
    client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Client One")
    agent = User(id=str(uuid.uuid4()), company_id=company.id, full_name="Agent One", role="agent")
    other_agent = User(id=str(uuid.uuid4()), company_id=company.id, full_name="Agent Two", role="agent")
    ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        assignee_id=agent.id,
        subject="Printer is tired",
    )
    session.add_all([company, client, agent, other_agent, ticket])
    session.commit()
    return {
        "company": company,
        "client": client,
        "agent": agent,
        "other_agent": other_agent,
        "ticket": ticket,
    }


def _create_cycle(session, context, *, agent_id: str | None = None):
    return create_review_cycle(
        session,
        ticket_id=context["ticket"].id,
        agent_id=agent_id or context["agent"].id,
        client_id=context["client"].id,
    )


def _save_rating(session, cycle, rating: int, *, rated_at: datetime | None = None):
    return save_review_rating(
        session,
        cycle=cycle,
        rating=rating,
        agent_name_snapshot="Agent Snapshot",
        client_name_snapshot="Client Snapshot",
        ticket_subject_snapshot="Subject Snapshot",
        rated_at=rated_at,
    )


def _review_count(session) -> int:
    return int(session.execute(select(func.count()).select_from(TicketReview)).scalar_one())


def test_create_review_cycle_starts_offered(session, review_context):
    cycle = _create_cycle(session, review_context)

    assert cycle.state == TicketReviewCycleState.OFFERED.value
    assert cycle.ticket_id == review_context["ticket"].id
    assert cycle.agent_id == review_context["agent"].id
    assert cycle.client_id == review_context["client"].id
    assert cycle.review_id is None


@pytest.mark.parametrize("rating", [1, 5])
def test_save_review_rating_accepts_boundary_values(session, review_context, rating):
    cycle = _create_cycle(session, review_context)

    review = _save_rating(session, cycle, rating)

    assert review.rating == rating
    assert review.review_text is None
    assert cycle.review_id == review.id
    assert cycle.state == TicketReviewCycleState.AWAITING_TEXT.value


@pytest.mark.parametrize("rating", [0, 6])
def test_save_review_rating_rejects_out_of_range_values(session, review_context, rating):
    cycle = _create_cycle(session, review_context)

    with pytest.raises(ValueError, match="rating must be between 1 and 5"):
        _save_rating(session, cycle, rating)


def test_first_rating_for_pair_creates_first_current_version(session, review_context):
    cycle = _create_cycle(session, review_context)
    rated_at = datetime(2026, 1, 1, 12, 0, 0)

    review = _save_rating(session, cycle, 4, rated_at=rated_at)

    assert review.version_number == 1
    assert review.is_current is True
    assert review.previous_version_id is None
    assert review.rated_at == rated_at


def test_new_rating_for_same_pair_moves_current_to_history(session, review_context):
    first_cycle = _create_cycle(session, review_context)
    first_review = _save_rating(session, first_cycle, 2)
    second_cycle = _create_cycle(session, review_context)

    second_review = _save_rating(session, second_cycle, 5)
    session.refresh(first_review)

    assert first_review.is_current is False
    assert second_review.is_current is True
    assert second_review.version_number == 2
    assert second_review.previous_version_id == first_review.id


def test_new_review_does_not_inherit_old_review_text(session, review_context):
    first_cycle = _create_cycle(session, review_context)
    first_review = _save_rating(session, first_cycle, 3)
    save_review_text(session, cycle=first_cycle, review_text="old text")
    second_cycle = _create_cycle(session, review_context)

    second_review = _save_rating(session, second_cycle, 4)
    session.refresh(first_review)

    assert first_review.review_text == "old text"
    assert second_review.review_text is None


def test_other_agent_has_independent_current_version_for_same_ticket(session, review_context):
    first_cycle = _create_cycle(session, review_context)
    first_review = _save_rating(session, first_cycle, 5)
    other_cycle = _create_cycle(session, review_context, agent_id=review_context["other_agent"].id)

    other_review = _save_rating(session, other_cycle, 1)
    session.refresh(first_review)

    assert first_review.is_current is True
    assert other_review.is_current is True
    assert other_review.version_number == 1
    assert other_review.previous_version_id is None


def test_repeat_save_review_rating_for_same_cycle_is_idempotent(session, review_context):
    cycle = _create_cycle(session, review_context)
    first_review = _save_rating(session, cycle, 1)

    second_result = save_review_rating(
        session,
        cycle=cycle,
        rating=5,
        agent_name_snapshot="Changed Agent",
        client_name_snapshot="Changed Client",
        ticket_subject_snapshot="Changed Subject",
    )

    assert second_result.id == first_review.id
    assert second_result.rating == 1
    assert second_result.agent_name_snapshot == "Agent Snapshot"
    assert _review_count(session) == 1


def test_save_review_text_replaces_text_and_keeps_rating_timestamp(session, review_context):
    cycle = _create_cycle(session, review_context)
    rated_at = datetime(2026, 1, 1, 12, 0, 0)
    first_text_updated_at = rated_at + timedelta(minutes=5)
    second_text_updated_at = rated_at + timedelta(minutes=10)
    review = _save_rating(session, cycle, 4, rated_at=rated_at)

    save_review_text(session, cycle=cycle, review_text="line 1\nline 2", text_updated_at=first_text_updated_at)
    save_review_text(session, cycle=cycle, review_text="replacement\ntext", text_updated_at=second_text_updated_at)
    session.refresh(review)

    assert review.review_text == "replacement\ntext"
    assert review.rated_at == rated_at
    assert review.text_updated_at == second_text_updated_at
    assert _review_count(session) == 1


def test_cycle_message_helpers_complete_and_cancel_preserve_review_data(session, review_context):
    cycle = _create_cycle(session, review_context)
    review = _save_rating(session, cycle, 5)
    save_review_text(session, cycle=cycle, review_text="keep me")
    set_review_current_message_id(session, cycle=cycle, message_id="123")

    cancel_review_cycle(session, cycle=cycle, cancelled_at=datetime(2026, 1, 1, 13, 0, 0))
    session.refresh(review)

    assert cycle.state == TicketReviewCycleState.CANCELLED.value
    assert cycle.review_current_message_id is None
    assert review.rating == 5
    assert review.review_text == "keep me"

    complete_cycle = _create_cycle(session, review_context)
    complete_review = _save_rating(session, complete_cycle, 4)
    set_review_current_message_id(session, cycle=complete_cycle, message_id="456")
    clear_review_current_message_id(session, cycle=complete_cycle)
    set_review_current_message_id(session, cycle=complete_cycle, message_id="789")

    complete_review_cycle(session, cycle=complete_cycle, completed_at=datetime(2026, 1, 1, 14, 0, 0))
    session.refresh(complete_review)

    assert complete_cycle.state == TicketReviewCycleState.COMPLETED.value
    assert complete_cycle.review_current_message_id is None
    assert complete_review.rating == 4


def test_complete_review_cycle_can_preserve_current_message_id(session, review_context):
    cycle = _create_cycle(session, review_context)
    _save_rating(session, cycle, 5)
    set_review_current_message_id(session, cycle=cycle, message_id="thank-you-message")

    complete_review_cycle(session, cycle=cycle, preserve_current_message_id=True)

    assert cycle.state == TicketReviewCycleState.COMPLETED.value
    assert cycle.review_current_message_id == "thank-you-message"


def test_save_review_caption_replaces_text_and_moves_to_caption_state(session, review_context):
    cycle = _create_cycle(session, review_context)
    review = _save_rating(session, cycle, 4)
    first_at = datetime(2026, 1, 1, 12, 5, 0)
    second_at = datetime(2026, 1, 1, 12, 10, 0)

    save_review_caption(session, cycle=cycle, caption="first caption", text_updated_at=first_at)
    save_review_caption(session, cycle=cycle, caption="second caption", text_updated_at=second_at)
    session.refresh(review)

    assert cycle.state == TicketReviewCycleState.AWAITING_TEXT_AFTER_CAPTION.value
    assert review.review_text == "second caption"
    assert review.text_updated_at == second_at


def test_save_review_caption_requires_existing_review(session, review_context):
    cycle = _create_cycle(session, review_context)
    cycle.state = TicketReviewCycleState.AWAITING_TEXT.value

    with pytest.raises(ValueError, match="has no rating review"):
        save_review_caption(session, cycle=cycle, caption="caption")


def test_new_cycle_without_rating_does_not_change_previous_current_review(session, review_context):
    first_cycle = _create_cycle(session, review_context)
    first_review = _save_rating(session, first_cycle, 5)

    _create_cycle(session, review_context)
    session.flush()
    session.refresh(first_review)

    assert first_review.is_current is True
    assert _review_count(session) == 1


def test_unique_current_invariant_is_enforced_by_database(session, review_context):
    cycle = _create_cycle(session, review_context)
    first_review = _save_rating(session, cycle, 5)
    duplicate_cycle = _create_cycle(session, review_context)
    duplicate = TicketReview(
        id=str(uuid.uuid4()),
        ticket_id=first_review.ticket_id,
        agent_id=first_review.agent_id,
        client_id=first_review.client_id,
        cycle_id=duplicate_cycle.id,
        previous_version_id=None,
        version_number=2,
        agent_name_snapshot="Agent",
        client_name_snapshot="Client",
        ticket_subject_snapshot="Subject",
        rating=4,
        rated_at=datetime.now(UTC),
        is_current=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(duplicate)

    with pytest.raises(IntegrityError):
        session.flush()


def test_cancelled_cycle_rejects_rating_without_creating_review(session, review_context):
    cycle = _create_cycle(session, review_context)
    cancel_review_cycle(session, cycle=cycle)

    with pytest.raises(ValueError, match="terminal review cycle"):
        _save_rating(session, cycle, 5)

    assert cycle.review_id is None
    assert _review_count(session) == 0


def test_completed_cycle_rejects_rating_without_creating_review(session, review_context):
    cycle = _create_cycle(session, review_context)
    complete_review_cycle(session, cycle=cycle)

    with pytest.raises(ValueError, match="terminal review cycle"):
        _save_rating(session, cycle, 5)

    assert cycle.review_id is None
    assert _review_count(session) == 0


@pytest.mark.parametrize(
    ("terminal_action", "terminal_state"),
    [
        (cancel_review_cycle, TicketReviewCycleState.CANCELLED.value),
        (complete_review_cycle, TicketReviewCycleState.COMPLETED.value),
    ],
)
def test_terminal_cycle_rejects_text_update_and_preserves_existing_text(
    session,
    review_context,
    terminal_action,
    terminal_state,
):
    cycle = _create_cycle(session, review_context)
    review = _save_rating(session, cycle, 4)
    save_review_text(session, cycle=cycle, review_text="original text")
    terminal_action(session, cycle=cycle)

    with pytest.raises(ValueError, match="not awaiting text"):
        save_review_text(session, cycle=cycle, review_text="changed text")
    session.refresh(review)

    assert cycle.state == terminal_state
    assert review.review_text == "original text"
    assert _review_count(session) == 1


@pytest.mark.parametrize("disallowed_state", ["offered", "awaiting_rating"])
def test_save_review_text_requires_text_waiting_state(session, review_context, disallowed_state):
    cycle = _create_cycle(session, review_context)
    cycle.state = disallowed_state

    with pytest.raises(ValueError, match="not awaiting text"):
        save_review_text(session, cycle=cycle, review_text="text")


def test_set_review_current_message_id_rejects_terminal_cycles(session, review_context):
    cancelled_cycle = _create_cycle(session, review_context)
    cancel_review_cycle(session, cycle=cancelled_cycle)

    with pytest.raises(ValueError, match="terminal review cycle"):
        set_review_current_message_id(session, cycle=cancelled_cycle, message_id="cancelled-message")

    completed_cycle = _create_cycle(session, review_context)
    complete_review_cycle(session, cycle=completed_cycle)

    with pytest.raises(ValueError, match="terminal review cycle"):
        set_review_current_message_id(session, cycle=completed_cycle, message_id="completed-message")


def test_completed_cycle_cannot_be_cancelled(session, review_context):
    cycle = _create_cycle(session, review_context)
    completed_at = datetime(2026, 1, 1, 12, 0, 0)
    complete_review_cycle(session, cycle=cycle, completed_at=completed_at)

    with pytest.raises(ValueError, match="cannot be cancelled"):
        cancel_review_cycle(session, cycle=cycle, cancelled_at=datetime(2026, 1, 1, 13, 0, 0))

    assert cycle.state == TicketReviewCycleState.COMPLETED.value
    assert cycle.completed_at == completed_at
    assert cycle.cancelled_at is None


def test_cancelled_cycle_cannot_be_completed(session, review_context):
    cycle = _create_cycle(session, review_context)
    cancelled_at = datetime(2026, 1, 1, 12, 0, 0)
    cancel_review_cycle(session, cycle=cycle, cancelled_at=cancelled_at)

    with pytest.raises(ValueError, match="cannot be completed"):
        complete_review_cycle(session, cycle=cycle, completed_at=datetime(2026, 1, 1, 13, 0, 0))

    assert cycle.state == TicketReviewCycleState.CANCELLED.value
    assert cycle.cancelled_at == cancelled_at
    assert cycle.completed_at is None


def test_repeated_complete_keeps_original_completed_at(session, review_context):
    cycle = _create_cycle(session, review_context)
    first_completed_at = datetime(2026, 1, 1, 12, 0, 0)
    second_completed_at = datetime(2026, 1, 1, 13, 0, 0)

    complete_review_cycle(session, cycle=cycle, completed_at=first_completed_at)
    complete_review_cycle(session, cycle=cycle, completed_at=second_completed_at)

    assert cycle.completed_at == first_completed_at


def test_repeated_cancel_keeps_original_cancelled_at(session, review_context):
    cycle = _create_cycle(session, review_context)
    first_cancelled_at = datetime(2026, 1, 1, 12, 0, 0)
    second_cancelled_at = datetime(2026, 1, 1, 13, 0, 0)

    cancel_review_cycle(session, cycle=cycle, cancelled_at=first_cancelled_at)
    cancel_review_cycle(session, cycle=cycle, cancelled_at=second_cancelled_at)

    assert cycle.cancelled_at == first_cancelled_at


def test_return_review_cycle_to_offered_from_awaiting_rating(session, review_context):
    cycle = _create_cycle(session, review_context)
    start_review_rating_cycle(session, cycle=cycle)
    set_review_current_message_id(session, cycle=cycle, message_id="rating-message")

    return_review_cycle_to_offered(session, cycle=cycle)

    assert cycle.state == TicketReviewCycleState.OFFERED.value
    assert cycle.review_current_message_id is None
    assert cycle.review_id is None


@pytest.mark.parametrize(
    "state",
    [
        TicketReviewCycleState.OFFERED.value,
        TicketReviewCycleState.AWAITING_TEXT.value,
        TicketReviewCycleState.COMPLETED.value,
        TicketReviewCycleState.CANCELLED.value,
    ],
)
def test_return_review_cycle_to_offered_rejects_invalid_states(session, review_context, state):
    cycle = _create_cycle(session, review_context)
    cycle.state = state

    with pytest.raises(ValueError):
        return_review_cycle_to_offered(session, cycle=cycle)


def test_return_review_cycle_to_offered_rejects_cycle_with_review(session, review_context):
    cycle = _create_cycle(session, review_context)
    _save_rating(session, cycle, 5)
    cycle.state = TicketReviewCycleState.AWAITING_RATING.value

    with pytest.raises(ValueError, match="already has a review"):
        return_review_cycle_to_offered(session, cycle=cycle)


def test_return_review_cycle_to_offered_does_not_commit(session, review_context):
    cycle = _create_cycle(session, review_context)
    start_review_rating_cycle(session, cycle=cycle)
    session.commit()

    return_review_cycle_to_offered(session, cycle=cycle)
    session.rollback()
    session.refresh(cycle)

    assert cycle.state == TicketReviewCycleState.AWAITING_RATING.value


def test_unique_cycle_and_pair_version_invariants_are_enforced_by_database(session, review_context):
    cycle = _create_cycle(session, review_context)
    first_review = _save_rating(session, cycle, 5)
    session.commit()

    duplicate_cycle_review = TicketReview(
        id=str(uuid.uuid4()),
        ticket_id=first_review.ticket_id,
        agent_id=first_review.agent_id,
        client_id=first_review.client_id,
        cycle_id=cycle.id,
        previous_version_id=first_review.id,
        version_number=2,
        agent_name_snapshot="Agent",
        client_name_snapshot="Client",
        ticket_subject_snapshot="Subject",
        rating=4,
        rated_at=datetime.now(UTC),
        is_current=False,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(duplicate_cycle_review)

    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    second_cycle = _create_cycle(session, review_context)
    duplicate_version_review = TicketReview(
        id=str(uuid.uuid4()),
        ticket_id=first_review.ticket_id,
        agent_id=first_review.agent_id,
        client_id=first_review.client_id,
        cycle_id=second_cycle.id,
        previous_version_id=None,
        version_number=first_review.version_number,
        agent_name_snapshot="Agent",
        client_name_snapshot="Client",
        ticket_subject_snapshot="Subject",
        rating=3,
        rated_at=datetime.now(UTC),
        is_current=False,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(duplicate_version_review)

    with pytest.raises(IntegrityError):
        session.flush()


def test_schema_evolution_upgrades_existing_reviews_table_cycle_unique_and_drops_legacy_index(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    monkeypatch.setattr(schema_evolution, "engine", engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE ticket_review_cycles (
                    id VARCHAR(36) PRIMARY KEY,
                    ticket_id VARCHAR(36) NOT NULL,
                    agent_id VARCHAR(36) NOT NULL,
                    client_id VARCHAR(36) NOT NULL,
                    state VARCHAR(40) NOT NULL,
                    review_current_message_id VARCHAR(100),
                    review_id VARCHAR(36),
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL,
                    completed_at TIMESTAMP,
                    cancelled_at TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE ticket_reviews (
                    id VARCHAR(36) PRIMARY KEY,
                    ticket_id VARCHAR(36) NOT NULL,
                    agent_id VARCHAR(36) NOT NULL,
                    client_id VARCHAR(36) NOT NULL,
                    cycle_id VARCHAR(36) NOT NULL,
                    previous_version_id VARCHAR(36),
                    version_number INTEGER NOT NULL,
                    agent_name_snapshot TEXT NOT NULL,
                    client_name_snapshot TEXT NOT NULL,
                    ticket_subject_snapshot TEXT NOT NULL,
                    rating INTEGER NOT NULL,
                    review_text TEXT,
                    rated_at TIMESTAMP NOT NULL,
                    text_updated_at TIMESTAMP,
                    is_current BOOLEAN NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL,
                    CONSTRAINT ck_ticket_reviews_rating_1_5 CHECK (rating >= 1 AND rating <= 5),
                    CONSTRAINT uq_ticket_reviews_pair_version UNIQUE (ticket_id, agent_id, version_number)
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_ticket_reviews_ticket_agent_version "
                "ON ticket_reviews(ticket_id, agent_id, version_number)"
            )
        )

    schema_evolution.ensure_ticket_reviews_tables()
    schema_evolution.ensure_ticket_reviews_tables()

    with engine.connect() as conn:
        indexes = {}
        index_meta = {}
        for row in conn.execute(text("PRAGMA index_list(ticket_reviews)")).mappings().all():
            if str(row["name"]).startswith("sqlite_autoindex_"):
                continue
            indexes[row["name"]] = tuple(
                column["name"]
                for column in conn.execute(text(f"PRAGMA index_info({row['name']})")).mappings().all()
            )
            index_meta[row["name"]] = {"unique": bool(row["unique"])}

    assert "ix_ticket_reviews_ticket_agent_version" not in indexes
    assert indexes[schema_evolution.TICKET_REVIEWS_CYCLE_UNIQUE_NAME] == ("cycle_id",)
    assert index_meta[schema_evolution.TICKET_REVIEWS_CYCLE_UNIQUE_NAME]["unique"] is True

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO ticket_review_cycles (
                    id, ticket_id, agent_id, client_id, state, created_at, updated_at
                ) VALUES (
                    'cycle-1', 'ticket-1', 'agent-1', 'client-1', 'offered', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO ticket_reviews (
                    id,
                    ticket_id,
                    agent_id,
                    client_id,
                    cycle_id,
                    version_number,
                    agent_name_snapshot,
                    client_name_snapshot,
                    ticket_subject_snapshot,
                    rating,
                    rated_at,
                    is_current,
                    created_at,
                    updated_at
                ) VALUES (
                    'review-1',
                    'ticket-1',
                    'agent-1',
                    'client-1',
                    'cycle-1',
                    1,
                    'Agent',
                    'Client',
                    'Subject',
                    5,
                    CURRENT_TIMESTAMP,
                    FALSE,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP
                )
                """
            )
        )

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO ticket_reviews (
                        id,
                        ticket_id,
                        agent_id,
                        client_id,
                        cycle_id,
                        version_number,
                        agent_name_snapshot,
                        client_name_snapshot,
                        ticket_subject_snapshot,
                        rating,
                        rated_at,
                        is_current,
                        created_at,
                        updated_at
                    ) VALUES (
                        'review-2',
                        'ticket-1',
                        'agent-1',
                        'client-1',
                        'cycle-1',
                        2,
                        'Agent',
                        'Client',
                        'Subject',
                        4,
                        CURRENT_TIMESTAMP,
                        FALSE,
                        CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP
                    )
                    """
                )
            )


def test_schema_evolution_refuses_duplicate_cycle_ids_before_adding_unique_index(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    monkeypatch.setattr(schema_evolution, "engine", engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE ticket_review_cycles (
                    id VARCHAR(36) PRIMARY KEY,
                    ticket_id VARCHAR(36) NOT NULL,
                    agent_id VARCHAR(36) NOT NULL,
                    client_id VARCHAR(36) NOT NULL,
                    state VARCHAR(40) NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE ticket_reviews (
                    id VARCHAR(36) PRIMARY KEY,
                    ticket_id VARCHAR(36) NOT NULL,
                    agent_id VARCHAR(36) NOT NULL,
                    client_id VARCHAR(36) NOT NULL,
                    cycle_id VARCHAR(36) NOT NULL,
                    version_number INTEGER NOT NULL,
                    agent_name_snapshot TEXT NOT NULL,
                    client_name_snapshot TEXT NOT NULL,
                    ticket_subject_snapshot TEXT NOT NULL,
                    rating INTEGER NOT NULL,
                    rated_at TIMESTAMP NOT NULL,
                    is_current BOOLEAN NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL
                )
                """
            )
        )
        for review_id, version_number in [("review-1", 1), ("review-2", 2)]:
            conn.execute(
                text(
                    """
                    INSERT INTO ticket_reviews (
                        id,
                        ticket_id,
                        agent_id,
                        client_id,
                        cycle_id,
                        version_number,
                        agent_name_snapshot,
                        client_name_snapshot,
                        ticket_subject_snapshot,
                        rating,
                        rated_at,
                        is_current,
                        created_at,
                        updated_at
                    ) VALUES (
                        :review_id,
                        'ticket-1',
                        'agent-1',
                        'client-1',
                        'cycle-1',
                        :version_number,
                        'Agent',
                        'Client',
                        'Subject',
                        5,
                        CURRENT_TIMESTAMP,
                        FALSE,
                        CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP
                    )
                    """
                ),
                {"review_id": review_id, "version_number": version_number},
            )

    with pytest.raises(RuntimeError, match="duplicate cycle_id groups"):
        schema_evolution.ensure_ticket_reviews_tables()

    with engine.connect() as conn:
        row_count = conn.execute(text("SELECT COUNT(*) FROM ticket_reviews")).scalar_one()
        indexes = {
            row["name"]
            for row in conn.execute(text("PRAGMA index_list(ticket_reviews)")).mappings().all()
            if not str(row["name"]).startswith("sqlite_autoindex_")
        }

    assert row_count == 2
    assert schema_evolution.TICKET_REVIEWS_CYCLE_UNIQUE_NAME not in indexes


def test_schema_evolution_is_idempotent_and_creates_review_invariants(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    monkeypatch.setattr(schema_evolution, "engine", engine)

    schema_evolution.ensure_ticket_reviews_tables()
    schema_evolution.ensure_ticket_reviews_tables()

    with engine.connect() as conn:
        cycle_columns = {row["name"] for row in conn.execute(text("PRAGMA table_info(ticket_review_cycles)")).mappings().all()}
        review_columns = {row["name"] for row in conn.execute(text("PRAGMA table_info(ticket_reviews)")).mappings().all()}
        indexes = {}
        index_meta = {}
        for row in conn.execute(text("PRAGMA index_list(ticket_reviews)")).mappings().all():
            if str(row["name"]).startswith("sqlite_autoindex_"):
                continue
            indexes[row["name"]] = tuple(
                column["name"]
                for column in conn.execute(text(f"PRAGMA index_info({row['name']})")).mappings().all()
            )
            index_meta[row["name"]] = {
                "unique": bool(row["unique"]),
                "partial": bool(row["partial"]),
                "sql": conn.execute(
                    text("SELECT sql FROM sqlite_master WHERE type = 'index' AND name = :name"),
                    {"name": row["name"]},
                ).scalar_one(),
            }

    assert cycle_columns == {
        "id",
        "ticket_id",
        "agent_id",
        "client_id",
        "state",
        "review_current_message_id",
        "review_id",
        "created_at",
        "updated_at",
        "completed_at",
        "cancelled_at",
    }
    assert review_columns == {
        "id",
        "ticket_id",
        "agent_id",
        "client_id",
        "cycle_id",
        "previous_version_id",
        "version_number",
        "agent_name_snapshot",
        "client_name_snapshot",
        "ticket_subject_snapshot",
        "rating",
        "review_text",
        "rated_at",
        "text_updated_at",
        "is_current",
        "created_at",
        "updated_at",
    }
    assert indexes[schema_evolution.TICKET_REVIEWS_CURRENT_PAIR_UNIQUE_NAME] == ("ticket_id", "agent_id")
    assert indexes[schema_evolution.TICKET_REVIEWS_CYCLE_UNIQUE_NAME] == ("cycle_id",)
    assert index_meta[schema_evolution.TICKET_REVIEWS_CYCLE_UNIQUE_NAME]["unique"] is True
    current_index = index_meta[schema_evolution.TICKET_REVIEWS_CURRENT_PAIR_UNIQUE_NAME]
    assert current_index["unique"] is True
    assert current_index["partial"] is True
    assert "WHERE is_current = 1" in current_index["sql"]
    assert indexes["ix_ticket_reviews_agent_current_rated"] == ("agent_id", "is_current", "rated_at")
    assert "ix_ticket_reviews_ticket_agent_version" not in indexes


def test_schema_evolution_is_idempotent_and_creates_feedback_invariants(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    monkeypatch.setattr(schema_evolution, "engine", engine)

    schema_evolution.ensure_ticket_feedback_tables()
    schema_evolution.ensure_ticket_feedback_tables()

    with engine.connect() as conn:
        feedback_columns = {row["name"] for row in conn.execute(text("PRAGMA table_info(ticket_feedback)")).mappings().all()}
        input_columns = {row["name"] for row in conn.execute(text("PRAGMA table_info(ticket_feedback_inputs)")).mappings().all()}
        indexes = {}
        index_meta = {}
        for row in conn.execute(text("PRAGMA index_list(ticket_feedback)")).mappings().all():
            if str(row["name"]).startswith("sqlite_autoindex_"):
                continue
            indexes[row["name"]] = tuple(
                column["name"]
                for column in conn.execute(text(f"PRAGMA index_info({row['name']})")).mappings().all()
            )
            index_meta[row["name"]] = {"unique": bool(row["unique"])}

    assert feedback_columns == {
        "id",
        "ticket_id",
        "client_id",
        "client_name_snapshot",
        "ticket_title_snapshot",
        "text",
        "submitted_at",
        "created_at",
        "updated_at",
    }
    assert input_columns == {
        "id",
        "ticket_id",
        "client_id",
        "state",
        "current_message_id",
        "created_at",
        "updated_at",
        "completed_at",
        "cancelled_at",
    }
    assert indexes[schema_evolution.TICKET_FEEDBACK_TICKET_UNIQUE_NAME] == ("ticket_id",)
    assert index_meta[schema_evolution.TICKET_FEEDBACK_TICKET_UNIQUE_NAME]["unique"] is True
