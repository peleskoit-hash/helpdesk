import uuid
from types import SimpleNamespace

import pytest

from api.app.models import AutomaticMessageTemplate, Client, Company, TelegramContact, Ticket
from api.app.routers import telegram
from api.app.services.automatic_messages import get_automatic_message_definition, reset_automatic_message_override
from test_db_only_blockers import db, button, no_txt


def setup_callback(db, monkeypatch, workflow, confirmation, automation="none", individual="Individual"):
    row = button(db, "Button", workflow=workflow, kind="group_parent" if workflow == "group_task" else "terminal",
                 client_text=individual, client_text_overridden=True, confirmation_enabled=confirmation,
                 confirmation_text="Confirm", automation_key=automation, automation_overridden=True)
    db.add(Company(id="company", name="Company"))
    db.flush()
    db.add(Client(id="client", company_id="company", display_name="Client"))
    db.flush()
    contact = TelegramContact(id="contact", company_id="company", client_id="client", external_user_id="123", external_chat_id="456")
    db.add(contact)
    db.commit()
    monkeypatch.setattr(telegram, "_menu_name_for_contact", lambda *args: "menu-a")
    monkeypatch.setattr(telegram, "get_or_create_contact_from_user", lambda *args: contact)
    monkeypatch.setattr(telegram, "schedule_avatar_refresh", lambda *args: None)
    monkeypatch.setattr(telegram, "_remove_menu_keyboard", lambda *args, **kwargs: None)
    monkeypatch.setattr(telegram, "_safe_remove_inline_keyboard", lambda *args: None)
    monkeypatch.setattr(telegram, "_safe_delete_message", lambda *args: None)
    monkeypatch.setattr(telegram, "evaluate_incoming_telegram_access", lambda *args, **kwargs: SimpleNamespace(blocked=False))
    monkeypatch.setattr(telegram, "_ready_ticket_for_distribution", lambda *args: None)
    monkeypatch.setattr(telegram, "answer_callback_query", lambda *args: None)
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda chat, text, **kwargs: sent.append((text, kwargs)) or {"result": {"message_id": len(sent)}})
    return row, sent


def select(db, row, confirmation, sent):
    callback = {"id": "callback", "data": f"btnid:{row.id}", "from": {"id": 123},
                "message": {"message_id": 10, "chat": {"id": 456, "type": "private"}}}
    result = telegram.process_telegram_update(db, {"update_id": 1, "callback_query": callback}, notify_client=True)
    if confirmation:
        assert result["awaiting_confirmation"] is True
        sent.clear()
        callback["data"] = telegram._confirm_callback_data(f"btnid:{row.id}", "yes")
        result = telegram.process_telegram_update(db, {"update_id": 2, "callback_query": callback}, notify_client=True)
    assert "ticket_id" in result, result
    return result


@pytest.mark.parametrize("workflow", ["ticket", "task", "group_task"])
@pytest.mark.parametrize("confirmation", [False, True])
@pytest.mark.parametrize("policy", ["override", "base", "missing", "reset"])
@pytest.mark.parametrize("individual", ["Individual", ""])
def test_request_text_priority_in_both_callback_branches(db, monkeypatch, no_txt, workflow, confirmation, policy, individual):
    row, sent = setup_callback(db, monkeypatch, workflow, confirmation, individual=individual)
    key = "telegram.ticket.create.default_client_text" if workflow == "ticket" else "telegram.task.request_text"
    if policy != "missing":
        db.add(AutomaticMessageTemplate(id=str(uuid.uuid4()), key=key, body="<b>Central {{button_label}}</b>", is_overridden=policy != "base"))
        db.commit()
    if policy == "reset":
        reset_automatic_message_override(db, key)
        db.commit()
    result = select(db, row, confirmation, sent)
    assert len(sent) == 1, sent
    text, options = sent[0]
    if policy == "override":
        assert text == "<b>Central Button</b>"
        assert options["parse_mode"] == "HTML"
    elif individual:
        assert text == individual
    elif workflow == "ticket":
        assert text == get_automatic_message_definition(key).default_text
    else:
        assert "Опишите задачу одним сообщением." in text and "{{timeout_minutes}}" not in text
    assert row.client_text == individual
    assert db.get(Ticket, result["ticket_id"]).button_config_key == row.button_key


@pytest.mark.parametrize("automation", ["send_files", "message_only"])
@pytest.mark.parametrize("confirmation", [False, True])
def test_auto_messages_ignore_ticket_and_task_overrides(db, monkeypatch, no_txt, automation, confirmation):
    row, sent = setup_callback(db, monkeypatch, "auto", confirmation, automation)
    for key in ("telegram.ticket.create.default_client_text", "telegram.task.request_text"):
        db.add(AutomaticMessageTemplate(id=str(uuid.uuid4()), key=key, body="Central", is_overridden=True))
    db.commit()
    select(db, row, confirmation, sent)
    assert any(text == "Individual" for text, _ in sent)
    assert not any("Central" in text for text, _ in sent)
    assert row.client_text == "Individual"
