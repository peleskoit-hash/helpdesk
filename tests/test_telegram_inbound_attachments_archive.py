from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import threading
import time
import uuid

import pytest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import Client, ClientProfile, Company, MessageType, TelegramContact, Ticket, TicketAttachment, TicketIntakeState, TicketMessage, TicketStatus, TicketWorkflowType
from api.app.routers import telegram as telegram_router
from api.app.services import telegram_attachments as telegram_attachments_service


def _session_bundle(tmp_path):
    db_path = tmp_path / f"{uuid.uuid4().hex}.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine, Session


def _seed_ticket_context(session):
    now = datetime.utcnow()
    company = Company(id=str(uuid.uuid4()), name="ACME")
    client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Telegram Client")
    profile = ClientProfile(
        id=str(uuid.uuid4()),
        client_id=client.id,
        contact_name="Ivan",
        last_name="Petrov",
        created_at=now,
        updated_at=now,
    )
    contact = TelegramContact(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        external_user_id="100500",
        external_chat_id="100500",
        external_username="telegram_user",
        external_display_name="Ivan Petrov",
    )
    ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        ticket_number=123,
        subject="Need help",
        workflow_type=TicketWorkflowType.TICKET.value,
        status=TicketStatus.NEW.value,
        created_at=now,
        updated_at=now,
    )
    session.add_all([company, client, profile, contact, ticket])
    session.commit()
    return ticket, contact


def _document_payload(
    *,
    update_id: int,
    chat_id: str,
    user_id: str,
    file_id: str = "telegram-file-1",
    filename: str = "report.pdf",
    file_size: int = 321,
    media_group_id: str | None = None,
) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "chat": {"id": int(chat_id), "type": "private"},
            "from": {"id": int(user_id), "first_name": "Ivan", "last_name": "Petrov"},
            "media_group_id": media_group_id,
            "document": {
                "file_id": file_id,
                "file_unique_id": f"uniq-{file_id}",
                "file_size": file_size,
                "mime_type": "application/pdf",
                "file_name": filename,
            },
        },
    }


def _photo_payload(
    *,
    update_id: int,
    chat_id: str,
    user_id: str,
    file_id: str = "telegram-photo-1",
    media_group_id: str | None = None,
) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "chat": {"id": int(chat_id), "type": "private"},
            "from": {"id": int(user_id), "first_name": "Ivan", "last_name": "Petrov"},
            "media_group_id": media_group_id,
            "photo": [
                {
                    "file_id": f"{file_id}-small",
                    "file_unique_id": f"uniq-{file_id}-small",
                    "file_size": 100,
                    "width": 10,
                    "height": 10,
                },
                {
                    "file_id": file_id,
                    "file_unique_id": f"uniq-{file_id}",
                    "file_size": 500,
                    "width": 40,
                    "height": 40,
                },
            ],
        },
    }


def _video_payload(
    *,
    update_id: int,
    chat_id: str,
    user_id: str,
    file_id: str = "telegram-video-1",
    media_group_id: str | None = None,
) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "chat": {"id": int(chat_id), "type": "private"},
            "from": {"id": int(user_id), "first_name": "Ivan", "last_name": "Petrov"},
            "media_group_id": media_group_id,
            "video": {
                "file_id": file_id,
                "file_unique_id": f"uniq-{file_id}",
                "file_size": 2048,
                "mime_type": "video/mp4",
                "file_name": "clip.mp4",
            },
        },
    }


def _too_large_document_payload(*, update_id: int, chat_id: str, user_id: str) -> dict:
    payload = _document_payload(update_id=update_id, chat_id=chat_id, user_id=user_id, file_id="too-large-file")
    payload["message"]["document"]["file_size"] = 999999999
    return payload


def _run_inline_archive_worker(monkeypatch, Session):
    monkeypatch.setattr(telegram_attachments_service, "SessionLocal", Session)
    monkeypatch.setattr(telegram_router, "start_background_archive_inbound_attachment", telegram_attachments_service._archive_inbound_attachment_worker)


def _run_media_group_archive_scheduler(monkeypatch, Session, timer_factory=None):
    if timer_factory is None:
        timer_factory = _ManualTimer
    _ManualTimer.instances = []
    monkeypatch.setattr(telegram_attachments_service, "SessionLocal", Session)
    monkeypatch.setattr(telegram_attachments_service, "_MEDIA_GROUP_ARCHIVE_TIMER_FACTORY", timer_factory)
    monkeypatch.setattr(telegram_router, "start_background_archive_inbound_attachment", telegram_attachments_service.start_background_archive_inbound_attachment)
    with telegram_attachments_service._MEDIA_GROUP_ARCHIVE_LOCK:
        telegram_attachments_service._MEDIA_GROUP_ARCHIVE.clear()


def test_extract_attachment_keeps_media_group_id():
    attachment = telegram_attachments_service._extract_attachment(
        _document_payload(
            update_id=1999,
            chat_id="100500",
            user_id="100500",
            file_id="telegram-file-mg",
            media_group_id="mg-1",
        )["message"]
    )

    assert attachment is not None
    assert attachment["media_group_id"] == "mg-1"


def test_copy_message_helper_includes_caption_even_when_empty(monkeypatch):
    calls = []

    def _telegram_request(method, payload=None, timeout=30, **kwargs):
        calls.append((method, payload, timeout))
        return {"ok": True, "result": {"message_id": 321}}

    from api.app.services import telegram_bot as telegram_bot_service
    monkeypatch.setattr(telegram_bot_service, "telegram_request", _telegram_request)

    telegram_bot_service.copy_message("dst-chat", "src-chat", "42", caption="", parse_mode="HTML")

    assert calls == [
        (
            "copyMessage",
            {
                "chat_id": "dst-chat",
                "from_chat_id": "src-chat",
                "message_id": 42,
                "caption": "",
                "parse_mode": "HTML",
            },
            30,
        )
    ]


def test_archive_info_pending_token_fits_varchar_100():
    token = telegram_attachments_service._archive_info_pending_token(
        "12345678-1234-1234-1234-123456789012",
        "very-long-media-group-id-that-should-not-affect-token-length-at-all",
    )

    assert len(token) <= 100


def _seed_existing_client_message(session, ticket_id: str) -> None:
    session.add(
        TicketMessage(
            id=str(uuid.uuid4()),
            ticket_id=ticket_id,
            message_type=MessageType.CLIENT.value,
            author_user_id=None,
            body="Existing message",
            created_at=datetime.utcnow(),
        )
    )
    session.commit()


class _InlineThread:
    def __init__(self, *, target, args=(), daemon=None):
        self._target = target
        self._args = args
        self.daemon = daemon

    def start(self):
        self._target(*self._args)


class _ManualTimer:
    instances = []

    def __init__(self, interval, function, args=(), kwargs=None):
        self.interval = interval
        self.function = function
        self.args = args
        self.kwargs = kwargs or {}
        self.daemon = False
        self.cancelled = False
        _ManualTimer.instances.append(self)

    def start(self):
        return None

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.function(*self.args, **self.kwargs)


def test_store_telegram_attachment_does_not_archive_synchronously(monkeypatch, tmp_path):
    _engine, Session = _session_bundle(tmp_path)
    session = Session()
    ticket, _contact = _seed_ticket_context(session)
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.CLIENT.value,
        author_user_id=None,
        body="Attachment inbound",
        created_at=datetime.utcnow(),
    )
    session.add(message)
    session.commit()
    monkeypatch.setattr(telegram_attachments_service.settings, "attachment_storage_dir", str(tmp_path))
    monkeypatch.setattr(
        telegram_attachments_service,
        "get_file",
        lambda file_id, **_kwargs: {"result": {"file_path": "docs/inbound.pdf", "file_unique_id": f"uniq-{file_id}"}},
    )
    monkeypatch.setattr(
        telegram_attachments_service,
        "copy_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("archive must not run inside _store_telegram_attachment")),
    )
    monkeypatch.setattr(
        telegram_attachments_service,
        "_try_prefetch_attachment_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("prefetch must not run inside _store_telegram_attachment")),
    )

    attachment = telegram_attachments_service._store_telegram_attachment(
        session,
        ticket,
        message,
        {
            "source": "document",
            "file_id": "telegram-file-1",
            "file_unique_id": "client-uniq-1",
            "file_size": 321,
            "mime_type": "application/pdf",
            "original_filename": "report.pdf",
        },
        "100500",
    )
    session.commit()

    saved = session.get(TicketAttachment, attachment.id)
    assert saved is not None
    assert saved.telegram_file_id == "telegram-file-1"
    assert saved.telegram_archive_message_id is None
    assert saved.telegram_archive_info_message_id is None
    assert saved.telegram_media_group_id is None


def test_ensure_local_attachment_file_restores_missing_file_via_telegram_file_id(monkeypatch, tmp_path):
    _engine, Session = _session_bundle(tmp_path)
    session = Session()
    ticket, _contact = _seed_ticket_context(session)
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.CLIENT.value,
        author_user_id=None,
        body="Attachment inbound",
        created_at=datetime.utcnow(),
    )
    session.add(message)
    session.commit()

    local_path = tmp_path / "downloads" / "restored.pdf"
    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=message.id,
        source="document",
        telegram_file_id="telegram-file-restore",
        telegram_file_unique_id="uniq-restore",
        telegram_file_path=None,
        original_filename="restored.pdf",
        mime_type="application/pdf",
        file_size=123,
        local_path=str(local_path),
        created_at=datetime.utcnow(),
    )
    session.add(attachment)
    session.commit()

    calls = {"get_file": 0, "download_file": 0}

    monkeypatch.setattr(
        telegram_attachments_service,
        "get_file",
        lambda file_id, **_kwargs: calls.__setitem__("get_file", calls["get_file"] + 1) or {
            "result": {"file_path": "docs/restored.pdf"}
        },
    )

    def _download_file(file_path, path, **_kwargs):
        calls["download_file"] += 1
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"restored")

    monkeypatch.setattr(telegram_attachments_service, "download_file", _download_file)

    restored = telegram_attachments_service._ensure_local_attachment_file(attachment, external_chat_id="100500")

    assert restored == local_path
    assert restored.exists()
    assert restored.read_bytes() == b"restored"
    assert attachment.telegram_file_path == "docs/restored.pdf"
    assert calls == {"get_file": 1, "download_file": 1}


def test_archive_is_scheduled_only_after_attachment_commit(monkeypatch, tmp_path):
    _engine, Session = _session_bundle(tmp_path)
    session = Session()
    ticket, contact = _seed_ticket_context(session)
    _seed_existing_client_message(session, ticket.id)
    monkeypatch.setattr(telegram_attachments_service.settings, "attachment_storage_dir", str(tmp_path))
    monkeypatch.setattr(
        telegram_attachments_service,
        "get_file",
        lambda file_id, **_kwargs: {"result": {"file_path": "docs/inbound.pdf", "file_unique_id": f"uniq-{file_id}"}},
    )
    monkeypatch.setattr(telegram_attachments_service, "_try_prefetch_attachment_file", lambda *_args, **_kwargs: None)

    seen: dict[str, object] = {}

    def _schedule_after_commit(attachment_id: str | None):
        seen["attachment_id"] = attachment_id
        with Session() as other:
            seen["exists_in_other_session"] = other.get(TicketAttachment, attachment_id) is not None

    monkeypatch.setattr(telegram_router, "start_background_archive_inbound_attachment", _schedule_after_commit)

    result = telegram_router.process_telegram_update(
        session,
        _document_payload(update_id=2001, chat_id=contact.external_chat_id, user_id=contact.external_user_id),
        notify_client=False,
    )

    assert result["ok"] is True
    assert seen["attachment_id"]
    assert seen["exists_in_other_session"] is True


def test_main_inbound_attachment_is_saved_even_when_background_archive_fails(monkeypatch, tmp_path):
    _engine, Session = _session_bundle(tmp_path)
    session = Session()
    ticket, contact = _seed_ticket_context(session)
    _seed_existing_client_message(session, ticket.id)
    monkeypatch.setattr(telegram_attachments_service.settings, "attachment_storage_dir", str(tmp_path))
    monkeypatch.setattr(
        telegram_attachments_service,
        "get_file",
        lambda file_id, **_kwargs: {"result": {"file_path": "docs/inbound.pdf", "file_unique_id": f"uniq-{file_id}"}},
    )
    monkeypatch.setattr(telegram_attachments_service, "_try_prefetch_attachment_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(telegram_attachments_service, "SessionLocal", Session)
    monkeypatch.setattr(telegram_attachments_service, "_staging_chat_id", lambda: "staging-chat")
    monkeypatch.setattr(
        telegram_attachments_service,
        "copy_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(telegram_attachments_service.TelegramApiError("staging failed")),
    )
    monkeypatch.setattr(telegram_router, "start_background_archive_inbound_attachment", telegram_attachments_service._archive_inbound_attachment_worker)

    result = telegram_router.process_telegram_update(
        session,
        _document_payload(update_id=2002, chat_id=contact.external_chat_id, user_id=contact.external_user_id, file_id="telegram-file-2"),
        notify_client=False,
    )

    attachments = session.execute(select(TicketAttachment).where(TicketAttachment.ticket_id == ticket.id)).scalars().all()
    assert result["ok"] is True
    assert len(attachments) == 1
    assert attachments[0].telegram_file_id == "telegram-file-2"
    assert attachments[0].telegram_archive_message_id is None


def test_too_large_attachment_does_not_store_attachment_or_schedule_archive(monkeypatch, tmp_path):
    _engine, Session = _session_bundle(tmp_path)
    session = Session()
    ticket, contact = _seed_ticket_context(session)
    _seed_existing_client_message(session, ticket.id)

    calls = {"store": 0, "background": 0}
    monkeypatch.setattr(telegram_router, "attachment_is_too_large", lambda _data: True)
    monkeypatch.setattr(
        telegram_router,
        "_store_telegram_attachment",
        lambda *_args, **_kwargs: calls.__setitem__("store", calls["store"] + 1),
    )
    monkeypatch.setattr(
        telegram_router,
        "start_background_archive_inbound_attachment",
        lambda _attachment_id: calls.__setitem__("background", calls["background"] + 1),
    )
    monkeypatch.setattr(telegram_router, "send_too_large_attachment_client_messages", lambda *_args, **_kwargs: None)

    result = telegram_router.process_telegram_update(
        session,
        _too_large_document_payload(update_id=2003, chat_id=contact.external_chat_id, user_id=contact.external_user_id),
        notify_client=False,
    )

    attachments = session.execute(select(TicketAttachment).where(TicketAttachment.ticket_id == ticket.id)).scalars().all()
    assert result["ok"] is True
    assert calls == {"store": 0, "background": 0}
    assert attachments == []


def test_background_archive_worker_finds_attachment_in_new_db_session(monkeypatch, tmp_path):
    _engine, Session = _session_bundle(tmp_path)
    session = Session()
    ticket, _contact = _seed_ticket_context(session)
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.CLIENT.value,
        author_user_id=None,
        body="Attachment inbound",
        telegram_message_id="3003",
        created_at=datetime.utcnow(),
    )
    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=message.id,
        source="document",
        telegram_file_id="telegram-file-3",
        telegram_file_unique_id="client-uniq-3",
        telegram_file_path="docs/inbound-3.pdf",
        original_filename="report-3.pdf",
        mime_type="application/pdf",
        file_size=777,
        local_path=str(tmp_path / "report-3.pdf"),
        created_at=datetime.utcnow(),
    )
    session.add_all([message, attachment])
    session.commit()

    monkeypatch.setattr(telegram_attachments_service, "SessionLocal", Session)
    monkeypatch.setattr(telegram_attachments_service, "_staging_chat_id", lambda: "staging-chat")
    archive_calls: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        telegram_attachments_service,
        "copy_message",
        lambda *args, **kwargs: archive_calls.append((args, kwargs)) or {"result": {"message_id": 903}},
    )

    monkeypatch.setattr(telegram_attachments_service, "Thread", _InlineThread)
    telegram_attachments_service.start_background_archive_inbound_attachment(attachment.id)

    with Session() as check:
        saved = check.get(TicketAttachment, attachment.id)
        assert saved is not None
        assert saved.telegram_archive_message_id == "903"
        assert saved.telegram_archive_info_message_id is None
    assert len(archive_calls) == 1
    assert archive_calls[0][0] == ("staging-chat", "100500", "3003")
    assert "report-3.pdf" in archive_calls[0][1]["caption"]

def test_single_document_inbound_file_archives_via_copy_message(monkeypatch, tmp_path):
    _engine, Session = _session_bundle(tmp_path)
    session = Session()
    ticket, contact = _seed_ticket_context(session)
    _seed_existing_client_message(session, ticket.id)
    monkeypatch.setattr(telegram_attachments_service.settings, "attachment_storage_dir", str(tmp_path))
    monkeypatch.setattr(
        telegram_attachments_service,
        "get_file",
        lambda file_id, **_kwargs: {"result": {"file_path": "docs/inbound.pdf", "file_unique_id": f"uniq-{file_id}"}},
    )
    monkeypatch.setattr(telegram_attachments_service, "_try_prefetch_attachment_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(telegram_attachments_service, "_staging_chat_id", lambda: "staging-chat")
    _run_inline_archive_worker(monkeypatch, Session)
    calls = {"send_message": 0, "edit_message_text": 0, "copy_message": []}
    monkeypatch.setattr(telegram_attachments_service, "send_message", lambda *_args, **_kwargs: calls.__setitem__("send_message", calls["send_message"] + 1))
    monkeypatch.setattr(telegram_attachments_service, "edit_message_text", lambda *_args, **_kwargs: calls.__setitem__("edit_message_text", calls["edit_message_text"] + 1))
    monkeypatch.setattr(
        telegram_attachments_service,
        "copy_message",
        lambda *args, **kwargs: calls["copy_message"].append((args, kwargs)) or {"result": {"message_id": 910}},
    )
    monkeypatch.setattr(
        telegram_attachments_service,
        "download_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("archive must not download local binary")),
    )

    result = telegram_router.process_telegram_update(
        session,
        _document_payload(update_id=2004, chat_id=contact.external_chat_id, user_id=contact.external_user_id, filename="single.csv"),
        notify_client=False,
    )

    saved = session.execute(select(TicketAttachment).where(TicketAttachment.ticket_id == ticket.id)).scalar_one()
    assert result["ok"] is True
    assert saved.telegram_archive_message_id == "910"
    assert saved.telegram_archive_info_message_id is None
    assert saved.telegram_media_group_id is None
    assert calls["send_message"] == 0
    assert calls["edit_message_text"] == 0
    assert len(calls["copy_message"]) == 1
    assert calls["copy_message"][0][0] == ("staging-chat", contact.external_chat_id, "2004")
    assert "caption" in calls["copy_message"][0][1]
    assert calls["copy_message"][0][1]["parse_mode"] == "HTML"
    assert "• <code>single.csv</code>" in calls["copy_message"][0][1]["caption"]


def test_single_photo_inbound_file_archives_via_copy_message_without_local_binary(monkeypatch, tmp_path):
    _engine, Session = _session_bundle(tmp_path)
    session = Session()
    ticket, contact = _seed_ticket_context(session)
    _seed_existing_client_message(session, ticket.id)
    monkeypatch.setattr(telegram_attachments_service.settings, "attachment_storage_dir", str(tmp_path))
    monkeypatch.setattr(
        telegram_attachments_service,
        "get_file",
        lambda file_id, **_kwargs: {"result": {"file_path": f"photos/{file_id}.jpg", "file_unique_id": f"uniq-{file_id}"}} ,
    )
    monkeypatch.setattr(telegram_attachments_service, "_try_prefetch_attachment_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(telegram_attachments_service, "_staging_chat_id", lambda: "staging-chat")
    _run_inline_archive_worker(monkeypatch, Session)
    calls = {"copy_message": 0}
    monkeypatch.setattr(
        telegram_attachments_service,
        "copy_message",
        lambda *args, **kwargs: calls.__setitem__("copy_message", calls["copy_message"] + 1) or {"result": {"message_id": 911}},
    )
    monkeypatch.setattr(
        telegram_attachments_service,
        "download_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("copyMessage must not require local binary")),
    )

    result = telegram_router.process_telegram_update(
        session,
        _photo_payload(update_id=2005, chat_id=contact.external_chat_id, user_id=contact.external_user_id, file_id="photo-main"),
        notify_client=False,
    )

    saved = session.execute(select(TicketAttachment).where(TicketAttachment.ticket_id == ticket.id)).scalar_one()
    assert result["ok"] is True
    assert saved.source == "photo"
    assert saved.telegram_archive_message_id == "911"
    assert not Path(saved.local_path).exists()
    assert calls == {"copy_message": 1}


def test_single_video_inbound_file_archives_via_copy_message(monkeypatch, tmp_path):
    _engine, Session = _session_bundle(tmp_path)
    session = Session()
    ticket, contact = _seed_ticket_context(session)
    _seed_existing_client_message(session, ticket.id)
    monkeypatch.setattr(telegram_attachments_service.settings, "attachment_storage_dir", str(tmp_path))
    monkeypatch.setattr(
        telegram_attachments_service,
        "get_file",
        lambda file_id, **_kwargs: {"result": {"file_path": f"video/{file_id}.mp4", "file_unique_id": f"uniq-{file_id}"}} ,
    )
    monkeypatch.setattr(telegram_attachments_service, "_try_prefetch_attachment_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(telegram_attachments_service, "_staging_chat_id", lambda: "staging-chat")
    _run_inline_archive_worker(monkeypatch, Session)
    calls = {"copy_message": []}
    monkeypatch.setattr(
        telegram_attachments_service,
        "copy_message",
        lambda *args, **kwargs: calls["copy_message"].append((args, kwargs)) or {"result": {"message_id": 912}},
    )

    result = telegram_router.process_telegram_update(
        session,
        _video_payload(update_id=2006, chat_id=contact.external_chat_id, user_id=contact.external_user_id, file_id="video-main"),
        notify_client=False,
    )

    saved = session.execute(select(TicketAttachment).where(TicketAttachment.ticket_id == ticket.id)).scalar_one()
    assert result["ok"] is True
    assert saved.source == "video"
    assert saved.telegram_archive_message_id == "912"
    assert len(calls["copy_message"]) == 1


@pytest.mark.parametrize("workflow,caption_index,file_count", [
    ("ticket", 0, 8),
    ("task", 0, 4),
    ("task", 2, 4),
])
def test_media_group_finalizes_after_quiet_window(monkeypatch, tmp_path, workflow, caption_index, file_count):
    _engine, Session = _session_bundle(tmp_path)
    session = Session()
    ticket, contact = _seed_ticket_context(session)
    if workflow == "task":
        ticket.workflow_type = workflow
        ticket.status = TicketStatus.UNASSIGNED.value
        ticket.intake_state = TicketIntakeState.COLLECTING.value
        session.commit()
        monkeypatch.setattr(telegram_router, "_button_from_callback", lambda *_args: None)
    else:
        _seed_existing_client_message(session, ticket.id)
    monkeypatch.setattr(telegram_attachments_service.settings, "attachment_storage_dir", str(tmp_path))
    monkeypatch.setattr(
        telegram_attachments_service,
        "get_file",
        lambda file_id, **_kwargs: {"result": {"file_path": f"docs/{file_id}.pdf", "file_unique_id": f"uniq-{file_id}"}},
    )
    monkeypatch.setattr(telegram_attachments_service, "_try_prefetch_attachment_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(telegram_attachments_service, "_staging_chat_id", lambda: "staging-chat")
    _run_media_group_archive_scheduler(monkeypatch, Session)
    state = {"send_message": [], "edit_message_text": [], "copy_message": []}
    monkeypatch.setattr(
        telegram_attachments_service,
        "send_message",
        lambda *args, **kwargs: state["send_message"].append((args, kwargs)) or {"result": {"message_id": 501}},
    )
    monkeypatch.setattr(
        telegram_attachments_service,
        "edit_message_text",
        lambda *args, **kwargs: state["edit_message_text"].append((args, kwargs)) or {"result": True},
    )
    monkeypatch.setattr(
        telegram_attachments_service,
        "copy_message",
        lambda *args, **kwargs: state["copy_message"].append((args, kwargs)) or {"result": {"message_id": 600 + len(state["copy_message"]) }},
    )

    for index in range(file_count):
        payload = _document_payload(
            update_id=2101 + index,
            chat_id=contact.external_chat_id,
            user_id=contact.external_user_id,
            file_id=f"file-{index}",
            filename=f"file-{index}.pdf",
            media_group_id="mg-42",
        )
        if index == caption_index:
            payload["message"]["caption"] = "Описание задачи клиента"
        result = telegram_router.process_telegram_update(session, payload, notify_client=False)
        assert result["ok"] is True
        assert result["ticket_id"] == ticket.id
        assert telegram_router.process_telegram_update(session, payload, notify_client=False)["duplicate"] is True

    assert len(session.execute(select(Ticket)).scalars().all()) == 1
    bodies = session.execute(select(TicketMessage.body).where(TicketMessage.ticket_id == ticket.id)).scalars().all()
    assert bodies.count("Описание задачи клиента") == 1
    if workflow == "task":
        assert ticket.intake_state == TicketIntakeState.READY.value
        client_messages = session.execute(select(TicketMessage).where(
            TicketMessage.ticket_id == ticket.id, TicketMessage.message_type == MessageType.CLIENT.value,
        )).scalars().all()
        assert len(client_messages) == file_count
        assert {row.telegram_message_id for row in client_messages} == {str(2101 + index) for index in range(file_count)}
        for index, group_id in enumerate(["another-album", None]):
            payload = _document_payload(
                update_id=2200 + index, chat_id=contact.external_chat_id,
                user_id=contact.external_user_id, media_group_id=group_id,
            )
            assert telegram_router.process_telegram_update(session, payload, notify_client=False)["ignored"] == "problem_type_required"
        text_payload = {
            "update_id": 2203,
            "message": {
                "message_id": 2203, "chat": {"id": int(contact.external_chat_id), "type": "private"},
                "from": {"id": int(contact.external_user_id)}, "text": "Another message", "media_group_id": "mg-42",
            },
        }
        assert telegram_router.process_telegram_update(session, text_payload, notify_client=False)["ignored"] == "problem_type_required"
        payload = _document_payload(update_id=2202, chat_id="200500", user_id="200500", media_group_id="mg-42")
        assert telegram_router.process_telegram_update(session, payload, notify_client=False)["ignored"] == "problem_type_required"
        assert len(session.execute(select(Ticket)).scalars().all()) == 1

    attachments = session.execute(
        select(TicketAttachment).where(TicketAttachment.ticket_id == ticket.id).order_by(TicketAttachment.created_at, TicketAttachment.id)
    ).scalars().all()
    assert len(attachments) == file_count
    assert {row.telegram_media_group_id for row in attachments} == {"mg-42"}
    caption_message = session.execute(select(TicketMessage).where(
        TicketMessage.ticket_id == ticket.id, TicketMessage.body == "Описание задачи клиента",
    )).scalar_one()
    assert next(row for row in attachments if row.message_id == caption_message.id).telegram_file_id == f"file-{caption_index}"
    assert len(_ManualTimer.instances) == file_count
    assert [timer.interval for timer in _ManualTimer.instances] == [30.0] * file_count
    assert _ManualTimer.instances[:-1]
    assert all(timer.cancelled for timer in _ManualTimer.instances[:-1])
    assert len(state["send_message"]) == 0
    assert len(state["copy_message"]) == 0
    _ManualTimer.instances[-1].fire()

    with Session() as check:
        attachments = check.execute(
            select(TicketAttachment).where(TicketAttachment.ticket_id == ticket.id).order_by(TicketAttachment.created_at, TicketAttachment.id)
        ).scalars().all()
    assert len({row.telegram_archive_info_message_id for row in attachments}) == 1
    assert attachments[0].telegram_archive_info_message_id == "501"
    assert all(str(row.telegram_archive_message_id or "").strip() for row in attachments)
    assert len(state["send_message"]) == 1
    assert len(state["copy_message"]) == file_count
    assert all(kwargs.get("caption") == "" for _args, kwargs in state["copy_message"])
    final_info_text = state["edit_message_text"][-1][0][2]
    assert all(f"• <code>file-{index}.pdf</code>" in final_info_text for index in range(file_count))


def test_task_album_requires_unambiguous_ticket_match(tmp_path):
    engine, Session = _session_bundle(tmp_path)
    with Session() as session:
        ticket, contact = _seed_ticket_context(session)
        ticket.workflow_type = TicketWorkflowType.TASK.value
        message = telegram_router._append_client_message(session, ticket, "Album", schedule_notification=False)
        for index in range(2):
            session.add(TicketAttachment(
                id=str(uuid.uuid4()), ticket_id=ticket.id, message_id=message.id,
                source="telegram", local_path="unused", telegram_media_group_id="album",
            ))
        session.commit()
        assert telegram_router._task_for_client_album(session, contact, {"media_group_id": "album"}).id == ticket.id

        other = Ticket(
            id=str(uuid.uuid4()), company_id=ticket.company_id, client_id=ticket.client_id,
            subject="Other task", workflow_type=TicketWorkflowType.TASK.value,
        )
        session.add(other)
        session.flush()
        other_message = telegram_router._append_client_message(session, other, "Other album", schedule_notification=False)
        session.add(TicketAttachment(
            id=str(uuid.uuid4()), ticket_id=other.id, message_id=other_message.id,
            source="telegram", local_path="unused", telegram_media_group_id="album",
        ))
        session.commit()
        assert telegram_router._task_for_client_album(session, contact, {"media_group_id": "album"}) is None
    engine.dispose()


def test_media_group_archives_ten_files_under_one_info_card(monkeypatch, tmp_path):
    _engine, Session = _session_bundle(tmp_path)
    session = Session()
    ticket, contact = _seed_ticket_context(session)
    _seed_existing_client_message(session, ticket.id)
    monkeypatch.setattr(telegram_attachments_service.settings, "attachment_storage_dir", str(tmp_path))
    monkeypatch.setattr(
        telegram_attachments_service,
        "get_file",
        lambda file_id, **_kwargs: {"result": {"file_path": f"docs/{file_id}.pdf", "file_unique_id": f"uniq-{file_id}"}},
    )
    monkeypatch.setattr(telegram_attachments_service, "_try_prefetch_attachment_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(telegram_attachments_service, "_staging_chat_id", lambda: "staging-chat")
    _run_media_group_archive_scheduler(monkeypatch, Session)
    state = {"send_message": [], "edit_message_text": [], "copy_message": []}
    monkeypatch.setattr(
        telegram_attachments_service,
        "send_message",
        lambda *args, **kwargs: state["send_message"].append((args, kwargs)) or {"result": {"message_id": 801}},
    )
    monkeypatch.setattr(
        telegram_attachments_service,
        "edit_message_text",
        lambda *args, **kwargs: state["edit_message_text"].append((args, kwargs)) or {"result": True},
    )
    monkeypatch.setattr(
        telegram_attachments_service,
        "copy_message",
        lambda *args, **kwargs: state["copy_message"].append((args, kwargs)) or {"result": {"message_id": 900 + len(state["copy_message"]) }},
    )

    for index in range(10):
        result = telegram_router.process_telegram_update(
            session,
            _document_payload(
                update_id=2300 + index,
                chat_id=contact.external_chat_id,
                user_id=contact.external_user_id,
                file_id=f"file-{index}",
                filename=f"file-{index}.pdf",
                media_group_id="mg-10",
            ),
            notify_client=False,
        )
        assert result["ok"] is True

    attachments = session.execute(
        select(TicketAttachment).where(TicketAttachment.ticket_id == ticket.id).order_by(TicketAttachment.created_at, TicketAttachment.id)
    ).scalars().all()
    assert len(attachments) == 10
    assert len(_ManualTimer.instances) == 10
    assert [timer.interval for timer in _ManualTimer.instances[:-1]] == [30.0] * 9
    assert _ManualTimer.instances[-1].interval == 0.0
    assert len(state["copy_message"]) == 0
    _ManualTimer.instances[-1].fire()

    with Session() as check:
        attachments = check.execute(
            select(TicketAttachment).where(TicketAttachment.ticket_id == ticket.id).order_by(TicketAttachment.created_at, TicketAttachment.id)
        ).scalars().all()
    assert len({row.telegram_archive_info_message_id for row in attachments}) == 1
    assert all(str(row.telegram_archive_message_id or "").strip() for row in attachments)
    assert len(state["send_message"]) == 1
    assert len(state["copy_message"]) == 10
    final_info_text = state["edit_message_text"][-1][0][2]
    assert all(f"• <code>file-{index}.pdf</code>" in final_info_text for index in range(10))


def test_repeated_media_group_finalizer_is_idempotent_for_summary_and_files(monkeypatch, tmp_path):
    _engine, Session = _session_bundle(tmp_path)
    session = Session()
    ticket, contact = _seed_ticket_context(session)
    _seed_existing_client_message(session, ticket.id)
    monkeypatch.setattr(telegram_attachments_service.settings, "attachment_storage_dir", str(tmp_path))
    monkeypatch.setattr(
        telegram_attachments_service,
        "get_file",
        lambda file_id, **_kwargs: {"result": {"file_path": f"docs/{file_id}.pdf", "file_unique_id": f"uniq-{file_id}"}},
    )
    monkeypatch.setattr(telegram_attachments_service, "_try_prefetch_attachment_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(telegram_attachments_service, "_staging_chat_id", lambda: "staging-chat")
    _run_media_group_archive_scheduler(monkeypatch, Session)
    state = {"send_message": 0, "copy_message": 0}
    monkeypatch.setattr(
        telegram_attachments_service,
        "send_message",
        lambda *_args, **_kwargs: state.__setitem__("send_message", state["send_message"] + 1) or {"result": {"message_id": 851}},
    )
    monkeypatch.setattr(telegram_attachments_service, "edit_message_text", lambda *_args, **_kwargs: {"result": True})
    monkeypatch.setattr(
        telegram_attachments_service,
        "copy_message",
        lambda *_args, **_kwargs: state.__setitem__("copy_message", state["copy_message"] + 1) or {"result": {"message_id": 950 + state["copy_message"]}},
    )

    for index in range(10):
        result = telegram_router.process_telegram_update(
            session,
            _document_payload(
                update_id=2450 + index,
                chat_id=contact.external_chat_id,
                user_id=contact.external_user_id,
                file_id=f"idem-{index}",
                filename=f"idem-{index}.pdf",
                media_group_id="mg-idem",
            ),
            notify_client=False,
        )
        assert result["ok"] is True

    _ManualTimer.instances[-1].fire()

    with Session() as check:
        attachments = check.execute(
            select(TicketAttachment).where(TicketAttachment.ticket_id == ticket.id).order_by(TicketAttachment.created_at, TicketAttachment.id)
        ).scalars().all()
        leader_id = attachments[0].id
        assert len({row.telegram_archive_info_message_id for row in attachments}) == 1
        assert all(str(row.telegram_archive_message_id or "").strip() for row in attachments)

    telegram_attachments_service.start_background_archive_inbound_attachment(leader_id)
    _ManualTimer.instances[-1].fire()

    with Session() as check:
        attachments = check.execute(
            select(TicketAttachment).where(TicketAttachment.ticket_id == ticket.id).order_by(TicketAttachment.created_at, TicketAttachment.id)
        ).scalars().all()
        assert len({row.telegram_archive_info_message_id for row in attachments}) == 1
        assert all(str(row.telegram_archive_message_id or "").strip() for row in attachments)

    assert state == {"send_message": 1, "copy_message": 10}


def test_media_group_archive_retries_temporary_telegram_error_and_keeps_all_files(monkeypatch, tmp_path):
    _engine, Session = _session_bundle(tmp_path)
    session = Session()
    ticket, contact = _seed_ticket_context(session)
    _seed_existing_client_message(session, ticket.id)
    monkeypatch.setattr(telegram_attachments_service.settings, "attachment_storage_dir", str(tmp_path))
    monkeypatch.setattr(
        telegram_attachments_service,
        "get_file",
        lambda file_id, **_kwargs: {"result": {"file_path": f"docs/{file_id}.pdf", "file_unique_id": f"uniq-{file_id}"}},
    )
    monkeypatch.setattr(telegram_attachments_service, "_try_prefetch_attachment_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(telegram_attachments_service, "_staging_chat_id", lambda: "staging-chat")
    _run_media_group_archive_scheduler(monkeypatch, Session)
    state = {"copy_message": [], "raised": False, "sleeps": []}
    monkeypatch.setattr(telegram_attachments_service, "send_message", lambda *_args, **_kwargs: {"result": {"message_id": 811}})
    monkeypatch.setattr(telegram_attachments_service, "edit_message_text", lambda *_args, **_kwargs: {"result": True})

    def _copy_message(*args, **kwargs):
        state["copy_message"].append((args, kwargs))
        if args[2] == "2404" and not state["raised"]:
            state["raised"] = True
            raise telegram_attachments_service.TelegramApiError("Too Many Requests: retry after 2")
        return {"result": {"message_id": 910 + len(state["copy_message"])}}

    monkeypatch.setattr(telegram_attachments_service, "copy_message", _copy_message)
    monkeypatch.setattr(telegram_attachments_service.time, "sleep", lambda seconds: state["sleeps"].append(seconds))

    for index in range(10):
        result = telegram_router.process_telegram_update(
            session,
            _document_payload(
                update_id=2400 + index,
                chat_id=contact.external_chat_id,
                user_id=contact.external_user_id,
                file_id=f"retry-file-{index}",
                filename=f"retry-{index}.pdf",
                media_group_id="mg-retry-copy",
            ),
            notify_client=False,
        )
        assert result["ok"] is True

    attachments = session.execute(
        select(TicketAttachment).where(TicketAttachment.ticket_id == ticket.id).order_by(TicketAttachment.created_at, TicketAttachment.id)
    ).scalars().all()
    assert len(attachments) == 10
    assert _ManualTimer.instances[-1].interval == 0.0
    _ManualTimer.instances[-1].fire()

    with Session() as check:
        attachments = check.execute(
            select(TicketAttachment).where(TicketAttachment.ticket_id == ticket.id).order_by(TicketAttachment.created_at, TicketAttachment.id)
        ).scalars().all()
    assert all(str(row.telegram_archive_message_id or "").strip() for row in attachments)
    assert len(state["copy_message"]) == 11
    assert state["sleeps"] == [2.0]


def test_media_group_summary_retries_before_any_attachment_copy(monkeypatch, tmp_path):
    _engine, Session = _session_bundle(tmp_path)
    session = Session()
    ticket, contact = _seed_ticket_context(session)
    _seed_existing_client_message(session, ticket.id)
    monkeypatch.setattr(telegram_attachments_service.settings, "attachment_storage_dir", str(tmp_path))
    monkeypatch.setattr(
        telegram_attachments_service,
        "get_file",
        lambda file_id, **_kwargs: {"result": {"file_path": f"docs/{file_id}.pdf", "file_unique_id": f"uniq-{file_id}"}},
    )
    monkeypatch.setattr(telegram_attachments_service, "_try_prefetch_attachment_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(telegram_attachments_service, "_staging_chat_id", lambda: "staging-chat")
    _run_media_group_archive_scheduler(monkeypatch, Session)
    state = {"order": [], "send_attempts": 0, "sleeps": []}
    monkeypatch.setattr(telegram_attachments_service, "edit_message_text", lambda *_args, **_kwargs: {"result": True})

    def _send_message(*_args, **_kwargs):
        state["send_attempts"] += 1
        if state["send_attempts"] == 1:
            state["order"].append("summary-attempt-1")
            raise telegram_attachments_service.TelegramApiError("Too Many Requests: retry after 2")
        if state["send_attempts"] == 2:
            state["order"].append("summary-attempt-2")
            raise telegram_attachments_service.TelegramApiError("Bad Gateway")
        state["order"].append("summary-success")
        return {"result": {"message_id": 812}}

    def _copy_message(_dst_chat_id, _src_chat_id, message_id, **_kwargs):
        state["order"].append(f"copy-{message_id}")
        return {"result": {"message_id": 910 + len(state['order'])}}

    monkeypatch.setattr(telegram_attachments_service, "send_message", _send_message)
    monkeypatch.setattr(telegram_attachments_service, "copy_message", _copy_message)
    monkeypatch.setattr(telegram_attachments_service.time, "sleep", lambda seconds: state["sleeps"].append(seconds))

    for index in range(2):
        result = telegram_router.process_telegram_update(
            session,
            _document_payload(
                update_id=2600 + index,
                chat_id=contact.external_chat_id,
                user_id=contact.external_user_id,
                file_id=f"summary-retry-{index}",
                filename=f"summary-retry-{index}.pdf",
                media_group_id="mg-summary-retry",
            ),
            notify_client=False,
        )
        assert result["ok"] is True

    assert _ManualTimer.instances[-1].interval == 30.0
    _ManualTimer.instances[-1].fire()

    with Session() as check:
        attachments = check.execute(
            select(TicketAttachment).where(TicketAttachment.ticket_id == ticket.id).order_by(TicketAttachment.created_at, TicketAttachment.id)
        ).scalars().all()
    assert len({row.telegram_archive_info_message_id for row in attachments}) == 1
    assert all(str(row.telegram_archive_message_id or "").strip() for row in attachments)
    assert state["order"] == [
        "summary-attempt-1",
        "summary-attempt-2",
        "summary-success",
        "copy-2600",
        "copy-2601",
    ]
    assert state["sleeps"] == [2.0, 1.0]


def test_media_group_does_not_copy_attachments_when_summary_creation_fails(monkeypatch, tmp_path):
    _engine, Session = _session_bundle(tmp_path)
    session = Session()
    ticket, contact = _seed_ticket_context(session)
    _seed_existing_client_message(session, ticket.id)
    monkeypatch.setattr(telegram_attachments_service.settings, "attachment_storage_dir", str(tmp_path))
    monkeypatch.setattr(
        telegram_attachments_service,
        "get_file",
        lambda file_id, **_kwargs: {"result": {"file_path": f"docs/{file_id}.pdf", "file_unique_id": f"uniq-{file_id}"}},
    )
    monkeypatch.setattr(telegram_attachments_service, "_try_prefetch_attachment_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(telegram_attachments_service, "_staging_chat_id", lambda: "staging-chat")
    _run_media_group_archive_scheduler(monkeypatch, Session)
    state = {"send_attempts": 0, "copy_message": 0, "sleeps": []}
    monkeypatch.setattr(telegram_attachments_service, "edit_message_text", lambda *_args, **_kwargs: {"result": True})

    def _send_message(*_args, **_kwargs):
        state["send_attempts"] += 1
        raise telegram_attachments_service.TelegramApiError("Too Many Requests: retry after 2")

    monkeypatch.setattr(telegram_attachments_service, "send_message", _send_message)
    monkeypatch.setattr(
        telegram_attachments_service,
        "copy_message",
        lambda *_args, **_kwargs: state.__setitem__("copy_message", state["copy_message"] + 1) or {"result": {"message_id": 950}},
    )
    monkeypatch.setattr(telegram_attachments_service.time, "sleep", lambda seconds: state["sleeps"].append(seconds))

    for index in range(2):
        result = telegram_router.process_telegram_update(
            session,
            _document_payload(
                update_id=2700 + index,
                chat_id=contact.external_chat_id,
                user_id=contact.external_user_id,
                file_id=f"summary-fail-{index}",
                filename=f"summary-fail-{index}.pdf",
                media_group_id="mg-summary-fail",
            ),
            notify_client=False,
        )
        assert result["ok"] is True

    initial_timer_count = len(_ManualTimer.instances)
    assert _ManualTimer.instances[-1].interval == 30.0
    _ManualTimer.instances[-1].fire()

    with Session() as check:
        attachments = check.execute(
            select(TicketAttachment).where(TicketAttachment.ticket_id == ticket.id).order_by(TicketAttachment.created_at, TicketAttachment.id)
        ).scalars().all()
    assert all(row.telegram_archive_info_message_id is None for row in attachments)
    assert all(row.telegram_archive_message_id is None for row in attachments)
    assert state["send_attempts"] == 3
    assert state["copy_message"] == 0
    assert state["sleeps"] == [2.0, 2.0]
    assert len(_ManualTimer.instances) == initial_timer_count + 1
    assert _ManualTimer.instances[-1].interval == 30.0
    assert _ManualTimer.instances[-1].cancelled is False


def test_media_group_archive_final_error_does_not_stop_other_files(monkeypatch, tmp_path, caplog):
    _engine, Session = _session_bundle(tmp_path)
    session = Session()
    ticket, contact = _seed_ticket_context(session)
    _seed_existing_client_message(session, ticket.id)
    monkeypatch.setattr(telegram_attachments_service.settings, "attachment_storage_dir", str(tmp_path))
    monkeypatch.setattr(
        telegram_attachments_service,
        "get_file",
        lambda file_id, **_kwargs: {"result": {"file_path": f"docs/{file_id}.pdf", "file_unique_id": f"uniq-{file_id}"}},
    )
    monkeypatch.setattr(telegram_attachments_service, "_try_prefetch_attachment_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(telegram_attachments_service, "_staging_chat_id", lambda: "staging-chat")
    monkeypatch.setattr(telegram_attachments_service.time, "sleep", lambda _seconds: None)
    _run_media_group_archive_scheduler(monkeypatch, Session)
    state = {"copy_message": []}
    monkeypatch.setattr(telegram_attachments_service, "send_message", lambda *_args, **_kwargs: {"result": {"message_id": 821}})
    monkeypatch.setattr(telegram_attachments_service, "edit_message_text", lambda *_args, **_kwargs: {"result": True})

    def _copy_message(*args, **kwargs):
        state["copy_message"].append((args, kwargs))
        if args[2] == "2505":
            raise telegram_attachments_service.TelegramApiError("Bad Gateway")
        return {"result": {"message_id": 920 + len(state["copy_message"])}}

    monkeypatch.setattr(telegram_attachments_service, "copy_message", _copy_message)

    with caplog.at_level("WARNING"):
        for index in range(10):
            result = telegram_router.process_telegram_update(
                session,
                _document_payload(
                    update_id=2500 + index,
                    chat_id=contact.external_chat_id,
                    user_id=contact.external_user_id,
                    file_id=f"partial-file-{index}",
                    filename=f"partial-{index}.pdf",
                    media_group_id="mg-final-error",
                ),
                notify_client=False,
            )
            assert result["ok"] is True

    assert _ManualTimer.instances[-1].interval == 0.0
    _ManualTimer.instances[-1].fire()

    with Session() as check:
        attachments = check.execute(
            select(TicketAttachment).where(TicketAttachment.ticket_id == ticket.id).order_by(TicketAttachment.created_at, TicketAttachment.id)
        ).scalars().all()
    failed = [row for row in attachments if row.original_filename == "partial-5.pdf"]
    delivered = [row for row in attachments if row.original_filename != "partial-5.pdf"]
    assert len(failed) == 1
    assert failed[0].telegram_archive_message_id is None
    assert len(delivered) == 9
    assert all(str(row.telegram_archive_message_id or "").strip() for row in delivered)
    assert len(state["copy_message"]) == 12
    assert any("Failed to archive inbound Telegram attachment" in record.getMessage() for record in caplog.records)


def test_mixed_media_group_skips_too_large_file_but_keeps_single_archive_group(monkeypatch, tmp_path):
    _engine, Session = _session_bundle(tmp_path)
    session = Session()
    ticket, contact = _seed_ticket_context(session)
    _seed_existing_client_message(session, ticket.id)
    monkeypatch.setattr(telegram_attachments_service.settings, "attachment_storage_dir", str(tmp_path))
    monkeypatch.setattr(
        telegram_attachments_service,
        "get_file",
        lambda file_id, **_kwargs: {"result": {"file_path": f"docs/{file_id}.pdf", "file_unique_id": f"uniq-{file_id}"}},
    )
    monkeypatch.setattr(telegram_attachments_service, "_try_prefetch_attachment_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(telegram_attachments_service, "_staging_chat_id", lambda: "staging-chat")
    _run_media_group_archive_scheduler(monkeypatch, Session)
    warnings = {"too_large": 0}
    state = {"send_message": [], "edit_message_text": [], "copy_message": []}
    monkeypatch.setattr(telegram_router, "send_too_large_attachment_client_messages", lambda *_args, **_kwargs: warnings.__setitem__("too_large", warnings["too_large"] + 1))
    monkeypatch.setattr(
        telegram_attachments_service,
        "send_message",
        lambda *args, **kwargs: state["send_message"].append((args, kwargs)) or {"result": {"message_id": 701}},
    )
    monkeypatch.setattr(
        telegram_attachments_service,
        "edit_message_text",
        lambda *args, **kwargs: state["edit_message_text"].append((args, kwargs)) or {"result": True},
    )
    monkeypatch.setattr(
        telegram_attachments_service,
        "copy_message",
        lambda *args, **kwargs: state["copy_message"].append((args, kwargs)) or {"result": {"message_id": 800 + len(state["copy_message"]) }},
    )

    payloads = [
        _document_payload(update_id=2201, chat_id=contact.external_chat_id, user_id=contact.external_user_id, file_id="file-a2", filename="A.csv", media_group_id="mg-43"),
        _document_payload(update_id=2202, chat_id=contact.external_chat_id, user_id=contact.external_user_id, file_id="file-b2", filename="B.csv", media_group_id="mg-43"),
        _document_payload(update_id=2203, chat_id=contact.external_chat_id, user_id=contact.external_user_id, file_id="file-big", filename="BIG.7z", file_size=25 * 1024 * 1024, media_group_id="mg-43"),
        _document_payload(update_id=2204, chat_id=contact.external_chat_id, user_id=contact.external_user_id, file_id="file-d2", filename="D.xlsx", media_group_id="mg-43"),
    ]
    for payload in payloads:
        result = telegram_router.process_telegram_update(session, payload, notify_client=False)
        assert result["ok"] is True

    with Session() as check:
        attachments = check.execute(
            select(TicketAttachment).where(TicketAttachment.ticket_id == ticket.id).order_by(TicketAttachment.created_at, TicketAttachment.id)
        ).scalars().all()
    assert len(attachments) == 3
    assert {row.original_filename for row in attachments} == {"A.csv", "B.csv", "D.xlsx"}
    assert {row.telegram_media_group_id for row in attachments} == {"mg-43"}
    assert len(state["send_message"]) == 0
    assert len(state["copy_message"]) == 0
    _ManualTimer.instances[-1].fire()

    with Session() as check:
        attachments = check.execute(
            select(TicketAttachment).where(TicketAttachment.ticket_id == ticket.id).order_by(TicketAttachment.created_at, TicketAttachment.id)
        ).scalars().all()
    assert len({row.telegram_archive_info_message_id for row in attachments}) == 1
    assert len(state["send_message"]) == 1
    assert len(state["copy_message"]) == 3
    assert all(args[2] != "2203" for args, _kwargs in state["copy_message"])
    final_info_text = state["edit_message_text"][-1][0][2]
    assert "BIG.7z" not in final_info_text
    assert "• <code>A.csv</code>" in final_info_text
    assert "• <code>B.csv</code>" in final_info_text
    assert "• <code>D.xlsx</code>" in final_info_text
    assert warnings["too_large"] == 1


def test_concurrent_background_archive_start_does_not_create_duplicate(monkeypatch, tmp_path):
    _engine, Session = _session_bundle(tmp_path)
    session = Session()
    ticket, _contact = _seed_ticket_context(session)
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.CLIENT.value,
        author_user_id=None,
        body="Attachment inbound",
        telegram_message_id="3004",
        created_at=datetime.utcnow(),
    )
    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=message.id,
        source="document",
        telegram_file_id="telegram-file-4",
        telegram_file_unique_id="client-uniq-4",
        telegram_file_path="docs/inbound-4.pdf",
        original_filename="report-4.pdf",
        mime_type="application/pdf",
        file_size=888,
        local_path=str(tmp_path / "report-4.pdf"),
        created_at=datetime.utcnow(),
    )
    session.add_all([message, attachment])
    session.commit()

    monkeypatch.setattr(telegram_attachments_service, "SessionLocal", Session)
    monkeypatch.setattr(telegram_attachments_service, "_staging_chat_id", lambda: "staging-chat")
    release_send = threading.Event()
    calls = {"copy_message": 0}
    def _copy_message(*_args, **_kwargs):
        calls["copy_message"] += 1
        release_send.wait(timeout=5)
        return {"result": {"message_id": 904}}
    monkeypatch.setattr(telegram_attachments_service, "copy_message", _copy_message)

    first = threading.Thread(target=telegram_attachments_service._archive_inbound_attachment_worker, args=(attachment.id,))
    second = threading.Thread(target=telegram_attachments_service._archive_inbound_attachment_worker, args=(attachment.id,))
    first.start()
    second.start()
    release_send.set()
    first.join(timeout=5)
    second.join(timeout=5)

    with Session() as check:
        saved = check.get(TicketAttachment, attachment.id)
        assert saved is not None
        assert saved.telegram_archive_message_id == "904"
    assert calls["copy_message"] == 1


def test_media_group_parallel_workers_do_not_create_second_info_card_or_duplicate_files(monkeypatch, tmp_path):
    _engine, Session = _session_bundle(tmp_path)
    session = Session()
    ticket, _contact = _seed_ticket_context(session)
    media_group_id = "mg-99"
    attachments: list[TicketAttachment] = []
    for index, filename in enumerate(["A.csv", "B.csv", "D.xlsx"], start=1):
        message = TicketMessage(
            id=str(uuid.uuid4()),
            ticket_id=ticket.id,
            message_type=MessageType.CLIENT.value,
            author_user_id=None,
            body=f"Attachment {index}",
            telegram_message_id=str(4000 + index),
            created_at=datetime.utcnow(),
        )
        attachment = TicketAttachment(
            id=str(uuid.uuid4()),
            ticket_id=ticket.id,
            message_id=message.id,
            source="document",
            telegram_file_id=f"media-file-{index}",
            telegram_file_unique_id=f"media-uniq-{index}",
            telegram_file_path=f"docs/media-file-{index}.pdf",
            telegram_media_group_id=media_group_id,
            original_filename=filename,
            mime_type="application/pdf",
            file_size=123,
            local_path=str(tmp_path / f"{filename}.pdf"),
            created_at=datetime.utcnow(),
        )
        session.add_all([message, attachment])
        attachments.append(attachment)
    session.commit()

    monkeypatch.setattr(telegram_attachments_service, "SessionLocal", Session)
    monkeypatch.setattr(telegram_attachments_service, "_staging_chat_id", lambda: "staging-chat")
    release_send = threading.Event()
    calls = {"send_message": 0, "send_document_by_file_id": 0}
    monkeypatch.setattr(
        telegram_attachments_service,
        "send_message",
        lambda *_args, **_kwargs: calls.__setitem__("send_message", calls["send_message"] + 1) or {"result": {"message_id": 990}},
    )
    monkeypatch.setattr(telegram_attachments_service, "edit_message_text", lambda *_args, **_kwargs: {"result": True})
    def _copy_message(*_args, **_kwargs):
        calls["send_document_by_file_id"] += 1
        release_send.wait(timeout=5)
        return {"result": {"message_id": 1000 + calls["send_document_by_file_id"]}}
    monkeypatch.setattr(telegram_attachments_service, "copy_message", _copy_message)

    workers = [threading.Thread(target=telegram_attachments_service._archive_inbound_attachment_worker, args=(attachment.id,)) for attachment in attachments]
    for worker in workers:
        worker.start()
    release_send.set()
    for worker in workers:
        worker.join(timeout=5)

    with Session() as check:
        rows = check.execute(
            select(TicketAttachment).where(TicketAttachment.ticket_id == ticket.id).order_by(TicketAttachment.created_at, TicketAttachment.id)
        ).scalars().all()
        assert len({row.telegram_archive_info_message_id for row in rows}) == 1
        assert all(str(row.telegram_archive_message_id or "").strip() for row in rows)
    assert calls["send_message"] == 1
    assert calls["send_document_by_file_id"] == 3


def test_media_group_workers_for_same_ticket_serialize_external_archive_order(monkeypatch, tmp_path):
    _engine, Session = _session_bundle(tmp_path)
    session = Session()
    ticket, _contact = _seed_ticket_context(session)
    groups = {
        "mg-a": [("A-1.pdf", "5101"), ("A-2.pdf", "5102")],
        "mg-b": [("B-1.pdf", "5201"), ("B-2.pdf", "5202")],
    }
    attachments_by_group: dict[str, list[TicketAttachment]] = {}
    base_time = datetime(2026, 8, 31, 12, 0, 0)
    for group_index, (media_group_id, files) in enumerate(groups.items()):
        group_rows: list[TicketAttachment] = []
        for file_index, (filename, telegram_message_id) in enumerate(files):
            created_at = base_time + timedelta(minutes=group_index, seconds=file_index)
            message = TicketMessage(
                id=str(uuid.uuid4()),
                ticket_id=ticket.id,
                message_type=MessageType.CLIENT.value,
                author_user_id=None,
                body=filename,
                telegram_message_id=telegram_message_id,
                created_at=created_at,
            )
            attachment = TicketAttachment(
                id=str(uuid.uuid4()),
                ticket_id=ticket.id,
                message_id=message.id,
                source="document",
                telegram_file_id=f"file-{telegram_message_id}",
                telegram_file_unique_id=f"uniq-{telegram_message_id}",
                telegram_file_path=f"docs/{telegram_message_id}.pdf",
                telegram_media_group_id=media_group_id,
                original_filename=filename,
                mime_type="application/pdf",
                file_size=123,
                local_path=str(tmp_path / filename),
                created_at=created_at,
            )
            session.add_all([message, attachment])
            group_rows.append(attachment)
        attachments_by_group[media_group_id] = group_rows
    session.commit()

    monkeypatch.setattr(telegram_attachments_service, "SessionLocal", Session)
    monkeypatch.setattr(telegram_attachments_service, "_staging_chat_id", lambda: "staging-chat")
    with telegram_attachments_service._MEDIA_GROUP_ARCHIVE_LOCK:
        telegram_attachments_service._MEDIA_GROUP_ARCHIVE.clear()
        for media_group_id in groups:
            telegram_attachments_service._MEDIA_GROUP_ARCHIVE[(ticket.id, media_group_id)] = telegram_attachments_service._InboundMediaGroupArchiveState(
                generation=1,
                timer=_ManualTimer(0.0, lambda: None),
            )
    with telegram_attachments_service._TICKET_MEDIA_GROUP_ARCHIVE_LOCKS_LOCK:
        telegram_attachments_service._TICKET_MEDIA_GROUP_ARCHIVE_LOCKS.clear()

    order: list[str] = []
    first_group_entered = threading.Event()
    allow_first_group_finish = threading.Event()

    def _send_message(_chat_id, text, **_kwargs):
        group = "A" if "A-1.pdf" in text else "B"
        order.append(f"summary-{group}")
        return {"result": {"message_id": 900 if group == "A" else 901}}

    def _copy_message(_dst_chat_id, _src_chat_id, message_id, **_kwargs):
        group = "A" if str(message_id) in {"5101", "5102"} else "B"
        order.append(f"copy-{group}-{message_id}")
        if str(message_id) == "5101":
            first_group_entered.set()
            allow_first_group_finish.wait(timeout=5)
        return {"result": {"message_id": 1000 + len(order)}}

    monkeypatch.setattr(telegram_attachments_service, "send_message", _send_message)
    monkeypatch.setattr(telegram_attachments_service, "copy_message", _copy_message)
    monkeypatch.setattr(telegram_attachments_service, "edit_message_text", lambda *_args, **_kwargs: {"ok": True})

    first = threading.Thread(target=telegram_attachments_service._archive_inbound_media_group_worker, args=(ticket.id, "mg-a", 1))
    second = threading.Thread(target=telegram_attachments_service._archive_inbound_media_group_worker, args=(ticket.id, "mg-b", 1))
    first.start()
    assert first_group_entered.wait(timeout=5) is True
    second.start()
    time.sleep(0.2)
    assert order == ["summary-A", "copy-A-5101"]
    allow_first_group_finish.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert order == [
        "summary-A",
        "copy-A-5101",
        "copy-A-5102",
        "summary-B",
        "copy-B-5201",
        "copy-B-5202",
    ]
    with Session() as check:
        rows = check.execute(
            select(TicketAttachment).where(TicketAttachment.ticket_id == ticket.id).order_by(TicketAttachment.created_at, TicketAttachment.id)
        ).scalars().all()
        assert len(rows) == 4
        assert all(str(row.telegram_archive_message_id or "").strip() for row in rows)
        assert len({row.telegram_archive_info_message_id for row in rows if row.telegram_media_group_id == "mg-a"}) == 1
        assert len({row.telegram_archive_info_message_id for row in rows if row.telegram_media_group_id == "mg-b"}) == 1


def test_unexpected_background_archive_exception_clears_pending_token_for_retry(monkeypatch, tmp_path):
    _engine, Session = _session_bundle(tmp_path)
    session = Session()
    ticket, _contact = _seed_ticket_context(session)
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.CLIENT.value,
        author_user_id=None,
        body="Attachment inbound",
        telegram_message_id="3005",
        created_at=datetime.utcnow(),
    )
    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=message.id,
        source="document",
        telegram_file_id="telegram-file-5",
        telegram_file_unique_id="client-uniq-5",
        telegram_file_path="docs/inbound-5.pdf",
        original_filename="report-5.pdf",
        mime_type="application/pdf",
        file_size=999,
        local_path=str(tmp_path / "report-5.pdf"),
        created_at=datetime.utcnow(),
    )
    session.add_all([message, attachment])
    session.commit()

    monkeypatch.setattr(telegram_attachments_service, "SessionLocal", Session)
    monkeypatch.setattr(telegram_attachments_service, "_staging_chat_id", lambda: "staging-chat")
    first_call = {"raised": False}

    def _copy_message(*_args, **_kwargs):
        if not first_call["raised"]:
            first_call["raised"] = True
            raise ValueError("unexpected boom")
        return {"result": {"message_id": 905}}

    monkeypatch.setattr(telegram_attachments_service, "copy_message", _copy_message)

    telegram_attachments_service._archive_inbound_attachment_worker(attachment.id)
    with Session() as check:
        failed = check.get(TicketAttachment, attachment.id)
        assert failed is not None
        assert failed.telegram_archive_message_id is None

    telegram_attachments_service._archive_inbound_attachment_worker(attachment.id)
    with Session() as check:
        retried = check.get(TicketAttachment, attachment.id)
        assert retried is not None
        assert retried.telegram_archive_message_id == "905"


def test_unexpected_group_info_exception_clears_pending_info_token_for_retry(monkeypatch, tmp_path):
    _engine, Session = _session_bundle(tmp_path)
    session = Session()
    ticket, _contact = _seed_ticket_context(session)
    attachments: list[TicketAttachment] = []
    for index, filename in enumerate(["A.csv", "B.csv"], start=1):
        message = TicketMessage(
            id=str(uuid.uuid4()),
            ticket_id=ticket.id,
            message_type=MessageType.CLIENT.value,
            author_user_id=None,
            body=f"Attachment {index}",
            telegram_message_id=str(5000 + index),
            created_at=datetime.utcnow(),
        )
        attachment = TicketAttachment(
            id=str(uuid.uuid4()),
            ticket_id=ticket.id,
            message_id=message.id,
            source="document",
            telegram_file_id=f"group-file-{index}",
            telegram_file_unique_id=f"group-uniq-{index}",
            telegram_file_path=f"docs/group-file-{index}.pdf",
            telegram_media_group_id="mg-retry",
            original_filename=filename,
            mime_type="application/pdf",
            file_size=123,
            local_path=str(tmp_path / f"{filename}.pdf"),
            created_at=datetime.utcnow(),
        )
        session.add_all([message, attachment])
        attachments.append(attachment)
    session.commit()

    monkeypatch.setattr(telegram_attachments_service, "SessionLocal", Session)
    monkeypatch.setattr(telegram_attachments_service, "_staging_chat_id", lambda: "staging-chat")
    state = {"raised": False, "send_message": 0}

    def _send_message(*_args, **_kwargs):
        state["send_message"] += 1
        if not state["raised"]:
            state["raised"] = True
            raise ValueError("unexpected group info boom")
        return {"result": {"message_id": 991}}

    sent_files = {"count": 0}
    monkeypatch.setattr(telegram_attachments_service, "send_message", _send_message)
    monkeypatch.setattr(telegram_attachments_service, "edit_message_text", lambda *_args, **_kwargs: {"result": True})
    monkeypatch.setattr(
        telegram_attachments_service,
        "copy_message",
        lambda *_args, **_kwargs: sent_files.__setitem__("count", sent_files["count"] + 1) or {"result": {"message_id": 1200 + sent_files["count"]}},
    )

    telegram_attachments_service._archive_inbound_attachment_worker(attachments[0].id)
    with Session() as check:
        failed_rows = check.execute(
            select(TicketAttachment).where(TicketAttachment.ticket_id == ticket.id).order_by(TicketAttachment.created_at, TicketAttachment.id)
        ).scalars().all()
        assert all(row.telegram_archive_info_message_id is None for row in failed_rows)

    telegram_attachments_service._archive_inbound_attachment_worker(attachments[1].id)
    with Session() as check:
        retried_rows = check.execute(
            select(TicketAttachment).where(TicketAttachment.ticket_id == ticket.id).order_by(TicketAttachment.created_at, TicketAttachment.id)
        ).scalars().all()
        assert len({row.telegram_archive_info_message_id for row in retried_rows}) == 1
        assert retried_rows[0].telegram_archive_info_message_id == "991"


def test_missing_source_message_is_best_effort_failure(monkeypatch, tmp_path):
    _engine, Session = _session_bundle(tmp_path)
    session = Session()
    ticket, _contact = _seed_ticket_context(session)
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.CLIENT.value,
        author_user_id=None,
        body="Attachment inbound",
        telegram_message_id=None,
        created_at=datetime.utcnow(),
    )
    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=message.id,
        source="photo",
        telegram_file_id="photo-file-1",
        telegram_file_unique_id="uniq-photo-file-1",
        telegram_file_path="photos/photo-file-1.jpg",
        original_filename="photo.jpg",
        mime_type="image/jpeg",
        file_size=123,
        local_path=str(tmp_path / "photo.jpg"),
        created_at=datetime.utcnow(),
    )
    session.add_all([message, attachment])
    session.commit()

    monkeypatch.setattr(telegram_attachments_service, "SessionLocal", Session)
    monkeypatch.setattr(telegram_attachments_service, "_staging_chat_id", lambda: "staging-chat")
    monkeypatch.setattr(
        telegram_attachments_service,
        "copy_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("copy must not run without source message")),
    )

    telegram_attachments_service._archive_inbound_attachment_worker(attachment.id)

    with Session() as check:
        saved = check.get(TicketAttachment, attachment.id)
        assert saved is not None
        assert saved.telegram_archive_message_id is None


def test_missing_source_chat_is_best_effort_failure(monkeypatch, tmp_path):
    _engine, Session = _session_bundle(tmp_path)
    session = Session()
    ticket, contact = _seed_ticket_context(session)
    contact.external_chat_id = ""
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.CLIENT.value,
        author_user_id=None,
        body="Attachment inbound",
        telegram_message_id="3999",
        created_at=datetime.utcnow(),
    )
    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=message.id,
        source="video",
        telegram_file_id="video-file-1",
        telegram_file_unique_id="uniq-video-file-1",
        telegram_file_path="video/video-file-1.mp4",
        original_filename="clip.mp4",
        mime_type="video/mp4",
        file_size=456,
        local_path=str(tmp_path / "clip.mp4"),
        created_at=datetime.utcnow(),
    )
    session.add_all([message, attachment])
    session.commit()

    monkeypatch.setattr(telegram_attachments_service, "SessionLocal", Session)
    monkeypatch.setattr(telegram_attachments_service, "_staging_chat_id", lambda: "staging-chat")
    monkeypatch.setattr(
        telegram_attachments_service,
        "copy_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("copy must not run without source chat")),
    )

    telegram_attachments_service._archive_inbound_attachment_worker(attachment.id)

    with Session() as check:
        saved = check.get(TicketAttachment, attachment.id)
        assert saved is not None
        assert saved.telegram_archive_message_id is None
