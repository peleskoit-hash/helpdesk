from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import BotButtonSetting
from api.app.routers import settings_bot_button_items
from api.app.routers import telegram as telegram_router
from api.app.services import bot_button_materialization as materialization
from api.app.services import telegram_automation
from api.app.services.button_config import ButtonConfig, ButtonSpec, MenuSpec, TerminalButtonRef


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def _seed_row(session, **overrides):
    now = datetime.utcnow()
    payload = {
        "id": str(uuid.uuid4()),
        "stable_button_id": str(uuid.uuid4()),
        "button_key": "menu-a|ticket|A",
        "legacy_button_key": None,
        "menu_type": "menu-a",
        "workflow_type": "ticket",
        "button_path": "A",
        "button_label": "A",
        "currently_present": True,
        "is_db_created": False,
        "button_kind": "terminal",
        "parent_path": "",
        "is_archived": False,
        "created_at": now,
        "updated_at": now,
    }
    payload.update(overrides)
    row = BotButtonSetting(**payload)
    session.add(row)
    session.commit()
    return row


def _ticket(**overrides):
    payload = {
        "instruction_button_key": "menu-a|ticket|A",
        "button_config_key": None,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _button(**overrides):
    payload = {
        "key": "txt-a",
        "label": "A",
        "kind": "ticket",
        "text": "TXT client text",
        "system_text": "TXT system text",
        "automation_key": "bitrix_deal_phone_lookup",
        "file_path": "folder/file.txt",
        "file_rules": (
            {"field": "employee_type", "equals": "Стажёр", "file": "cond/one.txt"},
        ),
        "confirmation_text": "TXT confirmation",
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _fail_fallback(*args, **kwargs):
    raise AssertionError("TXT fallback should not be used")


def _txt_submenu_config():
    child = ButtonSpec(key="child-a", label="Leaf A", kind="ticket", text="TXT child text")
    submenu = ButtonSpec(key="submenu-a", label="Submenu", kind="submenu", submenu_key="submenu-key")
    config = ButtonConfig(
        menus={
            "root": MenuSpec(key="root", title="menu-a", rows=((submenu.key,),)),
            "submenu-key": MenuSpec(key="submenu-key", title="Submenu", text="TXT submenu text", rows=((child.key,),)),
        },
        buttons={submenu.key: submenu, child.key: child},
        root_menu_key="root",
    )
    refs = (
        TerminalButtonRef(
            source_button_key=child.key,
            label=child.label,
            workflow_type="ticket",
            full_path="Submenu/Leaf A",
            technical_key="menu-a|ticket|Submenu/Leaf A",
            instruction_text=None,
        ),
    )
    return config, refs


def test_db_client_text_is_used_instead_of_txt():
    session = _session()
    _seed_row(session, client_text="DB client text")

    result = telegram_router._resolve_effective_client_text(session, _ticket(), _button())

    assert result == "DB client text"


def test_empty_db_client_text_does_not_use_txt():
    session = _session()
    _seed_row(session, client_text="")

    result = telegram_router._resolve_effective_client_text(session, _ticket(), _button())

    assert result is None


def test_missing_db_client_text_does_not_use_txt():
    session = _session()

    result = telegram_router._resolve_effective_client_text(session, _ticket(), _button())

    assert result is None


def test_empty_db_automation_key_disables_automation():
    session = _session()
    _seed_row(session, automation_key="", automation_overridden=False)

    result = telegram_automation.resolve_effective_automation_key(
        session,
        _ticket(),
        _button(),
        button_setting_for_ticket_fn=telegram_router._button_setting_for_ticket,
    )

    assert result is None


def test_empty_db_files_do_not_use_txt_files():
    session = _session()
    _seed_row(session, files_json=[])

    result = telegram_router._resolve_effective_direct_files(session, _ticket(), _button())

    assert result == []


def test_empty_db_conditional_files_do_not_use_txt_rules():
    session = _session()
    _seed_row(session, conditional_files_json=[])

    result = telegram_router._resolve_effective_conditional_rules(session, _ticket(), _button())

    assert result == []


def test_empty_db_confirmation_disables_prompt():
    session = _session()
    _seed_row(session, confirmation_enabled=False, confirmation_text="")

    enabled, text = telegram_router._resolve_effective_confirmation(_ticket(), _button(), session)

    assert enabled is False
    assert text is None


def test_editor_payload_returns_db_only_values(monkeypatch):
    session = _session()
    _seed_row(
        session,
        client_text=None,
        system_text=None,
        automation_key=None,
        files_json=None,
        conditional_files_json=None,
        confirmation_enabled=False,
        confirmation_text=None,
    )

    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "_fallback_client_text", _fail_fallback)
    monkeypatch.setattr(settings_bot_button_items, "_fallback_system_text", _fail_fallback)
    monkeypatch.setattr(settings_bot_button_items, "_fallback_automation_key", _fail_fallback)
    monkeypatch.setattr(settings_bot_button_items, "_fallback_direct_files", _fail_fallback)
    monkeypatch.setattr(settings_bot_button_items, "_fallback_conditional_files", _fail_fallback)
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: {})

    payload = settings_bot_button_items.get_bot_button_item(
        "menu-a|ticket|A",
        menu_type="menu-a",
        db=session,
        user=object(),
    )

    assert payload["client_text_effective"] is None
    assert payload["system_text_effective"] is None
    assert payload["automation_key_effective"] is None
    assert payload["files_effective"] == []
    assert payload["conditional_files_effective"] == []
    assert payload["client_text_from"] == "db"
    assert payload["files_from"] == "db"
    assert payload["conditional_files_from"] == "db"


def test_editor_payload_without_row_stays_empty(monkeypatch):
    session = _session()

    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "_fallback_client_text", _fail_fallback)
    monkeypatch.setattr(settings_bot_button_items, "_fallback_system_text", _fail_fallback)
    monkeypatch.setattr(settings_bot_button_items, "_fallback_automation_key", _fail_fallback)
    monkeypatch.setattr(settings_bot_button_items, "_fallback_direct_files", _fail_fallback)
    monkeypatch.setattr(settings_bot_button_items, "_fallback_conditional_files", _fail_fallback)
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: {})

    payload = settings_bot_button_items.get_bot_button_item(
        "menu-a|ticket|A",
        menu_type="menu-a",
        db=session,
        user=object(),
    )

    assert payload["client_text_effective"] is None
    assert payload["system_text_effective"] is None
    assert payload["automation_key_effective"] is None
    assert payload["files_effective"] == []
    assert payload["conditional_files_effective"] == []


def test_txt_structure_and_group_task_materialization_still_load(monkeypatch):
    child = ButtonSpec(key="child-a", label="Child", kind="task", text="Child text")
    parent = ButtonSpec(key="parent-a", label="Parent", kind="group_task", subtasks=(child,))
    config = ButtonConfig(
        menus={"root": MenuSpec(key="root", title="menu-a", rows=((parent.key,),))},
        buttons={parent.key: parent},
        root_menu_key="root",
    )
    refs = (
        TerminalButtonRef(
            source_button_key="missing-child-key",
            label="Child",
            workflow_type="group_task_child",
            full_path="Parent/Child",
            technical_key="menu-a|group_task_child|Parent/Child",
            instruction_text=None,
        ),
    )
    monkeypatch.setattr(materialization, "available_menu_names", lambda: ("menu-a",))
    monkeypatch.setattr(materialization, "load_button_config", lambda menu_name: config)
    monkeypatch.setattr(materialization, "list_terminal_buttons", lambda menu_name: refs)

    sources, warnings = materialization.collect_materialization_sources()

    assert len(sources) == 2
    assert warnings == []
    assert any(source.workflow_type == "group_task_child" for source in sources)


def test_txt_submenu_runtime_uses_db_text_instead_of_txt(monkeypatch):
    session = _session()
    _seed_row(
        session,
        button_key="menu-a|submenu|Submenu",
        workflow_type="submenu",
        button_kind="section",
        button_path="Submenu",
        button_label="Submenu",
        client_text="DB submenu text",
        parent_path="",
    )
    config, refs = _txt_submenu_config()
    sent = {}

    monkeypatch.setattr(telegram_router, "_use_db_buttons", lambda: False)
    monkeypatch.setattr(telegram_router, "load_button_config", lambda menu_name: config)
    monkeypatch.setattr(telegram_router, "find_menu", lambda menu_key: ("menu-a", config.menus["submenu-key"]) if menu_key == "submenu-key" else None)
    monkeypatch.setattr(telegram_router, "list_terminal_buttons", lambda menu_name: refs)
    monkeypatch.setattr(telegram_router, "_button_settings_map", lambda db, menu_name: {
        "menu-a|submenu|Submenu": db.query(BotButtonSetting).filter(BotButtonSetting.button_key == "menu-a|submenu|Submenu").one()
    })
    monkeypatch.setattr(telegram_router, "_submenu_path_by_key_or_db", lambda db, menu_name, menu_key: "Submenu")
    monkeypatch.setattr(telegram_router, "send_message", lambda chat_id, text, reply_markup=None, **kwargs: sent.update({"chat_id": chat_id, "text": text, "reply_markup": reply_markup}) or sent)

    telegram_router._send_problem_menu_with_visibility(session, "chat-1", menu_name="menu-a", menu_key="submenu-key")

    assert sent["text"] == "DB submenu text"
    assert sent["reply_markup"]["inline_keyboard"][0][0]["text"] == "Leaf A"
    assert sent["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "btn:child-a"


def test_txt_submenu_runtime_empty_db_text_does_not_fallback_to_txt(monkeypatch):
    session = _session()
    _seed_row(
        session,
        button_key="menu-a|submenu|Submenu",
        workflow_type="submenu",
        button_kind="section",
        button_path="Submenu",
        button_label="Submenu",
        client_text="",
        parent_path="",
    )
    config, refs = _txt_submenu_config()
    sent = {}

    monkeypatch.setattr(telegram_router, "_use_db_buttons", lambda: False)
    monkeypatch.setattr(telegram_router, "load_button_config", lambda menu_name: config)
    monkeypatch.setattr(telegram_router, "find_menu", lambda menu_key: ("menu-a", config.menus["submenu-key"]) if menu_key == "submenu-key" else None)
    monkeypatch.setattr(telegram_router, "list_terminal_buttons", lambda menu_name: refs)
    monkeypatch.setattr(telegram_router, "_button_settings_map", lambda db, menu_name: {
        "menu-a|submenu|Submenu": db.query(BotButtonSetting).filter(BotButtonSetting.button_key == "menu-a|submenu|Submenu").one()
    })
    monkeypatch.setattr(telegram_router, "_submenu_path_by_key_or_db", lambda db, menu_name, menu_key: "Submenu")
    monkeypatch.setattr(telegram_router, "send_message", lambda chat_id, text, reply_markup=None, **kwargs: sent.update({"text": text, "reply_markup": reply_markup}) or sent)

    telegram_router._send_problem_menu_with_visibility(session, "chat-1", menu_name="menu-a", menu_key="submenu-key")

    assert sent["text"] == "Выберите тип проблемы"


def test_txt_submenu_runtime_without_db_row_does_not_fallback_to_txt(monkeypatch):
    session = _session()
    config, refs = _txt_submenu_config()
    sent = {}

    monkeypatch.setattr(telegram_router, "_use_db_buttons", lambda: False)
    monkeypatch.setattr(telegram_router, "load_button_config", lambda menu_name: config)
    monkeypatch.setattr(telegram_router, "find_menu", lambda menu_key: ("menu-a", config.menus["submenu-key"]) if menu_key == "submenu-key" else None)
    monkeypatch.setattr(telegram_router, "list_terminal_buttons", lambda menu_name: refs)
    monkeypatch.setattr(telegram_router, "_button_settings_map", lambda db, menu_name: {})
    monkeypatch.setattr(telegram_router, "_submenu_path_by_key_or_db", lambda db, menu_name, menu_key: "Submenu")
    monkeypatch.setattr(telegram_router, "send_message", lambda chat_id, text, reply_markup=None, **kwargs: sent.update({"text": text, "reply_markup": reply_markup}) or sent)

    telegram_router._send_problem_menu_with_visibility(session, "chat-1", menu_name="menu-a", menu_key="submenu-key")

    assert sent["text"] == "Выберите тип проблемы"
    assert sent["reply_markup"]["inline_keyboard"][0][0]["text"] == "Leaf A"


def test_db_created_submenu_runtime_uses_db_text(monkeypatch):
    session = _session()
    section = _seed_row(
        session,
        button_key="menu-a|db|Custom submenu",
        workflow_type="submenu",
        button_kind="section",
        button_path="Custom submenu",
        button_label="Custom submenu",
        client_text="DB created submenu text",
        parent_path="",
    )
    child = _seed_row(
        session,
        button_key="menu-a|db|Custom submenu/Leaf",
        workflow_type="ticket",
        button_kind="terminal",
        button_path="Custom submenu/Leaf",
        button_label="Leaf",
        parent_path="Custom submenu",
    )
    sent = {}

    monkeypatch.setattr(telegram_router, "send_message", lambda chat_id, text, reply_markup=None, **kwargs: sent.update({"text": text, "reply_markup": reply_markup}) or sent)

    telegram_router._send_problem_menu_db_only(session, "chat-1", menu_name="menu-a", menu_key=section.button_key)

    assert sent["text"] == "DB created submenu text"
    assert sent["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"btnid:{child.id}"


def test_group_parent_does_not_use_submenu_client_text():
    session = _session()
    row = _seed_row(
        session,
        button_key="menu-a|group_task|Parent",
        workflow_type="group_task",
        button_kind="group_parent",
        button_path="Parent",
        button_label="Parent",
        client_text="Should not be used as submenu text",
    )

    result = telegram_router._resolve_submenu_client_text(session, "menu-a", menu_key=row.button_key, parent_path="Parent")

    assert result is None
