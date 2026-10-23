from unittest.mock import Mock
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, parse_qs

import pytest
from sqlalchemy import select

from api.app.models import TicketMessage
from api.app.routers import telegram
from api.app.services import telegram_bitrix_phone as engine, bitrix_phone_deals
from api.app.services.automatic_messages import upsert_automatic_message_override
from test_telegram_group_registry import case
from test_telegram_client_origin import flow, choose, incoming, response
from test_telegram_bitrix_phone import phone_flow, process


def action(f, name, update_id=20, *, ticket_id=None, message_id=None):
    payload = {"update_id": update_id, "callback_query": {"id": "action", "data": engine.PHONE_DEALS_PREFIX + name + ":" + (ticket_id or f.ticket.id),
        "from": {"id": int(f.contact.external_user_id)}, "message": incoming(f, "")["message"]}}
    payload["callback_query"]["message"]["message_id"] = int(message_id or f.ticket.client_close_message_id)
    return telegram.process_telegram_update(f.db, payload, notify_client=True)


@pytest.fixture
def search_flow(phone_flow):
    f = phone_flow
    f.button.automation_key = engine.PHONE_DEALS_KEY
    f.db.commit()
    def http(request, **kwargs):
        method = urlparse(request.full_url).path.rsplit("/", 1)[-1]
        if method == "crm.duplicate.findbycomm.json":
            return response({"result": {"CONTACT": [714865]}})
        if method == "crm.deal.list.json":
            return response({"result": [{"ID": "2415340", "CONTACT_ID": "714865", "ASSIGNED_BY_ID": "11037"},
                                        {"ID": "2555931", "CONTACT_ID": "714865", "ASSIGNED_BY_ID": "11037"}]})
        assert method == "user.get.json" and request.full_url.startswith("https://mark.invalid/")
        return response({"result": [{"ID": "11037", "NAME": "Иван", "LAST_NAME": "Иванов"}]})
    f.http.side_effect = http
    return f


def summary(f):
    return f.db.scalars(select(TicketMessage).where(TicketMessage.message_type == "system", TicketMessage.body.like("Поиск сделки по номеру телефона%"))).all()


@pytest.mark.parametrize("single", [True, False])
def test_success_uses_same_lifecycle_rich_links_and_one_history_summary(search_flow, single):
    f = search_flow
    original = f.http.side_effect
    if single:
        def http(request, **kw):
            data = original(request, **kw)
            if "crm.deal.list" in request.full_url:
                return response({"result": [{"ID": "2415340", "CONTACT_ID": "714865", "ASSIGNED_BY_ID": "11037"}]})
            return data
        f.http.side_effect = http
    assert choose(f)["automation_collecting"]
    body = "8 (999) 123-45-67"
    result = process(f, body)
    assert not result["closed"] and f.ticket.assignee_id is None and not f.ticket.load_counted
    html = f.rich.call_args.args[1]["html"]
    assert "<th>Сделка</th><th>Ответственный</th>" in html
    assert '<a href="https://crm.elitesochi.com/crm/deal/show/2415340/">2415340</a>' in html
    assert "Иванов Иван" in html
    assert len(f.rich.call_args.kwargs["reply_markup"]["inline_keyboard"]) == 2
    assert f.db.scalar(select(TicketMessage.body).where(TicketMessage.message_type == "client")) == body
    assert len(summary(f)) == 1 and summary(f)[0].agent_visible
    assert "2415340 — Иванов Иван" in summary(f)[0].body
    assert telegram.process_telegram_update(f.db, incoming(f, body), notify_client=True)["duplicate"]
    assert f.rich.call_count == 1
    f.ready.assert_not_called()


@pytest.mark.parametrize("body", ["1234567890", "abc", "", "89991234567\n89991234568"])
def test_invalid_input_keeps_waiting_without_api(search_flow, body):
    f = search_flow
    choose(f)
    assert process(f, body)["automation_collecting"]
    f.http.assert_not_called()
    assert "не менее 11" in f.sent.call_args.args[1]
    assert len(f.sent.call_args.kwargs["reply_markup"]["inline_keyboard"]) == 1
    assert not process(f, "+79991234567", 12)["closed"]


@pytest.mark.parametrize("failure", [401, 500, "offline", "not_found", "no_deals", "no_contact", "name_401"])
def test_errors_and_absence_offer_two_buttons_without_automatic_distribution(search_flow, failure):
    f = search_flow
    original = f.http.side_effect
    def http(request, **kw):
        if failure == "name_401" and "user.get" not in request.full_url:
            return original(request, **kw)
        if failure == "no_contact":
            return response({"result": {}})
        if failure == "no_deals":
            return original(request, **kw) if "duplicate" in request.full_url else response({"result": []})
        if failure == "offline":
            raise URLError("offline")
        raise HTTPError("https://test.invalid/", 400 if failure == "not_found" else 401 if failure == "name_401" else failure,
                        "error", {}, response({"error_description": "Not found"}))
    f.http.side_effect = http
    choose(f)
    assert not process(f, "+79991234567").get("closed")
    assert engine.waiting(f.ticket)
    f.ready.assert_not_called()
    last = f.rich.call_args or f.sent.call_args
    buttons = last.kwargs["reply_markup"]["inline_keyboard"]
    assert [row[0]["text"] for row in buttons] == ["Связаться с сотрудником", "Закрыть обращение"]
    if failure == "name_401":
        assert "ID 11037" in last.args[1]["html"]
    before = f.http.call_count
    assert action(f, "support")["automation_handoff"]
    f.ready.assert_called_once()
    assert len(summary(f)) == 1 and "по просьбе клиента" in summary(f)[0].body
    assert action(f, "support", 21)["ignored"]
    assert f.http.call_count == before


@pytest.mark.parametrize("phase", ["input", "results", "error"])
def test_both_ten_minute_timeouts_never_distribute(search_flow, monkeypatch, phase):
    f = search_flow
    clock = [1000.0]
    monkeypatch.setattr(engine, "now", lambda: clock[0])
    choose(f)
    if phase != "input":
        f.http.side_effect = URLError("offline") if phase == "error" else lambda *a, **kw: response({"result": {}})
        process(f, "+79991234567")
    deadline = engine.state(f.ticket)["deadline"]
    assert deadline == 1600
    clock[0] = deadline - 1
    assert engine.cleanup(f.db, telegram._phone_callbacks()) == 0
    clock[0] = deadline + 1
    assert engine.cleanup(f.db, telegram._phone_callbacks()) == 1
    assert engine.cleanup(f.db, telegram._phone_callbacks()) == 0
    assert len(summary(f)) == 1
    f.ready.assert_not_called()


@pytest.mark.parametrize("command", ["/close", "/start", "callback"])
def test_customer_close_and_stale_buttons_use_existing_protection(search_flow, command):
    f = search_flow
    choose(f)
    old = f.ticket.client_close_message_id
    if command == "callback":
        assert action(f, "close", message_id=int(old) + 1)["ignored"]
        result = action(f, "close", 21)
    else:
        result = process(f, command)
    assert result["closed"]
    assert action(f, "close", 22, message_id=old)["ignored"]
    if command == "/start":
        telegram._send_problem_menu_for_contact.assert_called_once()
    f.ready.assert_not_called()


def test_interrupted_query_restores_safe_choices_without_repeating_api(search_flow):
    f = search_flow
    choose(f)
    engine.save(f.db, f.ticket, phone_input="+79991234567", phase="processing", inflight="+79991234567",
                rows=[{"id": "2415340", "responsible_id": "11037", "responsible": None, "complete": False}])
    f.db.expire_all()
    assert engine.cleanup(f.db, telegram._phone_callbacks()) == 0
    assert not engine.state(f.ticket).get("inflight")
    assert engine.state(f.ticket)["phase"] == "results"
    engine.cleanup(f.db, telegram._phone_callbacks())
    f.http.assert_not_called()
    assert f.rich.call_count == 1
    f.ready.assert_not_called()


@pytest.mark.parametrize("delivery", ["rejected", "uncertain", "terminal_rejected"])
def test_delivery_confirmation_and_recovery_do_not_repeat_lookup(search_flow, delivery):
    f = search_flow
    choose(f)
    if delivery in {"rejected", "uncertain"}:
        f.rich.side_effect = None
        f.rich.return_value = {"ok": False} if delivery == "rejected" else {"ok": True}
    else:
        original = f.sent.side_effect
        f.sent.side_effect = lambda *a, **kw: {"ok": False} if "Обращение по поиску сделки закрыто" in a[1] else original(*a, **kw)
    result = process(f, "+79991234567")
    if delivery == "terminal_rejected":
        result = action(f, "close")
    count = f.http.call_count
    if delivery == "rejected":
        assert not result["closed"]
        fallback = next(call.args[1] for call in f.sent.call_args_list if "2415340</a>" in call.args[1])
        assert "https://crm.elitesochi.com/crm/deal/show/2415340/" in fallback
    else:
        assert not result.get("closed")
        if delivery == "terminal_rejected":
            f.sent.side_effect = lambda *a, **kw: {"ok": True, "result": {"message_id": 900}}
        engine.resume(f.db, f.ticket, f.contact.external_chat_id, True, telegram._phone_callbacks())
        assert (f.ticket.status == "closed") == (delivery == "terminal_rejected")
    assert f.http.call_count == count and f.rich.call_count == 1
    f.ready.assert_not_called()


def test_custom_templates_wrap_rich_and_plain_results_and_preserve_answer(search_flow):
    f = search_flow
    for key, body in [("prompt", "Мой запрос"), ("checking", "Мой поиск"), ("result_full", "<b>До</b> {{ответ}} <i>После</i>")]:
        upsert_automatic_message_override(f.db, "telegram.bitrix_phone_deals." + key, body)
    f.db.commit()
    choose(f)
    assert f.sent.call_args.args[1] == "Мой запрос"
    assert not process(f, "+79991234567")["closed"]
    assert any(call.args[1] == "Мой поиск" for call in f.sent.call_args_list)
    html = f.rich.call_args.args[1]["html"]
    assert html.startswith("<b>До</b>") and html.endswith("<i>После</i>") and "2415340" in html
    rows = engine.state(f.ticket)["rows"]
    plain, mode = engine.wrap_result(f.db, f.ticket, rows)
    assert "До" in plain and "После" in plain and "2415340" in plain and mode == "HTML"
    upsert_automatic_message_override(f.db, "telegram.bitrix_phone_deals.result_full", "Без тега")
    assert "2415340" in engine.wrap_result(f.db, f.ticket, rows, rich=True)[0]


def test_new_type_can_be_saved_in_button_settings_and_has_editable_catalog(search_flow):
    f = search_flow
    from api.app.services.button_automation_types import is_automation_allowed_for_workflow
    assert is_automation_allowed_for_workflow("auto", engine.PHONE_DEALS_KEY)
    saved = f.client.put("/api/v1/settings/bot-buttons/item/instruction",
        params={"button_key": f.button.button_key, "menu_type": "Menu", "workflow_type": "auto", "button_path": "Origin", "button_label": "Откуда клиент"},
        json={"automation_overridden": True, "automation_type": engine.PHONE_DEALS_KEY})
    assert saved.status_code == 200, saved.text
    f.db.refresh(f.button)
    assert f.button.automation_key == engine.PHONE_DEALS_KEY
    payload = f.client.get("/api/v1/settings/automatic-messages").json()
    block = next(category for category in payload["categories"] if category["category"] == "bitrix_phone_deals")
    assert block["category_title"] == "Поиск сделки по номеру телефона"
    assert len(block["items"]) == 20
    for item in block["items"]:
        result = f.client.patch(f"/api/v1/settings/automatic-messages/{item['key']}", json={"body": item["effective_text"]})
        assert result.status_code == 200, result.text


@pytest.mark.parametrize("second_kind", ["duplicate", "same_text", "close"])
def test_parallel_search_and_cleanup_reuse_chat_lock(search_flow, tmp_path, monkeypatch, second_kind):
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    f = search_flow
    clock = [1000]
    monkeypatch.setattr(engine, "now", lambda: clock[0])
    choose(f)
    path = tmp_path / "parallel.db"
    with sqlite3.connect(path) as target:
        f.db.connection().connection.driver_connection.backup(target)
    db_engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=db_engine, autoflush=False)
    entered, release = Event(), Event()
    original = f.http.side_effect
    def request(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            assert release.wait(10)
        return original(*args, **kwargs)
    f.http.side_effect = request
    def run(update):
        with factory() as db:
            return telegram.process_telegram_update(db, update, notify_client=True)
    update = incoming(f, "+79991234567")
    second = update if second_kind == "duplicate" else incoming(f, "/close" if second_kind == "close" else "+79991234567", 12)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(run, update)
            assert entered.wait(10)
            following = workers.submit(run, second)
            clock[0] = 1800
            with factory() as db:
                assert telegram.cleanup_expired_task_intents(db) == 0
            release.set()
            assert not first.result(timeout=10)["closed"]
            result = following.result(timeout=10)
            if second_kind == "duplicate":
                assert result["duplicate"]
        assert f.http.call_count == 5
        f.ready.assert_not_called()
    finally:
        release.set()
        db_engine.dispose()


def test_failure_after_first_page_keeps_results_until_customer_handoff(search_flow):
    f = search_flow
    original = f.http.side_effect
    def request(req, **kw):
        if "crm.deal.list" in req.full_url:
            if parse_qs(urlparse(req.full_url).query)["start"] == ["0"]:
                return response({"result": [{"ID": "2415340", "CONTACT_ID": "714865", "ASSIGNED_BY_ID": "11037"}], "next": 50})
            raise URLError("offline")
        return original(req, **kw)
    f.http.side_effect = request
    choose(f)
    assert not process(f, "+79991234567").get("closed")
    f.ready.assert_not_called()
    assert "2415340" in f.rich.call_args.args[1]["html"]
    assert "/show//" not in f.rich.call_args.args[1]["html"]
    assert action(f, "support")["automation_handoff"]
    assert "2415340" in summary(f)[0].body and "не завершён" in summary(f)[0].body
    assert len(summary(f)) == 1


def test_stale_search_callback_uses_own_editable_notice(search_flow):
    f = search_flow
    upsert_automatic_message_override(f.db, "telegram.bitrix_phone_deals.stale_action", "Старая кнопка поиска")
    f.db.commit()
    choose(f)
    old = f.ticket.client_close_message_id
    process(f, "/close")
    assert action(f, "close", 22, message_id=old)["ignored"]
    assert telegram.answer_callback_query.call_args.args[1] == "Старая кнопка поиска"


def test_large_plain_fallback_keeps_all_deal_links_and_confirms_every_chunk(search_flow):
    f = search_flow
    choose(f)
    rows = [{"id": str(value), "responsible": "Иванов Иван", "responsible_id": "11037", "complete": True} for value in range(1, 201)]
    engine.save(f.db, f.ticket, phone_input="+79991234567", rows=rows, finish="success")
    f.rich.side_effect = None
    f.rich.return_value = {"ok": False}
    assert not engine.finish(f.db, f.ticket, f.contact.external_chat_id, "success", True, telegram._phone_callbacks())["closed"]
    chunks = [call for call in f.sent.call_args_list if "crm.elitesochi.com" in call.args[1]]
    assert len(chunks) > 1
    assert all(len(call.args[1]) <= 4000 and call.kwargs["parse_mode"] is None for call in chunks)
    combined = "".join(call.args[1] for call in chunks)
    assert all(combined.count(f"https://crm.elitesochi.com/crm/deal/show/{row['id']}/") == 1 for row in rows)
    assert len(summary(f)) == 1


def test_partial_result_overrides_survive_and_repeated_input_never_restarts_api(search_flow):
    f = search_flow
    original = f.http.side_effect
    f.http.side_effect = lambda req, **kw: response({"result": []}) if "user.get" in req.full_url else original(req, **kw)
    for key, body in [("not_found", "Ничего нет"), ("partial_result", "Моя помощь"), ("result_partial", "<b>Начало</b>{{ответ}} {{пояснение}}Конец")]:
        upsert_automatic_message_override(f.db, "telegram.bitrix_phone_deals." + key, body)
    f.db.commit()
    choose(f)
    assert not process(f, "+79991234567").get("closed")
    html = f.rich.call_args.args[1]["html"]
    assert html.startswith("<b>Начало</b>") and "Моя помощь" in html and html.endswith("Конец")
    calls, sent = f.http.call_count, f.sent.call_count
    process(f, "+79999999999", 12)
    assert f.http.call_count == calls and f.sent.call_count == sent
    assert action(f, "close")["closed"]
    f.ready.assert_not_called()


@pytest.mark.parametrize("failure_method", [None, "crm.deal.list.json", "crm.deal.contact.items.get.json"])
def test_lead_discovery_keeps_existing_telegram_result_and_error_choices(search_flow, failure_method):
    f = search_flow
    original = f.http.side_effect
    def http(req, **kwargs):
        method = urlparse(req.full_url).path.rsplit("/", 1)[-1]
        params = parse_qs(urlparse(req.full_url).query)
        if method == "crm.duplicate.findbycomm.json":
            return response({"result": {"LEAD": [10]}})
        if method == failure_method:
            raise URLError("offline")
        if method == "crm.deal.contact.items.get.json":
            return response({"result": [{"CONTACT_ID": "714865"}]})
        if method == "crm.deal.list.json" and "filter[LEAD_ID]" in params:
            return response({"result": [{"ID": "100", "CONTACT_ID": "0"}]})
        return original(req, **kwargs)
    f.http.side_effect = http
    choose(f)
    result = process(f, "+79991234567")
    f.ready.assert_not_called()
    if failure_method is None:
        assert not result["closed"]
        html = f.rich.call_args.args[1]["html"]
        assert '<th>Сделка</th><th>Ответственный</th>' in html
        assert '/show/2415340/' in html and '/show/2555931/' in html and '/show/100/' not in html
    else:
        assert not result.get("closed")
        buttons = f.sent.call_args.kwargs["reply_markup"]["inline_keyboard"]
        assert [row[0]["text"] for row in buttons] == ["Связаться с сотрудником", "Закрыть обращение"]
        before = f.http.call_count
        process(f, "+79991234567", 12)
        assert f.http.call_count == before


def test_empty_results_allow_repeated_searches_in_same_ticket_then_success(search_flow, monkeypatch):
    f = search_flow
    clock = [1000.0]
    monkeypatch.setattr(engine, "now", lambda: clock[0])
    original = f.http.side_effect
    f.http.side_effect = lambda *a, **kw: response({"result": {}})
    choose(f)
    ticket_id = f.ticket.id
    first_button = None
    for update_id in range(11, 19):
        clock[0] += 60
        body = f"+799912345{update_id}"
        assert process(f, body, update_id)["automation_collecting"]
        assert f.ticket.id == ticket_id and engine.waiting(f.ticket)
        assert engine.state(f.ticket)["phase"] == "retry_input"
        assert engine.state(f.ticket)["deadline"] == clock[0] + 600
        assert not engine.state(f.ticket)["rows"]
        assert f.sent.call_args.args[1] == "По указанному номеру сделки не найдены.\nПроверьте номер и отправьте другой для повторного поиска."
        assert [row[0]["text"] for row in f.sent.call_args.kwargs["reply_markup"]["inline_keyboard"]] == ["Связаться с сотрудником", "Закрыть обращение"]
        count = f.http.call_count
        assert process(f, body, update_id)["duplicate"]
        assert f.http.call_count == count
        if first_button is None:
            first_button = f.ticket.client_close_message_id
    assert f.http.call_count == 8 * 3
    assert action(f, "close", 30, message_id=first_button)["ignored"]
    f.http.side_effect = original
    assert not process(f, "+79991234567", 31)["closed"]
    assert f.ticket.id == ticket_id and f.rich.call_count == 1
    assert '/show/2415340/' in f.rich.call_args.args[1]["html"]
    assert len(summary(f)) == 1
    bodies = f.db.scalars(select(TicketMessage.body).where(TicketMessage.message_type == "client")).all()
    assert len(bodies) == 9
    f.ready.assert_not_called()


@pytest.mark.parametrize("action_name", ["support", "close", "timeout"])
def test_retry_wait_keeps_support_close_and_refreshed_timeout(search_flow, monkeypatch, action_name):
    f = search_flow
    clock = [1000.0]
    monkeypatch.setattr(engine, "now", lambda: clock[0])
    f.http.side_effect = lambda *a, **kw: response({"result": {}})
    choose(f)
    process(f, "+79991234567")
    clock[0] = 1550
    process(f, "+79991234568", 12)
    assert engine.state(f.ticket)["deadline"] == 2150
    if action_name == "timeout":
        clock[0] = 1601
        assert engine.cleanup(f.db, telegram._phone_callbacks()) == 0
        clock[0] = 2151
        assert engine.cleanup(f.db, telegram._phone_callbacks()) == 1
        assert engine.cleanup(f.db, telegram._phone_callbacks()) == 0
        f.ready.assert_not_called()
    else:
        result = action(f, action_name)
        assert result["automation_handoff" if action_name == "support" else "closed"]
        assert f.ready.call_count == (1 if action_name == "support" else 0)
    assert len(summary(f)) == 1


def test_retry_wait_preserves_custom_not_found_and_recovers_without_search(search_flow):
    f = search_flow
    upsert_automatic_message_override(f.db, "telegram.bitrix_phone_deals.not_found", "<b>Мой текст</b>: отправьте другой телефон.")
    f.db.commit()
    f.http.side_effect = lambda *a, **kw: response({"result": {}})
    choose(f)
    process(f, "+79991234567")
    assert f.sent.call_args.args[1] == "<b>Мой текст</b>: отправьте другой телефон."
    calls, sent = f.http.call_count, f.sent.call_count
    f.db.expire_all()
    engine.resume(f.db, f.ticket, f.contact.external_chat_id, True, telegram._phone_callbacks())
    assert f.http.call_count == calls and f.sent.call_count == sent
    process(f, "короткий номер", 12)
    assert f.http.call_count == calls
    assert len(f.sent.call_args.kwargs["reply_markup"]["inline_keyboard"]) == 2
    process(f, "+79991234568", 13)
    assert f.http.call_count == calls + 3
    assert f.sent.call_args.args[1] == "<b>Мой текст</b>: отправьте другой телефон."


def test_api_error_after_empty_result_never_becomes_retryable_absence(search_flow):
    f = search_flow
    choose(f)
    f.http.side_effect = lambda *a, **kw: response({"result": {}})
    process(f, "+79991234567")
    f.http.side_effect = URLError("offline")
    process(f, "+79991234568", 12)
    assert engine.state(f.ticket)["phase"] == "results"
    assert engine.state(f.ticket)["technical_error"]
    assert "Не удалось завершить поиск" in f.sent.call_args.args[1]
    count = f.http.call_count
    process(f, "+79991234569", 13)
    assert f.http.call_count == count
    f.ready.assert_not_called()
