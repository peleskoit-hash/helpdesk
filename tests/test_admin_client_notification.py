"""Administrative close/delete use a per-request delivery flag; no Telegram runtime."""
import uuid
from datetime import datetime

import pytest
from sqlalchemy import select

from api.app.models import Ticket, TicketAttachment, TicketMessage
from api.app.routers import tickets
from test_admin_bulk_freeze import _session, _company, _user, _contact, _ticket, _client


@pytest.mark.parametrize("action", ["close", "delete"])
@pytest.mark.parametrize("kind", ["ticket", "normal", "group_parent", "group_child"])
@pytest.mark.parametrize("with_reply", [False, True])
@pytest.mark.parametrize("skip", [None, False, True])
def test_admin_delivery_paths(monkeypatch, tmp_path, action, kind, with_reply, skip):
    db = _session()
    company = _company(db)
    admin = _user(db, company, "admin")
    client = _contact(db, company)
    target = _ticket(db, company, client, workflow_type="ticket" if kind == "ticket" else "task")
    target.task_kind = "normal" if kind == "ticket" else kind
    parent = None
    child = None
    if kind == "group_parent":
        child = _ticket(db, company, client, workflow_type="task")
        child.task_kind = "group_child"
        child.parent_ticket_id = target.id
    if kind == "group_child":
        parent = _ticket(db, company, client, workflow_type="task")
        parent.task_kind = "group_parent"
        target.parent_ticket_id = parent.id
    for item in [target] + ([parent] if parent else []):
        db.add(TicketMessage(id=str(uuid.uuid4()), ticket_id=item.id, message_type="client", body="Request", created_at=datetime.utcnow()))
    # An aggregated answer/file already on the parent must also be suppressed.
    answer_target = parent or target
    attachment_path = tmp_path / "answer.txt"
    if with_reply:
        attachment_path.write_text("answer", encoding="utf-8")
        reply = TicketMessage(id=str(uuid.uuid4()), ticket_id=answer_target.id, message_type="agent", body="Answer", author_user_id=admin.id, created_at=datetime.utcnow())
        db.add(reply)
        db.add(TicketAttachment(id=str(uuid.uuid4()), ticket_id=answer_target.id, message_id=reply.id, source="upload", original_filename="answer.txt", local_path=str(attachment_path), created_at=datetime.utcnow()))
        if parent:
            db.add(TicketMessage(id=str(uuid.uuid4()), ticket_id=target.id, message_type="agent", body="Child answer", author_user_id=admin.id, created_at=datetime.utcnow()))
    db.commit()
    target_id = target.id
    parent_id = parent.id if parent else None
    child_id = child.id if child else None
    sent, attachments = [], []
    monkeypatch.setattr(tickets, "_send_to_telegram_client", lambda *args, **kw: sent.append(args[1].id) or {"ok": True, "result": {"message_id": 123}})
    monkeypatch.setattr(tickets, "_send_attachment_to_telegram_client", lambda *args, **kw: attachments.append(args[2].id))
    payload = {"ticket_ids": [target_id]}
    if skip is not None:
        payload["skip_client_notification"] = skip
    response = _client(db, admin).post(f"/api/v1/tickets/admin/{action}", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["closed" if action == "close" else "deleted"] == 1
    assert bool(sent) is (skip is not True)
    assert len(sent) <= 1
    expect_attachments = with_reply and (kind == "group_child" or (action == "close" and kind in ["normal", "group_parent"]))
    assert bool(attachments) is (expect_attachments and skip is not True)
    if action == "close":
        db.refresh(target)
        assert target.status == "closed"
        assert target.closed_by_user_id == admin.id
        assert target.closed_at is not None
        assert not target.is_frozen
        history = db.execute(select(TicketMessage).where(TicketMessage.ticket_id == target_id, TicketMessage.message_type == "system")).scalars().all()
        assert history
    else:
        assert db.get(Ticket, target_id) is None
        assert not db.execute(select(TicketMessage).where(TicketMessage.ticket_id == target_id)).scalars().all()
    if with_reply:
        assert attachment_path.exists() is (action == "close" or parent_id is not None)
    if child_id:
        if action == "delete":
            assert db.get(Ticket, child_id) is None
        else:
            db.refresh(child)
            assert child.status == "new"
    if parent_id:
        db.refresh(parent)
        assert parent.status == "closed"
        assert parent.closed_by_user_id == admin.id
        assert db.execute(select(TicketMessage).where(TicketMessage.ticket_id == parent_id, TicketMessage.message_type == "system")).scalars().all()
    db.close()


@pytest.mark.parametrize("action", ["close", "delete"])
@pytest.mark.parametrize("role", ["agent", "trainee", "chiki_piki"])
def test_admin_delivery_flag_does_not_grant_access(action, role):
    db = _session()
    company = _company(db)
    actor = _user(db, company, role)
    client = _contact(db, company)
    target = _ticket(db, company, client)
    db.commit()
    response = _client(db, actor).post(f"/api/v1/tickets/admin/{action}", json={"ticket_ids": [target.id], "skip_client_notification": True})
    assert response.status_code == 403
    db.refresh(target)
    assert target.status == "new"
    db.close()
