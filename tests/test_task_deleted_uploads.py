from types import SimpleNamespace
import os
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from api.app.auth import get_current_user
from api.app.db import Base, get_db
from api.app.models import Client, Company, TelegramContact, Ticket, TicketAttachmentPreload, TicketMessage, User
from api.app.routers import tickets
from api.app.services import telegram_outbound_delivery as outbound
from api.app.services.ticket_lifecycle import task_has_active_attachment_uploads
from test_ticket_messages_outbound import (_threaded_session_bundle, _save_async_task_reply,
                                           _messages, _attachments)


@pytest.fixture(params=["sqlite", "postgresql"])
def database(request, tmp_path):
    admin = None
    if request.param == "sqlite":
        engine, factory, db = _threaded_session_bundle(tmp_path)
    else:
        url = os.environ.get("TICKET_SEARCH_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Set TICKET_SEARCH_TEST_DATABASE_URL for isolated PostgreSQL schemas")
        admin = create_engine(url)
        schema = "deleted_uploads_" + uuid.uuid4().hex
        with admin.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {schema}"))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, autoflush=False)
        db = factory()
    try:
        yield engine, factory, db
    finally:
        db.close()
        engine.dispose()
        if admin is not None:
            with admin.begin() as connection:
                connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
            admin.dispose()


@pytest.fixture
def case(monkeypatch, tmp_path, database):
    _engine, factory, db = database
    db.add(Company(id="company", name="Synthetic"))
    db.flush()
    db.add(Client(id="client", company_id="company", display_name="Client"))
    db.add(User(id="agent-1", company_id="company", role="agent", full_name="Agent"))
    db.flush()
    ticket = Ticket(id=str(uuid.uuid4()), ticket_number=1, company_id="company", client_id="client",
                    workflow_type="task", assignee_id="agent-1", status="pending", subject="Task")
    db.add(ticket)
    db.add(TelegramContact(id="contact", company_id="company", client_id="client", external_chat_id="chat", external_user_id="user"))
    db.flush()
    db.add(TicketMessage(id=str(uuid.uuid4()), ticket_id=ticket.id, message_type="client", body="Request"))
    keys = []
    for index in range(3):
        path = tmp_path / f"file-{index}.pdf"
        path.write_bytes(b"pdf")
        key = str(uuid.uuid4())
        keys.append(key)
        db.add(TicketAttachmentPreload(id=key, ticket_id=ticket.id, user_id="agent-1",
            original_filename=path.name, mime_type="application/pdf", local_path=str(path), file_size=3,
            status="uploading", archive_group_id="shared-group", telegram_staging_info_message_id="card"))
    db.commit()
    ticket_id = ticket.id
    db.close()
    _save_async_task_reply(factory, ticket_id, keys[:2])
    _save_async_task_reply(factory, ticket_id, keys[2:])
    with factory() as check:
        answers = [message for message in _messages(check, ticket_id) if message.message_type == "agent"]
        deleted_id, other_id = [message.id for message in answers]
        attachment_ids = [attachment.id for attachment in _attachments(check, deleted_id)]
    remote = {"card"}
    deleted = []
    delivered = []
    staged = []
    monkeypatch.setattr(outbound, "SessionLocal", factory)
    monkeypatch.setattr(outbound.settings, "telegram_staging_chat_id", "technical")
    monkeypatch.setattr(outbound, "_best_effort_edit_group_archive_message", lambda **kwargs: None)

    def delete_remote(chat_id, message_id):
        assert chat_id == "technical"
        deleted.append(str(message_id))
        remote.discard(str(message_id))
        return {"ok": True}

    def stage(*, preload):
        staged.append(preload.id)
        remote.add("archive-" + preload.id)
        return "file-" + preload.id, "unique-" + preload.id, "archive-" + preload.id

    monkeypatch.setattr(outbound, "delete_message", delete_remote)
    monkeypatch.setattr(outbound, "_send_staging_document", stage)
    monkeypatch.setattr(outbound, "send_document_by_file_id", lambda *args, **kwargs: delivered.append("unexpected-client-file"))
    monkeypatch.setattr(tickets, "_send_to_telegram_client", lambda *args, **kwargs: delivered.append("text"))
    monkeypatch.setattr(tickets, "_send_attachment_to_telegram_client", lambda db, ticket, attachment, **kwargs: delivered.append(attachment.id))
    app = FastAPI()
    app.include_router(tickets.router)

    def database():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="agent-1", role="agent")
    with TestClient(app) as http:
        yield SimpleNamespace(factory=factory, http=http, ticket_id=ticket_id, message_id=deleted_id,
            other_id=other_id, keys=keys, attachment_ids=attachment_ids, remote=remote, deleted=deleted,
            delivered=delivered, staged=staged, stage=stage,
            delete=lambda: http.delete(f"/api/v1/tickets/{ticket_id}/messages/{deleted_id}"))


@pytest.mark.parametrize("when", ["before", "during", "ready"])
def test_delete_multiple_files_keeps_other_message_archive_and_unblocks_close(case, monkeypatch, when):
    # A different reply in the same archive group is already prepared.
    outbound.deliver_attachment_preload(preload_id=case.keys[2])
    if when == "ready":
        for key in case.keys[:2]:
            outbound.deliver_attachment_preload(preload_id=key)
    if when == "during":
        def stage_and_delete(*, preload):
            assert case.delete().status_code == 200
            # Deletion must unblock close before Telegram's upload returns.
            assert case.http.get(f"/api/v1/tickets/{case.ticket_id}").json()["has_active_attachment_uploads"] is False
            return case.stage(preload=preload)
        monkeypatch.setattr(outbound, "_send_staging_document", stage_and_delete)
        outbound.deliver_attachment_preload(preload_id=case.keys[0])
    else:
        assert case.delete().status_code == 200
    assert case.delete().status_code == 200
    for _ in range(2):
        for key in case.keys[:2]:
            outbound.deliver_attachment_preload(preload_id=key)
    with case.factory() as check:
        assert check.get(TicketMessage, case.message_id).message_status == "deleted"
        assert all(check.get(TicketAttachmentPreload, key) is None for key in case.keys[:2])
        assert check.get(TicketMessage, case.other_id).message_status != "deleted"
        other = check.get(TicketAttachmentPreload, case.keys[2])
        assert other.status == "ready"
        assert task_has_active_attachment_uploads(check, check.get(Ticket, case.ticket_id)) is False
        other_attachment_id = other.final_attachment_id
    assert case.remote == {"card", "archive-" + case.keys[2]}
    assert case.delivered == []
    assert case.http.post(f"/api/v1/tickets/{case.ticket_id}/close").status_code == 200
    assert case.delivered == ["text", other_attachment_id]


def test_other_reply_and_unclaimed_editor_preload_still_block(case):
    assert case.delete().status_code == 200
    card_url = f"/api/v1/tickets/{case.ticket_id}"
    assert case.http.get(card_url).json()["has_active_attachment_uploads"] is True
    outbound.deliver_attachment_preload(preload_id=case.keys[2])
    assert case.http.get(card_url).json()["has_active_attachment_uploads"] is False
    with case.factory() as db:
        db.add(TicketAttachmentPreload(id="editor", ticket_id=case.ticket_id, user_id="agent-1",
            original_filename="editor.pdf", local_path="editor.pdf", status="uploading"))
        db.commit()
    assert case.http.get(card_url).json()["has_active_attachment_uploads"] is True
    assert case.http.post(card_url + "/close").status_code == 409


def test_legacy_deleted_message_does_not_block_even_before_cancel_cleanup(case):
    outbound.deliver_attachment_preload(preload_id=case.keys[2])
    with case.factory() as db:
        db.get(TicketMessage, case.message_id).message_status = "deleted"
        db.commit()
        assert task_has_active_attachment_uploads(db, db.get(Ticket, case.ticket_id)) is False
    for key in case.keys[:2]:
        outbound.deliver_attachment_preload(preload_id=key)
    assert case.staged == [case.keys[2]]
    assert case.remote == {"card", "archive-" + case.keys[2]}


def test_repeat_delete_also_cleans_a_previously_deleted_ready_answer(case):
    for key in case.keys:
        outbound.deliver_attachment_preload(preload_id=key)
    with case.factory() as db:
        db.get(TicketMessage, case.message_id).message_status = "deleted"
        db.commit()
    assert case.delete().status_code == 200
    assert case.delete().status_code == 200
    assert case.remote == {"card", "archive-" + case.keys[2]}
    assert case.delivered == []


def test_late_file_is_removed_when_another_worker_finished_and_preload_row_was_deleted(case, monkeypatch):
    outbound.deliver_attachment_preload(preload_id=case.keys[2])
    calls = 0

    def interleaved_stage(*, preload):
        nonlocal calls
        calls += 1
        if calls == 1:
            outbound.deliver_attachment_preload(preload_id=preload.id)
            assert case.delete().status_code == 200
            case.remote.add("late-file")
            return "late-id", "late-unique", "late-file"
        return case.stage(preload=preload)

    monkeypatch.setattr(outbound, "_send_staging_document", interleaved_stage)
    outbound.deliver_attachment_preload(preload_id=case.keys[0])
    assert "late-file" in case.deleted
    assert case.remote == {"card", "archive-" + case.keys[2]}
    assert case.delivered == []


def test_last_deleted_answer_removes_archive_card_and_all_its_files(case):
    for key in case.keys:
        outbound.deliver_attachment_preload(preload_id=key)
    assert case.http.delete(f"/api/v1/tickets/{case.ticket_id}/messages/{case.other_id}").status_code == 200
    assert "card" in case.remote
    assert case.delete().status_code == 200
    assert case.delete().status_code == 200
    for key in case.keys:
        outbound.deliver_attachment_preload(preload_id=key)
    assert case.remote == set()
    assert case.delivered == []
    assert case.http.get(f"/api/v1/tickets/{case.ticket_id}").json()["has_active_attachment_uploads"] is False


def test_queued_attachment_worker_cleans_its_late_file_without_client_delivery(case, monkeypatch):
    outbound.deliver_attachment_preload(preload_id=case.keys[2])

    def stage_and_delete(**kwargs):
        assert case.delete().status_code == 200
        case.remote.add("late-generic-file")
        return "generic-id", "generic-unique", "late-generic-file"

    monkeypatch.setattr(outbound, "_send_staging_document_from_path", stage_and_delete)
    outbound.deliver_ticket_attachment(attachment_id=case.attachment_ids[0], message_id=case.message_id, ticket_id=case.ticket_id)
    outbound.deliver_ticket_attachment(attachment_id=case.attachment_ids[0], message_id=case.message_id, ticket_id=case.ticket_id)
    assert "late-generic-file" in case.deleted
    assert case.remote == {"card", "archive-" + case.keys[2]}
    assert case.delivered == []


@pytest.mark.parametrize("error", [FileNotFoundError, outbound.TelegramApiError])
def test_staging_failure_after_delete_does_not_revive_cancelled_preload(case, monkeypatch, error):
    outbound.deliver_attachment_preload(preload_id=case.keys[2])

    def fail_after_delete(**kwargs):
        assert case.delete().status_code == 200
        raise error("upload failed")

    monkeypatch.setattr(outbound, "_send_staging_document", fail_after_delete)
    outbound.deliver_attachment_preload(preload_id=case.keys[0])
    outbound.deliver_attachment_preload(preload_id=case.keys[0])
    with case.factory() as db:
        assert db.get(TicketAttachmentPreload, case.keys[0]) is None
    assert case.remote == {"card", "archive-" + case.keys[2]}
    assert case.delivered == []


@pytest.mark.parametrize("workflow,message_type,expected", [("ticket", "agent", 200), ("task", "internal", 409)])
def test_ordinary_tickets_and_comments_do_not_gain_task_cancellation(case, workflow, message_type, expected):
    with case.factory() as db:
        db.get(Ticket, case.ticket_id).workflow_type = workflow
        db.get(TicketMessage, case.message_id).message_type = message_type
        db.commit()
    assert case.delete().status_code == expected
    with case.factory() as db:
        assert all(db.get(TicketAttachmentPreload, key).status == "uploading" for key in case.keys)
    assert case.deleted == []
