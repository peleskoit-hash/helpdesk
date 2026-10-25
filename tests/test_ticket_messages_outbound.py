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

import pytest
from fastapi import HTTPException
from fastapi import BackgroundTasks
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.db import Base
from api.app.models import Client, Company, MessageType, StatsUserStatusInterval, TelegramContact, Ticket, TicketAttachment, TicketMessage, TicketStatus, TicketWorkflowType, User
from api.app.routers import tickets as tickets_router
from api.app.routers.tickets import create_attachment_preload, delete_attachment_preload, get_attachment_preload_status, get_ticket, update_ticket_draft
from api.app.services import telegram_bot as telegram_bot_service
from api.app.services import telegram_outbound_delivery as outbound_service
from api.app.services import ticket_messages as ticket_messages_service
from api.app.services.auto_close import refresh_ticket_auto_close_timer
from api.app.services.ticket_attachments import _attachment_out, _send_attachment_to_telegram_client
from api.app.schemas import TicketDraftPayload
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
    def __init__(
        self,
        *,
        text: str | None,
        files: list[object],
        quote_message_id: str | None = None,
        single_attachment: bool = False,
        preloaded_attachment_ids: list[str] | None = None,
    ):
        super().__init__()
        self["text"] = text
        self["quote_message_id"] = quote_message_id
        if single_attachment:
            self["single_attachment"] = "true"
        self._files = files
        self._preloaded_attachment_ids = list(preloaded_attachment_ids or [])

    def getlist(self, key: str):
        if key == "files":
            return list(self._files)
        if key == "preloaded_attachment_id":
            values = list(self._preloaded_attachment_ids)
            if self.get("preloaded_attachment_id"):
                values.append(self["preloaded_attachment_id"])
            return values
        return []


class _MultipartRequest:
    headers = {"content-type": "multipart/form-data; boundary=test"}

    def __init__(self, form_data: _FormData):
        self._form_data = form_data

    async def form(self):
        return self._form_data


class _JsonRequest:
    headers = {"content-type": "application/json"}

    def __init__(self, payload: dict[str, object]):
        self._payload = payload

    async def json(self):
        return self._payload


def _multipart_request_with_preload(*, text: str | None, preload_id: str, single_attachment: bool = False, quote_message_id: str | None = None):
    form_data = _FormData(
        text=text,
        files=[],
        quote_message_id=quote_message_id,
        single_attachment=single_attachment,
        preloaded_attachment_ids=[preload_id],
    )
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


@pytest.fixture(params=["telegram", "web"])
def text_channel_case(request, monkeypatch):
    engine, _Session, db = _session_bundle()
    ticket = _seed_ticket(db)
    ticket.channel = request.param
    ticket.assignee_id = "agent-1"
    ticket.client_close_message_id = "700"
    db.commit()
    calls = []

    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected Telegram contact lookup or API call")

    monkeypatch.setattr(tickets_router, "send_message", lambda *args, **kwargs:
                        calls.append(("send", args, kwargs)) or {"result": {"message_id": 901}})
    monkeypatch.setattr(tickets_router, "remove_inline_keyboard", lambda *args, **kwargs:
                        calls.append(("keyboard", args, kwargs)))
    monkeypatch.setattr(ticket_messages_service, "edit_message_text", lambda *args, **kwargs:
                        calls.append(("edit", args, kwargs)))
    monkeypatch.setattr(ticket_messages_service, "delete_message", lambda *args, **kwargs:
                        calls.append(("delete", args, kwargs)))
    monkeypatch.setattr(ticket_messages_service, "edit_message_caption", forbidden)
    if ticket.channel == "web":
        # The seeded client even has a Telegram contact: it must never be used.
        monkeypatch.setattr(tickets_router, "_telegram_contact_for_client", forbidden)
    try:
        yield SimpleNamespace(db=db, ticket=ticket, calls=calls,
                              user=SimpleNamespace(id="agent-1", role="agent"))
    finally:
        db.close()
        engine.dispose()


def _run_text_channel_operation(case, operation, message_id=None):
    if operation in {"reply", "comment"}:
        return asyncio.run(getattr(tickets_router, operation)(
            ticket_id=case.ticket.id, request=_JsonRequest({"text": "Answer"}),
            background_tasks=BackgroundTasks(), db=case.db, user=case.user,
        ))
    kwargs = dict(ticket_id=case.ticket.id, message_id=message_id, db=case.db, user=case.user)
    if operation == "edit":
        return tickets_router.edit_ticket_message(**kwargs, payload=SimpleNamespace(text="Edited"))
    return tickets_router.delete_ticket_message(**kwargs)


def test_text_channel_reply_edit_delete(text_channel_case):
    case = text_channel_case
    assert _run_text_channel_operation(case, "reply") == {"ok": True}
    message, = _messages(case.db, case.ticket.id)
    assert message.body == "Answer"
    assert message.message_type == MessageType.AGENT.value
    assert message.author_user_id == case.user.id
    assert message.message_status == "normal"
    assert message.telegram_message_id == ("901" if case.ticket.channel == "telegram" else None)
    assert case.ticket.status == TicketStatus.PENDING.value
    assert _run_text_channel_operation(case, "edit", message.id) == {"ok": True}
    case.db.refresh(message)
    assert message.body == "Edited"
    assert message.message_status == "edited"
    assert message.status_note
    assert _run_text_channel_operation(case, "delete", message.id) == {"ok": True}
    case.db.refresh(message)
    assert message.body == "Edited"
    assert message.message_status == "deleted"
    assert message.status_note
    assert _run_text_channel_operation(case, "delete", message.id) == {"ok": True}
    if case.ticket.channel == "telegram":
        assert [call[0] for call in case.calls] == ["send", "keyboard", "edit", "delete"]
        assert case.calls[0][1][:2] == ("chat-42", "Answer")
        assert case.calls[2][1] == ("chat-42", "901", "Edited")
        assert case.calls[3][1] == ("chat-42", "901")
    else:
        assert case.calls == []


def test_text_channel_internal_comment_does_not_deliver(text_channel_case):
    case = text_channel_case
    assert _run_text_channel_operation(case, "comment") == {"ok": True}
    message, = _messages(case.db, case.ticket.id)
    assert message.message_type == MessageType.INTERNAL.value
    assert message.body == "Answer"
    assert message.telegram_message_id is None
    assert case.calls == []


@pytest.mark.parametrize("operation", ["reply", "comment", "edit", "delete"])
@pytest.mark.parametrize("reason,code,status", [
    ("role", "readonly_role", 403),
    ("closed", "ticket_closed_readonly", 409),
    ("assignment", "forbidden_ticket_access", 403),
])
def test_text_channel_preserves_access_checks(text_channel_case, operation, reason, code, status):
    case = text_channel_case
    message = TicketMessage(id=str(uuid.uuid4()), ticket_id=case.ticket.id,
                            message_type=MessageType.AGENT.value, body="Original", telegram_message_id="901")
    case.db.add(message)
    if reason == "role":
        case.user.role = "chiki_piki"
        case.ticket.button_config_key = "Колл Центр|test"
    elif reason == "closed":
        case.ticket.status = TicketStatus.CLOSED.value
    else:
        case.ticket.assignee_id = "other-agent"
    case.db.commit()
    with pytest.raises(HTTPException) as exc:
        _run_text_channel_operation(case, operation, message.id)
    assert exc.value.status_code == status
    assert exc.value.detail["error"]["code"] == code
    case.db.refresh(message)
    assert message.body == "Original"
    assert message.message_status == "normal"
    assert len(_messages(case.db, case.ticket.id)) == 1
    assert case.calls == []


@pytest.mark.parametrize("telegram_id", [None, "901"])
def test_text_channel_edit_requires_telegram_id_only_for_telegram(text_channel_case, telegram_id):
    case = text_channel_case
    message = TicketMessage(id=str(uuid.uuid4()), ticket_id=case.ticket.id,
                            message_type=MessageType.AGENT.value, body="Original", telegram_message_id=telegram_id,
                            author_user_id=case.user.id)
    case.db.add(message)
    case.db.commit()
    if case.ticket.channel == "telegram" and telegram_id is None:
        with pytest.raises(HTTPException) as exc:
            _run_text_channel_operation(case, "edit", message.id)
        assert exc.value.detail["error"]["code"] == "telegram_message_id_missing"
        assert case.calls == []
        return
    assert _run_text_channel_operation(case, "edit", message.id) == {"ok": True}
    assert _run_text_channel_operation(case, "delete", message.id) == {"ok": True}
    assert [call[0] for call in case.calls] == (["edit", "delete"] if case.ticket.channel == "telegram" else [])


@pytest.mark.parametrize("channel", ["telegram", "web"])
@pytest.mark.parametrize("workflow,transport", [
    ("ticket", "raw"), ("ticket", "ready"), ("ticket", "uploading"),
    ("task", "ready"), ("task", "raw"), ("group_child", "raw"),
])
def test_channel_file_archiving_reply_and_task_completion(monkeypatch, tmp_path, channel, workflow, transport):
    from api.app.services import ticket_attachments

    engine, SessionFactory, db = _session_bundle()
    ticket = _seed_ticket(db)
    ticket.channel = channel
    group_child = workflow == "group_child"
    if group_child:
        workflow = "task"
        parent = Ticket(id=str(uuid.uuid4()), company_id=ticket.company_id, client_id=ticket.client_id,
                        subject="Group", channel=channel, workflow_type="task", task_kind="group_parent",
                        status="pending")
        db.add(parent)
        db.add(TicketMessage(id=str(uuid.uuid4()), ticket_id=parent.id, message_type="client", body="Group request"))
        ticket.parent_ticket_id = parent.id
        ticket.task_kind = "group_child"
    ticket.workflow_type = workflow
    ticket.assignee_id = "agent-1"
    ticket.client_close_message_id = "700"
    db.add(User(id="agent-1", company_id=ticket.company_id, full_name="Agent", role="agent"))
    db.add(TicketMessage(id=str(uuid.uuid4()), ticket_id=ticket.id, message_type="client", body="Request"))
    db.commit()
    user = SimpleNamespace(id="agent-1", role="agent")
    calls = []

    def send_document(chat_id, *args, **kwargs):
        calls.append(("document", chat_id))
        return {"result": {"message_id": 301, "document": {
            "file_id": "archive-file-id", "file_unique_id": "archive-unique-id"}}}

    def send_text(chat_id, *args, **kwargs):
        calls.append(("text", chat_id))
        return {"result": {"message_id": 401}}

    def send_file_id(chat_id, *args, **kwargs):
        calls.append(("file-id", chat_id))
        return {"result": {"message_id": 501}}

    monkeypatch.setattr(outbound_service, "SessionLocal", SessionFactory)
    monkeypatch.setattr(outbound_service.settings, "telegram_staging_chat_id", "staging-chat")
    monkeypatch.setattr(ticket_attachments.settings, "attachment_storage_dir", str(tmp_path))
    monkeypatch.setattr(outbound_service, "send_document", send_document)
    monkeypatch.setattr(outbound_service, "send_message", send_text)
    monkeypatch.setattr(outbound_service, "send_document_by_file_id", send_file_id)
    monkeypatch.setattr(outbound_service, "edit_message_text", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(outbound_service, "delete_message", lambda *args, **kwargs:
                        pytest.fail("A claimed archive must be retained"))
    monkeypatch.setattr(tickets_router, "send_message", send_text)
    monkeypatch.setattr(tickets_router, "remove_inline_keyboard", lambda *args, **kwargs: calls.append(("keyboard", args[0])))
    monkeypatch.setattr(ticket_attachments, "send_document", send_document)
    monkeypatch.setattr(ticket_attachments, "send_document_by_file_id", send_file_id)
    if channel == "web":
        monkeypatch.setattr(tickets_router, "_telegram_contact_for_client", lambda *args:
                            pytest.fail("WEB must not look up the client's Telegram contact"))

    upload = SimpleNamespace(filename="result.pdf", file=io.BytesIO(b"pdf"), content_type="application/pdf")
    preload_id = None
    if transport == "raw":
        request = _MultipartRequest(_FormData(text="Final answer", files=[upload], single_attachment=True))
    else:
        preparation = BackgroundTasks()
        result = asyncio.run(tickets_router.create_attachment_preload(
            ticket.id, background_tasks=preparation, file=upload, archive_group_id="test-group", db=db, user=user))
        preload_id = result["id"]
        if transport == "ready":
            asyncio.run(preparation())
        request = _multipart_request_with_preload(text="Final answer", preload_id=preload_id, single_attachment=True)
    delivery = BackgroundTasks()
    assert asyncio.run(tickets_router.reply(ticket.id, request=request, background_tasks=delivery, db=db, user=user)) == {"ok": True}
    if transport == "uploading":
        asyncio.run(preparation())
    asyncio.run(delivery())
    db.expire_all()
    message, = [m for m in _messages(db, ticket.id) if m.message_type == "agent"]
    attachment, = _attachments(db, message.id)
    assert attachment.ticket_id == ticket.id
    assert attachment.message_id == message.id
    assert message.body == "Final answer"
    if not (workflow == "task" and transport == "raw" and channel == "telegram"):
        assert attachment.telegram_file_id == "archive-file-id"
        assert attachment.telegram_file_unique_id == "archive-unique-id"
        assert Path(attachment.local_path).exists() == (workflow == "task" and transport == "raw")
        assert ("document", "staging-chat") in calls
    if workflow == "ticket" or (channel == "web" and transport == "raw"):
        assert attachment.telegram_delivery_status == "sent"
    if workflow == "task":
        assert all(chat_id != "chat-42" for kind, chat_id in calls if kind != "keyboard")
        assert tickets_router.close(ticket.id, db=db, user=user) == {"ok": True}
        if group_child:
            assert parent.status == "closed"
            parent_answer, = [m for m in _messages(db, parent.id) if m.message_type == "agent"]
            parent_file, = _attachments(db, parent_answer.id)
            assert parent_answer.body == message.body
            assert parent_file.telegram_file_id == attachment.telegram_file_id
            assert parent_file.local_path == attachment.local_path
    if channel == "web":
        assert all(chat_id == "staging-chat" for _, chat_id in calls)
        assert message.telegram_message_id is None
        assert attachment.telegram_message_id is None
    else:
        assert any(chat_id == "chat-42" for _, chat_id in calls)
    # Retrying completed workers must not redeliver or reupload.
    previous = list(calls)
    outbound_service.deliver_ticket_attachment(attachment_id=attachment.id, message_id=message.id, ticket_id=ticket.id)
    assert calls == previous
    db.close()
    engine.dispose()


def test_web_task_raw_archive_failure_does_not_persist_an_unarchived_answer(monkeypatch, tmp_path):
    engine, _Session, db = _session_bundle()
    ticket = _seed_ticket(db)
    ticket.channel = "web"
    ticket.workflow_type = "task"
    ticket.assignee_id = "agent-1"
    db.commit()
    from api.app.services import ticket_attachments
    monkeypatch.setattr(ticket_attachments.settings, "attachment_storage_dir", str(tmp_path))
    monkeypatch.setattr(outbound_service, "_send_staging_document_from_path", lambda **kwargs:
                        (_ for _ in ()).throw(outbound_service.TelegramApiError("archive unavailable")))
    monkeypatch.setattr(tickets_router, "send_message", lambda *args, **kwargs:
                        pytest.fail("WEB must not send to the client"))
    upload = SimpleNamespace(filename="result.pdf", file=io.BytesIO(b"pdf"), content_type="application/pdf")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(tickets_router.reply(
            ticket.id, request=_MultipartRequest(_FormData(text="Answer", files=[upload])),
            background_tasks=BackgroundTasks(), db=db, user=SimpleNamespace(id="agent-1", role="agent")))
    assert exc.value.status_code == 502
    # Match the request session's rollback on failure.
    db.rollback()
    assert _messages(db, ticket.id) == []
    assert db.query(TicketAttachment).filter_by(ticket_id=ticket.id).count() == 0
    assert list(tmp_path.glob("*.pdf"))
    db.close()
    engine.dispose()


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


def test_ticket_draft_save_shared_revision_and_clear():
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    ticket.assignee_id = "agent-1"
    session.commit()

    saved = update_ticket_draft(
        ticket.id,
        TicketDraftPayload(text="hello", mode="comment"),
        db=session,
        user=SimpleNamespace(id="agent-1", role="agent"),
    )
    assert saved.text == "hello"
    assert saved.mode == "comment"
    assert saved.revision == 1

    card = get_ticket(ticket.id, db=session, user=SimpleNamespace(id="admin-1", role="admin"))
    assert card.draft_text == "hello"
    assert card.draft_mode == "comment"
    assert card.draft_revision == 1

    changed = update_ticket_draft(
        ticket.id,
        TicketDraftPayload(text="hello again", mode="reply"),
        db=session,
        user=SimpleNamespace(id="admin-1", role="admin"),
    )
    assert changed.revision == 2

    cleared = update_ticket_draft(
        ticket.id,
        TicketDraftPayload(text="", mode="reply"),
        db=session,
        user=SimpleNamespace(id="agent-1", role="agent"),
    )
    assert cleared.text == ""
    assert cleared.mode == "reply"
    assert cleared.revision == 3


def test_admin_draft_save_is_visible_to_assigned_agent_card():
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    ticket.assignee_id = "agent-1"
    session.commit()

    saved = update_ticket_draft(
        ticket.id,
        TicketDraftPayload(text="admin edit", mode="comment"),
        db=session,
        user=SimpleNamespace(id="admin-1", role="admin"),
    )

    card = get_ticket(ticket.id, db=session, user=SimpleNamespace(id="agent-1", role="agent"))
    assert card.draft_text == "admin edit"
    assert card.draft_mode == "comment"
    assert card.draft_revision == saved.revision


def test_ticket_draft_respects_ticket_access_rules():
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    ticket.assignee_id = "agent-1"
    session.commit()

    with pytest.raises(HTTPException) as read_exc:
        get_ticket(ticket.id, db=session, user=SimpleNamespace(id="agent-2", role="agent"))
    assert read_exc.value.status_code == 403

    with pytest.raises(HTTPException) as write_exc:
        update_ticket_draft(
            ticket.id,
            TicketDraftPayload(text="nope", mode="reply"),
            db=session,
            user=SimpleNamespace(id="agent-2", role="agent"),
        )
    assert write_exc.value.status_code == 403


def test_successful_reply_clears_ticket_draft_and_bumps_revision():
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    ticket.draft_text = "saved draft"
    ticket.draft_mode = "comment"
    ticket.draft_revision = 7
    session.commit()

    async def run():
        result = await reply_to_ticket(
            request=_JsonRequest({"text": "Plain answer", "quote_message_id": None}),
            db=session,
            user=SimpleNamespace(id="agent-1", role="agent"),
            ticket=ticket,
            send_to_telegram_client=lambda *_args, **_kwargs: {"result": {"message_id": 123}},
            telegram_message_id_from_response=lambda response: str(response["result"]["message_id"]),
            save_upload_attachment=lambda *_args, **_kwargs: None,
            send_attachment_to_telegram_client=lambda *_args, **_kwargs: None,
            claim_attachment_preload=lambda *_args, **_kwargs: None,
            schedule_attachment_delivery=lambda **_kwargs: None,
            unfreeze_ticket_on_message=lambda *_args, **_kwargs: None,
            remove_client_close_button=lambda *_args, **_kwargs: None,
            refresh_ticket_auto_close=lambda *_args, **_kwargs: None,
        )
        assert result == {"ok": True}

    asyncio.run(run())

    session.refresh(ticket)
    assert ticket.draft_text == ""
    assert ticket.draft_mode == "reply"
    assert ticket.draft_revision == 8


def test_failed_reply_keeps_ticket_draft():
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    ticket.draft_text = "keep me"
    ticket.draft_mode = "reply"
    ticket.draft_revision = 4
    session.commit()

    async def run():
        with pytest.raises(HTTPException):
            await reply_to_ticket(
                request=_JsonRequest({"text": "Plain answer", "quote_message_id": None}),
                db=session,
                user=SimpleNamespace(id="agent-1", role="agent"),
                ticket=ticket,
                send_to_telegram_client=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    HTTPException(status_code=502, detail={"error": {"code": "telegram_send_failed"}})
                ),
                telegram_message_id_from_response=lambda response: None,
                save_upload_attachment=lambda *_args, **_kwargs: None,
                send_attachment_to_telegram_client=lambda *_args, **_kwargs: None,
                claim_attachment_preload=lambda *_args, **_kwargs: None,
                schedule_attachment_delivery=lambda **_kwargs: None,
                unfreeze_ticket_on_message=lambda *_args, **_kwargs: None,
                remove_client_close_button=lambda *_args, **_kwargs: None,
                refresh_ticket_auto_close=lambda *_args, **_kwargs: None,
            )

    asyncio.run(run())

    session.refresh(ticket)
    assert ticket.draft_text == "keep me"
    assert ticket.draft_mode == "reply"
    assert ticket.draft_revision == 4


def test_successful_internal_comment_clears_ticket_draft_and_bumps_revision():
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    ticket.draft_text = "comment draft"
    ticket.draft_mode = "comment"
    ticket.draft_revision = 10
    session.commit()

    async def run():
        result = await reply_to_ticket(
            request=_JsonRequest({"text": "Internal note", "quote_message_id": None}),
            db=session,
            user=SimpleNamespace(id="agent-1", role="agent"),
            ticket=ticket,
            message_type=MessageType.INTERNAL,
            send_to_telegram_client=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("comment text must not be sent")),
            telegram_message_id_from_response=lambda response: None,
            save_upload_attachment=lambda *_args, **_kwargs: None,
            send_attachment_to_telegram_client=lambda *_args, **_kwargs: None,
            claim_attachment_preload=lambda *_args, **_kwargs: None,
            schedule_attachment_delivery=lambda **_kwargs: None,
            unfreeze_ticket_on_message=lambda *_args, **_kwargs: None,
            remove_client_close_button=lambda *_args, **_kwargs: None,
            refresh_ticket_auto_close=lambda *_args, **_kwargs: None,
        )
        assert result == {"ok": True}

    asyncio.run(run())

    session.refresh(ticket)
    assert ticket.draft_text == ""
    assert ticket.draft_mode == "reply"
    assert ticket.draft_revision == 11


def test_failed_internal_comment_keeps_ticket_draft():
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    ticket.status = TicketStatus.CLOSED.value
    ticket.draft_text = "comment draft"
    ticket.draft_mode = "comment"
    ticket.draft_revision = 10
    session.commit()

    async def run():
        with pytest.raises(HTTPException):
            await reply_to_ticket(
                request=_JsonRequest({"text": "Internal note", "quote_message_id": None}),
                db=session,
                user=SimpleNamespace(id="agent-1", role="agent"),
                ticket=ticket,
                message_type=MessageType.INTERNAL,
                send_to_telegram_client=lambda *_args, **_kwargs: None,
                telegram_message_id_from_response=lambda response: None,
                save_upload_attachment=lambda *_args, **_kwargs: None,
                send_attachment_to_telegram_client=lambda *_args, **_kwargs: None,
                claim_attachment_preload=lambda *_args, **_kwargs: None,
                schedule_attachment_delivery=lambda **_kwargs: None,
                unfreeze_ticket_on_message=lambda *_args, **_kwargs: None,
                remove_client_close_button=lambda *_args, **_kwargs: None,
                refresh_ticket_auto_close=lambda *_args, **_kwargs: None,
            )

    asyncio.run(run())

    session.refresh(ticket)
    assert ticket.draft_text == "comment draft"
    assert ticket.draft_mode == "comment"
    assert ticket.draft_revision == 10


def test_comment_route_uses_reply_to_ticket_internal_path_contract():
    repo_root = Path(__file__).resolve().parents[1]
    router_source = (repo_root / "api" / "app" / "routers" / "tickets.py").read_text(encoding="utf-8")
    comment_route = router_source[router_source.index('@router.post("/{ticket_id}/comment")'):]
    comment_route = comment_route[:comment_route.index('@router.post("/{ticket_id}/greeting")')]

    assert "return await reply_to_ticket(" in comment_route
    assert "message_type=MessageType.INTERNAL" in comment_route
    assert "comment_on_ticket(" not in comment_route


def test_ticket_draft_revision_uses_atomic_sql_update_contract():
    repo_root = Path(__file__).resolve().parents[1]
    router_source = (repo_root / "api" / "app" / "routers" / "tickets.py").read_text(encoding="utf-8")
    service_source = (repo_root / "api" / "app" / "services" / "ticket_messages.py").read_text(encoding="utf-8")

    assert "draft_revision=func.coalesce(Ticket.draft_revision, 0) + 1" in router_source
    assert "draft_revision=func.coalesce(Ticket.draft_revision, 0) + 1" in service_source
    assert "ticket.draft_revision = int(ticket.draft_revision or 0) + 1" not in router_source
    assert "ticket.draft_revision = int(ticket.draft_revision or 0) + 1" not in service_source


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


@pytest.mark.parametrize("channel", ["telegram", "web"])
def test_background_delivery_staging_failure_keeps_local_file(monkeypatch, tmp_path, channel):
    _engine, SessionFactory, session = _session_bundle()
    ticket = _seed_ticket(session)
    ticket.channel = channel
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


@pytest.mark.parametrize("channel", ["telegram", "web"])
def test_background_delivery_commit_failure_keeps_local_file_and_skips_unlink(monkeypatch, tmp_path, channel):
    _engine, SessionFactory, session = _session_bundle()
    ticket = _seed_ticket(session)
    ticket.channel = channel
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
    app_shell_version = "20261025-stats-final-v2"
    bot_buttons_editor_version = "20261023-auto-only"
    ticket_message_view_version = "20261021-all-save-feedback"
    ticket_static_version = "20261021-chat-file-input"
    ticket_attachments_template_version = "20261020-quotes"
    ticket_attachments_shell_version = "20261020-quotes"
    ticket_controls_version = "20261025-ticket-sidebar-v2"
    ticket_page_version = "20261021-task-panel-refresh"
    ticket_styles_version = "20261025-status-log-responsive-v2"
    composer = (repo_root / "api" / "app" / "static" / "ticket_composer.js").read_text(encoding="utf-8")
    template = (repo_root / "api" / "app" / "templates" / "ticket.html").read_text(encoding="utf-8")
    index_template = (repo_root / "api" / "app" / "templates" / "index.html").read_text(encoding="utf-8")
    app_shell = (repo_root / "api" / "app" / "static" / "app_shell.js").read_text(encoding="utf-8")
    ticket_js = (repo_root / "api" / "app" / "static" / "ticket.js").read_text(encoding="utf-8")
    template_sources = {
        path.name: path.read_text(encoding="utf-8")
        for path in (repo_root / "api" / "app" / "templates").glob("*.html")
    }
    app_shell_entrypoints = {
        name: source
        for name, source in template_sources.items()
        if "/static/app_shell.js?v=" in source
    }

    assert 'formData.append("single_attachment", "true")' in composer
    assert app_shell_entrypoints
    assert all(f"/static/app_shell.js?v={app_shell_version}" in source for source in app_shell_entrypoints.values())
    assert f"/static/bot_buttons_editor.js?v={bot_buttons_editor_version}" in template_sources["settings_bot_buttons.html"]
    assert f"/static/bot_buttons_editor.js?v={bot_buttons_editor_version}" in app_shell
    assert all("/static/app_shell.js?v=20260821b" not in source for source in app_shell_entrypoints.values())
    assert all("/static/app_shell.js?v=20260826a" not in source for source in app_shell_entrypoints.values())
    assert f"/static/ticket_message_view.js?v={ticket_message_view_version}" in template
    assert f"/static/ticket_message_view.js?v={ticket_message_view_version}" in app_shell
    assert f"/static/ticket_composer.js?v={ticket_static_version}" in template
    assert f"/static/ticket_composer.js?v={ticket_static_version}" in app_shell
    assert f"/static/ticket_attachments.js?v={ticket_attachments_template_version}" in template
    assert f"/static/ticket_attachments.js?v={ticket_attachments_shell_version}" in app_shell
    assert f"/static/ticket_controls.js?v={ticket_controls_version}" in template
    assert f"/static/ticket_controls.js?v={ticket_controls_version}" in app_shell
    assert f'/static/styles.css?v={ticket_styles_version}' in template
    assert f'/static/styles.css?v={ticket_styles_version}' in index_template
    assert all(f"/static/styles.css?v={ticket_styles_version}" in source
               for source in template_sources.values() if "/static/styles.css?v=" in source)
    assert f"/static/ticket.js?v={ticket_page_version}" in template
    assert f"/static/ticket.js?v={ticket_page_version}" in app_shell
    for source in app_shell_entrypoints.values():
        assert "/static/app_shell.js?v=20260811b" not in source
        assert "/static/ticket_composer.js?v=20260811d" not in source
        assert "/static/ticket.js?v=20260810a" not in source
    assert "/static/ticket.js?v=20260810a" not in app_shell
    assert "preloaded_attachment_id" in composer
    assert "sendByEnterCheckbox" in composer
    assert "e.isComposing" in composer
    assert '"/api/v1/users/me/preferences"' in ticket_js
    assert "updateSendByEnterPreference" in ticket_js
    assert "pendingCardReloadOptions" in ticket_js
    assert "await loadCard(pendingOptions)" in ticket_js
    assert 'formData.append("archive_group_id", archiveGroupId)' in composer
    assert "currentArchiveGroupId" in composer
    assert "new Map()" in composer
    assert "filePreloads: new Map()" in ticket_js
    assert "canUseAttachmentPreload" in ticket_js
    assert "workflowType: () => state.card?.workflow_type || \"ticket\"" in ticket_js
    assert '["reply", "comment"].includes(state.composeMode)' in ticket_js
    assert "AbortController" in composer


def test_attachment_cards_show_size_without_mime_and_download_sets_start_status():
    repo_root = Path(__file__).resolve().parents[1]
    attachments_source = (repo_root / "api" / "app" / "static" / "ticket_attachments.js").read_text(encoding="utf-8")

    attachment_meta_body = attachments_source.split("function attachmentMeta(attachment) {", 1)[1].split("}", 1)[0]
    download_body = attachments_source.split("async function downloadAttachment(ctx, attachment) {", 1)[1].split("async function renderTgsStickerInline", 1)[0]

    assert "attachment.mime_type" not in attachment_meta_body
    assert "formatFileSize(attachment.file_size)" in attachment_meta_body
    assert 'ctx.setStatus?.("Скачивание начато...")' in download_body


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


def test_comment_mode_keeps_add_files_button_available():
    repo_root = Path(__file__).resolve().parents[1]
    composer_path = repo_root / "api" / "app" / "static" / "ticket_composer.js"
    script = f"""
const fs = require("fs");
global.window = global;
eval(fs.readFileSync({json.dumps(str(composer_path))}, "utf8"));

const toggles = [];
const ctx = {{
  state: {{
    composeMode: "comment",
    card: {{ status: "pending" }},
  }},
  el: {{
    modeReplyBtn: {{ classList: {{ toggle() {{}} }} }},
    modeCommentBtn: {{ classList: {{ toggle() {{}} }} }},
    messageInput: {{ classList: {{ toggle() {{}} }} }},
    addFilesBtn: {{
      disabled: true,
      title: "",
      classList: {{ toggle(name, value) {{ toggles.push([name, value]); }} }},
    }},
  }},
}};

global.TicketComposer.applyComposeModeUi(ctx);
console.log(JSON.stringify({{
  disabled: ctx.el.addFilesBtn.disabled,
  title: ctx.el.addFilesBtn.title,
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

    assert payload["disabled"] is False
    assert payload["title"]


def test_comment_file_selection_uses_existing_attachment_preload_flow():
    repo_root = Path(__file__).resolve().parents[1]
    composer_path = repo_root / "api" / "app" / "static" / "ticket_composer.js"
    script = f"""
const fs = require("fs");
global.window = global;
global.crypto = {{ randomUUID: () => "archive-group-1" }};
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
const preloadCalls = [];
const sendCalls = [];
const file = {{ name: "comment.pdf", size: 3 }};
const ctx = {{
  state: {{
    ticketId: "ticket-1",
    selectedFiles: [],
    filePreloads: new Map(),
    currentArchiveGroupId: null,
    composeMode: "comment",
    role: "agent",
    card: {{ workflow_type: "ticket", status: "pending" }},
    quoteMessageId: null,
    isSendingReply: false,
    pendingCloseAfterSend: false,
  }},
  el: {{
    replyFilesInput: {{ value: "" }},
    selectedFilesList: null,
    messageInput: {{ value: "internal note" }},
  }},
  canUseAttachmentPreload: () => true,
  workflowType: () => "ticket",
  api: (url, options = {{}}) => {{
    const entries = Array.from(options.body.entries());
    if (url.endsWith("/attachment-preloads")) {{
      preloadCalls.push(entries);
      return Promise.resolve({{ id: "preload-ready", status: "ready" }});
    }}
    if (url.endsWith("/comment")) {{
      sendCalls.push(entries);
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

global.TicketComposer.queueSelectedFiles(ctx, [file]);
await Promise.resolve();
await Promise.resolve();
await global.TicketComposer.sendByMode(ctx);
console.log(JSON.stringify({{ preloadCalls, sendCalls }}));
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

    assert len(payload["preloadCalls"]) == 1
    archive_group_entry = next(entry for entry in payload["preloadCalls"][0] if entry[0] == "archive_group_id")
    assert archive_group_entry[1]
    assert len(payload["sendCalls"]) == 1
    assert ["text", "internal note"] in payload["sendCalls"][0]
    assert ["preloaded_attachment_id", "preload-ready"] in payload["sendCalls"][0]


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


def test_removed_browser_uploading_preload_survives_restore_race_until_late_post_delete():
    repo_root = Path(__file__).resolve().parents[1]
    composer_path = repo_root / "api" / "app" / "static" / "ticket_composer.js"
    script = f"""
const fs = require("fs");
global.window = global;
global.crypto = {{ randomUUID: () => "archive-group-1" }};
global.TicketApi = {{ formatFileSize: (value) => String(value) }};
global.FormData = class FakeFormData {{
  append() {{}}
}};
global.AbortController = class FakeAbortController {{
  constructor() {{
    this.aborted = false;
    this.signal = {{}};
  }}
  abort() {{
    this.aborted = true;
  }}
}};
global.document = {{
  createElement() {{
    return {{
      className: "",
      textContent: "",
      append() {{}},
      appendChild(child) {{ return child; }},
      addEventListener() {{}},
      classList: {{ add() {{}}, remove() {{}}, toggle() {{}} }},
    }};
  }},
}};
global.setInterval = () => 1;
global.clearInterval = () => {{}};
eval(fs.readFileSync({json.dumps(str(composer_path))}, "utf8"));

(async () => {{
let resolvePost;
const file = {{ name: "late.pdf", size: 3 }};
const otherFile = {{ name: "keep.pdf", size: 5, __attachmentPreloadVirtual: true, __attachmentPreloadId: "preload-keep" }};
const calls = [];
const errors = [];
const ctx = {{
  state: {{
    ticketId: "ticket-1",
    selectedFiles: [otherFile],
    filePreloads: new Map([[otherFile, {{
      preloadId: "preload-keep",
      status: "ready",
      stage: "ready",
      controller: null,
      pollTimer: null,
      error: null,
      removed: false,
      restored: true,
    }}]]),
    currentArchiveGroupId: null,
  }},
  el: {{
    replyFilesInput: {{ value: "has-value" }},
    selectedFilesList: null,
  }},
  canUseAttachmentPreload: () => true,
  showError: (message) => errors.push(message),
  api: (url, options = {{}}) => {{
    calls.push([url, options.method || "POST"]);
    if ((options.method || "POST") === "POST") {{
      return new Promise((resolve) => {{ resolvePost = resolve; }});
    }}
    return Promise.resolve({{ ok: true }});
  }},
}};

global.TicketComposer.queueSelectedFiles(ctx, [file]);
await Promise.resolve();
const preloadBeforeRemove = ctx.state.filePreloads.get(file);
await global.TicketComposer.removeSelectedFile(ctx, file);

global.TicketComposer.syncRestoredPreloads(ctx, [
  {{ id: "preload-late", original_filename: "late.pdf", file_size: 3, status: "uploading" }},
  {{ id: "preload-keep", original_filename: "keep.pdf", file_size: 5, status: "ready" }},
]);
const afterRestoreNames = ctx.state.selectedFiles.map((item) => item.name);
const removedStateStillPending = ctx.state.filePreloads.has(file) && ctx.state.filePreloads.get(file).removed === true;

resolvePost({{ id: "preload-late", status: "uploading" }});
await Promise.resolve();
await Promise.resolve();
await Promise.resolve();

console.log(JSON.stringify({{
  selectedNames: ctx.state.selectedFiles.map((item) => item.name),
  afterRestoreNames,
  removedStateStillPending,
  hasPreloadState: ctx.state.filePreloads.has(file),
  otherPreloadId: ctx.state.filePreloads.get(otherFile)?.preloadId || null,
  aborted: preloadBeforeRemove.controller?.aborted || false,
  calls,
  errors,
  inputValue: ctx.el.replyFilesInput.value,
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

    assert payload["afterRestoreNames"] == ["keep.pdf"]
    assert payload["selectedNames"] == ["keep.pdf"]
    assert payload["removedStateStillPending"] is True
    assert payload["hasPreloadState"] is False
    assert payload["otherPreloadId"] == "preload-keep"
    assert payload["aborted"] is False
    assert payload["calls"] == [
        ["/api/v1/tickets/ticket-1/attachment-preloads", "POST"],
        ["/api/v1/tickets/ticket-1/attachment-preloads/preload-late", "DELETE"],
    ]
    assert payload["errors"] == []
    assert payload["inputValue"] == ""


def test_remove_preload_delete_failure_uses_existing_error_surface():
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
      append() {{}},
      appendChild(child) {{ return child; }},
      addEventListener() {{}},
      classList: {{ add() {{}}, remove() {{}}, toggle() {{}} }},
    }};
  }},
}};
global.clearInterval = () => {{}};
eval(fs.readFileSync({json.dumps(str(composer_path))}, "utf8"));

(async () => {{
const file = {{ name: "ready.pdf", size: 3 }};
const errors = [];
const ctx = {{
  state: {{
    ticketId: "ticket-1",
    selectedFiles: [file],
    filePreloads: new Map([[file, {{ preloadId: "preload-ready", status: "ready", stage: "ready" }}]]),
  }},
  el: {{
    replyFilesInput: {{ value: "has-value" }},
    selectedFilesList: null,
  }},
  canUseAttachmentPreload: () => true,
  showError: (message) => errors.push(message),
  api: () => Promise.reject(new Error("server delete failed")),
}};

await global.TicketComposer.removeSelectedFile(ctx, file);
await Promise.resolve();
console.log(JSON.stringify({{
  selectedCount: ctx.state.selectedFiles.length,
  errors,
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

    assert payload["selectedCount"] == 0
    assert payload["errors"] == ["server delete failed"]


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


def test_task_transport_selector_accepts_uploading_preload_without_waiting_for_telegram():
    repo_root = Path(__file__).resolve().parents[1]
    composer_path = repo_root / "api" / "app" / "static" / "ticket_composer.js"
    ticket_js = (repo_root / "api" / "app" / "static" / "ticket.js").read_text(encoding="utf-8")
    script = f"""
const fs = require("fs");
global.window = global;
global.TicketApi = {{ formatFileSize: (value) => String(value) }};
eval(fs.readFileSync({json.dumps(str(composer_path))}, "utf8"));
const file = {{ name: "report.pdf", size: 3 }};
const productionTaskCard = {{
  id: "task-1",
  ticket_number: 742,
  subject: "Task from Telegram",
  workflow_type: "task",
  task_kind: "normal",
  status: "pending",
  active_attachment_preloads: [],
  messages: [],
}};
const makeCtx = (preload) => ({{
  state: {{
    composeMode: "reply",
    card: productionTaskCard,
    filePreloads: new Map([[file, preload]]),
  }},
  workflowType: () => productionTaskCard.workflow_type,
  canUseAttachmentPreload: () => true,
}});
const canUseAttachmentPreload = ["ticket", "task"].includes(productionTaskCard.workflow_type || "ticket");
const uploading = global.TicketComposer.resolveAttachmentTransport(makeCtx({{
  preloadId: "preload-uploading",
  status: "uploading",
  stage: "uploading",
}}), file);
const ready = global.TicketComposer.resolveAttachmentTransport(makeCtx({{
  preloadId: "preload-ready",
  status: "ready",
  stage: "ready",
}}), file);
const helperWorkflow = global.TicketComposer.workflowType(makeCtx(null));
const helperIsTaskReply = global.TicketComposer.isTaskReply(makeCtx(null));
console.log(JSON.stringify({{ canUseAttachmentPreload, helperWorkflow, helperIsTaskReply, uploading, ready }}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        check=True,
        cwd=repo_root,
    )
    payload = json.loads(result.stdout)

    assert 'workflowType: () => state.card?.workflow_type || "ticket"' in ticket_js
    assert payload["canUseAttachmentPreload"] is True
    assert payload["helperWorkflow"] == "task"
    assert payload["helperIsTaskReply"] is True
    assert payload["uploading"] == {
        "mode": "preloaded",
        "preloadId": "preload-uploading",
        "preloadStatus": "uploading",
    }
    assert payload["ready"] == {
        "mode": "preloaded",
        "preloadId": "preload-ready",
        "preloadStatus": "ready",
    }


def test_task_reply_form_data_groups_multiple_ready_preloads_in_one_request():
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
    this._entries.push([key, filename || value]);
  }}
  entries() {{
    return this._entries[Symbol.iterator]();
  }}
}};
eval(fs.readFileSync({json.dumps(str(composer_path))}, "utf8"));
const fileA = {{ name: "a.pdf", size: 3 }};
const fileB = {{ name: "b.pdf", size: 4 }};
const productionTaskCard = {{
  id: "task-1",
  ticket_number: 742,
  subject: "Task from Telegram",
  workflow_type: "task",
  task_kind: "normal",
  status: "pending",
  active_attachment_preloads: [
    {{ id: "preload-a", original_filename: "a.pdf", file_size: 3, status: "ready" }},
    {{ id: "preload-b", original_filename: "b.pdf", file_size: 4, status: "ready" }},
  ],
  messages: [],
}};
const ctx = {{
  state: {{
    composeMode: "reply",
    card: productionTaskCard,
    filePreloads: new Map([
      [fileA, {{ preloadId: "preload-a", status: "ready", stage: "ready" }}],
      [fileB, {{ preloadId: "preload-b", status: "ready", stage: "ready" }}],
    ]),
  }},
  workflowType: () => productionTaskCard.workflow_type,
  canUseAttachmentPreload: () => true,
}};
const form = global.TicketComposer.buildTaskReplyFormData(ctx, [fileA, fileB], "Answer", null);
console.log(JSON.stringify(Array.from(form.entries())));
"""
    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        check=True,
        cwd=repo_root,
    )
    payload = json.loads(result.stdout)

    assert payload == [
        ["text", "Answer"],
        ["preloaded_attachment_id", "preload-a"],
        ["preloaded_attachment_id", "preload-b"],
    ]


def test_task_send_by_mode_with_production_card_groups_four_ready_preloads_in_one_reply_request():
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
    this._entries.push([key, filename || value]);
  }}
  entries() {{
    return this._entries[Symbol.iterator]();
  }}
}};
global.document = {{
  createElement() {{
    return {{
      className: "",
      textContent: "",
      append() {{}},
      appendChild() {{}},
      addEventListener() {{}},
      classList: {{ add() {{}}, remove() {{}}, toggle() {{}} }},
    }};
  }},
}};
eval(fs.readFileSync({json.dumps(str(composer_path))}, "utf8"));

(async () => {{
const files = [
  {{ name: "MISSION_CHECKLIST_DRAFT_v21.txt", size: 11 }},
  {{ name: "run_e2e_trace.log", size: 12 }},
  {{ name: "run_v6_integrated.py", size: 13 }},
  {{ name: "task_job.json", size: 14 }},
];
const productionTaskCard = {{
  id: "task-1",
  ticket_number: 742,
  subject: "Task from Telegram",
  workflow_type: "task",
  task_kind: "normal",
  status: "pending",
  active_attachment_preloads: files.map((file, index) => ({{
    id: `preload-${{index + 1}}`,
    original_filename: file.name,
    file_size: file.size,
    status: "ready",
  }})),
  messages: [],
}};
const preloadEntries = files.map((file, index) => [
  file,
  {{ preloadId: `preload-${{index + 1}}`, status: "ready", stage: "ready" }},
]);
const replyCalls = [];
const statuses = [];
let loadCardCalls = 0;
const ctx = {{
  state: {{
    ticketId: productionTaskCard.id,
    selectedFiles: files.slice(),
    filePreloads: new Map(preloadEntries),
    currentArchiveGroupId: "archive-group-1",
    composeMode: "reply",
    role: "agent",
    card: productionTaskCard,
    quoteMessageId: null,
    isSendingReply: false,
    pendingCloseAfterSend: false,
  }},
  el: {{
    replyFilesInput: {{ value: "" }},
    selectedFilesList: null,
    messageInput: {{ value: "task answer" }},
  }},
  workflowType: () => productionTaskCard.workflow_type,
  canUseAttachmentPreload: () => true,
  api: (url, options = {{}}) => {{
    if (url.endsWith("/reply")) {{
      replyCalls.push(Array.from(options.body.entries()));
      return Promise.resolve({{ ok: true }});
    }}
    throw new Error(`Unexpected URL: ${{url}}`);
  }},
  loadCard: async () => {{ loadCardCalls += 1; }},
  setStatus: (value) => statuses.push(value),
  showError: (message) => {{ throw new Error(message); }},
  isOnsitePhraseJs: () => false,
  renderAvailabilityStatus: () => {{}},
  action: async () => {{}},
}};

await global.TicketComposer.sendByMode(ctx);
console.log(JSON.stringify({{
  replyCalls,
  loadCardCalls,
  selectedFilesCount: ctx.state.selectedFiles.length,
  currentArchiveGroupId: ctx.state.currentArchiveGroupId,
  messageInput: ctx.el.messageInput.value,
  finalStatus: statuses[statuses.length - 1],
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

    assert len(payload["replyCalls"]) == 1
    assert payload["replyCalls"][0] == [
        ["text", "task answer"],
        ["preloaded_attachment_id", "preload-1"],
        ["preloaded_attachment_id", "preload-2"],
        ["preloaded_attachment_id", "preload-3"],
        ["preloaded_attachment_id", "preload-4"],
    ]
    assert payload["loadCardCalls"] == 1
    assert payload["selectedFilesCount"] == 0
    assert payload["currentArchiveGroupId"] is None
    assert payload["messageInput"] == ""
    assert payload["finalStatus"]


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


def test_task_preload_post_saves_before_close_without_touching_ticket_state(tmp_path):
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    ticket.workflow_type = TicketWorkflowType.TASK.value
    session.commit()
    original_updated_at = ticket.updated_at
    background_tasks = BackgroundTasks()

    result = asyncio.run(
        create_attachment_preload(
            ticket_id=ticket.id,
            background_tasks=background_tasks,
            file=_upload("task report.pdf", b"pdf"),
            archive_group_id="task-group-1",
            db=session,
            user=SimpleNamespace(id="admin-1", role="admin"),
        )
    )

    preloads = session.execute(select(outbound_service.TicketAttachmentPreload)).scalars().all()
    session.refresh(ticket)

    assert result["status"] == "uploading"
    assert len(preloads) == 1
    assert Path(preloads[0].local_path).exists()
    assert preloads[0].ticket_id == ticket.id
    assert preloads[0].archive_group_id == "task-group-1"
    assert _messages(session, ticket.id) == []
    assert ticket.updated_at == original_updated_at
    assert len(background_tasks.tasks) == 1


@pytest.mark.parametrize("preload_status", ["ready", "uploading"])
def test_task_reply_claims_ready_preload_without_client_delivery_or_background_schedule(tmp_path, preload_status):
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    ticket.workflow_type = TicketWorkflowType.TASK.value
    path = tmp_path / "task-report.pdf"
    path.write_bytes(b"pdf")
    preload = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="agent-1",
        original_filename="task-report.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=str(path),
        telegram_file_id="ready-file-id",
        telegram_file_unique_id="ready-unique-id",
        status=preload_status,
        created_at=datetime.utcnow(),
    )
    session.add(preload)
    session.commit()

    sent_to_client = []
    scheduled = []

    async def run():
        result = await reply_to_ticket(
            request=_multipart_request_with_preload(text="Task answer", preload_id=preload.id),
            db=session,
            user=SimpleNamespace(id="agent-1", role="agent"),
            ticket=ticket,
            send_to_telegram_client=lambda *_args, **_kwargs: sent_to_client.append("text"),
            telegram_message_id_from_response=lambda response: None,
            save_upload_attachment=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("raw upload should not be used")),
            send_attachment_to_telegram_client=lambda *_args, **_kwargs: sent_to_client.append("file"),
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

    assert sent_to_client == []
    assert scheduled == []
    assert messages[0].body == "Task answer"
    assert attachments[0].telegram_file_id == "ready-file-id"
    assert attachments[0].telegram_delivery_status == ("uploading" if preload_status == "uploading" else None)
    assert claimed_preload.final_attachment_id == attachments[0].id


def test_task_reply_claims_multiple_ready_preloads_into_one_agent_message(tmp_path):
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    ticket.workflow_type = TicketWorkflowType.TASK.value
    preloads = []
    for filename, file_id in [
        ("MISSION_CHECKLIST_DRAFT_v21.txt", "file-a"),
        ("run_e2e_trace.log", "file-b"),
        ("run_v6_integrated.py", "file-c"),
        ("task_job.json", "file-d"),
    ]:
        path = tmp_path / filename
        path.write_bytes(b"pdf")
        preload = outbound_service.TicketAttachmentPreload(
            id=str(uuid.uuid4()),
            ticket_id=ticket.id,
            user_id="agent-1",
            original_filename=filename,
            mime_type="application/pdf",
            file_size=3,
            local_path=str(path),
            telegram_file_id=file_id,
            status="ready",
            created_at=datetime.utcnow(),
        )
        preloads.append(preload)
    session.add_all(preloads)
    session.commit()

    sent_to_client = []
    scheduled = []
    async def run():
        result = await reply_to_ticket(
            request=_MultipartRequest(_FormData(text="выввыв", files=[], preloaded_attachment_ids=[preload.id for preload in preloads])),
            db=session,
            user=SimpleNamespace(id="agent-1", role="agent"),
            ticket=ticket,
            send_to_telegram_client=lambda *_args, **_kwargs: sent_to_client.append("text"),
            telegram_message_id_from_response=lambda response: None,
            save_upload_attachment=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("raw upload should not be used")),
            send_attachment_to_telegram_client=lambda *_args, **_kwargs: sent_to_client.append("file"),
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
    assert len(messages) == 1
    assert messages[0].body == "выввыв"
    assert [attachment.original_filename for attachment in attachments] == [
        "MISSION_CHECKLIST_DRAFT_v21.txt",
        "run_e2e_trace.log",
        "run_v6_integrated.py",
        "task_job.json",
    ]
    assert sent_to_client == []
    assert scheduled == []
    assert all(attachment.telegram_delivery_status is None for attachment in attachments)


def _save_async_task_reply(factory, ticket_id, preload_ids):
    def forbidden(*args, **kwargs):
        raise AssertionError("Task preparation must not deliver to the client")

    with factory() as db:
        asyncio.run(reply_to_ticket(
            request=_MultipartRequest(_FormData(text="Task answer", files=[], preloaded_attachment_ids=preload_ids)),
            db=db, user=SimpleNamespace(id="agent-1", role="agent"), ticket=db.get(Ticket, ticket_id),
            send_to_telegram_client=forbidden, telegram_message_id_from_response=lambda response: None,
            save_upload_attachment=forbidden, send_attachment_to_telegram_client=forbidden,
            claim_attachment_preload=lambda db, key, ticket_key, user_key: outbound_service.claim_attachment_preload(
                db, preload_id=key, ticket_id=ticket_key, user_id=user_key),
            schedule_attachment_delivery=forbidden, unfreeze_ticket_on_message=lambda *args: None,
            remove_client_close_button=lambda *args: None, refresh_ticket_auto_close=lambda *args: None,
        ))


@pytest.mark.parametrize("channel", ["telegram", "web"])
@pytest.mark.parametrize("claim_at", ["before", "during", "after"])
def test_task_async_preloads_keep_one_reply_across_staging_race(monkeypatch, tmp_path, channel, claim_at):
    engine, factory, db = _threaded_session_bundle(tmp_path)
    ticket = _seed_ticket(db)
    ticket.workflow_type = "task"
    ticket.channel = channel
    ticket.assignee_id = "agent-1"
    db.add(User(id="agent-1", company_id=ticket.company_id, role="agent", full_name="Agent"))
    keys = []
    for index in range(2):
        path = tmp_path / f"task-{index}.pdf"
        path.write_bytes(b"pdf")
        key = str(uuid.uuid4())
        keys.append(key)
        db.add(outbound_service.TicketAttachmentPreload(id=key, ticket_id=ticket.id, user_id="agent-1",
            original_filename=path.name, mime_type="application/pdf", local_path=str(path), file_size=3,
            status="uploading", archive_group_id="task-group", telegram_staging_info_message_id="card"))
    db.commit()
    ticket_id = ticket.id
    db.close()
    monkeypatch.setattr(outbound_service, "SessionLocal", factory)
    archive_updates = []
    monkeypatch.setattr(outbound_service, "_best_effort_edit_group_archive_message", lambda **kwargs: archive_updates.append(kwargs["status_text"]))
    deliveries = []
    monkeypatch.setattr(outbound_service, "deliver_ticket_attachment", lambda **kwargs: deliveries.append(kwargs))
    claimed = False

    def claim():
        nonlocal claimed
        _save_async_task_reply(factory, ticket_id, keys)
        claimed = True

    def stage(*, preload):
        if claim_at == "during" and not claimed:
            claim()
        return "file-" + preload.id, "unique-" + preload.id, "archive-" + preload.id

    monkeypatch.setattr(outbound_service, "_send_staging_document", stage)
    if claim_at == "before":
        claim()
        with factory() as check:
            assert {row.telegram_delivery_status for row in check.execute(select(TicketAttachment)).scalars()} == {"uploading"}
            card = get_ticket(ticket_id, db=check, user=SimpleNamespace(id="agent-1", role="agent"))
            assert {row.telegram_delivery_status for row in card.messages[0].attachments} == {"uploading"}
    for key in keys:
        outbound_service.deliver_attachment_preload(preload_id=key)
    if claim_at == "after":
        claim()
    # Repeated workers must never turn preparation into client delivery.
    for key in keys:
        outbound_service.deliver_attachment_preload(preload_id=key)
    with factory() as check:
        messages = _messages(check, ticket_id)
        assert len(messages) == 1 and messages[0].body == "Task answer"
        attachments = _attachments(check, messages[0].id)
        assert len(attachments) == 2
        for key in keys:
            preload = check.get(outbound_service.TicketAttachmentPreload, key)
            attachment = check.get(TicketAttachment, preload.final_attachment_id)
            assert attachment.message_id == messages[0].id
            assert preload.status == "ready"
            assert attachment.telegram_delivery_status in {None, "ready"}
            assert attachment.telegram_file_id == "file-" + key
            assert attachment.telegram_archive_message_id == "archive-" + key
        card = get_ticket(ticket_id, db=check, user=SimpleNamespace(id="agent-1", role="agent"))
        assert len(card.messages[0].attachments) == 2
        assert {row.telegram_delivery_status for row in card.messages[0].attachments} <= {None, "ready"}
    assert deliveries == []
    assert archive_updates[-1] == "готово к отправке"
    engine.dispose()


@pytest.mark.parametrize("failure", ["info", "missing", "telegram"])
@pytest.mark.parametrize("claim_at", ["before", "during"])
def test_task_async_preload_failure_preserves_claim_and_failed_status(monkeypatch, tmp_path, failure, claim_at):
    engine, factory, db = _threaded_session_bundle(tmp_path)
    ticket = _seed_ticket(db)
    ticket.workflow_type = "task"
    ticket.assignee_id = "agent-1"
    path = tmp_path / "failed.pdf"
    path.write_bytes(b"pdf")
    key = str(uuid.uuid4())
    db.add(outbound_service.TicketAttachmentPreload(id=key, ticket_id=ticket.id, user_id="agent-1",
        original_filename=path.name, mime_type="application/pdf", local_path=str(path), file_size=3,
        status="uploading", archive_group_id="task-group",
        telegram_staging_info_message_id=None if failure == "info" else "card"))
    db.commit()
    ticket_id = ticket.id
    db.close()
    monkeypatch.setattr(outbound_service, "SessionLocal", factory)

    def fail(**kwargs):
        if claim_at == "during":
            _save_async_task_reply(factory, ticket_id, [key])
        if failure == "missing":
            raise FileNotFoundError("missing")
        raise outbound_service.TelegramApiError("staging failed")

    monkeypatch.setattr(outbound_service, "_ensure_group_archive_info_message", fail)
    monkeypatch.setattr(outbound_service, "_send_staging_document", fail)
    if claim_at == "before":
        _save_async_task_reply(factory, ticket_id, [key])
    outbound_service.deliver_attachment_preload(preload_id=key)
    outbound_service.deliver_attachment_preload(preload_id=key)
    with factory() as check:
        preload = check.get(outbound_service.TicketAttachmentPreload, key)
        assert preload.status == "failed"
        attachment = check.get(TicketAttachment, preload.final_attachment_id)
        assert attachment.telegram_delivery_status == "failed"
        assert check.get(TicketMessage, attachment.message_id).body == "Task answer"
        card = get_ticket(ticket_id, db=check, user=SimpleNamespace(id="agent-1", role="agent"))
        assert card.messages[0].attachments[0].telegram_delivery_status == "failed"
        # A failed staged file must not be sent by the existing close-time sender.
        from api.app.services.ticket_attachments import _send_attachment_to_telegram_client
        assert _send_attachment_to_telegram_client(check, check.get(Ticket, ticket_id), attachment) is None
    engine.dispose()


def test_plain_ticket_onsite_reply_records_agent_on_ticket_status_history():
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    agent = User(
        id="agent-1",
        company_id=ticket.company_id,
        email="agent-1@example.test",
        login="agent-1",
        full_name="Agent One",
        role="agent",
        is_active=True,
        availability_status="waiting",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    old_started_at = datetime(2026, 9, 11, 9, 0, 0)
    session.add_all([
        agent,
        StatsUserStatusInterval(
            id=str(uuid.uuid4()),
            company_id=ticket.company_id,
            user_id=agent.id,
            status="waiting",
            started_at=old_started_at,
            ended_at=None,
            created_at=old_started_at,
        ),
    ])
    session.commit()

    async def run():
        result = await reply_to_ticket(
            request=_JsonRequest({"text": "иду к вам"}),
            db=session,
            user=SimpleNamespace(id=agent.id, role="agent"),
            ticket=ticket,
            send_to_telegram_client=lambda *_args, **_kwargs: {"ok": True, "result": {"message_id": 42}},
            telegram_message_id_from_response=lambda response: "42",
            save_upload_attachment=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("upload should not be used")),
            send_attachment_to_telegram_client=lambda *_args, **_kwargs: None,
            claim_attachment_preload=lambda *_args, **_kwargs: None,
            schedule_attachment_delivery=lambda **_kwargs: None,
            unfreeze_ticket_on_message=lambda *_args, **_kwargs: None,
            remove_client_close_button=lambda *_args, **_kwargs: None,
            refresh_ticket_auto_close=lambda *_args, **_kwargs: None,
        )
        assert result == {"ok": True}

    asyncio.run(run())

    rows = session.query(StatsUserStatusInterval).filter_by(user_id=agent.id).order_by(StatsUserStatusInterval.started_at).all()
    session.refresh(agent)
    assert agent.availability_status == "on_ticket"
    assert rows[0].status == "waiting"
    assert rows[0].ended_at is not None
    assert rows[1].status == "on_ticket"
    assert rows[1].started_at == rows[0].ended_at
    assert rows[1].ended_at is None


def test_plain_ticket_text_reply_persists_card_message_and_starts_auto_close_timer():
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    ticket.status = TicketStatus.NEW.value
    ticket.assignee_id = "agent-1"
    session.commit()
    sent_messages = []

    async def run():
        result = await reply_to_ticket(
            request=_JsonRequest({"text": "Plain answer", "quote_message_id": None}),
            db=session,
            user=SimpleNamespace(id="agent-1", role="agent"),
            ticket=ticket,
            send_to_telegram_client=lambda *_args, **_kwargs: sent_messages.append(_kwargs) or {"result": {"message_id": 901}},
            telegram_message_id_from_response=lambda response: str(response["result"]["message_id"]),
            save_upload_attachment=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("upload should not be used")),
            send_attachment_to_telegram_client=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("file send should not be used")),
            claim_attachment_preload=lambda *_args, **_kwargs: None,
            schedule_attachment_delivery=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("background delivery should not be used")),
            unfreeze_ticket_on_message=lambda current_ticket: setattr(current_ticket, "is_frozen", False),
            remove_client_close_button=lambda *_args, **_kwargs: None,
            refresh_ticket_auto_close=lambda db, current_ticket, restart_from_now: refresh_ticket_auto_close_timer(db, current_ticket, restart_from_now),
        )
        assert result == {"ok": True}

    asyncio.run(run())
    session.refresh(ticket)
    messages = _messages(session, ticket.id)
    card = get_ticket(ticket.id, db=session, user=SimpleNamespace(id="agent-1", role="agent"))
    agent_messages = [message for message in card.messages if message.message_type == MessageType.AGENT.value]

    assert len(sent_messages) == 1
    assert ticket.status == TicketStatus.PENDING.value
    assert ticket.auto_close_started_at is not None
    assert ticket.auto_close_due_at is not None
    assert len(messages) == 1
    assert messages[0].message_type == MessageType.AGENT.value
    assert messages[0].body == "Plain answer"
    assert messages[0].telegram_message_id == "901"
    assert len(agent_messages) == 1
    assert agent_messages[0].body == "Plain answer"


async def _send_quick_action_reply(session, ticket, payload, *, sent_messages):
    return await reply_to_ticket(
        request=_JsonRequest(payload),
        db=session,
        user=SimpleNamespace(id="agent-1", role="agent"),
        ticket=ticket,
        send_to_telegram_client=lambda *_args, **_kwargs: sent_messages.append((_args, _kwargs)) or {"result": {"message_id": 901}},
        telegram_message_id_from_response=lambda response: str(response["result"]["message_id"]),
        save_upload_attachment=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("upload should not be used")),
        send_attachment_to_telegram_client=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("file send should not be used")),
        claim_attachment_preload=lambda *_args, **_kwargs: None,
        schedule_attachment_delivery=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("background delivery should not be used")),
        unfreeze_ticket_on_message=lambda current_ticket: setattr(current_ticket, "is_frozen", False),
        remove_client_close_button=lambda *_args, **_kwargs: None,
        refresh_ticket_auto_close=lambda db, current_ticket, restart_from_now: refresh_ticket_auto_close_timer(db, current_ticket, restart_from_now),
    )


def test_quick_clarification_requires_greeting_and_does_not_send_on_failure():
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    sent_messages = []

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_send_quick_action_reply(
            session,
            ticket,
            {
                "text": "Могли бы Вы, пожалуйста, уточнить, что именно произошло?",
                "quick_action": "clarification",
            },
            sent_messages=sent_messages,
        ))

    assert exc.value.detail["error"]["code"] == "quick_action_greeting_required"
    assert sent_messages == []
    assert _messages(session, ticket.id) == []
    session.refresh(ticket)
    assert not ticket.quick_action_state_json


def test_quick_clarification_marks_action_only_after_success_and_repeat_is_rejected():
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    ticket.quick_action_state_json = {"greeting": True}
    session.commit()
    sent_messages = []
    payload = {
        "text": "Могли бы Вы, пожалуйста, уточнить, что именно произошло?",
        "quick_action": "clarification",
    }

    result = asyncio.run(_send_quick_action_reply(session, ticket, payload, sent_messages=sent_messages))

    assert result == {"ok": True}
    session.refresh(ticket)
    assert ticket.quick_action_state_json == {"greeting": True, "clarification": True}
    assert [message.body for message in _messages(session, ticket.id)] == [payload["text"]]
    assert len(sent_messages) == 1

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_send_quick_action_reply(session, ticket, payload, sent_messages=sent_messages))

    assert exc.value.detail["error"]["code"] == "quick_action_already_used"
    assert len(sent_messages) == 1
    assert len(_messages(session, ticket.id)) == 1


def test_quick_clarification_preserves_saved_draft():
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    ticket.quick_action_state_json = {"greeting": True}
    ticket.draft_text = "Набранный черновик"
    ticket.draft_mode = "comment"
    ticket.draft_revision = 7
    session.commit()
    sent_messages = []

    result = asyncio.run(_send_quick_action_reply(
        session,
        ticket,
        {
            "text": "Могли бы Вы, пожалуйста, уточнить, что именно произошло?",
            "quick_action": "clarification",
        },
        sent_messages=sent_messages,
    ))

    assert result == {"ok": True}
    session.refresh(ticket)
    assert ticket.draft_text == "Набранный черновик"
    assert ticket.draft_mode == "comment"
    assert ticket.draft_revision == 7


def test_quick_clarification_rechecks_used_state_from_database_before_sending():
    _engine, Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    ticket.quick_action_state_json = {"greeting": True}
    session.commit()
    session2 = Session()
    session2_ticket = session2.get(Ticket, ticket.id)
    session2_ticket.quick_action_state_json = {"greeting": True, "clarification": True}
    session2.commit()
    sent_messages = []

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_send_quick_action_reply(
            session,
            ticket,
            {
                "text": "Могли бы Вы, пожалуйста, уточнить, что именно произошло?",
                "quick_action": "clarification",
            },
            sent_messages=sent_messages,
        ))

    assert exc.value.detail["error"]["code"] == "quick_action_already_used"
    assert sent_messages == []
    assert _messages(session, ticket.id) == []


def test_manual_onsite_text_triggers_onsite_without_spending_quick_button():
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    sent_messages = []

    result = asyncio.run(_send_quick_action_reply(
        session,
        ticket,
        {"text": "иду к вам", "quote_message_id": None},
        sent_messages=sent_messages,
    ))

    assert result == {"ok": True}
    session.refresh(ticket)
    assert ticket.onsite_triggered is True
    assert not ticket.quick_action_state_json


def test_restore_resets_onsite_and_new_onsite_text_can_trigger_again(monkeypatch):
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    ticket.assignee_id = "agent-1"
    ticket.status = TicketStatus.CLOSED.value
    ticket.closed_by_user_id = "agent-1"
    ticket.closed_at = datetime.utcnow()
    ticket.onsite_triggered = True
    session.commit()
    monkeypatch.setattr(tickets_router, "_send_to_telegram_client", lambda *_args, **_kwargs: {"ok": True})

    result = tickets_router.restore(ticket.id, db=session, user=SimpleNamespace(id="admin-1", role="admin"))

    assert result == {"ok": True}
    session.refresh(ticket)
    assert ticket.onsite_triggered is False

    sent_messages = []
    reply_result = asyncio.run(_send_quick_action_reply(
        session,
        ticket,
        {"text": "Иду к Вам", "quote_message_id": None},
        sent_messages=sent_messages,
    ))

    assert reply_result == {"ok": True}
    session.refresh(ticket)
    assert ticket.onsite_triggered is True


def test_plain_ticket_file_reply_persists_card_attachment_and_starts_auto_close_timer(tmp_path):
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    ticket.status = TicketStatus.NEW.value
    ticket.assignee_id = "agent-1"
    path = tmp_path / "ready.pdf"
    path.write_bytes(b"pdf")
    preload = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="agent-1",
        original_filename="ready.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=str(path),
        telegram_file_id="ready-file-id",
        status="ready",
        created_at=datetime.utcnow(),
    )
    session.add(preload)
    session.commit()
    scheduled = []

    async def run():
        result = await reply_to_ticket(
            request=_multipart_request_with_preload(text="Plain answer with file", preload_id=preload.id, single_attachment=True),
            db=session,
            user=SimpleNamespace(id="agent-1", role="agent"),
            ticket=ticket,
            send_to_telegram_client=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("caption preload should schedule delivery")),
            telegram_message_id_from_response=lambda response: None,
            save_upload_attachment=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("raw upload should not be used")),
            send_attachment_to_telegram_client=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("immediate file send should not be used")),
            claim_attachment_preload=lambda db, preload_id, expected_ticket_id, expected_user_id: outbound_service.claim_attachment_preload(
                db,
                preload_id=preload_id,
                ticket_id=expected_ticket_id,
                user_id=expected_user_id,
            ),
            schedule_attachment_delivery=lambda **kwargs: scheduled.append(kwargs),
            unfreeze_ticket_on_message=lambda current_ticket: setattr(current_ticket, "is_frozen", False),
            remove_client_close_button=lambda *_args, **_kwargs: None,
            refresh_ticket_auto_close=lambda db, current_ticket, restart_from_now: refresh_ticket_auto_close_timer(db, current_ticket, restart_from_now),
        )
        assert result == {"ok": True}

    asyncio.run(run())
    session.refresh(ticket)
    messages = _messages(session, ticket.id)
    attachments = _attachments(session, messages[0].id)
    card = get_ticket(ticket.id, db=session, user=SimpleNamespace(id="agent-1", role="agent"))
    agent_messages = [message for message in card.messages if message.message_type == MessageType.AGENT.value]

    assert ticket.status == TicketStatus.PENDING.value
    assert ticket.auto_close_started_at is not None
    assert ticket.auto_close_due_at is not None
    assert len(messages) == 1
    assert messages[0].body == "Plain answer with file"
    assert len(attachments) == 1
    assert attachments[0].telegram_delivery_status == "sending"
    assert len(scheduled) == 1
    assert scheduled[0]["attachment_id"] == attachments[0].id
    assert len(agent_messages) == 1
    assert agent_messages[0].attachments[0].original_filename == "ready.pdf"


def test_internal_comment_raw_file_is_rejected_without_final_attachment(tmp_path):
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)

    async def run():
        await reply_to_ticket(
            request=_MultipartRequest(_FormData(text="Internal note", files=[_upload("note.pdf", b"pdf")])),
            db=session,
            user=SimpleNamespace(id="agent-1", role="agent"),
            ticket=ticket,
            message_type=MessageType.INTERNAL,
            send_to_telegram_client=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("comment text must not be sent")),
            telegram_message_id_from_response=lambda response: None,
            save_upload_attachment=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("raw comment file must not be saved")),
            send_attachment_to_telegram_client=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("comment file must not be sent")),
            claim_attachment_preload=lambda *_args, **_kwargs: None,
            schedule_attachment_delivery=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("comment file must not be scheduled")),
            unfreeze_ticket_on_message=lambda *_args, **_kwargs: None,
            remove_client_close_button=lambda *_args, **_kwargs: None,
            refresh_ticket_auto_close=lambda *_args, **_kwargs: None,
        )

    with pytest.raises(HTTPException) as raised:
        asyncio.run(run())

    messages = _messages(session, ticket.id)

    assert raised.value.status_code == 400
    assert raised.value.detail["error"]["code"] == "comment_raw_files_not_allowed"
    assert messages == []


def test_internal_comment_file_only_ready_preload_is_allowed(tmp_path):
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    path = tmp_path / "only.pdf"
    path.write_bytes(b"pdf")
    preload = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="agent-1",
        original_filename="only.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=str(path),
        telegram_file_id="ready-file-id",
        status="ready",
        created_at=datetime.utcnow(),
    )
    session.add(preload)
    session.commit()

    async def run():
        result = await reply_to_ticket(
            request=_multipart_request_with_preload(text="", preload_id=preload.id),
            db=session,
            user=SimpleNamespace(id="agent-1", role="agent"),
            ticket=ticket,
            message_type=MessageType.INTERNAL,
            send_to_telegram_client=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("comment text must not be sent")),
            telegram_message_id_from_response=lambda response: None,
            save_upload_attachment=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("raw upload should not be used")),
            send_attachment_to_telegram_client=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("comment file must not be sent")),
            claim_attachment_preload=lambda db, preload_id, expected_ticket_id, expected_user_id: outbound_service.claim_attachment_preload(
                db,
                preload_id=preload_id,
                ticket_id=expected_ticket_id,
                user_id=expected_user_id,
            ),
            schedule_attachment_delivery=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("comment file must not be scheduled")),
            unfreeze_ticket_on_message=lambda *_args, **_kwargs: None,
            remove_client_close_button=lambda *_args, **_kwargs: None,
            refresh_ticket_auto_close=lambda *_args, **_kwargs: None,
        )
        assert result == {"ok": True}

    asyncio.run(run())

    messages = _messages(session, ticket.id)
    attachments = _attachments(session, messages[0].id)

    assert messages[0].message_type == MessageType.INTERNAL.value
    assert messages[0].body == ""
    assert attachments[0].original_filename == "only.pdf"
    assert attachments[0].telegram_file_id == "ready-file-id"


def test_internal_comment_ready_preload_is_claimed_without_client_delivery(tmp_path):
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    path = tmp_path / "ready.pdf"
    path.write_bytes(b"pdf")
    preload = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="agent-1",
        original_filename="ready.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=str(path),
        telegram_file_id="ready-file-id",
        status="ready",
        created_at=datetime.utcnow(),
    )
    session.add(preload)
    session.commit()
    sent_to_client = []
    scheduled = []

    async def run():
        result = await reply_to_ticket(
            request=_multipart_request_with_preload(text="Internal with preload", preload_id=preload.id, single_attachment=True),
            db=session,
            user=SimpleNamespace(id="agent-1", role="agent"),
            ticket=ticket,
            message_type=MessageType.INTERNAL,
            send_to_telegram_client=lambda *_args, **_kwargs: sent_to_client.append("text"),
            telegram_message_id_from_response=lambda response: None,
            save_upload_attachment=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("raw upload should not be used")),
            send_attachment_to_telegram_client=lambda *_args, **_kwargs: sent_to_client.append("file"),
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

    assert sent_to_client == []
    assert scheduled == []
    assert messages[0].message_type == MessageType.INTERNAL.value
    assert attachments[0].telegram_file_id == "ready-file-id"
    assert attachments[0].telegram_delivery_status is None
    assert claimed_preload.final_attachment_id == attachments[0].id


def test_internal_comment_claims_multiple_ready_preloads_without_client_delivery(tmp_path):
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    preloads = []
    for filename, file_id in [("first.pdf", "ready-file-1"), ("second.pdf", "ready-file-2")]:
        path = tmp_path / filename
        path.write_bytes(b"pdf")
        preload = outbound_service.TicketAttachmentPreload(
            id=str(uuid.uuid4()),
            ticket_id=ticket.id,
            user_id="agent-1",
            original_filename=filename,
            mime_type="application/pdf",
            file_size=3,
            local_path=str(path),
            telegram_file_id=file_id,
            status="ready",
            created_at=datetime.utcnow(),
        )
        session.add(preload)
        preloads.append(preload)
    session.commit()
    sent_to_client = []
    scheduled = []

    async def run():
        result = await reply_to_ticket(
            request=_MultipartRequest(_FormData(text="Internal with files", files=[], preloaded_attachment_ids=[preload.id for preload in preloads])),
            db=session,
            user=SimpleNamespace(id="agent-1", role="agent"),
            ticket=ticket,
            message_type=MessageType.INTERNAL,
            send_to_telegram_client=lambda *_args, **_kwargs: sent_to_client.append("text"),
            telegram_message_id_from_response=lambda response: None,
            save_upload_attachment=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("raw upload should not be used")),
            send_attachment_to_telegram_client=lambda *_args, **_kwargs: sent_to_client.append("file"),
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

    assert sent_to_client == []
    assert scheduled == []
    assert len(messages) == 1
    assert messages[0].message_type == MessageType.INTERNAL.value
    assert len(attachments) == 2
    assert {attachment.original_filename for attachment in attachments} == {"first.pdf", "second.pdf"}
    assert {attachment.message_id for attachment in attachments} == {messages[0].id}


@pytest.mark.parametrize("workflow", ["ticket", "task"])
def test_internal_comment_uploading_preload_is_not_claimed_or_sent(tmp_path, workflow):
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    ticket.workflow_type = workflow
    path = tmp_path / "uploading.pdf"
    path.write_bytes(b"pdf")
    preload = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="agent-1",
        original_filename="uploading.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=str(path),
        status="uploading",
        created_at=datetime.utcnow(),
    )
    session.add(preload)
    session.commit()

    async def run():
        await reply_to_ticket(
            request=_multipart_request_with_preload(text="Internal with uploading preload", preload_id=preload.id),
            db=session,
            user=SimpleNamespace(id="agent-1", role="agent"),
            ticket=ticket,
            message_type=MessageType.INTERNAL,
            send_to_telegram_client=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("comment text must not be sent")),
            telegram_message_id_from_response=lambda response: None,
            save_upload_attachment=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("raw upload should not be used")),
            send_attachment_to_telegram_client=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("comment file must not be sent")),
            claim_attachment_preload=lambda db, preload_id, expected_ticket_id, expected_user_id: outbound_service.claim_attachment_preload(
                db,
                preload_id=preload_id,
                ticket_id=expected_ticket_id,
                user_id=expected_user_id,
            ),
            schedule_attachment_delivery=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("comment file must not be scheduled")),
            unfreeze_ticket_on_message=lambda *_args, **_kwargs: None,
            remove_client_close_button=lambda *_args, **_kwargs: None,
            refresh_ticket_auto_close=lambda *_args, **_kwargs: None,
        )

    with pytest.raises(HTTPException) as raised:
        asyncio.run(run())

    messages = _messages(session, ticket.id)
    saved_preload = session.get(outbound_service.TicketAttachmentPreload, preload.id)

    assert raised.value.status_code == 409
    assert raised.value.detail["error"]["code"] == "attachment_preload_not_ready"
    assert messages == []
    assert saved_preload.final_attachment_id is None


def test_send_attachment_to_client_uses_telegram_file_id_when_local_preload_file_was_cleaned(monkeypatch, tmp_path):
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    missing_path = tmp_path / "already-cleaned.pdf"
    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=str(uuid.uuid4()),
        source="helpdesk",
        original_filename="already-cleaned.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=str(missing_path),
        telegram_file_id="ready-file-id",
        created_at=datetime.utcnow(),
    )
    session.add(attachment)
    session.commit()

    calls = []
    monkeypatch.setattr(
        "api.app.services.ticket_attachments.send_document_by_file_id",
        lambda chat_id, file_id, **kwargs: calls.append((chat_id, file_id, kwargs)) or {"result": {"message_id": 501}},
    )

    _send_attachment_to_telegram_client(session, ticket, attachment, required=True)

    assert calls[0][0] == "chat-42"
    assert calls[0][1] == "ready-file-id"
    assert attachment.telegram_message_id == "501"


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


def test_remove_claimed_preload_is_rejected_and_preserves_sent_attachment(tmp_path):
    _engine, _Session, session = _session_bundle()
    ticket = _seed_ticket(session)
    path = tmp_path / "sent.pdf"
    path.write_bytes(b"pdf")
    preload = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id="admin-1",
        original_filename="sent.pdf",
        mime_type="application/pdf",
        file_size=3,
        local_path=str(path),
        status="ready",
        final_attachment_id="attachment-1",
        created_at=datetime.utcnow(),
    )
    session.add(preload)
    session.commit()

    with pytest.raises(HTTPException) as raised:
        delete_attachment_preload(
            ticket_id=ticket.id,
            preload_id=preload.id,
            db=session,
            user=SimpleNamespace(id="admin-1", role="admin"),
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["error"]["code"] == "attachment_preload_already_claimed"
    assert session.get(outbound_service.TicketAttachmentPreload, preload.id) is not None
    assert path.exists()


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


def test_cancelled_uploading_preload_cleans_late_staged_telegram_message(monkeypatch, tmp_path):
    _engine, SessionFactory, session = _session_bundle()
    ticket = _seed_ticket(session)
    user = ticket_messages_service.User(
        id="agent-1",
        company_id=ticket.company_id,
        full_name="Иван Петров",
        role="agent",
    )
    path = tmp_path / "late.pdf"
    path.write_bytes(b"pdf")
    preload = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id=user.id,
        original_filename="late.pdf",
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
    monkeypatch.setattr(outbound_service, "send_message", lambda *_args, **_kwargs: {"result": {"message_id": 501}})
    deleted = []
    monkeypatch.setattr(outbound_service, "delete_message", lambda chat_id, message_id: deleted.append((chat_id, str(message_id))) or {"ok": True})
    monkeypatch.setattr(outbound_service, "send_document_by_file_id", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("client delivery should not run")))

    def _cancel_after_staging_started(*_args, **_kwargs):
        cancel_session = SessionFactory()
        try:
            cancelling = cancel_session.get(outbound_service.TicketAttachmentPreload, preload.id)
            cancelling.status = "cancelled"
            cancel_session.commit()
        finally:
            cancel_session.close()
        return {"result": {"message_id": 502, "document": {"file_id": "stage-file-id", "file_unique_id": "stage-unique-id"}}}

    monkeypatch.setattr(outbound_service, "send_document", _cancel_after_staging_started)

    outbound_service.deliver_attachment_preload(preload_id=preload.id)

    check = SessionFactory()
    try:
        assert check.get(outbound_service.TicketAttachmentPreload, preload.id) is None
    finally:
        check.close()
    assert deleted == [("staging-chat", "502"), ("staging-chat", "501")]
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
    ticket.subject = "Need <help> & review"
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
    monkeypatch.setattr(outbound_service.settings, "app_public_base_url", "https://desk.example.test/base/")
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
    monkeypatch.setattr(outbound_service, "edit_message_text", lambda chat_id, message_id, text, **kwargs: edits.append((chat_id, message_id, text, kwargs)) or {"ok": True})

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
    assert '<a href="https://desk.example.test/base/tickets/480">Заявка №480</a> — Need &lt;help&gt; &amp; review' in calls[0][2]
    assert "Иван Петров" in calls[0][2]
    assert "• <code>report.pdf</code>" in calls[0][2]
    assert calls[0][3]["parse_mode"] == "HTML"
    assert "Статус: подготовка файла" in calls[0][2]
    assert "caption" not in calls[1][3]
    assert edits and "Статус: готов к отправке" in edits[-1][2]


def test_multiple_preloads_same_archive_group_share_one_info_card_and_filename_list(monkeypatch, tmp_path):
    _engine, SessionFactory, session = _session_bundle()
    ticket = _seed_ticket(session)
    ticket.ticket_number = 557
    ticket.subject = "Временно <заблокировать> & сотрудника"
    user = ticket_messages_service.User(
        id="agent-1",
        company_id=ticket.company_id,
        full_name="Иван Петров",
        role="agent",
    )
    first_path = tmp_path / "HANDOVER.md"
    second_path = tmp_path / "b.pdf"
    first_path.write_bytes(b"a")
    second_path.write_bytes(b"b")
    first = outbound_service.TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id=user.id,
        archive_group_id="group-1",
        original_filename="HANDOVER.md",
        mime_type="text/markdown",
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
    monkeypatch.setattr(outbound_service.settings, "app_public_base_url", "https://desk.example.test")
    info_calls = []
    monkeypatch.setattr(
        outbound_service,
        "send_message",
        lambda chat_id, text, **kwargs: info_calls.append((chat_id, text, kwargs)) or {"result": {"message_id": 301}},
    )
    file_message_ids = iter([302, 303])
    sent_documents = []
    monkeypatch.setattr(
        outbound_service,
        "send_document",
        lambda chat_id, path_value, **kwargs: sent_documents.append((chat_id, Path(path_value).name, kwargs)) or {
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
    monkeypatch.setattr(outbound_service, "edit_message_text", lambda chat_id, message_id, text, **kwargs: edits.append((chat_id, str(message_id), text, kwargs)) or {"ok": True})

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
    info_text = info_calls[0][1]
    assert info_calls[0][2]["parse_mode"] == "HTML"
    assert '<a href="https://desk.example.test/tickets/557">Заявка №557</a> — Временно &lt;заблокировать&gt; &amp; сотрудника' in info_text
    assert "• <code>HANDOVER.md</code>" in info_text
    assert "• <code>b.pdf</code>" in info_text
    assert any("• <code>HANDOVER.md</code>" in text and "• <code>b.pdf</code>" in text for _, _, text, _ in edits)
    assert any("Статус: готово к отправке" in text for _, _, text, _ in edits)
    assert [item[1] for item in sent_documents] == ["HANDOVER.md", "b.pdf"]


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
    monkeypatch.setattr(outbound_service, "edit_message_text", lambda chat_id, message_id, text, **kwargs: edits.append((chat_id, str(message_id), text, kwargs)) or {"ok": True})

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
    assert "• <code>b.pdf</code>" in edits[-1][2]
    assert "• <code>a.pdf</code>" not in edits[-1][2]


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
    monkeypatch.setattr(outbound_service, "edit_message_text", lambda chat_id, message_id, text, **kwargs: edits.append((chat_id, str(message_id), text, kwargs)) or {"ok": True})

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
    monkeypatch.setattr(outbound_service, "edit_message_text", lambda chat_id, message_id, text, **kwargs: edits.append((chat_id, str(message_id), text, kwargs)) or {"ok": True})

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

    assert any("Статус: ошибка доставки" in text for _, _, text, _ in edits)


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
    monkeypatch.setattr(outbound_service, "edit_message_text", lambda chat_id, message_id, text, **kwargs: edits.append((chat_id, str(message_id), text, kwargs)) or {"ok": True})

    outbound_service.deliver_attachment_preload(preload_id=preload.id)

    check = SessionFactory()
    try:
        saved_attachment = check.get(TicketAttachment, attachment.id)
        assert saved_attachment.telegram_delivery_status == "sent"
        assert check.get(outbound_service.TicketAttachmentPreload, preload.id) is None
    finally:
        check.close()

    assert any("Статус: отправляется клиенту" in text for _, _, text, _ in edits)
    assert any("Статус: отправлено" in text for _, _, text, _ in edits)


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
