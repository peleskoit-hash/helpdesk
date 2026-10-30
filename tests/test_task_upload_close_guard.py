from types import SimpleNamespace
import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.app.auth import get_current_user
from api.app.db import get_db
from api.app.models import Ticket, TicketAttachmentPreload, TicketMessage
from api.app.routers import tickets
from api.app.services.ticket_lifecycle import finalize_group_parent_if_ready
from test_ticket_lifecycle_close import (_session, _seed_ticket, _seed_agent_message, _seed_attachment,
                                        _seed_contact_for_ticket, _seed_user)


@pytest.fixture
def case(monkeypatch):
    db = _session()
    target = _seed_ticket(db, workflow_type="task", assignee_id="agent-1")
    _seed_contact_for_ticket(db, target)
    _seed_user(db, "agent-1")
    _seed_user(db, "admin", role="admin")
    answer = _seed_agent_message(db, target, "agent-1")
    db.add(TicketMessage(id=str(uuid.uuid4()), ticket_id=target.id, message_type="client", body="Request"))
    db.commit()
    sent = []
    monkeypatch.setattr(tickets, "_send_to_telegram_client", lambda *args, **kwargs: sent.append("text"))
    monkeypatch.setattr(tickets, "_send_attachment_to_telegram_client", lambda *args, **kwargs: sent.append(args[2].id))
    user = SimpleNamespace(id="admin", role="admin")
    app = FastAPI()
    app.include_router(tickets.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as http:
        yield SimpleNamespace(db=db, target=target, answer=answer, http=http, sent=sent, user=user)
    db.close()


def pending(case, ticket, source):
    if source == "preload":
        row = TicketAttachmentPreload(id=str(uuid.uuid4()), ticket_id=ticket.id, user_id="other-user",
            original_filename="upload.pdf", mime_type="application/pdf", local_path="upload.pdf", status="uploading")
    else:
        message = _seed_agent_message(case.db, ticket, "agent-1", body="Older answer still uploading")
        row = _seed_attachment(case.db, ticket, message)
        row.telegram_delivery_status = source
    case.db.add(row)
    case.db.commit()
    return row


@pytest.mark.parametrize("kind", ["normal", "group_child", "group_parent"])
@pytest.mark.parametrize("source", ["preload", "uploading", "sending"])
@pytest.mark.parametrize("force", [False, True])
def test_http_409_before_any_close_or_client_delivery(case, kind, source, force):
    case.target.task_kind = kind
    upload_ticket = case.target
    if kind == "group_parent":
        upload_ticket = _seed_ticket(case.db, workflow_type="task", task_kind="group_child", parent_ticket_id=case.target.id)
    pending(case, upload_ticket, source)
    if kind == "group_parent":
        assert case.http.get(f"/api/v1/tickets/{case.target.id}").json()["has_active_attachment_uploads"] is True
    response = (case.http.post("/api/v1/tickets/admin/close", json={"ticket_ids": [case.target.id], "skip_client_notification": True})
                if force else case.http.post(f"/api/v1/tickets/{case.target.id}/close"))
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["error"]["code"] == "task_attachments_uploading"
    case.db.expire_all()
    assert case.db.get(Ticket, case.target.id).status == "pending"
    assert case.sent == []


@pytest.mark.parametrize("terminal", ["ready", "failed"])
@pytest.mark.parametrize("source", ["preload", "uploading", "sending"])
def test_card_flag_clears_and_task_can_close_after_ready_or_failure(case, terminal, source):
    row = pending(case, case.target, source)
    card_url = f"/api/v1/tickets/{case.target.id}"
    assert case.http.get(card_url).json()["has_active_attachment_uploads"] is True
    if source == "preload":
        row.status = terminal
    else:
        row.telegram_delivery_status = terminal
    case.db.commit()
    assert case.http.get(card_url).json()["has_active_attachment_uploads"] is False
    row_id = row.id
    response = case.http.post(card_url + "/close")
    assert response.status_code == 200, response.text
    assert case.target.status == "closed"
    if terminal == "failed":
        assert row_id not in case.sent


def test_admin_batch_preflight_does_not_close_or_send_first_task(case):
    blocked = _seed_ticket(case.db, workflow_type="task")
    pending(case, blocked, "preload")
    response = case.http.post("/api/v1/tickets/admin/close", json={"ticket_ids": [case.target.id, blocked.id]})
    assert response.status_code == 409
    case.db.expire_all()
    assert case.target.status == blocked.status == "pending"
    assert case.sent == []


def test_agent_is_also_blocked(case):
    case.user.id, case.user.role = "agent-1", "agent"
    pending(case, case.target, "preload")
    assert case.http.post(f"/api/v1/tickets/{case.target.id}/close").status_code == 409
    assert case.sent == []


def test_group_auto_finalize_checks_closed_children_uploads_before_sending(case):
    case.target.task_kind = "group_parent"
    child = _seed_ticket(case.db, workflow_type="task", task_kind="group_child", parent_ticket_id=case.target.id, status="closed")
    pending(case, child, "uploading")
    with pytest.raises(HTTPException) as error:
        finalize_group_parent_if_ready(case.db, case.target, closed_by_user_id="admin",
            first_client_message_fn=lambda *args: None, send_to_telegram_client_fn=lambda *args, **kwargs: case.sent.append("text"),
            send_attachment_to_telegram_client_fn=lambda *args, **kwargs: case.sent.append("file"),
            add_message_fn=lambda *args, **kwargs: None, system_note="Closed")
    assert error.value.status_code == 409
    assert case.target.status == "pending"
    assert case.sent == []


def test_ordinary_ticket_visibility_and_close_rules_are_unchanged(case):
    case.target.workflow_type = "ticket"
    pending(case, case.target, "preload")
    response = case.http.get(f"/api/v1/tickets/{case.target.id}")
    assert response.json()["has_active_attachment_uploads"] is False
    assert case.http.post(f"/api/v1/tickets/{case.target.id}/close").status_code == 200


def test_close_sends_only_latest_reply_files_and_skips_failed_files(case):
    old = _seed_attachment(case.db, case.target, case.answer)
    latest = _seed_agent_message(case.db, case.target, "agent-1", body="Latest answer")
    failed = _seed_attachment(case.db, case.target, latest)
    failed.telegram_delivery_status = "failed"
    good = _seed_attachment(case.db, case.target, latest)
    case.db.commit()
    response = case.http.post(f"/api/v1/tickets/{case.target.id}/close")
    assert response.status_code == 200, response.text
    assert case.sent == ["text", good.id]
    assert old.id not in case.sent and failed.id not in case.sent


def test_group_finalization_does_not_send_failed_child_attachment(case):
    case.target.task_kind = "group_parent"
    _seed_ticket(case.db, workflow_type="task", task_kind="group_child", parent_ticket_id=case.target.id, status="closed")
    # Child close aggregates copies of its answer and files on the parent.
    case.answer.body = "Child answer"
    failed = _seed_attachment(case.db, case.target, case.answer)
    failed.telegram_delivery_status = "failed"
    good = _seed_attachment(case.db, case.target, case.answer)
    case.db.commit()
    closed = finalize_group_parent_if_ready(case.db, case.target, closed_by_user_id="admin",
        first_client_message_fn=lambda db, ticket: db.query(TicketMessage).filter_by(ticket_id=ticket.id, message_type="client").first(),
        send_to_telegram_client_fn=lambda *args, **kwargs: case.sent.append("text"),
        send_attachment_to_telegram_client_fn=lambda db, ticket, attachment, **kwargs: case.sent.append(attachment.id),
        add_message_fn=lambda *args, **kwargs: None, system_note="Closed")
    assert closed is True
    assert case.sent == ["text", good.id]
