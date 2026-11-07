"""After-hours deletion uses synthetic databases, fixed hours and mocked Telegram."""
import asyncio
import uuid
from datetime import datetime, timedelta
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from api.app.models import StatsCompletion, StatsWorkInterval, Ticket, TicketAttachment, TicketMessage
from api.app.routers import telegram
from api.app.services import internal_task_scheduler as scheduler, telegram_after_hours_cleanup as cleanup
from api.app.services.automatic_messages import upsert_automatic_message_override
from api.app.services.telegram_intake_hours import is_telegram_intake_open
from test_telegram_preliminary_cancel import case, draft
from test_telegram_button_intake_hours import button, choose, prepared_draft
from test_ticket_review_telegram import _message_payload


def set_time(monkeypatch, value):
    at = datetime.fromisoformat(value).replace(tzinfo=ZoneInfo("Europe/Moscow"))
    for module in (telegram, cleanup):
        monkeypatch.setattr(module, "is_telegram_intake_open", lambda: is_telegram_intake_open(at))


@pytest.fixture
def flow(case, monkeypatch):
    set_time(monkeypatch, "2026-11-02T19:00:00")
    monkeypatch.setattr(cleanup, "send_message", case.send)
    monkeypatch.setattr(cleanup, "remove_inline_keyboard", case.removed)
    factory = sessionmaker(bind=case.db.get_bind(), autoflush=False)
    case.run = lambda: cleanup.run_telegram_after_hours_cleanup_once(session_factory=factory)
    return case


def waiting(case, kind, confirmation):
    row = button(case, kind, confirmation_enabled=confirmation, confirmation_text="Подтвердите")
    if confirmation:
        ticket = draft(case)
        ticket.button_config_key = ticket.instruction_button_key = row.button_key
        case.db.commit()
    else:
        ticket = prepared_draft(case, row)
    return row, ticket


@pytest.mark.parametrize("kind,confirmation", [
    ("ticket", True), ("task", True), ("group_task", True), ("task", False), ("group_task", False),
])
@pytest.mark.parametrize("sunday", [False, True])
def test_cleanup_deletes_drafts_and_related_rows_once(flow, monkeypatch, tmp_path, kind, confirmation, sunday):
    if sunday:
        set_time(monkeypatch, "2026-11-08T12:00:00")
    _, ticket = waiting(flow, kind, confirmation)
    ticket_id = ticket.id
    message = TicketMessage(id=str(uuid.uuid4()), ticket_id=ticket_id, message_type="system", body="Ожидание")
    flow.db.add(message)
    flow.db.flush()
    path = tmp_path / "draft.txt"
    path.write_text("draft")
    flow.db.add(TicketAttachment(id=str(uuid.uuid4()), ticket_id=ticket_id, message_id=message.id,
        source="telegram", local_path=str(path)))
    object_type = "ticket" if confirmation else "task"
    flow.db.add_all([
        StatsCompletion(id=str(uuid.uuid4()), company_id=flow.company.id, object_id=ticket_id, object_type=object_type),
        StatsWorkInterval(id=str(uuid.uuid4()), company_id=flow.company.id, object_id=ticket_id,
            object_type=object_type, user_id=flow.agent.id, started_at=datetime.utcnow()),
    ])
    text = "<b>Настроенный текст после закрытия</b>"
    upsert_automatic_message_override(flow.db, "telegram.after_hours.creation_blocked.client", text)
    flow.db.commit()

    assert flow.run() == 1
    flow.db.expire_all()
    assert flow.db.get(Ticket, ticket_id) is None
    for model in (TicketMessage, TicketAttachment):
        assert flow.db.scalar(select(model.id).where(model.ticket_id == ticket_id)) is None
    for model in (StatsCompletion, StatsWorkInterval):
        assert flow.db.scalar(select(model.id).where(model.object_id == ticket_id)) is None
    flow.send.assert_called_once_with(flow.contact.external_chat_id, text, parse_mode="HTML")
    assert flow.removed.called
    assert flow.run() == 0
    assert flow.send.call_count == 1


@pytest.mark.parametrize("scenario", [
    "menu", "submenu", "auto_confirmation", "auto_input", "unknown_automation", "ready_ticket", "ready_task",
    "client_message", "children", "web", "numbered", "assigned", "closed", "special", "section", "no_confirmation",
])
def test_cleanup_preserves_non_candidates(flow, scenario):
    if scenario in {"menu", "submenu"}:
        ticket = draft(flow)
        if scenario == "submenu":
            ticket.button_config_key = "__submenu__:Support"
    else:
        kind = "ticket" if scenario in {"ready_ticket", "no_confirmation", "section"} else "task"
        row, ticket = waiting(flow, kind, scenario == "auto_confirmation")
        if scenario.startswith("auto_") or scenario == "unknown_automation":
            row.automation_key = "unknown" if scenario == "unknown_automation" else "client_origin"
            if scenario == "auto_input":
                ticket.quick_action_state_json = {"client_origin": {"deadline": 9999999999}}
        elif scenario.startswith("ready_"):
            ticket.intake_state = "ready"
        elif scenario == "client_message":
            flow.db.add(TicketMessage(id=str(uuid.uuid4()), ticket_id=ticket.id, message_type="client", body="Данные"))
        elif scenario == "children":
            child = draft(flow)
            child.parent_ticket_id = ticket.id
        elif scenario == "web":
            ticket.channel = "web"
        elif scenario == "numbered":
            ticket.ticket_number = 22
        elif scenario == "assigned":
            ticket.assignee_id = flow.agent.id
        elif scenario == "closed":
            ticket.status, ticket.closed_at = "closed", datetime.utcnow()
        elif scenario == "special":
            ticket.is_special = True
        elif scenario == "section":
            row.button_kind, row.confirmation_enabled = "section", True
    flow.db.commit()
    ticket_id = ticket.id
    assert flow.run() == 0
    flow.db.expire_all()
    assert flow.db.get(Ticket, ticket_id) is not None
    flow.send.assert_not_called()
    flow.removed.assert_not_called()


def test_open_hours_then_missed_pass_are_handled(flow, monkeypatch):
    _, ticket = waiting(flow, "task", False)
    ticket_id = ticket.id
    set_time(monkeypatch, "2026-11-02T18:59:59")
    assert flow.run() == 0
    assert flow.db.get(Ticket, ticket_id) is not None
    set_time(monkeypatch, "2026-11-03T01:00:00")
    assert flow.run() == 1
    flow.db.expire_all()
    assert flow.db.get(Ticket, ticket_id) is None


def test_group_parent_button_kind_is_recognized(flow):
    row = button(flow, "ticket", button_kind="group_parent")
    ticket = prepared_draft(flow, row)
    assert ticket.task_kind == "group_parent"
    ticket_id = ticket.id
    assert flow.run() == 1
    flow.db.expire_all()
    assert flow.db.get(Ticket, ticket_id) is None


@pytest.mark.parametrize("kind", ["task", "group_task"])
def test_description_wait_uses_task_type_even_without_saved_button(flow, kind):
    _, ticket = waiting(flow, kind, False)
    ticket.button_config_key = ticket.instruction_button_key = None
    flow.db.commit()
    assert flow.run() == 1


def test_delivery_failure_does_not_rollback_or_stop_batches(flow, monkeypatch):
    row, first = waiting(flow, "task", False)
    second = prepared_draft(flow, row)
    ids = {first.id, second.id}
    monkeypatch.setattr(cleanup, "BATCH_SIZE", 1)
    flow.send.side_effect = RuntimeError("Telegram unavailable")
    assert flow.run() == 2
    flow.db.expire_all()
    assert not set(flow.db.scalars(select(Ticket.id))) & ids
    assert flow.send.call_count == 2
    assert flow.run() == 0
    assert flow.send.call_count == 2


@pytest.mark.parametrize("kind", ["ticket", "task", "group_task", "auto"])
def test_confirmation_key_is_saved_without_changing_type_and_reset_clears_it(flow, monkeypatch, kind):
    row = button(flow, kind, confirmation_enabled=True, confirmation_text="Подтвердите")
    set_time(monkeypatch, "2026-11-02T08:00:00")
    result = choose(flow, row, notify=False)
    assert result["awaiting_confirmation"]
    ticket = flow.db.get(Ticket, result["ticket_id"])
    assert ticket.button_config_key == row.button_key
    assert ticket.workflow_type == "ticket" and ticket.task_kind == "normal"
    assert choose(flow, row, "no", notify=False, update_id=101)["confirmation"] == "no"
    assert ticket.button_config_key is None


@pytest.mark.parametrize("kind", ["ticket", "task", "group_task", "auto"])
def test_saved_confirmation_key_preserves_positive_confirmation(flow, monkeypatch, kind):
    row = button(flow, kind, confirmation_enabled=True, confirmation_text="Подтвердите")
    set_time(monkeypatch, "2026-11-02T08:00:00")
    assert choose(flow, row, notify=False)["awaiting_confirmation"]
    result = choose(flow, row, "yes", notify=False, update_id=101)
    assert result["ok"] and not result.get("after_hours_blocked")
    ticket = flow.db.get(Ticket, result["ticket_id"])
    assert ticket.workflow_type == ("task" if kind == "group_task" else kind)
    assert ticket.intake_state == ("collecting" if kind in {"task", "group_task"} else "ready")


@pytest.mark.parametrize("kind", ["ticket", "task", "group_task"])
def test_real_confirmation_wait_is_cleaned_after_closing(flow, monkeypatch, kind):
    row = button(flow, kind, confirmation_enabled=True, confirmation_text="Подтвердите")
    set_time(monkeypatch, "2026-11-02T18:59:00")
    result = choose(flow, row, notify=False)
    assert result["awaiting_confirmation"]
    set_time(monkeypatch, "2026-11-02T19:00:00")
    assert flow.run() == 1
    flow.db.expire_all()
    assert flow.db.get(Ticket, result["ticket_id"]) is None


@pytest.mark.parametrize("kind", ["task", "group_task"])
@pytest.mark.parametrize("notify", [False, True])
@pytest.mark.parametrize("unsafe", [False, True])
def test_late_description_is_blocked_before_data_or_distribution(flow, monkeypatch, kind, notify, unsafe):
    _, ticket = waiting(flow, kind, False)
    ticket_id = ticket.id
    if unsafe:
        flow.db.add(TicketMessage(id=str(uuid.uuid4()), ticket_id=ticket_id, message_type="client", body="Сохранить"))
        flow.db.commit()
    # Even an expired draft must not reach the existing timeout/close path.
    ticket.updated_at = datetime.utcnow() - timedelta(minutes=20)
    flow.db.commit()
    methods = ["process_collecting_ticket_automation", "_append_client_message", "_store_telegram_attachment_and_schedule_employee_notification",
        "_ready_ticket_for_distribution", "_create_group_children_from_collecting"]
    mocks = []
    for method in methods:
        mock = Mock(side_effect=AssertionError(method))
        monkeypatch.setattr(telegram, method, mock)
        mocks.append(mock)
    payload = _message_payload(update_id=110, contact=flow.contact, text="Позднее описание")
    payload["message"]["document"] = {"file_id": "test", "file_name": "test.txt", "file_size": 10}
    assert telegram.process_telegram_update(flow.db, payload, notify_client=notify)["after_hours_blocked"]
    for mock in mocks:
        mock.assert_not_called()
    flow.db.expire_all()
    if unsafe:
        assert flow.db.get(Ticket, ticket_id).status == "unassigned"
        assert flow.db.scalars(select(TicketMessage).where(TicketMessage.message_type == "client")).one().body == "Сохранить"
    else:
        assert flow.db.get(Ticket, ticket_id) is None
        assert flow.db.scalars(select(TicketMessage)).all() == []
    assert not flow.db.scalars(select(TicketAttachment)).all()
    assert flow.send.called is notify


@pytest.mark.parametrize("kind", ["task", "group_task"])
def test_open_description_creates_task_normally(flow, monkeypatch, kind):
    _, ticket = waiting(flow, kind, False)
    set_time(monkeypatch, "2026-11-02T08:00:00")
    payload = _message_payload(update_id=110, contact=flow.contact, text="Описание")
    result = telegram.process_telegram_update(flow.db, payload, notify_client=False)
    assert result["ticket_id"] == ticket.id
    assert ticket.intake_state == "ready"
    assert flow.db.scalars(select(TicketMessage).where(TicketMessage.message_type == "client")).one().body == "Описание"


def test_automation_description_reaches_existing_handler_outside_hours(flow, monkeypatch):
    row, ticket = waiting(flow, "task", False)
    row.automation_key = "send_files"
    flow.db.commit()
    handler = Mock(return_value={"ok": True, "automation": True})
    monkeypatch.setattr(telegram, "process_collecting_ticket_automation", handler)
    payload = _message_payload(update_id=110, contact=flow.contact, text="Данные автоматизации")
    assert telegram.process_telegram_update(flow.db, payload, notify_client=False) == {"ok": True, "automation": True}
    handler.assert_called_once()
    assert flow.db.get(Ticket, ticket.id) is not None


def test_cleanup_scheduler_failure_is_independent(monkeypatch):
    calls = []
    for name in ("run_internal_task_scheduler_once", "run_internal_task_recurring_once", "run_avatar_cleanup_once"):
        monkeypatch.setattr(scheduler, name, lambda name=name: calls.append(name))
    def fail():
        calls.append("cleanup")
        raise RuntimeError("cleanup failed")
    monkeypatch.setattr(scheduler, "run_telegram_after_hours_cleanup_once", fail)
    async def stop(seconds):
        assert seconds == 30
        if calls.count("cleanup") == 2:
            raise asyncio.CancelledError
    monkeypatch.setattr(scheduler.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scheduler.internal_task_scheduler_loop())
    assert calls == ["run_internal_task_scheduler_once", "run_internal_task_recurring_once", "run_avatar_cleanup_once", "cleanup"] * 2
