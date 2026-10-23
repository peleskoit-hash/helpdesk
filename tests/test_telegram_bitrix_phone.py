import io
import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from itertools import count
from threading import Event
from unittest.mock import Mock
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, parse_qs

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.app.models import Ticket, TicketMessage, TicketAttachment
from api.app.routers import telegram, settings_automatic_messages
from api.app.services import bitrix_client, telegram_bitrix_phone as phone, telegram_bot, telegram_polling, auto_close
from api.app.services.automatic_messages import get_effective_automatic_message_text, get_automatic_message_definition
from test_telegram_group_registry import case
from test_telegram_client_origin import flow, choose, incoming, response

PARTIAL_RESULT_NOTE = get_automatic_message_definition("telegram.bitrix_phone.partial_result").default_text


@pytest.fixture
def phone_flow(flow, monkeypatch):
    flow.button.automation_key = phone.KEY
    flow.db.commit()
    monkeypatch.setattr(bitrix_client.settings, "bitrix_webhook_base", "https://phone.invalid/test/")
    def http(request, **kwargs):
        method = urlparse(request.full_url).path.rsplit("/", 1)[-1]
        return response({"result": {"LEAD_ID": "42"} if method == "crm.deal.get.json" else {"PHONE": [{"VALUE": "79990000000"}]}})
    flow.http.side_effect = http
    numbers = count(500)
    flow.sent.side_effect = lambda *args, **kwargs: {"ok": True, "result": {"message_id": next(numbers)}}
    flow.rich = Mock(side_effect=lambda *args, **kwargs: {"ok": True, "result": {"message_id": next(numbers)}})
    monkeypatch.setattr(telegram, "send_rich_message", flow.rich)
    flow.ready = Mock(return_value=None)
    monkeypatch.setattr(telegram, "_ready_ticket_for_distribution", flow.ready)
    flow.app.include_router(settings_automatic_messages.router)
    return flow


def process(flow, text, update_id=11):
    return telegram.process_telegram_update(flow.db, incoming(flow, text, update_id), notify_client=True)


def action(flow, name, update_id=20, *, ticket_id=None, message_id=None):
    update = {"update_id": update_id, "callback_query": {"id": "action", "data": phone.PREFIX + name + ":" + (ticket_id or flow.ticket.id),
        "from": {"id": int(flow.contact.external_user_id)}, "message": incoming(flow, "")["message"]}}
    update["callback_query"]["message"]["message_id"] = int(message_id or flow.ticket.client_close_message_id)
    return telegram.process_telegram_update(flow.db, update, notify_client=True)


@pytest.mark.parametrize("path", ["show", "edit", "details"])
def test_single_strict_link_keeps_original_history_and_closes_after_delivery(phone_flow, path):
    f = phone_flow
    assert choose(f)["automation_collecting"]
    body = f"https://crm.elitesochi.com/crm/deal/{path}/1/"
    assert not process(f, body)["closed"]
    assert f.ticket.status != "closed" and f.ticket.assignee_id is None and not f.ticket.load_counted
    assert f.db.scalar(select(TicketMessage.body).where(TicketMessage.message_type == "client")) == body
    assert any(call.args[1] == "1 — +79990000000" for call in f.sent.call_args_list)
    assert f.http.call_args_list[0].args[0].full_url.endswith("crm.deal.get.json?ID=1")
    f.ready.assert_not_called()
    f.rich.assert_not_called()


@pytest.mark.parametrize("body", ["", "abc", "-1", "0", "１２３", " \n\t\r\n ", "https://evil.invalid/crm/deal/show/1/",
    "https://crm.elitesochi.com/crm/deal/show/1/?x=1", "https://crm.elitesochi.com/crm/deal/edit/1/#x", "1 extra",
    "\n".join(map(str, range(1, 32)))])
def test_invalid_batch_explains_without_any_request(phone_flow, body):
    f = phone_flow
    choose(f)
    result = process(f, body)
    assert result["automation_collecting"] and phone.waiting(f.ticket)
    f.http.assert_not_called()
    assert "строк" in f.sent.call_args.args[1] or "30" in f.sent.call_args.args[1]
    f.ready.assert_not_called()


def test_thirty_deals_and_duplicates_keep_order_and_use_real_two_column_table(phone_flow):
    f = phone_flow
    choose(f)
    ids = list(map(str, range(1, 31)))
    assert not process(f, "\n".join(["1", "0001", *ids]))["closed"]
    deal_urls = [call.args[0].full_url for call in f.http.call_args_list if "crm.deal.get.json" in call.args[0].full_url]
    assert [parse_qs(urlparse(url).query)["ID"][0] for url in deal_urls] == ids
    html = f.rich.call_args.args[1]["html"]
    assert html.count("<tr>") == 31 and html.count("<td>") == 60
    assert "<th>Номер сделки</th><th>Телефон (результат)</th>" in html
    assert "reply_markup" in f.rich.call_args.kwargs
    f.ready.assert_not_called()


@pytest.mark.parametrize("kind", ["missing_phone", "missing_deal"])
def test_missing_data_waits_for_customer_choice_then_handoffs_same_ticket(phone_flow, kind):
    f = phone_flow
    choose(f)
    f.http.side_effect = lambda *a, **k: response({"result": {}}) if kind == "missing_deal" else response({"result": {"CONTACT_ID": "9"}} if "deal.get" in a[0].full_url else {"result": {"PHONE": [{"VALUE": "7999_encX"}]}})
    result = process(f, "123")
    assert not result.get("closed") and phone.waiting(f.ticket)
    assert phone.state(f.ticket)["phase"] == "results"
    assert "Сделка не найдена" in f.sent.call_args.args[1] if kind == "missing_deal" else "Исходный телефон" in f.sent.call_args.args[1]
    buttons = f.sent.call_args.kwargs["reply_markup"]["inline_keyboard"]
    assert [row[0]["text"] for row in buttons] == ["Связаться с сотрудником", "Закрыть обращение"]
    f.ready.assert_not_called()
    assert action(f, "support")["automation_handoff"]
    f.ready.assert_called_once()
    assert f.ticket.intake_state == "ready" and f.ticket.status != "closed" and f.ticket.load_counted
    assert f.db.scalar(select(Ticket.id)) == f.ticket.id
    assert f.db.scalars(select(TicketMessage).where(TicketMessage.body.like("Расшифровка Bitrix передана сотрудникам%"))).all()
    assert action(f, "support", 21)["ignored"] == "stale_bitrix_phone_button"
    f.ready.assert_called_once()


def test_partial_success_and_not_found_400_are_not_technical(phone_flow):
    f = phone_flow
    choose(f)
    original = f.http.side_effect
    f.http.side_effect = lambda req, **kw: (_ for _ in ()).throw(HTTPError("https://phone.invalid", 400, "Bad Request", {}, response({"error_description": "Not found"}))) if "deal.get.json?ID=2" in req.full_url else original(req, **kw)
    assert process(f, "1\n2")["automation_collecting"]
    assert [row["phone"] for row in phone.state(f.ticket)["rows"]] == ["+79990000000", None]
    assert "Сделка не найдена" in f.rich.call_args.args[1]["html"]
    f.ready.assert_not_called()
    assert action(f, "close")["closed"]


@pytest.mark.parametrize("exc", [URLError("offline"), TimeoutError(), HTTPError("https://phone.invalid", 500, "Server error", {}, io.BytesIO(b"{}"))])
def test_technical_failure_waits_for_explicit_support_preserving_input_and_results(phone_flow, exc):
    f = phone_flow
    choose(f)
    original = f.http.side_effect
    f.http.side_effect = lambda req, **kw: (_ for _ in ()).throw(exc) if "deal.get.json?ID=2" in req.full_url else original(req, **kw)
    assert process(f, "1\n2")["automation_collecting"]
    assert phone.state(f.ticket)["rows"][0]["phone"] == "+79990000000"
    assert phone.waiting(f.ticket) and not f.ticket.load_counted
    f.ready.assert_not_called()
    assert get_automatic_message_definition("telegram.bitrix_phone.error").default_text in f.rich.call_args.args[1]["html"]
    buttons = f.rich.call_args.kwargs["reply_markup"]["inline_keyboard"]
    assert [row[0]["text"] for row in buttons] == ["Связаться с сотрудником", "Закрыть обращение"]
    assert process(f, "1\n2")["duplicate"]
    requests, deliveries = f.http.call_count, f.rich.call_count
    assert process(f, "3", 12)["automation_collecting"]
    with f.factory() as restarted:
        telegram.cleanup_expired_task_intents(restarted)
    f.db.expire_all()
    assert f.http.call_count == requests and f.rich.call_count == deliveries
    f.ready.assert_not_called()
    assert action(f, "support")["automation_handoff"]
    assert action(f, "support", 21)["ignored"] == "stale_bitrix_phone_button"
    f.ready.assert_called_once()


@pytest.mark.parametrize("phase", ["input", "results", "technical_error"])
def test_independent_ten_minute_timeouts_ignore_system_updates(phone_flow, monkeypatch, phase):
    f = phone_flow
    clock = [1000]
    monkeypatch.setattr(phone, "now", lambda: clock[0])
    choose(f)
    if phase == "results":
        clock[0] = 1200
        f.http.side_effect = lambda *args, **kwargs: response({"result": {}})
        process(f, "1")
    elif phase == "technical_error":
        f.http.side_effect = URLError("offline")
        process(f, "1")
    deadline = phone.state(f.ticket)["deadline"]
    telegram.append_system_message(f.db, f.ticket, "system update")
    f.db.commit()
    clock[0] = deadline - 1
    assert telegram.cleanup_expired_task_intents(f.db) == 0
    clock[0] = deadline
    assert telegram.cleanup_expired_task_intents(f.db) == 1
    assert f.ticket.status == "closed"
    assert telegram.cleanup_expired_task_intents(f.db) == 0
    f.ready.assert_not_called()


def test_technical_failure_can_close_without_distribution(phone_flow):
    f = phone_flow
    choose(f)
    f.http.side_effect = URLError("offline")
    process(f, "1")
    message_id = f.ticket.client_close_message_id
    assert action(f, "close")["closed"]
    assert action(f, "close", 21, message_id=message_id)["ignored"] == "stale_bitrix_phone_button"
    f.ready.assert_not_called()
    assert f.http.call_count == 1
    assert len(summaries(f)) == 1


@pytest.mark.parametrize("command", ["/start", "/close"])
def test_commands_close_without_staff_and_start_opens_menu(phone_flow, command):
    f = phone_flow
    choose(f)
    assert process(f, command)["closed"]
    f.ready.assert_not_called()
    assert telegram._send_problem_menu_for_contact.call_count == (command == "/start")


def test_rich_explicit_rejection_uses_plain_fallback_with_same_buttons(phone_flow):
    f = phone_flow
    choose(f)
    f.rich.return_value = {"ok": False, "error_code": 400}
    f.rich.side_effect = None
    assert not process(f, "1\n2")["closed"]
    f.rich.assert_called_once()
    assert any(call.args[1] == "1 — +79990000000\n2 — +79990000000" for call in f.sent.call_args_list)


def test_unknown_rich_delivery_never_repeats_or_falls_back_and_can_be_handed_off(phone_flow):
    f = phone_flow
    choose(f)
    f.rich.side_effect = TimeoutError()
    assert not process(f, "1\n2").get("closed")
    assert f.ticket.status != "closed"
    assert not any("1 —" in call.args[1] for call in f.sent.call_args_list)
    count_before = f.http.call_count
    process(f, "1\n2", 12)
    assert f.http.call_count == count_before
    f.rich.assert_called_once()
    assert action(f, "support")["automation_handoff"]


def test_closing_failure_recovers_after_new_session_without_resending_results(phone_flow, monkeypatch):
    f = phone_flow
    choose(f)
    complete = telegram._complete_auto_ticket
    monkeypatch.setattr(telegram, "_complete_auto_ticket", Mock(side_effect=RuntimeError("close failed")))
    assert not process(f, "1")["closed"]
    assert not action(f, "close")["closed"]
    result_messages = [call for call in f.sent.call_args_list if "1 —" in call.args[1]]
    assert len(result_messages) == 1
    monkeypatch.setattr(telegram, "_complete_auto_ticket", complete)
    with f.factory() as restarted:
        assert telegram.cleanup_expired_task_intents(restarted) == 1
    f.db.expire_all()
    assert f.ticket.status == "closed"
    assert len([call for call in f.sent.call_args_list if "1 —" in call.args[1]]) == 1
    f.http.assert_called()
    assert f.http.call_count == 2


def test_stale_ticket_and_previous_result_buttons_are_rejected(phone_flow):
    f = phone_flow
    choose(f)
    old_id = f.ticket.client_close_message_id
    f.http.side_effect = lambda *args, **kwargs: response({"result": {}})
    process(f, "1")
    assert action(f, "support", message_id=old_id)["ignored"] == "stale_bitrix_phone_button"
    assert action(f, "close", 21, ticket_id="another-ticket")["ignored"] == "stale_bitrix_phone_button"
    assert phone.waiting(f.ticket)
    f.ready.assert_not_called()


def test_templates_order_editing_and_old_overrides_survive(phone_flow):
    f = phone_flow
    old = "telegram.automation.fallback_created"
    assert f.client.patch(f"/api/v1/settings/automatic-messages/{old}", json={"body": "Сохранённый пользовательский текст"}).status_code == 200
    payload = f.client.get("/api/v1/settings/automatic-messages").json()
    categories = [item["category"] for item in payload["categories"]]
    pos = categories.index("automation")
    assert categories[pos:pos + 3] == ["automation", "client_origin", "bitrix_phone"]
    block = next(item for item in payload["categories"] if item["category"] == "bitrix_phone")
    assert len(block["items"]) == 21
    assert "telegram.automation.bitrix_deal_format_error_client" in [item["key"] for item in block["items"]]
    assert len({item["key"] for item in payload["items"]}) == len(payload["items"])
    for item in block["items"]:
        result = f.client.patch(f"/api/v1/settings/automatic-messages/{item['key']}", json={"body": item["effective_text"]})
        assert result.status_code == 200, result.text
    assert get_effective_automatic_message_text(f.db, old) == "Сохранённый пользовательский текст"


@pytest.mark.parametrize("direct_lead", [False, True])
def test_lookup_prefers_original_lead_phone_to_encrypted_contact_and_ignores_multiple_phones(monkeypatch, direct_lead):
    replies = [response({"result": {"CONTACT_ID": "9", **({"LEAD_ID": "42"} if direct_lead else {})}})]
    if direct_lead:
        replies += [response({"result": {"PHONE": [{"VALUE": "7 (999) 123-45-67"}]}}), response({"result": {"LEAD_ID": "666", "PHONE": [{"VALUE": "7_encX"}, {"VALUE": "888"}]}})]
    else:
        replies += [response({"result": {"LEAD_ID": "42", "PHONE": [{"VALUE": "7_encX"}, {"VALUE": "888"}]}}), response({"result": {"PHONE": [{"VALUE": "7 (999) 123-45-67"}]}})]
    http = Mock(side_effect=replies)
    monkeypatch.setattr(bitrix_client, "urlopen", http)
    monkeypatch.setattr(bitrix_client.settings, "bitrix_webhook_base", "https://phone.invalid/")
    result = bitrix_client.get_deal_phone_lookup("1")
    assert result.phone == "+7 (999) 123-45-67" and result.lead_id == "42" and result.source == "lead"
    assert not any("ID=666" in call.args[0].full_url for call in http.call_args_list)


@pytest.mark.parametrize("value, expected", [
    ("89991234567", "89991234567"),
    ("+79991234567", "+79991234567"),
    ("79991234567", "+79991234567"),
])
def test_original_phone_prefix_normalization(value, expected):
    assert bitrix_client._first_original_phone({"PHONE": [{"VALUE": value}]}) == expected


def test_send_rich_message_adapter_uses_real_method_and_keeps_keyboard(monkeypatch):
    request = Mock(return_value={"ok": True, "result": {"message_id": 42}})
    monkeypatch.setattr(telegram_bot, "telegram_request", request)
    assert telegram_bot.send_rich_message("chat", {"html": "<table></table>"}, {"inline_keyboard": []})["result"]["message_id"] == 42
    assert request.call_args.args == ("sendRichMessage",)
    assert request.call_args.kwargs["payload"] == {"chat_id": "chat", "rich_message": {"html": "<table></table>"}, "reply_markup": {"inline_keyboard": []}}


@pytest.mark.parametrize("second_kind", ["duplicate", "same_text", "close"])
def test_parallel_updates_and_cleanup_do_not_repeat_queries(phone_flow, tmp_path, monkeypatch, second_kind):
    f = phone_flow
    clock = [1000]
    monkeypatch.setattr(phone, "now", lambda: clock[0])
    choose(f)
    path = tmp_path / "parallel.db"
    with sqlite3.connect(path) as target:
        f.db.connection().connection.driver_connection.backup(target)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine, autoflush=False)
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
    update = incoming(f, "1")
    second = update if second_kind == "duplicate" else incoming(f, "/close" if second_kind == "close" else "1", 12)
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
        assert f.http.call_count == 2
        f.ready.assert_not_called()
    finally:
        release.set()
        engine.dispose()


def test_interrupted_lookup_recovers_to_customer_choice_without_repeating_http(phone_flow):
    f = phone_flow
    choose(f)
    phone.save(f.db, f.ticket, inflight="1", ids=["1"], phase="processing", rows=[])
    with f.factory() as restarted:
        telegram.cleanup_expired_task_intents(restarted)
    f.db.expire_all()
    assert phone.waiting(f.ticket) and phone.state(f.ticket)["phase"] == "results"
    buttons = f.sent.call_args.kwargs["reply_markup"]["inline_keyboard"]
    assert [row[0]["text"] for row in buttons] == ["Связаться с сотрудником", "Закрыть обращение"]
    f.ready.assert_not_called()
    with f.factory() as restarted:
        telegram.cleanup_expired_task_intents(restarted)
    assert action(f, "support")["automation_handoff"]
    f.ready.assert_called_once()
    f.http.assert_not_called()


def test_group_messages_never_start_phone_lookup(phone_flow):
    f = phone_flow
    choose(f)
    update = incoming(f, "1")
    update["message"]["chat"]["type"] = "supergroup"
    assert telegram.process_telegram_update(f.db, update, notify_client=True)["ignored"]
    f.http.assert_not_called()


def test_no_notify_cannot_close_without_a_confirmed_result(phone_flow):
    f = phone_flow
    choose(f)
    result = telegram.process_telegram_update(f.db, incoming(f, "1"), notify_client=False)
    assert not result.get("closed") and phone.waiting(f.ticket)
    assert not phone.state(f.ticket)["deliveries"].get("text_result_0")
    assert not process(f, "1", 12)["closed"]
    assert f.http.call_count == 2


def test_multiple_plain_contact_phones_choose_one_without_error(monkeypatch):
    monkeypatch.setattr(bitrix_client.settings, "bitrix_webhook_base", "https://phone.invalid/")
    http = Mock(side_effect=[response({"result": {"CONTACT_ID": "9"}}),
        response({"result": {"PHONE": [{"VALUE": "7_encX"}, {"VALUE": "123"}, {"VALUE": "456"}]}})])
    monkeypatch.setattr(bitrix_client, "urlopen", http)
    assert bitrix_client.get_deal_phone_lookup("12345678901234567890").phone == "+123"


@pytest.mark.parametrize("trigger", ["text", "support"])
def test_late_activity_cannot_extend_expired_result_wait(phone_flow, monkeypatch, trigger):
    f = phone_flow
    clock = [1000]
    monkeypatch.setattr(phone, "now", lambda: clock[0])
    choose(f)
    f.http.side_effect = lambda *args, **kwargs: response({"result": {}})
    process(f, "1")
    requests = f.http.call_count
    clock[0] = phone.state(f.ticket)["deadline"] + 1
    result = action(f, "support") if trigger == "support" else process(f, "1", 12)
    assert result["closed"] and f.ticket.status == "closed"
    assert f.http.call_count == requests
    f.ready.assert_not_called()


def test_attachment_history_and_archive_are_kept_without_notifications(phone_flow):
    f = phone_flow
    choose(f)
    update = incoming(f, "")
    update["message"]["document"] = {"file_id": "file-1", "file_unique_id": "unique-1", "file_name": "test.pdf", "mime_type": "application/pdf", "file_size": 12}
    assert telegram.process_telegram_update(f.db, update, notify_client=True)["automation_collecting"]
    attachment = f.db.scalars(select(TicketAttachment)).one()
    telegram.start_background_archive_inbound_attachment.assert_called_once_with(attachment.id)
    f.http.assert_not_called()
    f.ready.assert_not_called()


@pytest.mark.parametrize("failure", ["timeout", "unconfirmed"])
def test_unconfirmed_explicit_close_notice_keeps_request_open_without_repeated_delivery(phone_flow, failure):
    f = phone_flow
    choose(f)
    send = f.sent.side_effect
    attempts = []
    def sending(chat, text, **kwargs):
        if "Обращение по расшифровке номера Bitrix закрыто" in text:
            attempts.append(text)
            if failure == "timeout":
                raise TimeoutError()
            return {"ok": True, "result": {}}
        return send(chat, text, **kwargs)
    f.sent.side_effect = sending
    assert not process(f, "1")["closed"]
    assert not action(f, "close")["closed"]
    receipt = phone.state(f.ticket)["deliveries"]
    assert receipt["text_result_0"]["message_id"] > 0
    assert receipt["closed"]["status"] == "uncertain"
    assert phone.waiting(f.ticket) and f.ticket.closed_at is None
    calls = f.sent.call_count
    for update_id in [12, 13]:
        assert not process(f, "1", update_id).get("closed")
        assert telegram.cleanup_expired_task_intents(f.db) == 0
        assert not phone.finish(f.db, f.ticket, f.contact.external_chat_id, "closed", True, telegram._phone_callbacks())["closed"]
    assert f.sent.call_count == calls
    assert len(attempts) == 1
    assert f.http.call_count == 2
    f.ready.assert_not_called()


def test_unconfirmed_plain_result_stays_open_and_retries_only_failed_delivery(phone_flow):
    f = phone_flow
    choose(f)
    send = f.sent.side_effect
    f.sent.side_effect = lambda chat, text, **kwargs: {"ok": False, "error_code": 400} if "1 —" in text else send(chat, text, **kwargs)
    assert not process(f, "1")["closed"]
    f.sent.side_effect = send
    assert not process(f, "1", 12)["closed"]
    assert f.http.call_count == 2


def test_delivery_confirmation_commit_failure_never_repeats_result(phone_flow, monkeypatch):
    f = phone_flow
    choose(f)
    commit = f.db.commit
    send = f.sent.side_effect
    fail_next = [False]
    def sending(chat, text, **kwargs):
        reply = send(chat, text, **kwargs)
        if "1 —" in text:
            fail_next[0] = True
        return reply
    def committing():
        if fail_next[0]:
            fail_next[0] = False
            raise RuntimeError("receipt commit failed")
        return commit()
    f.sent.side_effect = sending
    monkeypatch.setattr(f.db, "commit", committing)
    with pytest.raises(RuntimeError, match="receipt commit failed"):
        process(f, "1")
    f.db.rollback()
    monkeypatch.setattr(f.db, "commit", commit)
    with f.factory() as restarted:
        telegram.cleanup_expired_task_intents(restarted)
    assert len([call for call in f.sent.call_args_list if "1 —" in call.args[1]]) == 1
    f.db.expire_all()
    assert phone.waiting(f.ticket)
    assert action(f, "support")["automation_handoff"]


def test_existing_polling_cleanup_actually_closes_phone_wait(phone_flow, monkeypatch):
    f = phone_flow
    monkeypatch.setattr(phone, "now", lambda: 1000)
    choose(f)
    monkeypatch.setattr(phone, "now", lambda: 1600)
    monkeypatch.setattr(telegram_polling, "SessionLocal", f.factory)
    monkeypatch.setattr(auto_close, "cleanup_auto_close_tickets", lambda db: None)
    def stop(*args):
        raise asyncio.CancelledError()
    monkeypatch.setattr(telegram_polling, "get_updates", stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(telegram_polling.telegram_polling_loop())
    f.db.expire_all()
    assert f.ticket.status == "closed"
    f.http.assert_not_called()


@pytest.mark.parametrize("response", [{"ok": False, "result": {"message_id": 5}}, {"ok": True},
    {"ok": True, "result": {"message_id": 0}}, {"ok": True, "result": {"message_id": -1}},
    {"ok": True, "result": {"message_id": True}}, {"ok": True, "result": {"message_id": "5"}}])
def test_only_a_positive_integer_message_id_confirms_delivery(response):
    assert phone.confirmed(response) is None


def test_plain_fallback_is_sticky_after_a_closing_failure(phone_flow, monkeypatch):
    f = phone_flow
    choose(f)
    f.rich.side_effect = None
    f.rich.return_value = {"ok": False, "error_code": 400}
    complete = telegram._complete_auto_ticket
    monkeypatch.setattr(telegram, "_complete_auto_ticket", Mock(side_effect=RuntimeError("close failed")))
    assert not process(f, "1\n2")["closed"]
    assert not action(f, "close")["closed"]
    f.rich.return_value = {"ok": True, "result": {"message_id": 999}}
    monkeypatch.setattr(telegram, "_complete_auto_ticket", complete)
    assert telegram.cleanup_expired_task_intents(f.db) == 1
    f.rich.assert_called_once()
    assert len([call for call in f.sent.call_args_list if "1 —" in call.args[1]]) == 1


@pytest.mark.parametrize("body", ["1", "1\n2"])
def test_success_requires_result_receipt_and_waits_without_completion_notice(phone_flow, body):
    f = phone_flow
    choose(f)
    send = f.sent.side_effect
    observed = []
    def sending(chat, text, **kwargs):
        if " — " in text or "Расшифровка завершена" in text:
            assert f.ticket.status != "closed" and f.ticket.closed_at is None
            observed.append(text)
        return send(chat, text, **kwargs)
    f.sent.side_effect = sending
    assert not process(f, body)["closed"]
    receipts = phone.state(f.ticket)["deliveries"]
    assert "success" not in receipts
    assert phone.state(f.ticket)["phase"] == "success"
    assert receipts["text_result_0" if body == "1" else "rich_result"]["message_id"] > 0
    assert not any("Расшифровка завершена" in text for text in observed)
    calls, requests = f.sent.call_count, f.http.call_count
    assert not phone.finish(f.db, f.ticket, f.contact.external_chat_id, "success", True, telegram._phone_callbacks())["closed"]
    assert (f.sent.call_count, f.http.call_count) == (calls, requests)


@pytest.mark.parametrize("body", ["1", "1\n2"])
def test_explicit_close_rejection_resumes_only_final_notice_after_restart(phone_flow, body):
    f = phone_flow
    choose(f)
    send = f.sent.side_effect
    f.sent.side_effect = lambda chat, text, **kwargs: {"ok": False, "error_code": 400} if "Обращение по расшифровке номера Bitrix закрыто" in text else send(chat, text, **kwargs)
    assert not process(f, body)["closed"]
    assert not action(f, "close")["closed"]
    assert phone.waiting(f.ticket) and f.ticket.closed_at is None
    receipts = phone.state(f.ticket)["deliveries"]
    assert receipts["closed"]["status"] == "failed"
    assert phone.state(f.ticket)["finish"] == "closed"
    result_calls, rich_calls, api_calls = len([call for call in f.sent.call_args_list if " — " in call.args[1]]), f.rich.call_count, f.http.call_count
    assert process(f, body)["duplicate"]
    f.sent.side_effect = send
    with f.factory() as restarted:
        assert telegram.cleanup_expired_task_intents(restarted) == 1
        assert telegram.cleanup_expired_task_intents(restarted) == 0
    f.db.expire_all()
    assert f.ticket.status == "closed"
    assert phone.state(f.ticket)["deliveries"]["closed"]["message_id"] > 0
    assert len([call for call in f.sent.call_args_list if " — " in call.args[1]]) == result_calls
    assert (f.rich.call_count, f.http.call_count) == (rich_calls, api_calls)


@pytest.mark.parametrize("body", ["1", "1\n2"])
def test_editable_checking_is_sent_once_before_api_and_not_repeated_on_recovery(phone_flow, body):
    f = phone_flow
    choose(f)
    text = "Проверяю выбранные сделки, пожалуйста, подождите…"
    saved = f.client.patch("/api/v1/settings/automatic-messages/telegram.bitrix_phone.checking", json={"body": text})
    assert saved.status_code == 200
    def requesting(*args, **kwargs):
        assert len([call for call in f.sent.call_args_list if call.args[1] == text]) == 1
        assert phone.state(f.ticket)["deliveries"]["checking"]["message_id"] > 0
        return response({"result": {}})
    f.http.side_effect = requesting
    assert process(f, body)["automation_collecting"]
    calls = f.http.call_count
    assert process(f, body)["duplicate"]
    telegram.cleanup_expired_task_intents(f.db)
    with f.factory() as restarted:
        restored = restarted.get(Ticket, f.ticket.id)
        phone.resume(restarted, restored, f.contact.external_chat_id, True, telegram._phone_callbacks())
    assert f.http.call_count == calls
    assert len([call for call in f.sent.call_args_list if call.args[1] == text]) == 1


@pytest.mark.parametrize("body", ["  1  ", "\n\t1\t\n\n", "\r\n 1 \r\n \r\n  https://crm.elitesochi.com/crm/deal/details/0001/ \r\n",
    "\n1\n\n2\n", "  https://crm.elitesochi.com/crm/deal/show/1/  ", "\t https://crm.elitesochi.com/crm/deal/edit/1/ \t"])
def test_input_ignores_blank_lines_and_outer_whitespace_but_preserves_history(phone_flow, body):
    f = phone_flow
    choose(f)
    ids, error = phone.parse_lines(body)
    assert error is None
    assert not process(f, body)["closed"]
    assert f.db.scalar(select(TicketMessage.body).where(TicketMessage.message_type == "client")) == body
    assert f.http.call_count == len(ids) * 2


def test_blank_lines_do_not_change_unique_limit_and_nonempty_lines_remain_strict():
    body = "\n\n".join("  " + str(value) + "  " for value in range(1, 31))
    assert phone.parse_lines(body + "\n 0001 \n") == (list(map(str, range(1, 31))), None)
    assert phone.parse_lines(body + "\n 31 \n")[1] == ("limit_exceeded", {})
    assert phone.parse_lines("\n 1 \n https://crm.elitesochi.com/crm/deal/show/2/?x=1 ")[1] == ("invalid_line", {"line_number": 3})
    assert phone.parse_lines(" 1 extra ")[0] == []


@pytest.mark.parametrize("body", ["1", "1\n2"])
def test_checking_has_only_close_and_success_result_offers_repeat(phone_flow, body):
    f = phone_flow
    choose(f)
    assert not process(f, body)["closed"]
    checking = next(call for call in f.sent.call_args_list if call.args[1] == "Проверяю сделки…")
    assert [row[0]["text"] for row in checking.kwargs["reply_markup"]["inline_keyboard"]] == ["Закрыть обращение"]
    result = next(call for call in f.sent.call_args_list if call.args[1].startswith("1 —")) if body == "1" else f.rich.call_args
    assert len(result.kwargs["reply_markup"]["inline_keyboard"]) == 2
    checking_id = phone.state(f.ticket)["deliveries"]["checking"]["message_id"]
    assert str(checking_id) != f.ticket.client_close_message_id
    assert action(f, "close", message_id=checking_id)["ignored"] == "stale_bitrix_phone_button"


@pytest.mark.parametrize("status", [400, 404])
@pytest.mark.parametrize("partial", [False, True])
def test_real_telegram_http_rejection_falls_back_once_with_correct_result_keyboard(phone_flow, monkeypatch, status, partial):
    f = phone_flow
    choose(f)
    original_http = f.http.side_effect
    if partial:
        f.http.side_effect = lambda req, **kwargs: response({"result": {}}) if "deal.get.json?ID=2" in req.full_url else original_http(req, **kwargs)
    requests = []
    def transport(request, **kwargs):
        requests.append(json.loads(request.data))
        raise HTTPError(request.full_url, status, "Rejected", {}, response({"ok": False, "error_code": status, "description": "Rich message rejected"}))
    monkeypatch.setattr(telegram_bot, "_api_url", lambda method, **kwargs: "https://telegram.invalid/" + method)
    monkeypatch.setattr(telegram_bot, "urlopen", transport)
    monkeypatch.setattr(telegram, "send_rich_message", telegram_bot.send_rich_message)
    result = process(f, "1\n2")
    assert not result.get("closed")
    assert len(requests) == 1
    assert phone.state(f.ticket)["deliveries"]["rich_result"]["status"] == "failed"
    assert phone.state(f.ticket)["result_format"] == "text"
    texts = [call for call in f.sent.call_args_list if call.args[1].startswith("1 —")]
    assert len(texts) == 1
    if partial:
        assert [row[0]["text"] for row in texts[0].kwargs["reply_markup"]["inline_keyboard"]] == ["Связаться с сотрудником", "Закрыть обращение"]
        assert texts[0].kwargs["reply_markup"] == requests[0]["reply_markup"]
        assert process(f, "1\n2")["duplicate"]
        with f.factory() as restarted:
            phone.resume(restarted, restarted.get(Ticket, f.ticket.id), f.contact.external_chat_id, True, telegram._phone_callbacks())
    else:
        assert len(texts[0].kwargs["reply_markup"]["inline_keyboard"]) == 2
        assert texts[0].kwargs["reply_markup"] == requests[0]["reply_markup"]
    assert len(requests) == 1
    assert len([call for call in f.sent.call_args_list if call.args[1].startswith("1 —")]) == 1
    f.ready.assert_not_called()


@pytest.mark.parametrize("failure", ["timeout", "network", "server"])
def test_real_telegram_uncertain_delivery_does_not_fall_back_or_repeat(phone_flow, monkeypatch, failure):
    f = phone_flow
    choose(f)
    calls = []
    def transport(request, **kwargs):
        calls.append(request)
        if failure == "network":
            raise URLError("connection lost")
        if failure == "server":
            raise HTTPError(request.full_url, 500, "Server Error", {}, response({"ok": False, "description": "Server error"}))
        raise TimeoutError()
    monkeypatch.setattr(telegram_bot, "_api_url", lambda method, **kwargs: "https://telegram.invalid/" + method)
    monkeypatch.setattr(telegram_bot, "urlopen", transport)
    monkeypatch.setattr(telegram, "send_rich_message", telegram_bot.send_rich_message)
    assert not process(f, "1\n2").get("closed")
    assert phone.state(f.ticket)["deliveries"]["rich_result"]["status"] == "uncertain"
    process(f, "1\n2", 12)
    telegram.cleanup_expired_task_intents(f.db)
    with f.factory() as restarted:
        phone.resume(restarted, restarted.get(Ticket, f.ticket.id), f.contact.external_chat_id, True, telegram._phone_callbacks())
    assert len(calls) == 1
    assert not any(call.args[1].startswith("1 —") for call in f.sent.call_args_list)
    assert phone.waiting(f.ticket)
    assert f.http.call_count == 4
    f.ready.assert_not_called()


@pytest.mark.parametrize("body", ["1", "1\n2"])
@pytest.mark.parametrize("fallback", [False, True])
def test_partial_result_note_is_in_the_same_telegram_result_with_two_buttons(phone_flow, body, fallback):
    f = phone_flow
    choose(f)
    original = f.http.side_effect
    f.http.side_effect = lambda req, **kwargs: response({"result": {}}) if "deal.get.json?ID=1" in req.full_url else original(req, **kwargs)
    if fallback:
        f.rich.side_effect = None
        f.rich.return_value = {"ok": False, "error_code": 400}
    start = f.sent.call_count
    assert process(f, body)["automation_collecting"]
    assert f.sent.call_count - start == (1 if body != "1" and not fallback else 2)
    if body != "1" and not fallback:
        html = f.rich.call_args.args[1]["html"]
        assert html.endswith("</table>\n\n<p>" + PARTIAL_RESULT_NOTE + "</p>")
        result = f.rich.call_args
    else:
        result = next(call for call in f.sent.call_args_list if call.args[1].startswith("1 —"))
        assert result.args[1].endswith("\n\n" + PARTIAL_RESULT_NOTE)
    assert [row[0]["text"] for row in result.kwargs["reply_markup"]["inline_keyboard"]] == ["Связаться с сотрудником", "Закрыть обращение"]
    assert not any(call.args[1] == PARTIAL_RESULT_NOTE for call in f.sent.call_args_list)


@pytest.mark.parametrize("body", ["1", "1\n2"])
@pytest.mark.parametrize("fallback", [False, True])
def test_success_result_has_no_partial_note(phone_flow, body, fallback):
    f = phone_flow
    choose(f)
    if fallback:
        f.rich.side_effect = None
        f.rich.return_value = {"ok": False, "error_code": 400}
    assert not process(f, body)["closed"]
    assert all(PARTIAL_RESULT_NOTE not in call.args[1] for call in f.sent.call_args_list)
    if f.rich.call_args:
        assert PARTIAL_RESULT_NOTE not in f.rich.call_args.args[1]["html"]


def summaries(f):
    return f.db.scalars(select(TicketMessage).where(TicketMessage.ticket_id == f.ticket.id,
        TicketMessage.message_type == "system", TicketMessage.body.like("Расшифровка%"))).all()


@pytest.mark.parametrize("first_body", ["1", "1\n2"])
@pytest.mark.parametrize("retry_body", ["3", "3\n4", "\n".join(map(str, range(3, 33)))])
def test_missing_phones_accept_new_batch_in_same_ticket_preserving_history(phone_flow, monkeypatch, first_body, retry_body):
    f = phone_flow
    clock = [1000]
    monkeypatch.setattr(phone, "now", lambda: clock[0])
    choose(f)
    original = f.http.side_effect
    f.http.side_effect = lambda req, **kw: response({"result": {}}) if req.full_url.endswith("deal.get.json?ID=1") else original(req, **kw)
    process(f, first_body)
    first_message = f.ticket.client_close_message_id
    assert phone.waiting(f.ticket) and phone.state(f.ticket)["deadline"] == 1600
    clock[0] = 1100
    assert not process(f, retry_body, 12)["closed"]
    assert phone.state(f.ticket)["phase"] == "success" and phone.state(f.ticket)["deadline"] == 1700
    assert f.ticket.workflow_type == "auto" and f.db.scalar(select(Ticket.id)) == f.ticket.id
    assert [row["id"] for row in phone.state(f.ticket)["rows"]] == retry_body.splitlines()
    assert "1 — Сделка не найдена" in summaries(f)[0].body
    assert len(summaries(f)) == 2 and all(message.agent_visible for message in summaries(f))
    result = f.rich.call_args if "\n" in retry_body else f.sent.call_args
    assert [row[0]["text"] for row in result.kwargs["reply_markup"]["inline_keyboard"]] == ["Расшифр. другой номер", "Закрыть обращение"]
    assert action(f, "support", 30, message_id=first_message)["ignored"] == "stale_bitrix_phone_button"
    requests = f.http.call_count
    assert process(f, retry_body, 12)["duplicate"]
    assert f.http.call_count == requests and len(summaries(f)) == 2
    bodies = f.db.scalars(select(TicketMessage.body).where(TicketMessage.message_type == "client").order_by(TicketMessage.created_at)).all()
    assert bodies == [first_body, retry_body]
    f.ready.assert_not_called()


def test_missing_phones_allow_unlimited_retries_invalid_input_and_restart(phone_flow, monkeypatch):
    f = phone_flow
    clock = [1000]
    monkeypatch.setattr(phone, "now", lambda: clock[0])
    choose(f)
    f.http.side_effect = lambda *a, **kw: response({"result": {}})
    for attempt in range(4):
        clock[0] += 100
        assert process(f, str(attempt + 1), 11 + attempt)["automation_collecting"]
        assert phone.state(f.ticket)["deadline"] == clock[0] + 600
        buttons = f.sent.call_args.kwargs["reply_markup"]["inline_keyboard"]
        assert [row[0]["text"] for row in buttons] == ["Связаться с сотрудником", "Закрыть обращение"]
        assert len(summaries(f)) == attempt
        assert process(f, str(attempt + 1), 11 + attempt)["duplicate"]
    assert f.http.call_count == 4
    for update, invalid in enumerate(["bad", "\n".join(map(str, range(1, 32)))], 20):
        assert process(f, invalid, update)["automation_collecting"]
        assert f.http.call_count == 4 and len(summaries(f)) == 4
        buttons = f.sent.call_args.kwargs["reply_markup"]["inline_keyboard"]
        assert [row[0]["text"] for row in buttons] == ["Связаться с сотрудником", "Закрыть обращение"]
    with f.factory() as restarted:
        telegram.cleanup_expired_task_intents(restarted)
    f.db.expire_all()
    assert process(f, "5", 22)["automation_collecting"]
    assert phone.state(f.ticket)["ids"] == ["5"] and f.http.call_count == 5
    assert len(summaries(f)) == 4
    assert action(f, "support", 23)["automation_handoff"]
    assert len(summaries(f)) == 5 and f.ticket.workflow_type == "task"
    f.ready.assert_called_once()


@pytest.mark.parametrize("key,body,expected", [
    ("limit_exceeded", "\n".join(map(str, range(1, 32))), "Пользовательский лимит"),
    ("empty_input", " \n\t", "Пользовательский пустой список"),
    ("invalid_line", "1\n\n bad", "Пользовательская строка 3"),
])
def test_input_reason_overrides_are_used_without_api_and_preserve_outer_override(phone_flow, key, body, expected):
    f = phone_flow
    custom = expected.replace("3", "{{line_number}}") if key == "invalid_line" else expected
    for name, text in [(f"telegram.bitrix_phone.{key}", custom),
                       ("telegram.automation.bitrix_deal_format_error_client", "Сохранённый общий текст")]:
        assert f.client.patch(f"/api/v1/settings/automatic-messages/{name}", json={"body": text}).status_code == 200
    choose(f)
    assert process(f, body)["automation_collecting"]
    assert "Сохранённый общий текст" in f.sent.call_args.args[1]
    assert expected in f.sent.call_args.args[1]
    f.http.assert_not_called()


@pytest.mark.parametrize("mode", ["single", "rich", "fallback"])
def test_partial_result_override_is_used_in_place_for_every_delivery_format(phone_flow, mode):
    f = phone_flow
    custom = "<b>Пользовательское пояснение</b> &amp; помощь"
    assert f.client.patch("/api/v1/settings/automatic-messages/telegram.bitrix_phone.partial_result", json={"body": custom}).status_code == 200
    f.db.expire_all()
    f.http.side_effect = lambda *args, **kwargs: response({"result": {}})
    if mode == "fallback":
        f.rich.side_effect = None
        f.rich.return_value = {"ok": False, "error_code": 400}
    choose(f)
    assert process(f, "1" if mode == "single" else "1\n2")["automation_collecting"]
    if mode == "rich":
        html = f.rich.call_args.args[1]["html"]
        assert html.endswith("</table>\n\n<p>" + custom + "</p>")
    else:
        text = f.sent.call_args.args[1]
        assert text.endswith(custom)
        assert f.sent.call_args.kwargs["parse_mode"] == "HTML"
    assert not any(call.args[1].startswith("Пользовательское пояснение") for call in f.sent.call_args_list)


def test_phone_callback_reply_overrides_reach_telegram(phone_flow):
    f = phone_flow
    for key in ["stale_action", "action_done"]:
        assert f.client.patch(f"/api/v1/settings/automatic-messages/telegram.bitrix_phone.{key}",
                              json={"body": "<b>Настроено: " + key + "</b>"}).status_code == 200
    choose(f)
    f.http.side_effect = lambda *args, **kwargs: response({"result": {}})
    process(f, "1")
    assert action(f, "close", ticket_id="stale")["ignored"]
    assert telegram.answer_callback_query.call_args.args[1] == "Настроено: stale_action"
    assert action(f, "close", 21)["closed"]
    assert telegram.answer_callback_query.call_args.args[1] == "Настроено: action_done"


@pytest.mark.parametrize("mode", ["single", "rich", "fallback"])
@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("missing_answer", [False, True])
def test_custom_result_wrappers_preserve_answer_formatting_and_buttons(phone_flow, mode, partial, missing_answer):
    f = phone_flow
    key = "result_partial" if partial else "result_full"
    template = "<b>До результата</b>\n" + ("" if missing_answer else "{{ответ}}") + "\n<i>После результата</i>"
    if partial:
        template += "\n{{пояснение}}"
        assert f.client.patch("/api/v1/settings/automatic-messages/telegram.bitrix_phone.partial_result",
                              json={"body": "<u>Моё пояснение</u>"}).status_code == 200
        original = f.http.side_effect
        f.http.side_effect = lambda req, **kwargs: response({"result": {}}) if "deal.get.json?ID=1" in req.full_url else original(req, **kwargs)
    assert f.client.patch(f"/api/v1/settings/automatic-messages/telegram.bitrix_phone.{key}", json={"body": template}).status_code == 200
    if mode == "fallback":
        f.rich.side_effect = None
        f.rich.return_value = {"ok": False, "error_code": 400}
    choose(f)
    result = process(f, "1" if mode == "single" else "1\n2")
    assert not result.get("closed")
    call = f.rich.call_args if mode == "rich" else next(call for call in f.sent.call_args_list if call.args[1].startswith("<b>До результата</b>"))
    text = call.args[1]["html"] if mode == "rich" else call.args[1]
    assert "<b>До результата</b>" in text and "<i>После результата</i>" in text
    assert "{{ответ}}" not in text and "{{пояснение}}" not in text
    answer_position = text.index("<table>") if mode == "rich" else text.index("1 —")
    if missing_answer:
        assert text.index("После результата") < answer_position
    else:
        assert text.index("До результата") < answer_position < text.index("После результата")
    assert ("Сделка не найдена" if partial else "+79990000000") in text
    if partial:
        assert "<u>Моё пояснение</u>" in text
        assert len(call.kwargs["reply_markup"]["inline_keyboard"]) == 2
    else:
        assert len(call.kwargs["reply_markup"]["inline_keyboard"]) == 2
    if mode != "rich":
        assert call.kwargs["parse_mode"] == "HTML"
    assert get_effective_automatic_message_text(f.db, f"telegram.bitrix_phone.{key}") == template
    requests, sent, rich_sent = f.http.call_count, f.sent.call_count, f.rich.call_count
    with f.factory() as restarted:
        telegram.cleanup_expired_task_intents(restarted)
    assert (f.http.call_count, f.sent.call_count, f.rich.call_count) == (requests, sent, rich_sent)


@pytest.mark.parametrize("ending", ["support", "close", "timeout", "success"])
def test_one_final_summary_preserves_all_rows_and_original_input_without_duplicates(phone_flow, monkeypatch, ending):
    f = phone_flow
    clock = [1000]
    monkeypatch.setattr(phone, "now", lambda: clock[0])
    choose(f)
    original = f.http.side_effect
    if ending != "success":
        f.http.side_effect = lambda req, **kwargs: response({"result": {}}) if "deal.get.json?ID=2" in req.full_url else original(req, **kwargs)
    body = " 1 \n\n https://crm.elitesochi.com/crm/deal/show/2/ \n"
    first = process(f, body)
    if ending != "success":
        assert summaries(f) == []
    if ending == "support":
        action(f, "support")
    elif ending == "close":
        action(f, "close")
    elif ending == "timeout":
        clock[0] = phone.state(f.ticket)["deadline"]
        assert telegram.cleanup_expired_task_intents(f.db) == 1
    else:
        assert not first["closed"]
    rows = summaries(f)
    assert len(rows) == 1 and rows[0].agent_visible is True
    text = rows[0].body
    assert "1 — +79990000000" in text
    assert "2 — " + ("+79990000000" if ending == "success" else "Сделка не найдена") in text
    if ending == "support":
        assert text.startswith("Расшифровка Bitrix передана сотрудникам: по просьбе клиента")
        assert text.endswith("Сотруднику требуется помочь с оставшимися нерасшифрованными номерами: 2")
    assert not f.db.scalars(select(TicketMessage).where(TicketMessage.body.like("Bitrix:%"))).all()
    assert f.db.scalar(select(TicketMessage.body).where(TicketMessage.message_type == "client")) == body
    assert process(f, body)["duplicate"]
    with f.factory() as restarted:
        telegram.cleanup_expired_task_intents(restarted)
    f.db.expire_all()
    assert len(summaries(f)) == 1


def test_technical_summary_keeps_processed_missing_and_unprocessed_ids(phone_flow):
    f = phone_flow
    choose(f)
    original = f.http.side_effect
    def request(req, **kwargs):
        if "deal.get.json?ID=2" in req.full_url:
            return response({"result": {}})
        if "deal.get.json?ID=3" in req.full_url:
            raise URLError("unavailable")
        return original(req, **kwargs)
    f.http.side_effect = request
    assert process(f, "1\n2\n3\n4")["automation_collecting"]
    assert summaries(f) == []
    f.ready.assert_not_called()
    assert action(f, "support")["automation_handoff"]
    text = summaries(f)[0].body
    assert text.startswith("Расшифровка Bitrix передана сотрудникам: по просьбе клиента")
    assert "1 — +79990000000" in text and "2 — Сделка не найдена" in text
    assert "Сделка 3: техническая ошибка Bitrix" in text
    assert "Необработанные ID: 3, 4" in text
    assert text.endswith("Сотруднику требуется помочь с оставшимися нерасшифрованными номерами: 2, 3, 4")
    assert len(summaries(f)) == 1 and summaries(f)[0].agent_visible is True
    assert not f.db.scalars(select(TicketMessage).where(TicketMessage.body.like("Сделка %техническая ошибка%"))).all()
    with f.factory() as restarted:
        telegram.cleanup_expired_task_intents(restarted)
    f.db.expire_all()
    assert len(summaries(f)) == 1


def test_summary_and_marker_roll_back_together_and_recovery_writes_only_one(phone_flow, monkeypatch):
    f = phone_flow
    choose(f)
    callbacks = telegram._phone_callbacks()
    append = callbacks["append_system"]
    def failing(db, ticket, body, **kwargs):
        append(db, ticket, body, **kwargs)
        raise RuntimeError("summary transaction interrupted")
    callbacks["append_system"] = failing
    original = telegram._phone_callbacks
    monkeypatch.setattr(telegram, "_phone_callbacks", lambda: callbacks)
    assert not process(f, "1\n2")["closed"]
    assert summaries(f) == []
    assert not phone.state(f.ticket).get("summary_saved")
    monkeypatch.setattr(telegram, "_phone_callbacks", original)
    with f.factory() as restarted:
        assert telegram.cleanup_expired_task_intents(restarted) == 0
        assert telegram.cleanup_expired_task_intents(restarted) == 0
    f.db.expire_all()
    assert len(summaries(f)) == 1
    assert phone.state(f.ticket)["summary_saved"] is True
    assert f.http.call_count == 4
