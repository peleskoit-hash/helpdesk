"""Real comment/preload routes with temporary storage and a simulated archive boundary."""
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.app.config import settings
from api.app.db import get_db
from api.app.models import Ticket, TicketAttachment, TicketAttachmentPreload, TicketMessage, User
from api.app.routers import tickets, management_saved_files
from test_call_center_observer import case


@pytest.fixture
def files_case(case, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "attachment_storage_dir", str(tmp_path))
    engine = case.db.get_bind()
    archives = []

    def archive(*, preload_id):
        # The archive transport is outside this task. Keep the real save/claim code.
        with Session(engine) as db:
            row = db.get(TicketAttachmentPreload, preload_id)
            assert Path(row.local_path).is_file()
            row.status = "ready"
            row.telegram_file_id = "archive-" + preload_id
            row.telegram_file_unique_id = "unique-" + preload_id
            archives.append(preload_id)
            db.commit()

    def forbidden(*args, **kwargs):
        pytest.fail("Comment attachments must never be delivered or scheduled to the client")

    def db_session():
        with Session(engine, autoflush=False) as db:
            yield db

    case.http.app.dependency_overrides[get_db] = db_session
    case.http.app.include_router(management_saved_files.router)
    monkeypatch.setattr(tickets, "deliver_attachment_preload", archive)
    monkeypatch.setattr(tickets, "_send_attachment_to_telegram_client", forbidden)
    monkeypatch.setattr(tickets, "deliver_ticket_attachment", forbidden)
    case.archives = archives
    return case


def prepare(case, name="note.pdf"):
    response = case.http.post(case.base + "/attachment-preloads",
                              files={"file": (name, b"%PDF-test", "application/pdf")})
    assert response.status_code == 200, response.text
    preload_id = response.json()["id"]
    status = case.http.get(case.base + "/attachment-preloads/" + preload_id)
    assert status.status_code == 200 and status.json()["status"] == "ready"
    return preload_id


def comment(case, ids, text="Observation"):
    fields = [("text", (None, text))]
    fields.extend(("preloaded_attachment_id", (None, value)) for value in ids)
    return case.http.post(case.base + "/comment", files=fields)


@pytest.mark.parametrize("workflow", ["ticket", "task"])
@pytest.mark.parametrize("channel", ["telegram", "web"])
@pytest.mark.parametrize("text", ["Observation", ""])
def test_observer_prepares_and_attaches_without_client_delivery_or_unfreeze(files_case, workflow, channel, text):
    c = files_case
    c.ticket.workflow_type = workflow
    c.ticket.channel = channel
    c.ticket.is_frozen = True
    c.ticket.freeze_until = datetime.utcnow() + timedelta(days=1)
    c.db.commit()
    before = (c.ticket.status, c.ticket.assignee_id, c.ticket.freeze_until)
    ids = [prepare(c, name) for name in ["first.pdf", "second.pdf"]]
    response = comment(c, ids, text)
    assert response.status_code == 200, response.text
    c.db.expire_all()
    message = c.db.scalar(select(TicketMessage).where(TicketMessage.message_type == "internal"))
    assert message.body == text and message.author_user_id == "viewer"
    assert message.telegram_message_id is None
    attachments = list(c.db.scalars(select(TicketAttachment).where(TicketAttachment.message_id == message.id)))
    assert len(attachments) == 2
    assert {a.original_filename for a in attachments} == {"first.pdf", "second.pdf"}
    assert all(a.telegram_delivery_status is None and a.telegram_message_id is None for a in attachments)
    assert all(Path(a.local_path).read_bytes() == b"%PDF-test" for a in attachments)
    assert all(c.db.get(TicketAttachmentPreload, value).final_attachment_id in {a.id for a in attachments} for value in ids)
    assert c.unfreezes == [] and c.ticket.is_frozen
    assert (c.ticket.status, c.ticket.assignee_id, c.ticket.freeze_until) == before
    assert c.archives == ids


@pytest.mark.parametrize("workflow", ["ticket", "task"])
def test_observer_cannot_send_files_to_client(files_case, workflow):
    c = files_case
    c.ticket.workflow_type = workflow
    c.db.commit()
    preload_id = prepare(c)
    for fields in [
        [("text", (None, "Forbidden")), ("preloaded_attachment_id", (None, preload_id))],
        [("text", (None, "Forbidden")), ("files", ("raw.pdf", b"pdf", "application/pdf"))],
    ]:
        assert c.http.post(c.base + "/reply", files=fields).status_code == 403
    c.db.expire_all()
    assert c.db.get(TicketAttachmentPreload, preload_id).final_attachment_id is None
    assert c.db.scalar(select(TicketAttachment)) is None
    assert c.db.scalar(select(TicketMessage).where(TicketMessage.message_type == "agent")) is None
    assert c.http.get("/api/v1/management/saved-files").status_code == 403


@pytest.mark.parametrize("foreign", ["user", "ticket"])
def test_observer_cannot_read_retry_delete_or_claim_foreign_preload(files_case, foreign):
    c = files_case
    own = prepare(c, "own.pdf")
    other = prepare(c, "other.pdf")
    c.db.expire_all()
    row = c.db.get(TicketAttachmentPreload, other)
    if foreign == "user":
        c.db.add(User(id="other", company_id=c.ticket.company_id, full_name="Other", role="agent"))
        row.user_id = "other"
    else:
        c.db.add(Ticket(id="other-ticket", ticket_number=2, company_id=c.ticket.company_id,
                        client_id=c.ticket.client_id, subject="Other", status="new"))
        row.ticket_id = "other-ticket"
    c.db.commit()
    endpoint = c.base + "/attachment-preloads/" + other
    assert c.http.get(endpoint).status_code == 404
    assert c.http.post(endpoint + "/retry").status_code == 404
    assert c.http.delete(endpoint).status_code == 404
    assert comment(c, [own, other]).status_code == 404
    c.db.expire_all()
    assert c.db.get(TicketAttachmentPreload, own).final_attachment_id is None
    assert c.db.get(TicketAttachmentPreload, other).final_attachment_id is None
    assert c.db.scalar(select(TicketAttachment)) is None
    assert c.db.scalar(select(TicketMessage).where(TicketMessage.message_type == "internal")) is None


@pytest.mark.parametrize("workflow", ["ticket", "task"])
@pytest.mark.parametrize("status", ["uploading", "failed", "cancelled", "ready-without-file"])
def test_comment_keeps_existing_ready_requirement(files_case, workflow, status):
    c = files_case
    c.ticket.workflow_type = workflow
    c.db.commit()
    preload_id = prepare(c)
    c.db.expire_all()
    row = c.db.get(TicketAttachmentPreload, preload_id)
    row.status = "ready" if status == "ready-without-file" else status
    if status == "ready-without-file":
        row.telegram_file_id = None
    c.db.commit()
    response = comment(c, [preload_id])
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "attachment_preload_not_ready"
    c.db.expire_all()
    assert c.db.get(TicketAttachmentPreload, preload_id).final_attachment_id is None
    assert c.db.scalar(select(TicketAttachment)) is None


def test_observer_uses_existing_retry_and_delete(files_case):
    c = files_case
    preload_id = prepare(c)
    c.db.expire_all()
    row = c.db.get(TicketAttachmentPreload, preload_id)
    row.status = "failed"
    c.db.commit()
    response = c.http.post(c.base + "/attachment-preloads/" + preload_id + "/retry")
    assert response.status_code == 200, response.text
    assert c.http.get(c.base + "/attachment-preloads/" + preload_id).json()["status"] == "ready"
    assert c.archives == [preload_id, preload_id]
    assert c.http.delete(c.base + "/attachment-preloads/" + preload_id).status_code == 200


def test_observer_closed_or_inaccessible_ticket_cannot_prepare(files_case):
    c = files_case
    c.ticket.status = "closed"
    c.db.commit()
    response = c.http.post(c.base + "/attachment-preloads", files={"file":("x.pdf",b"pdf","application/pdf")})
    assert response.status_code == 409
    c.ticket.status = "new"
    from api.app.models import ClientProfile
    c.db.get(ClientProfile,"profile").destination = "Other"
    c.db.commit()
    response = c.http.post(c.base + "/attachment-preloads", files={"file":("x.pdf",b"pdf","application/pdf")})
    assert response.status_code == 403
    assert c.archives == []
