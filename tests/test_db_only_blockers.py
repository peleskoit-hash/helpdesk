from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.app.config import Settings
from api.app.auth import get_current_user
from api.app.db import Base, get_db
from api.app.models import (BotButtonSetting, BotMenuDestinationBinding, BotMenuLevelLayout,
                            BotMenuProfile, Client, Company, TelegramContact, Ticket, User)
from api.app.routers import telegram, tickets, settings_bot_button_items as items, settings_bot_button_structure as structure, settings_bot_menu_profiles as profiles
from api.app.services import button_config, ticket_problem_flow as flow
from api.app.services.button_config import build_level_layout_key


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False) as session:
        yield session
    engine.dispose()


def button(db, name, *, parent="", workflow="ticket", kind="terminal", menu="menu-a", **extra):
    path = f"{parent}/{name}" if parent else name
    row = BotButtonSetting(id=f"{menu}:{path}", button_key=f"{menu}|{workflow}|{path}",
                           menu_type=menu, button_path=path, parent_path=parent,
                           button_label=name, workflow_type=workflow, button_kind=kind,
                           currently_present=True, is_archived=False, is_db_created=False,
                           created_at=datetime(2026, 1, 1), updated_at=datetime(2026, 1, 1))
    for key, value in extra.items():
        setattr(row, key, value)
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def no_txt(monkeypatch, tmp_path):
    monkeypatch.setattr(button_config, "BUTTON_CONFIG_DIR", tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("TXT must not be read")
    for name in ("load_button_config", "list_terminal_buttons", "find_button"):
        monkeypatch.setattr(button_config, name, forbidden, raising=False)
        monkeypatch.setattr(telegram, name, forbidden, raising=False)
    yield


@pytest.mark.parametrize("old_flag", ["true", "false"])
def test_group_callback_and_description_create_old_db_children(db, monkeypatch, no_txt, old_flag):
    monkeypatch.setenv("USE_DB_BUTTONS", old_flag)
    monkeypatch.setattr(telegram, "settings", Settings(_env_file=None))
    parent = button(db, "Parent", workflow="group_task", kind="group_parent")
    first = button(db, "A", parent="Parent", workflow="group_task_child", suppress_client_answer=True)
    second = button(db, "B", parent="Parent", workflow="group_task_child")
    button(db, "Archived", parent="Parent", workflow="group_task_child", is_archived=True)
    button(db, "Missing", parent="Parent", workflow="group_task_child", currently_present=False)
    button(db, "Hidden", parent="Parent", workflow="group_task_child", visibility_overridden=True, is_enabled=False)
    button(db, "OtherParent", parent="Other", workflow="group_task_child")
    button(db, "OtherMenu", parent="Parent", workflow="group_task_child", menu="other")
    db.add(BotMenuLevelLayout(id="layout", menu_type="menu-a",
           level_key=build_level_layout_key("menu-a", "Parent"),
           layout_json=[{"columns": 1, "button_keys": [second.button_key, first.button_key]}]))
    db.add(Company(id="company", name="Company"))
    db.add(Client(id="client", company_id="company", display_name="Client"))
    agent = User(id="agent", company_id="company", role="agent", full_name="Agent", is_active=True)
    contact = TelegramContact(id="contact", company_id="company", client_id="client",
                              external_user_id="123", external_chat_id="456")
    db.add_all([agent, contact])
    db.commit()
    monkeypatch.setattr(telegram, "_menu_name_for_contact", lambda *args: "menu-a")
    monkeypatch.setattr(telegram, "_menu_name_for_ticket_client", lambda *args: "menu-a")
    monkeypatch.setattr(telegram, "get_or_create_contact_from_user", lambda *args: contact)
    monkeypatch.setattr(telegram, "schedule_avatar_refresh", lambda *args: None)
    monkeypatch.setattr(telegram, "_remove_menu_keyboard", lambda *args, **kwargs: None)
    monkeypatch.setattr(telegram, "evaluate_incoming_telegram_access", lambda *args, **kwargs: SimpleNamespace(blocked=False))
    monkeypatch.setattr(telegram, "process_review_incoming_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(telegram, "process_feedback_incoming_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(telegram, "choose_online_agent", lambda *args: agent)
    resolved = telegram._button_from_callback(db, f"btnid:{parent.id}", "menu-a")
    assert [child.key for child in resolved.subtasks] == [second.button_key, first.button_key]
    result = telegram.process_telegram_update(db, {"update_id": 1, "callback_query": {
        "id": "callback", "data": f"btnid:{parent.id}", "from": {"id": 123},
        "message": {"chat": {"id": 456, "type": "private"}},
    }})
    assert result["task_pending"] is True
    created_parent = db.get(Ticket, result["ticket_id"])
    assert created_parent.task_kind == "group_parent"
    # The description completes the group intake after the parent callback.
    result = telegram.process_telegram_update(db, {"update_id": 2, "message": {
        "message_id": 10, "chat": {"id": 456, "type": "private"},
        "from": {"id": 123}, "text": "Group description",
    }})
    assert result["ticket_id"] == created_parent.id
    children = db.query(Ticket).filter_by(parent_ticket_id=created_parent.id).order_by(Ticket.created_at).all()
    assert [child.instruction_button_key for child in children] == [second.button_key, first.button_key]
    assert [child.subject for child in children] == ["B", "A"]
    assert [child.suppress_client_answer for child in children] == [False, True]
    assert all(child.task_kind == "group_child" and child.workflow_type == "task" for child in children)
    telegram._create_group_children_from_collecting(db, created_parent, resolved, "Again", agent)
    assert db.query(Ticket).filter_by(parent_ticket_id=created_parent.id).count() == 2


def wrong_problem_args(sent, messages):
    return dict(actor_user_id="agent", actor_role="agent", actor_display_name="Agent",
                wrong_problem_text_template="Select {label}",
                get_or_create_client_profile_fn=lambda *args: SimpleNamespace(is_verified=True, destination="Office"),
                send_to_telegram_client_fn=lambda db, ticket, text: sent.append(text) or {"result": {"message_id": 5}},
                telegram_message_id_from_response_fn=lambda response: "5",
                add_message_fn=lambda *args, **kwargs: messages.append((args, kwargs)),
                unfreeze_ticket_on_message_fn=lambda ticket: None,
                remove_client_close_button_fn=lambda *args: None)


@pytest.mark.parametrize("use_db", [True, False])
@pytest.mark.parametrize("workflow", ["ticket", "task"])
def test_wrong_problem_menu_and_apply_without_txt(db, monkeypatch, no_txt, use_db, workflow):
    monkeypatch.setenv("USE_DB_BUTTONS", str(use_db).lower())
    current = button(db, "Current", label_overridden=True, label_override="Current renamed")
    section = button(db, "Section", workflow="submenu", kind="section", label_override="Folder", label_overridden=True)
    target = button(db, "Target", parent="Section", label_override="Renamed", label_overridden=True)
    alternative = button(db, "Alternative", parent="Section", workflow="task")
    button(db, "Archived", is_archived=True)
    button(db, "Hidden", visibility_overridden=True, is_enabled=False)
    button(db, "HiddenSection", workflow="submenu", kind="section", visibility_overridden=True, is_enabled=False)
    button(db, "HiddenChild", parent="HiddenSection")
    db.add(BotMenuLevelLayout(id="layout", menu_type="menu-a", level_key=build_level_layout_key("menu-a", "Section"),
                            layout_json=[{"columns": 2, "button_keys": [alternative.button_key, target.button_key]}]))
    db.add(BotMenuDestinationBinding(id="binding", destination_value="Office", menu_type="menu-a"))
    db.add(BotMenuProfile(id="profile", menu_type="menu-a", display_name="Menu", is_active=True))
    ticket = Ticket(id="ticket", company_id="company", client_id="client", subject="Current",
                    workflow_type=workflow, task_kind="normal", status="new", is_frozen=False,
                    button_config_key="old-source-key", instruction_button_key=current.button_key)
    db.add(ticket)
    db.commit()
    sent, messages = [], []
    args = wrong_problem_args(sent, messages)
    menu = flow.get_problem_menu_for_ticket(db, ticket, get_or_create_client_profile_fn=args["get_or_create_client_profile_fn"])
    assert menu["menu_name"] == "menu-a"
    assert len(menu["rows"]) == 1
    node = menu["rows"][0]["buttons"][0]
    assert node["key"] == section.button_key and node["label"] == "Folder"
    assert [item["key"] for item in node["children"][0]["buttons"]] == [alternative.button_key, target.button_key]
    result = flow.apply_wrong_problem(db, ticket, SimpleNamespace(button_key=target.button_key), **args)
    assert result == {"ok": True, "label": "Folder → Renamed"}
    assert ticket.wrong_problem_button_key == target.button_key
    assert ticket.wrong_problem_used_at is not None
    assert ticket.instruction_button_key == current.button_key
    if workflow == "ticket":
        assert len(sent) == 1 and "Folder → Renamed" in sent[0]
        assert ticket.status == "pending" and len(messages) == 2
    else:
        assert sent == [] and "Folder → Renamed" in ticket.wrong_problem_hint
        assert ticket.status == "new" and len(messages) == 1
    with pytest.raises(HTTPException) as error:
        flow.apply_wrong_problem(db, ticket, SimpleNamespace(button_key=target.button_key), **args)
    assert error.value.detail["error"]["code"] == "wrong_problem_already_used"


@pytest.mark.parametrize("choice,code", [("section", "submenu_not_final"), ("hidden", "button_not_found"),
                                         ("foreign", "button_not_found"), ("child", "button_not_found")])
def test_wrong_problem_rejects_unavailable_buttons(db, no_txt, choice, code):
    current = button(db, "Current")
    section = button(db, "Section", workflow="submenu", kind="section")
    button(db, "Leaf", parent="Section")
    choices = {"section": section, "hidden": button(db, "Hidden", visibility_overridden=True, is_enabled=False),
               "foreign": button(db, "Foreign", menu="other"),
               "child": button(db, "Child", parent="Group", workflow="group_task_child")}
    ticket = SimpleNamespace(instruction_button_key=current.button_key, button_config_key=current.button_key,
                             status="new", task_kind="normal", wrong_problem_used_at=None)
    with pytest.raises(HTTPException) as error:
        flow.apply_wrong_problem(db, ticket, SimpleNamespace(button_key=choices[choice].button_key), **wrong_problem_args([], []))
    assert error.value.detail["error"]["code"] == code


@pytest.mark.parametrize("mode,expected", [("binding", "menu-a"), ("destination", "menu-a"),
                                           ("fallback", "fallback"), ("inactive", "fallback")])
def test_wrong_problem_resolves_db_menu_without_current_button(db, no_txt, mode, expected):
    button(db, "Target")
    db.add_all([BotMenuProfile(id="menu", menu_type="menu-a", display_name="Menu", is_active=mode != "inactive"),
                BotMenuProfile(id="fallback", menu_type="fallback", display_name="Fallback", is_active=True, is_fallback=True)])
    if mode in {"binding", "inactive"}:
        db.add(BotMenuDestinationBinding(id="binding", destination_value="Office", menu_type="menu-a"))
    db.flush()
    destination = "menu-a" if mode == "destination" else "Office"
    ticket = SimpleNamespace(instruction_button_key=None, button_config_key=None, client_id="client")
    assert flow.menu_name_for_ticket_client(db, ticket, get_or_create_client_profile_fn=lambda *args:
        SimpleNamespace(is_verified=True, destination=destination)) == expected


@pytest.mark.parametrize("status,kind,code", [("closed", "normal", "ticket_closed_readonly"),
                                             ("new", "group_child", "wrong_problem_not_allowed")])
def test_wrong_problem_keeps_ticket_restrictions(db, no_txt, status, kind, code):
    ticket = SimpleNamespace(status=status, task_kind=kind, wrong_problem_used_at=None)
    with pytest.raises(HTTPException) as error:
        flow.apply_wrong_problem(db, ticket, SimpleNamespace(button_key="any"), **wrong_problem_args([], []))
    assert error.value.detail["error"]["code"] == code


def test_wrong_problem_endpoints_use_db_without_txt(db, monkeypatch, no_txt):
    current = button(db, "Current")
    target = button(db, "Target")
    ticket = Ticket(id="ticket", company_id="company", client_id="client", subject="Current",
                    workflow_type="ticket", task_kind="normal", status="new", is_frozen=False,
                    instruction_button_key=current.button_key)
    db.add(ticket)
    db.commit()
    sent = []
    monkeypatch.setattr(tickets, "_get_ticket_or_404", lambda *args: ticket)
    monkeypatch.setattr(tickets, "_send_to_telegram_client", lambda db, ticket, text: sent.append(text) or {"result": {"message_id": 5}})
    monkeypatch.setattr(tickets, "_remove_client_close_button", lambda *args: None)
    app = FastAPI()
    app.include_router(tickets.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="agent", role="agent")
    with TestClient(app) as http:
        response = http.get("/api/v1/tickets/ticket/problem-menu")
        assert response.status_code == 200, response.text
        assert response.json()["rows"][0]["buttons"][0]["key"] == target.button_key
        response = http.post("/api/v1/tickets/ticket/wrong-problem", json={"button_key": target.button_key})
        assert response.status_code == 200, response.text
        assert response.json() == {"ok": True, "label": "Target"}
    db.expire_all()
    assert db.get(Ticket, "ticket").wrong_problem_button_key == target.button_key
    assert len(sent) == 1 and "Target" in sent[0]


def test_wrong_problem_system_ticket_uses_client_menu(db, no_txt):
    system = button(db, "First connection", menu="__system__")
    button(db, "Target")
    db.add(BotMenuDestinationBinding(id="binding", destination_value="Office", menu_type="menu-a"))
    db.flush()
    ticket = SimpleNamespace(instruction_button_key=system.button_key, button_config_key=None, client_id="client")
    assert flow.menu_name_for_ticket_client(db, ticket, get_or_create_client_profile_fn=lambda *args:
        SimpleNamespace(is_verified=True, destination="Office")) == "menu-a"


@pytest.mark.parametrize("source_key", ["legacy-txt-parent", None])
def test_assign_old_group_parent_uses_instruction_key_and_does_not_duplicate(db, monkeypatch, no_txt, source_key):
    monkeypatch.setenv("USE_DB_BUTTONS", "true")
    parent_button = button(db, "Parent", workflow="group_task", kind="group_parent")
    child_button = button(db, "Child", parent="Parent", workflow="group_task_child")
    db.add(Company(id="company", name="Company"))
    db.add(Client(id="client", company_id="company", display_name="Client"))
    agent = User(id="agent", company_id="company", role="agent", full_name="Agent", is_active=True)
    parent = Ticket(id="old-parent", company_id="company", client_id="client", subject="Parent",
                    workflow_type="task", task_kind="group_parent", intake_state="ready",
                    status="unassigned", button_config_key=source_key,
                    instruction_button_key=parent_button.button_key)
    db.add_all([agent, parent])
    db.commit()
    monkeypatch.setattr(telegram, "_menu_name_for_ticket_client", lambda *args: "menu-a")
    assert telegram.assign_ticket_to_agent(db, parent, agent, notify_client=False) is True
    db.flush()
    children = db.query(Ticket).filter_by(parent_ticket_id=parent.id).all()
    assert len(children) == 1
    child = children[0]
    assert child.instruction_button_key == child_button.button_key
    assert child.button_config_key == child_button.button_key
    assert child.assignee_id == agent.id
    assert child.workflow_type == "task" and child.task_kind == "group_child"
    telegram._on_group_parent_assigned(db, parent, agent)
    db.flush()
    assert db.query(Ticket).filter_by(parent_ticket_id=parent.id).count() == 1
    assert parent.button_config_key == source_key
    assert parent.instruction_button_key == parent_button.button_key


@pytest.mark.parametrize("old_flag", ["true", "false"])
def test_db_only_admin_menu_and_files_with_old_env(db, monkeypatch, tmp_path, no_txt, old_flag):
    env = tmp_path / "old.env"
    env.write_text(f"USE_DB_BUTTONS={old_flag}\n", encoding="utf-8")
    monkeypatch.delenv("USE_DB_BUTTONS", raising=False)
    loaded = Settings(_env_file=env)
    assert "use_db_buttons" not in type(loaded).model_fields
    monkeypatch.setattr(telegram, "settings", loaded)
    # A leftover configuration file cannot add a menu or change a DB button.
    (tmp_path / "TXT-only-menu.txt").write_text("[A] - тип Задача", encoding="utf-8")
    files = tmp_path / "files"
    files.mkdir()
    attachment = files / "user.txt"
    attachment.write_text("User attachment", encoding="utf-8")
    monkeypatch.setattr(items, "BUTTON_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(telegram, "BUTTON_CONFIG_DIR", tmp_path)
    section = button(db, "Section", workflow="submenu", kind="section", client_text="DB submenu")
    leaf = button(db, "Leaf", parent="Section", instruction_text="DB instruction", client_text="DB client",
                  system_text="DB system", files_json=["user.txt"], button_style="success")
    before = (leaf.button_key, leaf.updated_at, leaf.currently_present, leaf.instruction_text)
    app = FastAPI()
    for router in (items.router, structure.router, profiles.router):
        app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="admin", role="admin")
    with TestClient(app) as http:
        menu_types = http.get("/api/v1/settings/bot-buttons/menu-types")
        assert menu_types.status_code == 200
        assert [item["menu_name"] for item in menu_types.json()] == ["menu-a"]
        assert menu_types.json()[0]["source_file"] == "DB"
        tree = http.get("/api/v1/settings/bot-buttons/menu-types/menu-a/structure")
        assert tree.status_code == 200
        node = tree.json()["tree_rows"][0]["buttons"][0]
        assert node["technical_key"] == section.button_key
        assert node["children_rows"][0]["buttons"][0]["technical_key"] == leaf.button_key
        saved = http.get("/api/v1/settings/bot-buttons/item", params={"button_key": leaf.button_key})
        assert saved.status_code == 200
        assert saved.json()["instruction_text"] == "DB instruction"
        assert saved.json()["files_effective"] == ["user.txt"]
        assert saved.json()["button_label_from"] == "db"
        for path in ("import-from-txt", "sync-from-txt", "migrate-instructions", "db-only-readiness"):
            assert http.post(f"/api/v1/settings/bot-buttons/{path}").status_code == 404
    db.refresh(leaf)
    assert (leaf.button_key, leaf.updated_at, leaf.currently_present, leaf.instruction_text) == before
    assert items._list_available_button_files() == ["user.txt"]
    assert telegram._resolve_button_file_path(db, "user.txt") == (attachment.resolve(), "user.txt")
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda *args, **kwargs: sent.append((args, kwargs)) or {})
    telegram._send_problem_menu_with_visibility(db, "chat", menu_name="menu-a", menu_key=section.button_key)
    assert sent[0][0][1] == "DB submenu"
    assert sent[0][1]["reply_markup"]["inline_keyboard"][0][0] == {
        "text": "Leaf", "callback_data": f"btnid:{leaf.id}", "style": "success",
    }
    callback = telegram._button_from_callback(db, f"btnid:{leaf.id}", "menu-a")
    assert callback.kind == "ticket" and callback.instruction_text == "DB instruction"


def test_old_hashed_callbacks_resolve_only_db_rows(db, no_txt):
    import hashlib
    section = button(db, "Section", workflow="submenu", kind="section")
    leaf = button(db, "Leaf", parent="Section", workflow="task", label_overridden=True, label_override="New label")
    alias = hashlib.sha1(b"menu-a|Section|Leaf").hexdigest()[:12]
    menu_alias = hashlib.sha1(b"menu-a|Section|submenu").hexdigest()[:12]
    assert telegram._menu_key_from_callback(db, f"menu:{menu_alias}", "menu-a") == section.button_key
    assert telegram._button_from_callback(db, f"btn:{alias}", "menu-a").key == leaf.button_key
    assert telegram._button_from_callback(db, f"btn:{alias}", "other") is None
    leaf.is_archived = True
    db.flush()
    assert telegram._button_from_callback(db, f"btn:{alias}", "menu-a") is None


def test_txt_reload_route_is_unregistered():
    from api.app.main import app
    assert "/api/v1/button-config/reload" not in {route.path for route in app.routes}
