import hashlib
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text

from api.app.auth import get_current_user
from api.app.db import get_db
from api.app.models import (
    BotButtonSetting, BotMenuLevelLayout, Company, TelegramAutomationGroup, TelegramContact, TelegramGroupRegistrationCode,
    TelegramUpdate, Ticket, TicketMessage,
)
from api.app.routers import settings_bot_button_items, telegram, telegram_groups
from api.app.routers import settings_bot_button_structure as structure
from api.app.services.button_config import build_level_layout_key
from api.app.services import schema_evolution, telegram_group_registry as registry
from api.app.services.telegram_bot import TelegramApiError
from test_employee_notification_preferences import _user
from test_ticket_messages_outbound import _seed_ticket, _session_bundle
from test_telegram_client_blocking import _seed_contact, _seed_ticket as _seed_client_ticket


@pytest.fixture
def case(monkeypatch):
    engine, factory, db = _session_bundle()
    admin = _user(db, role="admin")
    monkeypatch.setattr(registry.settings, "app_env", "dev")
    monkeypatch.setattr(registry.settings, "telegram_staging_chat_id", "-999")
    def request(method, payload=None, **kwargs):
        values = {
            "getMe": {"id": 101, "username": "dev_bot"},
            "getChat": {"id": int((payload or {}).get("chat_id", -100)), "title": "Тестовая группа", "type": "supergroup"},
            "getChatAdministrators": [{"user": {"id": 777}, "status": "administrator"}],
            "getChatMember": {"status": "member"},
            "sendMessage": {"message_id": 55}, "deleteMessage": True,
        }
        return {"ok": True, "result": values[method]}
    api = Mock(side_effect=request)
    monkeypatch.setattr(registry, "telegram_request", api)
    app = FastAPI()
    app.include_router(telegram_groups.router)
    app.include_router(settings_bot_button_items.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: admin
    with TestClient(app) as client:
        yield SimpleNamespace(db=db, engine=engine, factory=factory, admin=admin,
                              client=client, api=api, app=app, request=request)
    db.close()
    engine.dispose()


def code(case):
    response = case.client.post("/api/v1/settings/telegram-groups/registration-code")
    assert response.status_code == 200, response.text
    return response.json()["command"]


def payload(command, *, update_id=1, chat_id=-100, chat_type="supergroup", sender=777):
    return {"update_id": update_id, "message": {
        "message_id": update_id, "from": {"id": sender},
        "chat": {"id": chat_id, "title": "Untrusted title", "type": chat_type}, "text": command,
    }}


def register(case, command=None, **kwargs):
    return telegram.process_telegram_update(case.db, payload(command or code(case), **kwargs), notify_client=False)


@pytest.mark.parametrize("chat_type", ["group", "supergroup"])
def test_registration_verifies_actual_send_and_delete_and_redacts_code(case, chat_type):
    command = code(case)
    token = command.split()[1]
    stored = case.db.scalars(select(TelegramGroupRegistrationCode)).one()
    assert stored.code_hash == hashlib.sha256(token.encode()).hexdigest()
    assert timedelta(minutes=9) < stored.expires_at - datetime.utcnow() <= timedelta(minutes=10)
    result = register(case, command, chat_type=chat_type)
    assert result["group_registration"] is True
    case.db.rollback()
    case.db.expire_all()
    group = case.db.get(TelegramAutomationGroup, result["group_id"])
    assert (group.chat_id, group.title, group.status, group.environment) == ("-100", "Тестовая группа", "verified", "dev")
    assert group.registered_at and group.checked_at
    assert case.db.get(TelegramGroupRegistrationCode, stored.id).used_at
    methods = [call.args[0] for call in case.api.call_args_list]
    assert "getChatAdministrators" in methods and "getChatMember" in methods
    assert methods[-2:] == ["sendMessage", "deleteMessage"]
    sent = next(call.args[1] for call in case.api.call_args_list if call.args[0] == "sendMessage")
    assert sent["chat_id"] == "-100" and sent["disable_notification"] is True
    update = case.db.scalars(select(TelegramUpdate)).one()
    assert token not in update.payload_json
    assert not case.db.scalars(select(Ticket)).all()
    assert not case.db.scalars(select(TelegramContact)).all()
    listed = case.client.get("/api/v1/settings/telegram-groups").json()["items"]
    assert listed[0]["title"] == group.title and listed[0]["status"] == "verified"


def test_same_update_and_consumed_code_cannot_register_again(case):
    command = code(case)
    register(case, command)
    count = case.api.call_count
    assert register(case, command) == {"ok": True, "duplicate": True}
    assert register(case, command, update_id=2, chat_id=-200)["group_registration"] is False
    assert case.api.call_count == count
    assert len(case.db.scalars(select(TelegramAutomationGroup)).all()) == 1


@pytest.mark.parametrize("reason", ["expired", "unknown", "revoked", "wrong_environment", "wrong_bot", "wrong_target"])
def test_invalid_code_or_issuer_cannot_register(case, reason, monkeypatch):
    command = code(case)
    row = case.db.scalars(select(TelegramGroupRegistrationCode)).one()
    if reason == "expired":
        row.expires_at = datetime.utcnow() - timedelta(seconds=1)
    elif reason == "unknown":
        command = "/register_group invalid"
    elif reason == "revoked":
        registry.require_admin(case.db, case.admin.id).role = "agent"
    elif reason == "wrong_environment":
        monkeypatch.setattr(registry.settings, "app_env", "prod")
    elif reason == "wrong_bot":
        row.bot_id = "other-bot"
    elif reason == "wrong_target":
        command = command.replace("@dev_bot", "@another_bot")
    case.db.commit()
    case.api.reset_mock()
    assert register(case, command)["group_registration"] is False
    assert not case.db.scalars(select(TelegramAutomationGroup)).all()
    assert not any(call.args[0] == "sendMessage" for call in case.api.call_args_list)


@pytest.mark.parametrize("failure", ["not_admin", "anonymous", "forwarded", "bot_left", "restricted", "send_denied", "archive"])
def test_registration_rejects_insufficient_rights_and_archive(case, failure):
    command = code(case)
    data = payload(command, chat_id=-999 if failure == "archive" else -100)
    def request(method, body=None, **kwargs):
        if method == "getChatAdministrators" and failure == "not_admin":
            return {"ok": True, "result": []}
        if method == "getChatMember" and failure in {"bot_left", "restricted"}:
            return {"ok": True, "result": {"status": "left" if failure == "bot_left" else "restricted", "is_member": True, "can_send_messages": False}}
        if method == "sendMessage" and failure == "send_denied":
            raise TelegramApiError("Forbidden")
        return case.request(method, body, **kwargs)
    case.api.side_effect = request
    if failure == "anonymous":
        data["message"]["sender_chat"] = {"id": -100}
    if failure == "forwarded":
        data["message"]["forward_origin"] = {"type": "user"}
    result = telegram.process_telegram_update(case.db, data)
    assert result["group_registration"] is False
    assert not case.db.scalars(select(TelegramAutomationGroup)).all()
    assert case.db.scalars(select(TelegramGroupRegistrationCode)).one().used_at is None


def test_recheck_updates_status_and_title_and_new_code_preserves_group_id(case):
    first = register(case)
    group_id = first["group_id"]
    def denied(method, body=None, **kwargs):
        if method == "sendMessage":
            raise TelegramApiError("Forbidden")
        return case.request(method, body, **kwargs)
    case.api.side_effect = denied
    response = case.client.post(f"/api/v1/settings/telegram-groups/{group_id}/check")
    assert response.json()["status"] == "unavailable"
    def renamed(method, body=None, **kwargs):
        response = case.request(method, body, **kwargs)
        if method == "getChat":
            response["result"]["title"] = "Новое название"
        return response
    case.api.side_effect = renamed
    response = case.client.post(f"/api/v1/settings/telegram-groups/{group_id}/check")
    assert response.json()["status"] == "verified" and response.json()["title"] == "Новое название"
    assert register(case, update_id=2)["group_id"] == group_id
    assert len(case.db.scalars(select(TelegramAutomationGroup)).all()) == 1


@pytest.mark.parametrize("role", ["agent", "trainee", "chiki_piki"])
def test_registry_api_is_admin_only(case, role):
    case.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=case.admin.id, role=role)
    for method, url in [("get", ""), ("post", "/registration-code"), ("post", "/missing/check")]:
        assert getattr(case.client, method)("/api/v1/settings/telegram-groups" + url).status_code == 403


def save_button(case, group_id, *, workflow="auto"):
    return case.client.put("/api/v1/settings/bot-buttons/item/instruction", params={
        "button_key": "Menu|ticket|Request", "menu_type": "Menu", "workflow_type": workflow,
        "button_path": "Request", "button_label": "Request",
    }, json={"automation_type": "telegram_group", "telegram_group_id": group_id})


@pytest.mark.parametrize("selection", ["verified", "missing", "unverified", "other_environment", "other_company"])
def test_button_requires_accessible_verified_group_and_roundtrips_selection(case, selection):
    group_id = register(case)["group_id"]
    group = case.db.get(TelegramAutomationGroup, group_id)
    if selection == "unverified":
        group.status = "unavailable"
    elif selection == "other_environment":
        group.environment = "prod"
    elif selection == "other_company":
        group.company_id = "another-company"
    case.db.commit()
    response = save_button(case, None if selection == "missing" else group_id)
    assert response.status_code == (200 if selection == "verified" else 422), response.text
    if selection == "verified":
        case.db.expire_all()
        row = case.db.scalars(select(BotButtonSetting)).one()
        assert row.telegram_group_id == group_id and row.automation_key == "telegram_group"
        response = case.client.get("/api/v1/settings/bot-buttons/item", params={"button_key": row.button_key, "menu_type": "Menu"})
        assert response.status_code == 200, response.text
        assert response.json()["telegram_group_id"] == group_id


def test_non_registration_group_messages_still_ignored(case):
    result = register(case, "hello")
    assert result == {"ok": True, "ignored": "non_private_chat"}
    case.api.assert_not_called()
    assert not case.db.scalars(select(Ticket)).all()


@pytest.mark.parametrize("field", ["automation_type", "automation_key"])
def test_override_flag_cannot_bypass_required_group(case, field):
    response = case.client.put("/api/v1/settings/bot-buttons/item/instruction", params={
        "button_key": "Menu|auto|Request", "menu_type": "Menu", "workflow_type": "auto",
        "button_path": "Request", "button_label": "Request",
    }, json={field: "telegram_group", "automation_overridden": False})
    assert response.status_code == 422
    assert not case.db.scalars(select(BotButtonSetting)).all()


def test_probe_cleanup_failure_is_explicit_and_does_not_hide_successful_send(case):
    command = code(case)
    def request(method, body=None, **kwargs):
        if method == "deleteMessage":
            raise TelegramApiError("Forbidden")
        return case.request(method, body, **kwargs)
    case.api.side_effect = request
    group = case.db.get(TelegramAutomationGroup, register(case, command)["group_id"])
    assert group.status == "verified" and group.last_error == "probe_cleanup_failed"


def test_other_bot_recheck_marks_group_unavailable_without_sending(case):
    group_id = register(case)["group_id"]
    case.api.reset_mock()
    def request(method, body=None, **kwargs):
        if method == "getMe":
            return {"ok": True, "result": {"id": 202, "username": "other_bot"}}
        return case.request(method, body, **kwargs)
    case.api.side_effect = request
    response = case.client.post(f"/api/v1/settings/telegram-groups/{group_id}/check")
    assert response.json()["status"] == "unavailable"
    assert [call.args[0] for call in case.api.call_args_list] == ["getMe"]


@pytest.mark.parametrize("confirmation", [False, True])
def test_auto_button_callback_keeps_request_open_without_false_success(case, monkeypatch, confirmation):
    company = case.db.get(Company, registry.require_admin(case.db, case.admin.id).company_id)
    client, _, contact = _seed_contact(case.db, company=company)
    ticket = _seed_client_ticket(case.db, company_id=company.id, client_id=client.id, workflow_type="auto",
                        intake_state="collecting", status="unassigned")
    row = BotButtonSetting(id="auto-group", button_key="Menu|auto|Request", menu_type="Menu", workflow_type="auto",
                           button_path="Request", button_label="Request", automation_key="telegram_group",
                           automation_overridden=True, currently_present=True)
    case.db.add(row)
    case.db.commit()
    monkeypatch.setattr(telegram, "_menu_name_for_contact", lambda *args: "Menu")
    monkeypatch.setattr(telegram, "_resolve_effective_confirmation", lambda *args: (False, None))
    monkeypatch.setattr(telegram, "choose_online_agent", lambda *args: None)
    sent, answered = Mock(return_value={"ok": True}), Mock()
    monkeypatch.setattr(telegram, "send_message", sent)
    monkeypatch.setattr(telegram, "answer_callback_query", answered)
    monkeypatch.setattr(telegram, "_safe_remove_inline_keyboard", lambda *args: None)
    contact.active_menu_message_id = ticket.menu_message_id = "1"
    contact.menu_message_ids_json = [1]
    if confirmation:
        ticket.button_config_key = row.button_key
    case.db.commit()
    data = f"btn:{row.button_key}"
    if confirmation:
        data = f"confirm:yes:{row.button_key}"
    update = {"update_id": 1, "callback_query": {"id": "cb", "data": data,
        "from": {"id": int(contact.external_user_id)},
        "message": {"message_id": 1, "chat": {"id": int(contact.external_chat_id), "type": "private"}}}}
    if confirmation:
        update["callback_query"]["message"]["reply_markup"] = telegram._confirmation_markup(row.button_key)
    result = telegram.process_telegram_update(case.db, update, notify_client=True)
    assert result.get("group_waiting") is True, result
    assert case.db.get(Ticket, result["ticket_id"]).status != "closed"
    assert answered.call_args.args[1] == "Отправьте одно сообщение"
    assert not any("обработана" in str(call) for call in sent.call_args_list)


@pytest.mark.parametrize("workflow", ["ticket", "task", "auto"])
def test_prepared_automation_never_reports_success_or_closes_ticket(case, monkeypatch, workflow):
    ticket = _seed_ticket(case.db)
    ticket.workflow_type = workflow
    ticket.instruction_button_key = "prepared-group"
    case.db.add(BotButtonSetting(id="prepared", button_key="prepared-group", menu_type="Menu",
                                workflow_type=workflow, button_path="Request", button_label="Request",
                                automation_key="telegram_group", automation_overridden=True))
    case.db.commit()
    sent = Mock()
    monkeypatch.setattr(telegram, "send_message", sent)
    result = telegram._dispatch_builtin_button_automation(case.db, ticket, None, "client-chat", False)
    assert result["automation_pending"] is True
    case.db.refresh(ticket)
    assert ticket.status != "closed" and ticket.closed_at is None
    assert any("Не удалось начать ожидание" in message.body for message in case.db.scalars(select(TicketMessage)))
    sent.assert_not_called()
    assert not any(call.args[0] == "sendMessage" for call in case.api.call_args_list)


def test_schema_evolution_adds_registry_idempotently_without_changing_old_buttons(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(schema_evolution, "engine", engine)
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE bot_button_settings (id VARCHAR(36) PRIMARY KEY, button_key VARCHAR(500))"))
            conn.execute(text("INSERT INTO bot_button_settings VALUES ('old', 'Menu|ticket|Old')"))
        schema_evolution.ensure_telegram_group_registry_tables()
        schema_evolution.ensure_telegram_group_registry_tables()
        assert "telegram_group_id" in {col["name"] for col in inspect(engine).get_columns("bot_button_settings")}
        with engine.connect() as conn:
            assert conn.execute(text("SELECT * FROM bot_button_settings")).one() == ("old", "Menu|ticket|Old", None)
    finally:
        engine.dispose()


def test_registry_routes_are_registered_in_application():
    from api.app.main import app
    routes = {(route.path, method) for route in app.routes for method in getattr(route, "methods", [])}
    for method, path in [
        ("GET", "/api/v1/settings/telegram-groups"),
        ("POST", "/api/v1/settings/telegram-groups/registration-code"),
        ("POST", "/api/v1/settings/telegram-groups/{group_id}/check"),
    ]:
        assert (path, method) in routes


@pytest.mark.parametrize("parent", ["", "Раздел"])
def test_slash_in_button_label_preserves_parent_after_type_change_and_resave(case, parent):
    case.app.include_router(structure.router)
    menu = "Колл Центр"
    label = "❌Не берет/долго берет ОП (Для РОП)❌"
    group_id = register(case)["group_id"]
    if parent:
        structure.create_bot_button_item({"menu_type": menu, "label": parent, "button_kind": "section"},
                                         db=case.db, user=case.admin)
    created = structure.create_bot_button_item({
        "menu_type": menu, "label": label, "parent_path": parent, "button_kind": "task", "workflow_type": "task",
    }, db=case.db, user=case.admin)
    old_key = created["button_key"]
    button = case.db.scalar(select(BotButtonSetting).where(BotButtonSetting.button_key == old_key))
    identity = button.id, button.stable_button_id
    path = button.button_path
    button.instruction_text = "Preserved instruction"
    layout = BotMenuLevelLayout(id="slash-layout", menu_type=menu, level_key=build_level_layout_key(menu, parent),
                                layout_json=[{"columns": 1, "button_keys": [old_key]}])
    case.db.add(layout)
    case.db.commit()
    for _ in range(2):
        # A stale editor key must still resolve the same button after task -> auto.
        response = case.client.put("/api/v1/settings/bot-buttons/item/instruction", params={
            "button_key": old_key, "menu_type": menu, "workflow_type": "auto",
            "button_path": path, "button_label": label,
        }, json={"automation_type": "telegram_group", "telegram_group_id": group_id})
        assert response.status_code == 200, response.text
        case.db.expire_all()
        assert button.parent_path == parent
        assert (button.id, button.stable_button_id) == identity
        assert button.button_key == f"{menu}|auto|{path}"
        assert button.button_path == path and button.instruction_text == "Preserved instruction"
        assert button.telegram_group_id == group_id and button.automation_key == "telegram_group"
        assert button.deleted_at is None
        assert layout.layout_json == [{"columns": 1, "button_keys": [button.button_key]}]
        assert len(case.db.scalars(select(BotButtonSetting)).all()) == (2 if parent else 1)
        response = case.client.get(f"/api/v1/settings/bot-buttons/menu-types/{menu}/structure")
        assert response.status_code == 200, response.text
        rows = response.json()["tree_rows"]
        if parent:
            rows = next(node["children_rows"] for row in rows for node in row["buttons"] if node["full_path"] == parent)
        assert any(node["stable_button_id"] == identity[1] for row in rows for node in row["buttons"])


@pytest.mark.parametrize("parent", ["", "Раздел", "Раздел/Подраздел"])
def test_slash_button_full_edit_lifecycle_preserves_real_hierarchy(case, parent):
    case.app.include_router(structure.router)
    menu = "Колл Центр"
    group_id = register(case)["group_id"]
    # Real submenu hierarchy alongside a root button whose label contains '/'.
    for path, name in [("", "Раздел"), ("Раздел", "Подраздел")]:
        structure.create_bot_button_item({"menu_type": menu, "label": name, "parent_path": path,
                                         "button_kind": "section"}, db=case.db, user=case.admin)
    structure.create_bot_button_item({"menu_type": menu, "parent_path": "Раздел/Подраздел", "label": "Обычная кнопка",
                                     "button_kind": "task", "workflow_type": "task"}, db=case.db, user=case.admin)
    structure.create_bot_button_item({"menu_type": "Другое меню", "label": "Чужая/кнопка",
                                     "button_kind": "task", "workflow_type": "task"}, db=case.db, user=case.admin)
    untouched = {row.id: {column.name: getattr(row, column.name) for column in row.__table__.columns}
                 for row in case.db.scalars(select(BotButtonSetting))}
    label = "Не берет/долго берет ОП"
    response = case.client.post("/api/v1/settings/bot-buttons/item/create", json={
        "menu_type": menu, "parent_path": parent, "label": label, "button_kind": "task", "workflow_type": "task",
    })
    assert response.status_code == 200, response.text
    initial_key = response.json()["button_key"]
    row = case.db.scalar(select(BotButtonSetting).where(BotButtonSetting.button_key == initial_key))
    identity, path = (row.id, row.stable_button_id), row.button_path
    assert row.parent_path == parent
    assert row.button_key == f"{menu}|task|{path}"
    def visible_node():
        response = case.client.get(f"/api/v1/settings/bot-buttons/menu-types/{menu}/structure")
        assert response.status_code == 200, response.text
        rows = response.json()["tree_rows"]
        prefix = ""
        for part in parent.split("/") if parent else []:
            prefix = f"{prefix}/{part}" if prefix else part
            rows = next(node["children_rows"] for level in rows for node in level["buttons"] if node["full_path"] == prefix)
        return next(node for level in rows for node in level["buttons"] if node["stable_button_id"] == identity[1])
    assert visible_node()["label"] == label
    renamed = "Новое/название кнопки"
    for workflow, changes in [
        ("task", {"instruction_text": "Instruction"}),
        ("task", {"label_overridden": True, "label_override": renamed}),
        ("auto", {"automation_type": "telegram_group", "telegram_group_id": group_id}),
        ("auto", {}),
        ("task", {}),
        ("task", {}),
    ]:
        previous_key = row.button_key
        response = case.client.put("/api/v1/settings/bot-buttons/item/instruction", params={
            "button_key": initial_key, "menu_type": menu, "workflow_type": workflow,
            "button_path": path, "button_label": row.label_override or label,
        }, json=changes)
        assert response.status_code == 200, response.text
        case.db.expire_all()
        assert row.parent_path == parent and (row.id, row.stable_button_id) == identity
        assert row.button_path == path and row.button_key == f"{menu}|{workflow}|{path}"
        if workflow == "task" and previous_key.endswith(f"|task|{path}"):
            assert row.button_key == previous_key
        node = visible_node()
        assert node["technical_key"] == row.button_key and node["is_enabled"] is True
        assert node["label"] == (renamed if row.label_overridden else label)
        assert row.instruction_text == "Instruction" and row.deleted_at is None
        assert len(case.db.scalars(select(BotButtonSetting)).all()) == len(untouched) + 1
        for id_, snapshot in untouched.items():
            original = case.db.get(BotButtonSetting, id_)
            assert {field: getattr(original, field) for field in snapshot} == snapshot
    other = case.client.get("/api/v1/settings/bot-buttons/menu-types/Другое меню/structure").json()["tree_rows"]
    assert all(node["stable_button_id"] != identity[1] for level in other for node in level["buttons"])


@pytest.mark.parametrize("parent", ["", "Раздел"])
def test_first_instruction_save_with_slash_label_uses_whole_label(case, parent):
    # The instruction endpoint also has a legacy branch that creates a missing setting.
    label = "Не берет/долго берет ОП"
    path = f"{parent}/{label}" if parent else label
    response = case.client.put("/api/v1/settings/bot-buttons/item/instruction", params={
        "button_key": f"Колл Центр|task|{path}", "menu_type": "Колл Центр", "workflow_type": "task",
        "button_path": path, "button_label": label,
    }, json={"instruction_text": "Instruction"})
    assert response.status_code == 200, response.text
    row = case.db.scalars(select(BotButtonSetting)).one()
    assert row.parent_path == parent
