from __future__ import annotations

from datetime import datetime
from pathlib import Path
import threading
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import Client, ClientProfile, Company, MessageType, TelegramContact, Ticket, TicketAttachment, TicketMessage, TicketStatus, TicketWorkflowType
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

    def _telegram_request(method, payload=None, timeout=30):
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


def test_media_group_archives_three_valid_files_under_one_info_card(monkeypatch, tmp_path):
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
    _run_inline_archive_worker(monkeypatch, Session)
    state = {"send_message": [], "edit_message_text": [], "send_document_by_file_id": []}
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
        lambda *args, **kwargs: state["send_document_by_file_id"].append((args, kwargs)) or {"result": {"message_id": 600 + len(state["send_document_by_file_id"]) }},
    )

    payloads = [
        _document_payload(update_id=2101, chat_id=contact.external_chat_id, user_id=contact.external_user_id, file_id="file-a", filename="A.csv", media_group_id="mg-42"),
        _document_payload(update_id=2102, chat_id=contact.external_chat_id, user_id=contact.external_user_id, file_id="file-b", filename="B.csv", media_group_id="mg-42"),
        _document_payload(update_id=2103, chat_id=contact.external_chat_id, user_id=contact.external_user_id, file_id="file-d", filename="D.xlsx", media_group_id="mg-42"),
    ]
    for payload in payloads:
        result = telegram_router.process_telegram_update(session, payload, notify_client=False)
        assert result["ok"] is True

    attachments = session.execute(
        select(TicketAttachment).where(TicketAttachment.ticket_id == ticket.id).order_by(TicketAttachment.created_at, TicketAttachment.id)
    ).scalars().all()
    assert len(attachments) == 3
    assert {row.original_filename for row in attachments} == {"A.csv", "B.csv", "D.xlsx"}
    assert {row.telegram_media_group_id for row in attachments} == {"mg-42"}
    assert len({row.telegram_archive_info_message_id for row in attachments}) == 1
    assert attachments[0].telegram_archive_info_message_id == "501"
    assert len(state["send_message"]) == 1
    assert len(state["send_document_by_file_id"]) == 3
    assert all(kwargs.get("caption") == "" for _args, kwargs in state["send_document_by_file_id"])
    final_info_text = state["edit_message_text"][-1][0][2]
    assert "• A.csv" in final_info_text
    assert "• B.csv" in final_info_text
    assert "• D.xlsx" in final_info_text


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
    _run_inline_archive_worker(monkeypatch, Session)
    warnings = {"too_large": 0}
    state = {"send_message": [], "edit_message_text": [], "send_document_by_file_id": []}
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
        lambda *args, **kwargs: state["send_document_by_file_id"].append((args, kwargs)) or {"result": {"message_id": 800 + len(state["send_document_by_file_id"]) }},
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

    attachments = session.execute(
        select(TicketAttachment).where(TicketAttachment.ticket_id == ticket.id).order_by(TicketAttachment.created_at, TicketAttachment.id)
    ).scalars().all()
    assert len(attachments) == 3
    assert {row.original_filename for row in attachments} == {"A.csv", "B.csv", "D.xlsx"}
    assert {row.telegram_media_group_id for row in attachments} == {"mg-43"}
    assert len({row.telegram_archive_info_message_id for row in attachments}) == 1
    assert len(state["send_message"]) == 1
    assert len(state["send_document_by_file_id"]) == 3
    assert all(args[2] != "2203" for args, _kwargs in state["send_document_by_file_id"])
    final_info_text = state["edit_message_text"][-1][0][2]
    assert "BIG.7z" not in final_info_text
    assert "• A.csv" in final_info_text
    assert "• B.csv" in final_info_text
    assert "• D.xlsx" in final_info_text
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
