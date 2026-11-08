from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

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


def _company(session, name: str = "ACME") -> Company:
    company = Company(id=str(uuid.uuid4()), name=name, is_active=True, created_at=datetime.utcnow())
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


def _definition(
    session,
    company: Company,
    creator: User,
    responsible: User,
    *,
    scheduled_for_utc: datetime | None,
    title: str | None = None,
    schedule_kind: str = InternalTaskScheduleKind.SCHEDULED.value,
    state: str = InternalTaskDefinitionState.ACTIVE.value,
    assignment_mode: str = InternalTaskAssignmentMode.DISTRIBUTION.value,
    assignee_user_id: str | None = None,
) -> InternalTaskDefinition:
    definition_id = str(uuid.uuid4())
    definition = InternalTaskDefinition(
        id=definition_id,
        company_id=company.id,
        title=title or f"Scheduled {definition_id[:8]}",
        description="Internal scheduled description",
        creator_admin_id=creator.id,
        responsible_admin_id=responsible.id,
        display_author=True,
        assignment_mode=assignment_mode,
        assignee_user_id=assignee_user_id,
        notify_on_completion=False,
        deadline_at_utc=None,
        schedule_kind=schedule_kind,
        timezone_name=settings.app_timezone,
        scheduled_for_utc=scheduled_for_utc,
        state=state,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(definition)
    return definition


def _seed_base(Session):
    session = Session()
    company = _company(session)
    creator = _user(session, company, "admin")
    responsible = _user(session, company, "admin")
    session.commit()
    session.close()
    return company, creator, responsible


def _count(session, model) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


def test_future_scheduled_definition_is_not_materialized():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        definition = _definition(
            session,
            company,
            creator,
            responsible,
            scheduled_for_utc=datetime(2026, 9, 16, 10, 0, 0),
        )
        session.commit()

    processed = internal_task_scheduler.run_internal_task_scheduler_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 16, 9, 59, 0),
    )

    with Session() as session:
        assert processed == 0
        assert session.get(InternalTaskDefinition, definition.id).state == InternalTaskDefinitionState.ACTIVE.value
        assert _count(session, InternalTaskOccurrence) == 0
        assert _count(session, Ticket) == 0


def test_due_scheduled_definition_is_materialized_and_finished():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        definition = _definition(
            session,
            company,
            creator,
            responsible,
            scheduled_for_utc=datetime(2026, 9, 16, 10, 0, 0),
        )
        session.commit()

    processed = internal_task_scheduler.run_internal_task_scheduler_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 16, 10, 0, 0),
    )

    with Session() as session:
        occurrence = session.execute(select(InternalTaskOccurrence)).scalar_one()
        assert processed == 1
        assert session.get(InternalTaskDefinition, definition.id).state == InternalTaskDefinitionState.FINISHED.value
        assert _count(session, Ticket) == 1
        assert occurrence.definition_id == definition.id


def test_due_scheduled_definition_accepts_postgresql_aware_timestamp():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        definition = _definition(
            session,
            company,
            creator,
            responsible,
            scheduled_for_utc=datetime(2026, 9, 16, 10, 0, 0, tzinfo=timezone.utc),
        )
        assert internal_task_scheduler._is_due_scheduled_definition(
            definition,
            now_utc=datetime(2026, 9, 16, 10, 1, 0),
        )
        session.commit()

    processed = internal_task_scheduler.run_internal_task_scheduler_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 16, 10, 1, 0),
    )

    with Session() as session:
        occurrence = session.execute(select(InternalTaskOccurrence)).scalar_one()
        assert processed == 1
        assert session.get(InternalTaskDefinition, definition.id).state == InternalTaskDefinitionState.FINISHED.value
        assert occurrence.definition_id == definition.id


def test_occurrence_time_uses_scheduled_for_not_scan_time():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    scheduled_for = datetime(2026, 9, 16, 10, 0, 0)
    with Session() as session:
        _definition(session, company, creator, responsible, scheduled_for_utc=scheduled_for)
        session.commit()

    internal_task_scheduler.run_internal_task_scheduler_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 16, 13, 40, 0),
    )

    with Session() as session:
        occurrence = session.execute(select(InternalTaskOccurrence)).scalar_one()
        assert occurrence.occurrence_at_utc == scheduled_for
        assert occurrence.materialized_at_utc != occurrence.occurrence_at_utc


def test_overdue_catch_up_has_no_lookback_cutoff():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        _definition(
            session,
            company,
            creator,
            responsible,
            scheduled_for_utc=datetime(2025, 1, 1, 10, 0, 0),
        )
        session.commit()

    processed = internal_task_scheduler.run_internal_task_scheduler_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 16, 10, 0, 0),
    )

    with Session() as session:
        assert processed == 1
        assert _count(session, InternalTaskOccurrence) == 1
        assert _count(session, Ticket) == 1


def test_repeated_scan_is_idempotent():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        _definition(
            session,
            company,
            creator,
            responsible,
            scheduled_for_utc=datetime(2026, 9, 16, 10, 0, 0),
        )
        session.commit()

    first = internal_task_scheduler.run_internal_task_scheduler_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 16, 11, 0, 0),
    )
    second = internal_task_scheduler.run_internal_task_scheduler_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 16, 11, 1, 0),
    )

    with Session() as session:
        assert first == 1
        assert second == 0
        assert _count(session, Ticket) == 1
        assert _count(session, InternalTaskOccurrence) == 1
        assert (
            session.execute(
                select(func.count())
                .select_from(TicketMessage)
                .where(TicketMessage.message_type == MessageType.CLIENT.value)
            ).scalar_one()
            == 1
        )


def test_multiple_due_definitions_materialize_in_one_scan():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        for index in range(3):
            _definition(
                session,
                company,
                creator,
                responsible,
                scheduled_for_utc=datetime(2026, 9, 16, 10, index, 0),
            )
        session.commit()

    processed = internal_task_scheduler.run_internal_task_scheduler_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 16, 11, 0, 0),
    )

    with Session() as session:
        assert processed == 3
        assert _count(session, Ticket) == 3
        assert _count(session, InternalTaskOccurrence) == 3


def test_one_failure_does_not_block_other_due_definitions(monkeypatch):
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        failing = _definition(
            session,
            company,
            creator,
            responsible,
            title="fail",
            scheduled_for_utc=datetime(2026, 9, 16, 10, 0, 0),
        )
        successful = _definition(
            session,
            company,
            creator,
            responsible,
            title="success",
            scheduled_for_utc=datetime(2026, 9, 16, 10, 1, 0),
        )
        session.commit()

    original = internal_task_scheduler.materialize_internal_task

    def flaky_materialize(db, definition, *, occurrence_at_utc):
        if definition.title == "fail":
            raise RuntimeError("boom")
        return original(db, definition, occurrence_at_utc=occurrence_at_utc)

    monkeypatch.setattr(internal_task_scheduler, "materialize_internal_task", flaky_materialize)

    processed = internal_task_scheduler.run_internal_task_scheduler_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 16, 11, 0, 0),
    )

    with Session() as session:
        assert processed == 1
        assert session.get(InternalTaskDefinition, failing.id).state == InternalTaskDefinitionState.ACTIVE.value
        assert session.get(InternalTaskDefinition, successful.id).state == InternalTaskDefinitionState.FINISHED.value
        assert _count(session, InternalTaskOccurrence) == 1
        assert _count(session, Ticket) == 1


def test_failed_first_chunk_does_not_starve_later_due_definitions_and_retries_next_pass(monkeypatch):
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        first = _definition(
            session,
            company,
            creator,
            responsible,
            title="fail-1",
            scheduled_for_utc=datetime(2026, 9, 16, 10, 0, 0),
        )
        second = _definition(
            session,
            company,
            creator,
            responsible,
            title="fail-2",
            scheduled_for_utc=datetime(2026, 9, 16, 10, 1, 0),
        )
        third = _definition(
            session,
            company,
            creator,
            responsible,
            title="success",
            scheduled_for_utc=datetime(2026, 9, 16, 10, 2, 0),
        )
        session.commit()

    original = internal_task_scheduler.materialize_internal_task
    attempts: list[str] = []
    fail_ids = {first.id, second.id}

    def failing_first_chunk(db, definition, *, occurrence_at_utc):
        attempts.append(definition.id)
        if definition.id in fail_ids:
            raise RuntimeError("boom")
        return original(db, definition, occurrence_at_utc=occurrence_at_utc)

    monkeypatch.setattr(internal_task_scheduler, "materialize_internal_task", failing_first_chunk)

    processed = internal_task_scheduler.run_internal_task_scheduler_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 16, 11, 0, 0),
        batch_limit=2,
    )

    with Session() as session:
        assert processed == 1
        assert session.get(InternalTaskDefinition, first.id).state == InternalTaskDefinitionState.ACTIVE.value
        assert session.get(InternalTaskDefinition, second.id).state == InternalTaskDefinitionState.ACTIVE.value
        assert session.get(InternalTaskDefinition, third.id).state == InternalTaskDefinitionState.FINISHED.value
        assert _count(session, Ticket) == 1
        assert _count(session, InternalTaskOccurrence) == 1
        assert attempts.count(first.id) == 1
        assert attempts.count(second.id) == 1
        assert attempts.count(third.id) == 1

    retry_attempts: list[str] = []

    def retry_only(db, definition, *, occurrence_at_utc):
        retry_attempts.append(definition.id)
        raise RuntimeError("still failing")

    monkeypatch.setattr(internal_task_scheduler, "materialize_internal_task", retry_only)

    retried = internal_task_scheduler.run_internal_task_scheduler_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 16, 11, 1, 0),
        batch_limit=2,
    )

    assert retried == 0
    assert retry_attempts.count(first.id) == 1
    assert retry_attempts.count(second.id) == 1
    assert third.id not in retry_attempts


def test_failed_definition_is_retried_on_next_scan(monkeypatch):
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        definition = _definition(
            session,
            company,
            creator,
            responsible,
            scheduled_for_utc=datetime(2026, 9, 16, 10, 0, 0),
        )
        session.commit()

    original = internal_task_scheduler.materialize_internal_task
    should_fail = True

    def once_failing_materialize(db, definition, *, occurrence_at_utc):
        nonlocal should_fail
        if should_fail:
            should_fail = False
            raise RuntimeError("boom")
        return original(db, definition, occurrence_at_utc=occurrence_at_utc)

    monkeypatch.setattr(internal_task_scheduler, "materialize_internal_task", once_failing_materialize)

    first = internal_task_scheduler.run_internal_task_scheduler_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 16, 11, 0, 0),
    )
    second = internal_task_scheduler.run_internal_task_scheduler_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 16, 11, 1, 0),
    )

    with Session() as session:
        assert first == 0
        assert second == 1
        assert session.get(InternalTaskDefinition, definition.id).state == InternalTaskDefinitionState.FINISHED.value
        assert _count(session, InternalTaskOccurrence) == 1


def test_scan_filters_future_wrong_state_immediate_and_recurring_definitions():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    now = datetime(2026, 9, 16, 11, 0, 0)
    with Session() as session:
        _definition(session, company, creator, responsible, scheduled_for_utc=datetime(2026, 9, 16, 12, 0, 0))
        _definition(
            session,
            company,
            creator,
            responsible,
            scheduled_for_utc=datetime(2026, 9, 16, 10, 0, 0),
            state=InternalTaskDefinitionState.FINISHED.value,
        )
        _definition(
            session,
            company,
            creator,
            responsible,
            scheduled_for_utc=datetime(2026, 9, 16, 10, 0, 0),
            state=InternalTaskDefinitionState.PAUSED.value,
        )
        _definition(
            session,
            company,
            creator,
            responsible,
            scheduled_for_utc=datetime(2026, 9, 16, 10, 0, 0),
            state=InternalTaskDefinitionState.DISABLED.value,
        )
        _definition(
            session,
            company,
            creator,
            responsible,
            scheduled_for_utc=None,
            schedule_kind=InternalTaskScheduleKind.IMMEDIATE.value,
        )
        _definition(
            session,
            company,
            creator,
            responsible,
            scheduled_for_utc=None,
            schedule_kind=InternalTaskScheduleKind.RECURRING.value,
        )
        session.commit()

    processed = internal_task_scheduler.run_internal_task_scheduler_once(session_factory=Session, now_utc=now)

    with Session() as session:
        assert processed == 0
        assert _count(session, InternalTaskOccurrence) == 0
        assert _count(session, Ticket) == 0


def test_direct_inactive_assignee_falls_back_without_scheduler_specific_handling():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        inactive_agent = _user(session, company, "agent", is_active=False, availability_status="online")
        definition = _definition(
            session,
            company,
            creator,
            responsible,
            scheduled_for_utc=datetime(2026, 9, 16, 10, 0, 0),
            assignment_mode=InternalTaskAssignmentMode.DIRECT.value,
            assignee_user_id=inactive_agent.id,
        )
        session.commit()

    processed = internal_task_scheduler.run_internal_task_scheduler_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 16, 11, 0, 0),
    )

    with Session() as session:
        occurrence = session.execute(select(InternalTaskOccurrence)).scalar_one()
        assert processed == 1
        assert session.get(InternalTaskDefinition, definition.id).state == InternalTaskDefinitionState.FINISHED.value
        assert occurrence.effective_assignment_mode == InternalTaskAssignmentMode.DISTRIBUTION.value
        assert occurrence.effective_assignee_user_id is None


def test_overdue_definition_materializes_without_in_memory_timer_state():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        _definition(
            session,
            company,
            creator,
            responsible,
            scheduled_for_utc=datetime(2026, 9, 15, 10, 0, 0),
        )
        session.commit()

    processed = internal_task_scheduler.run_internal_task_scheduler_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 16, 11, 0, 0),
    )

    with Session() as session:
        assert processed == 1
        assert _count(session, InternalTaskOccurrence) == 1


def test_batch_limit_is_fetch_chunk_size_and_one_pass_picks_up_remaining_definitions():
    Session = _session_factory()
    company, creator, responsible = _seed_base(Session)
    with Session() as session:
        for index in range(3):
            _definition(
                session,
                company,
                creator,
                responsible,
                scheduled_for_utc=datetime(2026, 9, 16, 10, index, 0),
            )
        session.commit()

    first = internal_task_scheduler.run_internal_task_scheduler_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 16, 11, 0, 0),
        batch_limit=2,
    )

    with Session() as session:
        assert first == 3
        assert _count(session, InternalTaskOccurrence) == 3


def test_scheduler_loop_runs_one_off_and_recurring_before_first_sleep(monkeypatch):
    calls = []

    def run_once():
        calls.append("one_off")
        return 0

    def run_recurring_once():
        calls.append("recurring")
        return 0

    async def stop_on_sleep(_seconds):
        calls.append("sleep")
        raise asyncio.CancelledError

    monkeypatch.setattr(internal_task_scheduler, "run_internal_task_scheduler_once", run_once)
    monkeypatch.setattr(internal_task_scheduler, "run_internal_task_recurring_once", run_recurring_once)
    monkeypatch.setattr(internal_task_scheduler, "run_avatar_cleanup_once", lambda: calls.append("avatars"))
    monkeypatch.setattr(internal_task_scheduler, "run_telegram_after_hours_cleanup_once", lambda: calls.append("intake"))
    monkeypatch.setattr(internal_task_scheduler, "run_employee_auto_offline_once", lambda: calls.append("offline"))
    monkeypatch.setattr(internal_task_scheduler, "run_employee_notification_backlog_once", lambda: calls.append("backlog"))
    monkeypatch.setattr(internal_task_scheduler.asyncio, "sleep", stop_on_sleep)

    try:
        asyncio.run(internal_task_scheduler.internal_task_scheduler_loop())
    except asyncio.CancelledError:
        pass

    assert calls == ["one_off", "recurring", "avatars", "intake", "offline", "backlog", "sleep"]
