from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import Company, TelegramNotificationAccount, User
from api.app.services import employee_telegram_notifications
from api.app.services.telegram_employee_binding import (
    REQUEST_STATUS_APPROVED,
    REQUEST_STATUS_PENDING,
)


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def _company(session) -> Company:
    company = Company(id=str(uuid.uuid4()), name="ACME", is_active=True, created_at=datetime.utcnow())
    session.add(company)
    session.commit()
    return company


def _user(session, *, company_id: str, full_name: str, login: str | None = None) -> User:
    now = datetime.utcnow()
    user = User(
        id=str(uuid.uuid4()),
        company_id=company_id,
        email=f"{login or full_name}@local.helpdesk",
        login=login or f"{full_name.lower().replace(' ', '.')}.{uuid.uuid4().hex[:6]}",
        password_hash="hash",
        full_name=full_name,
        role="agent",
        is_active=True,
        availability_status="online",
        legacy_ticket_count=0,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    session.commit()
    return user


def _account(
    session,
    *,
    user_id: str,
    telegram_user_id: str,
    request_status: str,
    delivery_available: bool,
    telegram_chat_id: str | None,
) -> TelegramNotificationAccount:
    now = datetime.utcnow()
    account = TelegramNotificationAccount(
        id=str(uuid.uuid4()),
        telegram_user_id=telegram_user_id,
        telegram_chat_id=telegram_chat_id,
        telegram_username="worker",
        telegram_display_name="Worker",
        bound_user_id=user_id,
        request_status=request_status,
        delivery_available=delivery_available,
        created_at=now,
        updated_at=now,
    )
    session.add(account)
    session.commit()
    return account


def test_notify_user_calls_send_message_for_approved_deliverable_account(monkeypatch):
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov")
    _account(
        session,
        user_id=user.id,
        telegram_user_id="tg-200",
        request_status=REQUEST_STATUS_APPROVED,
        delivery_available=True,
        telegram_chat_id="chat-200",
    )
    send_calls: list[tuple[str, str]] = []

    def fake_send_message(chat_id, text, **kwargs):
        send_calls.append((chat_id, text))
        return {"ok": True}

    monkeypatch.setattr(employee_telegram_notifications.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_notifications, "send_message", fake_send_message)

    result = employee_telegram_notifications.notify_user(session, user.id, "Test message")

    assert result is True
    assert send_calls == [("chat-200", "Test message")]


def test_policy_allows_delivery_for_approved_available_binding():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Policy Allowed")
    _account(
        session,
        user_id=user.id,
        telegram_user_id="tg-210",
        request_status=REQUEST_STATUS_APPROVED,
        delivery_available=True,
        telegram_chat_id="chat-210",
    )
    event = employee_telegram_notifications.build_assignment_notification_event(
        old_assignee_id=None,
        new_assignee_id=user.id,
    )

    deliveries = employee_telegram_notifications.resolve_assignment_telegram_deliveries(
        session,
        event=event,
        text="Policy message",
    )

    assert event is not None
    assert len(deliveries) == 1
    assert deliveries[0].event_kind == employee_telegram_notifications.ASSIGNMENT_EVENT_NEW
    assert deliveries[0].delivery_kind == employee_telegram_notifications.DELIVERY_KIND_NEW
    assert deliveries[0].user_id == user.id
    assert deliveries[0].telegram_chat_id == "chat-210"
    assert deliveries[0].text == "Policy message"


def test_policy_blocks_delivery_for_unavailable_binding():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Policy Blocked", login="policy-blocked")
    _account(
        session,
        user_id=user.id,
        telegram_user_id="tg-211",
        request_status=REQUEST_STATUS_PENDING,
        delivery_available=True,
        telegram_chat_id="chat-211",
    )
    event = employee_telegram_notifications.build_assignment_notification_event(
        old_assignee_id=None,
        new_assignee_id=user.id,
    )

    deliveries = employee_telegram_notifications.resolve_assignment_telegram_deliveries(
        session,
        event=event,
        text="Blocked message",
    )

    assert event is not None
    assert deliveries == ()


def test_notify_user_does_not_send_for_pending_or_rejected_status(monkeypatch):
    session = _session()
    company = _company(session)
    pending_user = _user(session, company_id=company.id, full_name="Pending User", login="pending")
    rejected_user = _user(session, company_id=company.id, full_name="Rejected User", login="rejected")
    _account(
        session,
        user_id=pending_user.id,
        telegram_user_id="tg-201",
        request_status=REQUEST_STATUS_PENDING,
        delivery_available=True,
        telegram_chat_id="chat-201",
    )
    _account(
        session,
        user_id=rejected_user.id,
        telegram_user_id="tg-202",
        request_status="rejected",
        delivery_available=True,
        telegram_chat_id="chat-202",
    )

    def fail_send_message(*args, **kwargs):
        raise AssertionError("send_message should not be called for non-approved accounts")

    monkeypatch.setattr(employee_telegram_notifications.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_notifications, "send_message", fail_send_message)

    assert employee_telegram_notifications.notify_user(session, pending_user.id, "Pending message") is False
    assert employee_telegram_notifications.notify_user(session, rejected_user.id, "Rejected message") is False


def test_notify_user_does_not_send_without_chat_id(monkeypatch):
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="No Chat User")
    _account(
        session,
        user_id=user.id,
        telegram_user_id="tg-203",
        request_status=REQUEST_STATUS_APPROVED,
        delivery_available=True,
        telegram_chat_id=None,
    )

    def fail_send_message(*args, **kwargs):
        raise AssertionError("send_message should not be called without telegram_chat_id")

    monkeypatch.setattr(employee_telegram_notifications.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_notifications, "send_message", fail_send_message)

    assert employee_telegram_notifications.notify_user(session, user.id, "No chat message") is False


def test_notify_user_does_not_send_without_employee_bot_token(monkeypatch):
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="No Token User")
    _account(
        session,
        user_id=user.id,
        telegram_user_id="tg-204",
        request_status=REQUEST_STATUS_APPROVED,
        delivery_available=True,
        telegram_chat_id="chat-204",
    )

    def fail_send_message(*args, **kwargs):
        raise AssertionError("send_message should not be called without employee bot token")

    monkeypatch.setattr(employee_telegram_notifications.settings, "employee_telegram_bot_token", None)
    monkeypatch.setattr(employee_telegram_notifications, "send_message", fail_send_message)

    assert employee_telegram_notifications.notify_user(session, user.id, "No token message") is False
