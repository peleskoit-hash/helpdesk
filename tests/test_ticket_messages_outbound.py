from __future__ import annotations

import asyncio
import io
import json
import logging
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi import BackgroundTasks
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.db import Base
from api.app.models import Client, Company, MessageType, TelegramContact, Ticket, TicketAttachment, TicketMessage, TicketStatus, TicketWorkflowType
from api.app.routers.tickets import create_attachment_preload, delete_attachment_preload, get_attachment_preload_status, get_ticket
from api.app.services import telegram_bot as telegram_bot_service
from api.app.services import telegram_outbound_delivery as outbound_service
from api.app.services import ticket_messages as ticket_messages_service
from api.app.services.ticket_attachments import _attachment_out
from api.app.services.ticket_messages import delete_agent_message, edit_agent_message, reply_to_ticket
from api.app.services import schema_evolution


def _session_bundle():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine, Session, Session()


def _threaded_session_bundle(tmp_path: Path):
    db_path = tmp_path / "threaded-preload.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine, Session, Session()


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


def _attachments(session, message_id: str) -> list[TicketAttachment]:
    return session.execute(
        select(TicketAttachment).where(TicketAttachment.message_id == message_id).order_by(TicketAttachment.created_at, TicketAttachment.id)
    ).scalars().all()


class _FormData(dict):
    def __init__(self, *, text: str | None, files: list[object], quote_message_id: str | None = None, single_attachment: bool = False):
        super().__init__()
        self["text"] = text
        self["quote_message_id"] = quote_message_id
        if single_attachment:
            self["single_attachment"] = "true"
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


def _multipart_request_with_preload(*, text: str | None, preload_id: str, single_attachment: bool = False, quote_message_id: str | None = None):
    form_data = _FormData(text=text, files=[], quote_message_id=quote_message_id, single_attachment=single_attachment)
    form_data["preloaded_attachment_id"] = preload_id
    return _MultipartRequest(form_data)


def _save_upload_attachment_factory(tmp_path: Path):
    def _save_upload_attachment(db, current_ticket, message, upload):
        target = tmp_path / f"{uuid.uuid4().hex}-{upload.filename}"
        target.write_bytes(upload.file.getvalue())
        attachment = TicketAttachment(
            id=str(uuid.uuid4()),
            ticket_id=current_ticket.id,
            message_id=message.id,
            source="helpdesk",
            original_filename=upload.filename,
            mime_type=upload.content_type,
            local_path=str(target),
            created_at=datetime.utcnow(),
        )
        db.add(attachment)
        db.flush()
        return attachment

    return _save_upload_attachment


def _upload(filename: str, content: bytes, content_type: str = "application/pdf"):
    return SimpleNamespace(filename=filename, file=io.BytesIO(content), content_type=content_type)


def test_schema_evolution_adds_delivery_status_idempotently(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE ticket_attachments (
                    id VARCHAR(36) PRIMARY KEY,
                    ticket_id VARCHAR(36) NOT NULL,
                    message_id VARCHAR(36) NOT NULL,
                    source VARCHAR(40) NOT NULL,
                    telegram_file_id VARCHAR(255),
                    telegram_file_unique_id VARCHAR(255),
                    telegram_file_path VARCHAR(500),
                    telegram_message_id VARCHAR(100),
                    original_filename VARCHAR(255),
                    mime_type VARCHAR(255),
                    file_size INTEGER,
                    local_path VARCHAR(1000) NOT NULL,
                    created_at TIMESTAMP
                )
                """
            )
        )
    monkeypatch.setattr(schema_evolution, "engine", engine)

    schema_evolution.ensure_ticket_attachment_table()
    schema_evolution.ensure_ticket_attachment_table()

    with engine.connect() as conn:
        columns = {
            row["name"]
            for row in conn.execute(text("PRAGMA table_info(ticket_attachments)")).mappings().all()
        }
    assert "telegram_delivery_status" in columns


def test_attachment_out_includes_delivery_status():
    attachment = TicketAttachment(
        id="att-1",
        ticket_id="ticket-1",
        message_id="msg-1",
        source="helpdesk",
        original_filename="report.pdf",
        mime_type="application/pdf",
        file_size=123,
        local_path="C:/tmp/report.pdf",
        telegram_delivery_status="uploading",
        created_at=datetime.utcnow(),
    )

    payload = _attachment_out("ticket-1", attachment)

    assert payload["telegram_delivery_status"] == "uploading"


def test_reply_plain_ticket_attachment_saves_locally_marks_uploading_and_schedules_background(tmp_path):
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    upload = SimpleNamespace(filename="report.pdf", file=io.BytesIO(b"pdf"), content_type="application/pdf")
    scheduled = []
    send_calls = {"text": 0, "file": 0}

    async def run():
        result = await reply_to_ticket(
            request=_MultipartRequest(_FormData(text="Hello", files=[upload], single_attachment=True)),
            db=session,
            user=SimpleNamespace(id="agent-1", role="agent"),
            ticket=ticket,
            send_to_telegram_client=lambda *_args, **_kwargs: send_calls.__setitem__("text", send_calls["text"] + 1),
            telegram_message_id_from_response=lambda response: str(response["result"]["message_id"]),
            save_upload_attachment=_save_upload_attachment_factory(tmp_path),
            send_attachment_to_telegram_client=lambda *_args, **_kwargs: send_calls.__setitem__("file", send_calls["file"] + 1),
            claim_attachment_preload=lambda *_args, **_kwargs: None,
            schedule_attachment_delivery=lambda **kwargs: scheduled.append(kwargs),
            unfreeze_ticket_on_message=lambda *_args, **_kwargs: None,
            remove_client_close_button=lambda *_args, **_kwargs: None,
            refresh_ticket_auto_close=lambda *_args, **_kwargs: None,
        )
        assert result == {"ok": True}

    asyncio.run(run())

    assert send_calls == {"text": 0, "file": 0}
    messages = _messages(session, ticket.id)
    assert len(messages) == 1
    assert messages[0].body == "Hello"
    assert messages[0].telegram_message_id is None
    attachments = _attachments(session, messages[0].id)
    assert len(attachments) == 1
    assert attachments[0].telegram_delivery_status == "uploading"
    assert scheduled == [
        {
            "attachment_id": attachments[0].id,
            "message_id": messages[0].id,
            "ticket_id": ticket.id,
            "reply_to_message_id": None,
            "prefer_caption": True,
        }
    ]


def test_background_delivery_short_text_uses_staging_and_file_id_caption(monkeypatch, tmp_path):
    _engine, SessionFactory, session = _session_bundle()
    ticket = _seed_ticket(session)
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.AGENT.value,
        body="Короткий ответ",
        author_user_id="agent-1",
        created_at=datetime.utcnow(),
    )
    path = tmp_path / "report.pdf"
    path.write_bytes(b"pdf")
    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=message.id,
        source="helpdesk",
        original_filename="report.pdf",
        mime_type="application/pdf",
        local_path=str(path),
        telegram_delivery_status="uploading",
        created_at=datetime.utcnow(),
    )
    session.add_all([message, attachment])
    session.commit()

    monkeypatch.setattr(outbound_service, "SessionLocal", SessionFactory)
    monkeypatch.setattr(outbound_service.settings, "telegram_staging_chat_id", "staging-chat")

    calls = {"send_message": 0, "send_document": [], "send_document_by_file_id": [], "delete_message": 0}

    monkeypatch.setattr(
        outbound_service,
        "send_document",
        lambda *args, **kwargs: calls["send_document"].append((args, kwargs)) or {
            "result": {
                "message_id": 301,
                "document": {"file_id": "stage-file-id", "file_unique_id": "stage-unique-id"},
            }
        },
    )
    monkeypatch.setattr(outbound_service, "delete_message", lambda *args, **kwargs: calls.__setitem__("delete_message", calls["delete_message"] + 1))
    monkeypatch.setattr(outbound_service, "send_message", lambda *args, **kwargs: calls.__setitem__("send_message", calls["send_message"] + 1))
    monkeypatch.setattr(
        outbound_service,
        "send_document_by_file_id",
        lambda *args, **kwargs: calls["send_document_by_file_id"].append((args, kwargs)) or {"result": {"message_id": 777}},
    )

    outbound_service.deliver_ticket_attachment(
        attachment_id=attachment.id,
        message_id=message.id,
        ticket_id=ticket.id,
        prefer_caption=True,
    )

    check = SessionFactory()
    try:
        saved_attachment = check.get(TicketAttachment, attachment.id)
        saved_message = check.get(TicketMessage, message.id)
        assert saved_attachment.telegram_file_id == "stage-file-id"
        assert saved_attachment.telegram_file_unique_id == "stage-unique-id"
        assert saved_attachment.telegram_delivery_status == "sent"
        assert saved_attachment.telegram_message_id == "777"
        assert saved_message.telegram_message_id == "777"
    finally:
        check.close()

    assert not path.exists()
    assert calls["send_message"] == 0
    assert calls["delete_message"] == 0
    assert len(calls["send_document"]) == 1
    assert len(calls["send_document_by_file_id"]) == 1
    assert calls["send_document_by_file_id"][0][0][1] == "stage-file-id"
    assert calls["send_document_by_file_id"][0][1]["caption"]


def test_background_delivery_staging_failure_keeps_local_file(monkeypatch, tmp_path):
    _engine, SessionFactory, session = _session_bundle()
    ticket = _seed_ticket(session)
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.AGENT.value,
        body="Короткий ответ",
        author_user_id="agent-1",
        created_at=datetime.utcnow(),
    )
    path = tmp_path / "report.pdf"
    path.write_bytes(b"pdf")
    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=message.id,
        source="helpdesk",
        original_filename="report.pdf",
        mime_type="application/pdf",
        local_path=str(path),
        telegram_delivery_status="uploading",
        created_at=datetime.utcnow(),
    )
    session.add_all([message, attachment])
    session.commit()

    monkeypatch.setattr(outbound_service, "SessionLocal", SessionFactory)
    monkeypatch.setattr(outbound_service.settings, "telegram_staging_chat_id", "staging-chat")
    monkeypatch.setattr(outbound_service, "send_document", lambda *_args, **_kwargs: (_ for _ in ()).throw(outbound_service.TelegramApiError("staging failed")))
    monkeypatch.setattr(outbound_service, "send_message", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("client text send should not run")))
    monkeypatch.setattr(outbound_service, "send_document_by_file_id", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("client file send should not run")))

    outbound_service.deliver_ticket_attachment(
        attachment_id=attachment.id,
        message_id=message.id,
        ticket_id=ticket.id,
        prefer_caption=True,
    )

    check = SessionFactory()
    try:
        saved_attachment = check.get(TicketAttachment, attachment.id)
        assert saved_attachment.telegram_file_id is None
        assert saved_attachment.telegram_delivery_status == "failed"
    finally:
        check.close()

    assert path.exists()


def test_background_delivery_delete_staging_failure_does_not_break_final_send(monkeypatch, tmp_path):
    _engine, SessionFactory, session = _session_bundle()
    ticket = _seed_ticket(session)
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.AGENT.value,
        body="",
        author_user_id="agent-1",
        created_at=datetime.utcnow(),
    )
    path = tmp_path / "report.pdf"
    path.write_bytes(b"pdf")
    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=message.id,
        source="helpdesk",
        original_filename="report.pdf",
        mime_type="application/pdf",
        local_path=str(path),
        telegram_delivery_status="uploading",
        created_at=datetime.utcnow(),
    )
    session.add_all([message, attachment])
    session.commit()

    monkeypatch.setattr(outbound_service, "SessionLocal", SessionFactory)
    monkeypatch.setattr(outbound_service.settings, "telegram_staging_chat_id", "staging-chat")
    monkeypatch.setattr(
        outbound_service,
        "send_document",
        lambda *args, **kwargs: {
            "result": {
                "message_id": 301,
                "document": {"file_id": "stage-file-id", "file_unique_id": "stage-unique-id"},
            }
        },
    )
    monkeypatch.setattr(outbound_service, "delete_message", lambda *_args, **_kwargs: (_ for _ in ()).throw(outbound_service.TelegramApiError("cleanup failed")))
    monkeypatch.setattr(outbound_service, "send_message", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("send_message should not be called")))
    monkeypatch.setattr(outbound_service, "send_document_by_file_id", lambda *_args, **_kwargs: {"result": {"message_id": 801}})

    outbound_service.deliver_ticket_attachment(
        attachment_id=attachment.id,
        message_id=message.id,
        ticket_id=ticket.id,
        prefer_caption=False,
    )

    check = SessionFactory()
    try:
        saved_attachment = check.get(TicketAttachment, attachment.id)
        assert saved_attachment.telegram_delivery_status == "sent"
        assert saved_attachment.telegram_message_id == "801"
    finally:
        check.close()

    assert not path.exists()


def test_background_delivery_commit_failure_keeps_local_file_and_skips_unlink(monkeypatch, tmp_path):
    _engine, SessionFactory, session = _session_bundle()
    ticket = _seed_ticket(session)
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.AGENT.value,
        body="Короткий ответ",
        author_user_id="agent-1",
        created_at=datetime.utcnow(),
    )
    path = tmp_path / "report.pdf"
    path.write_bytes(b"pdf")
    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=message.id,
        source="helpdesk",
        original_filename="report.pdf",
        mime_type="application/pdf",
        local_path=str(path),
        telegram_delivery_status="uploading",
        created_at=datetime.utcnow(),
    )
    session.add_all([message, attachment])
    session.commit()

    delivery_session = SessionFactory()
    original_commit = delivery_session.commit
    commit_calls = {"count": 0}

    def _failing_commit():
        commit_calls["count"] += 1
        if commit_calls["count"] == 1:
            raise RuntimeError("commit failed")
        return original_commit()

    delivery_session.commit = _failing_commit

    monkeypatch.setattr(outbound_service, "SessionLocal", lambda: delivery_session)
    monkeypatch.setattr(outbound_service.settings, "telegram_staging_chat_id", "staging-chat")
    monkeypatch.setattr(
        outbound_service,
        "send_document",
        lambda *_args, **_kwargs: {
            "result": {
                "message_id": 301,
                "document": {"file_id": "stage-file-id", "file_unique_id": "stage-unique-id"},
            }
        },
    )
    unlinked = []
    monkeypatch.setattr(outbound_service, "_unlink_attachment_local_file", lambda path_value: unlinked.append(path_value))

    try:
        outbound_service.deliver_ticket_attachment(
            attachment_id=attachment.id,
            message_id=message.id,
            ticket_id=ticket.id,
            prefer_caption=True,
        )
        raise AssertionError("deliver_ticket_attachment should raise when commit fails")
    except RuntimeError as exc:
        assert str(exc) == "commit failed"

    check = SessionFactory()
    try:
        saved_attachment = check.get(TicketAttachment, attachment.id)
        assert saved_attachment.telegram_file_id is None
        assert saved_attachment.telegram_delivery_status == "uploading"
    finally:
        check.close()

    assert path.exists()
    assert unlinked == []


def test_background_delivery_long_text_sends_message_then_file_by_id(monkeypatch, tmp_path):
    _engine, SessionFactory, session = _session_bundle()
    ticket = _seed_ticket(session)
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.AGENT.value,
        body="X" * 1100,
        author_user_id="agent-1",
        created_at=datetime.utcnow(),
    )
    path = tmp_path / "report.pdf"
    path.write_bytes(b"pdf")
    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=message.id,
        source="helpdesk",
        original_filename="report.pdf",
        mime_type="application/pdf",
        local_path=str(path),
        telegram_delivery_status="uploading",
        created_at=datetime.utcnow(),
    )
    session.add_all([message, attachment])
    session.commit()

    monkeypatch.setattr(outbound_service, "SessionLocal", SessionFactory)
    monkeypatch.setattr(outbound_service.settings, "telegram_staging_chat_id", "staging-chat")
    monkeypatch.setattr(
        outbound_service,
        "send_document",
        lambda *args, **kwargs: {
            "result": {
                "message_id": 301,
                "document": {"file_id": "stage-file-id", "file_unique_id": "stage-unique-id"},
            }
        },
    )

    calls = {"send_message": 0, "send_document_by_file_id": 0}
    monkeypatch.setattr(outbound_service, "delete_message", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        outbound_service,
        "send_message",
        lambda *args, **kwargs: calls.__setitem__("send_message", calls["send_message"] + 1) or {"result": {"message_id": 700}},
    )
    monkeypatch.setattr(
        outbound_service,
        "send_document_by_file_id",
        lambda *args, **kwargs: calls.__setitem__("send_document_by_file_id", calls["send_document_by_file_id"] + 1) or {"result": {"message_id": 701}},
    )

    outbound_service.deliver_ticket_attachment(
        attachment_id=attachment.id,
        message_id=message.id,
        ticket_id=ticket.id,
        prefer_caption=True,
        reply_to_message_id="555",
    )

    check = SessionFactory()
    try:
        saved_attachment = check.get(TicketAttachment, attachment.id)
        saved_message = check.get(TicketMessage, message.id)
        assert saved_message.telegram_message_id == "700"
        assert saved_attachment.telegram_message_id == "701"
        assert saved_attachment.telegram_delivery_status == "sent"
    finally:
        check.close()

    assert calls == {"send_message": 1, "send_document_by_file_id": 1}


def test_background_delivery_failure_marks_failed_and_records_note(monkeypatch, tmp_path):
    _engine, SessionFactory, session = _session_bundle()
    ticket = _seed_ticket(session)
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.AGENT.value,
        body="Короткий ответ",
        author_user_id="agent-1",
        created_at=datetime.utcnow(),
    )
    path = tmp_path / "report.pdf"
    path.write_bytes(b"pdf")
    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=message.id,
        source="helpdesk",
        original_filename="report.pdf",
        mime_type="application/pdf",
        local_path=str(path),
        telegram_delivery_status="uploading",
        created_at=datetime.utcnow(),
    )
    session.add_all([message, attachment])
    session.commit()

    monkeypatch.setattr(outbound_service, "SessionLocal", SessionFactory)
    monkeypatch.setattr(outbound_service.settings, "telegram_staging_chat_id", "staging-chat")
    monkeypatch.setattr(
        outbound_service,
        "send_document",
        lambda *args, **kwargs: {
            "result": {
                "message_id": 301,
                "document": {"file_id": "stage-file-id", "file_unique_id": "stage-unique-id"},
            }
        },
    )
    monkeypatch.setattr(outbound_service, "delete_message", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        outbound_service,
        "send_document_by_file_id",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(outbound_service.TelegramApiError("final send failed")),
    )

    outbound_service.deliver_ticket_attachment(
        attachment_id=attachment.id,
        message_id=message.id,
        ticket_id=ticket.id,
        prefer_caption=True,
    )

    check = SessionFactory()
    try:
        saved_attachment = check.get(TicketAttachment, attachment.id)
        saved_messages = _messages(check, ticket.id)
        assert saved_attachment.telegram_delivery_status == "failed"
        assert any(message.message_type == MessageType.SYSTEM.value and "report.pdf" in message.body for message in saved_messages)
        assert any(message.message_type == MessageType.AGENT.value for message in saved_messages)
    finally:
        check.close()


def test_deleted_message_while_background_pending_is_not_delivered(monkeypatch, tmp_path):
    _engine, SessionFactory, session = _session_bundle()
    ticket = _seed_ticket(session)
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.AGENT.value,
        body="Короткий ответ",
        author_user_id="agent-1",
        message_status="deleted",
        created_at=datetime.utcnow(),
    )
    path = tmp_path / "report.pdf"
    path.write_bytes(b"pdf")
    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=message.id,
        source="helpdesk",
        original_filename="report.pdf",
        mime_type="application/pdf",
        local_path=str(path),
        telegram_delivery_status="uploading",
        created_at=datetime.utcnow(),
    )
    session.add_all([message, attachment])
    session.commit()

    monkeypatch.setattr(outbound_service, "SessionLocal", SessionFactory)
    monkeypatch.setattr(outbound_service.settings, "telegram_staging_chat_id", "staging-chat")
    monkeypatch.setattr(
        outbound_service,
        "send_document",
        lambda *args, **kwargs: {
            "result": {
                "message_id": 301,
                "document": {"file_id": "stage-file-id", "file_unique_id": "stage-unique-id"},
            }
        },
    )
    monkeypatch.setattr(outbound_service, "delete_message", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(outbound_service, "send_message", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("send_message should not be called")))
    monkeypatch.setattr(outbound_service, "send_document_by_file_id", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("send_document_by_file_id should not be called")))

    outbound_service.deliver_ticket_attachment(
        attachment_id=attachment.id,
        message_id=message.id,
        ticket_id=ticket.id,
        prefer_caption=True,
    )

    check = SessionFactory()
    try:
        saved_attachment = check.get(TicketAttachment, attachment.id)
        assert saved_attachment.telegram_delivery_status == "failed"
        assert saved_attachment.telegram_message_id is None
    finally:
        check.close()


def test_edit_agent_message_uses_caption_method_for_single_attachment_message(monkeypatch):
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.AGENT.value,
        body="Old",
        author_user_id="agent-1",
        telegram_message_id="501",
        created_at=datetime.utcnow(),
    )
    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=message.id,
        source="helpdesk",
        telegram_message_id="501",
        original_filename="report.pdf",
        mime_type="application/pdf",
        local_path="C:/tmp/report.pdf",
        created_at=datetime.utcnow(),
    )
    session.add_all([message, attachment])
    session.commit()

    calls = []
    monkeypatch.setattr(ticket_messages_service, "edit_message_caption", lambda chat_id, message_id, caption: calls.append(("caption", chat_id, message_id, caption)))
    monkeypatch.setattr(ticket_messages_service, "edit_message_text", lambda chat_id, message_id, text: calls.append(("text", chat_id, message_id, text)))

    result = edit_agent_message(
        db=session,
        user=SimpleNamespace(id="agent-1", role="agent"),
        ticket=ticket,
        message_id=message.id,
        payload=SimpleNamespace(text="New caption"),
        telegram_chat_id_by_client_id=lambda *_args: "chat-42",
        actor_display_name=lambda *_args: "Agent",
    )

    assert result == {"ok": True}
    assert calls == [("caption", "chat-42", "501", "New caption")]


def test_delete_agent_message_deduplicates_identical_telegram_ids(monkeypatch):
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.AGENT.value,
        body="Hello",
        author_user_id="agent-1",
        telegram_message_id="501",
        created_at=datetime.utcnow(),
    )
    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=message.id,
        source="helpdesk",
        telegram_message_id="501",
        original_filename="report.pdf",
        mime_type="application/pdf",
        local_path="C:/tmp/report.pdf",
        created_at=datetime.utcnow(),
    )
    session.add_all([message, attachment])
    session.commit()

    deleted = []
    monkeypatch.setattr(ticket_messages_service, "delete_message", lambda chat_id, message_id: deleted.append((chat_id, message_id)))

    result = delete_agent_message(
        db=session,
        user=SimpleNamespace(id="agent-1", role="agent"),
        ticket=ticket,
        message_id=message.id,
        telegram_chat_id_by_client_id=lambda *_args: "chat-42",
        actor_display_name=lambda *_args: "Agent",
    )

    assert result == {"ok": True}
    assert deleted == [("chat-42", "501")]


def test_static_contract_keeps_single_attachment_marker_and_bumps_versions():
    repo_root = Path(__file__).resolve().parents[1]
    composer = (repo_root / "api" / "app" / "static" / "ticket_composer.js").read_text(encoding="utf-8")
    template = (repo_root / "api" / "app" / "templates" / "ticket.html").read_text(encoding="utf-8")
    index_template = (repo_root / "api" / "app" / "templates" / "index.html").read_text(encoding="utf-8")
    app_shell = (repo_root / "api" / "app" / "static" / "app_shell.js").read_text(encoding="utf-8")
    ticket_js = (repo_root / "api" / "app" / "static" / "ticket.js").read_text(encoding="utf-8")

    assert 'formData.append("single_attachment", "true")' in composer
    assert "/static/ticket_composer.js?v=20260824a" in template
    assert "/static/ticket_composer.js?v=20260824a" in app_shell
    assert "/static/ticket_attachments.js?v=20260810b" in template
    assert "/static/ticket_attachments.js?v=20260810b" in app_shell
    assert "/static/app_shell.js?v=20260821b" in template
    assert '/static/styles.css?v=20260824a' in template
    assert '/static/styles.css?v=20260824a' in index_template
    assert "/static/ticket.js?v=20260824a" in template
    assert "/static/ticket.js?v=20260824a" in app_shell
    assert "/static/app_shell.js?v=20260811b" not in template
    assert "/static/ticket_composer.js?v=20260811d" not in template
    assert "/static/ticket.js?v=20260810a" not in template
    assert "/static/ticket.js?v=20260810a" not in app_shell
    assert "preloaded_attachment_id" in composer
    assert "sendByEnterCheckbox" in composer
    assert "e.isComposing" in composer
    assert '"/api/v1/users/me/preferences"' in ticket_js
    assert "updateSendByEnterPreference" in ticket_js
    assert 'formData.append("archive_group_id", archiveGroupId)' in composer
    assert "currentArchiveGroupId" in composer
    assert "new Map()" in composer
    assert "filePreloads: new Map()" in ticket_js
    assert "canUseAttachmentPreload" in ticket_js
    assert "AbortController" in composer


def test_selected_files_css_contract_keeps_rows_non_shrinking_and_container_scrollable():
    repo_root = Path(__file__).resolve().parents[1]
    styles = (repo_root / "api" / "app" / "static" / "styles.css").read_text(encoding="utf-8")

    selected_files_block = styles.split(".selected-files {", 1)[1].split("}", 1)[0]
    selected_file_item_block = styles.split(".selected-file-item {", 1)[1].split("}", 1)[0]

    assert "display: flex;" in selected_files_block
    assert "flex-direction: column;" in selected_files_block
    assert "overflow-y: auto;" in selected_files_block
    assert "max-height: 146px;" in selected_files_block

    assert "flex: 0 0 auto;" in selected_file_item_block
    assert "flex-shrink: 0;" in selected_file_item_block
    assert "min-height: 54px;" in selected_file_item_block


def test_archive_coordination_contract_avoids_group_wide_for_update_and_limits_dev_reload_scope():
    repo_root = Path(__file__).resolve().parents[1]
    outbound_source = (repo_root / "api" / "app" / "services" / "telegram_outbound_delivery.py").read_text(encoding="utf-8")
    dev_compose = (repo_root / "docker-compose.yml").read_text(encoding="utf-8")
    ensure_helper = outbound_source.split("def _ensure_group_archive_info_message", 1)[1].split("def _discard_preload", 1)[0]
    finalize_helper = outbound_source.split("def _finalize_claimed_preload_archive", 1)[1].split("def deliver_ticket_attachment", 1)[0]

    assert "for_update=True" not in ensure_helper
    assert "for_update=True" not in finalize_helper
    assert "_load_locked_preload(db, preload_id=leader.id)" in ensure_helper
    assert "--reload --reload-dir /app/app --reload-exclude /app/storage/attachments" in dev_compose


def test_archive_group_id_batch_lifecycle_stays_stable_until_last_file_removed():
    repo_root = Path(__file__).resolve().parents[1]
    composer_path = repo_root / "api" / "app" / "static" / "ticket_composer.js"
    script = f"""
const fs = require("fs");
global.window = global;
global.crypto = {{ randomUUID: (() => {{
  let index = 0;
  return () => `group-${{++index}}`;
}})() }};
global.TicketApi = {{ formatFileSize: (value) => String(value) }};
global.FormData = class FakeFormData {{
  constructor() {{
    this._entries = [];
  }}
  append(key, value, filename) {{
    this._entries.push([key, filename ? filename : value]);
  }}
  entries() {{
    return this._entries[Symbol.iterator]();
  }}
}};
global.AbortController = class FakeAbortController {{
  constructor() {{
    this.signal = {{}};
  }}
  abort() {{}}
}};
global.document = {{
  createElement() {{
    return {{
      className: "",
      textContent: "",
      append() {{}},
      addEventListener() {{}},
      classList: {{ add() {{}}, remove() {{}}, toggle() {{}} }},
    }};
  }},
}};
global.setInterval = () => 1;
global.clearInterval = () => {{}};
eval(fs.readFileSync({json.dumps(str(composer_path))}, "utf8"));

(async () => {{
const recordedGroups = [];
const deletedPreloads = [];
let preloadSequence = 0;
const ctx = {{
  state: {{
    ticketId: "ticket-1",
    selectedFiles: [],
    filePreloads: new Map(),
    currentArchiveGroupId: null,
  }},
  el: {{
    replyFilesInput: {{ value: "" }},
    selectedFilesList: null,
  }},
  canUseAttachmentPreload: () => true,
  api: (url, options = {{}}) => {{
    if (options.method === "DELETE") {{
      deletedPreloads.push(url);
      return Promise.resolve({{ ok: true }});
    }}
    const entries = Array.from(options.body.entries());
    recordedGroups.push(entries.find(([key]) => key === "archive_group_id")[1]);
    preloadSequence += 1;
    return Promise.resolve({{ id: `preload-${{preloadSequence}}`, status: "ready" }});
  }},
}};

const filesA = [
  {{ name: "a.pdf", size: 1 }},
  {{ name: "b.pdf", size: 1 }},
  {{ name: "c.pdf", size: 1 }},
  {{ name: "d.pdf", size: 1 }},
];
global.TicketComposer.queueSelectedFiles(ctx, filesA);
await Promise.resolve();
await Promise.resolve();
const firstBatchGroups = [...recordedGroups];
const firstBatchCurrent = ctx.state.currentArchiveGroupId;
global.TicketComposer.clearSelectedFiles(ctx);
const afterFirstBatchClear = ctx.state.currentArchiveGroupId;

const single = {{ name: "one.pdf", size: 1 }};
global.TicketComposer.queueSelectedFiles(ctx, [single]);
await Promise.resolve();
await Promise.resolve();
const beforeAppendCurrent = ctx.state.currentArchiveGroupId;
const added = [
  {{ name: "two.pdf", size: 1 }},
  {{ name: "three.pdf", size: 1 }},
];
global.TicketComposer.queueSelectedFiles(ctx, added);
await Promise.resolve();
await Promise.resolve();
const appendedGroups = recordedGroups.slice(4);
const afterAppendCurrent = ctx.state.currentArchiveGroupId;

await global.TicketComposer.removeSelectedFile(ctx, single);
const afterRemoveOne = ctx.state.currentArchiveGroupId;
await global.TicketComposer.removeSelectedFile(ctx, added[0]);
await global.TicketComposer.removeSelectedFile(ctx, added[1]);
const afterRemoveLast = ctx.state.currentArchiveGroupId;

console.log(JSON.stringify({{
  firstBatchGroups,
  firstBatchCurrent,
  afterFirstBatchClear,
  beforeAppendCurrent,
  appendedGroups,
  afterAppendCurrent,
  afterRemoveOne,
  afterRemoveLast,
  deletedPreloads,
}}));
}})().catch((error) => {{
  console.error(error);
  process.exit(1);
}});
"""
    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        check=True,
        cwd=repo_root,
    )
    payload = json.loads(result.stdout)

    assert len(payload["firstBatchGroups"]) == 4
    assert len(set(payload["firstBatchGroups"])) == 1
    assert payload["firstBatchCurrent"] == payload["firstBatchGroups"][0]
    assert payload["afterFirstBatchClear"] is None
    assert len(payload["appendedGroups"]) == 3
    assert len(set(payload["appendedGroups"])) == 1
    assert payload["beforeAppendCurrent"] == payload["appendedGroups"][0]
    assert payload["afterAppendCurrent"] == payload["beforeAppendCurrent"]
    assert payload["afterRemoveOne"] == payload["beforeAppendCurrent"]
    assert payload["beforeAppendCurrent"] != payload["firstBatchCurrent"]
    assert payload["afterRemoveLast"] is None
    assert len(payload["deletedPreloads"]) == 3


def test_successful_send_clears_archive_group_id_and_next_selection_gets_new_one():
    repo_root = Path(__file__).resolve().parents[1]
    composer_path = repo_root / "api" / "app" / "static" / "ticket_composer.js"
    script = f"""
const fs = require("fs");
global.window = global;
global.crypto = {{ randomUUID: (() => {{
  let index = 0;
  return () => `group-${{++index}}`;
}})() }};
global.TicketApi = {{ formatFileSize: (value) => String(value) }};
global.FormData = class FakeFormData {{
  constructor() {{
    this._entries = [];
  }}
  append(key, value, filename) {{
    this._entries.push([key, filename ? filename : value]);
  }}
  entries() {{
    return this._entries[Symbol.iterator]();
  }}
}};
global.AbortController = class FakeAbortController {{
  constructor() {{
    this.signal = {{}};
  }}
  abort() {{}}
}};
global.document = {{
  createElement() {{
    return {{
      className: "",
      textContent: "",
      append() {{}},
      addEventListener() {{}},
      classList: {{ add() {{}}, remove() {{}}, toggle() {{}} }},
    }};
  }},
}};
global.setInterval = () => 1;
global.clearInterval = () => {{}};
eval(fs.readFileSync({json.dumps(str(composer_path))}, "utf8"));

(async () => {{
const preloadGroups = [];
const replyCalls = [];
let preloadSequence = 0;
const ctx = {{
  state: {{
    ticketId: "ticket-1",
    selectedFiles: [],
    filePreloads: new Map(),
    currentArchiveGroupId: null,
    composeMode: "reply",
    role: "agent",
    card: {{ workflow_type: "ticket", status: "open" }},
    quoteMessageId: null,
    isSendingReply: false,
    pendingCloseAfterSend: false,
  }},
  el: {{
    replyFilesInput: {{ value: "" }},
    selectedFilesList: null,
    messageInput: {{ value: "hello" }},
  }},
  canUseAttachmentPreload: () => true,
  api: (url, options = {{}}) => {{
    if (url.endsWith("/attachment-preloads")) {{
      const entries = Array.from(options.body.entries());
      preloadGroups.push(entries.find(([key]) => key === "archive_group_id")[1]);
      preloadSequence += 1;
      return Promise.resolve({{ id: `preload-${{preloadSequence}}`, status: "ready" }});
    }}
    if (url.endsWith("/reply")) {{
      replyCalls.push(Array.from(options.body.entries()));
      return Promise.resolve({{ ok: true }});
    }}
    throw new Error(`Unexpected URL: ${{url}}`);
  }},
  loadCard: async () => {{}},
  setStatus: () => {{}},
  showError: (message) => {{ throw new Error(message); }},
  isOnsitePhraseJs: () => false,
  renderAvailabilityStatus: () => {{}},
  action: async () => {{}},
}};

const firstFile = {{ name: "first.pdf", size: 1 }};
global.TicketComposer.queueSelectedFiles(ctx, [firstFile]);
await Promise.resolve();
await Promise.resolve();
const beforeSend = ctx.state.currentArchiveGroupId;
await global.TicketComposer.sendByMode(ctx);
const afterSend = ctx.state.currentArchiveGroupId;
const afterSendCount = ctx.state.selectedFiles.length;

const secondFile = {{ name: "second.pdf", size: 1 }};
global.TicketComposer.queueSelectedFiles(ctx, [secondFile]);
await Promise.resolve();
await Promise.resolve();
const nextGroup = ctx.state.currentArchiveGroupId;

console.log(JSON.stringify({{
  preloadGroups,
  beforeSend,
  afterSend,
  afterSendCount,
  nextGroup,
  replyCalls,
}}));
}})().catch((error) => {{
  console.error(error);
  process.exit(1);
}});
"""
    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        check=True,
        cwd=repo_root,
    )
    payload = json.loads(result.stdout)

    assert len(payload["preloadGroups"]) == 2
    assert payload["beforeSend"] == payload["preloadGroups"][0]
    assert payload["afterSend"] is None
    assert payload["afterSendCount"] == 0
    assert payload["nextGroup"] == payload["preloadGroups"][1]
    assert payload["nextGroup"] != payload["beforeSend"]
    assert len(payload["replyCalls"]) == 1
    assert ["preloaded_attachment_id", "preload-1"] in payload["replyCalls"][0]


def test_transport_selector_uses_preload_for_ready_and_uploading_and_raw_for_failures():
    repo_root = Path(__file__).resolve().parents[1]
    composer_path = repo_root / "api" / "app" / "static" / "ticket_composer.js"
    script = f"""
const fs = require("fs");
global.window = global;
global.TicketApi = {{ formatFileSize: (value) => String(value) }};
global.FormData = class FakeFormData {{
  constructor() {{
    this._entries = [];
  }}
  append(key, value, filename) {{
    if (filename) {{
      this._entries.push([key, {{ name: filename }}]);
      return;
    }}
    this._entries.push([key, value]);
  }}
  entries() {{
    return this._entries[Symbol.iterator]();
  }}
}};
eval(fs.readFileSync({json.dumps(str(composer_path))}, "utf8"));
const file = {{ name: "report.pdf", size: 3 }};
const makeCtx = (preload) => ({{
  state: {{
    filePreloads: new Map(preload ? [[file, preload]] : []),
  }},
  canUseAttachmentPreload: () => true,
}});
const readyCtx = makeCtx({{
  preloadId: "preload-ready",
  status: "ready",
  stage: "sending",
}});
const uploadingCtx = makeCtx({{
  preloadId: "preload-uploading",
  status: "uploading",
  stage: "sending",
}});
const failedCtx = makeCtx({{
  preloadId: null,
  status: "failed",
  stage: "failed",
}});
const staleFailedCtx = makeCtx({{
  preloadId: "preload-stale-failed",
  status: "uploading",
  stage: "failed",
}});
const ready = global.TicketComposer.resolveAttachmentTransport(readyCtx, file);
const uploading = global.TicketComposer.resolveAttachmentTransport(uploadingCtx, file);
const failed = global.TicketComposer.resolveAttachmentTransport(failedCtx, file);
const staleFailed = global.TicketComposer.resolveAttachmentTransport(staleFailedCtx, file);
const readyForm = global.TicketComposer.buildReplyFormData(readyCtx, file, 0, 1, "Hello", null, ready);
const uploadingForm = global.TicketComposer.buildReplyFormData(uploadingCtx, file, 0, 1, "Hello", null, uploading);
const failedForm = global.TicketComposer.buildReplyFormData(failedCtx, file, 0, 1, "Hello", null, failed);
const staleFailedForm = global.TicketComposer.buildReplyFormData(staleFailedCtx, file, 0, 1, "Hello", null, staleFailed);
const formEntries = (formData) => Array.from(formData.entries()).map(([key, value]) => [key, typeof value === "string" ? value : value.name || "<binary>"]);
console.log(JSON.stringify({{
  ready,
  uploading,
  failed,
  staleFailed,
  readyEntries: formEntries(readyForm),
  uploadingEntries: formEntries(uploadingForm),
  failedEntries: formEntries(failedForm),
  staleFailedEntries: formEntries(staleFailedForm),
}}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        check=True,
        cwd=repo_root,
    )
    payload = json.loads(result.stdout)

    assert payload["ready"] == {
        "mode": "preloaded",
        "preloadId": "preload-ready",
        "preloadStatus": "ready",
    }
    assert payload["uploading"] == {
        "mode": "preloaded",
        "preloadId": "preload-uploading",
        "preloadStatus": "uploading",
    }
    assert payload["failed"] == {
        "mode": "raw",
        "reason": "preload_failed",
    }
    assert payload["staleFailed"] == {
        "mode": "raw",
        "reason": "preload_failed",
    }
    assert ["preloaded_attachment_id", "preload-ready"] in payload["readyEntries"]
    assert not any(entry[0] == "files" for entry in payload["readyEntries"])
    assert ["preloaded_attachment_id", "preload-uploading"] in payload["uploadingEntries"]
    assert not any(entry[0] == "files" for entry in payload["uploadingEntries"])
    assert any(entry[0] == "files" and entry[1] == "report.pdf" for entry in payload["failedEntries"])
    assert not any(entry[0] == "preloaded_attachment_id" for entry in payload["failedEntries"])
    assert any(entry[0] == "files" and entry[1] == "report.pdf" for entry in payload["staleFailedEntries"])
    assert not any(entry[0] == "preloaded_attachment_id" for entry in payload["staleFailedEntries"])


def test_virtual_restored_failed_preload_never_falls_back_to_raw_and_requires_reselect():
    repo_root = Path(__file__).resolve().parents[1]
    composer_path = repo_root / "api" / "app" / "static" / "ticket_composer.js"
    script = f"""
const fs = require("fs");
global.window = global;
global.TicketApi = {{ formatFileSize: (value) => String(value) }};
global.FormData = class FakeFormData {{
  constructor() {{
    this._entries = [];
  }}
  append(key, value, filename) {{
    if (filename) {{
      this._entries.push([key, {{ name: filename }}]);
      return;
    }}
    this._entries.push([key, value]);
  }}
  entries() {{
    return this._entries[Symbol.iterator]();
  }}
}};
eval(fs.readFileSync({json.dumps(str(composer_path))}, "utf8"));
const file = {{
  name: "restored.zip",
  size: 8,
  __attachmentPreloadVirtual: true,
  __attachmentPreloadId: "preload-restored",
}};
const ctx = {{
  state: {{
    filePreloads: new Map([[file, {{
      preloadId: "preload-restored",
      status: "failed",
      stage: "failed",
    }}]]),
  }},
  canUseAttachmentPreload: () => true,
}};
const transport = global.TicketComposer.resolveAttachmentTransport(ctx, file);
let buildError = null;
try {{
  global.TicketComposer.buildReplyFormData(ctx, file, 0, 1, "Hello", null, transport);
}} catch (error) {{
  buildError = error.message;
}}
console.log(JSON.stringify({{ transport, buildError }}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        check=True,
        cwd=repo_root,
    )
    payload = json.loads(result.stdout)

    assert payload["transport"]["mode"] == "error"
    assert payload["transport"]["reason"] == "virtual_preload_failed"
    assert payload["transport"]["message"]
    assert payload["buildError"] == payload["transport"]["message"]


def test_selected_file_row_markup_uses_header_remove_and_progress_only_for_active_states():
    repo_root = Path(__file__).resolve().parents[1]
    composer_path = repo_root / "api" / "app" / "static" / "ticket_composer.js"
    script = f"""
const fs = require("fs");
global.window = global;
global.TicketApi = {{ formatFileSize: (value) => `${{(value / (1024 * 1024)).toFixed(1)}} МБ` }};

function makeClassList(node) {{
  const values = new Set();
  return {{
    add: (...names) => names.forEach((name) => values.add(name)),
    remove: (...names) => names.forEach((name) => values.delete(name)),
    toggle: (name, force) => {{
      if (force === undefined) {{
        if (values.has(name)) values.delete(name);
        else values.add(name);
      }} else if (force) {{
        values.add(name);
      }} else {{
        values.delete(name);
      }}
    }},
    contains: (name) => values.has(name),
    toArray: () => Array.from(values),
  }};
}}

function makeNode(tagName) {{
  const node = {{
    tagName,
    className: "",
    textContent: "",
    children: [],
    listeners: {{}},
    dataset: {{}},
    classList: makeClassList(),
    append: (...children) => {{
      node.children.push(...children);
    }},
    appendChild: (child) => {{
      node.children.push(child);
      return child;
    }},
    addEventListener: (name, handler) => {{
      node.listeners[name] = handler;
    }},
  }};
  return node;
}}

global.document = {{
  createElement(tagName) {{
    return makeNode(tagName);
  }},
}};

eval(fs.readFileSync({json.dumps(str(composer_path))}, "utf8"));

const file = {{ name: "WinDisk4Setup_x64.msi", size: 11639193 }};
const makeCtx = (preload) => ({{
  state: {{
    selectedFiles: [file],
    filePreloads: new Map([[file, preload]]),
  }},
  el: {{
    replyFilesInput: {{ value: "" }},
    selectedFilesList: null,
  }},
  canUseAttachmentPreload: () => true,
  api: () => Promise.resolve({{ ok: true }}),
}});

const activeRow = global.TicketComposer.createSelectedFileRow(makeCtx({{ stage: "uploading", status: "uploading", preloadId: "p-1" }}), file);
const readyRow = global.TicketComposer.createSelectedFileRow(makeCtx({{ stage: "ready", status: "ready", preloadId: "p-2" }}), file);
const failedRow = global.TicketComposer.createSelectedFileRow(makeCtx({{ stage: "failed", status: "failed", preloadId: null }}), file);

const summarize = (row) => {{
  const header = row.children[0];
  const footer = row.children[1];
  return {{
    rowClasses: row.classList.toArray(),
    headerClass: header.className,
    footerClass: footer.className,
    headerChildren: header.children.map((child) => child.className),
    footerChildren: footer.children.map((child) => child.className),
    removeText: header.children[1].textContent,
  }};
}};

console.log(JSON.stringify({{
  active: summarize(activeRow),
  ready: summarize(readyRow),
  failed: summarize(failedRow),
}}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        check=True,
        cwd=repo_root,
    )
    payload = json.loads(result.stdout)

    assert payload["active"]["headerClass"] == "selected-file-head"
    assert payload["active"]["footerClass"] == "selected-file-foot"
    assert payload["active"]["headerChildren"] == ["selected-file-name", "selected-file-remove"]
    assert "selected-file-item-active" in payload["active"]["rowClasses"]
    assert "selected-file-progress" in payload["active"]["footerChildren"]
    assert "selected-file-progress" not in payload["ready"]["footerChildren"]
    assert "selected-file-progress" not in payload["failed"]["footerChildren"]
    assert "selected-file-item-ready" in payload["ready"]["rowClasses"]


def test_render_selected_files_preserves_scroll_position_on_rerender():
    repo_root = Path(__file__).resolve().parents[1]
    composer_path = repo_root / "api" / "app" / "static" / "ticket_composer.js"
    script = f"""
const fs = require("fs");
global.window = global;
global.TicketApi = {{ formatFileSize: (value) => String(value) }};

function makeClassList(node) {{
  const values = new Set();
  return {{
    add: (...names) => names.forEach((name) => values.add(name)),
    remove: (...names) => names.forEach((name) => values.delete(name)),
    toggle: (name, force) => {{
      if (force === undefined) {{
        if (values.has(name)) values.delete(name);
        else values.add(name);
      }} else if (force) {{
        values.add(name);
      }} else {{
        values.delete(name);
      }}
    }},
  }};
}}

function makeNode(tagName) {{
  const node = {{
    tagName,
    className: "",
    textContent: "",
    children: [],
    listeners: {{}},
    classList: makeClassList(),
    append: (...children) => {{
      node.children.push(...children);
    }},
    appendChild: (child) => {{
      node.children.push(child);
      return child;
    }},
    addEventListener: (name, handler) => {{
      node.listeners[name] = handler;
    }},
  }};
  return node;
}}

const selectedFilesList = {{
  children: [],
  scrollTop: 73,
  _html: "",
  classList: makeClassList(),
  appendChild(child) {{
    this.children.push(child);
    return child;
  }},
}};
Object.defineProperty(selectedFilesList, "innerHTML", {{
  get() {{
    return this._html;
  }},
  set(value) {{
    this._html = value;
    this.children = [];
    this.scrollTop = 0;
  }},
}});

global.document = {{
  createElement(tagName) {{
    return makeNode(tagName);
  }},
}};

eval(fs.readFileSync({json.dumps(str(composer_path))}, "utf8"));

const files = [
  {{ name: "one.bin", size: 1 }},
  {{ name: "two.bin", size: 2 }},
  {{ name: "three.bin", size: 3 }},
];
const ctx = {{
  state: {{
    selectedFiles: files,
    filePreloads: new Map([
      [files[0], {{ stage: "uploading", status: "uploading", preloadId: "p-1" }}],
      [files[1], {{ stage: "ready", status: "ready", preloadId: "p-2" }}],
      [files[2], {{ stage: "failed", status: "failed", preloadId: null }}],
    ]),
  }},
  el: {{
    selectedFilesList,
    replyFilesInput: {{ value: "" }},
  }},
  canUseAttachmentPreload: () => true,
  api: () => Promise.resolve({{ ok: true }}),
}};

global.TicketComposer.renderSelectedFiles(ctx);
selectedFilesList.scrollTop = 91;
ctx.state.filePreloads.get(files[0]).stage = "ready";
ctx.state.filePreloads.get(files[0]).status = "ready";
global.TicketComposer.renderSelectedFiles(ctx);

console.log(JSON.stringify({{
  scrollTop: selectedFilesList.scrollTop,
  childCount: selectedFilesList.children.length,
}}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        check=True,
        cwd=repo_root,
    )
    payload = json.loads(result.stdout)

    assert payload == {"scrollTop": 91, "childCount": 3}


def test_destroy_composer_state_keeps_preloaded_files_for_restore():
    repo_root = Path(__file__).resolve().parents[1]
    composer_path = repo_root / "api" / "app" / "static" / "ticket_composer.js"
    script = f"""
const fs = require("fs");
global.window = global;
global.TicketApi = {{ formatFileSize: (value) => String(value) }};
global.document = {{
  createElement() {{
    return {{
      className: "",
      textContent: "",
      children: [],
      append(...children) {{ this.children.push(...children); }},
      appendChild(child) {{ this.children.push(child); return child; }},
      addEventListener() {{}},
      classList: {{ add() {{}}, remove() {{}}, toggle() {{}}, contains() {{ return false; }} }},
    }};
  }},
}};
let cleared = 0;
let aborted = 0;
global.window.clearInterval = () => {{ cleared += 1; }};
eval(fs.readFileSync({json.dumps(str(composer_path))}, "utf8"));

const file = {{ name: "restore.pdf", size: 5 }};
const controller = {{ abort() {{ aborted += 1; }} }};
const state = {{
  selectedFiles: [file],
  filePreloads: new Map([[file, {{
    stage: "ready",
    status: "ready",
    preloadId: "preload-1",
    controller,
    pollTimer: 42,
    error: null,
    removed: false,
  }}]]),
}};

global.TicketComposer.destroyComposerState({{ state }});
const saved = state.filePreloads.get(file);
    console.log(JSON.stringify({{
      selectedCount: state.selectedFiles.length,
      preloadCount: state.filePreloads.size,
      preloadId: saved?.preloadId || null,
      pollTimerIsNull: saved?.pollTimer === null,
      controllerIsNull: saved?.controller === null,
      cleared,
      aborted,
    }}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        check=True,
        cwd=repo_root,
    )
    payload = json.loads(result.stdout)

    assert payload == {
        "selectedCount": 1,
        "preloadCount": 1,
        "preloadId": "preload-1",
        "pollTimerIsNull": True,
        "controllerIsNull": True,
        "cleared": 1,
        "aborted": 1,
    }


def test_sync_restored_preloads_restores_ready_and_uploading_after_reopen():
    repo_root = Path(__file__).resolve().parents[1]
    composer_path = repo_root / "api" / "app" / "static" / "ticket_composer.js"
    script = f"""
const fs = require("fs");
global.window = global;
global.TicketApi = {{ formatFileSize: (value) => String(value) }};

function makeClassList() {{
  return {{ add() {{}}, remove() {{}}, toggle() {{}}, contains() {{ return false; }} }};
}}

function makeNode(tagName) {{
  return {{
    tagName,
    className: "",
    textContent: "",
    children: [],
    scrollTop: 0,
    append(...children) {{ this.children.push(...children); }},
    appendChild(child) {{ this.children.push(child); return child; }},
    addEventListener() {{}},
    classList: makeClassList(),
    _html: "",
    set innerHTML(value) {{ this._html = value; this.children = []; }},
    get innerHTML() {{ return this._html; }},
  }};
}}

global.document = {{
  createElement(tagName) {{
    return makeNode(tagName);
  }},
}};
let timerId = 10;
const activeTimers = [];
global.window.setInterval = () => {{
  timerId += 1;
  activeTimers.push(timerId);
  return timerId;
}};
global.window.clearInterval = () => {{}};
eval(fs.readFileSync({json.dumps(str(composer_path))}, "utf8"));

const selectedFilesList = makeNode("div");
const localPendingFile = {{ name: "local.bin", size: 1 }};
const ctx = {{
  state: {{
    ticketId: "ticket-1",
    selectedFiles: [localPendingFile],
    filePreloads: new Map([[localPendingFile, {{
      stage: "browser_uploading",
      status: null,
      preloadId: null,
      controller: null,
      pollTimer: null,
      error: null,
      removed: false,
    }}]]),
  }},
  el: {{
    selectedFilesList,
  }},
  canUseAttachmentPreload: () => true,
  api: () => Promise.resolve({{ id: "ignored", status: "ready" }}),
}};

global.TicketComposer.syncRestoredPreloads(ctx, [
  {{ id: "preload-uploading", original_filename: "uploading.pdf", file_size: 3, status: "uploading" }},
  {{ id: "preload-ready", original_filename: "ready.pdf", file_size: 4, status: "ready" }},
  {{ id: "preload-failed", original_filename: "failed.pdf", file_size: 5, status: "failed" }},
  {{ id: "preload-cancelled", original_filename: "cancelled.pdf", file_size: 6, status: "cancelled" }},
]);

const summary = ctx.state.selectedFiles.map((file) => {{
  const preload = ctx.state.filePreloads.get(file);
  return {{
    name: file.name,
    virtual: Boolean(file.__attachmentPreloadVirtual),
    preloadId: preload?.preloadId || null,
    stage: preload?.stage || null,
    status: preload?.status || null,
    pollTimer: preload?.pollTimer || null,
  }};
}});
console.log(JSON.stringify({{
  selectedCount: ctx.state.selectedFiles.length,
  renderedCount: selectedFilesList.children.length,
  summary,
}}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        check=True,
        cwd=repo_root,
    )
    payload = json.loads(result.stdout)

    assert payload["selectedCount"] == 3
    assert payload["renderedCount"] == 3
    assert payload["summary"] == [
        {
            "name": "local.bin",
            "virtual": False,
            "preloadId": None,
            "stage": "browser_uploading",
            "status": None,
            "pollTimer": None,
        },
        {
            "name": "uploading.pdf",
            "virtual": True,
            "preloadId": "preload-uploading",
            "stage": "uploading",
            "status": "uploading",
            "pollTimer": 11,
        },
        {
            "name": "ready.pdf",
            "virtual": True,
            "preloadId": "preload-ready",
            "stage": "ready",
            "status": "ready",
            "pollTimer": None,
        },
    ]


def test_staging_binary_upload_uses_extended_timeout(monkeypatch, tmp_path):
    path = tmp_path / "report.bin"
    path.write_bytes(b"x")
    calls = []

    def _send_document(*_args, **kwargs):
        calls.append(kwargs.get("timeout"))
        return {
            "result": {
                "message_id": 400,
                "document": {"file_id": "stage-1", "file_unique_id": "uniq-1"},
            }
        }

    monkeypatch.setattr(outbound_service, "send_document", _send_document)
    monkeypatch.setattr(outbound_service.settings, "telegram_staging_chat_id", "staging-chat")

    result = outbound_service._send_staging_document_from_path(
        local_path=str(path),
        original_filename="report.bin",
        mime_type="application/octet-stream",
    )

    assert result == ("stage-1", "uniq-1", "400")
    assert calls == [300]


def test_regular_telegram_calls_keep_default_timeouts(monkeypatch):
    calls = []

    def _telegram_request(method, payload=None, timeout=30, **kwargs):
        calls.append((method, timeout))
        if method == "sendDocument":
            return {"ok": True, "result": {"message_id": 1, "document": {"file_id": "file-1", "file_unique_id": "uniq-1"}}}
        return {"ok": True, "result": {"message_id": 1}}

    monkeypatch.setattr(telegram_bot_service, "telegram_request", _telegram_request)

    telegram_bot_service.send_message("chat-1", "hello")
    telegram_bot_service.edit_message_text("chat-1", 10, "edited")
    telegram_bot_service.send_document_by_file_id("chat-1", "file-1")

    assert calls == [
        ("sendMessage", 30),
        ("editMessageText", 30),
        ("sendDocument", 30),
    ]
    assert all(timeout != 300 for _method, timeout in calls)


def test_staging_binary_upload_limit_is_one_and_releases_after_success(monkeypatch, tmp_path):
    _engine, SessionFactory, session = _threaded_session_bundle(tmp_path)
    ticket = _seed_ticket(session)
    user = ticket_messages_service.User(
        id="agent-1",
        company_id=ticket.company_id,
        full_name="Иван Петров",
        role="agent",
    )
    session.add(user)
    session.commit()

    preloads = []
    for index in range(3):
        path = tmp_path / f"file-{index}.bin"
        path.write_bytes(b"x")
        preload = outbound_service.TicketAttachmentPreload(
            id=str(uuid.uuid4()),
            ticket_id=ticket.id,
            user_id=user.id,
            original_filename=f"file-{index}.bin",
            mime_type="application/octet-stream",
            file_size=1,
            local_path=str(path),
            status="uploading",
            created_at=datetime.utcnow(),
        )
        preloads.append(preload)
    session.add_all(preloads)
    session.commit()

    monkeypatch.setattr(outbound_service, "SessionLocal", SessionFactory)
    monkeypatch.setattr(outbound_service.settings, "telegram_staging_chat_id", "staging-chat")
    monkeypatch.setattr(outbound_service, "send_message", lambda *_args, **_kwargs: {"result": {"message_id": 300}})
    monkeypatch.setattr(outbound_service, "edit_message_text", lambda *_args, **_kwargs: {"ok": True})

    counters = {"active": 0, "max_active": 0}
    counter_lock = threading.Lock()

    def _send_document(*_args, **_kwargs):
        with counter_lock:
            counters["active"] += 1
            counters["max_active"] = max(counters["max_active"], counters["active"])
        try:
            time.sleep(0.15)
            return {
                "result": {
                    "message_id": 400,
                    "document": {"file_id": f"stage-{uuid.uuid4()}", "file_unique_id": f"uniq-{uuid.uuid4()}"},
                }
            }
        finally:
            with counter_lock:
                counters["active"] -= 1

    monkeypatch.setattr(outbound_service, "send_document", _send_document)

    threads = [threading.Thread(target=outbound_service.deliver_attachment_preload, kwargs={"preload_id": preload.id}) for preload in preloads]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    check = SessionFactory()
    try:
        saved_statuses = [check.get(outbound_service.TicketAttachmentPreload, preload.id).status for preload in preloads]
    finally:
        check.close()

    assert counters["max_active"] == 1
    assert counters["active"] == 0
    assert saved_statuses == ["ready", "ready", "ready"]


def test_staging_binary_upload_limit_releases_after_exception(monkeypatch, tmp_path):
    _engine, SessionFactory, session = _threaded_session_bundle(tmp_path)
    ticket = _seed_ticket(session)
    user = ticket_messages_service.User(
        id="agent-1",
        company_id=ticket.company_id,
        full_name="Иван Петров",
        role="agent",
    )
    session.add(user)
    session.commit()

    preloads = []
    for index in range(3):
        path = tmp_path / f"file-{index}.bin"
        path.write_bytes(b"x")
        preload = outbound_service.TicketAttachmentPreload(
            id=str(uuid.uuid4()),
            ticket_id=ticket.id,
            user_id=user.id,
            original_filename=f"file-{index}.bin",
            mime_type="application/octet-stream",
            file_size=1,
            local_path=str(path),
            status="uploading",
            created_at=datetime.utcnow(),
        )
        preloads.append(preload)
    session.add_all(preloads)
    session.commit()

    monkeypatch.setattr(outbound_service, "SessionLocal", SessionFactory)
    monkeypatch.setattr(outbound_service.settings, "telegram_staging_chat_id", "staging-chat")
    monkeypatch.setattr(outbound_service, "send_message", lambda *_args, **_kwargs: {"result": {"message_id": 300}})
    monkeypatch.setattr(outbound_service, "edit_message_text", lambda *_args, **_kwargs: {"ok": True})
    monkeypatch.setattr(outbound_service, "delete_message", lambda *_args, **_kwargs: {"ok": True})

    counters = {"active": 0, "max_active": 0, "calls": 0}
    counter_lock = threading.Lock()

    def _send_document(*_args, **_kwargs):
        with counter_lock:
            counters["active"] += 1
            counters["max_active"] = max(counters["max_active"], counters["active"])
            counters["calls"] += 1
            call_number = counters["calls"]
        try:
            time.sleep(0.1)
            if call_number == 1:
                raise outbound_service.TelegramApiError("timed out")
            return {
                "result": {
                    "message_id": 400 + call_number,
                    "document": {"file_id": f"stage-{call_number}", "file_unique_id": f"uniq-{call_number}"},
                }
            }
        finally:
            with counter_lock:
                counters["active"] -= 1

    monkeypatch.setattr(outbound_service, "send_document", _send_document)

    threads = [threading.Thread(target=outbound_service.deliver_attachment_preload, kwargs={"preload_id": preload.id}) for preload in preloads]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    check = SessionFactory()
    try:
        remaining = [check.get(outbound_service.TicketAttachmentPreload, preload.id) for preload in preloads]
        remaining_statuses = sorted(row.status for row in remaining if row is not None)
    finally:
        check.close()

    assert counters["max_active"] == 1
    assert counters["active"] == 0
    assert remaining_statuses == ["ready", "ready"]


def test_terminal_staging_failure_clears_pending_reservation(monkeypatch, tmp_path):
    _engine, SessionFactory, session = _session_bundle()
    ticket = _seed_ticket(session)
    user = ticket_messages_service.User(
        id="agent-1",
        company_id=ticket.company_id,
        full_name="Иван Петров",
        role="agent",
    )
    session.add(user)
    session.commit()

    path = tmp_path / "broken.bin"
    path.write_bytes(b"x")
    preload = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id=user.id,
        original_filename="broken.bin",
        mime_type="application/octet-stream",
        file_size=1,
        local_path=str(path),
        status="uploading",
        created_at=datetime.utcnow(),
    )
    session.add(preload)
    session.commit()

    monkeypatch.setattr(outbound_service, "SessionLocal", SessionFactory)
    monkeypatch.setattr(outbound_service.settings, "telegram_staging_chat_id", "staging-chat")
    monkeypatch.setattr(outbound_service, "_send_preload_archive_info", lambda **_kwargs: (_ for _ in ()).throw(outbound_service.TelegramApiError("staging info failed")))

    outbound_service.deliver_attachment_preload(preload_id=preload.id)

    check = SessionFactory()
    try:
        saved = check.get(outbound_service.TicketAttachmentPreload, preload.id)
    finally:
        check.close()

    assert saved is not None
    assert saved.status == "failed"
    assert saved.telegram_staging_info_message_id is None


def test_preload_post_saves_local_file_and_schedules_staging_without_touching_ticket_state(tmp_path):
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    original_updated_at = ticket.updated_at
    background_tasks = BackgroundTasks()

    result = asyncio.run(
        create_attachment_preload(
            ticket_id=ticket.id,
            background_tasks=background_tasks,
            file=_upload("report.pdf", b"pdf"),
            archive_group_id="group-1",
            db=session,
            user=SimpleNamespace(id="admin-1", role="admin"),
        )
    )

    preloads = session.execute(select(outbound_service.TicketAttachmentPreload)).scalars().all()
    messages = _messages(session, ticket.id)
    session.refresh(ticket)

    assert result["status"] == "uploading"
    assert len(preloads) == 1
    assert Path(preloads[0].local_path).exists()
    assert preloads[0].status == "uploading"
    assert preloads[0].archive_group_id == "group-1"
    assert messages == []
    assert ticket.status == TicketStatus.PENDING.value
    assert ticket.updated_at == original_updated_at
    assert len(background_tasks.tasks) == 1


def test_preload_get_returns_current_status(tmp_path):
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    preload = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="admin-1",
        original_filename="report.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=str(tmp_path / "report.pdf"),
        status="ready",
        created_at=datetime.utcnow(),
    )
    Path(preload.local_path).write_bytes(b"pdf")
    session.add(preload)
    session.commit()

    result = get_attachment_preload_status(
        ticket_id=ticket.id,
        preload_id=preload.id,
        db=session,
        user=SimpleNamespace(id="admin-1", role="admin"),
    )

    assert result == {"id": preload.id, "status": "ready"}


def test_ticket_card_returns_only_active_attachment_preloads(tmp_path):
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    now = datetime.utcnow()

    active_uploading = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="agent-1",
        original_filename="uploading.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=str(tmp_path / "uploading.pdf"),
        status="uploading",
        created_at=now,
    )
    active_ready = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="agent-1",
        original_filename="ready.pdf",
        mime_type="application/pdf",
        file_size=4,
        local_path=str(tmp_path / "ready.pdf"),
        status="ready",
        created_at=now,
    )
    failed = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="agent-1",
        original_filename="failed.pdf",
        mime_type="application/pdf",
        file_size=5,
        local_path=str(tmp_path / "failed.pdf"),
        status="failed",
        created_at=now,
    )
    cancelled = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="agent-1",
        original_filename="cancelled.pdf",
        mime_type="application/pdf",
        file_size=6,
        local_path=str(tmp_path / "cancelled.pdf"),
        status="cancelled",
        created_at=now,
    )
    stale_ready = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="agent-1",
        original_filename="stale.pdf",
        mime_type="application/pdf",
        file_size=7,
        local_path=str(tmp_path / "stale.pdf"),
        status="ready",
        created_at=now - outbound_service.timedelta(hours=25),
    )
    foreign_user = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="agent-2",
        original_filename="foreign.pdf",
        mime_type="application/pdf",
        file_size=8,
        local_path=str(tmp_path / "foreign.pdf"),
        status="ready",
        created_at=now,
    )
    claimed = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="agent-1",
        original_filename="claimed.pdf",
        mime_type="application/pdf",
        file_size=9,
        local_path=str(tmp_path / "claimed.pdf"),
        status="ready",
        final_attachment_id="att-1",
        created_at=now,
    )
    session.add_all([active_uploading, active_ready, failed, cancelled, stale_ready, foreign_user, claimed])
    session.commit()

    result = get_ticket(
        ticket_id=ticket.id,
        db=session,
        user=SimpleNamespace(id="agent-1", role="admin"),
    )

    expected_active = [
        {
            "id": active_uploading.id,
            "original_filename": "uploading.pdf",
            "file_size": 3,
            "status": "uploading",
            "created_at": active_uploading.created_at,
        },
        {
            "id": active_ready.id,
            "original_filename": "ready.pdf",
            "file_size": 4,
            "status": "ready",
            "created_at": active_ready.created_at,
        },
    ]
    expected_active.sort(key=lambda row: (row["created_at"], row["id"]))

    assert result.active_attachment_preloads == expected_active


def test_remove_ready_preload_deletes_local_file_and_row(tmp_path):
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    path = tmp_path / "report.pdf"
    path.write_bytes(b"pdf")
    preload = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="admin-1",
        original_filename="report.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=str(path),
        status="ready",
        created_at=datetime.utcnow(),
    )
    session.add(preload)
    session.commit()

    result = delete_attachment_preload(
        ticket_id=ticket.id,
        preload_id=preload.id,
        db=session,
        user=SimpleNamespace(id="admin-1", role="admin"),
    )

    assert result == {"ok": True}
    assert session.get(outbound_service.TicketAttachmentPreload, preload.id) is None
    assert not path.exists()


def test_remove_uploading_preload_marks_cancelled_and_worker_cleans_without_client_delivery(monkeypatch, tmp_path):
    _engine, SessionFactory, session = _session_bundle()
    ticket = _seed_ticket(session)
    path = tmp_path / "report.pdf"
    path.write_bytes(b"pdf")
    preload = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="admin-1",
        original_filename="report.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=str(path),
        status="uploading",
        created_at=datetime.utcnow(),
    )
    session.add(preload)
    session.commit()

    result = delete_attachment_preload(
        ticket_id=ticket.id,
        preload_id=preload.id,
        db=session,
        user=SimpleNamespace(id="admin-1", role="admin"),
    )
    assert result == {"ok": True}
    assert session.get(outbound_service.TicketAttachmentPreload, preload.id).status == "cancelled"

    monkeypatch.setattr(outbound_service, "SessionLocal", SessionFactory)
    monkeypatch.setattr(outbound_service, "send_document", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("staging should not run")))
    monkeypatch.setattr(outbound_service, "send_document_by_file_id", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("client delivery should not run")))

    outbound_service.deliver_attachment_preload(preload_id=preload.id)

    check = SessionFactory()
    try:
        assert check.get(outbound_service.TicketAttachmentPreload, preload.id) is None
    finally:
        check.close()
    assert not path.exists()


def test_ready_preload_send_uses_existing_file_id_without_binary_reupload(tmp_path):
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    path = tmp_path / "report.pdf"
    path.write_bytes(b"pdf")
    preload = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="agent-1",
        original_filename="report.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=str(path),
        telegram_file_id="ready-file-id",
        telegram_file_unique_id="ready-unique-id",
        status="ready",
        created_at=datetime.utcnow(),
    )
    session.add(preload)
    session.commit()

    scheduled = []
    async def run():
        result = await reply_to_ticket(
            request=_multipart_request_with_preload(text="Hello", preload_id=preload.id, single_attachment=True),
            db=session,
            user=SimpleNamespace(id="agent-1", role="agent"),
            ticket=ticket,
            send_to_telegram_client=lambda *_args, **_kwargs: None,
            telegram_message_id_from_response=lambda response: None,
            save_upload_attachment=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("raw upload should not be used")),
            send_attachment_to_telegram_client=lambda *_args, **_kwargs: None,
            claim_attachment_preload=lambda db, preload_id, expected_ticket_id, expected_user_id: outbound_service.claim_attachment_preload(
                db,
                preload_id=preload_id,
                ticket_id=expected_ticket_id,
                user_id=expected_user_id,
            ),
            schedule_attachment_delivery=lambda **kwargs: scheduled.append(kwargs),
            unfreeze_ticket_on_message=lambda *_args, **_kwargs: None,
            remove_client_close_button=lambda *_args, **_kwargs: None,
            refresh_ticket_auto_close=lambda *_args, **_kwargs: None,
        )
        assert result == {"ok": True}

    asyncio.run(run())

    messages = _messages(session, ticket.id)
    attachments = _attachments(session, messages[0].id)
    assert attachments[0].telegram_file_id == "ready-file-id"
    assert attachments[0].telegram_delivery_status == "sending"
    claimed_preload = session.get(outbound_service.TicketAttachmentPreload, preload.id)
    assert claimed_preload is not None
    assert claimed_preload.final_attachment_id == attachments[0].id
    assert len(scheduled) == 1


def test_uploading_preload_send_claims_attachment_and_waits_for_worker(tmp_path):
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    path = tmp_path / "report.pdf"
    path.write_bytes(b"pdf")
    preload = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="agent-1",
        original_filename="report.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=str(path),
        status="uploading",
        created_at=datetime.utcnow(),
    )
    session.add(preload)
    session.commit()

    scheduled = []
    async def run():
        result = await reply_to_ticket(
            request=_multipart_request_with_preload(text="Hello", preload_id=preload.id, single_attachment=True),
            db=session,
            user=SimpleNamespace(id="agent-1", role="agent"),
            ticket=ticket,
            send_to_telegram_client=lambda *_args, **_kwargs: None,
            telegram_message_id_from_response=lambda response: None,
            save_upload_attachment=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("raw upload should not be used")),
            send_attachment_to_telegram_client=lambda *_args, **_kwargs: None,
            claim_attachment_preload=lambda db, preload_id, expected_ticket_id, expected_user_id: outbound_service.claim_attachment_preload(
                db,
                preload_id=preload_id,
                ticket_id=expected_ticket_id,
                user_id=expected_user_id,
            ),
            schedule_attachment_delivery=lambda **kwargs: scheduled.append(kwargs),
            unfreeze_ticket_on_message=lambda *_args, **_kwargs: None,
            remove_client_close_button=lambda *_args, **_kwargs: None,
            refresh_ticket_auto_close=lambda *_args, **_kwargs: None,
        )
        assert result == {"ok": True}

    asyncio.run(run())

    messages = _messages(session, ticket.id)
    attachments = _attachments(session, messages[0].id)
    claimed_preload = session.get(outbound_service.TicketAttachmentPreload, preload.id)
    assert attachments[0].telegram_delivery_status == "uploading"
    assert claimed_preload.final_attachment_id == attachments[0].id
    assert scheduled == []


def test_preload_worker_finishes_claimed_uploading_preload_and_delivers_once(monkeypatch, tmp_path):
    _engine, SessionFactory, session = _session_bundle()
    ticket = _seed_ticket(session)
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.AGENT.value,
        body="Hello",
        author_user_id="agent-1",
        created_at=datetime.utcnow(),
    )
    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=message.id,
        source="helpdesk",
        original_filename="report.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=str(tmp_path / "report.pdf"),
        telegram_delivery_status="uploading",
        created_at=datetime.utcnow(),
    )
    Path(attachment.local_path).write_bytes(b"pdf")
    preload = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="agent-1",
        original_filename="report.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=attachment.local_path,
        status="uploading",
        final_attachment_id=attachment.id,
        prefer_caption=True,
        created_at=datetime.utcnow(),
    )
    session.add_all([message, attachment, preload])
    session.commit()

    monkeypatch.setattr(outbound_service, "SessionLocal", SessionFactory)
    monkeypatch.setattr(outbound_service.settings, "telegram_staging_chat_id", "staging-chat")
    monkeypatch.setattr(outbound_service, "send_document", lambda *_args, **_kwargs: {"result": {"message_id": 301, "document": {"file_id": "stage-file-id", "file_unique_id": "stage-unique-id"}}})
    monkeypatch.setattr(outbound_service, "delete_message", lambda *_args, **_kwargs: None)
    calls = {"send_document_by_file_id": 0}
    monkeypatch.setattr(outbound_service, "send_document_by_file_id", lambda *_args, **_kwargs: calls.__setitem__("send_document_by_file_id", calls["send_document_by_file_id"] + 1) or {"result": {"message_id": 888}})
    monkeypatch.setattr(
        outbound_service,
        "send_message",
        lambda chat_id, *_args, **_kwargs: {"result": {"message_id": 300}} if chat_id == "staging-chat" else (_ for _ in ()).throw(AssertionError("caption path should be used")),
    )

    outbound_service.deliver_attachment_preload(preload_id=preload.id)
    assert not Path(attachment.local_path).exists()
    outbound_service.deliver_ticket_attachment(
        attachment_id=attachment.id,
        message_id=message.id,
        ticket_id=ticket.id,
        prefer_caption=True,
    )

    check = SessionFactory()
    try:
        saved_attachment = check.get(TicketAttachment, attachment.id)
        saved_message = check.get(TicketMessage, message.id)
        assert check.get(outbound_service.TicketAttachmentPreload, preload.id) is None
        assert saved_attachment.telegram_file_id == "stage-file-id"
        assert saved_attachment.telegram_delivery_status == "sent"
        assert saved_attachment.telegram_message_id == "888"
        assert saved_message.telegram_message_id == "888"
    finally:
        check.close()
    assert calls["send_document_by_file_id"] == 1


def test_preload_worker_rechecks_fresh_state_after_staging_and_delivers_claimed_preload_once(monkeypatch, tmp_path):
    _engine, SessionFactory, session = _session_bundle()
    ticket = _seed_ticket(session)
    path = tmp_path / "report.pdf"
    path.write_bytes(b"pdf")
    preload = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="agent-1",
        original_filename="report.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=str(path),
        status="uploading",
        created_at=datetime.utcnow(),
    )
    session.add(preload)
    session.commit()

    monkeypatch.setattr(outbound_service, "SessionLocal", SessionFactory)
    monkeypatch.setattr(outbound_service.settings, "telegram_staging_chat_id", "staging-chat")

    created_ids: dict[str, str] = {}

    def _claim_during_staging(*_args, **_kwargs):
        claim_session = SessionFactory()
        try:
            claimed_preload = claim_session.get(outbound_service.TicketAttachmentPreload, preload.id)
            message = TicketMessage(
                id=str(uuid.uuid4()),
                ticket_id=ticket.id,
                message_type=MessageType.AGENT.value,
                body="Hello",
                author_user_id="agent-1",
                created_at=datetime.utcnow(),
            )
            attachment = TicketAttachment(
                id=str(uuid.uuid4()),
                ticket_id=ticket.id,
                message_id=message.id,
                source="helpdesk",
                original_filename="report.pdf",
                mime_type="application/pdf",
                file_size=3,
                local_path=str(path),
                telegram_delivery_status="uploading",
                created_at=datetime.utcnow(),
            )
            claim_session.add_all([message, attachment])
            claim_session.flush()
            claimed_preload.final_attachment_id = attachment.id
            claimed_preload.reply_to_message_id = "321"
            claimed_preload.prefer_caption = True
            claim_session.commit()
            created_ids["message_id"] = message.id
            created_ids["attachment_id"] = attachment.id
        finally:
            claim_session.close()
        return {"result": {"message_id": 301, "document": {"file_id": "stage-file-id", "file_unique_id": "stage-unique-id"}}}

    monkeypatch.setattr(outbound_service, "send_document", _claim_during_staging)
    monkeypatch.setattr(outbound_service, "delete_message", lambda *_args, **_kwargs: None)
    calls = {"send_document_by_file_id": 0}
    monkeypatch.setattr(
        outbound_service,
        "send_document_by_file_id",
        lambda *_args, **_kwargs: calls.__setitem__("send_document_by_file_id", calls["send_document_by_file_id"] + 1) or {"result": {"message_id": 889}},
    )
    monkeypatch.setattr(
        outbound_service,
        "send_message",
        lambda chat_id, *_args, **_kwargs: {"result": {"message_id": 300}} if chat_id == "staging-chat" else (_ for _ in ()).throw(AssertionError("caption path should be used")),
    )

    outbound_service.deliver_attachment_preload(preload_id=preload.id)

    check = SessionFactory()
    try:
        saved_attachment = check.get(TicketAttachment, created_ids["attachment_id"])
        saved_message = check.get(TicketMessage, created_ids["message_id"])
        assert check.get(outbound_service.TicketAttachmentPreload, preload.id) is None
        assert saved_attachment.telegram_file_id == "stage-file-id"
        assert saved_attachment.telegram_delivery_status == "sent"
        assert saved_attachment.telegram_message_id == "889"
        assert saved_message.telegram_message_id == "889"
    finally:
        check.close()
    assert calls["send_document_by_file_id"] == 1


def test_preload_worker_skips_duplicate_final_send_when_attachment_already_sent(monkeypatch, tmp_path):
    _engine, SessionFactory, session = _session_bundle()
    ticket = _seed_ticket(session)
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.AGENT.value,
        body="Hello",
        author_user_id="agent-1",
        telegram_message_id="777",
        created_at=datetime.utcnow(),
    )
    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=message.id,
        source="helpdesk",
        original_filename="report.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=str(tmp_path / "report.pdf"),
        telegram_delivery_status="sent",
        telegram_message_id="888",
        created_at=datetime.utcnow(),
    )
    Path(attachment.local_path).write_bytes(b"pdf")
    preload = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="agent-1",
        original_filename="report.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=attachment.local_path,
        status="uploading",
        final_attachment_id=attachment.id,
        created_at=datetime.utcnow(),
    )
    session.add_all([message, attachment, preload])
    session.commit()

    monkeypatch.setattr(outbound_service, "SessionLocal", SessionFactory)
    monkeypatch.setattr(outbound_service.settings, "telegram_staging_chat_id", "staging-chat")
    monkeypatch.setattr(outbound_service, "send_document", lambda *_args, **_kwargs: {"result": {"message_id": 301, "document": {"file_id": "stage-file-id", "file_unique_id": "stage-unique-id"}}})
    monkeypatch.setattr(outbound_service, "delete_message", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(outbound_service, "send_document_by_file_id", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("duplicate client delivery should not run")))
    monkeypatch.setattr(
        outbound_service,
        "send_message",
        lambda chat_id, *_args, **_kwargs: {"result": {"message_id": 300}} if chat_id == "staging-chat" else (_ for _ in ()).throw(AssertionError("duplicate text send should not run")),
    )

    outbound_service.deliver_attachment_preload(preload_id=preload.id)

    check = SessionFactory()
    try:
        saved_attachment = check.get(TicketAttachment, attachment.id)
        assert check.get(outbound_service.TicketAttachmentPreload, preload.id) is None
        assert saved_attachment.telegram_file_id == "stage-file-id"
        assert saved_attachment.telegram_delivery_status == "sent"
        assert saved_attachment.telegram_message_id == "888"
    finally:
        check.close()
    assert not Path(attachment.local_path).exists()


def test_preload_worker_sends_info_before_staging_file_and_keeps_exact_ids(monkeypatch, tmp_path):
    _engine, SessionFactory, session = _session_bundle()
    ticket = _seed_ticket(session)
    ticket.ticket_number = 480
    user = ticket_messages_service.User(
        id="agent-1",
        company_id=ticket.company_id,
        full_name="Иван Петров",
        role="agent",
    )
    path = tmp_path / "report.pdf"
    path.write_bytes(b"pdf")
    preload = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id=user.id,
        original_filename="report.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=str(path),
        status="uploading",
        created_at=datetime.utcnow(),
    )
    session.add_all([user, preload])
    session.commit()

    monkeypatch.setattr(outbound_service, "SessionLocal", SessionFactory)
    monkeypatch.setattr(outbound_service.settings, "telegram_staging_chat_id", "staging-chat")
    calls = []
    monkeypatch.setattr(
        outbound_service,
        "send_message",
        lambda chat_id, text, **kwargs: calls.append(("info", chat_id, text, kwargs)) or {"result": {"message_id": 301}},
    )
    monkeypatch.setattr(
        outbound_service,
        "send_document",
        lambda chat_id, path_value, **kwargs: calls.append(("file", chat_id, str(path_value), kwargs)) or {
            "result": {"message_id": 302, "document": {"file_id": "stage-file-id", "file_unique_id": "stage-unique-id"}}
        },
    )
    edits = []
    monkeypatch.setattr(outbound_service, "edit_message_text", lambda chat_id, message_id, text: edits.append((chat_id, message_id, text)) or {"ok": True})

    outbound_service.deliver_attachment_preload(preload_id=preload.id)

    check = SessionFactory()
    try:
        saved = check.get(outbound_service.TicketAttachmentPreload, preload.id)
        assert saved is not None
        assert saved.status == "ready"
        assert saved.telegram_staging_info_message_id == "301"
        assert saved.telegram_staging_file_message_id == "302"
        assert saved.telegram_file_id == "stage-file-id"
    finally:
        check.close()

    assert calls[0][0] == "info"
    assert calls[1][0] == "file"
    assert "Заявка №480" in calls[0][2]
    assert "Иван Петров" in calls[0][2]
    assert "Статус: подготовка файла" in calls[0][2]
    assert "caption" not in calls[1][3]
    assert edits and "Статус: готов к отправке" in edits[-1][2]


def test_multiple_preloads_same_archive_group_share_one_info_card_and_filename_list(monkeypatch, tmp_path):
    _engine, SessionFactory, session = _session_bundle()
    ticket = _seed_ticket(session)
    ticket.ticket_number = 481
    user = ticket_messages_service.User(
        id="agent-1",
        company_id=ticket.company_id,
        full_name="Иван Петров",
        role="agent",
    )
    first_path = tmp_path / "a.pdf"
    second_path = tmp_path / "b.pdf"
    first_path.write_bytes(b"a")
    second_path.write_bytes(b"b")
    first = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id=user.id,
        archive_group_id="group-1",
        original_filename="a.pdf",
        mime_type="application/pdf",
        file_size=1,
        local_path=str(first_path),
        status="uploading",
        created_at=datetime.utcnow(),
    )
    second = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id=user.id,
        archive_group_id="group-1",
        original_filename="b.pdf",
        mime_type="application/pdf",
        file_size=1,
        local_path=str(second_path),
        status="uploading",
        created_at=datetime.utcnow(),
    )
    session.add_all([user, first, second])
    session.commit()

    monkeypatch.setattr(outbound_service, "SessionLocal", SessionFactory)
    monkeypatch.setattr(outbound_service.settings, "telegram_staging_chat_id", "staging-chat")
    info_calls = []
    monkeypatch.setattr(
        outbound_service,
        "send_message",
        lambda chat_id, text, **kwargs: info_calls.append((chat_id, text, kwargs)) or {"result": {"message_id": 301}},
    )
    file_message_ids = iter([302, 303])
    monkeypatch.setattr(
        outbound_service,
        "send_document",
        lambda chat_id, path_value, **kwargs: {
            "result": {
                "message_id": next(file_message_ids),
                "document": {
                    "file_id": f"stage-{Path(path_value).name}",
                    "file_unique_id": f"uniq-{Path(path_value).name}",
                },
            }
        },
    )
    edits = []
    monkeypatch.setattr(outbound_service, "edit_message_text", lambda chat_id, message_id, text: edits.append((chat_id, str(message_id), text)) or {"ok": True})

    outbound_service.deliver_attachment_preload(preload_id=first.id)
    outbound_service.deliver_attachment_preload(preload_id=second.id)

    check = SessionFactory()
    try:
        saved_first = check.get(outbound_service.TicketAttachmentPreload, first.id)
        saved_second = check.get(outbound_service.TicketAttachmentPreload, second.id)
        assert saved_first is not None
        assert saved_second is not None
        assert saved_first.telegram_staging_info_message_id == "301"
        assert saved_second.telegram_staging_info_message_id == "301"
        assert saved_first.status == "ready"
        assert saved_second.status == "ready"
    finally:
        check.close()

    assert len(info_calls) == 1
    assert any("• a.pdf" in text and "• b.pdf" in text for _, _, text in edits)
    assert any("Статус: готово к отправке" in text for _, _, text in edits)


def test_remove_one_preload_from_group_keeps_common_card_and_updates_filename_list(monkeypatch, tmp_path):
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    first_path = tmp_path / "a.pdf"
    second_path = tmp_path / "b.pdf"
    first_path.write_bytes(b"a")
    second_path.write_bytes(b"b")
    first = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="agent-1",
        archive_group_id="group-1",
        original_filename="a.pdf",
        mime_type="application/pdf",
        file_size=1,
        local_path=str(first_path),
        status="ready",
        telegram_staging_info_message_id="401",
        telegram_staging_file_message_id="402",
        created_at=datetime.utcnow(),
    )
    second = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="agent-1",
        archive_group_id="group-1",
        original_filename="b.pdf",
        mime_type="application/pdf",
        file_size=1,
        local_path=str(second_path),
        status="ready",
        telegram_staging_info_message_id="401",
        telegram_staging_file_message_id="403",
        created_at=datetime.utcnow(),
    )
    session.add_all([first, second])
    session.commit()

    monkeypatch.setattr(outbound_service.settings, "telegram_staging_chat_id", "staging-chat")
    deleted = []
    edits = []
    monkeypatch.setattr(outbound_service, "delete_message", lambda chat_id, message_id: deleted.append((chat_id, str(message_id))) or {"ok": True})
    monkeypatch.setattr(outbound_service, "edit_message_text", lambda chat_id, message_id, text: edits.append((chat_id, str(message_id), text)) or {"ok": True})

    result = delete_attachment_preload(
        ticket_id=ticket.id,
        preload_id=first.id,
        db=session,
        user=SimpleNamespace(id="agent-1", role="admin"),
    )

    assert result == {"ok": True}
    assert deleted == [("staging-chat", "402")]
    assert session.get(outbound_service.TicketAttachmentPreload, first.id) is None
    assert session.get(outbound_service.TicketAttachmentPreload, second.id) is not None
    assert session.get(outbound_service.TicketAttachmentPreload, second.id).telegram_staging_info_message_id == "401"
    assert edits
    assert "• b.pdf" in edits[-1][2]
    assert "• a.pdf" not in edits[-1][2]


def test_group_ready_and_uploading_status_is_preparing_files(monkeypatch, tmp_path):
    _engine, SessionFactory, session = _session_bundle()
    ticket = _seed_ticket(session)
    ready_path = tmp_path / "ready.pdf"
    uploading_path = tmp_path / "uploading.pdf"
    ready_path.write_bytes(b"a")
    uploading_path.write_bytes(b"b")
    ready_preload = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="agent-1",
        archive_group_id="group-1",
        original_filename="ready.pdf",
        mime_type="application/pdf",
        file_size=1,
        local_path=str(ready_path),
        status="ready",
        telegram_file_id="ready-file-id",
        telegram_staging_info_message_id="501",
        telegram_staging_file_message_id="502",
        created_at=datetime.utcnow(),
    )
    uploading_preload = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="agent-1",
        archive_group_id="group-1",
        original_filename="uploading.pdf",
        mime_type="application/pdf",
        file_size=1,
        local_path=str(uploading_path),
        status="uploading",
        telegram_staging_info_message_id="501",
        created_at=datetime.utcnow(),
    )
    session.add_all([ready_preload, uploading_preload])
    session.commit()

    monkeypatch.setattr(outbound_service, "SessionLocal", SessionFactory)
    monkeypatch.setattr(outbound_service.settings, "telegram_staging_chat_id", "staging-chat")
    edits = []
    monkeypatch.setattr(outbound_service, "edit_message_text", lambda chat_id, message_id, text: edits.append((chat_id, str(message_id), text)) or {"ok": True})

    outbound_service.deliver_attachment_preload(preload_id=ready_preload.id)

    assert edits == []


def test_archive_edit_message_not_modified_is_benign_noop(monkeypatch, tmp_path, caplog):
    _engine, SessionFactory, session = _session_bundle()
    ticket = _seed_ticket(session)
    ticket.ticket_number = 490
    user = ticket_messages_service.User(
        id="agent-1",
        company_id=ticket.company_id,
        full_name="Иван Петров",
        role="agent",
    )
    path = tmp_path / "report.pdf"
    path.write_bytes(b"pdf")
    preload = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id=user.id,
        original_filename="report.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=str(path),
        status="ready",
        telegram_file_id="stage-file-id",
        telegram_staging_info_message_id="501",
        telegram_staging_file_message_id="502",
        created_at=datetime.utcnow(),
    )
    session.add_all([user, preload])
    session.commit()

    monkeypatch.setattr(outbound_service, "SessionLocal", SessionFactory)
    monkeypatch.setattr(outbound_service.settings, "telegram_staging_chat_id", "staging-chat")
    monkeypatch.setattr(
        outbound_service,
        "edit_message_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(outbound_service.TelegramApiError("Bad Request: message is not modified")),
    )

    with caplog.at_level(logging.DEBUG):
        outbound_service.deliver_attachment_preload(preload_id=preload.id)

    check = SessionFactory()
    try:
        saved = check.get(outbound_service.TicketAttachmentPreload, preload.id)
        assert saved is not None
        assert saved.status == "ready"
    finally:
        check.close()

    assert not any(record.levelno >= logging.WARNING for record in caplog.records if "message is not modified" in record.getMessage())


def test_archive_edit_rate_limit_does_not_break_final_delivery(monkeypatch, tmp_path):
    _engine, SessionFactory, session = _session_bundle()
    ticket = _seed_ticket(session)
    ticket.ticket_number = 491
    user = ticket_messages_service.User(
        id="agent-1",
        company_id=ticket.company_id,
        full_name="Иван Петров",
        role="agent",
    )
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.AGENT.value,
        body="Hello",
        author_user_id="agent-1",
        created_at=datetime.utcnow(),
    )
    path = tmp_path / "report.pdf"
    path.write_bytes(b"pdf")
    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=message.id,
        source="helpdesk",
        original_filename="report.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=str(path),
        telegram_delivery_status="uploading",
        created_at=datetime.utcnow(),
    )
    preload = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id=user.id,
        original_filename="report.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=str(path),
        status="uploading",
        final_attachment_id=attachment.id,
        prefer_caption=True,
        telegram_staging_info_message_id="601",
        telegram_staging_file_message_id="602",
        created_at=datetime.utcnow(),
    )
    session.add_all([user, message, attachment, preload])
    session.commit()

    monkeypatch.setattr(outbound_service, "SessionLocal", SessionFactory)
    monkeypatch.setattr(outbound_service.settings, "telegram_staging_chat_id", "staging-chat")
    monkeypatch.setattr(outbound_service, "send_document", lambda *_args, **_kwargs: {"result": {"message_id": 602, "document": {"file_id": "stage-file-id", "file_unique_id": "stage-unique-id"}}})
    monkeypatch.setattr(outbound_service, "delete_message", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(outbound_service, "send_message", lambda chat_id, *_args, **_kwargs: {"result": {"message_id": 601}} if chat_id == "staging-chat" else (_ for _ in ()).throw(AssertionError("text path should not be used")))
    monkeypatch.setattr(outbound_service, "send_document_by_file_id", lambda *_args, **_kwargs: {"result": {"message_id": 888}})
    monkeypatch.setattr(
        outbound_service,
        "edit_message_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(outbound_service.TelegramApiError("Too Many Requests: retry after 3")),
    )

    outbound_service.deliver_attachment_preload(preload_id=preload.id)

    check = SessionFactory()
    try:
        saved_attachment = check.get(TicketAttachment, attachment.id)
        assert saved_attachment.telegram_delivery_status == "sent"
        assert saved_attachment.telegram_message_id == "888"
        assert check.get(outbound_service.TicketAttachmentPreload, preload.id) is None
    finally:
        check.close()


def test_group_final_failure_marks_common_archive_error(monkeypatch, tmp_path):
    _engine, SessionFactory, session = _session_bundle()
    ticket = _seed_ticket(session)
    ticket.ticket_number = 481
    user = ticket_messages_service.User(
        id="agent-1",
        company_id=ticket.company_id,
        full_name="Иван Петров",
        role="agent",
    )
    message_one = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.AGENT.value,
        body="",
        author_user_id="agent-1",
        created_at=datetime.utcnow(),
    )
    message_two = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.AGENT.value,
        body="",
        author_user_id="agent-1",
        created_at=datetime.utcnow(),
    )
    first_path = tmp_path / "a.pdf"
    second_path = tmp_path / "b.pdf"
    first_path.write_bytes(b"a")
    second_path.write_bytes(b"b")
    attachment_one = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=message_one.id,
        source="helpdesk",
        original_filename="a.pdf",
        mime_type="application/pdf",
        file_size=1,
        local_path=str(first_path),
        telegram_delivery_status="uploading",
        created_at=datetime.utcnow(),
    )
    attachment_two = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=message_two.id,
        source="helpdesk",
        original_filename="b.pdf",
        mime_type="application/pdf",
        file_size=1,
        local_path=str(second_path),
        telegram_delivery_status="uploading",
        created_at=datetime.utcnow(),
    )
    preload_one = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id=user.id,
        archive_group_id="group-1",
        original_filename="a.pdf",
        mime_type="application/pdf",
        file_size=1,
        local_path=str(first_path),
        status="uploading",
        final_attachment_id=attachment_one.id,
        telegram_staging_info_message_id="601",
        created_at=datetime.utcnow(),
    )
    preload_two = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id=user.id,
        archive_group_id="group-1",
        original_filename="b.pdf",
        mime_type="application/pdf",
        file_size=1,
        local_path=str(second_path),
        status="uploading",
        final_attachment_id=attachment_two.id,
        telegram_staging_info_message_id="601",
        created_at=datetime.utcnow(),
    )
    session.add_all([user, message_one, message_two, attachment_one, attachment_two, preload_one, preload_two])
    session.commit()

    monkeypatch.setattr(outbound_service, "SessionLocal", SessionFactory)
    monkeypatch.setattr(outbound_service.settings, "telegram_staging_chat_id", "staging-chat")
    staged_ids = iter([701, 702])
    monkeypatch.setattr(
        outbound_service,
        "send_document",
        lambda *_args, **_kwargs: {
            "result": {
                "message_id": next(staged_ids),
                "document": {"file_id": f"stage-{uuid.uuid4()}", "file_unique_id": f"uniq-{uuid.uuid4()}"},
            }
        },
    )
    delivery_calls = {"count": 0}

    def _deliver_by_file_id(*_args, **_kwargs):
        delivery_calls["count"] += 1
        if delivery_calls["count"] == 1:
            return {"result": {"message_id": 901}}
        raise outbound_service.TelegramApiError("client delivery failed")

    monkeypatch.setattr(outbound_service, "send_document_by_file_id", _deliver_by_file_id)
    monkeypatch.setattr(outbound_service, "send_message", lambda chat_id, *_args, **_kwargs: {"result": {"message_id": 601}} if chat_id == "staging-chat" else (_ for _ in ()).throw(AssertionError("client text path should not run")))
    edits = []
    monkeypatch.setattr(outbound_service, "edit_message_text", lambda chat_id, message_id, text: edits.append((chat_id, str(message_id), text)) or {"ok": True})

    outbound_service.deliver_attachment_preload(preload_id=preload_one.id)
    outbound_service.deliver_attachment_preload(preload_id=preload_two.id)

    check = SessionFactory()
    try:
        assert check.get(outbound_service.TicketAttachmentPreload, preload_one.id) is None
        assert check.get(outbound_service.TicketAttachmentPreload, preload_two.id) is None
        assert check.get(TicketAttachment, attachment_one.id).telegram_delivery_status == "sent"
        assert check.get(TicketAttachment, attachment_two.id).telegram_delivery_status == "failed"
    finally:
        check.close()

    assert any("Статус: ошибка доставки" in text for _, _, text in edits)


def test_remove_ready_preload_deletes_exact_archive_messages(monkeypatch, tmp_path):
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    path = tmp_path / "report.pdf"
    path.write_bytes(b"pdf")
    preload = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="admin-1",
        original_filename="report.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=str(path),
        status="ready",
        telegram_staging_info_message_id="301",
        telegram_staging_file_message_id="302",
        created_at=datetime.utcnow(),
    )
    session.add(preload)
    session.commit()

    monkeypatch.setattr(outbound_service.settings, "telegram_staging_chat_id", "staging-chat")
    deleted = []
    monkeypatch.setattr(outbound_service, "delete_message", lambda chat_id, message_id: deleted.append((chat_id, str(message_id))) or {"ok": True})

    result = delete_attachment_preload(
        ticket_id=ticket.id,
        preload_id=preload.id,
        db=session,
        user=SimpleNamespace(id="admin-1", role="admin"),
    )

    assert result == {"ok": True}
    assert deleted == [("staging-chat", "302"), ("staging-chat", "301")]


def test_stale_cleanup_deletes_exact_archive_messages(monkeypatch, tmp_path):
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    path = tmp_path / "old.pdf"
    path.write_bytes(b"old")
    preload = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="agent-1",
        original_filename="old.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=str(path),
        status="ready",
        telegram_staging_info_message_id="401",
        telegram_staging_file_message_id="402",
        created_at=datetime.utcnow() - outbound_service.timedelta(hours=25),
    )
    session.add(preload)
    session.commit()

    monkeypatch.setattr(outbound_service.settings, "telegram_staging_chat_id", "staging-chat")
    deleted = []
    monkeypatch.setattr(outbound_service, "delete_message", lambda chat_id, message_id: deleted.append((chat_id, str(message_id))) or {"ok": True})

    removed = outbound_service.cleanup_stale_attachment_preloads(session)

    assert removed == 1
    assert deleted == [("staging-chat", "402"), ("staging-chat", "401")]


def test_claimed_preload_archive_is_kept_and_marked_sent(monkeypatch, tmp_path):
    _engine, SessionFactory, session = _session_bundle()
    ticket = _seed_ticket(session)
    ticket.ticket_number = 480
    user = ticket_messages_service.User(
        id="agent-1",
        company_id=ticket.company_id,
        full_name="Иван Петров",
        role="agent",
    )
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.AGENT.value,
        body="Hello",
        author_user_id="agent-1",
        created_at=datetime.utcnow(),
    )
    path = tmp_path / "report.pdf"
    path.write_bytes(b"pdf")
    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=message.id,
        source="helpdesk",
        original_filename="report.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=str(path),
        telegram_delivery_status="uploading",
        created_at=datetime.utcnow(),
    )
    preload = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id=user.id,
        original_filename="report.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=str(path),
        status="uploading",
        final_attachment_id=attachment.id,
        prefer_caption=True,
        telegram_staging_info_message_id="501",
        telegram_staging_file_message_id="502",
        created_at=datetime.utcnow(),
    )
    session.add_all([user, message, attachment, preload])
    session.commit()

    monkeypatch.setattr(outbound_service, "SessionLocal", SessionFactory)
    monkeypatch.setattr(outbound_service.settings, "telegram_staging_chat_id", "staging-chat")
    monkeypatch.setattr(outbound_service, "send_document", lambda *_args, **_kwargs: {"result": {"message_id": 502, "document": {"file_id": "stage-file-id", "file_unique_id": "stage-unique-id"}}})
    monkeypatch.setattr(outbound_service, "delete_message", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("claimed archive should not be deleted")))
    monkeypatch.setattr(outbound_service, "send_message", lambda chat_id, *_args, **_kwargs: {"result": {"message_id": 501}} if chat_id == "staging-chat" else (_ for _ in ()).throw(AssertionError("text path should not be used")))
    monkeypatch.setattr(outbound_service, "send_document_by_file_id", lambda *_args, **_kwargs: {"result": {"message_id": 888}})
    edits = []
    monkeypatch.setattr(outbound_service, "edit_message_text", lambda chat_id, message_id, text: edits.append((chat_id, str(message_id), text)) or {"ok": True})

    outbound_service.deliver_attachment_preload(preload_id=preload.id)

    check = SessionFactory()
    try:
        saved_attachment = check.get(TicketAttachment, attachment.id)
        assert saved_attachment.telegram_delivery_status == "sent"
        assert check.get(outbound_service.TicketAttachmentPreload, preload.id) is None
    finally:
        check.close()

    assert any("Статус: отправляется клиенту" in text for _, _, text in edits)
    assert any("Статус: отправлено" in text for _, _, text in edits)


def test_preparation_failure_before_claim_deletes_temporary_archive_messages(monkeypatch, tmp_path):
    _engine, SessionFactory, session = _session_bundle()
    ticket = _seed_ticket(session)
    user = ticket_messages_service.User(
        id="agent-1",
        company_id=ticket.company_id,
        full_name="Иван Петров",
        role="agent",
    )
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"pdf")
    preload = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id=user.id,
        original_filename="broken.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=str(path),
        status="uploading",
        created_at=datetime.utcnow(),
    )
    session.add_all([user, preload])
    session.commit()

    monkeypatch.setattr(outbound_service, "SessionLocal", SessionFactory)
    monkeypatch.setattr(outbound_service.settings, "telegram_staging_chat_id", "staging-chat")
    monkeypatch.setattr(outbound_service, "send_message", lambda *_args, **_kwargs: {"result": {"message_id": 601}})
    monkeypatch.setattr(outbound_service, "send_document", lambda *_args, **_kwargs: (_ for _ in ()).throw(outbound_service.TelegramApiError("staging failed")))
    deleted = []
    monkeypatch.setattr(outbound_service, "delete_message", lambda chat_id, message_id: deleted.append((chat_id, str(message_id))) or {"ok": True})

    outbound_service.deliver_attachment_preload(preload_id=preload.id)

    check = SessionFactory()
    try:
        assert check.get(outbound_service.TicketAttachmentPreload, preload.id) is None
    finally:
        check.close()

    assert deleted == [("staging-chat", "601")]


def test_stale_preload_cleanup_removes_old_unclaimed_and_keeps_claimed(tmp_path):
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    old_path = tmp_path / "old.pdf"
    old_path.write_bytes(b"old")
    claimed_path = tmp_path / "claimed.pdf"
    claimed_path.write_bytes(b"claimed")
    stale_unclaimed = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="agent-1",
        original_filename="old.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=str(old_path),
        status="ready",
        created_at=datetime.utcnow() - outbound_service.timedelta(hours=25),
    )
    stale_claimed = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="agent-1",
        original_filename="claimed.pdf",
        mime_type="application/pdf",
        file_size=7,
        local_path=str(claimed_path),
        status="uploading",
        final_attachment_id="attachment-1",
        created_at=datetime.utcnow() - outbound_service.timedelta(hours=25),
    )
    session.add_all([stale_unclaimed, stale_claimed])
    session.commit()

    removed = outbound_service.cleanup_stale_attachment_preloads(session)

    assert removed == 1
    assert session.get(outbound_service.TicketAttachmentPreload, stale_unclaimed.id) is None
    assert session.get(outbound_service.TicketAttachmentPreload, stale_claimed.id) is not None
    assert not old_path.exists()
    assert claimed_path.exists()
