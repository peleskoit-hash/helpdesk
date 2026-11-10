from datetime import datetime, timedelta
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
    StatsWorkInterval,
    TelegramContact,
    Ticket,
    TicketIntakeState,
    TicketMessage,
    TicketStatus,
    TicketTaskKind,
    TicketWorkflowType,
    User,
)
from api.app.routers import telegram as telegram_router
from api.app.routers import tickets as tickets_router
from api.app.schemas import TextPayload
from api.app.services import assignment, auto_close, telegram_automation
from api.app.services.bitrix_client import BitrixDealPhoneLookupResult
from api.app.services.statistics_lifecycle import (
    open_work_interval_if_current,
    sync_work_interval_after_assignment_change,
    sync_work_interval_after_close,
    sync_work_interval_after_freeze,
    sync_work_interval_after_reopen,
    sync_work_interval_after_unfreeze,
)
from api.app.services.telegram_client_close import close_active_ticket_for_contact, process_client_close_callback
from api.app.services.ticket_lifecycle import close_ticket_by_actor, freeze_ticket, unfreeze_ticket, unfreeze_ticket_on_message
from api.app.services.ticket_messages import comment_on_ticket


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


def _user(session, company: Company, *, role: str = "agent") -> User:
    user = User(
        id=str(uuid.uuid4()),
        company_id=company.id,
        email=f"{uuid.uuid4().hex}@example.test",
        login=f"user-{uuid.uuid4().hex}",
        password_hash="hash",
        full_name="User",
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
    *,
    client_id: str | None = None,
    assignee_id: str | None = None,
    status: str = TicketStatus.NEW.value,
    workflow_type: str = TicketWorkflowType.TICKET.value,
    task_kind: str | None = TicketTaskKind.NORMAL.value,
    is_frozen: bool = False,
) -> Ticket:
    ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client_id or str(uuid.uuid4()),
        assignee_id=assignee_id,
        subject="Need help",
        status=status,
        workflow_type=workflow_type,
        task_kind=task_kind,
        is_frozen=is_frozen,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(ticket)
    session.flush()
    return ticket


def _work_rows(session, ticket: Ticket) -> list[StatsWorkInterval]:
    return session.execute(
        select(StatsWorkInterval)
        .where(StatsWorkInterval.object_id == ticket.id)
        .order_by(StatsWorkInterval.started_at, StatsWorkInterval.id)
    ).scalars().all()


def _contact(session, company: Company, client: Client) -> TelegramContact:
    contact = TelegramContact(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        external_user_id=str(uuid.uuid4()),
        external_chat_id=f"chat-{uuid.uuid4().hex}",
        external_username="client",
        external_display_name="Client",
    )
    session.add(contact)
    session.flush()
    return contact


def _add_message(db, ticket, message_type, body, author_user_id=None, **kwargs):
    db.add(
        TicketMessage(
            id=str(uuid.uuid4()),
            ticket_id=ticket.id,
            message_type=message_type.value if hasattr(message_type, "value") else message_type,
            body=body,
            author_user_id=author_user_id,
            agent_visible=kwargs.get("agent_visible", True),
            message_status="normal",
            created_at=datetime.utcnow(),
        )
    )
    db.flush()


def _append_system_message(db, ticket, body, **kwargs):
    _add_message(db, ticket, MessageType.SYSTEM, body, None, **kwargs)


def _append_client_message(db, ticket, body, telegram_message_id=None):
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.CLIENT.value,
        body=body,
        author_user_id=None,
        agent_visible=True,
        telegram_message_id=telegram_message_id,
        message_status="normal",
        created_at=datetime.utcnow(),
    )
    db.add(message)
    db.flush()
    return message


def _agent_message(session, ticket: Ticket, agent: User) -> TicketMessage:
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.AGENT.value,
        body="Done",
        author_user_id=agent.id,
        agent_visible=True,
        message_status="normal",
        created_at=datetime.utcnow(),
    )
    session.add(message)
    session.flush()
    return message


def test_assignment_from_unassigned_to_agent_opens_interval():
    session = _session()
    company = _company(session)
    agent = _user(session, company)
    ticket = _ticket(session, company)
    now = datetime(2026, 9, 12, 9, 0, 0)

    ticket.assignee_id = agent.id
    sync_work_interval_after_assignment_change(session, ticket, old_assignee_id=None, new_assignee_id=agent.id, now=now)

    rows = _work_rows(session, ticket)
    assert [(row.user_id, row.started_at, row.ended_at, row.end_reason) for row in rows] == [
        (agent.id, now, None, None),
    ]


def test_reassignment_closes_old_interval_and_opens_new_one():
    session = _session()
    company = _company(session)
    agent_a = _user(session, company)
    agent_b = _user(session, company)
    start = datetime(2026, 9, 12, 9, 0, 0)
    now = datetime(2026, 9, 12, 10, 0, 0)
    ticket = _ticket(session, company, assignee_id=agent_a.id)
    open_work_interval_if_current(session, ticket, now=start)

    ticket.assignee_id = agent_b.id
    sync_work_interval_after_assignment_change(session, ticket, old_assignee_id=agent_a.id, new_assignee_id=agent_b.id, now=now)

    rows = _work_rows(session, ticket)
    assert [(row.user_id, row.started_at, row.ended_at, row.end_reason) for row in rows] == [
        (agent_a.id, start, now, "reassignment"),
        (agent_b.id, now, None, None),
    ]


def test_unassignment_closes_current_interval():
    session = _session()
    company = _company(session)
    agent = _user(session, company)
    start = datetime(2026, 9, 12, 9, 0, 0)
    now = datetime(2026, 9, 12, 10, 0, 0)
    ticket = _ticket(session, company, assignee_id=agent.id)
    open_work_interval_if_current(session, ticket, now=start)

    ticket.assignee_id = None
    sync_work_interval_after_assignment_change(session, ticket, old_assignee_id=agent.id, new_assignee_id=None, now=now)

    rows = _work_rows(session, ticket)
    assert [(row.user_id, row.started_at, row.ended_at, row.end_reason) for row in rows] == [
        (agent.id, start, now, "unassignment"),
    ]


def test_same_assignee_does_not_split_interval():
    session = _session()
    company = _company(session)
    agent = _user(session, company)
    start = datetime(2026, 9, 12, 9, 0, 0)
    now = datetime(2026, 9, 12, 10, 0, 0)
    ticket = _ticket(session, company, assignee_id=agent.id)
    open_work_interval_if_current(session, ticket, now=start)

    sync_work_interval_after_assignment_change(session, ticket, old_assignee_id=agent.id, new_assignee_id=agent.id, now=now)

    rows = _work_rows(session, ticket)
    assert [(row.user_id, row.started_at, row.ended_at, row.end_reason) for row in rows] == [
        (agent.id, start, None, None),
    ]


def test_assignment_to_admin_closes_old_without_opening_new_interval():
    session = _session()
    company = _company(session)
    agent = _user(session, company)
    admin = _user(session, company, role="admin")
    start = datetime(2026, 9, 12, 9, 0, 0)
    now = datetime(2026, 9, 12, 10, 0, 0)
    ticket = _ticket(session, company, assignee_id=agent.id)
    open_work_interval_if_current(session, ticket, now=start)

    ticket.assignee_id = admin.id
    sync_work_interval_after_assignment_change(session, ticket, old_assignee_id=agent.id, new_assignee_id=admin.id, now=now)

    rows = _work_rows(session, ticket)
    assert [(row.user_id, row.started_at, row.ended_at, row.end_reason) for row in rows] == [
        (agent.id, start, now, "reassignment"),
    ]


def test_assignment_to_frozen_object_does_not_open_interval():
    session = _session()
    company = _company(session)
    agent = _user(session, company)
    ticket = _ticket(session, company, is_frozen=True)
    now = datetime(2026, 9, 12, 10, 0, 0)

    ticket.assignee_id = agent.id
    sync_work_interval_after_assignment_change(session, ticket, old_assignee_id=None, new_assignee_id=agent.id, now=now)

    assert _work_rows(session, ticket) == []


def test_freeze_closes_current_interval_and_repeated_freeze_is_noop():
    session = _session()
    company = _company(session)
    agent = _user(session, company)
    start = datetime(2026, 9, 12, 9, 0, 0)
    now = datetime(2026, 9, 12, 10, 0, 0)
    ticket = _ticket(session, company, assignee_id=agent.id)
    open_work_interval_if_current(session, ticket, now=start)

    ticket.is_frozen = True
    sync_work_interval_after_freeze(session, ticket, now=now)
    sync_work_interval_after_freeze(session, ticket, now=datetime(2026, 9, 12, 11, 0, 0))

    rows = _work_rows(session, ticket)
    assert [(row.user_id, row.started_at, row.ended_at, row.end_reason) for row in rows] == [
        (agent.id, start, now, "freeze"),
    ]


def test_unfreeze_opens_interval_for_current_support_assignee_only_once():
    session = _session()
    company = _company(session)
    agent = _user(session, company)
    now = datetime(2026, 9, 12, 10, 0, 0)
    ticket = _ticket(session, company, assignee_id=agent.id, is_frozen=False)

    sync_work_interval_after_unfreeze(session, ticket, now=now)
    sync_work_interval_after_unfreeze(session, ticket, now=datetime(2026, 9, 12, 11, 0, 0))

    rows = _work_rows(session, ticket)
    assert [(row.user_id, row.started_at, row.ended_at, row.end_reason) for row in rows] == [
        (agent.id, now, None, None),
    ]


def test_unfreeze_without_support_assignee_does_not_open_interval():
    session = _session()
    company = _company(session)
    admin = _user(session, company, role="admin")
    now = datetime(2026, 9, 12, 10, 0, 0)
    no_assignee = _ticket(session, company, assignee_id=None, is_frozen=False)
    admin_assignee = _ticket(session, company, assignee_id=admin.id, is_frozen=False)

    sync_work_interval_after_unfreeze(session, no_assignee, now=now)
    sync_work_interval_after_unfreeze(session, admin_assignee, now=now)

    assert _work_rows(session, no_assignee) == []
    assert _work_rows(session, admin_assignee) == []


def test_close_closes_all_open_intervals():
    session = _session()
    company = _company(session)
    agent_a = _user(session, company)
    agent_b = _user(session, company)
    ticket = _ticket(session, company, assignee_id=agent_a.id)
    start_a = datetime(2026, 9, 12, 9, 0, 0)
    start_b = datetime(2026, 9, 12, 9, 30, 0)
    now = datetime(2026, 9, 12, 10, 0, 0)
    open_work_interval_if_current(session, ticket, now=start_a)
    ticket.assignee_id = agent_b.id
    open_work_interval_if_current(session, ticket, now=start_b)

    ticket.status = TicketStatus.CLOSED.value
    sync_work_interval_after_close(session, ticket, now=now)

    rows = _work_rows(session, ticket)
    assert [(row.user_id, row.started_at, row.ended_at, row.end_reason) for row in rows] == [
        (agent_a.id, start_a, now, "close"),
        (agent_b.id, start_b, now, "close"),
    ]


def test_reopen_opens_new_interval_after_close():
    session = _session()
    company = _company(session)
    agent = _user(session, company)
    start = datetime(2026, 9, 12, 9, 0, 0)
    closed_at = datetime(2026, 9, 12, 10, 0, 0)
    reopened_at = datetime(2026, 9, 12, 11, 0, 0)
    ticket = _ticket(session, company, assignee_id=agent.id)
    open_work_interval_if_current(session, ticket, now=start)
    ticket.status = TicketStatus.CLOSED.value
    sync_work_interval_after_close(session, ticket, now=closed_at)

    ticket.status = TicketStatus.NEW.value
    sync_work_interval_after_reopen(session, ticket, now=reopened_at)

    rows = _work_rows(session, ticket)
    assert [(row.user_id, row.started_at, row.ended_at, row.end_reason) for row in rows] == [
        (agent.id, start, closed_at, "close"),
        (agent.id, reopened_at, None, None),
    ]


def test_reopen_without_assignee_does_not_open_interval():
    session = _session()
    company = _company(session)
    ticket = _ticket(session, company, assignee_id=None, status=TicketStatus.NEW.value)

    sync_work_interval_after_reopen(session, ticket, now=datetime(2026, 9, 12, 11, 0, 0))

    assert _work_rows(session, ticket) == []


def test_work_object_identity_tracks_only_supported_ticket_and_task_kinds():
    session = _session()
    company = _company(session)
    agent = _user(session, company)
    now = datetime(2026, 9, 12, 9, 0, 0)
    cases = [
        (TicketWorkflowType.TICKET.value, TicketTaskKind.NORMAL.value, True, "ticket", None),
        (TicketWorkflowType.TASK.value, TicketTaskKind.NORMAL.value, True, "task", TicketTaskKind.NORMAL.value),
        (TicketWorkflowType.TASK.value, TicketTaskKind.GROUP_CHILD.value, True, "task", TicketTaskKind.GROUP_CHILD.value),
        (TicketWorkflowType.TASK.value, TicketTaskKind.GROUP_PARENT.value, False, None, None),
        (TicketWorkflowType.TASK.value, None, True, "task", TicketTaskKind.NORMAL.value),
        (TicketWorkflowType.TASK.value, "future_unknown_kind", False, None, None),
        (TicketWorkflowType.AUTO.value, TicketTaskKind.NORMAL.value, False, None, None),
    ]

    for workflow_type, task_kind, expected_created, object_type, expected_task_kind in cases:
        ticket = _ticket(session, company, assignee_id=agent.id, workflow_type=workflow_type, task_kind=task_kind)
        created = open_work_interval_if_current(session, ticket, now=now)
        rows = _work_rows(session, ticket)
        assert created is expected_created
        if expected_created:
            assert len(rows) == 1
            assert rows[0].object_type == object_type
            assert rows[0].task_kind == expected_task_kind
        else:
            assert rows == []


def test_real_assignment_service_opens_work_interval():
    session = _session()
    company = _company(session)
    agent = _user(session, company)
    ticket = _ticket(session, company, status=TicketStatus.UNASSIGNED.value)

    assigned = assignment.assign_ticket_to_agent(session, ticket, agent, notify_client=False)

    assert assigned is True
    rows = _work_rows(session, ticket)
    assert len(rows) == 1
    assert rows[0].user_id == agent.id
    assert rows[0].ended_at is None
    assert rows[0].end_reason is None


def test_real_forced_assignment_creates_and_switches_work_interval():
    session = _session()
    company = _company(session)
    agent_a = _user(session, company)
    agent_b = _user(session, company)
    ticket = _ticket(session, company, status=TicketStatus.UNASSIGNED.value)

    assigned_a, error_a, _candidate_a = assignment.assign_ticket_to_forced_user(session, ticket, "user_id", agent_a.id)
    assigned_b, error_b, _candidate_b = assignment.assign_ticket_to_forced_user(session, ticket, "user_id", agent_b.id)

    assert (assigned_a.id, error_a) == (agent_a.id, None)
    assert (assigned_b.id, error_b) == (agent_b.id, None)
    rows = _work_rows(session, ticket)
    assert [(row.user_id, row.ended_at is None, row.end_reason) for row in rows] == [
        (agent_a.id, False, "reassignment"),
        (agent_b.id, True, None),
    ]


def test_real_admin_assignment_reassigns_and_unassigns_work_interval():
    session = _session()
    company = _company(session)
    admin = _user(session, company, role="admin")
    agent_a = _user(session, company)
    agent_b = _user(session, company)
    ticket = _ticket(session, company, assignee_id=agent_a.id, status=TicketStatus.NEW.value)
    open_work_interval_if_current(session, ticket, now=datetime(2026, 9, 12, 9, 0, 0))

    actor = SimpleNamespace(id=admin.id, role="admin")
    tickets_router._assign_ticket_by_admin(session, ticket, agent_b, actor)
    tickets_router._assign_ticket_by_admin(session, ticket, None, actor)

    rows = _work_rows(session, ticket)
    assert [(row.user_id, row.ended_at is None, row.end_reason) for row in rows] == [
        (agent_a.id, False, "reassignment"),
        (agent_b.id, False, "unassignment"),
    ]


def test_real_freeze_and_unfreeze_close_and_reopen_work_interval():
    session = _session()
    company = _company(session)
    agent = _user(session, company)
    ticket = _ticket(session, company, assignee_id=agent.id)
    open_work_interval_if_current(session, ticket, now=datetime(2026, 9, 12, 9, 0, 0))

    freeze_ticket(
        session,
        ticket,
        freeze_until=datetime.utcnow() + timedelta(hours=1),
        add_message_fn=_add_message,
    )
    rows_after_freeze = _work_rows(session, ticket)
    assert [(row.user_id, row.ended_at is None, row.end_reason) for row in rows_after_freeze] == [
        (agent.id, False, "freeze"),
    ]

    unfreeze_ticket(
        session,
        ticket,
        add_message_fn=_add_message,
        refresh_ticket_auto_close_timer_fn=lambda *_args, **_kwargs: None,
    )

    rows = _work_rows(session, ticket)
    assert [(row.user_id, row.ended_at is None, row.end_reason) for row in rows] == [
        (agent.id, False, "freeze"),
        (agent.id, True, None),
    ]


def test_internal_comment_keeps_frozen_ticket_and_does_not_open_work_interval():
    session = _session()
    company = _company(session)
    agent = _user(session, company)
    ticket = _ticket(session, company, assignee_id=agent.id, is_frozen=True)

    result = comment_on_ticket(
        db=session,
        user=SimpleNamespace(id=agent.id, role="agent"),
        ticket=ticket,
        payload=TextPayload(text="internal note"),
        unfreeze_ticket_on_message=unfreeze_ticket_on_message,
    )

    assert result == {"ok": True}
    rows = _work_rows(session, ticket)
    assert ticket.is_frozen
    assert rows == []


def test_real_manual_close_closes_work_interval():
    session = _session()
    company = _company(session)
    agent = _user(session, company)
    ticket = _ticket(session, company, assignee_id=agent.id, status=TicketStatus.PENDING.value)
    open_work_interval_if_current(session, ticket, now=datetime(2026, 9, 12, 9, 0, 0))
    agent_message = _agent_message(session, ticket, agent)

    result = close_ticket_by_actor(
        session,
        ticket,
        SimpleNamespace(id=agent.id, role="agent"),
        force_without_agent_reply_for_task=False,
        is_admin_fn=lambda actor: False,
        get_or_create_client_profile_fn=lambda *_args, **_kwargs: None,
        last_agent_message_fn=lambda *_args, **_kwargs: agent_message,
        first_client_message_fn=lambda *_args, **_kwargs: None,
        send_to_telegram_client_fn=lambda *_args, **_kwargs: None,
        actor_display_name_fn=lambda *_args, **_kwargs: "Agent",
        add_message_fn=_add_message,
        message_attachments_fn=lambda *_args, **_kwargs: [],
        send_attachment_to_telegram_client_fn=lambda *_args, **_kwargs: None,
        onsite_close_reminder_text="onsite",
        ticket_closed_client_text="closed",
    )

    assert result == "closed"
    rows = _work_rows(session, ticket)
    assert [(row.user_id, row.ended_at is None, row.end_reason) for row in rows] == [
        (agent.id, False, "close"),
    ]


def test_real_auto_close_closes_work_interval(monkeypatch):
    session = _session()
    company = _company(session)
    agent = _user(session, company)
    ticket = _ticket(session, company, assignee_id=agent.id, status=TicketStatus.PENDING.value)
    ticket.auto_close_due_at = datetime.utcnow() - timedelta(minutes=1)
    open_work_interval_if_current(session, ticket, now=datetime(2026, 9, 12, 9, 0, 0))
    monkeypatch.setattr(auto_close, "send_message", lambda *_args, **_kwargs: {"ok": True})

    closed = auto_close.cleanup_auto_close_tickets(session)

    assert closed == 1
    rows = _work_rows(session, ticket)
    assert [(row.user_id, row.ended_at is None, row.end_reason) for row in rows] == [
        (agent.id, False, "close"),
    ]


def test_real_close_active_ticket_for_contact_closes_work_interval():
    session = _session()
    company = _company(session)
    client = _client(session, company)
    contact = _contact(session, company, client)
    agent = _user(session, company)
    ticket = _ticket(session, company, client_id=client.id, assignee_id=agent.id)
    open_work_interval_if_current(session, ticket, now=datetime(2026, 9, 12, 9, 0, 0))

    closed_ticket = close_active_ticket_for_contact(
        session,
        contact,
        active_ticket_for_client_fn=lambda *_args, **_kwargs: ticket,
        collecting_ticket_for_client_fn=lambda *_args, **_kwargs: None,
        remove_menu_keyboard_fn=lambda *_args, **_kwargs: None,
        remove_client_close_button_fn=lambda *_args, **_kwargs: None,
        append_system_message_fn=_append_system_message,
    )

    assert closed_ticket.id == ticket.id
    rows = _work_rows(session, ticket)
    assert [(row.user_id, row.ended_at is None, row.end_reason) for row in rows] == [
        (agent.id, False, "close"),
    ]


def test_real_process_client_close_callback_closes_work_interval():
    session = _session()
    company = _company(session)
    client = _client(session, company)
    contact = _contact(session, company, client)
    agent = _user(session, company)
    ticket = _ticket(session, company, client_id=client.id, assignee_id=agent.id)
    open_work_interval_if_current(session, ticket, now=datetime(2026, 9, 12, 9, 0, 0))

    result = process_client_close_callback(
        session,
        {"update_id": 123},
        {"id": "callback-1", "message": {"chat": {"id": contact.external_chat_id}, "message_id": 55}, "from": {"id": contact.external_user_id}},
        False,
        first_company_fn=lambda _db: company,
        get_or_create_contact_from_user_fn=lambda *_args, **_kwargs: contact,
        collecting_ticket_for_client_fn=lambda *_args, **_kwargs: None,
        active_ticket_for_client_fn=lambda *_args, **_kwargs: ticket,
        client_can_close_ticket_fn=lambda *_args, **_kwargs: True,
        remove_menu_keyboard_fn=lambda *_args, **_kwargs: None,
        append_system_message_fn=_append_system_message,
        answer_callback_query_fn=lambda *_args, **_kwargs: None,
        safe_remove_inline_keyboard_fn=lambda *_args, **_kwargs: None,
        send_message_fn=lambda *_args, **_kwargs: {"ok": True},
        ticket_closed_client_text="closed",
    )

    assert result == {"ok": True, "closed": True, "ticket_id": ticket.id}
    rows = _work_rows(session, ticket)
    assert [(row.user_id, row.ended_at is None, row.end_reason) for row in rows] == [
        (agent.id, False, "close"),
    ]


def test_real_telegram_automation_direct_close_closes_work_interval(monkeypatch):
    session = _session()
    company = _company(session)
    agent = _user(session, company)
    ticket = _ticket(
        session,
        company,
        assignee_id=agent.id,
        workflow_type=TicketWorkflowType.TASK.value,
        status=TicketStatus.NEW.value,
    )
    open_work_interval_if_current(session, ticket, now=datetime(2026, 9, 12, 9, 0, 0))
    from api.app.services import telegram_bitrix_phone as phone
    ticket.intake_state = TicketIntakeState.COLLECTING.value
    ticket.quick_action_state_json = {phone.KEY: {"phase": "processing", "deadline": 9999999999,
        "rows": [{"id": "12345", "phone": "+79990000000"}], "finish": "success"}}
    session.commit()
    def complete(db, item, *args):
        item.status = TicketStatus.CLOSED.value
        item.closed_at = datetime.utcnow()
        sync_work_interval_after_close(db, item, now=item.closed_at)
    result = phone.finish(session, ticket, "chat-1", "success", True, {
        "send": lambda *args, **kwargs: {"ok": True, "result": {"message_id": 50}},
        "remove_close": lambda *args: None, "complete": complete, "append_system": _append_system_message,
    })
    assert result["automation_completed"] is True
    rows = _work_rows(session, ticket)
    assert [(row.user_id, row.ended_at is None, row.end_reason) for row in rows] == [
        (agent.id, False, "close"),
    ]


def test_real_restore_path_opens_work_interval_after_closed_interval():
    session = _session()
    company = _company(session)
    admin = _user(session, company, role="admin")
    agent = _user(session, company)
    ticket = _ticket(session, company, assignee_id=agent.id, status=TicketStatus.NEW.value)
    open_work_interval_if_current(session, ticket, now=datetime(2026, 9, 12, 9, 0, 0))
    ticket.status = TicketStatus.CLOSED.value
    ticket.closed_at = datetime(2026, 9, 12, 10, 0, 0)
    sync_work_interval_after_close(session, ticket, now=ticket.closed_at)

    result = tickets_router.restore(ticket.id, db=session, user=SimpleNamespace(id=admin.id, role="admin"))

    assert result["ok"] is True
    rows = _work_rows(session, ticket)
    assert [(row.user_id, row.ended_at is None, row.end_reason) for row in rows] == [
        (agent.id, False, "close"),
        (agent.id, True, None),
    ]


def test_real_telegram_direct_creation_with_assignee_opens_work_interval():
    session = _session()
    company = _company(session)
    client = _client(session, company)
    agent = _user(session, company)

    ticket = telegram_router._ensure_ticket_for_incoming(session, company.id, client.id, "Need help")

    assert ticket.assignee_id == agent.id
    rows = _work_rows(session, ticket)
    assert len(rows) == 1
    assert rows[0].user_id == agent.id
    assert rows[0].ended_at is None
    assert rows[0].end_reason is None
