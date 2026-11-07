"""Only automation handoff is scheduled; all external I/O is mocked."""
from datetime import datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from api.app.models import Ticket, TicketMessage
from api.app.routers import telegram
from api.app.services import telegram_automation as automation, telegram_bitrix_phone as phone, telegram_group_automation as group
from api.app.services.automatic_messages import upsert_automatic_message_override
from api.app.services.telegram_intake_hours import is_telegram_intake_open
from api.app.services import saved_files
from test_telegram_auto_fallback import case as fallback_case, select_button as fallback_select
from test_telegram_group_registry import case
from test_telegram_group_copy import flow as group_flow, select_button as group_select, incoming as group_incoming
from test_telegram_client_origin import flow
from test_telegram_bitrix_phone import phone_flow, choose as phone_select, process as phone_process, action as phone_action


TEXT = "<b>Передача недоступна</b>: настроенный текст."


def set_time(monkeypatch, value):
    at = datetime.fromisoformat(value).replace(tzinfo=ZoneInfo("Europe/Moscow"))
    for module in (telegram, automation, phone, group):
        monkeypatch.setattr(module, "is_telegram_intake_open", lambda: is_telegram_intake_open(at))


@pytest.fixture(autouse=True)
def closed_hours(monkeypatch):
    set_time(monkeypatch, "2026-11-02T19:00:00")


def customize(db):
    upsert_automatic_message_override(db, "telegram.after_hours.automation_handoff_blocked.client", TEXT)
    db.commit()


def invoke(path, case, notify):
    db, ticket, chat = case.db, case.task, case.contact.external_chat_id
    if path == "fallback":
        return telegram._fallback_auto_to_support(db, ticket, "Фактическая ошибка отправки", chat, notify)
    if path == "generic":
        return automation._handoff_automation_task_to_agent(
            db, ticket, None, chat, notify, append_button_system_text_fn=telegram._store_button_system_text,
            ready_ticket_for_distribution_fn=telegram._ready_ticket_for_distribution, send_message_fn=telegram.send_message,
            complete_automation_fn=telegram._complete_auto_ticket,
            remove_client_close_button_fn=telegram._group_automation_callbacks()["remove_close"],
        )
    if path == "bitrix":
        return phone.handoff(db, ticket, chat, notify, telegram._phone_callbacks(), technical=True)
    return group.handoff(db, ticket, None, chat, notify, callbacks=telegram._group_automation_callbacks(),
        reason="Фактическая ошибка отправки в группу")


@pytest.mark.parametrize("path", ["fallback", "generic", "bitrix", "group"])
@pytest.mark.parametrize("notify", [False, True])
@pytest.mark.parametrize("at,opened", [
    ("2026-11-02T07:49:59", False), ("2026-11-02T07:50:00", True),
    ("2026-11-02T18:59:59", True), ("2026-11-02T19:00:00", False), ("2026-11-08T12:00:00", False),
])
def test_four_handoff_boundaries(fallback_case, monkeypatch, path, notify, at, opened):
    c = fallback_case
    set_time(monkeypatch, at)
    initial_workflow = "task" if path == "generic" else "auto"
    c.task.workflow_type = initial_workflow
    c.task.client_close_message_id = None
    c.task.instruction_button_key = c.row.button_key
    c.task.quick_action_state_json = {phone.KEY: {"phase": "results", "deliveries": {}, "technical_error": "API failure"}}
    c.db.commit()
    customize(c.db)
    send = Mock(return_value={"ok": True, "result": {"message_id": 999}})
    monkeypatch.setattr(telegram, "send_message", send)
    ready = Mock(wraps=telegram._ready_ticket_for_distribution)
    monkeypatch.setattr(telegram, "_ready_ticket_for_distribution", ready)

    result = invoke(path, c, notify)
    c.db.commit()
    c.db.refresh(c.task)
    if opened:
        ready.assert_called_once()
        assert c.task.status != "closed"
        assert c.task.assignee_id == c.agent.id
    else:
        ready.assert_not_called()
        assert c.task.status == "closed" and c.task.closed_at is not None
        assert c.task.workflow_type == initial_workflow and c.task.assignee_id is None
        assert not c.task.agent_visible and not c.task.load_counted
        assert not c.db.scalars(select(TicketMessage).where(TicketMessage.message_type == "client")).all()
        assert not c.db.scalars(select(Ticket).where(Ticket.parent_ticket_id == c.task.id)).all()
        c.employee.assert_not_called()
        if result is not None:
            assert result["after_hours_blocked"] and not result.get("automation_handoff")
        if notify:
            send.assert_called_once_with(c.contact.external_chat_id, TEXT, parse_mode="HTML")
        else:
            send.assert_not_called()
        # Existing closed lifecycle is terminal even if reception subsequently opens.
        set_time(monkeypatch, "2026-11-03T08:00:00")
        invoke(path, c, notify)
        assert send.call_count == int(notify)
        ready.assert_not_called()


@pytest.mark.parametrize("failure", ["file", "message", "unavailable"])
def test_real_automatic_selection_fallback_closes_without_staff(fallback_case, monkeypatch, failure):
    c = fallback_case
    c.row.automation_key = {"file": "send_files", "message": "message_only", "unavailable": "unknown"}[failure]
    if failure == "file":
        c.row.files_json = ["saved:missing-file"]
    c.db.commit()
    customize(c.db)
    original_send = c.send
    def send(chat, text, **kwargs):
        if failure == "message" and text == "Автоматический ответ":
            raise RuntimeError("automatic message failed")
        return original_send(chat, text, **kwargs)
    monkeypatch.setattr(telegram, "send_message", send)
    ready = Mock(side_effect=AssertionError("No distribution"))
    monkeypatch.setattr(telegram, "_ready_ticket_for_distribution", ready)
    _, result = fallback_select(c)
    c.db.refresh(c.task)
    assert c.task.status == "closed" and c.task.workflow_type == "auto"
    assert c.task.assignee_id is None
    assert not result.get("automation_handoff")
    assert c.sent[-1][1:] == (TEXT, {"parse_mode": "HTML"})
    assert not any("передана в работу" in item[1] for item in c.sent)
    assert not c.db.scalars(select(TicketMessage).where(TicketMessage.message_type == "client")).all()
    assert any("Исходные данные автоматизации" in body for body in c.db.scalars(select(TicketMessage.body)))
    ready.assert_not_called()
    c.employee.assert_not_called()


@pytest.mark.parametrize("success", [False, True])
def test_group_copy_is_round_the_clock_but_failure_does_not_handoff(group_flow, monkeypatch, success):
    f = group_flow
    customize(f.db)
    group_select(f)
    f.sent.reset_mock()
    if not success:
        f.copied.side_effect = RuntimeError("group unavailable")
    ready = Mock(side_effect=AssertionError("No staff handoff"))
    monkeypatch.setattr(telegram, "_ready_ticket_for_distribution", ready)
    result = telegram.process_telegram_update(f.db, group_incoming(f), notify_client=True)
    assert f.ticket.status == "closed" and f.ticket.workflow_type == "auto"
    assert f.ticket.assignee_id is None
    assert f.db.scalars(select(TicketMessage).where(TicketMessage.message_type == "client")).one().body == "  Исходный текст клиента  "
    if success:
        assert result["automation_completed"]
        assert TEXT not in [call.args[1] for call in f.sent.call_args_list]
    else:
        assert result["after_hours_blocked"] and not result.get("automation_handoff")
        f.sent.assert_called_once_with(f.contact.external_chat_id, TEXT, parse_mode="HTML")
        assert any("Копирование в Telegram-группу не подтверждено" in body for body in f.db.scalars(select(TicketMessage.body)))
    assert telegram.recover_incomplete_group_automations(f.db) == 0
    f.copied.assert_called_once()
    ready.assert_not_called()


def test_bitrix_support_request_closes_without_marking_handoff(phone_flow):
    f = phone_flow
    customize(f.db)
    phone_select(f)
    phone_process(f, "1")
    f.sent.reset_mock()
    result = phone_action(f, "support")
    assert result["after_hours_blocked"]
    assert not phone.state(f.ticket).get("handed_off")
    assert f.ticket.status == "closed" and f.ticket.assignee_id is None
    f.sent.assert_called_once_with(f.contact.external_chat_id, TEXT, parse_mode="HTML")
    f.ready.assert_not_called()
    assert phone.cleanup(f.db, telegram._phone_callbacks()) == 0


def test_already_handed_off_bitrix_result_is_preserved(fallback_case, monkeypatch):
    c = fallback_case
    c.task.workflow_type, c.task.intake_state, c.task.status = "task", "ready", "new"
    c.task.assignee_id = c.agent.id
    c.task.quick_action_state_json = {phone.KEY: {"handed_off": True, "deliveries": {}}}
    c.db.commit()
    ready = Mock(side_effect=AssertionError("No second assignment"))
    monkeypatch.setattr(telegram, "_ready_ticket_for_distribution", ready)
    assert phone.handoff(c.db, c.task, c.contact.external_chat_id, False, telegram._phone_callbacks())["automation_handoff"]
    assert c.task.status == "new" and c.task.assignee_id == c.agent.id
    ready.assert_not_called()


@pytest.mark.parametrize("key", ["message_only", "send_files"])
def test_successful_automatic_output_outside_hours_is_unchanged(fallback_case, monkeypatch, key):
    c = fallback_case
    c.row.automation_key = key
    document = Mock(return_value={"ok": True, "result": {"message_id": 991}})
    monkeypatch.setattr(telegram, "send_document", document)
    if key == "send_files":
        saved = saved_files.create_saved_file(c.db, content=b"material", display_filename="material.pdf")
        c.row.files_json = [f"saved:{saved.id}"]
    c.db.commit()
    customize(c.db)
    _, result = fallback_select(c)
    assert c.task.status == "closed" and c.task.workflow_type == "auto"
    assert not result.get("automation_handoff")
    assert c.sent[0][1] == "Автоматический ответ"
    assert TEXT not in [item[1] for item in c.sent]
    c.employee.assert_not_called()
    if key == "send_files":
        document.assert_called_once()


def test_bitrix_technical_failure_retains_history_and_blocks_support(phone_flow):
    f = phone_flow
    customize(f.db)
    phone_select(f)
    f.http.side_effect = TimeoutError("Bitrix unavailable")
    assert phone_process(f, "1")["automation_collecting"]
    assert phone.state(f.ticket).get("technical_error")
    f.sent.reset_mock()
    assert phone_action(f, "support")["after_hours_blocked"]
    assert f.ticket.status == "closed" and not phone.state(f.ticket).get("handed_off")
    assert any("техническая ошибка Bitrix" in body for body in f.db.scalars(select(TicketMessage.body)))
    f.sent.assert_called_once_with(f.contact.external_chat_id, TEXT, parse_mode="HTML")
    f.ready.assert_not_called()
