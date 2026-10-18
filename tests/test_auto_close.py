from __future__ import annotations

from datetime import datetime, timedelta
import logging
import uuid

import pytest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.db import Base
from api.app.models import Client, Company, MessageType, TelegramContact, Ticket, TicketMessage, TicketStatus, TicketWorkflowType
from api.app.services import auto_close


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


def _seed_due_ticket(session, *, with_contact: bool = True) -> tuple[Ticket, TelegramContact | None]:
    now = datetime.utcnow()
    company = Company(id=str(uuid.uuid4()), name="ACME")
    client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Telegram Client")
    ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        subject="Need help",
        workflow_type=TicketWorkflowType.TICKET.value,
        status=TicketStatus.NEW.value,
        auto_close_due_at=now - timedelta(minutes=1),
        created_at=now,
        updated_at=now,
    )
    session.add_all([company, client, ticket])

    contact = None
    if with_contact:
        contact = TelegramContact(
            id=str(uuid.uuid4()),
            company_id=company.id,
            client_id=client.id,
            external_user_id=str(uuid.uuid4()),
            external_chat_id="chat-42",
            external_username="tg_user",
            external_display_name="Telegram Client",
        )
        session.add(contact)

    session.commit()
    return ticket, contact


def _ticket_messages(session, ticket_id: str) -> list[TicketMessage]:
    return session.execute(
        select(TicketMessage).where(TicketMessage.ticket_id == ticket_id).order_by(TicketMessage.created_at, TicketMessage.id)
    ).scalars().all()


@pytest.mark.parametrize("channel", ["telegram", "web"])
def test_cleanup_auto_close_tickets_notifies_client_with_rendered_telegram_message(monkeypatch, channel):
    session = _session()
    ticket, contact = _seed_due_ticket(session)
    ticket.channel = channel
    session.commit()
    rendered_calls: list[tuple[object, str, str, object]] = []
    send_calls: list[tuple[str, str, str | None, bool]] = []

    def fake_render(db, key, fallback, *, ticket=None, **kwargs):
        rendered_calls.append((db, key, fallback, ticket))
        return "<b>Closed</b>", "HTML"

    def fake_send_message(chat_id, text, *, parse_mode=None, remove_keyboard=False, **kwargs):
        send_calls.append((chat_id, text, parse_mode, remove_keyboard))
        return {"ok": True}

    monkeypatch.setattr(auto_close, "render_automatic_message_for_telegram", fake_render)
    monkeypatch.setattr(auto_close, "send_message", fake_send_message)

    closed = auto_close.cleanup_auto_close_tickets(session)

    assert closed == 1
    session.refresh(ticket)
    assert ticket.status == TicketStatus.CLOSED.value
    assert rendered_calls == ([(session, "telegram.auto_close.client", auto_close.AUTO_CLOSE_CLIENT_TEXT, ticket)] if channel == "telegram" else [])
    assert send_calls == ([(contact.external_chat_id, "<b>Closed</b>", "HTML", True)] if channel == "telegram" else [])
    assert auto_close.cleanup_auto_close_tickets(session) == 0

    messages = _ticket_messages(session, ticket.id)
    assert [message.body for message in messages] == ["Заявка автоматически закрыта из-за отсутствия активности"]
    assert all(message.message_type == MessageType.SYSTEM.value for message in messages)


def test_cleanup_auto_close_tickets_logs_and_records_failure_when_notification_errors(monkeypatch, caplog):
    session = _session()
    ticket, _ = _seed_due_ticket(session)

    def fake_render(db, key, fallback, *, ticket=None, **kwargs):
        return "Closed", None

    def fake_send_message(chat_id, text, *, parse_mode=None, remove_keyboard=False, **kwargs):
        raise RuntimeError("telegram send failed")

    monkeypatch.setattr(auto_close, "render_automatic_message_for_telegram", fake_render)
    monkeypatch.setattr(auto_close, "send_message", fake_send_message)

    with caplog.at_level(logging.ERROR, logger=auto_close.__name__):
        closed = auto_close.cleanup_auto_close_tickets(session)

    assert closed == 1
    session.refresh(ticket)
    assert ticket.status == TicketStatus.CLOSED.value

    messages = _ticket_messages(session, ticket.id)
    assert sorted(message.body for message in messages) == sorted([
        "Заявка автоматически закрыта из-за отсутствия активности",
        "Не удалось отправить клиенту сообщение об автозакрытии",
    ])
    assert any(
        "Failed to send auto-close notification for ticket" in record.getMessage() and ticket.id in record.getMessage()
        for record in caplog.records
    )
    assert any(record.exc_info for record in caplog.records)


def test_cleanup_auto_close_tickets_keeps_no_contact_behavior(monkeypatch):
    session = _session()
    ticket, _ = _seed_due_ticket(session, with_contact=False)

    def fail_render(*args, **kwargs):
        raise AssertionError("renderer should not be called without TelegramContact")

    def fail_send(*args, **kwargs):
        raise AssertionError("send_message should not be called without TelegramContact")

    monkeypatch.setattr(auto_close, "render_automatic_message_for_telegram", fail_render)
    monkeypatch.setattr(auto_close, "send_message", fail_send)

    closed = auto_close.cleanup_auto_close_tickets(session)

    assert closed == 1
    session.refresh(ticket)
    assert ticket.status == TicketStatus.CLOSED.value

    messages = _ticket_messages(session, ticket.id)
    assert [message.body for message in messages] == ["Заявка автоматически закрыта из-за отсутствия активности"]
