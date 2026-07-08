from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import BotButtonSetting, MessageType, Ticket, TicketMessage, TicketStatus, TicketWorkflowType
from api.app.routers import settings_bot_button_items
from api.app.routers import settings_bot_menu_profiles
from api.app.routers import settings_bot_button_structure
from api.app.routers import telegram as telegram_router
from api.app.schemas import BotButtonInstructionUpdate
from api.app.services import bot_button_materialization as materialization
from api.app.services import button_automation_types
from api.app.services import telegram_automation
from api.app.services.system_tickets import FIRST_CONNECTION_BUTTON_KEY, FIRST_CONNECTION_TITLE, SYSTEM_MENU_TYPE
from api.app.services.ticket_lifecycle import close_ticket_by_actor
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
        "id": "ticket-1",
        "workflow_type": "ticket",
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


def _save_button_instruction(session, payload, *, button_key="menu-a|ticket|A", workflow_type="ticket"):
    return settings_bot_button_items.save_bot_button_instruction(
        BotButtonInstructionUpdate(**payload),
        button_key=button_key,
        menu_type="menu-a",
        workflow_type=workflow_type,
        button_path="A",
        button_label="A",
        db=session,
        user=object(),
    )


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


def _nested_txt_submenu_config():
    leaf_a = ButtonSpec(key="child-a", label="Leaf A", kind="ticket", text="TXT child text")
    leaf_b = ButtonSpec(key="child-b", label="Leaf B", kind="task", text="TXT child text B")
    nested = ButtonSpec(key="submenu-b", label="Nested", kind="submenu", submenu_key="submenu-key-b")
    submenu = ButtonSpec(key="submenu-a", label="Submenu", kind="submenu", submenu_key="submenu-key-a")
    config = ButtonConfig(
        menus={
            "root": MenuSpec(key="root", title="menu-a", rows=((submenu.key,),)),
            "submenu-key-a": MenuSpec(key="submenu-key-a", title="Submenu", text="TXT submenu text", rows=((leaf_a.key,), (nested.key,))),
            "submenu-key-b": MenuSpec(key="submenu-key-b", title="Nested", text="TXT nested text", rows=((leaf_b.key,),)),
        },
        buttons={submenu.key: submenu, nested.key: nested, leaf_a.key: leaf_a, leaf_b.key: leaf_b},
        root_menu_key="root",
    )
    refs = (
        TerminalButtonRef(
            source_button_key=leaf_a.key,
            label=leaf_a.label,
            workflow_type="ticket",
            full_path="Submenu/Leaf A",
            technical_key="menu-a|ticket|Submenu/Leaf A",
            instruction_text=None,
        ),
        TerminalButtonRef(
            source_button_key=leaf_b.key,
            label=leaf_b.label,
            workflow_type="task",
            full_path="Submenu/Nested/Leaf B",
            technical_key="menu-a|task|Submenu/Nested/Leaf B",
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


def test_get_bot_button_item_returns_canonical_automation_type(monkeypatch):
    session = _session()
    _seed_row(session, automation_key="send_files")

    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: telegram_automation.get_automation_registry())

    payload = settings_bot_button_items.get_bot_button_item(
        "menu-a|ticket|A",
        menu_type="menu-a",
        db=session,
        user=object(),
    )

    assert payload["automation_type"] == "send_files"
    assert payload["automation_key"] == "send_files"


def test_null_automation_reads_as_none():
    assert button_automation_types.automation_type_from_key(None) == "none"
    assert button_automation_types.automation_type_from_key("") == "none"


def test_save_automation_type_none_persists_null(monkeypatch):
    session = _session()
    _seed_row(session, automation_key="bitrix_deal_phone_lookup", automation_overridden=True)

    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: telegram_automation.get_automation_registry())

    payload = _save_button_instruction(
        session,
        {
            "automation_type": "none",
            "automation_overridden": True,
        },
    )

    row = session.query(BotButtonSetting).filter(BotButtonSetting.button_key == "menu-a|ticket|A").one()
    assert payload["automation_type"] == "none"
    assert row.automation_key is None


def test_conflicting_automation_type_and_legacy_key_is_rejected(monkeypatch):
    session = _session()
    _seed_row(session)

    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)

    try:
        _save_button_instruction(
            session,
            {
                "automation_type": "send_files",
                "automation_key": "bitrix_deal_phone_lookup",
                "automation_overridden": True,
            },
        )
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 422
        assert "automation_type и automation_key конфликтуют" in str(getattr(exc, "detail", ""))
    else:
        raise AssertionError("Expected conflict error")


def test_ticket_plus_bitrix_is_rejected(monkeypatch):
    session = _session()
    _seed_row(session)

    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: telegram_automation.get_automation_registry())

    try:
        _save_button_instruction(
            session,
            {
                "automation_type": "bitrix_deal_phone_lookup",
                "automation_overridden": True,
            },
        )
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 422
        assert "недоступна для выбранного типа кнопки" in str(getattr(exc, "detail", ""))
    else:
        raise AssertionError("Expected workflow validation error")


def test_ticket_plus_message_only_is_rejected(monkeypatch):
    session = _session()
    _seed_row(session)

    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: telegram_automation.get_automation_registry())

    try:
        _save_button_instruction(
            session,
            {
                "automation_type": "message_only",
                "automation_overridden": True,
            },
        )
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 422
        assert "недоступна для выбранного типа кнопки" in str(getattr(exc, "detail", ""))
    else:
        raise AssertionError("Expected workflow validation error")


def test_task_plus_message_only_is_rejected(monkeypatch):
    session = _session()
    _seed_row(session, button_key="menu-a|task|A", workflow_type="task")

    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: telegram_automation.get_automation_registry())

    try:
        _save_button_instruction(
            session,
            {
                "automation_type": "message_only",
                "automation_overridden": True,
            },
            button_key="menu-a|task|A",
            workflow_type="task",
        )
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 422
        assert "недоступна для выбранного типа кнопки" in str(getattr(exc, "detail", ""))
    else:
        raise AssertionError("Expected workflow validation error")


def test_task_plus_bitrix_is_allowed(monkeypatch):
    session = _session()
    _seed_row(session, button_key="menu-a|task|A", workflow_type="task")

    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: telegram_automation.get_automation_registry())

    payload = _save_button_instruction(
        session,
        {
            "automation_type": "bitrix_deal_phone_lookup",
            "automation_overridden": True,
        },
        button_key="menu-a|task|A",
        workflow_type="task",
    )

    assert payload["automation_type"] == "bitrix_deal_phone_lookup"


def test_auto_allowed_types_and_transitional_null(monkeypatch):
    session = _session()
    _seed_row(session, button_key="menu-a|auto|A", workflow_type="auto")

    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: telegram_automation.get_automation_registry())

    for automation_type in ("send_files", "message_only", "none"):
        payload = _save_button_instruction(
            session,
            {
                "automation_type": automation_type,
                "automation_overridden": True,
            },
            button_key="menu-a|auto|A",
            workflow_type="auto",
        )
        assert payload["automation_type"] == automation_type


def test_auto_plus_bitrix_is_rejected_with_workflow_error_code(monkeypatch):
    session = _session()
    _seed_row(session, button_key="menu-a|auto|A", workflow_type="auto")

    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: telegram_automation.get_automation_registry())

    try:
        _save_button_instruction(
            session,
            {
                "automation_type": "bitrix_deal_phone_lookup",
                "automation_overridden": True,
            },
            button_key="menu-a|auto|A",
            workflow_type="auto",
        )
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 422
        detail = getattr(exc, "detail", {}) or {}
        error = detail.get("error", {}) if isinstance(detail, dict) else {}
        assert error.get("code") == "automation_not_allowed_for_workflow"
    else:
        raise AssertionError("Expected workflow validation error")


def test_task_switch_from_bitrix_to_send_files_persists_first_files_and_rule(monkeypatch):
    session = _session()
    _seed_row(
        session,
        button_key="menu-a|task|A",
        workflow_type="task",
        automation_key="bitrix_deal_phone_lookup",
        automation_overridden=True,
        files_overridden=True,
        files_json=[],
        conditional_files_overridden=True,
        conditional_files_json=[],
    )

    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: telegram_automation.get_automation_registry())
    monkeypatch.setattr(
        settings_bot_button_items,
        "_list_available_button_files",
        lambda: ["docs/a.pdf", "docs/b.pdf"],
    )
    monkeypatch.setattr(
        settings_bot_button_items,
        "_build_conditional_field_catalog",
        lambda db, menu_type=None: {
            "location_type": {
                "type": "select",
                "label": "Тип локации",
                "values": ["Офисный", "Удалённый"],
            }
        },
    )

    payload = _save_button_instruction(
        session,
        {
            "automation_type": "send_files",
            "automation_overridden": True,
            "files_overridden": True,
            "files": ["docs/a.pdf"],
            "conditional_files_overridden": True,
            "conditional_files": [
                {
                    "conditions": [{"field": "location_type", "operator": "eq", "value": "Удалённый"}],
                    "files": ["docs/b.pdf"],
                }
            ],
        },
        button_key="menu-a|task|A",
        workflow_type="task",
    )

    assert payload["automation_type"] == "send_files"
    assert payload["files_overridden"] is True
    assert payload["files"] == ["docs/a.pdf"]
    assert payload["conditional_files_overridden"] is True
    assert payload["conditional_files"][0]["files"] == ["docs/b.pdf"]

    item = settings_bot_button_items.get_bot_button_item(
        "menu-a|task|A",
        menu_type="menu-a",
        db=session,
        user=object(),
    )
    assert item["automation_type"] == "send_files"
    assert item["files_overridden"] is True
    assert item["files"] == ["docs/a.pdf"]
    assert item["conditional_files_overridden"] is True
    assert item["conditional_files"] == [
        {
            "conditions": [{"field": "location_type", "operator": "eq", "value": "Удалённый"}],
            "files": ["docs/b.pdf"],
        }
    ]


def test_legacy_conditional_files_are_returned_by_item_api_without_automation_key(monkeypatch):
    session = _session()
    _seed_row(
        session,
        button_key="menu-a|auto|memo",
        workflow_type="auto",
        automation_key=None,
        automation_overridden=False,
        files_overridden=True,
        files_json=[],
        conditional_files_overridden=True,
        conditional_files_json=[
            {
                "conditions": [{"field": "location_type", "operator": "eq", "value": "Офисный"}],
                "files": ["office-guide.pdf"],
            },
            {
                "conditions": [{"field": "location_type", "operator": "eq", "value": "Удалённый"}],
                "files": ["remote-guide.pdf"],
            },
        ],
    )

    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: telegram_automation.get_automation_registry())

    item = settings_bot_button_items.get_bot_button_item(
        "menu-a|auto|memo",
        menu_type="menu-a",
        db=session,
        user=object(),
    )

    assert item["automation_type"] == "send_files"
    assert item["automation_key"] is None
    assert item["automation_from"] == "legacy_files"
    assert item["automation_title"] == "Отправка файлов"
    assert item["automation_description"]
    assert item["automation_available"] is True
    assert item["automation_status"] == "available"
    assert item["files_overridden"] is True
    assert item["files"] == []
    assert item["conditional_files_overridden"] is True
    assert item["conditional_files"] == [
        {
            "conditions": [{"field": "location_type", "operator": "eq", "value": "Офисный"}],
            "files": ["office-guide.pdf"],
        },
        {
            "conditions": [{"field": "location_type", "operator": "eq", "value": "Удалённый"}],
            "files": ["remote-guide.pdf"],
        },
    ]


def test_empty_auto_button_without_files_stays_none(monkeypatch):
    session = _session()
    _seed_row(
        session,
        button_key="menu-a|auto|empty",
        workflow_type="auto",
        automation_key=None,
        automation_overridden=False,
        files_overridden=True,
        files_json=[],
        conditional_files_overridden=True,
        conditional_files_json=[],
    )

    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: telegram_automation.get_automation_registry())

    item = settings_bot_button_items.get_bot_button_item(
        "menu-a|auto|empty",
        menu_type="menu-a",
        db=session,
        user=object(),
    )

    assert item["automation_type"] == "none"
    assert item["automation_key"] is None
    assert item["automation_from"] == "none"
    assert item["automation_title"] == "Не использовать"
    assert item["automation_description"]
    assert item["automation_available"] is True
    assert item["automation_status"] == "available"


def test_task_switching_automation_does_not_clear_existing_file_settings(monkeypatch):
    session = _session()
    _seed_row(
        session,
        button_key="menu-a|task|A",
        workflow_type="task",
        automation_key="send_files",
        automation_overridden=True,
        files_overridden=True,
        files_json=["docs/a.pdf"],
        conditional_files_overridden=True,
        conditional_files_json=[
            {
                "conditions": [{"field": "location_type", "operator": "eq", "value": "Офисный"}],
                "files": ["docs/b.pdf"],
            }
        ],
    )

    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: telegram_automation.get_automation_registry())
    monkeypatch.setattr(
        settings_bot_button_items,
        "_list_available_button_files",
        lambda: ["docs/a.pdf", "docs/b.pdf"],
    )
    monkeypatch.setattr(
        settings_bot_button_items,
        "_build_conditional_field_catalog",
        lambda db, menu_type=None: {
            "location_type": {
                "type": "select",
                "label": "Тип локации",
                "values": ["Офисный", "Удалённый"],
            }
        },
    )

    payload = _save_button_instruction(
        session,
        {
            "automation_type": "bitrix_deal_phone_lookup",
            "automation_overridden": True,
            "files_overridden": True,
            "files": ["docs/a.pdf"],
            "conditional_files_overridden": True,
            "conditional_files": [
                {
                    "conditions": [{"field": "location_type", "operator": "eq", "value": "Офисный"}],
                    "files": ["docs/b.pdf"],
                }
            ],
        },
        button_key="menu-a|task|A",
        workflow_type="task",
    )

    assert payload["automation_type"] == "bitrix_deal_phone_lookup"
    assert payload["files"] == ["docs/a.pdf"]
    assert payload["conditional_files"][0]["files"] == ["docs/b.pdf"]

    item = settings_bot_button_items.get_bot_button_item(
        "menu-a|task|A",
        menu_type="menu-a",
        db=session,
        user=object(),
    )
    assert item["automation_type"] == "bitrix_deal_phone_lookup"
    assert item["files"] == ["docs/a.pdf"]
    assert item["conditional_files"] == [
        {
            "conditions": [{"field": "location_type", "operator": "eq", "value": "Офисный"}],
            "files": ["docs/b.pdf"],
        }
    ]


def test_available_automations_do_not_expose_forward_to_telegram_group(monkeypatch):
    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: telegram_automation.get_automation_registry())

    payload = settings_bot_button_items.get_bot_button_automations(user=object())

    assert "forward_to_telegram_group" not in [item["key"] for item in payload["items"]]


def test_forward_to_telegram_group_is_rejected_on_save(monkeypatch):
    session = _session()
    _seed_row(session, button_key="menu-a|auto|A", workflow_type="auto")

    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: telegram_automation.get_automation_registry())

    try:
        _save_button_instruction(
            session,
            {
                "automation_type": "forward_to_telegram_group",
                "automation_overridden": True,
            },
            button_key="menu-a|auto|A",
            workflow_type="auto",
        )
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 422
        assert "automation_not_found" in str(getattr(exc, "detail", ""))
    else:
        raise AssertionError("Expected unknown automation rejection")


def test_ticket_send_files_dispatch_keeps_ticket_open(monkeypatch):
    session = _session()
    _seed_row(session, automation_key="send_files")
    ticket = _ticket(workflow_type="ticket")
    calls = []

    monkeypatch.setattr(telegram_router, "_send_button_direct_file", lambda *args, **kwargs: calls.append("direct") or (True, None))
    monkeypatch.setattr(telegram_router, "_send_button_rule_files", lambda *args, **kwargs: calls.append("rules"))

    result = telegram_router._dispatch_builtin_button_automation(
        session,
        ticket,
        _button(kind="ticket"),
        "chat-1",
        True,
    )

    assert result["builtin_automation"] == "send_files"
    assert result["workflow_type"] == "ticket"
    assert calls == ["direct", "rules"]
    assert ticket.workflow_type == "ticket"


def test_task_send_files_direct_error_does_not_close_task(monkeypatch):
    session = _session()
    _seed_row(session, button_key="menu-a|task|A", workflow_type="task", automation_key="send_files")
    ticket = _ticket(workflow_type="task", instruction_button_key="menu-a|task|A")
    calls = []

    monkeypatch.setattr(telegram_router, "_send_button_direct_file", lambda *args, **kwargs: calls.append("direct") or (False, "file error"))
    monkeypatch.setattr(telegram_router, "_send_button_rule_files", lambda *args, **kwargs: calls.append("rules"))

    result = telegram_router._dispatch_builtin_button_automation(
        session,
        ticket,
        _button(kind="task"),
        "chat-1",
        True,
    )

    assert result["builtin_automation"] == "send_files"
    assert result["workflow_type"] == "task"
    assert calls == ["direct", "rules"]
    assert ticket.workflow_type == "task"


def test_auto_send_files_dispatch_sends_text_once_and_completes(monkeypatch):
    session = _session()
    _seed_row(session, button_key="menu-a|auto|A", workflow_type="auto", automation_key="send_files")
    ticket = _ticket(workflow_type="auto", instruction_button_key="menu-a|auto|A")
    sent = []
    completed = []

    monkeypatch.setattr(telegram_router, "_render_client_text_for_telegram", lambda *args, **kwargs: ("done text", "HTML"))
    monkeypatch.setattr(telegram_router, "send_message", lambda chat_id, text, parse_mode=None, **kwargs: sent.append((chat_id, text, parse_mode)))
    monkeypatch.setattr(telegram_router, "_send_button_direct_file", lambda *args, **kwargs: (True, None))
    monkeypatch.setattr(telegram_router, "_send_button_rule_files", lambda *args, **kwargs: sent.append(("rules", None, None)))
    monkeypatch.setattr(telegram_router, "_complete_auto_ticket", lambda *args, **kwargs: completed.append("done"))

    result = telegram_router._dispatch_builtin_button_automation(
        session,
        ticket,
        _button(kind="auto"),
        "chat-1",
        True,
        default_client_text="fallback",
    )

    assert result["builtin_automation"] == "send_files"
    assert result["workflow_type"] == "auto"
    assert sent[0] == ("chat-1", "done text", "HTML")
    assert completed == ["done"]


def test_auto_send_files_direct_error_handoffs(monkeypatch):
    session = _session()
    _seed_row(session, button_key="menu-a|auto|A", workflow_type="auto", automation_key="send_files")
    ticket = _ticket(workflow_type="auto", instruction_button_key="menu-a|auto|A")
    fallback_calls = []

    monkeypatch.setattr(telegram_router, "_render_client_text_for_telegram", lambda *args, **kwargs: ("done text", None))
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(telegram_router, "_send_button_direct_file", lambda *args, **kwargs: (False, "file error"))
    monkeypatch.setattr(telegram_router, "_send_button_rule_files", lambda *args, **kwargs: None)
    monkeypatch.setattr(telegram_router, "_fallback_auto_to_support", lambda *args, **kwargs: fallback_calls.append((args, kwargs)) or None)
    monkeypatch.setattr(telegram_router, "_complete_auto_ticket", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not complete")))

    result = telegram_router._dispatch_builtin_button_automation(
        session,
        ticket,
        _button(kind="auto"),
        "chat-1",
        True,
    )

    assert result["auto_file_error"] is True
    assert result["workflow_type"] == "ticket"
    assert len(fallback_calls) == 1


def test_auto_message_only_uses_default_text_and_completes(monkeypatch):
    session = _session()
    _seed_row(session, button_key="menu-a|auto|A", workflow_type="auto", automation_key="message_only")
    ticket = _ticket(workflow_type="auto", instruction_button_key="menu-a|auto|A")
    sent = []
    completed = []

    monkeypatch.setattr(telegram_router, "_render_client_text_for_telegram", lambda *args, **kwargs: ("fallback", None))
    monkeypatch.setattr(telegram_router, "send_message", lambda chat_id, text, parse_mode=None, **kwargs: sent.append((chat_id, text, parse_mode)))
    monkeypatch.setattr(telegram_router, "_complete_auto_ticket", lambda *args, **kwargs: completed.append("done"))

    result = telegram_router._dispatch_builtin_button_automation(
        session,
        ticket,
        _button(kind="auto"),
        "chat-1",
        True,
        default_client_text="fallback",
    )

    assert result["builtin_automation"] == "message_only"
    assert result["workflow_type"] == "auto"
    assert sent == [("chat-1", "fallback", None)]
    assert completed == ["done"]


def test_auto_message_only_send_failure_handoffs(monkeypatch):
    session = _session()
    _seed_row(session, button_key="menu-a|auto|A", workflow_type="auto", automation_key="message_only")
    ticket = _ticket(workflow_type="auto", instruction_button_key="menu-a|auto|A")
    fallback_calls = []

    monkeypatch.setattr(telegram_router, "_render_client_text_for_telegram", lambda *args, **kwargs: ("fallback", None))
    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(telegram_router, "_fallback_auto_to_support", lambda *args, **kwargs: fallback_calls.append((args, kwargs)) or None)
    monkeypatch.setattr(telegram_router, "_complete_auto_ticket", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not complete")))

    result = telegram_router._dispatch_builtin_button_automation(
        session,
        ticket,
        _button(kind="auto"),
        "chat-1",
        True,
        default_client_text="fallback",
    )

    assert result["automation_handoff"] is True
    assert result["workflow_type"] == "ticket"
    assert len(fallback_calls) == 1


def test_builtin_send_files_does_not_trigger_unknown_registry_handler():
    session = _session()
    _seed_row(session, workflow_type="task", automation_key="send_files")
    messages = []

    result = telegram_automation.process_collecting_ticket_automation(
        db=session,
        ticket=_ticket(),
        button=_button(kind="task"),
        client_body="hello",
        telegram_message_id=None,
        attachment_data=None,
        attachment_too_large=False,
        external_chat_id="chat-1",
        notify_client=True,
        append_system_message_fn=lambda *args, **kwargs: messages.append((args, kwargs)),
        append_client_message_fn=lambda *args, **kwargs: None,
        store_telegram_attachment_fn=lambda *args, **kwargs: None,
        remove_client_close_button_fn=lambda *args, **kwargs: None,
        send_message_fn=lambda *args, **kwargs: None,
        too_large_attachment_system_text_fn=lambda *_: "",
        send_too_large_attachment_client_messages_fn=lambda *args, **kwargs: None,
        button_setting_for_ticket_fn=telegram_router._button_setting_for_ticket,
        append_button_system_text_fn=lambda *args, **kwargs: None,
        ready_ticket_for_distribution_fn=lambda *args, **kwargs: None,
    )

    assert result is None
    assert messages == []


def test_task_message_only_reports_unsupported_automation():
    session = _session()
    _seed_row(session, workflow_type="task", automation_key="message_only")
    messages = []

    result = telegram_automation.process_collecting_ticket_automation(
        db=session,
        ticket=_ticket(),
        button=_button(kind="task"),
        client_body="hello",
        telegram_message_id=None,
        attachment_data=None,
        attachment_too_large=False,
        external_chat_id="chat-1",
        notify_client=True,
        append_system_message_fn=lambda *args, **kwargs: messages.append((args, kwargs)),
        append_client_message_fn=lambda *args, **kwargs: None,
        store_telegram_attachment_fn=lambda *args, **kwargs: None,
        remove_client_close_button_fn=lambda *args, **kwargs: None,
        send_message_fn=lambda *args, **kwargs: None,
        too_large_attachment_system_text_fn=lambda *_: "",
        send_too_large_attachment_client_messages_fn=lambda *args, **kwargs: None,
        button_setting_for_ticket_fn=telegram_router._button_setting_for_ticket,
        append_button_system_text_fn=lambda *args, **kwargs: None,
        ready_ticket_for_distribution_fn=lambda *args, **kwargs: None,
    )

    assert result is None
    assert len(messages) == 1


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


def test_submenu_save_creates_section_row_and_persists_db_text(monkeypatch):
    session = _session()
    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "_list_available_button_files", lambda: [])
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: {})

    settings_bot_button_items.save_bot_button_instruction(
        BotButtonInstructionUpdate(
            client_text="Текст подменю",
            client_text_overridden=True,
            label_override="Новый раздел",
            label_overridden=True,
            is_enabled=False,
            visibility_overridden=True,
        ),
        button_key="menu-a|submenu|Submenu",
        menu_type="menu-a",
        workflow_type="submenu",
        button_path="Root/Submenu",
        button_label="Submenu",
        db=session,
        user=SimpleNamespace(id="admin-1"),
    )

    row = session.query(BotButtonSetting).filter(BotButtonSetting.button_key == "menu-a|submenu|Submenu").one()
    assert row.workflow_type == "submenu"
    assert row.button_kind == "section"
    assert row.parent_path == "Root"
    assert row.client_text == "Текст подменю"
    assert row.client_text_overridden is True
    assert row.label_override == "Новый раздел"
    assert row.label_overridden is True
    assert row.is_enabled is False
    assert row.visibility_overridden is True


def test_submenu_empty_client_text_stays_empty_without_txt_fallback(monkeypatch):
    session = _session()
    _seed_row(
        session,
        button_key="menu-a|submenu|Submenu",
        workflow_type="submenu",
        button_kind="section",
        button_path="Submenu",
        button_label="Submenu",
        client_text="Старый текст",
        client_text_overridden=True,
    )
    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "_list_available_button_files", lambda: [])
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: {})

    settings_bot_button_items.save_bot_button_instruction(
        BotButtonInstructionUpdate(
            client_text="",
            client_text_overridden=True,
            label_override="Submenu",
            label_overridden=True,
        ),
        button_key="menu-a|submenu|Submenu",
        menu_type="menu-a",
        workflow_type="submenu",
        button_path="Submenu",
        button_label="Submenu",
        db=session,
        user=SimpleNamespace(id="admin-1"),
    )

    payload = settings_bot_button_items.get_bot_button_item(
        "menu-a|submenu|Submenu",
        menu_type="menu-a",
        db=session,
        user=object(),
    )

    assert payload["client_text"] is None
    assert payload["client_text_effective"] is None
    assert payload["client_text_from"] == "db"


def test_submenu_save_does_not_change_type_when_query_workflow_differs(monkeypatch):
    session = _session()
    _seed_row(
        session,
        button_key="menu-a|submenu|Submenu",
        workflow_type="submenu",
        button_kind="section",
        button_path="Submenu",
        button_label="Submenu",
    )
    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "_list_available_button_files", lambda: [])
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: {})

    settings_bot_button_items.save_bot_button_instruction(
        BotButtonInstructionUpdate(
            client_text="Текст",
            client_text_overridden=True,
            label_override="Submenu",
            label_overridden=True,
        ),
        button_key="menu-a|submenu|Submenu",
        menu_type="menu-a",
        workflow_type="ticket",
        button_path="Submenu",
        button_label="Submenu",
        db=session,
        user=SimpleNamespace(id="admin-1"),
    )

    row = session.query(BotButtonSetting).filter(BotButtonSetting.button_key == "menu-a|submenu|Submenu").one()
    assert row.workflow_type == "submenu"
    assert row.button_kind == "section"


def test_disabling_submenu_does_not_delete_children(monkeypatch):
    session = _session()
    _seed_row(
        session,
        button_key="menu-a|submenu|Submenu",
        workflow_type="submenu",
        button_kind="section",
        button_path="Submenu",
        button_label="Submenu",
        is_db_created=True,
    )
    child = _seed_row(
        session,
        button_key="menu-a|db|Submenu/Child",
        workflow_type="ticket",
        button_kind="terminal",
        button_path="Submenu/Child",
        button_label="Child",
        is_db_created=True,
        parent_path="Submenu",
    )
    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "_list_available_button_files", lambda: [])
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: {})

    settings_bot_button_items.save_bot_button_instruction(
        BotButtonInstructionUpdate(
            client_text="",
            client_text_overridden=True,
            label_override="Submenu",
            label_overridden=True,
            is_enabled=False,
            visibility_overridden=True,
        ),
        button_key="menu-a|submenu|Submenu",
        menu_type="menu-a",
        workflow_type="submenu",
        button_path="Submenu",
        button_label="Submenu",
        db=session,
        user=SimpleNamespace(id="admin-1"),
    )

    session.refresh(child)
    assert child.currently_present is True
    assert child.is_archived is False


def test_delete_submenu_archives_descendants(monkeypatch):
    session = _session()
    parent = _seed_row(
        session,
        button_key="menu-a|db|Submenu",
        workflow_type="submenu",
        button_kind="section",
        button_path="Submenu",
        button_label="Submenu",
        is_db_created=True,
    )
    child = _seed_row(
        session,
        button_key="menu-a|db|Submenu/Child",
        workflow_type="ticket",
        button_kind="terminal",
        button_path="Submenu/Child",
        button_label="Child",
        is_db_created=True,
        parent_path="Submenu",
    )
    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)

    result = settings_bot_button_items.delete_bot_button_setting(
        button_key=parent.button_key,
        hard=False,
        db=session,
        user=SimpleNamespace(id="admin-1"),
    )

    session.refresh(parent)
    session.refresh(child)
    assert result["ok"] is True
    assert result["deleted"] is True
    assert parent.currently_present is False
    assert parent.is_archived is True
    assert child.currently_present is False
    assert child.is_archived is True


def test_txt_submenu_can_be_deleted_via_backend_and_archives_recursive_descendants(monkeypatch):
    session = _session()
    parent = _seed_row(
        session,
        button_key="menu-a|submenu|Submenu",
        workflow_type="submenu",
        button_kind="section",
        button_path="Submenu",
        button_label="Submenu",
        is_db_created=False,
    )
    nested = _seed_row(
        session,
        button_key="menu-a|submenu|Submenu/Nested",
        workflow_type="submenu",
        button_kind="section",
        button_path="Submenu/Nested",
        button_label="Nested",
        is_db_created=False,
        parent_path="Submenu",
    )
    child_a = _seed_row(
        session,
        button_key="menu-a|ticket|Submenu/Leaf A",
        workflow_type="ticket",
        button_kind="terminal",
        button_path="Submenu/Leaf A",
        button_label="Leaf A",
        is_db_created=False,
        parent_path="Submenu",
    )
    child_b = _seed_row(
        session,
        button_key="menu-a|task|Submenu/Nested/Leaf B",
        workflow_type="task",
        button_kind="terminal",
        button_path="Submenu/Nested/Leaf B",
        button_label="Leaf B",
        is_db_created=False,
        parent_path="Submenu/Nested",
    )
    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)

    result = settings_bot_button_items.delete_bot_button_setting(
        button_key=parent.button_key,
        hard=False,
        db=session,
        user=SimpleNamespace(id="admin-1"),
    )

    for row in (parent, nested, child_a, child_b):
        session.refresh(row)
        assert row.currently_present is False
        assert row.is_archived is True
    assert result["ok"] is True
    assert result["deleted"] is True


def test_deleted_txt_submenu_disappears_from_telegram_runtime_but_txt_config_stays(monkeypatch):
    session = _session()
    parent = _seed_row(
        session,
        button_key="menu-a|submenu|Submenu",
        workflow_type="submenu",
        button_kind="section",
        button_path="Submenu",
        button_label="Submenu",
        currently_present=False,
        is_archived=True,
        is_db_created=False,
    )
    config, refs = _txt_submenu_config()
    sent = {}

    monkeypatch.setattr(telegram_router, "_use_db_buttons", lambda: False)
    monkeypatch.setattr(telegram_router, "load_button_config", lambda menu_name: config)
    monkeypatch.setattr(telegram_router, "find_menu", lambda menu_key: None)
    monkeypatch.setattr(telegram_router, "list_terminal_buttons", lambda menu_name: refs)
    monkeypatch.setattr(telegram_router, "_button_settings_map", lambda db, menu_name: {parent.button_key: parent})
    monkeypatch.setattr(telegram_router, "send_message", lambda chat_id, text, reply_markup=None, **kwargs: sent.update({"text": text, "reply_markup": reply_markup}) or sent)

    telegram_router._send_problem_menu_with_visibility(session, "chat-1", menu_name="menu-a", menu_key=None)

    assert "submenu-key" in config.menus
    assert config.menus["submenu-key"].text == "TXT submenu text"
    assert sent["reply_markup"]["inline_keyboard"] == []


def test_deleted_txt_submenu_disappears_from_active_settings_tree_recursively(monkeypatch):
    session = _session()
    config, refs = _nested_txt_submenu_config()
    parent = _seed_row(
        session,
        button_key="menu-a|submenu|Submenu",
        workflow_type="submenu",
        button_kind="section",
        button_path="Submenu",
        button_label="Submenu",
        currently_present=False,
        is_archived=True,
        is_db_created=False,
    )
    _seed_row(
        session,
        button_key="menu-a|submenu|Submenu/Nested",
        workflow_type="submenu",
        button_kind="section",
        button_path="Submenu/Nested",
        button_label="Nested",
        currently_present=False,
        is_archived=True,
        is_db_created=False,
        parent_path="Submenu",
    )
    _seed_row(
        session,
        button_key="menu-a|ticket|Submenu/Leaf A",
        workflow_type="ticket",
        button_kind="terminal",
        button_path="Submenu/Leaf A",
        button_label="Leaf A",
        currently_present=False,
        is_archived=True,
        is_db_created=False,
        parent_path="Submenu",
    )
    _seed_row(
        session,
        button_key="menu-a|task|Submenu/Nested/Leaf B",
        workflow_type="task",
        button_kind="terminal",
        button_path="Submenu/Nested/Leaf B",
        button_label="Leaf B",
        currently_present=False,
        is_archived=True,
        is_db_created=False,
        parent_path="Submenu/Nested",
    )

    monkeypatch.setattr(settings_bot_button_structure, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_structure, "load_button_config", lambda menu_name: config)
    monkeypatch.setattr(settings_bot_button_structure, "list_terminal_buttons", lambda menu_name: refs)

    structure = settings_bot_button_structure._build_bot_menu_structure(session, "menu-a")
    labels = []

    def walk(rows):
        for row in rows or []:
            for node in row.get("buttons", []):
                labels.append(node.get("label"))
                walk(node.get("children_rows", []))

    walk(structure.tree_rows)

    assert config.menus["submenu-key-a"].text == "TXT submenu text"
    assert "Submenu" not in labels
    assert "Nested" not in labels
    assert "Leaf A" not in labels
    assert "Leaf B" not in labels


def test_txt_submenu_restore_restores_recursive_descendants(monkeypatch):
    session = _session()
    parent = _seed_row(
        session,
        button_key="menu-a|submenu|Submenu",
        workflow_type="submenu",
        button_kind="section",
        button_path="Submenu",
        button_label="Submenu",
        currently_present=False,
        is_archived=True,
        is_db_created=False,
    )
    child = _seed_row(
        session,
        button_key="menu-a|ticket|Submenu/Leaf A",
        workflow_type="ticket",
        button_kind="terminal",
        button_path="Submenu/Leaf A",
        button_label="Leaf A",
        currently_present=False,
        is_archived=True,
        is_db_created=False,
        parent_path="Submenu",
    )
    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)

    result = settings_bot_button_items.restore_bot_button_setting(
        button_key=parent.button_key,
        db=session,
        user=SimpleNamespace(id="admin-1"),
    )

    session.refresh(parent)
    session.refresh(child)
    assert result["ok"] is True
    assert result["restored"] is True
    assert parent.currently_present is True
    assert parent.is_archived is False
    assert child.currently_present is True
    assert child.is_archived is False


def test_first_connection_can_close_after_agent_reply_without_verified_profile():
    session = _session()
    ticket = Ticket(
        id="ticket-first",
        company_id="company-1",
        client_id="client-1",
        subject="Первое подключение к боту",
        status=TicketStatus.PENDING.value,
        workflow_type=TicketWorkflowType.TICKET.value,
    )
    session.add(ticket)
    session.add(
        TicketMessage(
            id="message-agent",
            ticket_id=ticket.id,
            message_type=MessageType.AGENT.value,
            body="Ответ клиенту",
            author_user_id="agent-1",
            agent_visible=True,
        )
    )
    session.commit()
    sent = []

    result = close_ticket_by_actor(
        session,
        ticket,
        SimpleNamespace(id="agent-1", role="agent"),
        is_admin_fn=lambda actor: False,
        get_or_create_client_profile_fn=lambda _db, _client_id: SimpleNamespace(is_verified=False),
        last_agent_message_fn=lambda db, t: db.query(TicketMessage).filter_by(ticket_id=t.id, message_type=MessageType.AGENT.value).first(),
        first_client_message_fn=lambda *_: None,
        send_to_telegram_client_fn=lambda _db, _ticket, text, **kwargs: sent.append((text, kwargs)),
        actor_display_name_fn=lambda *_: "Agent",
        add_message_fn=lambda db, t, message_type, body, author_user_id, **kwargs: db.add(TicketMessage(
            id=str(uuid.uuid4()),
            ticket_id=t.id,
            message_type=message_type.value if hasattr(message_type, "value") else message_type,
            body=body,
            author_user_id=author_user_id,
            agent_visible=kwargs.get("agent_visible", True),
        )),
        message_attachments_fn=lambda *_: [],
        send_attachment_to_telegram_client_fn=lambda *_args, **_kwargs: None,
        onsite_close_reminder_text="onsite",
        ticket_closed_client_text="closed",
    )

    assert result == "closed"
    assert ticket.status == TicketStatus.CLOSED.value
    assert sent == [("closed", {"required": False, "remove_keyboard": True})]


def test_first_connection_system_api_materializes_and_preserves_partial_payload():
    session = _session()
    admin = SimpleNamespace(id="admin-1", role="admin")
    first = settings_bot_button_items.get_first_connection_system_ticket(db=session, user=admin)
    assert first["button_key"] == FIRST_CONNECTION_BUTTON_KEY
    assert first["button_label"] == FIRST_CONNECTION_TITLE
    assert first["subject_effective"]

    settings_bot_button_items.save_first_connection_system_ticket(
        settings_bot_button_items.FirstConnectionSystemUpdate(
            subject="Custom subject",
            client_text="Custom client",
            system_text="Custom system",
        ),
        db=session,
        user=admin,
    )
    settings_bot_button_items.save_first_connection_system_ticket(
        settings_bot_button_items.FirstConnectionSystemUpdate(client_text=""),
        db=session,
        user=admin,
    )
    row = session.query(BotButtonSetting).filter_by(button_key=FIRST_CONNECTION_BUTTON_KEY).one()
    assert row.label_override == "Custom subject"
    assert row.client_text == "Custom client"
    assert row.system_text == "Custom system"
    assert row.workflow_type == "ticket"


def test_menu_types_list_excludes_system_menu_after_first_connection_materialization(monkeypatch):
    session = _session()
    admin = SimpleNamespace(id="admin-1", role="admin")
    settings_bot_button_items.get_first_connection_system_ticket(db=session, user=admin)
    _seed_row(session, menu_type="menu-a", button_key="menu-a|ticket|A", button_path="A", button_label="A")
    _seed_row(session, menu_type="menu-b", button_key="menu-b|ticket|B", button_path="B", button_label="B")

    config = ButtonConfig(
        menus={"root": MenuSpec(key="root", title="menu", rows=tuple())},
        buttons={},
        root_menu_key="root",
    )
    monkeypatch.setattr(settings_bot_menu_profiles, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_menu_profiles, "available_menu_names", lambda: ("menu-a", "menu-b"))
    monkeypatch.setattr(settings_bot_menu_profiles, "load_button_config", lambda menu_name: config)

    items = settings_bot_menu_profiles.list_bot_button_menu_types(db=session, user=admin)
    menu_names = [item.menu_name for item in items]

    assert SYSTEM_MENU_TYPE not in menu_names
    assert "menu-a" in menu_names
    assert "menu-b" in menu_names


def test_first_connection_system_row_cannot_be_deleted_as_regular_button():
    session = _session()
    admin = SimpleNamespace(id="admin-1", role="admin")
    settings_bot_button_items.get_first_connection_system_ticket(db=session, user=admin)

    try:
        settings_bot_button_items.delete_bot_button_setting(
            button_key=FIRST_CONNECTION_BUTTON_KEY,
            hard=True,
            db=session,
            user=admin,
        )
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 422
    else:
        raise AssertionError("system first connection row must not be deletable through regular endpoint")
