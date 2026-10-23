from itertools import count
from unittest.mock import Mock

import pytest
from sqlalchemy import select, func

from api.app.models import Ticket, TicketMessage, User
from api.app.routers import telegram
from api.app.routers.telegram import _ready_ticket_for_distribution as ready_for_distribution
from api.app.services import assignment
from api.app.services import telegram_bitrix_phone as phone, telegram_client_origin as origin
from test_telegram_group_registry import case
from test_telegram_client_origin import flow, choose, incoming, response
from test_telegram_bitrix_phone import phone_flow
from test_telegram_phone_deals import search_flow


@pytest.fixture(params=["origin", "phone", "search"])
def repeated(request):
    f = request.getfixturevalue({"origin": "flow", "phone": "phone_flow", "search": "search_flow"}[request.param])
    f.kind = request.param
    f.engine = origin if f.kind == "origin" else phone
    f.body = "+79991234567" if f.kind == "search" else "2528563"
    if f.kind == "origin":
        f.http.side_effect = lambda *a, **kw: response({"result": {"UF_CRM_MARK": "Результат поиска"}})
    ids = count(700)
    f.sent.side_effect = lambda *a, **kw: {"ok": True, "result": {"message_id": next(ids)}}
    if f.kind != "origin":
        f.rich.side_effect = lambda *a, **kw: {"ok": True, "result": {"message_id": next(ids)}}
    choose(f)
    return f


def process(f, update_id):
    return telegram.process_telegram_update(f.db, incoming(f, f.body, update_id), notify_client=True)


def callback(f, data, message_id, update_id):
    payload = {"update_id": update_id, "callback_query": {"id": "repeat", "data": data,
        "from": {"id": int(f.contact.external_user_id)}, "message": incoming(f, "")["message"]}}
    payload["callback_query"]["message"]["message_id"] = int(message_id)
    return telegram.process_telegram_update(f.db, payload, notify_client=True)


def result_call(f):
    return f.rich.call_args if f.kind == "search" else f.sent.call_args


def state(f):
    return f.ticket.quick_action_state_json[origin.KEY] if f.kind == "origin" else phone.state(f.ticket)


def test_success_repeat_same_ticket_preserves_history_and_invalidates_old_buttons(repeated):
    f = repeated
    ticket_id = f.ticket.id
    f.ticket.quick_action_state_json = {**f.ticket.quick_action_state_json, "unrelated": {"keep": True}}
    f.db.commit()
    old_buttons = []
    for run in range(4):
        update = 20 + run * 10
        result = process(f, update)
        assert not result["closed"] and f.ticket.status != "closed" and f.ticket.id == ticket_id
        assert f.ticket.workflow_type == "auto"
        assert state(f)["phase"] == "success"
        call = result_call(f)
        buttons = call.kwargs["reply_markup"]["inline_keyboard"]
        label = {"origin": "Запрос по другой сделке", "phone": "Расшифр. другой номер", "search": "Найти другую сделку"}[f.kind]
        assert [row[0]["text"] for row in buttons] == [label, "Закрыть обращение"]
        message_id = f.ticket.client_close_message_id
        old_buttons.append((buttons, message_id))
        api_calls = f.http.call_count
        assert process(f, update)["duplicate"]
        assert f.http.call_count == api_calls
        if run == 3:
            break
        assert callback(f, buttons[0][0]["callback_data"], message_id, update + 1)["automation_collecting"]
        assert f.ticket.workflow_type == "auto"
        prompt = f.sent.call_args.args[1]
        assert "Отправьте" in prompt
        assert f.http.call_count == api_calls
        assert callback(f, buttons[0][0]["callback_data"], message_id, update + 1)["duplicate"]
        for offset, row in enumerate(buttons):
            assert callback(f, row[0]["callback_data"], message_id, update + 2 + offset)["ignored"]
        assert f.ticket.status != "closed" and f.ticket.quick_action_state_json["unrelated"] == {"keep": True}
    assert f.db.scalar(select(func.count()).select_from(Ticket)) == 1
    assert f.db.scalar(select(func.count()).select_from(TicketMessage).where(TicketMessage.message_type == "client")) == 4
    system = f.db.scalars(select(TicketMessage).where(TicketMessage.message_type == "system")).all()
    marker = "Откуда клиент:" if f.kind == "origin" else "Поиск сделки по номеру телефона" if f.kind == "search" else "Расшифровка номера Bitrix: success"
    assert sum(message.body.startswith(marker) for message in system) == 4
    title = {"origin": "Откуда клиент", "phone": "Расшифровка номера Bitrix", "search": "Поиск сделки по номеру телефона"}[f.kind]
    repeats = [message for message in system if message.body == f"Повторный запуск автоматизации: {title}"]
    assert len(repeats) == 3 and all(message.agent_visible for message in repeats)
    assert not any("Повторный запуск автоматизации:" in call.args[1] for call in f.sent.call_args_list)
    assert callback(f, old_buttons[-1][0][1][0]["callback_data"], old_buttons[-1][1], 80)["closed"]
    assert callback(f, old_buttons[-1][0][1][0]["callback_data"], old_buttons[-1][1], 80)["duplicate"]
    assert f.ticket.assignee_id is None
    telegram._ready_ticket_for_distribution.assert_not_called()


def test_result_and_repeat_keep_ten_minute_client_deadline(repeated, monkeypatch):
    f = repeated
    clock = [1000.0]
    monkeypatch.setattr(f.engine, "now", lambda: clock[0])
    process(f, 20)
    assert state(f)["deadline"] == 1600
    button = result_call(f).kwargs["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    clock[0] = 1550
    callback(f, button, f.ticket.client_close_message_id, 21)
    assert state(f)["deadline"] == 2150
    clock[0] = 1600
    process(f, 22)
    assert state(f)["deadline"] == 2200
    callbacks = telegram._origin_callbacks() if f.kind == "origin" else telegram._phone_callbacks()
    clock[0] = 2151
    assert f.engine.cleanup(f.db, callbacks) == 0
    clock[0] = 2201
    assert f.engine.cleanup(f.db, callbacks) == 1
    assert f.engine.cleanup(f.db, callbacks) == 0
    telegram._ready_ticket_for_distribution.assert_not_called()


def test_result_recovery_does_not_repeat_confirmed_delivery_or_api(repeated):
    f = repeated
    process(f, 20)
    api_calls, messages = f.http.call_count, f.sent.call_count
    rich_calls = f.rich.call_count if f.kind != "origin" else 0
    f.db.expire_all()
    process(f, 21)
    assert f.http.call_count == api_calls and f.sent.call_count == messages
    if f.kind != "origin":
        assert f.rich.call_count == rich_calls
    assert state(f)["phase"] == "success" and f.ticket.status != "closed"


def test_uncertain_result_is_not_resent_and_customer_can_still_close(repeated):
    f = repeated
    original = f.sent.side_effect
    def send(chat, text, **kwargs):
        if (kwargs.get("reply_markup") or {}).get("inline_keyboard", [[{}]])[0][0].get("text") in {
            "Запрос по другой сделке", "Расшифр. другой номер"}:
            return {"ok": True}
        return original(chat, text, **kwargs)
    f.sent.side_effect = send
    if f.kind == "search":
        f.rich.side_effect = lambda *a, **kw: {"ok": True}
    assert not process(f, 20).get("closed")
    api_calls = f.http.call_count
    attempts = f.rich.call_count if f.kind == "search" else sum(" — " in call.args[1] or "Результат поиска" in call.args[1] for call in f.sent.call_args_list)
    process(f, 21)
    assert f.http.call_count == api_calls
    assert attempts == (f.rich.call_count if f.kind == "search" else sum(" — " in call.args[1] or "Результат поиска" in call.args[1] for call in f.sent.call_args_list))
    closed = telegram.process_telegram_update(f.db, incoming(f, "/close", 22), notify_client=True)
    assert closed["closed"]


def test_repeat_cannot_reset_a_pending_explicit_close(repeated):
    f = repeated
    process(f, 20)
    buttons = result_call(f).kwargs["reply_markup"]["inline_keyboard"]
    message_id = f.ticket.client_close_message_id
    original = f.sent.side_effect
    f.sent.side_effect = lambda *a, **kw: {"ok": False} if kw.get("reply_markup") is None else original(*a, **kw)
    assert not callback(f, buttons[1][0]["callback_data"], message_id, 21)["closed"]
    assert state(f).get("finish")
    assert callback(f, buttons[0][0]["callback_data"], message_id, 22)["ignored"]
    f.sent.side_effect = original
    assert telegram.process_telegram_update(f.db, incoming(f, "/close", 23), notify_client=True)["closed"]


def test_parallel_repeat_callbacks_restart_only_once(repeated, tmp_path):
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    f = repeated
    process(f, 20)
    data = result_call(f).kwargs["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    message_id = int(f.ticket.client_close_message_id)
    path = tmp_path / "repeat.db"
    with sqlite3.connect(path) as target:
        f.db.connection().connection.driver_connection.backup(target)
    db_engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=db_engine, autoflush=False)
    entered, release = Event(), Event()
    original = f.sent.side_effect
    prompt = f.sent.call_args_list[0].args[1]
    def sending(chat, text, **kwargs):
        if text == prompt:
            entered.set()
            assert release.wait(10)
        return original(chat, text, **kwargs)
    f.sent.side_effect = sending
    requests = f.http.call_count
    def run(update_id):
        payload = {"update_id": update_id, "callback_query": {"id": str(update_id), "data": data,
            "from": {"id": int(f.contact.external_user_id)}, "message": incoming(f, "")["message"]}}
        payload["callback_query"]["message"]["message_id"] = message_id
        with factory() as db:
            return telegram.process_telegram_update(db, payload, notify_client=True)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(run, 40)
            assert entered.wait(10)
            second = workers.submit(run, 41)
            release.set()
            assert first.result(timeout=10)["automation_collecting"]
            assert second.result(timeout=10)["ignored"]
        assert f.http.call_count == requests
        assert sum(call.args[1] == prompt for call in f.sent.call_args_list) == 2
        with factory() as db:
            repeats = db.scalars(select(TicketMessage).where(TicketMessage.body.like("Повторный запуск автоматизации:%"))).all()
            assert len(repeats) == 1 and repeats[0].agent_visible
    finally:
        release.set()
        db_engine.dispose()


@pytest.mark.parametrize("fixture", ["phone_flow", "search_flow"])
@pytest.mark.parametrize("failure", [False, True])
def test_explicit_support_converts_auto_and_assigns_online_agent_once(request, monkeypatch, fixture, failure):
    from urllib.error import URLError
    f = request.getfixturevalue(fixture)
    choose(f)
    agent = User(id="online-agent", company_id=f.ticket.company_id, full_name="Онлайн сотрудник",
                 role="agent", is_active=True, availability_status="online")
    f.db.add(agent)
    f.db.commit()
    notifications = Mock()
    monkeypatch.setattr(assignment, "schedule_assignment_notification", notifications)
    monkeypatch.setattr(telegram, "_ready_ticket_for_distribution", ready_for_distribution)
    f.http.side_effect = URLError("offline") if failure else lambda *a, **kw: response({"result": {}})
    body = "1" if fixture == "phone_flow" else "+79991234567"
    assert telegram.process_telegram_update(f.db, incoming(f, body, 20), notify_client=True)["automation_collecting"]
    assert f.ticket.workflow_type == "auto" and f.ticket.assignee_id is None
    notifications.assert_not_called()
    # The global guard must still reject AUTO even when an online agent exists.
    f.ticket.intake_state = "ready"
    assert assignment.assign_ticket_to_agent(f.db, f.ticket, agent) is False
    f.ticket.intake_state = "collecting"
    f.db.commit()
    message_id = f.ticket.client_close_message_id
    data = (phone.PREFIX if fixture == "phone_flow" else phone.PHONE_DEALS_PREFIX) + "support:" + f.ticket.id
    assert callback(f, data, message_id, 21)["automation_handoff"]
    f.db.expire_all()
    assert f.ticket.workflow_type == "task" and f.ticket.assignee_id == agent.id
    assert f.ticket.status == "new" and f.ticket.intake_state == "ready" and f.ticket.load_counted
    notifications.assert_called_once()
    assert notifications.call_args.kwargs["new_assignee_id"] == agent.id
    assert callback(f, data, message_id, 21)["duplicate"]
    assert callback(f, data, message_id, 22)["ignored"]
    with f.factory() as restarted:
        telegram.cleanup_expired_task_intents(restarted)
    notifications.assert_called_once()
