import io
import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.app.models import BotButtonSetting, Company, Ticket, TicketMessage, TicketAttachment
from api.app.routers import telegram, settings_automatic_messages
from api.app.services import bitrix_client, telegram_client_origin as origin
from api.app.services import telegram_polling, auto_close
from api.app.services import telegram_attachments
from api.app.services.automatic_messages import render_automatic_message_for_telegram
from test_telegram_group_registry import case
from test_telegram_client_blocking import _seed_contact, _seed_ticket


@pytest.fixture
def flow(case, monkeypatch, tmp_path):
    company = case.db.scalar(select(Company))
    client, _, contact = _seed_contact(case.db, company=company)
    ticket = _seed_ticket(case.db, company_id=company.id, client_id=client.id,
                          workflow_type="task", intake_state="collecting", status="unassigned")
    button = BotButtonSetting(id="origin-button", button_key="Menu|task|Origin", menu_type="Menu",
        workflow_type="task", button_path="Origin", button_label="Откуда клиент", automation_key="client_origin",
        automation_overridden=True, currently_present=True)
    case.db.add(button)
    case.db.commit()
    case.ticket, case.button, case.contact = ticket, button, contact
    case.sent = Mock(return_value={"ok": True, "result": {"message_id": 500}})
    case.http = Mock(return_value=io.BytesIO(json.dumps({"result": {"UF_CRM_MARK": "Смотри подборку квартир на побережье"}}).encode()))
    monkeypatch.setattr(origin.settings, "bitrix_deal_mark_webhook_base", "https://mark.invalid/test/")
    monkeypatch.setattr(bitrix_client, "urlopen", case.http)
    monkeypatch.setattr(telegram, "send_message", case.sent)
    monkeypatch.setattr(telegram, "answer_callback_query", Mock())
    monkeypatch.setattr(telegram, "remove_inline_keyboard", Mock())
    monkeypatch.setattr(telegram, "_safe_remove_inline_keyboard", Mock())
    monkeypatch.setattr(telegram, "_menu_name_for_contact", lambda *args: "Menu")
    monkeypatch.setattr(telegram, "schedule_new_message_notification", Mock(side_effect=AssertionError("no staff notifications")))
    monkeypatch.setattr(telegram, "schedule_avatar_refresh", Mock())
    monkeypatch.setattr(telegram, "start_background_archive_inbound_attachment", Mock())
    monkeypatch.setattr(telegram_attachments, "get_file", Mock(return_value={"result": {"file_path": "files/test.pdf"}}))
    monkeypatch.setattr(telegram_attachments.settings, "attachment_storage_dir", str(tmp_path))
    monkeypatch.setattr(telegram, "_ready_ticket_for_distribution", Mock(side_effect=AssertionError("no distribution")))
    monkeypatch.setattr(telegram, "_send_problem_menu_for_contact", Mock(return_value={"ok": True}))
    return case


def choose(flow, confirmation=False, update_id=10):
    return telegram.process_telegram_update(flow.db, {"update_id": update_id, "callback_query": {
        "id": "choose", "data": f"confirm:yes:{flow.button.button_key}" if confirmation else f"btnid:{flow.button.id}",
        "from": {"id": int(flow.contact.external_user_id)},
        "message": {"message_id": 10, "chat": {"id": int(flow.contact.external_chat_id), "type": "private"}},
    }}, notify_client=True)


def set_workflow(flow, workflow):
    flow.button.workflow_type = flow.ticket.workflow_type = workflow
    flow.button.button_key = f"Menu|{workflow}|Origin"
    flow.db.commit()


def incoming(flow, text, update_id=11):
    return {"update_id": update_id, "message": {"message_id": update_id, "text": text,
        "from": {"id": int(flow.contact.external_user_id)},
        "chat": {"id": int(flow.contact.external_chat_id), "type": "private"}}}


def response(value):
    return io.BytesIO(json.dumps(value).encode())


def http_error(description="Not found", status=400):
    return HTTPError("https://mark.invalid/test/", status, "Bad Request", {},
                     response({"error_description": description}))


@pytest.mark.parametrize("confirmation", [False, True])
@pytest.mark.parametrize("workflow", ["task", "auto"])
def test_selection_starts_waiting_without_assignment(flow, confirmation, workflow):
    set_workflow(flow, workflow)
    assert choose(flow, confirmation)["automation_collecting"]
    assert flow.ticket.intake_state == "collecting" and flow.ticket.assignee_id is None
    assert flow.ticket.load_counted is False
    assert flow.ticket.workflow_type == workflow
    assert flow.sent.call_args.kwargs["reply_markup"] == {"inline_keyboard": [[{"text": "Закрыть обращение", "callback_data": "client_close"}]]}
    flow.http.assert_not_called()


def test_confirmed_success_one_request_separate_webhook_and_safe_result(flow, monkeypatch):
    choose(flow)
    old_base = origin.settings.bitrix_webhook_base
    flow.http.return_value = response({"result": {"UF_CRM_MARK": "Смотри подборку квартир на побережье <b>&"}})
    result = telegram.process_telegram_update(flow.db, incoming(flow, "2528563"), notify_client=True)
    assert result["closed"] and flow.ticket.status == "closed" and flow.ticket.assignee_id is None
    flow.http.assert_called_once()
    assert flow.http.call_args.args[0].full_url == "https://mark.invalid/test/crm.deal.get.json?ID=2528563"
    assert origin.settings.bitrix_webhook_base == old_base
    texts = [call.args[1] for call in flow.sent.call_args_list]
    assert any("&lt;b&gt;&amp;" in text for text in texts)
    assert "Проверка завершена" in texts[-1]
    assert len(flow.db.scalars(select(TicketMessage).where(TicketMessage.message_type == "client")).all()) == 1
    assert telegram.process_telegram_update(flow.db, incoming(flow, "2528563")) == {"ok": True, "duplicate": True}
    flow.http.assert_called_once()


@pytest.mark.parametrize("value", ["abc", "", "0", "000", "-1", "+1", "1.5", "１２３"])
def test_invalid_input_never_calls_api_and_renews_only_client_deadline(flow, monkeypatch, value):
    monkeypatch.setattr(origin, "now", lambda: 1000)
    choose(flow)
    monkeypatch.setattr(origin, "now", lambda: 1200)
    assert telegram.process_telegram_update(flow.db, incoming(flow, value), notify_client=True)["automation_collecting"]
    flow.http.assert_not_called()
    assert flow.ticket.quick_action_state_json[origin.KEY]["deadline"] == 1800
    assert flow.ticket.status == "unassigned" and flow.ticket.assignee_id is None
    assert flow.sent.call_args.kwargs["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "client_close"


@pytest.mark.parametrize("number", ["1", "999999999", "123456789012345678901234567890"])
def test_positive_ids_have_no_five_to_seven_digit_limit(flow, number):
    choose(flow)
    flow.http.side_effect = http_error()
    assert telegram.process_telegram_update(flow.db, incoming(flow, number), notify_client=True)["automation_collecting"]
    flow.http.assert_called_once()
    assert "Сделка не найдена" in flow.sent.call_args.args[1]


@pytest.mark.parametrize("workflow", ["task", "auto"])
@pytest.mark.parametrize("body", ["2528563", *[
    f"https://crm.elitesochi.com/crm/deal/{path}/2528563/" for path in ["show", "edit", "details"]]])
def test_id_and_three_strict_links_close_both_workflows_and_preserve_original_message(flow, workflow, body):
    set_workflow(flow, workflow)
    key = flow.button.button_key
    choose(flow)
    assert origin.waiting(flow.ticket) and flow.ticket.assignee_id is None
    result = telegram.process_telegram_update(flow.db, incoming(flow, body), notify_client=True)
    assert result["closed"]
    flow.db.expire_all()
    assert flow.ticket.status == "closed" and flow.ticket.workflow_type == workflow
    assert flow.ticket.assignee_id is None and flow.ticket.load_counted is False
    assert flow.button.workflow_type == workflow and flow.button.button_key == key
    source = flow.db.scalars(select(TicketMessage).where(TicketMessage.message_type == "client")).one()
    assert source.body == body
    flow.http.assert_called_once()
    assert flow.http.call_args.args[0].full_url == "https://mark.invalid/test/crm.deal.get.json?ID=2528563"


@pytest.mark.parametrize("path", ["show", "edit", "details"])
@pytest.mark.parametrize("number", ["1", "123456789012345678901234567890", "000001"])
def test_strict_link_parser_has_no_id_length_limit(path, number):
    assert origin.extract_deal_id(number) == number
    assert origin.extract_deal_id(f"https://crm.elitesochi.com/crm/deal/{path}/{number}/") == number


INVALID_LINKS = [
    "http://crm.elitesochi.com/crm/deal/show/1/",
    "ftp://crm.elitesochi.com/crm/deal/show/1/",
    "https://other.example/crm/deal/show/1/",
    "https://crm.elitesochi.com.evil.example/crm/deal/show/1/",
    "https://crm.elitesochi.com@evil.example/crm/deal/show/1/",
    "https://user@crm.elitesochi.com/crm/deal/show/1/",
    "https://crm.elitesochi.com:443/crm/deal/show/1/",
    "https://crm.elitesochi.com/crm/contact/show/1/",
    "https://crm.elitesochi.com/crm/deal/view/1/",
    "https://crm.elitesochi.com/crm/deal/show/1",
    "https://crm.elitesochi.com/crm/deal/show/1//",
    "https://crm.elitesochi.com/crm/deal/show/1/?x=1",
    "https://crm.elitesochi.com/crm/deal/show/1/?",
    "https://crm.elitesochi.com/crm/deal/edit/1/#test",
    "https://crm.elitesochi.com/crm/deal/details/1/#",
    "https://crm.elitesochi.com/crm/deal/show/0/",
    "https://crm.elitesochi.com/crm/deal/edit/000/",
    "https://crm.elitesochi.com/crm/deal/details/-1/",
    "https://crm.elitesochi.com/crm/deal/show/%31/",
    "https://crm.elitesochi.com/crm/deal/show/１２３/",
    "https://crm.elitesochi.com/crm/deal/show/1/extra",
    "Текст https://crm.elitesochi.com/crm/deal/show/1/",
    "https://crm.elitesochi.com/crm/deal/show/1/ Текст",
    " https://crm.elitesochi.com/crm/deal/show/1/",
    "https://crm.elitesochi.com/crm/deal/show/1/\n",
    "https://CRM.ELITESOCHI.COM/crm/deal/show/1/",
]


@pytest.mark.parametrize("body", INVALID_LINKS)
def test_link_parser_rejects_any_other_url_shape(body):
    assert origin.extract_deal_id(body) is None


@pytest.mark.parametrize("workflow", ["task", "auto"])
@pytest.mark.parametrize("body", [INVALID_LINKS[i] for i in [0, 2, 7, 11, 13, 22]])
def test_invalid_urls_never_reach_bitrix_and_keep_original_message(flow, workflow, body):
    set_workflow(flow, workflow)
    choose(flow)
    assert telegram.process_telegram_update(flow.db, incoming(flow, body), notify_client=True)["automation_collecting"]
    flow.http.assert_not_called()
    assert origin.waiting(flow.ticket) and flow.ticket.workflow_type == workflow
    assert "Нужен положительный номер" in flow.sent.call_args.args[1]
    source = flow.db.scalars(select(TicketMessage).where(TicketMessage.message_type == "client")).one()
    assert source.body == body


@pytest.mark.parametrize("kind", ["not_found", "empty", "network", "timeout", "http", "malformed", "wrong_type", "config"])
def test_unsuccessful_attempt_waits_for_next_number_then_succeeds(flow, monkeypatch, kind):
    choose(flow)
    errors = {"not_found": http_error(), "network": URLError("offline"), "timeout": TimeoutError(),
              "http": http_error("service unavailable", 503)}
    if kind in errors:
        flow.http.side_effect = errors[kind]
    elif kind == "config":
        monkeypatch.setattr(origin.settings, "bitrix_deal_mark_webhook_base", None)
    else:
        flow.http.return_value = {"empty": response({"result": {"UF_CRM_MARK": ""}}),
                                  "malformed": io.BytesIO(b"not json"),
                                  "wrong_type": response({"result": {"UF_CRM_MARK": []}})}[kind]
    first = incoming(flow, "2557615" if kind == "empty" else "999999999")
    assert telegram.process_telegram_update(flow.db, first, notify_client=True)["automation_collecting"]
    assert flow.ticket.status != "closed" and flow.ticket.assignee_id is None
    assert telegram.process_telegram_update(flow.db, first) == {"ok": True, "duplicate": True}
    assert flow.http.call_count == (0 if kind == "config" else 1)
    flow.http.reset_mock(side_effect=True)
    flow.http.return_value = response({"result": {"UF_CRM_MARK": "Смотри подборку квартир на побережье"}})
    monkeypatch.setattr(origin.settings, "bitrix_deal_mark_webhook_base", "https://mark.invalid/test/")
    assert telegram.process_telegram_update(flow.db, incoming(flow, "2528563", 12), notify_client=True)["closed"]
    flow.http.assert_called_once()


@pytest.mark.parametrize("command", ["/close", "/start", "inline"])
@pytest.mark.parametrize("workflow", ["task", "auto"])
def test_client_close_and_start_never_enter_onboarding_or_staff_flow(flow, command, workflow):
    set_workflow(flow, workflow)
    choose(flow)
    if command == "inline":
        update = {"update_id": 11, "callback_query": {"id": "close", "data": "client_close",
            "from": {"id": int(flow.contact.external_user_id)}, "message": incoming(flow, "")["message"]}}
        update["callback_query"]["message"]["message_id"] = int(flow.ticket.client_close_message_id)
    else:
        update = incoming(flow, command)
    assert telegram.process_telegram_update(flow.db, update, notify_client=True)["closed"]
    assert flow.ticket.status == "closed" and flow.ticket.assignee_id is None
    assert "Откуда клиент» закрыто" in flow.sent.call_args.args[1]
    if command == "/start":
        telegram._send_problem_menu_for_contact.assert_called_once()
        drafts = flow.db.scalars(select(Ticket).where(Ticket.status != "closed")).all()
        assert len(drafts) == 1 and drafts[0].intake_state == "collecting" and drafts[0].assignee_id is None
    assert telegram.process_telegram_update(flow.db, update) == {"ok": True, "duplicate": True}
    flow.http.assert_not_called()


@pytest.mark.parametrize("workflow", ["task", "auto"])
def test_cleanup_uses_deadline_not_system_updated_at_and_is_idempotent(flow, monkeypatch, workflow):
    set_workflow(flow, workflow)
    monkeypatch.setattr(origin, "now", lambda: 1000)
    choose(flow)
    telegram.append_system_message(flow.db, flow.ticket, "Системное обновление")
    flow.db.commit()
    monkeypatch.setattr(origin, "now", lambda: 1599)
    assert telegram.cleanup_expired_task_intents(flow.db) == 0
    monkeypatch.setattr(origin, "now", lambda: 1600)
    assert telegram.cleanup_expired_task_intents(flow.db) == 1
    assert telegram.cleanup_expired_task_intents(flow.db) == 0
    assert flow.ticket.status == "closed" and flow.ticket.assignee_id is None
    assert "10 минут" in flow.sent.call_args.args[1]
    flow.http.assert_not_called()


def test_input_after_deadline_closes_without_lookup(flow, monkeypatch):
    monkeypatch.setattr(origin, "now", lambda: 1000)
    choose(flow)
    monkeypatch.setattr(origin, "now", lambda: 1601)
    assert telegram.process_telegram_update(flow.db, incoming(flow, "2528563"), notify_client=True)["closed"]
    flow.http.assert_not_called()


def test_file_without_number_is_archived_but_never_notifies_or_assigns_staff(flow):
    choose(flow)
    update = incoming(flow, "")
    update["message"]["document"] = {"file_id": "file-1", "file_unique_id": "unique-1", "file_name": "test.pdf", "mime_type": "application/pdf", "file_size": 12}
    assert telegram.process_telegram_update(flow.db, update, notify_client=True)["automation_collecting"]
    flow.http.assert_not_called()
    stored = flow.db.scalars(select(TicketAttachment)).one()
    telegram.start_background_archive_inbound_attachment.assert_called_once_with(stored.id)
    assert "Нужен положительный номер" in flow.sent.call_args.args[1]


def test_ten_templates_are_independent_editable_and_html_safe(flow):
    payload = settings_automatic_messages.list_automatic_messages(db=flow.db, user=flow.admin)
    category = next(c for c in payload["categories"] if c["category"] == "client_origin")
    assert category["category_title"] == "Откуда клиент" and len(category["items"]) == 10
    for item in category["items"]:
        settings_automatic_messages.update_automatic_message(item["key"],
            settings_automatic_messages.AutomaticMessageUpdatePayload(body="<b>Тест</b> {{client_origin}}", html_enabled=True),
            db=flow.db, user=flow.admin)
        flow.db.expire_all()
        text, mode = render_automatic_message_for_telegram(flow.db, item["key"], extra={"client_origin": "<script>&"})
        assert text == "<b>Тест</b> &lt;script&gt;&amp;" and mode == "HTML"


@pytest.mark.parametrize("workflow", ["task", "auto"])
def test_origin_automation_is_saved_and_roundtrips_without_changing_existing_button(flow, workflow):
    from api.app.services.button_automation_types import is_automation_allowed_for_workflow
    set_workflow(flow, workflow)
    assert not is_automation_allowed_for_workflow("ticket", "client_origin")
    assert is_automation_allowed_for_workflow(workflow, "client_origin")
    flow.button.stable_button_id = "stable-origin"
    flow.button.instruction_text = "Сохранённая инструкция"
    flow.button.client_text = "Сохранённый текст"
    flow.button.client_text_overridden = True
    flow.db.commit()
    fields = ["id", "stable_button_id", "button_key", "workflow_type", "instruction_text", "client_text", "client_text_overridden"]
    before = {name: getattr(flow.button, name) for name in fields}
    catalog = flow.client.get("/api/v1/settings/bot-buttons/automations").json()["items"]
    assert next(item for item in catalog if item["key"] == "client_origin")["workflow_types"] == ["task", "auto"]
    params = {"button_key": flow.button.button_key, "menu_type": "Menu", "workflow_type": workflow,
              "button_path": "Origin", "button_label": "Откуда клиент"}
    saved = flow.client.put("/api/v1/settings/bot-buttons/item/instruction", params=params,
                            json={"automation_type": "client_origin", "instruction_text": before["instruction_text"],
                                  "client_text": before["client_text"], "client_text_overridden": True})
    assert saved.status_code == 200, saved.text
    assert saved.json()["automation_type"] == "client_origin"
    reread = flow.client.get("/api/v1/settings/bot-buttons/item", params=params)
    assert reread.status_code == 200 and reread.json()["automation_type"] == "client_origin"
    flow.db.expire_all()
    assert {name: getattr(flow.button, name) for name in fields} == before
    assert len(flow.db.scalars(select(BotButtonSetting)).all()) == 1


def test_existing_phone_lookup_retains_its_webhook_and_contact_requests(monkeypatch):
    monkeypatch.setattr(origin.settings, "bitrix_webhook_base", "https://phone.invalid/test/")
    monkeypatch.setattr(origin.settings, "bitrix_deal_mark_webhook_base", "https://mark.invalid/test/")
    http = Mock(side_effect=[response({"result": {"CONTACT_ID": "42"}}),
        response({"result": {"PHONE": [{"VALUE": "+79990000000"}]}}),
        response({"result": {"UF_CRM_MARK": "Смотри подборку квартир на побережье"}})])
    monkeypatch.setattr(bitrix_client, "urlopen", http)
    assert bitrix_client.get_deal_phone_lookup("2528563").phone == "+79990000000"
    assert origin.lookup("2528563") == ("success", "Смотри подборку квартир на побережье")
    assert [call.args[0].full_url for call in http.call_args_list] == [
        "https://phone.invalid/test/crm.deal.get.json?ID=2528563",
        "https://phone.invalid/test/crm.contact.get.json?ID=42",
        "https://mark.invalid/test/crm.deal.get.json?ID=2528563",
    ]
    assert all(call.kwargs["timeout"] == 20 for call in http.call_args_list)


@pytest.mark.parametrize("stage", ["result", "success", "closed", "timeout"])
def test_telegram_failure_never_closes_origin_before_confirmed_reply(flow, monkeypatch, stage):
    choose(flow)
    def failed_send(chat, text, **kwargs):
        if (stage == "result" and text == "Смотри подборку квартир на побережье"
                or stage == "success" and "Проверка завершена" in text
                or stage == "closed" and "Откуда клиент» закрыто" in text
                or stage == "timeout" and "10 минут" in text):
            raise RuntimeError("Telegram unavailable")
        return {"ok": True, "result": {"message_id": 501}}
    flow.sent.side_effect = failed_send
    if stage == "timeout":
        deadline = flow.ticket.quick_action_state_json[origin.KEY]["deadline"]
        monkeypatch.setattr(origin, "now", lambda: deadline + 1)
        assert telegram.cleanup_expired_task_intents(flow.db) == 0
    else:
        update = incoming(flow, "/close" if stage == "closed" else "2528563")
        assert telegram.process_telegram_update(flow.db, update, notify_client=True)["closed"] is False
        assert telegram.process_telegram_update(flow.db, update) == {"ok": True, "duplicate": True}
    flow.db.expire_all()
    assert origin.waiting(flow.ticket) and flow.ticket.closed_at is None
    assert flow.ticket.assignee_id is None


def test_old_origin_close_button_cannot_close_current_origin(flow):
    choose(flow)
    update = {"update_id": 11, "callback_query": {"id": "old-close", "data": "client_close",
        "from": {"id": int(flow.contact.external_user_id)}, "message": incoming(flow, "")["message"]}}
    update["callback_query"]["message"]["message_id"] = 499
    assert telegram.process_telegram_update(flow.db, update, notify_client=True)["closed"] is False
    assert origin.waiting(flow.ticket)


def test_confirmed_result_is_not_repeated_when_final_message_is_retried_after_restart(flow):
    choose(flow)
    def fail_final(chat, text, **kwargs):
        if "Проверка завершена" in text:
            raise RuntimeError("final message failed")
        return {"ok": True, "result": {"message_id": 501}}
    flow.sent.side_effect = fail_final
    update = incoming(flow, "2528563")
    assert telegram.process_telegram_update(flow.db, update, notify_client=True)["automation_delivery_pending"]
    flow.db.expire_all()
    assert origin.waiting(flow.ticket)
    assert flow.ticket.quick_action_state_json[origin.KEY]["finish"]["result_message_id"] == 501
    flow.sent.side_effect = None
    with flow.factory() as restarted_db:
        retry = incoming(flow, "2528563", 12)
        assert telegram.process_telegram_update(restarted_db, retry, notify_client=True)["closed"]
        assert telegram.process_telegram_update(restarted_db, retry) == {"ok": True, "duplicate": True}
        assert restarted_db.get(Ticket, flow.ticket.id).status == "closed"
        assert len(restarted_db.scalars(select(TicketMessage).where(TicketMessage.body == "Откуда клиент: success")).all()) == 1
    assert sum(call.args[1] == "Смотри подборку квартир на побережье" for call in flow.sent.call_args_list) == 1
    flow.http.assert_called_once()


@pytest.mark.parametrize("trigger", ["cleanup", "/start", "/close", "inline"])
def test_started_success_cannot_be_replaced_by_timeout_or_client_close(flow, monkeypatch, trigger):
    choose(flow)
    mark = "Смотри подборку квартир на побережье"
    def fail_final(chat, text, **kwargs):
        if "Проверка завершена" in text:
            raise RuntimeError("final message failed")
        return {"ok": True, "result": {"message_id": 501}}
    flow.sent.side_effect = fail_final
    assert telegram.process_telegram_update(flow.db, incoming(flow, "2528563"), notify_client=True)["automation_delivery_pending"]
    flow.db.expire_all()
    original = dict(flow.ticket.quick_action_state_json[origin.KEY]["finish"])
    assert original == {"key": "success", "mark": mark, "result_message_id": 501}
    if trigger == "cleanup":
        deadline = flow.ticket.quick_action_state_json[origin.KEY]["deadline"]
        monkeypatch.setattr(origin, "now", lambda: deadline + 1)
        run = lambda update_id: telegram.cleanup_expired_task_intents(flow.db)
    else:
        def run(update_id):
            update = incoming(flow, trigger, update_id)
            if trigger == "inline":
                update = {"update_id": update_id, "callback_query": {"id": str(update_id), "data": "client_close",
                    "from": update["message"]["from"], "message": update["message"]}}
                update["callback_query"]["message"]["message_id"] = int(flow.ticket.client_close_message_id)
            return telegram.process_telegram_update(flow.db, update, notify_client=True)
    first_retry = run(12)
    assert first_retry == 0 if trigger == "cleanup" else first_retry["closed"] is False
    flow.db.expire_all()
    assert origin.waiting(flow.ticket)
    assert flow.ticket.quick_action_state_json[origin.KEY]["finish"] == original
    telegram._send_problem_menu_for_contact.assert_not_called()
    flow.sent.side_effect = None
    completed = run(13)
    assert completed == 1 if trigger == "cleanup" else completed["closed"] is True
    flow.db.expire_all()
    assert flow.ticket.status == "closed"
    delivery = flow.ticket.quick_action_state_json[origin.KEY]["finish"]
    assert delivery["key"] == "success" and delivery["mark"] == mark
    assert delivery["result_message_id"] == 501 and delivery["success_message_id"] == 500
    texts = [call.args[1] for call in flow.sent.call_args_list]
    assert texts.count(mark) == 1
    assert not any("10 минут" in text or "Откуда клиент» закрыто" in text for text in texts)
    notes = flow.db.scalars(select(TicketMessage).where(TicketMessage.body.like("Откуда клиент:%"))).all()
    assert [note.body for note in notes] == ["Откуда клиент: success"]
    if trigger == "/start":
        telegram._send_problem_menu_for_contact.assert_called_once()
    else:
        telegram._send_problem_menu_for_contact.assert_not_called()
    flow.http.assert_called_once()


@pytest.mark.parametrize("reply", [{"ok": False}, {"ok": True}, {"ok": True, "result": {"message_id": 0}},
                                  {"ok": True, "result": {"message_id": True}}])
@pytest.mark.parametrize("stage", ["result", "success"])
def test_unconfirmed_telegram_response_keeps_origin_open(flow, reply, stage):
    choose(flow)
    flow.sent.side_effect = lambda chat, text, **kwargs: reply if (
        stage == "result" and text == "Смотри подборку квартир на побережье"
        or stage == "success" and "Проверка завершена" in text) else {"ok": True, "result": {"message_id": 501}}
    assert telegram.process_telegram_update(flow.db, incoming(flow, "2528563"), notify_client=True)["closed"] is False
    flow.db.expire_all()
    assert origin.waiting(flow.ticket) and flow.ticket.closed_at is None
    if stage == "result":
        assert not any("Проверка завершена" in call.args[1] for call in flow.sent.call_args_list)
    assert telegram.process_telegram_update(flow.db, incoming(flow, "2528563")) == {"ok": True, "duplicate": True}
    flow.http.assert_called_once()


@pytest.mark.parametrize("new_kind", ["origin", "ordinary"])
def test_historical_origin_button_never_closes_a_new_request(flow, new_kind):
    choose(flow)
    old_message_id = int(flow.ticket.client_close_message_id)
    telegram.process_telegram_update(flow.db, incoming(flow, "/close"), notify_client=True)
    old_ticket_id = flow.ticket.id
    new_ticket = _seed_ticket(flow.db, company_id=flow.ticket.company_id, client_id=flow.ticket.client_id,
        workflow_type="task" if new_kind == "origin" else "ticket",
        intake_state="collecting" if new_kind == "origin" else "ready",
        status="unassigned" if new_kind == "origin" else "new")
    new_ticket.client_close_message_id = "900"
    flow.db.commit()
    flow.ticket = new_ticket
    if new_kind == "origin":
        flow.sent.return_value = {"ok": True, "result": {"message_id": 900}}
        choose(flow, update_id=12)
    update = {"update_id": 13, "callback_query": {"id": "old-close", "data": "client_close",
        "from": {"id": int(flow.contact.external_user_id)}, "message": incoming(flow, "")["message"]}}
    update["callback_query"]["message"]["message_id"] = old_message_id
    assert telegram.process_telegram_update(flow.db, update, notify_client=True)["ignored"] == "stale_client_close"
    flow.db.expire_all()
    assert new_ticket.status != "closed" and new_ticket.client_close_message_id == "900"
    assert flow.db.get(Ticket, old_ticket_id).status == "closed"
    assert telegram.process_telegram_update(flow.db, update) == {"ok": True, "duplicate": True}


def test_failed_start_close_does_not_open_menu_until_reply_is_confirmed(flow):
    choose(flow)
    flow.sent.side_effect = RuntimeError("Telegram unavailable")
    assert telegram.process_telegram_update(flow.db, incoming(flow, "/start"), notify_client=True)["closed"] is False
    telegram._send_problem_menu_for_contact.assert_not_called()
    assert origin.waiting(flow.ticket)
    flow.sent.side_effect = None
    assert telegram.process_telegram_update(flow.db, incoming(flow, "/start", 12), notify_client=True)["closed"]
    telegram._send_problem_menu_for_contact.assert_called_once()


def test_current_origin_button_still_requires_standard_close_permission(flow, monkeypatch):
    choose(flow)
    permission = Mock(return_value=False)
    monkeypatch.setattr(telegram, "client_can_close_ticket", permission)
    update = {"update_id": 11, "callback_query": {"id": "close", "data": "client_close",
        "from": {"id": int(flow.contact.external_user_id)}, "message": incoming(flow, "")["message"]}}
    update["callback_query"]["message"]["message_id"] = int(flow.ticket.client_close_message_id)
    assert telegram.process_telegram_update(flow.db, update, notify_client=True)["closed"] is False
    permission.assert_called_once_with(flow.db, flow.ticket)
    assert origin.waiting(flow.ticket)


def test_polling_loop_actually_runs_origin_timeout_cleanup(flow, monkeypatch):
    monkeypatch.setattr(origin, "now", lambda: 1000)
    choose(flow)
    monkeypatch.setattr(origin, "now", lambda: 1600)
    monkeypatch.setattr(telegram_polling, "SessionLocal", flow.factory)
    monkeypatch.setattr(auto_close, "cleanup_auto_close_tickets", lambda db: None)
    def stop_polling(*args):
        raise asyncio.CancelledError()
    monkeypatch.setattr(telegram_polling, "get_updates", stop_polling)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(telegram_polling.telegram_polling_loop())
    flow.db.expire_all()
    assert flow.ticket.status == "closed"
    assert "10 минут" in flow.sent.call_args.args[1]
    flow.http.assert_not_called()


@pytest.mark.parametrize("second", ["duplicate", "another_number", "close"])
def test_parallel_updates_and_cleanup_cannot_repeat_lookup_or_double_close(flow, tmp_path, monkeypatch, second):
    clock = [1000]
    monkeypatch.setattr(origin, "now", lambda: clock[0])
    choose(flow)
    path = tmp_path / "parallel.db"
    with sqlite3.connect(path) as target:
        flow.db.connection().connection.driver_connection.backup(target)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine, autoflush=False)
    entered, release = Event(), Event()
    def request(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return response({"result": {"UF_CRM_MARK": "Смотри подборку квартир на побережье"}})
    flow.http.side_effect = request
    def process(update):
        with factory() as db:
            return telegram.process_telegram_update(db, update, notify_client=True)
    first = incoming(flow, "2528563")
    next_update = first if second == "duplicate" else incoming(flow, "/close" if second == "close" else "2528563", 12)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            a = workers.submit(process, first)
            assert entered.wait(10)
            b = workers.submit(process, next_update)
            clock[0] = 1700
            try:
                with factory() as db:
                    assert telegram.cleanup_expired_task_intents(db) == 0
            finally:
                release.set()
            assert a.result(timeout=10)["closed"]
            result = b.result(timeout=10)
            if second == "duplicate":
                assert result == {"ok": True, "duplicate": True}
        flow.http.assert_called_once()
        with factory() as db:
            assert db.get(Ticket, flow.ticket.id).status == "closed"
            notes = db.scalars(select(TicketMessage).where(TicketMessage.body == "Откуда клиент: success")).all()
            assert len(notes) == 1
    finally:
        engine.dispose()
