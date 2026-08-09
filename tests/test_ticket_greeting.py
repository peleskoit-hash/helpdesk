from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

import pytest
from fastapi import FastAPI
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.auth import get_current_user
from api.app.db import Base
from api.app.db import get_db
from api.app.models import (
    Client,
    ClientProfile,
    Company,
    MessageType,
    TelegramContact,
    Ticket,
    TicketIntakeState,
    TicketMessage,
    TicketStatus,
    TicketWorkflowType,
    User,
)
from api.app.routers import tickets


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


def _admin():
    return SimpleNamespace(id="admin-1", role="admin")


class _FixedDateTime(datetime):
    fixed_now = datetime(2026, 1, 1, 12, 0, 0)

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls.fixed_now
        return cls.fixed_now.replace(tzinfo=tz)

    @classmethod
    def utcnow(cls):
        return cls.fixed_now


def _seed_ticket(session, *, contact_name: str | None, telegram_username: str | None) -> Ticket:
    company = Company(id=str(uuid.uuid4()), name="ACME")
    user = User(id="admin-1", company_id=company.id, full_name="Admin", role="admin")
    client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Telegram Client")
    profile = ClientProfile(
        id=str(uuid.uuid4()),
        client_id=client.id,
        contact_name=contact_name,
        is_verified=True,
    )
    contact = TelegramContact(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        external_user_id=str(uuid.uuid4()),
        external_chat_id=str(uuid.uuid4()),
        external_username=telegram_username,
        external_display_name="Telegram Client",
    )
    ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        subject="Need help",
        workflow_type=TicketWorkflowType.TICKET.value,
        intake_state=TicketIntakeState.READY.value,
        status=TicketStatus.NEW.value,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add_all([company, user, client, profile, contact, ticket])
    session.commit()
    return ticket


def _send_greeting(monkeypatch: pytest.MonkeyPatch, session, ticket: Ticket, *, hour: int = 12) -> str:
    sent_texts: list[str] = []

    def fake_send_message(chat_id, text, **kwargs):
        sent_texts.append(text)
        return {"result": {"message_id": 100}}

    _FixedDateTime.fixed_now = datetime(2026, 1, 1, hour, 0, 0)
    monkeypatch.setattr(tickets, "datetime", _FixedDateTime)
    monkeypatch.setattr(tickets, "ZoneInfo", lambda key: timezone.utc)
    monkeypatch.setattr(tickets, "send_message", fake_send_message)

    result = tickets.greeting(ticket.id, db=session, user=_admin())

    assert result == {"ok": True, "text": sent_texts[0]}
    saved = session.execute(select(TicketMessage).where(TicketMessage.ticket_id == ticket.id)).scalar_one()
    assert saved.message_type == MessageType.AGENT.value
    assert saved.body == sent_texts[0]
    return sent_texts[0]


async def _asgi_post(app: FastAPI, path: str) -> tuple[int, str]:
    messages = []
    request_sent = False

    async def receive():
        nonlocal request_sent
        if request_sent:
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": b"", "more_body": False}

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
            "headers": [(b"host", b"testserver")],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        },
        receive,
        send,
    )
    status = next(message["status"] for message in messages if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return status, body.decode("utf-8")


def _endpoint_app(monkeypatch: pytest.MonkeyPatch, session, *, hour: int = 12) -> tuple[FastAPI, list[str]]:
    sent_texts: list[str] = []

    def fake_send_message(chat_id, text, **kwargs):
        sent_texts.append(text)
        return {"result": {"message_id": 100}}

    _FixedDateTime.fixed_now = datetime(2026, 1, 1, hour, 0, 0)
    monkeypatch.setattr(tickets, "datetime", _FixedDateTime)
    monkeypatch.setattr(tickets, "ZoneInfo", lambda key: timezone.utc)
    monkeypatch.setattr(tickets, "send_message", fake_send_message)

    app = FastAPI()
    app.include_router(tickets.router)
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = _admin
    return app, sent_texts


def test_greeting_uses_client_name_first(monkeypatch):
    session = _session()
    ticket = _seed_ticket(session, contact_name="Илья", telegram_username="belfiss")

    assert _send_greeting(monkeypatch, session, ticket) == "Добрый день, Илья!"


def test_greeting_falls_back_to_telegram_username(monkeypatch):
    session = _session()
    ticket = _seed_ticket(session, contact_name="   ", telegram_username="belfiss")

    assert _send_greeting(monkeypatch, session, ticket) == "Добрый день, @belfiss!"


def test_greeting_keeps_existing_telegram_username_at_sign(monkeypatch):
    session = _session()
    ticket = _seed_ticket(session, contact_name="", telegram_username="@belfiss")

    assert _send_greeting(monkeypatch, session, ticket) == "Добрый день, @belfiss!"


def test_greeting_without_name_and_username_sends_plain_greeting(monkeypatch):
    session = _session()
    ticket = _seed_ticket(session, contact_name=None, telegram_username=None)

    assert _send_greeting(monkeypatch, session, ticket) == "Добрый день"


def test_greeting_missing_name_no_longer_raises_client_name_required(monkeypatch):
    session = _session()
    ticket = _seed_ticket(session, contact_name=None, telegram_username=None)

    try:
        _send_greeting(monkeypatch, session, ticket)
    except HTTPException as exc:
        assert exc.detail["error"]["code"] != "client_name_required"
        raise


def test_greeting_time_of_day_boundaries_are_unchanged(monkeypatch):
    for hour, expected in [
        (11, "Доброе утро, Илья!"),
        (12, "Добрый день, Илья!"),
        (16, "Добрый день, Илья!"),
        (17, "Добрый вечер, Илья!"),
    ]:
        session = _session()
        ticket = _seed_ticket(session, contact_name="Илья", telegram_username=None)

        assert _send_greeting(monkeypatch, session, ticket, hour=hour) == expected


def test_greeting_endpoint_falls_back_to_telegram_username(monkeypatch):
    session = _session()
    ticket = _seed_ticket(session, contact_name="", telegram_username="belfiss")
    app, sent_texts = _endpoint_app(monkeypatch, session)

    status_code, response_text = asyncio.run(_asgi_post(app, f"/api/v1/tickets/{ticket.id}/greeting"))

    assert status_code == 200
    assert json.loads(response_text) == {"ok": True, "text": "Добрый день, @belfiss!"}
    assert sent_texts == ["Добрый день, @belfiss!"]
    assert "client_name_required" not in response_text
    assert "Поле 'Имя' в карточке клиента не заполнено" not in response_text


def test_greeting_endpoint_without_name_and_username_sends_plain_greeting(monkeypatch):
    session = _session()
    ticket = _seed_ticket(session, contact_name="", telegram_username="")
    app, sent_texts = _endpoint_app(monkeypatch, session)

    status_code, response_text = asyncio.run(_asgi_post(app, f"/api/v1/tickets/{ticket.id}/greeting"))

    assert status_code == 200
    assert json.loads(response_text) == {"ok": True, "text": "Добрый день"}
    assert sent_texts == ["Добрый день"]
    assert "client_name_required" not in response_text
    assert "Поле 'Имя' в карточке клиента не заполнено" not in response_text
