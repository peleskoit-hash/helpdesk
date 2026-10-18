from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.config import settings
from api.app.db import Base
from api.app.models import (
    Client,
    Company,
    InternalTaskAssignmentMode,
    InternalTaskDefinition,
    InternalTaskDefinitionState,
    InternalTaskOccurrence,
    InternalTaskScheduleKind,
    MessageType,
    Ticket,
    TicketAttachment,
    TicketIntakeState,
    TicketMessage,
    TicketStatus,
    TicketTaskKind,
    TicketWorkflowType,
    User,
)
from api.app.services.internal_tasks import INTERNAL_TASK_SYSTEM_CLIENT_NAME, materialize_internal_task
from api.app.services.ticket_lifecycle import close_ticket_by_actor


def _session():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


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
    title: str = "Internal title",
    description: str = "Internal description",
    assignment_mode: str = InternalTaskAssignmentMode.DISTRIBUTION.value,
    assignee_user_id: str | None = None,
    schedule_kind: str = InternalTaskScheduleKind.RECURRING.value,
    display_author: bool = True,
    notify_on_completion: bool = True,
    deadline_at_utc: datetime | None = None,
) -> InternalTaskDefinition:
    definition = InternalTaskDefinition(
        id=str(uuid.uuid4()),
        company_id=company.id,
        title=title,
        description=description,
        creator_admin_id=creator.id,
        responsible_admin_id=responsible.id,
        display_author=display_author,
        assignment_mode=assignment_mode,
        assignee_user_id=assignee_user_id,
        notify_on_completion=notify_on_completion,
        deadline_at_utc=deadline_at_utc,
        schedule_kind=schedule_kind,
        timezone_name=settings.app_timezone,
        state=InternalTaskDefinitionState.ACTIVE.value,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(definition)
    return definition


def _messages(session, ticket: Ticket, message_type: str) -> list[TicketMessage]:
    return session.execute(
        select(TicketMessage)
        .where(TicketMessage.ticket_id == ticket.id, TicketMessage.message_type == message_type)
        .order_by(TicketMessage.created_at.asc(), TicketMessage.id.asc())
    ).scalars().all()


def _last_agent_message(session, ticket: Ticket) -> TicketMessage | None:
    return session.execute(
        select(TicketMessage)
        .where(TicketMessage.ticket_id == ticket.id, TicketMessage.message_type == MessageType.AGENT.value)
        .order_by(TicketMessage.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _first_client_message(session, ticket: Ticket) -> TicketMessage | None:
    return session.execute(
        select(TicketMessage)
        .where(TicketMessage.ticket_id == ticket.id, TicketMessage.message_type == MessageType.CLIENT.value)
        .order_by(TicketMessage.created_at.asc())
        .limit(1)
    ).scalar_one_or_none()


def _add_message(session, ticket, message_type, body, author_user_id, **kwargs):
    session.add(
        TicketMessage(
            id=str(uuid.uuid4()),
            ticket_id=ticket.id,
            message_type=message_type.value if hasattr(message_type, "value") else message_type,
            body=body,
            author_user_id=author_user_id,
            agent_visible=kwargs.get("agent_visible", True),
            created_at=datetime.utcnow(),
        )
    )


def _attachments(session, message_id: str):
    return session.execute(select(TicketAttachment).where(TicketAttachment.message_id == message_id)).scalars().all()


def _seed_agent_response(session, ticket: Ticket, agent: User) -> TicketMessage:
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.AGENT.value,
        body="Готово",
        author_user_id=agent.id,
        agent_visible=True,
        message_status="normal",
        created_at=datetime.utcnow(),
    )
    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=message.id,
        source="helpdesk",
        original_filename="result.txt",
        local_path="/tmp/result.txt",
        created_at=datetime.utcnow(),
    )
    session.add_all([message, attachment])
    return message


def _close_task(session, ticket: Ticket, actor: User, send_texts: list[str], sent_files: list[str]):
    return close_ticket_by_actor(
        session,
        ticket,
        SimpleNamespace(id=actor.id, role=actor.role, real_user_id=actor.id, real_role=actor.role, is_real_admin=False),
        force_without_agent_reply_for_task=False,
        is_admin_fn=lambda actor_obj: actor_obj.role == "admin",
        get_or_create_client_profile_fn=lambda *_: None,
        last_agent_message_fn=_last_agent_message,
        first_client_message_fn=_first_client_message,
        send_to_telegram_client_fn=lambda _db, _ticket, text, **_kwargs: send_texts.append(text) or {"ok": True},
        actor_display_name_fn=lambda *_: actor.full_name,
        add_message_fn=_add_message,
        message_attachments_fn=_attachments,
        send_attachment_to_telegram_client_fn=lambda _db, _ticket, attachment, **_kwargs: sent_files.append(attachment.id) or {"ok": True},
        onsite_close_reminder_text="onsite",
        ticket_closed_client_text="closed",
    )


def test_basic_materialization_creates_task_message_and_occurrence():
    session = _session()
    company = _company(session)
    creator = _user(session, company, "admin")
    responsible = _user(session, company, "admin")
    definition = _definition(session, company, creator, responsible)
    session.commit()

    result = materialize_internal_task(session, definition, datetime(2026, 9, 16, 8, 0, 0))
    session.flush()

    assert result.created is True
    assert result.ticket.workflow_type == TicketWorkflowType.TASK.value
    assert result.ticket.task_kind == TicketTaskKind.NORMAL.value
    assert result.ticket.agent_visible is True
    assert result.ticket.load_counted is True
    assert result.ticket.intake_state == TicketIntakeState.READY.value
    assert _messages(session, result.ticket, MessageType.CLIENT.value)[0].body == "Internal description"
    assert result.occurrence.ticket_id == result.ticket.id
    assert result.occurrence.description_snapshot == "Internal description"


def test_technical_client_is_stable_per_company_and_ignores_display_author():
    session = _session()
    company_a = _company(session, "A")
    company_b = _company(session, "B")
    creator_a = _user(session, company_a, "admin")
    responsible_a = _user(session, company_a, "admin")
    creator_b = _user(session, company_b, "admin")
    responsible_b = _user(session, company_b, "admin")
    first = _definition(session, company_a, creator_a, responsible_a, display_author=True)
    second = _definition(session, company_a, creator_a, responsible_a, title="Second", display_author=False)
    third = _definition(session, company_b, creator_b, responsible_b, display_author=True)
    session.commit()

    first_result = materialize_internal_task(session, first, datetime(2026, 9, 16, 8, 0, 0))
    second_result = materialize_internal_task(session, second, datetime(2026, 9, 16, 9, 0, 0))
    third_result = materialize_internal_task(session, third, datetime(2026, 9, 16, 8, 0, 0))
    session.flush()

    assert first_result.ticket.client_id == second_result.ticket.client_id
    assert first_result.ticket.client_id != third_result.ticket.client_id
    assert session.get(Client, first_result.ticket.client_id).display_name == INTERNAL_TASK_SYSTEM_CLIENT_NAME
    assert session.execute(select(func.count()).select_from(Client).where(Client.company_id == company_a.id)).scalar_one() == 1
    assert first_result.occurrence.display_author is True
    assert second_result.occurrence.display_author is False


def test_description_deadline_append_uses_app_timezone(monkeypatch):
    session = _session()
    monkeypatch.setattr(settings, "app_timezone", "Europe/Moscow")
    company = _company(session)
    creator = _user(session, company, "admin")
    responsible = _user(session, company, "admin")
    no_deadline = _definition(session, company, creator, responsible, description="Без дедлайна")
    with_deadline = _definition(
        session,
        company,
        creator,
        responsible,
        description="Проверить документы и дать ответ.",
        deadline_at_utc=datetime(2026, 9, 18, 12, 30, 0, tzinfo=timezone.utc),
    )
    session.commit()

    first = materialize_internal_task(session, no_deadline, datetime(2026, 9, 16, 8, 0, 0))
    second = materialize_internal_task(session, with_deadline, datetime(2026, 9, 16, 9, 0, 0))
    session.flush()

    assert first.occurrence.description_snapshot == "Без дедлайна"
    assert second.occurrence.description_snapshot == "Проверить документы и дать ответ.\n\nДедлайн: 18.09.2026 15:30"
    assert _messages(session, second.ticket, MessageType.CLIENT.value)[0].body == second.occurrence.description_snapshot


@pytest.mark.parametrize("role", ["agent", "trainee"])
def test_direct_active_support_user_is_assigned_even_when_not_online(role):
    session = _session()
    company = _company(session)
    creator = _user(session, company, "admin")
    responsible = _user(session, company, "admin")
    assignee = _user(session, company, role, availability_status="lunch")
    definition = _definition(
        session,
        company,
        creator,
        responsible,
        assignment_mode=InternalTaskAssignmentMode.DIRECT.value,
        assignee_user_id=assignee.id,
    )
    session.commit()

    result = materialize_internal_task(session, definition, datetime(2026, 9, 16, 8, 0, 0))
    session.flush()

    assert result.ticket.assignee_id == assignee.id
    assert result.ticket.status == TicketStatus.NEW.value
    assert result.occurrence.effective_assignment_mode == InternalTaskAssignmentMode.DIRECT.value
    assert result.occurrence.effective_assignee_user_id == assignee.id


@pytest.mark.parametrize(
    "candidate_role,is_active",
    [
        ("agent", False),
        ("admin", True),
    ],
)
def test_direct_invalid_assignee_falls_back_to_distribution(candidate_role, is_active):
    session = _session()
    company = _company(session)
    creator = _user(session, company, "admin")
    responsible = _user(session, company, "admin")
    candidate = _user(session, company, candidate_role, is_active=is_active, availability_status="online")
    definition = _definition(
        session,
        company,
        creator,
        responsible,
        assignment_mode=InternalTaskAssignmentMode.DIRECT.value,
        assignee_user_id=candidate.id,
    )
    session.commit()

    result = materialize_internal_task(session, definition, datetime(2026, 9, 16, 8, 0, 0))
    session.flush()

    assert definition.assignment_mode == InternalTaskAssignmentMode.DIRECT.value
    assert definition.assignee_user_id == candidate.id
    assert result.occurrence.effective_assignment_mode == InternalTaskAssignmentMode.DISTRIBUTION.value
    assert result.occurrence.effective_assignee_user_id is None
    assert result.ticket.assignee_id is None
    assert result.ticket.status == TicketStatus.UNASSIGNED.value


def test_direct_missing_user_falls_back_without_mutating_definition():
    session = _session()
    company = _company(session)
    creator = _user(session, company, "admin")
    responsible = _user(session, company, "admin")
    missing_user_id = str(uuid.uuid4())
    definition = _definition(
        session,
        company,
        creator,
        responsible,
        assignment_mode=InternalTaskAssignmentMode.DIRECT.value,
        assignee_user_id=missing_user_id,
    )
    session.commit()

    result = materialize_internal_task(session, definition, datetime(2026, 9, 16, 8, 0, 0))
    session.flush()

    assert definition.assignment_mode == InternalTaskAssignmentMode.DIRECT.value
    assert definition.assignee_user_id == missing_user_id
    assert result.occurrence.effective_assignment_mode == InternalTaskAssignmentMode.DISTRIBUTION.value
    assert result.occurrence.effective_assignee_user_id is None
    assert result.ticket.assignee_id is None


@pytest.mark.parametrize("role", ["agent", "trainee"])
def test_direct_cross_company_support_user_falls_back_to_distribution(role):
    session = _session()
    company = _company(session, "Primary")
    other_company = _company(session, "Other")
    creator = _user(session, company, "admin")
    responsible = _user(session, company, "admin")
    cross_company_assignee = _user(session, other_company, role, availability_status="online")
    definition = _definition(
        session,
        company,
        creator,
        responsible,
        assignment_mode=InternalTaskAssignmentMode.DIRECT.value,
        assignee_user_id=cross_company_assignee.id,
    )
    session.commit()

    result = materialize_internal_task(session, definition, datetime(2026, 9, 16, 8, 0, 0))
    session.flush()

    assert definition.assignment_mode == InternalTaskAssignmentMode.DIRECT.value
    assert definition.assignee_user_id == cross_company_assignee.id
    assert result.occurrence.effective_assignment_mode == InternalTaskAssignmentMode.DISTRIBUTION.value
    assert result.occurrence.effective_assignee_user_id is None
    assert result.ticket.assignee_id is None
    assert result.ticket.status == TicketStatus.UNASSIGNED.value


def test_distribution_uses_existing_online_agent_assignment_when_available():
    session = _session()
    company = _company(session)
    creator = _user(session, company, "admin")
    responsible = _user(session, company, "admin")
    online_agent = _user(session, company, "agent", availability_status="online")
    definition = _definition(session, company, creator, responsible)
    session.commit()

    result = materialize_internal_task(session, definition, datetime(2026, 9, 16, 8, 0, 0))
    session.flush()

    assert result.ticket.assignee_id == online_agent.id
    assert result.ticket.status == TicketStatus.NEW.value
    assert result.occurrence.effective_assignment_mode == InternalTaskAssignmentMode.DISTRIBUTION.value
    assert result.occurrence.effective_assignee_user_id is None


def test_distribution_assigns_only_new_internal_ticket_and_leaves_existing_queue_untouched():
    session = _session()
    company = _company(session)
    creator = _user(session, company, "admin")
    responsible = _user(session, company, "admin")
    online_agent = _user(session, company, "agent", availability_status="online")
    existing_client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Existing client")
    existing_ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=existing_client.id,
        assignee_id=None,
        subject="Existing queued task",
        workflow_type=TicketWorkflowType.TASK.value,
        intake_state=TicketIntakeState.READY.value,
        task_kind=TicketTaskKind.NORMAL.value,
        status=TicketStatus.UNASSIGNED.value,
        agent_visible=True,
        load_counted=True,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    definition = _definition(session, company, creator, responsible)
    session.add_all([existing_client, existing_ticket])
    session.commit()

    result = materialize_internal_task(session, definition, datetime(2026, 9, 16, 8, 0, 0))
    session.flush()

    assert result.ticket.id != existing_ticket.id
    assert result.ticket.assignee_id == online_agent.id
    assert result.ticket.status == TicketStatus.NEW.value
    assert existing_ticket.assignee_id is None
    assert existing_ticket.status == TicketStatus.UNASSIGNED.value


def test_occurrence_snapshot_survives_definition_changes():
    session = _session()
    company = _company(session)
    creator = _user(session, company, "admin")
    responsible = _user(session, company, "admin")
    definition = _definition(session, company, creator, responsible, title="Before", description="Before description")
    session.commit()

    result = materialize_internal_task(session, definition, datetime(2026, 9, 16, 8, 0, 0))
    definition.title = "After"
    definition.description = "After description"
    definition.display_author = False
    session.flush()

    assert result.occurrence.title_snapshot == "Before"
    assert result.occurrence.description_snapshot == "Before description"
    assert result.occurrence.display_author is True


def test_materialization_is_idempotent_for_definition_and_occurrence_time():
    session = _session()
    company = _company(session)
    creator = _user(session, company, "admin")
    responsible = _user(session, company, "admin")
    assignee = _user(session, company, "agent", availability_status="offline")
    definition = _definition(
        session,
        company,
        creator,
        responsible,
        assignment_mode=InternalTaskAssignmentMode.DIRECT.value,
        assignee_user_id=assignee.id,
    )
    occurrence_at = datetime(2026, 9, 16, 8, 0, 0)
    session.commit()

    first = materialize_internal_task(session, definition, occurrence_at)
    second = materialize_internal_task(session, definition, occurrence_at)
    session.flush()

    assert first.created is True
    assert second.created is False
    assert first.ticket.id == second.ticket.id
    assert first.occurrence.id == second.occurrence.id
    assert session.execute(select(func.count()).select_from(Ticket)).scalar_one() == 1
    assert session.execute(select(func.count()).select_from(InternalTaskOccurrence)).scalar_one() == 1
    assert len(_messages(session, first.ticket, MessageType.CLIENT.value)) == 1


@pytest.mark.parametrize(
    ("schedule_kind", "expected_state"),
    [
        (InternalTaskScheduleKind.IMMEDIATE.value, InternalTaskDefinitionState.FINISHED.value),
        (InternalTaskScheduleKind.SCHEDULED.value, InternalTaskDefinitionState.FINISHED.value),
        (InternalTaskScheduleKind.RECURRING.value, InternalTaskDefinitionState.ACTIVE.value),
    ],
)
def test_one_off_definitions_finish_but_recurring_remains_active(schedule_kind, expected_state):
    session = _session()
    company = _company(session)
    creator = _user(session, company, "admin")
    responsible = _user(session, company, "admin")
    definition = _definition(session, company, creator, responsible, schedule_kind=schedule_kind)
    session.commit()

    materialize_internal_task(session, definition, datetime(2026, 9, 16, 8, 0, 0))
    session.flush()

    assert definition.state == expected_state


@pytest.mark.parametrize("channel", ["telegram", "web"])
def test_internal_task_close_suppresses_client_telegram_but_normal_task_still_sends(channel):
    session = _session()
    company = _company(session)
    creator = _user(session, company, "admin")
    responsible = _user(session, company, "admin")
    agent = _user(session, company, "agent")
    definition = _definition(session, company, creator, responsible)
    session.commit()
    internal_result = materialize_internal_task(session, definition, datetime(2026, 9, 16, 8, 0, 0))
    normal_client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Normal client")
    normal_ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=normal_client.id,
        assignee_id=agent.id,
        subject="Normal task",
        workflow_type=TicketWorkflowType.TASK.value,
        intake_state=TicketIntakeState.READY.value,
        task_kind=TicketTaskKind.NORMAL.value,
        status=TicketStatus.NEW.value,
        agent_visible=True,
        load_counted=True,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    normal_initial = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=normal_ticket.id,
        message_type=MessageType.CLIENT.value,
        body="Normal task body",
        agent_visible=True,
        created_at=datetime.utcnow(),
    )
    session.add_all([normal_client, normal_ticket, normal_initial])
    internal_result.ticket.channel = normal_ticket.channel = channel
    _seed_agent_response(session, internal_result.ticket, agent)
    _seed_agent_response(session, normal_ticket, agent)
    session.flush()

    internal_texts: list[str] = []
    internal_files: list[str] = []
    normal_texts: list[str] = []
    normal_files: list[str] = []
    assert _close_task(session, internal_result.ticket, agent, internal_texts, internal_files) == "closed"
    assert _close_task(session, normal_ticket, agent, normal_texts, normal_files) == "closed"

    assert internal_texts == []
    assert internal_files == []
    assert bool(normal_texts) == (channel == "telegram")
    assert bool(normal_files) == (channel == "telegram")
