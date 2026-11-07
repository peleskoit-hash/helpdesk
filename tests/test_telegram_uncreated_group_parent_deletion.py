"""Group draft cancellation uses real handlers, synthetic DBs and mocked Telegram."""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from fastapi import HTTPException

from api.app.models import (
    BotButtonSetting, StatsCompletion, StatsWorkInterval, Ticket, TicketAttachment,
    TicketAttachmentPreload, TicketMessage, TicketReviewCycle,
)
from api.app.routers import telegram, tickets
from api.app.schemas import AdminBulkTicketsPayload
from api.app.services.automatic_messages import upsert_automatic_message_override
from api.app.services.ticket_lifecycle import format_task_closed_without_answer_text
from test_telegram_preliminary_cancel import case, draft
from test_ticket_review_telegram import _message_payload, _callback_payload


@pytest.fixture(autouse=True)
def open_intake(monkeypatch):
    # Exercise existing cancellation/timeouts independently of the after-hours guard.
    monkeypatch.setattr(telegram, "is_telegram_intake_open", lambda: True)


def task(case, kind="group_parent"):
    ticket = draft(case)
    ticket.workflow_type, ticket.task_kind = "task", kind
    case.db.commit()
    return ticket


def close_via(case, ticket, via, notify=True):
    if via in {"message_timeout", "cleanup"}:
        ticket.updated_at = datetime.utcnow() - timedelta(minutes=11)
        case.db.commit()
    if via == "admin":
        response = tickets.admin_close_tickets(
            AdminBulkTicketsPayload(ticket_ids=[ticket.id], skip_client_notification=not notify), db=case.db, user=case.admin)
        assert response.ok and response.closed == 1 and response.not_found == []
        return
    if via == "cleanup":
        assert telegram.cleanup_expired_task_intents(case.db) == 1
        return
    payload = (_callback_payload(update_id=101, contact=case.contact, data="client_close", message_id=12)
        if via == "button" else _message_payload(update_id=101, contact=case.contact,
            text="Позднее описание" if via == "message_timeout" else "/close"))
    result = telegram.process_telegram_update(case.db, payload, notify_client=notify)
    assert result["task_pending_expired" if via == "message_timeout" else "closed"]


@pytest.mark.parametrize("via", ["command", "button", "message_timeout", "cleanup", "admin"])
@pytest.mark.parametrize("kind", ["group_parent", "normal"])
def test_safe_draft_is_deleted_with_related_records_and_notification(case, tmp_path, via, kind):
    ticket = task(case, kind)
    ticket_id = ticket.id
    path = tmp_path / "draft.txt"
    path.write_text("draft")
    message = TicketMessage(id=str(uuid.uuid4()), ticket_id=ticket_id, message_type="system", body="Ожидание описания")
    case.db.add(message)
    case.db.flush()
    case.db.add_all([
        TicketAttachment(id=str(uuid.uuid4()), ticket_id=ticket_id, message_id=message.id,
            source="telegram", local_path=str(path)),
        TicketAttachmentPreload(id=str(uuid.uuid4()), ticket_id=ticket_id, client_id=case.client.id,
            local_path=str(path), status="uploading"),
        StatsCompletion(id=str(uuid.uuid4()), company_id=case.company.id, object_id=ticket_id, object_type="task"),
        StatsWorkInterval(id=str(uuid.uuid4()), company_id=case.company.id, object_id=ticket_id,
            object_type="task", user_id=case.agent.id, started_at=datetime.utcnow()),
    ])
    key = ("telegram.intake.task_timeout" if via in {"message_timeout", "cleanup"} else
        "telegram.task.closed_without_answer.client" if via == "admin" else "telegram.task.creation_cancelled.client")
    expected = "<b>Существующее уведомление</b>"
    upsert_automatic_message_override(case.db, key, expected)
    case.db.commit()
    expected_mode = "HTML"
    if via == "admin" and kind == "group_parent":
        expected = format_task_closed_without_answer_text(case.db, ticket, None)
        expected_mode = None

    close_via(case, ticket, via)
    case.db.expire_all()
    assert case.db.get(Ticket, ticket_id) is None
    assert not case.db.scalars(select(Ticket)).all()
    for model in (TicketMessage, TicketAttachment, TicketAttachmentPreload):
        assert case.db.scalar(select(model.id).where(model.ticket_id == ticket_id)) is None
    for model in (StatsCompletion, StatsWorkInterval):
        assert case.db.scalar(select(model.id).where(model.object_id == ticket_id)) is None
    assert not case.db.scalars(select(TicketReviewCycle)).all()
    assert not path.exists()
    case.send.assert_called_once()
    args, kwargs = case.send.call_args
    assert (args[1] if len(args) > 1 else kwargs["text"]) == expected
    assert (args[2] if len(args) > 2 else kwargs.get("parse_mode")) == expected_mode
    assert case.removed.called
    assert telegram.cleanup_expired_task_intents(case.db) == 0


@pytest.mark.parametrize("via", ["command", "button", "message_timeout", "admin"])
def test_group_deletion_can_suppress_client_notification(case, via):
    ticket = task(case)
    ticket_id = ticket.id
    close_via(case, ticket, via, notify=False)
    case.db.expire_all()
    assert case.db.get(Ticket, ticket_id) is None
    case.send.assert_not_called()


@pytest.mark.parametrize("scenario", ["created", "children", "client_data", "automation"])
def test_unsafe_group_parent_is_not_deleted_by_admin(case, scenario):
    ticket = task(case)
    if scenario == "created":
        ticket.intake_state, ticket.ticket_number = "ready", 51
    elif scenario == "children":
        child = task(case, "group_child")
        child.parent_ticket_id, child.intake_state = ticket.id, "ready"
    elif scenario == "client_data":
        case.db.add(TicketMessage(id=str(uuid.uuid4()), ticket_id=ticket.id, message_type="client", body="Описание"))
    else:
        row = BotButtonSetting(id=str(uuid.uuid4()), menu_type="Support", button_key="group-auto",
            button_label="Группа", button_path="Группа", workflow_type="task", automation_key="telegram_group")
        case.db.add(row)
        ticket.instruction_button_key = ticket.button_config_key = row.button_key
    case.db.commit()
    ticket_id = ticket.id
    close_via(case, ticket, "admin")
    case.db.expire_all()
    assert case.db.get(Ticket, ticket_id) is not None
    if scenario == "children":
        assert case.db.scalar(select(Ticket.id).where(Ticket.parent_ticket_id == ticket_id)) is not None
    if scenario == "client_data":
        assert case.db.scalar(select(TicketMessage.id).where(
            TicketMessage.ticket_id == ticket_id, TicketMessage.message_type == "client")) is not None


@pytest.mark.parametrize("via", ["command", "button", "message_timeout", "cleanup"])
def test_group_with_client_description_keeps_existing_close_semantics(case, via):
    ticket = task(case)
    ticket_id = ticket.id
    case.db.add(TicketMessage(id=str(uuid.uuid4()), ticket_id=ticket_id, message_type="client", body="Сохранить"))
    case.db.commit()
    close_via(case, ticket, via)
    case.db.expire_all()
    assert case.db.get(Ticket, ticket_id) is not None
    assert case.db.scalar(select(TicketMessage.id).where(
        TicketMessage.ticket_id == ticket_id, TicketMessage.message_type == "client")) is not None


def test_automation_wait_is_excluded_from_background_timeout(case):
    ticket = task(case)
    row = BotButtonSetting(id=str(uuid.uuid4()), menu_type="Support", button_key="group-auto",
        button_label="Группа", button_path="Группа", workflow_type="task", automation_key="telegram_group")
    case.db.add(row)
    ticket.instruction_button_key = ticket.button_config_key = row.button_key
    ticket.updated_at = datetime.utcnow() - timedelta(minutes=11)
    case.db.commit()
    assert telegram.cleanup_expired_task_intents(case.db) == 0
    assert case.db.get(Ticket, ticket.id).status == "unassigned"
    case.send.assert_not_called()


def test_admin_close_still_requires_admin(case):
    ticket = task(case)
    with pytest.raises(HTTPException) as error:
        tickets.admin_close_tickets(AdminBulkTicketsPayload(ticket_ids=[ticket.id]), db=case.db, user=case.agent)
    assert error.value.status_code == 403
    assert case.db.get(Ticket, ticket.id) is not None
