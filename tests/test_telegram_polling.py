from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
import uuid

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.db import Base
from api.app.models import (
    AutomaticMessageTemplate,
    Client,
    Company,
    TelegramContact,
    Ticket,
    TicketIntakeState,
    TicketStatus,
    TicketTaskKind,
    TicketWorkflowType,
    User,
)
from api.app.routers import telegram as telegram_router
from api.app.routers.settings_automatic_messages import list_automatic_messages
from api.app.services import telegram_polling
from api.app.services.automatic_messages import get_automatic_message_definition


async def _asgi_post(app: FastAPI, path: str) -> tuple[int, str]:
    messages = []
    request_sent = False

    async def receive():
        nonlocal request_sent
        if request_sent:
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": [
                (b"host", b"testserver"),
                (b"content-type", b"application/json"),
                (b"content-length", b"2"),
            ],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        },
        receive,
        send,
    )
    status = next(message["status"] for message in messages if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return status, body.decode("utf-8")


def test_telegram_webhook_route_is_removed():
    app = FastAPI()
    app.include_router(telegram_router.router)

    registered_paths = {route.path for route in app.router.routes}

    assert "/api/v1/messenger/webhook/telegram" not in registered_paths

    status_code, _ = asyncio.run(_asgi_post(app, "/api/v1/messenger/webhook/telegram"))

    assert status_code == 404


class _SessionContext:
    def __init__(self, session):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, exc_type, exc, tb):
        return False


def test_process_update_sync_forwards_update_to_process_telegram_update(monkeypatch):
    expected_session = object()
    update = {"update_id": 101, "message": {"text": "/start"}}
    captured_calls: list[tuple[object, dict, bool]] = []
    expected_result = {"status": "ok", "external_chat_id": "42"}

    def fake_session_local():
        return _SessionContext(expected_session)

    def fake_process_telegram_update(db, payload, notify_client=False):
        captured_calls.append((db, payload, notify_client))
        return expected_result

    monkeypatch.setattr(telegram_polling, "SessionLocal", fake_session_local)
    monkeypatch.setattr(telegram_router, "process_telegram_update", fake_process_telegram_update)

    result = telegram_polling._process_update_sync(update)

    assert captured_calls == [(expected_session, update, True)]
    assert result is expected_result


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


def _agent(session, *, company_id: str) -> User:
    now = datetime.utcnow()
    agent = User(
        id=str(uuid.uuid4()),
        company_id=company_id,
        email=f"agent-{uuid.uuid4().hex[:6]}@local.helpdesk",
        login=f"agent-{uuid.uuid4().hex[:6]}",
        password_hash="hash",
        full_name="Agent",
        role="agent",
        is_active=True,
        availability_status="online",
        created_at=now,
        updated_at=now,
    )
    session.add(agent)
    session.commit()
    return agent


def _ticket(
    session,
    *,
    company_id: str,
    client_id: str,
    workflow_type: str = TicketWorkflowType.TICKET.value,
    task_kind: str = TicketTaskKind.NORMAL.value,
    status: str = TicketStatus.UNASSIGNED.value,
    intake_state: str = TicketIntakeState.READY.value,
    assignee_id: str | None = None,
    closed_at=None,
    distribution_notice_sent_at=None,
) -> Ticket:
    now = datetime.utcnow()
    ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company_id,
        client_id=client_id,
        assignee_id=assignee_id,
        subject="Нужна помощь",
        workflow_type=workflow_type,
        task_kind=task_kind,
        intake_state=intake_state,
        status=status,
        is_frozen=False,
        is_special=False,
        closed_at=closed_at,
        distribution_notice_sent_at=distribution_notice_sent_at,
        created_at=now,
        updated_at=now,
    )
    session.add(ticket)
    session.commit()
    return ticket


def _telegram_contact(session, *, company_id: str, client_id: str, chat_id: str = "client-chat") -> TelegramContact:
    now = datetime.utcnow()
    contact = TelegramContact(
        id=str(uuid.uuid4()),
        company_id=company_id,
        client_id=client_id,
        external_user_id=f"tg-{uuid.uuid4().hex[:8]}",
        external_chat_id=chat_id,
        external_username="client_tg",
        external_display_name="Client Tg",
        created_at=now,
        updated_at=now,
    )
    session.add(contact)
    session.commit()
    return contact


def test_distribution_wait_notice_sends_for_regular_unassigned_ticket(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _company(session)
    client = _client(session, company_id=company.id)
    ticket = _ticket(session, company_id=company.id, client_id=client.id)
    _telegram_contact(session, company_id=company.id, client_id=client.id)
    send_calls: list[tuple[str, str, str | None]] = []

    def fake_send_message(chat_id, text, parse_mode=None, **_kwargs):
        send_calls.append((chat_id, text, parse_mode))
        return {"ok": True}

    monkeypatch.setattr(telegram_polling, "send_message", fake_send_message)

    sent = telegram_polling.send_distribution_wait_notice_if_still_unassigned(session, ticket.id)

    session.refresh(ticket)
    assert sent is True
    assert ticket.distribution_notice_sent_at is not None
    assert send_calls == [
        (
            "client-chat",
            telegram_polling.DISTRIBUTION_WAIT_NOTICE_TEXT,
            None,
        )
    ]


def test_distribution_wait_notice_skips_ticket_assigned_before_delay(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _company(session)
    client = _client(session, company_id=company.id)
    agent = _agent(session, company_id=company.id)
    ticket = _ticket(
        session,
        company_id=company.id,
        client_id=client.id,
        assignee_id=agent.id,
        status=TicketStatus.NEW.value,
    )
    _telegram_contact(session, company_id=company.id, client_id=client.id)

    monkeypatch.setattr(telegram_polling, "send_message", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("send should not happen")))

    sent = telegram_polling.send_distribution_wait_notice_if_still_unassigned(session, ticket.id)

    session.refresh(ticket)
    assert sent is False
    assert ticket.distribution_notice_sent_at is None


def test_distribution_wait_notice_skips_task(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _company(session)
    client = _client(session, company_id=company.id)
    ticket = _ticket(
        session,
        company_id=company.id,
        client_id=client.id,
        workflow_type=TicketWorkflowType.TASK.value,
    )
    _telegram_contact(session, company_id=company.id, client_id=client.id)

    monkeypatch.setattr(telegram_polling, "send_message", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("send should not happen")))

    sent = telegram_polling.send_distribution_wait_notice_if_still_unassigned(session, ticket.id)

    assert sent is False
    assert ticket.distribution_notice_sent_at is None


def test_distribution_wait_notice_sends_only_once(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _company(session)
    client = _client(session, company_id=company.id)
    ticket = _ticket(session, company_id=company.id, client_id=client.id)
    _telegram_contact(session, company_id=company.id, client_id=client.id)
    send_calls: list[str] = []

    monkeypatch.setattr(telegram_polling, "send_message", lambda chat_id, text, **_kwargs: send_calls.append(text) or {"ok": True})

    assert telegram_polling.send_distribution_wait_notice_if_still_unassigned(session, ticket.id) is True
    assert telegram_polling.send_distribution_wait_notice_if_still_unassigned(session, ticket.id) is False

    assert send_calls == [telegram_polling.DISTRIBUTION_WAIT_NOTICE_TEXT]


def test_distribution_wait_notice_keeps_sent_at_empty_when_telegram_send_fails(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _company(session)
    client = _client(session, company_id=company.id)
    ticket = _ticket(session, company_id=company.id, client_id=client.id)
    _telegram_contact(session, company_id=company.id, client_id=client.id)

    def fail_send_message(*_args, **_kwargs):
        raise telegram_polling.TelegramApiError("telegram unavailable")

    monkeypatch.setattr(telegram_polling, "send_message", fail_send_message)

    sent = telegram_polling.send_distribution_wait_notice_if_still_unassigned(session, ticket.id)

    session.refresh(ticket)
    assert sent is False
    assert ticket.distribution_notice_sent_at is None


def test_distribution_wait_automatic_message_is_registered_and_listed_for_admin():
    definition = get_automatic_message_definition(telegram_polling.DISTRIBUTION_WAIT_NOTICE_KEY)
    assert definition is not None
    assert definition.title == "Заявка на распределении"
    assert definition.category == "ticket_creation"
    assert definition.default_text == telegram_polling.DISTRIBUTION_WAIT_NOTICE_TEXT

    response = list_automatic_messages(db=_session_factory()(), user=SimpleNamespace(role="admin"))
    listed = {item["key"]: item for item in response["items"]}

    assert telegram_polling.DISTRIBUTION_WAIT_NOTICE_KEY in listed
    assert listed[telegram_polling.DISTRIBUTION_WAIT_NOTICE_KEY]["effective_text"] == telegram_polling.DISTRIBUTION_WAIT_NOTICE_TEXT


def test_distribution_wait_notice_uses_custom_automatic_message_text(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _company(session)
    client = _client(session, company_id=company.id)
    ticket = _ticket(session, company_id=company.id, client_id=client.id)
    _telegram_contact(session, company_id=company.id, client_id=client.id)
    custom_text = "Кастомный текст для {{ticket_subject}}"
    now = datetime.utcnow()
    session.add(
        AutomaticMessageTemplate(
            id=str(uuid.uuid4()),
            key=telegram_polling.DISTRIBUTION_WAIT_NOTICE_KEY,
            body=custom_text,
            is_overridden=True,
            html_enabled=True,
            created_at=now,
            updated_at=now,
        )
    )
    session.commit()
    send_calls: list[str] = []

    monkeypatch.setattr(telegram_polling, "send_message", lambda _chat_id, text, **_kwargs: send_calls.append(text) or {"ok": True})

    assert telegram_polling.send_distribution_wait_notice_if_still_unassigned(session, ticket.id) is True
    assert send_calls == ["Кастомный текст для Нужна помощь"]
