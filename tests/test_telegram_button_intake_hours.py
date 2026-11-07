"""Button intake hours use synthetic databases and mocked Telegram only."""
import uuid
from datetime import datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from api.app.models import BotButtonSetting, StatsCompletion, StatsWorkInterval, TelegramUpdate, Ticket, TicketMessage
from api.app.routers import telegram
from api.app.services.automatic_messages import upsert_automatic_message_override
from api.app.services.ticket_admin_actions import (
    delete_uncreated_telegram_group_parent_task,
    is_uncreated_telegram_group_parent_task,
)
from api.app.services.telegram_intake_hours import is_telegram_intake_open
from test_telegram_preliminary_cancel import case, draft
from test_ticket_review_telegram import _callback_payload


@pytest.fixture(autouse=True)
def fixed_hours(monkeypatch):
    at = datetime(2026, 11, 2, 19, tzinfo=ZoneInfo("Europe/Moscow"))
    monkeypatch.setattr(telegram, "is_telegram_intake_open", lambda: is_telegram_intake_open(at))


def button(case, kind="ticket", **values):
    row = BotButtonSetting(id=str(uuid.uuid4()), menu_type="Support", button_key="chosen",
        button_label="Выбранная кнопка", button_path="Выбранная кнопка", workflow_type=kind,
        currently_present=True, **values)
    case.db.add(row)
    case.db.commit()
    return row


def choose(case, row, decision=None, notify=True, update_id=100):
    data = f"btnid:{row.id}" if decision is None else f"confirm:{decision}:btnid:{row.id}"
    payload = _callback_payload(update_id=update_id, contact=case.contact, data=data, message_id=55)
    return telegram.process_telegram_update(case.db, payload, notify_client=notify)


def prepared_draft(case, row):
    ticket = draft(case)
    telegram._prepare_collecting_ticket_for_button(case.db, ticket, telegram._button_from_db_row(case.db, row, "Support"), "Support")
    case.db.commit()
    return ticket


@pytest.mark.parametrize("kind", ["ticket", "task", "group_task"])
@pytest.mark.parametrize("decision", [None, "yes"])
@pytest.mark.parametrize("notify", [True, False])
@pytest.mark.parametrize("sunday", [True, False])
def test_blocking_deletes_only_uncreated_draft(case, monkeypatch, kind, decision, notify, sunday):
    if sunday:
        at = datetime(2026, 11, 8, 12, tzinfo=ZoneInfo("Europe/Moscow"))
        monkeypatch.setattr(telegram, "is_telegram_intake_open", lambda: is_telegram_intake_open(at))
    row = button(case, kind)
    ticket = prepared_draft(case, row)
    ticket_id = ticket.id
    case.db.add_all([
        StatsCompletion(id=str(uuid.uuid4()), company_id=case.company.id, object_id=ticket_id,
            object_type="ticket" if kind == "ticket" else "task"),
        StatsWorkInterval(id=str(uuid.uuid4()), company_id=case.company.id, object_id=ticket_id,
            object_type="ticket" if kind == "ticket" else "task", user_id=case.agent.id, started_at=datetime.utcnow()),
    ])
    custom = "<b>Настроенный текст</b>: приём закрыт."
    upsert_automatic_message_override(case.db, "telegram.after_hours.creation_blocked.client", custom)
    case.db.commit()
    ready, children = Mock(), Mock()
    monkeypatch.setattr(telegram, "_ready_ticket_for_distribution", ready)
    monkeypatch.setattr(telegram, "_create_group_children_from_collecting", children)
    menu = Mock()
    monkeypatch.setattr(telegram, "_send_problem_menu_for_contact", menu)

    assert choose(case, row, decision, notify) == {"ok": True, "after_hours_blocked": True}
    case.db.rollback()
    assert case.db.get(Ticket, ticket_id) is None
    assert case.db.scalars(select(Ticket)).all() == []
    assert case.db.scalars(select(TicketMessage)).all() == []
    for model in (StatsCompletion, StatsWorkInterval):
        assert case.db.scalar(select(model.id).where(model.object_id == ticket_id)) is None
    assert case.db.scalar(select(TelegramUpdate.update_id)) == "100"
    ready.assert_not_called()
    children.assert_not_called()
    menu.assert_not_called()
    if notify:
        case.send.assert_called_once_with(case.contact.external_chat_id, custom, parse_mode="HTML")
        telegram.answer_callback_query.assert_called_once()
        assert any(call.args == (case.contact.external_chat_id, 55) for call in case.removed.call_args_list)
    else:
        case.send.assert_not_called()
        case.removed.assert_not_called()
        telegram.answer_callback_query.assert_not_called()


@pytest.mark.parametrize("kind", ["ticket", "task", "group_task"])
def test_selection_without_draft_creates_nothing(case, kind):
    row = button(case, kind)
    assert choose(case, row, notify=False)["after_hours_blocked"]
    assert case.db.scalars(select(Ticket)).all() == []


@pytest.mark.parametrize("kind", ["ticket", "task", "group_task"])
def test_selection_before_closing_then_confirmation_after_closing(case, monkeypatch, kind):
    row = button(case, kind, confirmation_enabled=True, confirmation_text="Подтвердите")
    monkeypatch.setattr(telegram, "is_telegram_intake_open", lambda: True)
    assert choose(case, row, notify=False)["awaiting_confirmation"]
    monkeypatch.setattr(telegram, "is_telegram_intake_open", lambda: False)
    assert choose(case, row, "yes", notify=False, update_id=101)["after_hours_blocked"]
    assert case.db.scalars(select(Ticket)).all() == []


@pytest.mark.parametrize("decision", [None, "yes"])
def test_crossing_closing_time_before_ready_is_blocked(case, monkeypatch, decision):
    row = button(case)
    if decision:
        prepared_draft(case, row)
    monkeypatch.setattr(telegram, "is_telegram_intake_open", Mock(side_effect=[True, False]))
    ready = Mock()
    monkeypatch.setattr(telegram, "_ready_ticket_for_distribution", ready)
    assert choose(case, row, decision, notify=False)["after_hours_blocked"]
    assert case.db.scalars(select(Ticket)).all() == []
    assert case.db.scalars(select(TicketMessage)).all() == []
    ready.assert_not_called()


def test_negative_confirmation_is_unchanged(case, monkeypatch):
    row = button(case, "task")
    ticket = prepared_draft(case, row)
    check = Mock(side_effect=AssertionError("No hours check for cancellation"))
    monkeypatch.setattr(telegram, "is_telegram_intake_open", check)
    menu = Mock()
    monkeypatch.setattr(telegram, "_send_problem_menu_for_contact", menu)
    assert choose(case, row, "no")["confirmation"] == "no"
    assert case.db.get(Ticket, ticket.id) is not None
    menu.assert_called_once()
    check.assert_not_called()


@pytest.mark.parametrize("kind", ["ticket", "task", "group_task"])
@pytest.mark.parametrize("decision", [None, "yes"])
def test_working_hours_preserve_creation(case, monkeypatch, kind, decision):
    row = button(case, kind)
    if decision:
        prepared_draft(case, row)
    monkeypatch.setattr(telegram, "is_telegram_intake_open", lambda: True)
    result = choose(case, row, decision, notify=False)
    assert not result.get("after_hours_blocked")
    ticket = case.db.get(Ticket, result["ticket_id"])
    assert ticket.intake_state == ("ready" if kind == "ticket" else "collecting")
    if kind != "ticket":
        assert result["task_pending"]


@pytest.mark.parametrize("kind", ["auto", "ticket", "task", "group_task"])
@pytest.mark.parametrize("key", ["telegram_group", "client_origin", "bitrix_deal_phone_lookup", "bitrix_phone_deal_lookup"])
@pytest.mark.parametrize("decision", [None, "yes"])
def test_known_automations_reach_existing_handlers(case, monkeypatch, kind, key, decision):
    row = button(case, kind, automation_key=key, automation_overridden=True)
    if decision:
        prepared_draft(case, row)
    check = Mock(side_effect=AssertionError("Automation must bypass hours"))
    monkeypatch.setattr(telegram, "is_telegram_intake_open", check)
    names = {"telegram_group": "_begin_group_message_wait", "client_origin": "_begin_client_origin_wait",
        "bitrix_deal_phone_lookup": "_begin_bitrix_phone_wait", "bitrix_phone_deal_lookup": "_begin_bitrix_phone_wait"}
    for name in set(names.values()):
        monkeypatch.setattr(telegram, name, Mock(return_value={"ok": True, "automation_wait": True} if name == names[key] else None))
    assert choose(case, row, decision, notify=False)["automation_wait"]
    getattr(telegram, names[key]).assert_called_once()
    assert len(case.db.scalars(select(Ticket)).all()) == 1
    check.assert_not_called()


@pytest.mark.parametrize("decision", [None, "yes"])
def test_auto_button_without_key_still_runs(case, decision):
    row = button(case, "auto")
    if decision:
        prepared_draft(case, row)
    assert choose(case, row, decision, notify=False)["workflow_type"] == "auto"


def test_unknown_automation_key_does_not_bypass_hours(case):
    row = button(case, automation_key="unknown")
    assert choose(case, row, notify=False)["after_hours_blocked"]
    assert case.db.scalars(select(Ticket)).all() == []


@pytest.mark.parametrize("changes", [
    {"intake_state": "ready"}, {"status": "closed", "closed_at": datetime(2026, 11, 1)},
    {"quick_action_state_json": {"client_origin": {"deadline": 9999999999}}},
    {"workflow_type": "auto"}, {"channel": "web"},
])
def test_existing_non_drafts_are_preserved(case, changes):
    row = button(case)
    ticket = draft(case)
    for key, value in changes.items():
        setattr(ticket, key, value)
    case.db.commit()
    ticket_id = ticket.id
    result = choose(case, row, notify=False)
    assert result["ok"]
    case.db.expire_all()
    assert case.db.get(Ticket, ticket_id) is not None
    for key, value in changes.items():
        assert getattr(case.db.get(Ticket, ticket_id), key) == value


def test_active_ticket_takes_precedence(case):
    row = button(case)
    ticket = draft(case)
    ticket.intake_state, ticket.status = "ready", "new"
    case.db.commit()
    assert choose(case, row)["already_active"]
    assert case.db.get(Ticket, ticket.id).status == "new"


@pytest.mark.parametrize("kind", ["ticket", "task", "group_task"])
def test_client_data_is_preserved_on_block(case, kind):
    row = button(case, kind)
    ticket = prepared_draft(case, row)
    message = TicketMessage(id=str(uuid.uuid4()), ticket_id=ticket.id, message_type="client", body="Описание")
    case.db.add(message)
    case.db.commit()
    assert choose(case, row, "yes", notify=False)["after_hours_blocked"]
    assert case.db.get(Ticket, ticket.id) is not None
    assert case.db.get(TicketMessage, message.id) is not None


@pytest.mark.parametrize("changes", [
    {"channel": "web"}, {"workflow_type": "ticket"}, {"task_kind": "normal"},
    {"intake_state": "ready"}, {"status": "new"}, {"assignee_id": "agent"},
    {"closed_at": datetime(2026, 11, 1)}, {"ticket_number": 1},
    {"parent_ticket_id": "parent"}, {"is_special": True},
    {"quick_action_state_json": {"active": True}},
])
def test_group_parent_predicate_is_strict(case, changes):
    row = button(case, "group_task")
    ticket = prepared_draft(case, row)
    assert is_uncreated_telegram_group_parent_task(case.db, ticket)
    for key, value in changes.items():
        if key == "assignee_id":
            value = case.agent.id
        if key == "parent_ticket_id":
            value = draft(case).id
        setattr(ticket, key, value)
    assert not is_uncreated_telegram_group_parent_task(case.db, ticket)
    with pytest.raises(ValueError):
        delete_uncreated_telegram_group_parent_task(case.db, ticket)


@pytest.mark.parametrize("evidence", ["client", "child", "automation"])
def test_group_parent_rejects_pending_database_evidence(case, evidence):
    row = button(case, "group_task")
    ticket = prepared_draft(case, row)
    if evidence == "client":
        case.db.add(TicketMessage(id=str(uuid.uuid4()), ticket_id=ticket.id, message_type="client", body="Данные"))
    elif evidence == "child":
        child = draft(case)
        child.parent_ticket_id = ticket.id
    else:
        row.automation_key = "telegram_group"
    # No commit: the predicate must flush before checking children/messages/settings.
    assert not is_uncreated_telegram_group_parent_task(case.db, ticket)
    with pytest.raises(ValueError):
        delete_uncreated_telegram_group_parent_task(case.db, ticket)
    assert case.db.get(Ticket, ticket.id) is not None


def test_group_parent_with_children_is_preserved_on_block(case):
    row = button(case, "group_task")
    parent = prepared_draft(case, row)
    child = draft(case)
    child.parent_ticket_id = parent.id
    case.db.commit()
    ids = {parent.id, child.id}
    assert choose(case, row, "yes", notify=False)["after_hours_blocked"]
    assert set(case.db.scalars(select(Ticket.id))) == ids


@pytest.mark.parametrize("kind", ["ticket", "task", "group_task"])
def test_automation_draft_is_not_deleted_when_ordinary_button_is_blocked(case, kind):
    row = button(case, kind)
    ticket = prepared_draft(case, row)
    automation = BotButtonSetting(id=str(uuid.uuid4()), menu_type="Support", button_key="automation",
        button_label="Автоматизация", button_path="Автоматизация", workflow_type="task", automation_key="send_files")
    case.db.add(automation)
    ticket.button_config_key = ticket.instruction_button_key = automation.button_key
    case.db.commit()
    ticket_id = ticket.id
    assert choose(case, row, notify=False)["after_hours_blocked"]
    assert case.db.get(Ticket, ticket_id).instruction_button_key == "automation"


def test_navigation_is_not_blocked(case, monkeypatch):
    check = Mock(side_effect=AssertionError("Navigation must bypass intake hours"))
    monkeypatch.setattr(telegram, "is_telegram_intake_open", check)
    payload = _callback_payload(update_id=100, contact=case.contact, data="menu:__root__", message_id=55)
    result = telegram.process_telegram_update(case.db, payload, notify_client=False)
    assert result == {"ok": True, "menu_key": None}
    check.assert_not_called()


def test_disabled_button_is_checked_before_hours(case, monkeypatch):
    row = button(case)
    monkeypatch.setattr(telegram, "_is_callback_button_enabled", lambda *args, **kwargs: False)
    check = Mock(side_effect=AssertionError("Disabled buttons are rejected first"))
    monkeypatch.setattr(telegram, "is_telegram_intake_open", check)
    assert choose(case, row, notify=False)["ignored"] == "button_disabled"
    check.assert_not_called()
