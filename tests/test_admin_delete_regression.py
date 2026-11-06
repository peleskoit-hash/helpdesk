"""Admin hard-delete on enforced FKs, including isolated PostgreSQL schemas."""
import os
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.app.db import Base, get_db
from api.app.models import (IntegrationLog, Ticket, TicketAttachment, TicketAttachmentPreload,
    TicketFeedback, TicketFeedbackInput, TicketMessage, TicketReview, TicketReviewCycle, TicketView, TelegramContact)
from api.app.routers import tickets
from api.app.services import telegram_outbound_delivery as outbound
from api.app.services.ticket_reviews import create_review_cycle, save_review_rating
from test_admin_bulk_freeze import _company, _contact, _current_user, _ticket, _user


@pytest.fixture(scope="module", params=["sqlite", "postgresql"])
def database(request):
    control = None
    if request.param == "sqlite":
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

        @event.listens_for(engine, "connect")
        def enforce_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
    else:
        url = os.environ.get("TICKET_SEARCH_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Set TICKET_SEARCH_TEST_DATABASE_URL for isolated PostgreSQL schemas")
        control = create_engine(url)
        schema = "admin_delete_" + uuid.uuid4().hex
        with control.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {schema}"))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        Base.metadata.create_all(engine)
        if request.param == "postgresql":
            # This legacy FK exists in DEV PostgreSQL, but not in ORM metadata.
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE integration_logs ADD CONSTRAINT integration_logs_task_id_fkey "
                                        "FOREIGN KEY (task_id) REFERENCES tickets(id)"))
        yield engine
    finally:
        engine.dispose()
        if control is not None:
            with control.begin() as connection:
                connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
            control.dispose()


@pytest.fixture
def case(database, monkeypatch):
    with Session(database, autoflush=False) as db:
        company = _company(db)
        db.flush()
        admin = _user(db, company, "admin")
        contact = _contact(db, company)
        db.commit()
        app = FastAPI()
        app.include_router(tickets.router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[tickets.get_current_user] = lambda: _current_user(admin)
        sent = []
        committed = []
        event.listen(db, "after_commit", lambda session: committed.append(True))
        deleted_ids = []

        def deliver(db_session, ticket, content, **kwargs):
            assert committed, "Client delivery preceded commit"
            # A separate session must see the deletion, even for parent delivery.
            with Session(database) as check:
                assert not check.scalars(select(Ticket.id).where(Ticket.id.in_(deleted_ids))).all()
            sent.append((ticket.id, content, kwargs))
            return {"ok": True, "result": {"message_id": 123}}

        send_to_telegram_client = tickets._send_to_telegram_client
        monkeypatch.setattr(tickets, "_send_to_telegram_client", deliver)
        monkeypatch.setattr(tickets, "_send_attachment_to_telegram_client", deliver)
        monkeypatch.setattr(outbound, "_delete_archive_message_by_id", lambda mid: True)

        def target(status="new", workflow="ticket", kind="normal", channel="telegram", parent=None):
            row = _ticket(db, company, contact, status=status, workflow_type=workflow)
            row.task_kind = kind
            row.channel = channel
            row.parent_ticket_id = parent.id if parent else None
            db.flush()
            db.add(TicketMessage(id=str(uuid.uuid4()), ticket_id=row.id, message_type="client", body="Request"))
            db.commit()
            return row

        def delete(rows, skip=False):
            deleted_ids[:] = [row.id for row in rows]
            committed.clear()
            with TestClient(app, raise_server_exceptions=False) as http:
                return http.post("/api/v1/tickets/admin/delete", json={
                    "ticket_ids": deleted_ids, "skip_client_notification": skip})

        yield SimpleNamespace(db=db, admin=admin, contact=contact, target=target, delete=delete, sent=sent,
                              deleted_ids=deleted_ids, committed=committed, send_to_telegram_client=send_to_telegram_client)


def related_rows(case, ticket, tmp_path):
    db = case.db
    # Two versions exercise both directions of the review cycle and self-reference.
    for rating in (4, 5):
        cycle = create_review_cycle(db, ticket_id=ticket.id, agent_id=case.admin.id, client_id=case.contact.id)
        save_review_rating(db, cycle=cycle, rating=rating, agent_name_snapshot="Admin",
                           client_name_snapshot="Client", ticket_subject_snapshot="Test")
    db.add(TicketFeedback(id=str(uuid.uuid4()), ticket_id=ticket.id, client_id=case.contact.id,
        client_name_snapshot="Client", ticket_title_snapshot="Test", text="Feedback", submitted_at=datetime.utcnow()))
    db.add(TicketFeedbackInput(id=str(uuid.uuid4()), ticket_id=ticket.id, client_id=case.contact.id, state="waiting_text"))
    log = IntegrationLog(id=str(uuid.uuid4()), task_id=ticket.id, source="test", event_type="test",
                         result="ok", code="test", description="Synthetic")
    db.add(log)
    db.add(TicketView(id=str(uuid.uuid4()), ticket_id=ticket.id, user_id=case.admin.id))
    message = TicketMessage(id=str(uuid.uuid4()), ticket_id=ticket.id, message_type="agent", body="Answer")
    db.add(message)
    db.flush()
    path = tmp_path / (ticket.id + ".txt")
    path.write_text("answer", encoding="utf-8")
    db.add(TicketAttachment(id=str(uuid.uuid4()), ticket_id=ticket.id, message_id=message.id,
        source="upload", original_filename=path.name, local_path=str(path)))
    db.add(TicketAttachmentPreload(id=str(uuid.uuid4()), ticket_id=ticket.id, user_id=case.admin.id,
        local_path=str(path), status="ready"))
    db.commit()
    return path, log.id


@pytest.mark.parametrize("skip", [False, True])
@pytest.mark.parametrize("channel", ["telegram", "web"])
@pytest.mark.parametrize("kind", ["ticket", "normal", "group_parent", "group_child"])
def test_closed_objects_never_notify(case, tmp_path, skip, channel, kind):
    parent = case.target(workflow="task", kind="group_parent", channel=channel) if kind == "group_child" else None
    target = case.target(status="closed", workflow="ticket" if kind == "ticket" else "task",
                         kind="normal" if kind == "ticket" else kind, channel=channel, parent=parent)
    targets = [target]
    if kind == "group_parent":
        targets.append(case.target(status="closed", workflow="task", kind="group_child", channel=channel, parent=target))
    ids = [row.id for row in targets]
    resources = [related_rows(case, row, tmp_path) for row in targets]
    response = case.delete([target], skip)
    assert response.status_code == 200, response.text
    assert response.json()["deleted"] == 1
    assert not case.sent
    assert not case.db.scalars(select(Ticket.id).where(Ticket.id.in_(ids))).all()
    for model in (TicketReview, TicketReviewCycle, TicketFeedback, TicketFeedbackInput,
                  TicketAttachment, TicketAttachmentPreload, TicketMessage, TicketView):
        assert not case.db.scalars(select(model.id).where(model.ticket_id.in_(ids))).all()
    for path, log_id in resources:
        assert not path.exists()
        assert case.db.get(IntegrationLog, log_id).task_id is None
    if parent:
        case.db.refresh(parent)
        assert parent.status == "closed"
        assert parent.closed_by_user_id == case.admin.id


@pytest.mark.parametrize("workflow", ["ticket", "task"])
@pytest.mark.parametrize("skip", [False, True])
def test_active_notification_after_commit(case, monkeypatch, workflow, skip):
    target = case.target(workflow=workflow)
    target_id = target.id
    template = Mock(return_value="Configured close text with review and suggestion buttons")
    monkeypatch.setattr(tickets, "_ticket_closed_client_text", template)
    expected = (tickets.TICKET_CLOSED_CLIENT_TEXT if workflow == "ticket" else
                tickets.format_task_closed_without_answer_text(case.db, target, tickets._first_client_message(case.db, target)))
    response = case.delete([target], skip)
    assert response.status_code == 200, response.text
    assert case.db.get(Ticket, target_id) is None
    assert [content for _, content, _ in case.sent] == ([] if skip else [expected])
    template.assert_not_called()
    for _, _, kwargs in case.sent:
        assert "reply_markup" not in kwargs
        assert kwargs["remove_keyboard"] is True
    assert not case.db.scalars(select(TicketReviewCycle.id).where(TicketReviewCycle.ticket_id == target_id)).all()


@pytest.mark.parametrize("failure", ["delete", "commit"])
@pytest.mark.parametrize("group", [False, True])
def test_failure_rolls_back_without_delivery(case, monkeypatch, tmp_path, failure, group):
    parent = case.target(workflow="task", kind="group_parent") if group else None
    target = case.target(workflow="task" if group else "ticket", kind="group_child" if group else "normal", parent=parent)
    target_id = target.id
    path, _ = related_rows(case, target, tmp_path)
    rollback = Mock(wraps=case.db.rollback)
    monkeypatch.setattr(case.db, "rollback", rollback)
    if failure == "commit":
        monkeypatch.setattr(case.db, "commit", Mock(side_effect=RuntimeError("synthetic commit failure")))
    else:
        original = tickets._delete_ticket_hard

        def fail_after_delete(db, ticket):
            original(db, ticket)
            db.flush()
            raise RuntimeError("synthetic deletion failure")

        monkeypatch.setattr(tickets, "_delete_ticket_hard", fail_after_delete)
    response = case.delete([target])
    assert response.status_code == 500
    assert not case.sent
    rollback.assert_called_once()
    assert case.db.get(Ticket, target_id) is not None
    assert path.exists()
    if parent:
        case.db.refresh(parent)
        assert parent.status == "new"


@pytest.mark.parametrize("skip", [False, True])
def test_bulk_mixed_statuses(case, skip):
    rows = [case.target(status=status, workflow=workflow) for status in ("new", "closed") for workflow in ("ticket", "task")]
    expected = {row.id for row in rows if row.status != "closed"} if not skip else set()
    response = case.delete(rows, skip)
    assert response.status_code == 200, response.text
    assert response.json()["deleted"] == 4
    assert {key for key, _, _ in case.sent} == expected


@pytest.mark.parametrize("parent_status", ["new", "closed"])
@pytest.mark.parametrize("skip", [False, True])
def test_active_group_child_defers_parent_text_and_attachment(case, tmp_path, parent_status, skip):
    parent = case.target(status=parent_status, workflow="task", kind="group_parent")
    child = case.target(workflow="task", kind="group_child", parent=parent)
    message = TicketMessage(id=str(uuid.uuid4()), ticket_id=parent.id, message_type="agent", body="Aggregated answer")
    case.db.add(message)
    case.db.flush()
    path = tmp_path / "parent.txt"
    path.write_text("answer", encoding="utf-8")
    case.db.add(TicketAttachment(id=str(uuid.uuid4()), ticket_id=parent.id, message_id=message.id,
                                source="upload", local_path=str(path), original_filename=path.name))
    case.db.commit()
    parent_id = parent.id
    response = case.delete([child], skip)
    assert response.status_code == 200, response.text
    assert len(case.sent) == (2 if parent_status != "closed" and not skip else 0)
    assert all(key == parent_id for key, _, _ in case.sent)
    case.db.refresh(parent)
    assert parent.status == "closed"
    assert path.exists()


def test_parent_and_child_bulk_does_not_double_notify(case):
    parent = case.target(workflow="task", kind="group_parent")
    child = case.target(workflow="task", kind="group_child", parent=parent)
    parent_id = parent.id
    response = case.delete([child, parent])
    assert response.status_code == 200, response.text
    assert [key for key, _, _ in case.sent] == [parent_id]


@pytest.mark.parametrize("group", [False, True])
def test_delivery_error_does_not_fail_committed_delete(case, monkeypatch, group):
    parent = case.target(workflow="task", kind="group_parent") if group else None
    target = case.target(workflow="task" if group else "ticket", kind="group_child" if group else "normal", parent=parent)
    target_id = target.id
    sender = Mock(side_effect=RuntimeError("synthetic Telegram failure"))
    monkeypatch.setattr(tickets, "_send_to_telegram_client", sender)
    response = case.delete([target])
    assert response.status_code == 200, response.text
    sender.assert_called_once()
    assert case.db.get(Ticket, target_id) is None


@pytest.mark.parametrize("workflow", ["ticket", "task"])
def test_agent_cannot_delete(case, workflow):
    target = case.target(workflow=workflow)
    case.admin.role = "agent"
    case.db.commit()
    response = case.delete([target])
    assert response.status_code == 403
    assert not case.sent
    assert case.db.get(Ticket, target.id) is not None


def test_real_sender_uses_snapshot_after_ticket_deletion(case, monkeypatch):
    target = case.target()
    target_id = target.id
    case.db.add(TelegramContact(id=str(uuid.uuid4()), company_id=target.company_id,
        client_id=target.client_id, external_chat_id=target_id, external_user_id=target_id))
    case.db.commit()
    delivered = []

    def send(chat_id, body, **kwargs):
        assert case.committed
        assert case.db.get(Ticket, target_id) is None
        delivered.append((chat_id, body, kwargs))
        return {"ok": True, "result": {"message_id": 123}}

    monkeypatch.setattr(tickets, "send_message", send)
    monkeypatch.setattr(tickets, "_send_to_telegram_client", case.send_to_telegram_client)
    response = case.delete([target])
    assert response.status_code == 200, response.text
    assert len(delivered) == 1
    assert delivered[0][0] == target_id
    assert delivered[0][1] == tickets.TICKET_CLOSED_CLIENT_TEXT
    assert delivered[0][2].get("reply_markup") is None
    assert delivered[0][2]["remove_keyboard"] is True


def test_bulk_failure_discards_prepared_notifications(case, monkeypatch):
    rows = [case.target() for _ in range(2)]
    ids = [row.id for row in rows]
    original = tickets._delete_ticket_hard
    calls = []

    def fail_second(db, ticket):
        original(db, ticket)
        calls.append(ticket.id)
        if len(calls) == 2:
            raise RuntimeError("synthetic second deletion failure")
        return []

    monkeypatch.setattr(tickets, "_delete_ticket_hard", fail_second)
    assert case.delete(rows).status_code == 500
    assert len(calls) == 2
    assert not case.sent
    assert set(case.db.scalars(select(Ticket.id).where(Ticket.id.in_(ids)))) == set(ids)


def test_closed_child_with_open_sibling_keeps_parent_open(case):
    parent = case.target(workflow="task", kind="group_parent")
    closed_child = case.target(status="closed", workflow="task", kind="group_child", parent=parent)
    sibling = case.target(workflow="task", kind="group_child", parent=parent)
    assert case.delete([closed_child]).status_code == 200
    assert not case.sent
    case.db.refresh(parent)
    assert parent.status == "new"
    assert case.db.get(Ticket, sibling.id) is not None


@pytest.mark.parametrize("workflow,helper", [
    ("task", "_first_client_message"),
    ("task", "format_task_closed_without_answer_text"),
])
def test_notification_preparation_failure_does_not_block_delete(case, monkeypatch, caplog, workflow, helper):
    target = case.target(workflow=workflow)
    target_id = target.id
    prepare = Mock(side_effect=ValueError("synthetic notification preparation failure"))
    monkeypatch.setattr(tickets, helper, prepare)
    response = case.delete([target])
    assert response.status_code == 200, response.text
    assert response.json()["deleted"] == 1
    prepare.assert_called_once()
    assert case.db.get(Ticket, target_id) is None
    assert not case.sent
    assert any(record.exc_info and target_id in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("workflow,helper", [
    ("task", "_first_client_message"),
    ("task", "format_task_closed_without_answer_text"),
])
def test_notification_preparation_database_failure_rolls_back(case, monkeypatch, workflow, helper):
    target = case.target(workflow=workflow)
    target_id = target.id
    rollback = Mock(wraps=case.db.rollback)
    monkeypatch.setattr(case.db, "rollback", rollback)

    def database_failure(*args):
        case.db.execute(text("SELECT nonexistent_admin_delete_column FROM tickets"))

    monkeypatch.setattr(tickets, helper, database_failure)
    response = case.delete([target])
    assert response.status_code == 500
    rollback.assert_called_once()
    assert case.db.get(Ticket, target_id) is not None
    assert not case.sent


@pytest.mark.parametrize('skip', [False, True])
@pytest.mark.parametrize('overridden', [False, True])
def test_real_task_delete_sender_preserves_task_template_after_commit(case, monkeypatch, skip, overridden):
    from api.app.models import AutomaticMessageTemplate
    keys = ['telegram.ticket.close.client', 'telegram.task.closed_without_answer.client']
    case.db.execute(AutomaticMessageTemplate.__table__.delete().where(AutomaticMessageTemplate.key.in_(keys)))
    target = case.target(workflow='task')
    target_id = target.id
    case.db.add(TelegramContact(id=str(uuid.uuid4()), company_id=target.company_id,
        client_id=target.client_id, external_chat_id=target_id, external_user_id=target_id))
    case.db.add(AutomaticMessageTemplate(id=str(uuid.uuid4()), key='telegram.ticket.close.client',
        body='WRONG: review and suggestions', is_overridden=True, html_enabled=False))
    if overridden:
        case.db.add(AutomaticMessageTemplate(id=str(uuid.uuid4()), key='telegram.task.closed_without_answer.client',
            body='Настроенное удаление задачи.', is_overridden=True, html_enabled=False))
    case.db.commit()
    expected = tickets.format_task_closed_without_answer_text(case.db, target, tickets._first_client_message(case.db, target))
    delivered = []

    def send(chat_id, body, **kwargs):
        assert case.committed, 'Delivery must follow commit'
        with Session(case.db.get_bind()) as check:
            assert check.get(Ticket, target_id) is None
            assert check.scalar(select(TicketMessage.id).where(TicketMessage.ticket_id == target_id)) is None
        delivered.append((chat_id, body, kwargs))
        return {'ok': True, 'result': {'message_id': 123}}

    monkeypatch.setattr(tickets, 'send_message', send)
    monkeypatch.setattr(tickets, '_send_to_telegram_client', case.send_to_telegram_client)
    response = case.delete([target], skip)
    assert response.status_code == 200, response.text
    assert len(delivered) == (0 if skip else 1)
    if not skip:
        assert delivered[0][0:2] == (target_id, expected)
        assert delivered[0][2]['remove_keyboard'] is True
        assert delivered[0][2].get('reply_markup') is None
        assert 'WRONG' not in expected
        if overridden:
            assert expected == 'Настроенное удаление задачи.'
    case.db.execute(AutomaticMessageTemplate.__table__.delete().where(AutomaticMessageTemplate.key.in_(keys)))
    case.db.commit()
