from __future__ import annotations

import asyncio
import io
import uuid
from datetime import datetime
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.db import Base
from api.app.models import Client, Company, MessageType, TelegramContact, Ticket, TicketAttachment, TicketMessage, TicketStatus, TicketWorkflowType
from api.app.services.ticket_lifecycle import close_ticket_by_actor
from api.app.services.ticket_messages import reply_to_ticket


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


def _delivery_error(message: str) -> HTTPException:
    return HTTPException(
        status_code=502,
        detail={"error": {"code": "telegram_send_failed", "message": message, "details": {}}},
    )


def _seed_ticket(session) -> Ticket:
    now = datetime.utcnow()
    company = Company(id=str(uuid.uuid4()), name="ACME")
    client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Telegram Client")
    contact = TelegramContact(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        external_user_id=str(uuid.uuid4()),
        external_chat_id="chat-42",
        external_username="tg_user",
        external_display_name="Telegram Client",
    )
    ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        subject="Need help",
        workflow_type=TicketWorkflowType.TICKET.value,
        status=TicketStatus.PENDING.value,
        created_at=now,
        updated_at=now,
    )
    session.add_all([company, client, contact, ticket])
    session.commit()
    return ticket


def _messages(session, ticket_id: str) -> list[TicketMessage]:
    return session.execute(
        select(TicketMessage).where(TicketMessage.ticket_id == ticket_id).order_by(TicketMessage.created_at, TicketMessage.id)
    ).scalars().all()


class _JsonRequest:
    headers = {"content-type": "application/json"}

    def __init__(self, payload: dict):
        self._payload = payload

    async def json(self):
        return self._payload


class _FormData(dict):
    def __init__(self, *, text: str | None, files: list[object]):
        super().__init__()
        self["text"] = text
        self["quote_message_id"] = None
        self._files = files

    def getlist(self, key: str):
        if key == "files":
            return list(self._files)
        return []


class _MultipartRequest:
    headers = {"content-type": "multipart/form-data; boundary=test"}

    def __init__(self, form_data: _FormData):
        self._form_data = form_data

    async def form(self):
        return self._form_data


def test_reply_text_send_failure_persists_internal_system_note():
    session = _session()
    ticket = _seed_ticket(session)

    async def run():
        try:
            await reply_to_ticket(
                request=_JsonRequest({"text": "Hello"}),
                db=session,
                user=SimpleNamespace(id="agent-1", role="agent"),
                ticket=ticket,
                send_to_telegram_client=lambda *_args, **_kwargs: (_ for _ in ()).throw(_delivery_error("Telegram не принял сообщение")),
                telegram_message_id_from_response=lambda response: None,
                save_upload_attachment=lambda *_args, **_kwargs: None,
                send_attachment_to_telegram_client=lambda *_args, **_kwargs: None,
                unfreeze_ticket_on_message=lambda *_args, **_kwargs: None,
                remove_client_close_button=lambda *_args, **_kwargs: None,
                refresh_ticket_auto_close=lambda *_args, **_kwargs: None,
            )
        except HTTPException as exc:
            assert exc.status_code == 502
            assert exc.detail["error"]["code"] == "telegram_send_failed"
            return
        raise AssertionError("reply_to_ticket should raise HTTPException")

    asyncio.run(run())

    messages = _messages(session, ticket.id)
    assert [message.body for message in messages] == ["Не удалось отправить сообщение клиенту в Telegram."]
    assert all(message.message_type == MessageType.SYSTEM.value for message in messages)


def test_reply_attachment_failure_after_text_send_persists_specific_note():
    session = _session()
    ticket = _seed_ticket(session)
    upload = SimpleNamespace(filename="report.pdf", file=io.BytesIO(b"pdf"), content_type="application/pdf")

    def save_upload_attachment(_db, current_ticket, message, _upload):
        return TicketAttachment(
            id=str(uuid.uuid4()),
            ticket_id=current_ticket.id,
            message_id=message.id,
            source="helpdesk",
            original_filename="report.pdf",
            mime_type="application/pdf",
            local_path="C:/tmp/report.pdf",
            created_at=datetime.utcnow(),
        )

    async def run():
        try:
            await reply_to_ticket(
                request=_MultipartRequest(_FormData(text="Hello", files=[upload])),
                db=session,
                user=SimpleNamespace(id="agent-1", role="agent"),
                ticket=ticket,
                send_to_telegram_client=lambda *_args, **_kwargs: {"result": {"message_id": 777}},
                telegram_message_id_from_response=lambda response: str(response["result"]["message_id"]),
                save_upload_attachment=save_upload_attachment,
                send_attachment_to_telegram_client=lambda *_args, **_kwargs: (_ for _ in ()).throw(_delivery_error("Telegram не принял файл")),
                unfreeze_ticket_on_message=lambda *_args, **_kwargs: None,
                remove_client_close_button=lambda *_args, **_kwargs: None,
                refresh_ticket_auto_close=lambda *_args, **_kwargs: None,
            )
        except HTTPException as exc:
            assert exc.status_code == 502
            assert exc.detail["error"]["code"] == "telegram_send_failed"
            return
        raise AssertionError("reply_to_ticket should raise HTTPException")

    asyncio.run(run())

    messages = _messages(session, ticket.id)
    assert [message.body for message in messages] == ["Текст отправлен клиенту, но не удалось отправить вложение «report.pdf»."]
    assert all(message.message_type == MessageType.SYSTEM.value for message in messages)


def test_close_keeps_ticket_closed_and_records_failure_note_when_client_notification_fails():
    session = _session()
    ticket = _seed_ticket(session)
    agent_message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.AGENT.value,
        body="Done",
        author_user_id="agent-1",
        agent_visible=True,
        created_at=datetime.utcnow(),
    )
    session.add(agent_message)
    session.commit()

    def add_message(db, current_ticket, message_type, body, author_user_id, **kwargs):
        db.add(
            TicketMessage(
                id=str(uuid.uuid4()),
                ticket_id=current_ticket.id,
                message_type=message_type.value if hasattr(message_type, "value") else message_type,
                body=body,
                author_user_id=author_user_id,
                agent_visible=kwargs.get("agent_visible", True),
                created_at=datetime.utcnow(),
            )
        )

    result = close_ticket_by_actor(
        session,
        ticket,
        SimpleNamespace(id="agent-1", role="agent"),
        force_without_agent_reply_for_task=False,
        is_admin_fn=lambda actor: False,
        get_or_create_client_profile_fn=lambda *_: None,
        last_agent_message_fn=lambda *_: agent_message,
        first_client_message_fn=lambda *_: None,
        send_to_telegram_client_fn=lambda *_args, **_kwargs: None,
        actor_display_name_fn=lambda *_: "Agent",
        add_message_fn=add_message,
        message_attachments_fn=lambda *_: [],
        send_attachment_to_telegram_client_fn=lambda *_args, **_kwargs: None,
        onsite_close_reminder_text="onsite",
        ticket_closed_client_text="closed",
    )
    session.commit()
    session.refresh(ticket)

    assert result == "closed"
    assert ticket.status == TicketStatus.CLOSED.value
    assert [
        message.body for message in _messages(session, ticket.id) if message.message_type == MessageType.SYSTEM.value
    ] == [
        "Заявка закрыта сотрудником",
        "Заявка закрыта, но уведомление о закрытии не удалось отправить клиенту в Telegram.",
    ]
