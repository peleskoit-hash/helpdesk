from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.db import Base
from api.app.models import Client, Company, TelegramNotificationAccount, Ticket, TicketIntakeState, TicketStatus, TicketWorkflowType, User
from api.app.routers.tickets import _assign_ticket_by_admin
from api.app.services import assignment, employee_telegram_notifications


def _session_factory():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _company(session) -> Company:
    company = Company(id=str(uuid.uuid4()), name="ACME", is_active=True, created_at=datetime.utcnow())
    session.add(company)
    session.commit()
    return company


def _client(session, *, company_id: str) -> Client:
    client = Client(id=str(uuid.uuid4()), company_id=company_id, display_name="Client")
    session.add(client)
    session.commit()
    return client


def _user(session, *, company_id: str, full_name: str, role: str = "agent", login: str | None = None) -> User:
    now = datetime.utcnow()
    user = User(
        id=str(uuid.uuid4()),
        company_id=company_id,
        email=f"{login or full_name}@local.helpdesk",
        login=login or f"{full_name.lower().replace(' ', '.')}.{uuid.uuid4().hex[:6]}",
        password_hash="hash",
        full_name=full_name,
        role=role,
        is_active=True,
        availability_status="online",
        legacy_ticket_count=0,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    session.commit()
    return user


def _ticket(session, *, company_id: str, client_id: str) -> Ticket:
    now = datetime.utcnow()
    ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company_id,
        client_id=client_id,
        subject="Не работает интернет",
        workflow_type=TicketWorkflowType.TICKET.value,
        intake_state=TicketIntakeState.READY.value,
        status=TicketStatus.UNASSIGNED.value,
        created_at=now,
        updated_at=now,
    )
    session.add(ticket)
    session.commit()
    return ticket


def _notification_account(session, *, user_id: str) -> TelegramNotificationAccount:
    now = datetime.utcnow()
    account = TelegramNotificationAccount(
        id=str(uuid.uuid4()),
        telegram_user_id=f"tg-{uuid.uuid4().hex[:8]}",
        telegram_chat_id="chat-assign",
        telegram_username="agent_tg",
        telegram_display_name="Agent Tg",
        bound_user_id=user_id,
        request_status="approved",
        delivery_available=True,
        created_at=now,
        updated_at=now,
    )
    session.add(account)
    session.commit()
    return account


def test_none_to_user_creates_new_assignment_event_for_new_assignee_only():
    event = employee_telegram_notifications.build_assignment_notification_event(
        old_assignee_id=None,
        new_assignee_id="user-b",
    )

    assert event is not None
    assert event.kind == employee_telegram_notifications.ASSIGNMENT_EVENT_NEW
    assert [delivery.kind for delivery in event.deliveries] == [employee_telegram_notifications.DELIVERY_KIND_NEW]
    assert [delivery.user_id for delivery in event.deliveries] == ["user-b"]


def test_user_a_to_user_b_creates_transfer_event_with_two_recipients():
    event = employee_telegram_notifications.build_assignment_notification_event(
        old_assignee_id="user-a",
        new_assignee_id="user-b",
    )

    assert event is not None
    assert event.kind == employee_telegram_notifications.ASSIGNMENT_EVENT_TRANSFER
    assert employee_telegram_notifications.DELIVERY_KIND_NEW not in {delivery.kind for delivery in event.deliveries}
    assert [(delivery.kind, delivery.user_id) for delivery in event.deliveries] == [
        (employee_telegram_notifications.DELIVERY_KIND_TRANSFER_TO, "user-b"),
        (employee_telegram_notifications.DELIVERY_KIND_TRANSFER_FROM, "user-a"),
    ]


def test_user_a_to_none_creates_removed_from_agent_event():
    event = employee_telegram_notifications.build_assignment_notification_event(
        old_assignee_id="user-a",
        new_assignee_id=None,
    )

    assert event is not None
    assert event.kind == employee_telegram_notifications.ASSIGNMENT_EVENT_UNASSIGNED
    assert [(delivery.kind, delivery.user_id) for delivery in event.deliveries] == [
        (employee_telegram_notifications.DELIVERY_KIND_TRANSFER_FROM, "user-a"),
    ]


def test_admin_assignment_sends_employee_notification_after_commit(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _company(session)
    client = _client(session, company_id=company.id)
    ticket = _ticket(session, company_id=company.id, client_id=client.id)
    assignee = _user(session, company_id=company.id, full_name="Ivan Petrov", role="agent", login="ivan")
    _notification_account(session, user_id=assignee.id)
    send_calls: list[tuple[str, str]] = []

    def fake_send_message(chat_id, text, **kwargs):
        send_calls.append((chat_id, text))
        return {"ok": True}

    monkeypatch.setattr(employee_telegram_notifications, "SessionLocal", Session)
    monkeypatch.setattr(employee_telegram_notifications.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_notifications, "send_message", fake_send_message)

    _assign_ticket_by_admin(
        session,
        ticket,
        assignee,
        SimpleNamespace(id=assignee.id, role="admin", real_user_id=assignee.id, real_role="admin", is_real_admin=True),
    )
    session.commit()

    assert len(send_calls) == 1
    assert send_calls[0][0] == "chat-assign"
    assert "Вам назначена новая заявка" in send_calls[0][1]
    assert "Не работает интернет" in send_calls[0][1]


def test_admin_assignment_without_approved_telegram_account_skips_send(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _company(session)
    client = _client(session, company_id=company.id)
    ticket = _ticket(session, company_id=company.id, client_id=client.id)
    assignee = _user(session, company_id=company.id, full_name="Ivan Petrov", role="agent", login="ivan")

    def fail_send_message(*args, **kwargs):
        raise AssertionError("send_message should not be called without approved TelegramNotificationAccount")

    monkeypatch.setattr(employee_telegram_notifications, "SessionLocal", Session)
    monkeypatch.setattr(employee_telegram_notifications.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_notifications, "send_message", fail_send_message)

    _assign_ticket_by_admin(
        session,
        ticket,
        assignee,
        SimpleNamespace(id=assignee.id, role="admin", real_user_id=assignee.id, real_role="admin", is_real_admin=True),
    )
    session.commit()

    session.refresh(ticket)
    assert ticket.assignee_id == assignee.id


def test_service_assignment_sends_single_employee_notification_after_commit(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _company(session)
    client = _client(session, company_id=company.id)
    ticket = _ticket(session, company_id=company.id, client_id=client.id)
    assignee = _user(session, company_id=company.id, full_name="Petr Sidorov", role="agent", login="petr")
    _notification_account(session, user_id=assignee.id)
    send_calls: list[tuple[str, str]] = []

    def fake_send_message(chat_id, text, **kwargs):
        send_calls.append((chat_id, text))
        return {"ok": True}

    monkeypatch.setattr(employee_telegram_notifications, "SessionLocal", Session)
    monkeypatch.setattr(employee_telegram_notifications.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_notifications, "send_message", fake_send_message)

    assigned = assignment.assign_ticket_to_agent(session, ticket, assignee, notify_client=False)
    session.commit()

    assert assigned is True
    assert len(send_calls) == 1
    assert send_calls[0][0] == "chat-assign"


def test_admin_unassign_without_telegram_binding_keeps_business_operation_working(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _company(session)
    client = _client(session, company_id=company.id)
    assignee_a = _user(session, company_id=company.id, full_name="Agent A", role="agent", login="agent-a")
    ticket = _ticket(session, company_id=company.id, client_id=client.id)
    ticket.assignee_id = assignee_a.id
    ticket.status = TicketStatus.NEW.value
    session.commit()

    def fail_send_message(*args, **kwargs):
        raise AssertionError("send_message should not be called without approved TelegramNotificationAccount")

    monkeypatch.setattr(employee_telegram_notifications, "SessionLocal", Session)
    monkeypatch.setattr(employee_telegram_notifications.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_notifications, "send_message", fail_send_message)

    _assign_ticket_by_admin(
        session,
        ticket,
        None,
        SimpleNamespace(id=assignee_a.id, role="admin", real_user_id=assignee_a.id, real_role="admin", is_real_admin=True),
    )
    session.commit()

    session.refresh(ticket)
    assert ticket.assignee_id is None
    assert ticket.status == TicketStatus.UNASSIGNED.value


def test_admin_unassign_schedules_removed_from_agent_event():
    Session = _session_factory()
    session = Session()
    company = _company(session)
    client = _client(session, company_id=company.id)
    assignee_a = _user(session, company_id=company.id, full_name="Agent A", role="agent", login="agent-a")
    ticket = _ticket(session, company_id=company.id, client_id=client.id)
    ticket.assignee_id = assignee_a.id
    ticket.status = TicketStatus.NEW.value
    session.commit()

    _assign_ticket_by_admin(
        session,
        ticket,
        None,
        SimpleNamespace(id=assignee_a.id, role="admin", real_user_id=assignee_a.id, real_role="admin", is_real_admin=True),
    )

    pending = session.info[employee_telegram_notifications._PENDING_ASSIGNMENT_NOTIFICATIONS_KEY]
    assert len(pending) == 1
    assert pending[0]["event_kind"] == employee_telegram_notifications.ASSIGNMENT_EVENT_UNASSIGNED
    assert pending[0]["old_assignee_id"] == assignee_a.id
    assert pending[0]["new_assignee_id"] is None
    assert pending[0]["deliveries"] == [
        {
            "kind": employee_telegram_notifications.DELIVERY_KIND_TRANSFER_FROM,
            "user_id": assignee_a.id,
        }
    ]


def test_admin_reassign_a_to_b_creates_transfer_event_after_commit(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _company(session)
    client = _client(session, company_id=company.id)
    assignee_a = _user(session, company_id=company.id, full_name="Agent A", role="agent", login="agent-a")
    assignee_b = _user(session, company_id=company.id, full_name="Agent B", role="agent", login="agent-b")
    _notification_account(session, user_id=assignee_a.id)
    _notification_account(session, user_id=assignee_b.id)
    ticket = _ticket(session, company_id=company.id, client_id=client.id)
    ticket.assignee_id = assignee_a.id
    ticket.status = TicketStatus.NEW.value
    session.commit()

    captured_events: list[employee_telegram_notifications.AssignmentNotificationEvent] = []
    send_calls: list[tuple[str, str]] = []

    real_builder = employee_telegram_notifications.build_assignment_notification_event

    def capture_event(**kwargs):
        event = real_builder(**kwargs)
        if event is not None:
            captured_events.append(event)
        return event

    def fake_send_message(chat_id, text, **kwargs):
        send_calls.append((chat_id, text))
        return {"ok": True}

    monkeypatch.setattr(employee_telegram_notifications, "build_assignment_notification_event", capture_event)
    monkeypatch.setattr(employee_telegram_notifications, "SessionLocal", Session)
    monkeypatch.setattr(employee_telegram_notifications.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_notifications, "send_message", fake_send_message)

    _assign_ticket_by_admin(
        session,
        ticket,
        assignee_b,
        SimpleNamespace(id=assignee_b.id, role="admin", real_user_id=assignee_b.id, real_role="admin", is_real_admin=True),
    )
    session.commit()

    session.refresh(ticket)
    assert ticket.assignee_id == assignee_b.id
    assert len(captured_events) == 1
    assert captured_events[0].kind == employee_telegram_notifications.ASSIGNMENT_EVENT_TRANSFER
    assert captured_events[0].kind != employee_telegram_notifications.ASSIGNMENT_EVENT_NEW
    assert [(delivery.kind, delivery.user_id) for delivery in captured_events[0].deliveries] == [
        (employee_telegram_notifications.DELIVERY_KIND_TRANSFER_TO, assignee_b.id),
        (employee_telegram_notifications.DELIVERY_KIND_TRANSFER_FROM, assignee_a.id),
    ]
    assert sorted(chat_id for chat_id, _text in send_calls) == ["chat-assign", "chat-assign"]
