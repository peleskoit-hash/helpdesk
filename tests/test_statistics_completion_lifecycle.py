from datetime import datetime
from types import SimpleNamespace
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.db import Base
from api.app.models import (
    Client,
    Company,
    MessageType,
    StatsCompletion,
    Ticket,
    TicketMessage,
    TicketStatus,
    TicketTaskKind,
    TicketWorkflowType,
    User,
)
from api.app.routers import tickets as tickets_router
from api.app.services.statistics_lifecycle import sync_completion_after_close, sync_completion_after_reopen
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


def _company(session) -> Company:
    company = Company(id=str(uuid.uuid4()), name="ACME", created_at=datetime.utcnow())
    session.add(company)
    session.flush()
    return company


def _client(session, company: Company) -> Client:
    client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Client")
    session.add(client)
    session.flush()
    return client


def _user(session, company: Company, user_id: str, *, role: str = "agent") -> User:
    user = User(
        id=user_id,
        company_id=company.id,
        email=f"{user_id}@example.test",
        login=user_id,
        password_hash="hash",
        full_name=user_id,
        role=role,
        is_active=True,
        availability_status="online",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(user)
    session.flush()
    return user


def _ticket(
    session,
    company: Company,
    client: Client,
    ticket_id: str,
    *,
    assignee_id: str | None = None,
    status: str = TicketStatus.PENDING.value,
    workflow_type: str = TicketWorkflowType.TICKET.value,
    task_kind: str | None = TicketTaskKind.NORMAL.value,
    parent_ticket_id: str | None = None,
) -> Ticket:
    ticket = Ticket(
        id=ticket_id,
        company_id=company.id,
        client_id=client.id,
        assignee_id=assignee_id,
        subject=ticket_id,
        status=status,
        workflow_type=workflow_type,
        task_kind=task_kind,
        parent_ticket_id=parent_ticket_id,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(ticket)
    session.flush()
    return ticket


def _message(
    session,
    ticket: Ticket,
    message_type: MessageType,
    *,
    author_user_id: str | None = None,
    body: str = "Done",
) -> TicketMessage:
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=message_type.value,
        body=body,
        author_user_id=author_user_id,
        agent_visible=True,
        message_status="normal",
        created_at=datetime.utcnow(),
    )
    session.add(message)
    session.flush()
    return message


def _add_message(db, ticket, message_type, body, author_user_id=None, **kwargs):
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=message_type.value if hasattr(message_type, "value") else message_type,
        body=body,
        author_user_id=author_user_id,
        agent_visible=kwargs.get("agent_visible", True),
        message_status="normal",
        created_at=datetime.utcnow(),
    )
    db.add(message)
    db.flush()
    return message


def _completion_rows(session, ticket: Ticket) -> list[StatsCompletion]:
    return session.execute(
        select(StatsCompletion)
        .where(StatsCompletion.company_id == ticket.company_id, StatsCompletion.object_id == ticket.id)
        .order_by(StatsCompletion.created_at, StatsCompletion.id)
    ).scalars().all()


def _close_kwargs(agent_message=None, client_message=None):
    return dict(
        is_admin_fn=lambda actor: actor.role == "admin",
        get_or_create_client_profile_fn=lambda *_args, **_kwargs: None,
        last_agent_message_fn=lambda *_args, **_kwargs: agent_message,
        first_client_message_fn=lambda *_args, **_kwargs: client_message,
        send_to_telegram_client_fn=lambda *_args, **_kwargs: None,
        actor_display_name_fn=lambda *_args, **_kwargs: "Actor",
        add_message_fn=_add_message,
        message_attachments_fn=lambda *_args, **_kwargs: [],
        send_attachment_to_telegram_client_fn=lambda *_args, **_kwargs: None,
        onsite_close_reminder_text="onsite",
        ticket_closed_client_text="closed",
    )


def test_completion_first_close_and_repeat_close_keep_single_completed_row():
    session = _session()
    company = _company(session)
    client = _client(session, company)
    _user(session, company, "ivan", role="agent")
    ticket = _ticket(session, company, client, "ticket-1", assignee_id="ivan")
    agent_message = _message(session, ticket, MessageType.AGENT, author_user_id="ivan")

    first = close_ticket_by_actor(
        session,
        ticket,
        SimpleNamespace(id="ivan", role="agent"),
        **_close_kwargs(agent_message=agent_message),
    )
    second = close_ticket_by_actor(
        session,
        ticket,
        SimpleNamespace(id="ivan", role="agent"),
        **_close_kwargs(agent_message=agent_message),
    )

    assert first == "closed"
    assert second == "already_closed"
    rows = _completion_rows(session, ticket)
    assert len(rows) == 1
    assert rows[0].is_completed is True
    assert rows[0].completed_by_user_id == "ivan"
    assert rows[0].completed_at == ticket.closed_at
    assert ticket.completed_by_user_id == "ivan"
    assert ticket.completed_counted_at == ticket.closed_at


def test_completion_reopen_clears_same_row_and_reclose_updates_to_new_agent():
    session = _session()
    company = _company(session)
    client = _client(session, company)
    _user(session, company, "admin", role="admin")
    _user(session, company, "ivan", role="agent")
    _user(session, company, "petr", role="agent")
    ticket = _ticket(session, company, client, "ticket-reopen", assignee_id="ivan")
    ivan_message = _message(session, ticket, MessageType.AGENT, author_user_id="ivan")
    close_ticket_by_actor(
        session,
        ticket,
        SimpleNamespace(id="ivan", role="agent"),
        **_close_kwargs(agent_message=ivan_message),
    )
    first_row_id = _completion_rows(session, ticket)[0].id

    result = tickets_router.restore(ticket.id, db=session, user=SimpleNamespace(id="admin", role="admin"))

    assert result["ok"] is True
    rows_after_reopen = _completion_rows(session, ticket)
    assert len(rows_after_reopen) == 1
    assert rows_after_reopen[0].id == first_row_id
    assert rows_after_reopen[0].is_completed is False
    assert rows_after_reopen[0].completed_by_user_id is None
    assert rows_after_reopen[0].completed_at is None
    assert ticket.completed_by_user_id is None
    assert ticket.completed_counted_at is None

    ticket.assignee_id = "petr"
    petr_message = _message(session, ticket, MessageType.AGENT, author_user_id="petr")
    close_ticket_by_actor(
        session,
        ticket,
        SimpleNamespace(id="petr", role="agent"),
        **_close_kwargs(agent_message=petr_message),
    )

    rows_after_reclose = _completion_rows(session, ticket)
    assert len(rows_after_reclose) == 1
    assert rows_after_reclose[0].id == first_row_id
    assert rows_after_reclose[0].is_completed is True
    assert rows_after_reclose[0].completed_by_user_id == "petr"
    assert rows_after_reclose[0].completed_at == ticket.closed_at
    assert ticket.completed_by_user_id == "petr"
    assert ticket.completed_counted_at == ticket.closed_at


def test_admin_close_uses_existing_resolver_attribution():
    session = _session()
    company = _company(session)
    client = _client(session, company)
    _user(session, company, "admin", role="admin")
    _user(session, company, "agent-a", role="agent")
    ticket = _ticket(session, company, client, "ticket-admin", assignee_id="agent-a")
    _message(session, ticket, MessageType.AGENT, author_user_id="agent-a", body="Assignee answer")

    result = close_ticket_by_actor(
        session,
        ticket,
        SimpleNamespace(id="admin", role="admin"),
        force_without_agent_reply_for_task=True,
        **_close_kwargs(agent_message=None),
    )

    assert result == "closed"
    row = _completion_rows(session, ticket)[0]
    assert row.completed_by_user_id == "agent-a"
    assert row.completed_at == ticket.closed_at
    assert ticket.completed_by_user_id == "agent-a"
    assert ticket.completed_counted_at == ticket.closed_at


def test_completion_with_unresolved_attribution_still_marks_object_completed():
    session = _session()
    company = _company(session)
    client = _client(session, company)
    _user(session, company, "admin", role="admin")
    ticket = _ticket(session, company, client, "ticket-no-attribution", assignee_id=None)

    result = close_ticket_by_actor(
        session,
        ticket,
        SimpleNamespace(id="admin", role="admin"),
        force_without_agent_reply_for_task=True,
        **_close_kwargs(agent_message=None),
    )

    assert result == "closed"
    row = _completion_rows(session, ticket)[0]
    assert row.is_completed is True
    assert row.completed_by_user_id is None
    assert row.completed_at == ticket.closed_at
    assert ticket.completed_by_user_id is None
    assert ticket.completed_counted_at == ticket.closed_at


def test_real_normal_task_close_creates_completion():
    session = _session()
    company = _company(session)
    client = _client(session, company)
    _user(session, company, "agent-a", role="agent")
    task = _ticket(
        session,
        company,
        client,
        "task-normal",
        assignee_id="agent-a",
        workflow_type=TicketWorkflowType.TASK.value,
        task_kind=TicketTaskKind.NORMAL.value,
    )
    client_message = _message(session, task, MessageType.CLIENT, body="Task body")
    agent_message = _message(session, task, MessageType.AGENT, author_user_id="agent-a", body="Task answer")

    result = close_ticket_by_actor(
        session,
        task,
        SimpleNamespace(id="agent-a", role="agent"),
        **_close_kwargs(agent_message=agent_message, client_message=client_message),
    )

    assert result == "closed"
    row = _completion_rows(session, task)[0]
    assert row.object_type == "task"
    assert row.task_kind == TicketTaskKind.NORMAL.value
    assert row.completed_by_user_id == "agent-a"
    assert row.completed_at == task.closed_at


def test_real_group_parent_close_does_not_create_completion_or_legacy_completion():
    session = _session()
    company = _company(session)
    client = _client(session, company)
    _user(session, company, "agent-a", role="agent")
    parent = _ticket(
        session,
        company,
        client,
        "task-group-parent",
        assignee_id="agent-a",
        workflow_type=TicketWorkflowType.TASK.value,
        task_kind=TicketTaskKind.GROUP_PARENT.value,
    )
    client_message = _message(session, parent, MessageType.CLIENT, body="Parent body")
    agent_message = _message(session, parent, MessageType.AGENT, author_user_id="agent-a", body="Parent answer")

    result = close_ticket_by_actor(
        session,
        parent,
        SimpleNamespace(id="agent-a", role="agent"),
        **_close_kwargs(agent_message=agent_message, client_message=client_message),
    )

    assert result == "closed"
    assert _completion_rows(session, parent) == []
    assert parent.completed_by_user_id is None
    assert parent.completed_counted_at is None


def test_completion_object_identity_whitelist():
    session = _session()
    company = _company(session)
    client = _client(session, company)
    completed_at = datetime(2026, 9, 12, 12, 0, 0)
    cases = [
        (TicketWorkflowType.TASK.value, TicketTaskKind.NORMAL.value, True, TicketTaskKind.NORMAL.value),
        (TicketWorkflowType.TASK.value, TicketTaskKind.GROUP_CHILD.value, True, TicketTaskKind.GROUP_CHILD.value),
        (TicketWorkflowType.TASK.value, TicketTaskKind.GROUP_PARENT.value, False, None),
        (TicketWorkflowType.AUTO.value, TicketTaskKind.NORMAL.value, False, None),
        (TicketWorkflowType.TASK.value, "future_unknown_kind", False, None),
        (TicketWorkflowType.TASK.value, None, True, TicketTaskKind.NORMAL.value),
    ]

    for workflow_type, task_kind, expected_created, expected_task_kind in cases:
        ticket = _ticket(
            session,
            company,
            client,
            f"{workflow_type}-{task_kind}",
            workflow_type=workflow_type,
            task_kind=task_kind,
        )
        sync_completion_after_close(
            session,
            ticket,
            completed_by_user_id="agent-a",
            completed_at=completed_at,
        )
        rows = _completion_rows(session, ticket)
        if expected_created:
            assert len(rows) == 1
            assert rows[0].object_type == "task"
            assert rows[0].task_kind == expected_task_kind
            assert rows[0].is_completed is True
        else:
            assert rows == []


def test_completion_reopen_helper_keeps_single_incomplete_row():
    session = _session()
    company = _company(session)
    client = _client(session, company)
    ticket = _ticket(session, company, client, "ticket-helper-reopen")
    sync_completion_after_close(
        session,
        ticket,
        completed_by_user_id="agent-a",
        completed_at=datetime(2026, 9, 12, 12, 0, 0),
    )
    row_id = _completion_rows(session, ticket)[0].id

    sync_completion_after_reopen(session, ticket)

    rows = _completion_rows(session, ticket)
    assert len(rows) == 1
    assert rows[0].id == row_id
    assert rows[0].is_completed is False
    assert rows[0].completed_by_user_id is None
    assert rows[0].completed_at is None


def test_completion_survives_source_ticket_and_user_delete():
    session = _session()
    company = _company(session)
    client = _client(session, company)
    user = _user(session, company, "agent-a", role="agent")
    ticket = _ticket(session, company, client, "ticket-survival", assignee_id=user.id)
    completed_at = datetime(2026, 9, 12, 12, 0, 0)
    sync_completion_after_close(
        session,
        ticket,
        completed_by_user_id=user.id,
        completed_at=completed_at,
    )
    row_id = _completion_rows(session, ticket)[0].id
    ticket_id = ticket.id

    session.delete(ticket)
    session.delete(user)
    session.flush()

    row = session.get(StatsCompletion, row_id)
    assert row is not None
    assert row.object_id == ticket_id
    assert row.completed_by_user_id == "agent-a"
    assert row.completed_at == completed_at
