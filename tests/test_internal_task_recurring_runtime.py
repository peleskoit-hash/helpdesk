from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.config import settings
from api.app.db import Base
from api.app.models import (
    Company,
    InternalTaskAssignmentMode,
    InternalTaskDefinition,
    InternalTaskDefinitionState,
    InternalTaskOccurrence,
    InternalTaskScheduleKind,
    MessageType,
    Ticket,
    TicketMessage,
    User,
)
from api.app.services import internal_task_scheduler


def _session_factory():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _company(session) -> Company:
    company = Company(id=str(uuid.uuid4()), name="ACME", is_active=True, created_at=datetime.utcnow())
    session.add(company)
    return company


def _user(session, company: Company, role: str, *, is_active: bool = True, availability_status: str = "offline") -> User:
    user_id = str(uuid.uuid4())
    user = User(
        id=user_id,
        company_id=company.id,
        email=f"{user_id}@example.test",
        login=user_id,
        full_name=f"{role}-{user_id[:8]}",
        role=role,
        is_active=is_active,
        availability_status=availability_status,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(user)
    return user


def _seed_base(Session):
    with Session() as session:
        company = _company(session)
        creator = _user(session, company, "admin")
        responsible = _user(session, company, "admin")
        session.commit()
        return company, creator, responsible


def _definition(
    session,
    company: Company,
    creator: User,
    responsible: User,
    *,
    title: str | None = None,
    recurrence_mode: str = "daily",
    recurrence_config: dict | None = None,
    cycle_start_at_utc: datetime = datetime(2026, 9, 15, 0, 0),
    cycle_end_mode: str = "never",
    cycle_end_at_utc: datetime | None = None,
    cycle_max_occurrences: int | None = None,
    generation_not_before_utc: datetime | None = None,
    state: str = InternalTaskDefinitionState.ACTIVE.value,
    schedule_kind: str = InternalTaskScheduleKind.RECURRING.value,
    assignment_mode: str = InternalTaskAssignmentMode.DISTRIBUTION.value,
    assignee_user_id: str | None = None,
) -> InternalTaskDefinition:
    definition_id = str(uuid.uuid4())
    definition = InternalTaskDefinition(
        id=definition_id,
        company_id=company.id,
        title=title or f"Recurring {definition_id[:8]}",
        description="Recurring description",
        creator_admin_id=creator.id,
        responsible_admin_id=responsible.id,
        display_author=True,
        assignment_mode=assignment_mode,
        assignee_user_id=assignee_user_id,
        notify_on_completion=False,
        deadline_at_utc=None,
        schedule_kind=schedule_kind,
        timezone_name=settings.app_timezone,
        recurrence_mode=recurrence_mode,
        recurrence_config=recurrence_config or {"times": ["10:00"]},
        cycle_start_at_utc=cycle_start_at_utc,
        cycle_end_mode=cycle_end_mode,
        cycle_end_at_utc=cycle_end_at_utc,
        cycle_max_occurrences=cycle_max_occurrences,
        state=state,
        generation_not_before_utc=generation_not_before_utc,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(definition)
    return definition


def _fake_occurrence(session, definition: InternalTaskDefinition, occurrence_at_utc: datetime) -> InternalTaskOccurrence:
    occurrence = InternalTaskOccurrence(
        id=str(uuid.uuid4()),
        definition_id=definition.id,
        occurrence_at_utc=occurrence_at_utc,
        ticket_id=str(uuid.uuid4()),
        title_snapshot=definition.title,
        description_snapshot=definition.description,
        creator_admin_id=definition.creator_admin_id,
        responsible_admin_id=definition.responsible_admin_id,
        display_author=True,
        effective_assignment_mode=InternalTaskAssignmentMode.DISTRIBUTION.value,
        effective_assignee_user_id=None,
        notify_on_completion=False,
        deadline_at_utc=None,
        materialized_at_utc=datetime.utcnow(),
        created_at=datetime.utcnow(),
    )
    session.add(occurrence)
    return occurrence


def _count(session, model) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


def _occurrence_times(session) -> list[datetime]:
    return list(
        session.execute(
            select(InternalTaskOccurrence.occurrence_at_utc).order_by(InternalTaskOccurrence.occurrence_at_utc.asc())
        ).scalars()
    )


def test_future_only_recurring_candidate_is_not_materialized():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        definition = _definition(session, company, creator, responsible, cycle_start_at_utc=datetime(2026, 9, 15, 0, 0))
        session.commit()

    processed = internal_task_scheduler.run_internal_task_recurring_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 15, 6, 0),  # 09:00 Europe/Moscow, first 10:00 local is future.
    )

    with Session() as session:
        assert processed == 0
        assert _count(session, InternalTaskOccurrence) == 0
        assert _count(session, Ticket) == 0
        assert session.get(InternalTaskDefinition, definition.id).state == InternalTaskDefinitionState.ACTIVE.value


def test_due_one_candidate_materializes_and_never_mode_stays_active():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        definition = _definition(session, company, creator, responsible, cycle_start_at_utc=datetime(2026, 9, 15, 0, 0))
        session.commit()

    processed = internal_task_scheduler.run_internal_task_recurring_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 15, 8, 0),
    )

    with Session() as session:
        assert processed == 1
        assert _count(session, Ticket) == 1
        assert _occurrence_times(session) == [datetime(2026, 9, 15, 7, 0)]
        assert session.get(InternalTaskDefinition, definition.id).state == InternalTaskDefinitionState.ACTIVE.value


def test_downtime_catch_up_materializes_all_due_historical_candidates():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        _definition(session, company, creator, responsible, cycle_start_at_utc=datetime(2026, 9, 15, 0, 0))
        session.commit()

    processed = internal_task_scheduler.run_internal_task_recurring_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 18, 8, 0),
    )

    with Session() as session:
        assert processed == 4
        assert _occurrence_times(session) == [
            datetime(2026, 9, 15, 7, 0),
            datetime(2026, 9, 16, 7, 0),
            datetime(2026, 9, 17, 7, 0),
            datetime(2026, 9, 18, 7, 0),
        ]


def test_repeated_pass_is_idempotent_and_does_not_duplicate_initial_messages():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        _definition(session, company, creator, responsible, cycle_start_at_utc=datetime(2026, 9, 15, 0, 0))
        session.commit()

    first = internal_task_scheduler.run_internal_task_recurring_once(session_factory=Session, now_utc=datetime(2026, 9, 16, 8, 0))
    second = internal_task_scheduler.run_internal_task_recurring_once(session_factory=Session, now_utc=datetime(2026, 9, 16, 8, 0))

    with Session() as session:
        assert first == 2
        assert second == 0
        assert _count(session, Ticket) == 2
        assert _count(session, InternalTaskOccurrence) == 2
        assert (
            session.execute(
                select(func.count()).select_from(TicketMessage).where(TicketMessage.message_type == MessageType.CLIENT.value)
            ).scalar_one()
            == 2
        )


def test_existing_occurrences_are_excluded_from_partial_catch_up():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        definition = _definition(session, company, creator, responsible, cycle_start_at_utc=datetime(2026, 9, 15, 0, 0))
        _fake_occurrence(session, definition, datetime(2026, 9, 15, 7, 0))
        session.commit()

    processed = internal_task_scheduler.run_internal_task_recurring_once(session_factory=Session, now_utc=datetime(2026, 9, 16, 8, 0))

    with Session() as session:
        assert processed == 1
        assert _count(session, InternalTaskOccurrence) == 2
        assert _count(session, Ticket) == 1
        assert _occurrence_times(session) == [datetime(2026, 9, 15, 7, 0), datetime(2026, 9, 16, 7, 0)]


def test_paused_recurring_definition_is_ignored():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        _definition(session, company, creator, responsible, state=InternalTaskDefinitionState.PAUSED.value)
        session.commit()

    processed = internal_task_scheduler.run_internal_task_recurring_once(session_factory=Session, now_utc=datetime(2026, 9, 16, 8, 0))

    with Session() as session:
        assert processed == 0
        assert _count(session, InternalTaskOccurrence) == 0


def test_generation_not_before_skips_manual_pause_gap_without_catch_up():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        _definition(
            session,
            company,
            creator,
            responsible,
            cycle_start_at_utc=datetime(2026, 9, 15, 0, 0),
            generation_not_before_utc=datetime(2026, 9, 18, 11, 0),  # 14:00 local.
        )
        session.commit()

    processed = internal_task_scheduler.run_internal_task_recurring_once(session_factory=Session, now_utc=datetime(2026, 9, 19, 8, 0))

    with Session() as session:
        assert processed == 1
        assert _occurrence_times(session) == [datetime(2026, 9, 19, 7, 0)]


def test_count_mode_materializes_until_max_and_finishes():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        definition = _definition(
            session,
            company,
            creator,
            responsible,
            cycle_end_mode="count",
            cycle_max_occurrences=3,
            cycle_start_at_utc=datetime(2026, 9, 15, 0, 0),
        )
        session.commit()

    processed = internal_task_scheduler.run_internal_task_recurring_once(session_factory=Session, now_utc=datetime(2026, 9, 19, 8, 0))

    with Session() as session:
        assert processed == 3
        assert _count(session, InternalTaskOccurrence) == 3
        assert session.get(InternalTaskDefinition, definition.id).state == InternalTaskDefinitionState.FINISHED.value


def test_count_mode_uses_persisted_successes():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        definition = _definition(
            session,
            company,
            creator,
            responsible,
            cycle_end_mode="count",
            cycle_max_occurrences=3,
            cycle_start_at_utc=datetime(2026, 9, 15, 0, 0),
        )
        _fake_occurrence(session, definition, datetime(2026, 9, 15, 7, 0))
        _fake_occurrence(session, definition, datetime(2026, 9, 16, 7, 0))
        session.commit()

    processed = internal_task_scheduler.run_internal_task_recurring_once(session_factory=Session, now_utc=datetime(2026, 9, 19, 8, 0))

    with Session() as session:
        assert processed == 1
        assert _count(session, InternalTaskOccurrence) == 3
        assert _count(session, Ticket) == 1
        assert session.get(InternalTaskDefinition, definition.id).state == InternalTaskDefinitionState.FINISHED.value


def test_failed_candidate_does_not_count_and_blocks_later_candidates_for_same_definition(monkeypatch):
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        definition = _definition(
            session,
            company,
            creator,
            responsible,
            cycle_end_mode="count",
            cycle_max_occurrences=2,
            cycle_start_at_utc=datetime(2026, 9, 15, 0, 0),
        )
        session.commit()

    original = internal_task_scheduler.materialize_internal_task
    attempts: list[datetime] = []
    fail_once = True

    def fail_first(db, definition, *, occurrence_at_utc):
        nonlocal fail_once
        attempts.append(occurrence_at_utc)
        if fail_once:
            fail_once = False
            raise RuntimeError("boom")
        return original(db, definition, occurrence_at_utc=occurrence_at_utc)

    monkeypatch.setattr(internal_task_scheduler, "materialize_internal_task", fail_first)

    first = internal_task_scheduler.run_internal_task_recurring_once(session_factory=Session, now_utc=datetime(2026, 9, 17, 8, 0))
    second = internal_task_scheduler.run_internal_task_recurring_once(session_factory=Session, now_utc=datetime(2026, 9, 17, 8, 0))

    with Session() as session:
        assert first == 0
        assert second == 2
        assert attempts == [datetime(2026, 9, 15, 7, 0), datetime(2026, 9, 15, 7, 0), datetime(2026, 9, 16, 7, 0)]
        assert _occurrence_times(session) == [datetime(2026, 9, 15, 7, 0), datetime(2026, 9, 16, 7, 0)]
        assert session.get(InternalTaskDefinition, definition.id).state == InternalTaskDefinitionState.FINISHED.value


def test_end_at_inclusive_candidate_is_materialized():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        definition = _definition(
            session,
            company,
            creator,
            responsible,
            cycle_start_at_utc=datetime(2026, 9, 15, 0, 0),
            cycle_end_mode="at",
            cycle_end_at_utc=datetime(2026, 9, 16, 7, 0),
        )
        session.commit()

    processed = internal_task_scheduler.run_internal_task_recurring_once(session_factory=Session, now_utc=datetime(2026, 9, 16, 7, 0))

    with Session() as session:
        assert processed == 2
        assert datetime(2026, 9, 16, 7, 0) in _occurrence_times(session)
        assert session.get(InternalTaskDefinition, definition.id).state == InternalTaskDefinitionState.FINISHED.value


def test_at_mode_finishes_after_end_when_all_allowed_candidates_exist():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        definition = _definition(
            session,
            company,
            creator,
            responsible,
            cycle_start_at_utc=datetime(2026, 9, 15, 0, 0),
            cycle_end_mode="at",
            cycle_end_at_utc=datetime(2026, 9, 15, 7, 0),
        )
        _fake_occurrence(session, definition, datetime(2026, 9, 15, 7, 0))
        session.commit()

    processed = internal_task_scheduler.run_internal_task_recurring_once(session_factory=Session, now_utc=datetime(2026, 9, 16, 8, 0))

    with Session() as session:
        assert processed == 0
        assert session.get(InternalTaskDefinition, definition.id).state == InternalTaskDefinitionState.FINISHED.value


def test_at_mode_failed_candidate_before_end_prevents_finish_and_retries(monkeypatch):
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        definition = _definition(
            session,
            company,
            creator,
            responsible,
            cycle_start_at_utc=datetime(2026, 9, 15, 0, 0),
            cycle_end_mode="at",
            cycle_end_at_utc=datetime(2026, 9, 15, 7, 0),
        )
        session.commit()

    original = internal_task_scheduler.materialize_internal_task
    fail_once = True

    def fail_then_succeed(db, definition, *, occurrence_at_utc):
        nonlocal fail_once
        if fail_once:
            fail_once = False
            raise RuntimeError("boom")
        return original(db, definition, occurrence_at_utc=occurrence_at_utc)

    monkeypatch.setattr(internal_task_scheduler, "materialize_internal_task", fail_then_succeed)

    first = internal_task_scheduler.run_internal_task_recurring_once(session_factory=Session, now_utc=datetime(2026, 9, 16, 8, 0))
    with Session() as session:
        assert first == 0
        assert session.get(InternalTaskDefinition, definition.id).state == InternalTaskDefinitionState.ACTIVE.value

    second = internal_task_scheduler.run_internal_task_recurring_once(session_factory=Session, now_utc=datetime(2026, 9, 16, 8, 0))
    with Session() as session:
        assert second == 1
        assert session.get(InternalTaskDefinition, definition.id).state == InternalTaskDefinitionState.FINISHED.value


def test_at_mode_with_no_candidates_before_end_finishes_after_end():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        definition = _definition(
            session,
            company,
            creator,
            responsible,
            recurrence_mode="weekly",
            recurrence_config={"time": "10:00"},
            cycle_start_at_utc=datetime(2026, 9, 15, 11, 0),  # 14:00 local; 10:00 candidate before cycle_start.
            cycle_end_mode="at",
            cycle_end_at_utc=datetime(2026, 9, 16, 0, 0),
        )
        session.commit()

    processed = internal_task_scheduler.run_internal_task_recurring_once(session_factory=Session, now_utc=datetime(2026, 9, 16, 1, 0))

    with Session() as session:
        assert processed == 0
        assert _count(session, InternalTaskOccurrence) == 0
        assert session.get(InternalTaskDefinition, definition.id).state == InternalTaskDefinitionState.FINISHED.value


def test_at_mode_finishes_when_future_generation_not_before_is_after_elapsed_end():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        definition = _definition(
            session,
            company,
            creator,
            responsible,
            cycle_start_at_utc=datetime(2026, 9, 15, 0, 0),
            cycle_end_mode="at",
            cycle_end_at_utc=datetime(2026, 9, 20, 10, 0),
            generation_not_before_utc=datetime(2026, 9, 25, 10, 0),
        )
        session.commit()

    processed = internal_task_scheduler.run_internal_task_recurring_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 21, 10, 0),
    )

    with Session() as session:
        assert processed == 0
        assert _count(session, Ticket) == 0
        assert _count(session, InternalTaskOccurrence) == 0
        assert session.get(InternalTaskDefinition, definition.id).state == InternalTaskDefinitionState.FINISHED.value


def test_one_definition_failure_does_not_block_another(monkeypatch):
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        failing = _definition(session, company, creator, responsible, title="fail", cycle_start_at_utc=datetime(2026, 9, 15, 0, 0))
        successful = _definition(session, company, creator, responsible, title="ok", cycle_start_at_utc=datetime(2026, 9, 15, 0, 0))
        session.commit()

    original = internal_task_scheduler.materialize_internal_task

    def fail_one(db, definition, *, occurrence_at_utc):
        if definition.id == failing.id:
            raise RuntimeError("boom")
        return original(db, definition, occurrence_at_utc=occurrence_at_utc)

    monkeypatch.setattr(internal_task_scheduler, "materialize_internal_task", fail_one)

    processed = internal_task_scheduler.run_internal_task_recurring_once(session_factory=Session, now_utc=datetime(2026, 9, 15, 8, 0))

    with Session() as session:
        assert processed == 1
        assert session.get(InternalTaskDefinition, failing.id).state == InternalTaskDefinitionState.ACTIVE.value
        assert session.get(InternalTaskDefinition, successful.id).state == InternalTaskDefinitionState.ACTIVE.value
        assert _count(session, InternalTaskOccurrence) == 1


def test_malformed_recurring_definition_does_not_block_another_definition():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        malformed = _definition(
            session,
            company,
            creator,
            responsible,
            title="bad",
            recurrence_config={"times": ["25:00"]},
        )
        valid = _definition(session, company, creator, responsible, title="valid")
        session.commit()

    processed = internal_task_scheduler.run_internal_task_recurring_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 15, 8, 0),
    )

    with Session() as session:
        assert processed == 1
        assert session.get(InternalTaskDefinition, malformed.id).state == InternalTaskDefinitionState.ACTIVE.value
        assert session.get(InternalTaskDefinition, valid.id).state == InternalTaskDefinitionState.ACTIVE.value
        assert _count(session, InternalTaskOccurrence) == 1


def test_partial_success_survives_later_failure_and_next_pass_retries_from_failed_candidate(monkeypatch):
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        _definition(session, company, creator, responsible, cycle_start_at_utc=datetime(2026, 9, 15, 0, 0))
        session.commit()

    original = internal_task_scheduler.materialize_internal_task
    attempts: list[datetime] = []

    def fail_third(db, definition, *, occurrence_at_utc):
        attempts.append(occurrence_at_utc)
        if occurrence_at_utc == datetime(2026, 9, 17, 7, 0):
            raise RuntimeError("boom")
        return original(db, definition, occurrence_at_utc=occurrence_at_utc)

    monkeypatch.setattr(internal_task_scheduler, "materialize_internal_task", fail_third)

    first = internal_task_scheduler.run_internal_task_recurring_once(session_factory=Session, now_utc=datetime(2026, 9, 18, 8, 0))
    with Session() as session:
        assert first == 2
        assert _occurrence_times(session) == [datetime(2026, 9, 15, 7, 0), datetime(2026, 9, 16, 7, 0)]
        assert _count(session, Ticket) == 2

    attempts.clear()
    second = internal_task_scheduler.run_internal_task_recurring_once(session_factory=Session, now_utc=datetime(2026, 9, 18, 8, 0))
    assert second == 0
    assert attempts == [datetime(2026, 9, 17, 7, 0)]


def test_wrong_state_and_kind_are_ignored():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        for state in [
            InternalTaskDefinitionState.PAUSED.value,
            InternalTaskDefinitionState.FINISHED.value,
            InternalTaskDefinitionState.DISABLED.value,
        ]:
            _definition(session, company, creator, responsible, state=state)
        _definition(session, company, creator, responsible, schedule_kind=InternalTaskScheduleKind.IMMEDIATE.value)
        _definition(session, company, creator, responsible, schedule_kind=InternalTaskScheduleKind.SCHEDULED.value)
        active = _definition(session, company, creator, responsible)
        session.commit()

    processed = internal_task_scheduler.run_internal_task_recurring_once(session_factory=Session, now_utc=datetime(2026, 9, 15, 8, 0))

    with Session() as session:
        assert processed == 1
        assert session.get(InternalTaskDefinition, active.id).state == InternalTaskDefinitionState.ACTIVE.value
        assert _count(session, InternalTaskOccurrence) == 1


def test_inactive_direct_assignee_falls_back_without_mutating_definition_assignment_mode():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        inactive_agent = _user(session, company, "agent", is_active=False, availability_status="online")
        definition = _definition(
            session,
            company,
            creator,
            responsible,
            assignment_mode=InternalTaskAssignmentMode.DIRECT.value,
            assignee_user_id=inactive_agent.id,
        )
        session.commit()

    processed = internal_task_scheduler.run_internal_task_recurring_once(session_factory=Session, now_utc=datetime(2026, 9, 15, 8, 0))

    with Session() as session:
        occurrence = session.execute(select(InternalTaskOccurrence)).scalar_one()
        definition = session.get(InternalTaskDefinition, definition.id)
        assert processed == 1
        assert occurrence.effective_assignment_mode == InternalTaskAssignmentMode.DISTRIBUTION.value
        assert occurrence.effective_assignee_user_id is None
        assert definition.assignment_mode == InternalTaskAssignmentMode.DIRECT.value


def test_recurring_definition_chunk_does_not_starve_later_active_definitions():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        for index in range(3):
            _definition(session, company, creator, responsible, title=f"def-{index}", cycle_start_at_utc=datetime(2026, 9, 15, 0, 0))
        session.commit()

    processed = internal_task_scheduler.run_internal_task_recurring_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 15, 8, 0),
        batch_limit=2,
    )

    with Session() as session:
        assert processed == 3
        assert _count(session, InternalTaskOccurrence) == 3
        assert all(
            definition.state == InternalTaskDefinitionState.ACTIVE.value
            for definition in session.execute(select(InternalTaskDefinition)).scalars()
        )


def test_failure_first_chunk_does_not_starve_third_recurring_definition(monkeypatch):
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        first = _definition(session, company, creator, responsible, title="fail-1")
        second = _definition(session, company, creator, responsible, title="fail-2")
        third = _definition(session, company, creator, responsible, title="success")
        session.commit()

    original = internal_task_scheduler.materialize_internal_task
    attempts: list[str] = []
    failing_ids = {first.id, second.id}

    def fail_first_two(db, definition, *, occurrence_at_utc):
        attempts.append(definition.id)
        if definition.id in failing_ids:
            raise RuntimeError("boom")
        return original(db, definition, occurrence_at_utc=occurrence_at_utc)

    monkeypatch.setattr(internal_task_scheduler, "materialize_internal_task", fail_first_two)

    processed = internal_task_scheduler.run_internal_task_recurring_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 15, 8, 0),
        batch_limit=2,
    )

    with Session() as session:
        assert processed == 1
        assert _count(session, InternalTaskOccurrence) == 1
        assert attempts.count(first.id) == 1
        assert attempts.count(second.id) == 1
        assert attempts.count(third.id) == 1
        assert session.get(InternalTaskDefinition, first.id).state == InternalTaskDefinitionState.ACTIVE.value
        assert session.get(InternalTaskDefinition, second.id).state == InternalTaskDefinitionState.ACTIVE.value
        assert session.get(InternalTaskDefinition, third.id).state == InternalTaskDefinitionState.ACTIVE.value


def test_scheduler_loop_runs_one_off_then_recurring_then_sleep_and_survives_one_off_exception(monkeypatch):
    calls = []

    def one_off():
        calls.append("one_off")
        raise RuntimeError("boom")

    def recurring():
        calls.append("recurring")
        return 0

    async def stop_on_sleep(_seconds):
        calls.append("sleep")
        raise asyncio.CancelledError

    monkeypatch.setattr(internal_task_scheduler, "run_internal_task_scheduler_once", one_off)
    monkeypatch.setattr(internal_task_scheduler, "run_internal_task_recurring_once", recurring)
    monkeypatch.setattr(internal_task_scheduler, "run_avatar_cleanup_once", lambda: calls.append("avatars"))
    monkeypatch.setattr(internal_task_scheduler, "run_telegram_after_hours_cleanup_once", lambda: calls.append("intake"))
    monkeypatch.setattr(internal_task_scheduler.asyncio, "sleep", stop_on_sleep)

    try:
        asyncio.run(internal_task_scheduler.internal_task_scheduler_loop())
    except asyncio.CancelledError:
        pass

    assert calls == ["one_off", "recurring", "avatars", "intake", "sleep"]
