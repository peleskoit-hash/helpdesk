from __future__ import annotations

import uuid
import hashlib
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import BotButtonSetting, BotMenuDestinationBinding, BotMenuLevelLayout, Client, ClientProfile, Company, MessageType, SavedFile, TelegramContact, Ticket, TicketMessage, TicketStatus, TicketTaskKind, TicketWorkflowType
from api.app.routers import settings_bot_button_items
from api.app.routers import settings_bot_menu_profiles
from api.app.routers import settings_bot_button_structure
from api.app.routers import telegram as telegram_router
from api.app.schemas import BotButtonInstructionUpdate
from api.app.services import button_automation_types
from api.app.services import saved_files
from api.app.services import telegram_menu_flow
from api.app.services import telegram_automation
from api.app.services.telegram_bot import TelegramApiError
from api.app.services.system_tickets import FIRST_CONNECTION_BUTTON_KEY, FIRST_CONNECTION_TITLE, SYSTEM_MENU_TYPE, FirstConnectionDefaults, first_connection_values
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


def _seed_menu_client(session, *, destination="Отдел Продаж", temporary_rop=False):
    company = Company(id=str(uuid.uuid4()), name="Company")
    client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Иванов Иван")
    profile = ClientProfile(
        id=str(uuid.uuid4()),
        client_id=client.id,
        contact_name="Иван",
        last_name="Иванов",
        is_verified=True,
        destination=destination,
        custom_fields={"custom_vremennyy_rop": temporary_rop} if temporary_rop else {},
    )
    contact = TelegramContact(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        external_user_id=str(uuid.uuid4()),
        external_chat_id=str(uuid.uuid4()),
    )
    ticket = Ticket(id=str(uuid.uuid4()), company_id=company.id, client_id=client.id, subject="Ticket")
    session.add_all(
        [
            company,
            client,
            profile,
            contact,
            ticket,
            BotMenuDestinationBinding(id=str(uuid.uuid4()), destination_value="Отдел Продаж", menu_type="Отдел Продаж"),
            BotMenuDestinationBinding(id=str(uuid.uuid4()), destination_value="Руководство", menu_type="Руководство"),
        ]
    )
    session.commit()
    return profile, contact, ticket


def test_menu_name_for_contact_uses_real_destination_without_temporary_rop(monkeypatch):
    session = _session()
    profile, contact, _ticket = _seed_menu_client(session, temporary_rop=False)

    assert telegram_router._menu_name_for_contact(session, contact) == "Отдел Продаж"
    assert profile.destination == "Отдел Продаж"


def test_menu_name_for_contact_uses_management_menu_for_temporary_rop(monkeypatch):
    session = _session()
    profile, contact, _ticket = _seed_menu_client(session, temporary_rop=True)

    assert telegram_router._menu_name_for_contact(session, contact) == "Руководство"
    assert profile.destination == "Отдел Продаж"
    assert profile.contact_name == "Иван"
    assert profile.last_name == "Иванов"


def test_menu_name_for_contact_ignores_non_boolean_temporary_rop(monkeypatch):
    session = _session()
    profile, contact, _ticket = _seed_menu_client(session, temporary_rop=False)
    profile.custom_fields = {"custom_vremennyy_rop": "true"}
    session.commit()

    assert telegram_router._menu_name_for_contact(session, contact) == "Отдел Продаж"


def test_menu_name_for_contact_keeps_real_management_destination(monkeypatch):
    session = _session()
    profile, contact, _ticket = _seed_menu_client(session, destination="Руководство", temporary_rop=False)

    assert telegram_router._menu_name_for_contact(session, contact) == "Руководство"
    assert profile.custom_fields == {}


def test_menu_name_for_ticket_client_uses_management_menu_for_temporary_rop_without_contact(monkeypatch):
    session = _session()
    profile, contact, ticket = _seed_menu_client(session, temporary_rop=True)
    session.delete(contact)
    session.commit()

    assert telegram_router._menu_name_for_ticket_client(session, ticket) == "Руководство"
    assert profile.destination == "Отдел Продаж"


def _fail_fallback(*args, **kwargs):
    raise AssertionError("TXT fallback should not be used")


def test_safe_delete_message_not_found_does_not_remove_inline_keyboard(monkeypatch):
    calls = []

    def fake_delete(chat_id, message_id):
        calls.append(("delete", chat_id, message_id))
        raise TelegramApiError("Bad Request: message to delete not found")

    def fake_remove(chat_id, message_id):
        calls.append(("remove_inline", chat_id, message_id))

    monkeypatch.setattr(telegram_menu_flow, "delete_message", fake_delete)
    monkeypatch.setattr(telegram_menu_flow, "remove_inline_keyboard", fake_remove)

    assert telegram_menu_flow._safe_delete_message("chat-1", 42) is True
    assert calls == [("delete", "chat-1", 42)]


def test_clear_client_menu_keyboards_clears_successful_contact_and_ticket_tracking(monkeypatch):
    session = _session()
    contact = TelegramContact(
        id="contact-1",
        company_id="company-1",
        client_id="client-1",
        external_user_id="user-1",
        external_chat_id="chat-1",
        active_menu_message_id="102",
        menu_message_ids_json=[101, 102],
    )
    ticket = Ticket(
        id="ticket-1",
        company_id="company-1",
        client_id="client-1",
        subject="Ticket",
        menu_message_id="102",
    )
    session.add_all([contact, ticket])
    session.commit()
    deleted = []
    monkeypatch.setattr(telegram_router, "_safe_delete_message", lambda chat_id, message_id: deleted.append(message_id) or True)

    telegram_router._clear_client_menu_keyboards(session, contact, [101, 102], keep_message_id=200)
    session.flush()
    session.refresh(ticket)

    assert deleted == [101, 102]
    assert contact.menu_message_ids_json == [200]
    assert contact.active_menu_message_id == "200"
    assert ticket.menu_message_id is None


def test_clear_client_menu_keyboards_keeps_tracking_when_delete_fails(monkeypatch):
    session = _session()
    contact = TelegramContact(
        id="contact-1",
        company_id="company-1",
        client_id="client-1",
        external_user_id="user-1",
        external_chat_id="chat-1",
        active_menu_message_id="201",
        menu_message_ids_json=[201],
    )
    ticket = Ticket(
        id="ticket-1",
        company_id="company-1",
        client_id="client-1",
        subject="Ticket",
        menu_message_id="201",
    )
    session.add_all([contact, ticket])
    session.commit()
    monkeypatch.setattr(telegram_router, "_safe_delete_message", lambda chat_id, message_id: False)

    telegram_router._clear_client_menu_keyboards(session, contact, [201, 202], keep_message_id=300)
    session.flush()
    session.refresh(ticket)

    assert contact.menu_message_ids_json == [201, 300]
    assert contact.active_menu_message_id == "300"
    assert ticket.menu_message_id == "201"


def _start_payload(chat_id: str = "chat-1", user_id: str = "user-1") -> dict:
    return {
        "update_id": 123,
        "message": {
            "message_id": 1,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": user_id, "first_name": "User"},
            "text": "/start",
        },
    }


def _patch_start_flow(monkeypatch, session, contact, collecting_ticket):
    monkeypatch.setattr(telegram_router, "_first_company", lambda db: SimpleNamespace(id=contact.company_id))
    monkeypatch.setattr(telegram_router, "evaluate_incoming_telegram_access", lambda *args, **kwargs: SimpleNamespace(blocked=False))
    monkeypatch.setattr(telegram_router, "telegram_contact_by_chat_id", lambda db, chat_id: contact)
    monkeypatch.setattr(telegram_router, "get_or_create_contact_from_user", lambda db, company_id, external_chat_id, from_user: contact)
    monkeypatch.setattr(telegram_router, "cleanup_feedback_flow_for_contact", lambda *args, **kwargs: None)
    monkeypatch.setattr(telegram_router, "cleanup_review_flow_for_contact", lambda *args, **kwargs: None)
    monkeypatch.setattr(telegram_router, "_active_ticket_for_client", lambda *args, **kwargs: None)
    monkeypatch.setattr(telegram_router, "_client_profile", lambda db, client_id: SimpleNamespace(is_verified=True, destination="Office"))
    monkeypatch.setattr(telegram_router, "_collecting_ticket_for_client", lambda *args, **kwargs: collecting_ticket)


def test_start_sends_new_menu_before_deleting_old_and_never_deletes_new(monkeypatch):
    session = _session()
    contact = TelegramContact(
        id="contact-1",
        company_id="company-1",
        client_id="client-1",
        external_user_id="user-1",
        external_chat_id="chat-1",
        active_menu_message_id="20",
        menu_message_ids_json=[10, 20],
    )
    ticket = Ticket(id="ticket-1", company_id="company-1", client_id="client-1", subject="Ticket", menu_message_id="10")
    session.add_all([contact, ticket])
    session.commit()
    events = []
    _patch_start_flow(monkeypatch, session, contact, ticket)

    def fake_send(db, send_contact, send_ticket):
        events.append(("send", 20))
        response = {"ok": True, "result": {"message_id": 20}}
        telegram_router._store_menu_message_id(send_ticket, response)
        telegram_router._remember_contact_menu_message(send_contact, response)
        return response

    monkeypatch.setattr(telegram_router, "_send_problem_menu_for_contact", fake_send)
    monkeypatch.setattr(telegram_router, "_safe_delete_message", lambda chat_id, message_id: events.append(("delete", message_id)) or True)

    telegram_router.process_telegram_update(session, _start_payload(), notify_client=True)

    assert events == [("send", 20), ("delete", 10)]
    assert contact.menu_message_ids_json == [20]


def test_start_send_failure_does_not_delete_old_menu(monkeypatch):
    session = _session()
    contact = TelegramContact(
        id="contact-1",
        company_id="company-1",
        client_id="client-1",
        external_user_id="user-1",
        external_chat_id="chat-1",
        active_menu_message_id="10",
        menu_message_ids_json=[10],
    )
    ticket = Ticket(id="ticket-1", company_id="company-1", client_id="client-1", subject="Ticket", menu_message_id="10")
    session.add_all([contact, ticket])
    session.commit()
    deleted = []
    _patch_start_flow(monkeypatch, session, contact, ticket)
    monkeypatch.setattr(telegram_router, "_send_problem_menu_for_contact", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("send failed")))
    monkeypatch.setattr(telegram_router, "_safe_delete_message", lambda chat_id, message_id: deleted.append(message_id) or True)

    with pytest.raises(RuntimeError, match="send failed"):
        telegram_router.process_telegram_update(session, _start_payload(), notify_client=True)

    assert deleted == []
    assert contact.menu_message_ids_json == [10]


def test_start_cleanup_does_not_delete_historical_ticket_menu_ids(monkeypatch):
    session = _session()
    contact = TelegramContact(
        id="contact-1",
        company_id="company-1",
        client_id="client-1",
        external_user_id="user-1",
        external_chat_id="chat-1",
        active_menu_message_id=None,
        menu_message_ids_json=[],
    )
    current = Ticket(id="ticket-current", company_id="company-1", client_id="client-1", subject="Current", menu_message_id="10")
    historical = Ticket(id="ticket-old", company_id="company-1", client_id="client-1", subject="Old", menu_message_id="99")
    session.add_all([contact, current, historical])
    session.commit()
    deleted = []
    _patch_start_flow(monkeypatch, session, contact, current)

    def fake_send(db, send_contact, send_ticket):
        response = {"ok": True, "result": {"message_id": 20}}
        telegram_router._store_menu_message_id(send_ticket, response)
        telegram_router._remember_contact_menu_message(send_contact, response)
        return response

    monkeypatch.setattr(telegram_router, "_send_problem_menu_for_contact", fake_send)
    monkeypatch.setattr(telegram_router, "_safe_delete_message", lambda chat_id, message_id: deleted.append(message_id) or True)

    telegram_router.process_telegram_update(session, _start_payload(), notify_client=True)
    session.refresh(historical)

    assert deleted == [10]
    assert historical.menu_message_id == "99"


def test_start_active_ticket_sends_response_before_deleting_old_menu(monkeypatch):
    session = _session()
    contact = TelegramContact(
        id="contact-1",
        company_id="company-1",
        client_id="client-1",
        external_user_id="user-1",
        external_chat_id="chat-1",
        active_menu_message_id="20",
        menu_message_ids_json=[10, 20],
    )
    ticket = Ticket(id="ticket-1", company_id="company-1", client_id="client-1", subject="Ticket", menu_message_id="10")
    session.add_all([contact, ticket])
    session.commit()
    events = []
    _patch_start_flow(monkeypatch, session, contact, None)
    monkeypatch.setattr(telegram_router, "_active_ticket_for_client", lambda *args, **kwargs: ticket)
    monkeypatch.setattr(telegram_router, "client_can_close_ticket", lambda *args, **kwargs: False)
    monkeypatch.setattr(telegram_router, "_automatic_telegram_text", lambda *args, **kwargs: ("Active ticket", None))
    monkeypatch.setattr(
        telegram_router,
        "send_message",
        lambda *args, **kwargs: events.append(("send", 30)) or {"ok": True, "result": {"message_id": 30}},
    )
    monkeypatch.setattr(telegram_router, "_safe_delete_message", lambda chat_id, message_id: events.append(("delete", message_id)) or True)

    telegram_router.process_telegram_update(session, _start_payload(), notify_client=True)
    session.refresh(ticket)

    assert events == [("send", 30), ("delete", 10), ("delete", 20)]
    assert contact.menu_message_ids_json == []
    assert ticket.menu_message_id is None


def _save_button_instruction(session, payload, *, button_key="menu-a|ticket|A", workflow_type="ticket", user=None):
    return settings_bot_button_items.save_bot_button_instruction(
        BotButtonInstructionUpdate(**payload),
        button_key=button_key,
        menu_type="menu-a",
        workflow_type=workflow_type,
        button_path="A",
        button_label="A",
        db=session,
        user=user if user is not None else object(),
    )


def test_bot_button_style_save_get_and_validation():
    for style in ("primary", "success", "danger"):
        session = _session()
        result = _save_button_instruction(session, {"button_style": style}, user=SimpleNamespace(role="admin"))
        assert result["button_style"] == style

        item = settings_bot_button_items.get_bot_button_item("menu-a|ticket|A", menu_type="menu-a", db=session, user=SimpleNamespace(role="admin"))
        assert item["button_style"] == style

    session = _session()
    with pytest.raises(HTTPException) as exc_info:
        _save_button_instruction(session, {"button_style": "purple"}, user=SimpleNamespace(role="admin"))
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["error"]["code"] == "invalid_button_style"


def _txt_submenu_config():
    child = ButtonSpec(key="child-a", label="Leaf A", kind="ticket", text="TXT child text")
    submenu = ButtonSpec(key="submenu-a", label="Submenu", kind="submenu", submenu_key="menu-a|submenu|Submenu")
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


def test_legacy_button_file_still_sends(monkeypatch, tmp_path):
    session = _session()
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    legacy_path = files_dir / "legacy.pdf"
    legacy_path.write_bytes(b"legacy")
    _seed_row(session, automation_key="send_files", files_json=["legacy.pdf"])
    sent = []

    monkeypatch.setattr(telegram_router, "BUTTON_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(telegram_router, "send_document", lambda chat_id, path, filename=None: sent.append((chat_id, path, filename)))

    ok, reason = telegram_router._send_button_direct_file(session, _ticket(), _button(), "chat-1")

    assert ok is True
    assert reason is None
    assert sent == [("chat-1", legacy_path.resolve(), "legacy.pdf")]


def test_saved_file_reference_resolves_to_physical_file(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    session = _session()
    saved = saved_files.create_saved_file(session, content=b"saved", display_filename="saved.pdf")
    session.commit()

    resolved_path, telegram_filename = telegram_router._resolve_button_file_path(session, f"saved:{saved.id}")

    assert resolved_path == saved_files.resolve_saved_file_path(saved)
    assert telegram_filename == "saved.pdf"


def test_direct_button_sends_saved_file_reference(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    session = _session()
    saved = saved_files.create_saved_file(session, content=b"saved", display_filename="saved.pdf")
    session.commit()
    _seed_row(session, automation_key="send_files", files_json=[f"saved:{saved.id}"])
    sent = []

    monkeypatch.setattr(telegram_router, "send_document", lambda chat_id, path, filename=None: sent.append((chat_id, path, filename)))

    ok, reason = telegram_router._send_button_direct_file(session, _ticket(), _button(), "chat-1")

    assert ok is True
    assert reason is None
    assert sent == [("chat-1", saved_files.resolve_saved_file_path(saved), "saved.pdf")]


def test_conditional_button_sends_saved_file_reference(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    session = _session()
    saved = saved_files.create_saved_file(session, content=b"saved", display_filename="saved.pdf")
    session.commit()
    _seed_row(
        session,
        automation_key="send_files",
        conditional_files_json=[
            {
                "conditions": [{"field": "flag", "operator": "eq", "value": "yes"}],
                "files": [f"saved:{saved.id}"],
            }
        ],
    )
    sent = []

    monkeypatch.setattr(telegram_router, "_field_value_for_rule", lambda *args, **kwargs: "yes")
    monkeypatch.setattr(telegram_router, "send_document", lambda chat_id, path, filename=None: sent.append((chat_id, path, filename)))

    telegram_router._send_button_rule_files(session, _ticket(), _button(), "chat-1")

    assert sent == [("chat-1", saved_files.resolve_saved_file_path(saved), "saved.pdf")]


def test_direct_saved_file_rename_changes_next_telegram_filename_only(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    session = _session()
    saved = saved_files.create_saved_file(session, content=b"saved", display_filename="old.pdf")
    session.commit()
    original_storage_key = saved.storage_key
    original_path = saved_files.resolve_saved_file_path(saved)
    saved_files.rename_saved_file(session, saved, "new.pdf")
    session.commit()
    _seed_row(session, automation_key="send_files", files_json=[f"saved:{saved.id}"])
    sent = []

    monkeypatch.setattr(telegram_router, "send_document", lambda chat_id, path, filename=None: sent.append((chat_id, path, filename)))

    ok, reason = telegram_router._send_button_direct_file(session, _ticket(), _button(), "chat-1")

    assert ok is True
    assert reason is None
    assert saved.storage_key == original_storage_key
    assert saved_files.resolve_saved_file_path(saved) == original_path
    assert sent == [("chat-1", original_path, "new.pdf")]


def test_conditional_saved_file_rename_changes_next_telegram_filename_only(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path))
    session = _session()
    saved = saved_files.create_saved_file(session, content=b"saved", display_filename="old.pdf")
    session.commit()
    original_storage_key = saved.storage_key
    original_path = saved_files.resolve_saved_file_path(saved)
    saved_files.rename_saved_file(session, saved, "new.pdf")
    session.commit()
    _seed_row(
        session,
        automation_key="send_files",
        conditional_files_json=[
            {
                "conditions": [{"field": "flag", "operator": "eq", "value": "yes"}],
                "files": [f"saved:{saved.id}"],
            }
        ],
    )
    sent = []

    monkeypatch.setattr(telegram_router, "_field_value_for_rule", lambda *args, **kwargs: "yes")
    monkeypatch.setattr(telegram_router, "send_document", lambda chat_id, path, filename=None: sent.append((chat_id, path, filename)))

    telegram_router._send_button_rule_files(session, _ticket(), _button(), "chat-1")

    assert saved.storage_key == original_storage_key
    assert saved_files.resolve_saved_file_path(saved) == original_path
    assert sent == [("chat-1", original_path, "new.pdf")]


def test_missing_saved_file_reference_is_controlled_and_not_sent(monkeypatch):
    session = _session()
    _seed_row(session, automation_key="send_files", files_json=[f"saved:{uuid.uuid4()}"])
    messages = []

    monkeypatch.setattr(telegram_router, "send_document", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not send")))
    monkeypatch.setattr(telegram_router, "append_system_message", lambda *args, **kwargs: messages.append(args[2]))

    ok, reason = telegram_router._send_button_direct_file(session, _ticket(), _button(), "chat-1")

    assert ok is False
    assert "saved:" in reason
    assert messages


def test_saved_file_reference_cannot_traverse_storage_root(monkeypatch, tmp_path):
    monkeypatch.setattr(saved_files.settings, "saved_file_storage_dir", str(tmp_path / "root"))
    session = _session()
    saved = SavedFile(
        id=str(uuid.uuid4()),
        display_filename="outside.pdf",
        storage_key="../outside.pdf",
        mime_type="application/pdf",
        file_size=3,
        created_at=datetime.utcnow(),
    )
    session.add(saved)
    session.commit()
    _seed_row(session, automation_key="send_files", files_json=[f"saved:{saved.id}"])
    messages = []

    monkeypatch.setattr(telegram_router, "send_document", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not send")))
    monkeypatch.setattr(telegram_router, "append_system_message", lambda *args, **kwargs: messages.append(args[2]))

    ok, reason = telegram_router._send_button_direct_file(session, _ticket(), _button(), "chat-1")

    assert ok is False
    assert "saved:" in reason
    assert messages


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
    def fake_fallback(_db, current_ticket, *args, **kwargs):
        current_ticket.status = TicketStatus.UNASSIGNED.value
        fallback_calls.append((args, kwargs))
        return None

    monkeypatch.setattr(telegram_router, "_fallback_auto_to_support", fake_fallback)
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
    assert result["status"] == TicketStatus.UNASSIGNED.value
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
    def fake_fallback(_db, current_ticket, *args, **kwargs):
        current_ticket.status = TicketStatus.UNASSIGNED.value
        fallback_calls.append((args, kwargs))
        return None

    monkeypatch.setattr(telegram_router, "_fallback_auto_to_support", fake_fallback)
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
    assert result["status"] == TicketStatus.UNASSIGNED.value
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




def test_db_submenu_runtime_uses_db_text_instead_of_txt(monkeypatch):
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
    _seed_row(session, button_key="menu-a|ticket|Submenu/Leaf A", button_label="Leaf A",
              button_path="Submenu/Leaf A", parent_path="Submenu")
    config, refs = _txt_submenu_config()
    sent = {}

    monkeypatch.setattr(telegram_router, "_button_settings_map", lambda db, menu_name: {
        "menu-a|submenu|Submenu": db.query(BotButtonSetting).filter(BotButtonSetting.button_key == "menu-a|submenu|Submenu").one()
    })
    monkeypatch.setattr(telegram_router, "_submenu_path_by_key_or_db", lambda db, menu_name, menu_key: "Submenu")
    monkeypatch.setattr(telegram_router, "send_message", lambda chat_id, text, reply_markup=None, **kwargs: sent.update({"chat_id": chat_id, "text": text, "reply_markup": reply_markup}) or sent)

    telegram_router._send_problem_menu_with_visibility(session, "chat-1", menu_name="menu-a", menu_key="menu-a|submenu|Submenu")

    assert sent["text"] == "DB submenu text"
    assert sent["reply_markup"]["inline_keyboard"][0][0]["text"] == "Leaf A"
    assert sent["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "btnid:" + session.query(BotButtonSetting).filter_by(button_key="menu-a|ticket|Submenu/Leaf A").one().id
    assert "style" not in sent["reply_markup"]["inline_keyboard"][0][0]


def test_db_runtime_adds_saved_button_style(monkeypatch):
    session = _session()
    submenu_row = _seed_row(
        session,
        button_key="menu-a|submenu|Submenu",
        workflow_type="submenu",
        button_kind="section",
        button_path="Submenu",
        button_label="Submenu",
        client_text="DB submenu text",
        parent_path="",
    )
    child_row = _seed_row(
        session,
        button_key="menu-a|ticket|Submenu/Leaf A",
        workflow_type="ticket",
        button_kind="terminal",
        button_path="Submenu/Leaf A",
        button_label="Leaf A",
        parent_path="Submenu",
        button_style="success",
    )
    config, refs = _txt_submenu_config()
    sent = {}

    monkeypatch.setattr(telegram_router, "_button_settings_map", lambda db, menu_name: {
        submenu_row.button_key: submenu_row,
        child_row.button_key: child_row,
    })
    monkeypatch.setattr(telegram_router, "_submenu_path_by_key_or_db", lambda db, menu_name, menu_key: "Submenu")
    monkeypatch.setattr(telegram_router, "send_message", lambda chat_id, text, reply_markup=None, **kwargs: sent.update({"text": text, "reply_markup": reply_markup}) or sent)

    telegram_router._send_problem_menu_with_visibility(session, "chat-1", menu_name="menu-a", menu_key="menu-a|submenu|Submenu")

    button = sent["reply_markup"]["inline_keyboard"][0][0]
    assert button["text"] == "Leaf A"
    assert button["callback_data"] == "btnid:" + session.query(BotButtonSetting).filter_by(button_key="menu-a|ticket|Submenu/Leaf A").one().id
    assert button["style"] == "success"


def test_db_submenu_runtime_empty_db_text_does_not_fallback_to_txt(monkeypatch):
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

    monkeypatch.setattr(telegram_router, "_button_settings_map", lambda db, menu_name: {
        "menu-a|submenu|Submenu": db.query(BotButtonSetting).filter(BotButtonSetting.button_key == "menu-a|submenu|Submenu").one()
    })
    monkeypatch.setattr(telegram_router, "_submenu_path_by_key_or_db", lambda db, menu_name, menu_key: "Submenu")
    monkeypatch.setattr(telegram_router, "send_message", lambda chat_id, text, reply_markup=None, **kwargs: sent.update({"text": text, "reply_markup": reply_markup}) or sent)

    telegram_router._send_problem_menu_with_visibility(session, "chat-1", menu_name="menu-a", menu_key="menu-a|submenu|Submenu")

    assert sent["text"] == "Выберите тип проблемы"


def test_db_submenu_runtime_without_db_row_does_not_fallback_to_txt(monkeypatch):
    session = _session()
    config, refs = _txt_submenu_config()
    sent = {}

    monkeypatch.setattr(telegram_router, "_button_settings_map", lambda db, menu_name: {})
    monkeypatch.setattr(telegram_router, "_submenu_path_by_key_or_db", lambda db, menu_name, menu_key: "Submenu")
    monkeypatch.setattr(telegram_router, "send_message", lambda chat_id, text, reply_markup=None, **kwargs: sent.update({"text": text, "reply_markup": reply_markup}) or sent)

    telegram_router._send_problem_menu_with_visibility(session, "chat-1", menu_name="menu-a", menu_key="menu-a|submenu|Submenu")

    assert "больше недоступна" in sent["text"]
    assert sent["reply_markup"] is None


@pytest.mark.parametrize("button_style", ["primary", "success", "danger"])
def test_db_created_submenu_runtime_uses_db_text(monkeypatch, button_style):
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
        button_style=button_style,
    )
    sent = {}

    monkeypatch.setattr(telegram_router, "send_message", lambda chat_id, text, reply_markup=None, **kwargs: sent.update({"text": text, "reply_markup": reply_markup}) or sent)

    telegram_router._send_problem_menu_db_only(session, "chat-1", menu_name="menu-a", menu_key=section.button_key)

    assert sent["text"] == "DB created submenu text"
    assert sent["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"btnid:{child.id}"
    assert sent["reply_markup"]["inline_keyboard"][0][0]["style"] == button_style


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


def test_submenu_save_rejects_type_change_when_query_workflow_differs(monkeypatch):
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

    try:
        settings_bot_button_items.save_bot_button_instruction(
            BotButtonInstructionUpdate(
                client_text="\u0422\u0435\u043a\u0441\u0442",
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
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 422
        detail = getattr(exc, "detail", {}) or {}
        error = detail.get("error", {}) if isinstance(detail, dict) else {}
        assert error.get("code") == "button_type_change_forbidden"
        assert error.get("message") == "\u041d\u0435\u043b\u044c\u0437\u044f \u043c\u0435\u043d\u044f\u0442\u044c \u0442\u0438\u043f \u043f\u043e\u0434\u043c\u0435\u043d\u044e \u0447\u0435\u0440\u0435\u0437 \u0440\u0435\u0434\u0430\u043a\u0442\u043e\u0440 \u043a\u043d\u043e\u043f\u043a\u0438"
    else:
        raise AssertionError("Expected submenu type transition rejection")


@pytest.mark.parametrize("old_type,new_type", [("ticket", "task"), ("task", "ticket")])
@pytest.mark.parametrize("callback_kind", ["source", "technical", "id", "db"])
def test_existing_button_type_change_applies_to_new_telegram_ticket(monkeypatch, old_type, new_type, callback_kind):
    session = _session()
    key = f"menu-a|{old_type}|A"
    row = _seed_row(session, button_key=key, workflow_type=old_type)
    identity = (row.id, row.stable_button_id, row.button_key, row.legacy_button_key)
    source = ButtonSpec(key="txt-a", label="A", kind=old_type)
    refs = (TerminalButtonRef(
        source_button_key=source.key, label="A", workflow_type=old_type,
        full_path="A", technical_key=key,
    ),)
    config = ButtonConfig(
        menus={"root": MenuSpec(key="root", title="menu-a", rows=((source.key,),))},
        buttons={source.key: source}, root_menu_key="root",
    )
    _patch_layout_config(monkeypatch, config, refs, session)
    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "_list_available_button_files", lambda: [])

    result = _save_button_instruction(session, {"instruction_text": "Saved instruction"}, button_key=key, workflow_type=new_type)
    assert result["ok"] is True
    session.expire_all()
    structure = settings_bot_button_structure._build_bot_menu_structure(session, "menu-a")
    item = settings_bot_button_items.get_bot_button_item(key, menu_type="menu-a", db=session, user=object())
    assert item["workflow_type"] == new_type
    assert item["instruction_text"] == "Saved instruction"
    assert structure.tree_rows[0]["buttons"][0]["workflow_type"] == new_type
    row = session.query(BotButtonSetting).filter_by(button_key=key).one()
    assert (row.id, row.stable_button_id, row.button_key, row.legacy_button_key) == identity

    company = Company(id=str(uuid.uuid4()), name="Company")
    client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Client")
    contact = TelegramContact(id=str(uuid.uuid4()), company_id=company.id, client_id=client.id,
                              external_user_id="123", external_chat_id="456")
    old_ticket = Ticket(id=str(uuid.uuid4()), company_id=company.id, client_id=client.id,
                        subject="Old", workflow_type=old_type, status=TicketStatus.CLOSED.value,
                        instruction_button_key=key)
    session.add_all([company, client, contact, old_ticket])
    session.commit()
    monkeypatch.setattr(telegram_router, "_first_company", lambda db: company)
    monkeypatch.setattr(telegram_router, "get_or_create_contact_from_user", lambda *args: contact)
    monkeypatch.setattr(telegram_router, "_menu_name_for_contact", lambda db, contact: "menu-a")
    monkeypatch.setattr(telegram_router, "schedule_avatar_refresh", lambda *args: None)
    monkeypatch.setattr(telegram_router, "_remove_menu_keyboard", lambda *args, **kwargs: None)
    callback_data = {
        "source": "btn:" + hashlib.sha1(b"menu-a|A").hexdigest()[:12], "technical": f"btn:{key}",
        "id": f"btnid:{row.id}", "db": f"btn:{key}",
    }[callback_kind]
    button = telegram_router._button_from_callback(session, callback_data, "menu-a")
    assert button.kind == new_type
    assert button.key == key
    response = telegram_router._process_problem_type_callback(
        session, {"update_id": 1},
        {"data": callback_data, "message": {"chat": {"id": 456}}, "from": {"id": 123}},
        notify_client=False,
    )
    created = session.get(Ticket, response["ticket_id"])
    assert created.id != old_ticket.id
    assert created.workflow_type == new_type
    assert created.instruction_button_key == key
    assert created.task_kind == TicketTaskKind.NORMAL.value
    assert bool(response.get("task_pending")) == (new_type == "task")
    session.expire_all()
    assert session.get(Ticket, old_ticket.id).workflow_type == old_type


@pytest.mark.parametrize("workflow,kind", [
    ("group_task", "group_parent"), ("group_task_child", "terminal"), ("system", "system"),
])
@pytest.mark.parametrize("requested", ["ticket", "task"])
def test_existing_protected_button_rejects_workflow_change(monkeypatch, workflow, kind, requested):
    session = _session()
    key = f"menu-a|{workflow}|A"
    row = _seed_row(session, button_key=key, workflow_type=workflow, button_kind=kind)
    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "_list_available_button_files", lambda: [])
    with pytest.raises(HTTPException) as error:
        _save_button_instruction(session, {}, button_key=key, workflow_type=requested)
    assert error.value.status_code == 422
    assert error.value.detail["error"]["code"] == (
        "system_button_type_change_forbidden" if kind == "system" else "button_type_change_forbidden"
    )
    session.expire_all()
    assert row.workflow_type == workflow
    assert row.button_kind == kind
    assert row.button_key == key


def test_existing_terminal_button_can_change_type_between_ticket_task_and_auto(monkeypatch):
    session = _session()
    _seed_row(session, button_key="menu-a|ticket|A", workflow_type="ticket", button_kind="terminal")
    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "_list_available_button_files", lambda: [])
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: telegram_automation.get_automation_registry())

    payload = settings_bot_button_items.save_bot_button_instruction(
        BotButtonInstructionUpdate(
            instruction_text="Task instruction",
            label_override="A",
            label_overridden=True,
            automation_type="send_files",
            automation_overridden=True,
        ),
        button_key="menu-a|ticket|A",
        menu_type="menu-a",
        workflow_type="task",
        button_path="A",
        button_label="A",
        db=session,
        user=SimpleNamespace(id="admin-1"),
    )

    row = session.query(BotButtonSetting).filter(BotButtonSetting.button_key == "menu-a|ticket|A").one()
    assert payload["ok"] is True
    assert row.workflow_type == "task"
    assert row.button_kind == "terminal"


def test_structure_build_keeps_saved_editable_workflow_type_override(monkeypatch):
    session = _session()
    _seed_row(
        session,
        button_key="menu-a|ticket|A",
        workflow_type="task",
        button_kind="terminal",
        button_path="A",
        button_label="A",
    )
    config = ButtonConfig(
        menus={"root": MenuSpec(key="root", title="menu-a", rows=(("txt-a",),))},
        buttons={"txt-a": ButtonSpec(key="txt-a", label="A", kind="ticket", text="TXT text")},
        root_menu_key="root",
    )
    refs = (
        TerminalButtonRef(
            source_button_key="txt-a",
            label="A",
            workflow_type="ticket",
            full_path="A",
            technical_key="menu-a|ticket|A",
            instruction_text=None,
        ),
    )
    monkeypatch.setattr(settings_bot_button_structure, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)

    structure = settings_bot_button_structure._build_bot_menu_structure(session, "menu-a")
    row = session.query(BotButtonSetting).filter(BotButtonSetting.button_key == "menu-a|ticket|A").one()
    payload = settings_bot_button_items.get_bot_button_item(
        "menu-a|ticket|A",
        menu_type="menu-a",
        db=session,
        user=SimpleNamespace(id="admin-1"),
    )

    assert structure.tree_rows[0]["buttons"][0]["technical_key"] == "menu-a|ticket|A"
    assert structure.tree_rows[0]["buttons"][0]["workflow_type"] == "task"
    assert row.button_key == "menu-a|ticket|A"
    assert row.workflow_type == "task"
    assert payload["workflow_type"] == "task"


def test_group_parent_save_with_same_workflow_keeps_group_parent_button_kind(monkeypatch):
    session = _session()
    _seed_row(session, button_key="menu-a|group_task|A", workflow_type="group_task", button_kind="group_parent")
    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "_list_available_button_files", lambda: [])
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: {})

    settings_bot_button_items.save_bot_button_instruction(
        BotButtonInstructionUpdate(
            label_override="A",
            label_overridden=True,
            client_text="\u0422\u0435\u043a\u0441\u0442",
            client_text_overridden=True,
        ),
        button_key="menu-a|group_task|A",
        menu_type="menu-a",
        workflow_type="group_task",
        button_path="A",
        button_label="A",
        db=session,
        user=SimpleNamespace(id="admin-1"),
    )

    row = session.query(BotButtonSetting).filter(BotButtonSetting.button_key == "menu-a|group_task|A").one()
    assert row.workflow_type == "group_task"
    assert row.button_kind == "group_parent"


def test_group_parent_save_allows_legacy_section_button_kind(monkeypatch):
    session = _session()
    _seed_row(session, button_key="menu-a|group_task|A", workflow_type="group_task", button_kind="section")
    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "_list_available_button_files", lambda: [])
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: {})

    payload = settings_bot_button_items.save_bot_button_instruction(
        BotButtonInstructionUpdate(
            label_override="A",
            label_overridden=True,
            client_text="Текст",
            client_text_overridden=True,
        ),
        button_key="menu-a|group_task|A",
        menu_type="menu-a",
        workflow_type="group_task",
        button_path="A",
        button_label="A",
        db=session,
        user=SimpleNamespace(id="admin-1"),
    )

    row = session.query(BotButtonSetting).filter(BotButtonSetting.button_key == "menu-a|group_task|A").one()
    assert payload["ok"] is True
    assert row.workflow_type == "group_task"
    assert row.button_kind == "section"
    assert row.client_text == "Текст"


def test_group_task_child_save_with_same_workflow_keeps_terminal_child_button_kind(monkeypatch):
    session = _session()
    _seed_row(session, button_key="menu-a|group_task_child|A", workflow_type="group_task_child", button_kind="terminal")
    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "_list_available_button_files", lambda: [])
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: {})

    settings_bot_button_items.save_bot_button_instruction(
        BotButtonInstructionUpdate(
            label_override="A",
            label_overridden=True,
            instruction_text="Child instruction",
        ),
        button_key="menu-a|group_task_child|A",
        menu_type="menu-a",
        workflow_type="group_task_child",
        button_path="A",
        button_label="A",
        db=session,
        user=SimpleNamespace(id="admin-1"),
    )

    row = session.query(BotButtonSetting).filter(BotButtonSetting.button_key == "menu-a|group_task_child|A").one()
    assert row.workflow_type == "group_task_child"
    assert row.button_kind == "terminal"


def test_group_task_child_suppress_client_answer_get_put_default(monkeypatch):
    session = _session()
    _seed_row(session, button_key="menu-a|group_task_child|A", workflow_type="group_task_child", button_kind="terminal")
    admin = SimpleNamespace(id="admin-1", role="admin")
    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "_list_available_button_files", lambda: [])
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: {})

    item = settings_bot_button_items.get_bot_button_item("menu-a|group_task_child|A", menu_type="menu-a", db=session, user=admin)
    assert item["suppress_client_answer"] is False

    payload = settings_bot_button_items.save_bot_button_instruction(
        BotButtonInstructionUpdate(
            label_override="A",
            label_overridden=True,
            instruction_text="Child instruction",
            suppress_client_answer=True,
        ),
        button_key="menu-a|group_task_child|A",
        menu_type="menu-a",
        workflow_type="group_task_child",
        button_path="A",
        button_label="A",
        db=session,
        user=admin,
    )
    assert payload["suppress_client_answer"] is True
    item = settings_bot_button_items.get_bot_button_item("menu-a|group_task_child|A", menu_type="menu-a", db=session, user=admin)
    assert item["suppress_client_answer"] is True

    payload = settings_bot_button_items.save_bot_button_instruction(
        BotButtonInstructionUpdate(
            label_override="A",
            label_overridden=True,
            suppress_client_answer=False,
        ),
        button_key="menu-a|group_task_child|A",
        menu_type="menu-a",
        workflow_type="group_task_child",
        button_path="A",
        button_label="A",
        db=session,
        user=admin,
    )
    assert payload["suppress_client_answer"] is False


def test_type_change_clears_incompatible_existing_automation_when_frontend_does_not_send_replacement(monkeypatch):
    session = _session()
    _seed_row(
        session,
        button_key="menu-a|auto|A",
        workflow_type="auto",
        button_kind="terminal",
        automation_key="message_only",
        automation_overridden=True,
    )
    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "_list_available_button_files", lambda: [])
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: telegram_automation.get_automation_registry())

    payload = settings_bot_button_items.save_bot_button_instruction(
        BotButtonInstructionUpdate(
            instruction_text="Ticket instruction",
            label_override="A",
            label_overridden=True,
        ),
        button_key="menu-a|auto|A",
        menu_type="menu-a",
        workflow_type="ticket",
        button_path="A",
        button_label="A",
        db=session,
        user=SimpleNamespace(id="admin-1"),
    )

    row = session.query(BotButtonSetting).filter(BotButtonSetting.button_key == "menu-a|auto|A").one()
    assert row.workflow_type == "ticket"
    assert row.automation_key is None
    assert payload["automation_type"] == "none"


def test_existing_terminal_button_cannot_change_type_to_submenu(monkeypatch):
    session = _session()
    _seed_row(session, button_key="menu-a|ticket|A", workflow_type="ticket", button_kind="terminal")
    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "_list_available_button_files", lambda: [])
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: {})

    try:
        settings_bot_button_items.save_bot_button_instruction(
            BotButtonInstructionUpdate(
                label_override="A",
                label_overridden=True,
            ),
            button_key="menu-a|ticket|A",
            menu_type="menu-a",
            workflow_type="submenu",
            button_path="A",
            button_label="A",
            db=session,
            user=SimpleNamespace(id="admin-1"),
        )
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 422
        detail = getattr(exc, "detail", {}) or {}
        error = detail.get("error", {}) if isinstance(detail, dict) else {}
        assert error.get("code") == "button_type_change_forbidden"
        assert error.get("message") == "\u0420\u0430\u0437\u0440\u0435\u0448\u0435\u043d\u0430 \u0441\u043c\u0435\u043d\u0430 \u0442\u0438\u043f\u0430 \u0442\u043e\u043b\u044c\u043a\u043e \u043c\u0435\u0436\u0434\u0443 \u0417\u0430\u044f\u0432\u043a\u043e\u0439, \u0417\u0430\u0434\u0430\u0447\u0435\u0439 \u0438 \u0410\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u043e\u0439 \u0437\u0430\u0434\u0430\u0447\u0435\u0439"
    else:
        raise AssertionError("Expected submenu transition rejection")


def test_system_first_connection_cannot_change_type_via_regular_button_endpoint(monkeypatch):
    session = _session()
    admin = SimpleNamespace(id="admin-1", role="admin")
    settings_bot_button_items.get_first_connection_system_ticket(db=session, user=admin)
    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "_list_available_button_files", lambda: [])
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: {})

    try:
        settings_bot_button_items.save_bot_button_instruction(
            BotButtonInstructionUpdate(
                label_override="System",
                label_overridden=True,
            ),
            button_key=FIRST_CONNECTION_BUTTON_KEY,
            menu_type="menu-a",
            workflow_type="task",
            button_path="System",
            button_label="System",
            db=session,
            user=admin,
        )
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 422
        detail = getattr(exc, "detail", {}) or {}
        error = detail.get("error", {}) if isinstance(detail, dict) else {}
        assert error.get("code") == "system_button_type_change_forbidden"
        assert error.get("message") == "\u0421\u0438\u0441\u0442\u0435\u043c\u043d\u043e\u0439 \u0437\u0430\u044f\u0432\u043a\u0435 \u043d\u0435\u043b\u044c\u0437\u044f \u043c\u0435\u043d\u044f\u0442\u044c \u0442\u0438\u043f"
    else:
        raise AssertionError("Expected system type transition rejection")


def test_system_row_save_with_same_workflow_keeps_system_button_kind(monkeypatch):
    session = _session()
    _seed_row(
        session,
        button_key="menu-a|system|A",
        workflow_type="system",
        button_kind="system",
        button_path="A",
        button_label="A",
    )
    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "_list_available_button_files", lambda: [])
    monkeypatch.setattr(settings_bot_button_items, "get_automation_registry", lambda: {})

    settings_bot_button_items.save_bot_button_instruction(
        BotButtonInstructionUpdate(
            label_override="A",
            label_overridden=True,
        ),
        button_key="menu-a|system|A",
        menu_type="menu-a",
        workflow_type="system",
        button_path="A",
        button_label="A",
        db=session,
        user=SimpleNamespace(id="admin-1"),
    )

    row = session.query(BotButtonSetting).filter(BotButtonSetting.button_key == "menu-a|system|A").one()
    assert row.workflow_type == "system"
    assert row.button_kind == "system"


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


def test_db_submenu_can_be_deleted_via_backend_and_archives_recursive_descendants(monkeypatch):
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


def test_deleted_db_submenu_disappears_from_telegram_runtime_but_txt_config_stays(monkeypatch):
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

    monkeypatch.setattr(telegram_router, "_button_settings_map", lambda db, menu_name: {parent.button_key: parent})
    monkeypatch.setattr(telegram_router, "send_message", lambda chat_id, text, reply_markup=None, **kwargs: sent.update({"text": text, "reply_markup": reply_markup}) or sent)

    telegram_router._send_problem_menu_with_visibility(session, "chat-1", menu_name="menu-a", menu_key=None)

    assert "submenu-key" in config.menus
    assert config.menus["submenu-key"].text == "TXT submenu text"
    assert sent["reply_markup"]["inline_keyboard"] == []


def test_deleted_db_submenu_disappears_from_active_settings_tree_recursively(monkeypatch):
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

    structure = settings_bot_button_structure._build_bot_menu_structure(session, "menu-a")
    labels = []

    def walk(rows):
        for row in rows or []:
            for node in row.get("buttons", []):
                labels.append(node.get("label"))
                walk(node.get("children_rows", []))

    walk([row for row in structure.tree_rows if row["buttons"][0]["kind"] != "removed_folder"])

    assert config.menus["submenu-key-a"].text == "TXT submenu text"
    assert "Submenu" not in labels
    assert "Nested" not in labels
    assert "Leaf A" not in labels
    assert "Leaf B" not in labels


def test_db_submenu_restore_restores_recursive_descendants(monkeypatch):
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


def test_db_created_group_task_child_survives_rebuild_archive_and_restore(monkeypatch):
    session = _session()
    source_child = ButtonSpec(key="source-child", label="Source child", kind="task")
    source_parent = ButtonSpec(key="source-parent", label="Parent", kind="group_task", subtasks=(source_child,))
    config = ButtonConfig(
        menus={"root": MenuSpec(key="root", title="menu-a", rows=((source_parent.key,),))},
        buttons={source_parent.key: source_parent},
        root_menu_key="root",
    )
    refs = (
        TerminalButtonRef(
            source_button_key=source_child.key,
            label=source_child.label,
            workflow_type="group_task_child",
            full_path="Parent/Source child",
            technical_key="menu-a|group_task_child|Parent/Source child",
            instruction_text=None,
        ),
    )
    _seed_row(session, button_key="menu-a|group_task|Parent", workflow_type="group_task",
              button_kind="group_parent", button_path="Parent", button_label="Parent", legacy_button_key="source-parent")
    _seed_row(session, button_key="menu-a|group_task_child|Parent/Source child", workflow_type="group_task_child",
              button_path="Parent/Source child", parent_path="Parent", button_label="Source child", legacy_button_key="source-child")
    admin = SimpleNamespace(id="admin-1", role="admin")

    monkeypatch.setattr(settings_bot_button_structure, "_require_admin", lambda user: None)
    monkeypatch.setattr(settings_bot_button_items, "_require_admin", lambda user: None)

    created = settings_bot_button_structure.create_bot_button_item(
        {
            "menu_type": "menu-a",
            "parent_path": "Parent",
            "label": "DB child",
            "button_kind": "group_task_child",
            "workflow_type": "group_task_child",
            "is_enabled": True,
        },
        db=session,
        user=admin,
    )
    db_key = created["button_key"]
    db_child = session.query(BotButtonSetting).filter(BotButtonSetting.button_key == db_key).one()
    db_child.instruction_text = "DB child instruction"
    db_child.system_text = "DB child system"
    db_child.system_text_overridden = True
    db_child.automation_key = "message_only"
    db_child.automation_overridden = True
    db_child.fixed_assignee_enabled = True
    db_child.fixed_assignee_user_id = "agent-db"
    db_child.files_json = ["db/direct.txt"]
    db_child.files_overridden = True
    db_child.conditional_files_json = [{"conditions": [{"field": "employee_type", "operator": "eq", "value": "staff"}], "files": ["db/rule.txt"]}]
    db_child.conditional_files_overridden = True
    db_child.confirmation_enabled = True
    db_child.confirmation_text = "Confirm DB child"
    db_child.confirmation_overridden = True
    db_child.label_override = "DB child visible"
    db_child.label_overridden = True
    session.commit()

    structure = settings_bot_button_structure._build_bot_menu_structure(session, "menu-a")
    db_child = session.query(BotButtonSetting).filter(BotButtonSetting.button_key == db_key).one()
    parent_node = structure.tree_rows[0]["buttons"][0]
    child_keys = [row["buttons"][0]["technical_key"] for row in parent_node["children_rows"]]

    assert db_child.currently_present is True
    assert db_child.is_archived is False
    assert child_keys == ["menu-a|group_task_child|Parent/Source child", db_key]
    assert len(child_keys) == len(set(child_keys))
    runtime_children = telegram_router._button_from_callback(session, "btn:source-parent", "menu-a").subtasks
    assert [child.label for child in runtime_children] == ["Source child", "DB child visible"]
    runtime_db_child = runtime_children[1]
    assert runtime_db_child.key == db_key
    assert runtime_db_child.kind == "task"
    assert runtime_db_child.instruction_text == "DB child instruction"
    assert runtime_db_child.system_text == "DB child system"
    assert runtime_db_child.automation_key == "message_only"
    assert runtime_db_child.forced_assignee_type == "user_id"
    assert runtime_db_child.forced_assignee_value == "agent-db"
    assert runtime_db_child.file_path == "db/direct.txt"
    assert runtime_db_child.file_rules == ({"conditions": [{"field": "employee_type", "operator": "eq", "value": "staff"}], "files": ["db/rule.txt"]},)
    assert runtime_db_child.confirmation_text == "Confirm DB child"

    deleted = settings_bot_button_items.delete_bot_button_setting(
        button_key=db_key,
        hard=False,
        db=session,
        user=admin,
    )
    structure_after_delete = settings_bot_button_structure._build_bot_menu_structure(session, "menu-a")
    db_child = session.query(BotButtonSetting).filter(BotButtonSetting.button_key == db_key).one()
    child_keys_after_delete = [row["buttons"][0]["technical_key"] for row in structure_after_delete.tree_rows[0]["buttons"][0]["children_rows"]]

    assert deleted["deleted"] is True
    assert db_child.currently_present is False
    assert db_child.is_archived is True
    assert child_keys_after_delete == ["menu-a|group_task_child|Parent/Source child"]

    restored = settings_bot_button_items.restore_bot_button_setting(
        button_key=db_key,
        db=session,
        user=admin,
    )
    structure_after_restore = settings_bot_button_structure._build_bot_menu_structure(session, "menu-a")
    db_child = session.query(BotButtonSetting).filter(BotButtonSetting.button_key == db_key).one()
    child_nodes_after_restore = [row["buttons"][0] for row in structure_after_restore.tree_rows[0]["buttons"][0]["children_rows"]]
    child_keys_after_restore = [node["technical_key"] for node in child_nodes_after_restore]

    assert restored["restored"] is True
    assert db_child.currently_present is True
    assert db_child.is_archived is False
    assert db_child.instruction_text == "DB child instruction"
    assert child_keys_after_restore == ["menu-a|group_task_child|Parent/Source child", db_key]
    assert len(child_keys_after_restore) == len(set(child_keys_after_restore))
    assert child_nodes_after_restore[1]["has_instruction"] is True


def test_group_task_child_creation_snapshots_suppress_client_answer_for_source_and_db_children(monkeypatch):
    session = _session()
    source_key = "menu-a|group_task_child|Parent/Source child"
    db_key = "menu-a|group_task_child|Parent/DB child"
    _seed_row(
        session,
        button_key=source_key,
        workflow_type="group_task_child",
        button_kind="terminal",
        button_path="Parent/Source child",
        button_label="Source child",
        suppress_client_answer=False,
    )
    _seed_row(
        session,
        button_key=db_key,
        workflow_type="group_task_child",
        button_kind="terminal",
        button_path="Parent/DB child",
        button_label="DB child",
        is_db_created=True,
        suppress_client_answer=True,
    )
    parent = Ticket(
        id="parent-ticket",
        company_id="company-1",
        client_id="client-1",
        subject="Parent",
        workflow_type=TicketWorkflowType.TASK.value,
        task_kind=TicketTaskKind.GROUP_PARENT.value,
        status=TicketStatus.PENDING.value,
    )
    session.add(parent)
    session.commit()
    monkeypatch.setattr(telegram_router, "_menu_name_for_ticket_client", lambda db, ticket: "menu-a")

    source_child = ButtonSpec(key="source-child", label="Source child", kind="task")
    db_child = telegram_router._button_from_db_row(session, session.query(BotButtonSetting).filter(BotButtonSetting.button_key == db_key).one(), "menu-a")
    group = ButtonSpec(key="parent", label="Parent", kind="group_task", subtasks=(source_child, db_child))

    telegram_router._create_group_children_from_collecting(session, parent, group, "client body", None)
    created_children = session.query(Ticket).filter(Ticket.parent_ticket_id == parent.id).order_by(Ticket.subject).all()
    snapshot_by_subject = {child.subject: child.suppress_client_answer for child in created_children}
    assert snapshot_by_subject == {"DB child": True, "Source child": False}

    session.query(BotButtonSetting).filter(BotButtonSetting.button_key == source_key).one().suppress_client_answer = True
    session.query(BotButtonSetting).filter(BotButtonSetting.button_key == db_key).one().suppress_client_answer = False
    session.commit()
    created_children = session.query(Ticket).filter(Ticket.parent_ticket_id == parent.id).order_by(Ticket.subject).all()
    assert {child.subject: child.suppress_client_answer for child in created_children} == snapshot_by_subject


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


def test_first_connection_values_ignore_legacy_client_text_override():
    session = _session()
    _seed_row(
        session,
        button_key=FIRST_CONNECTION_BUTTON_KEY,
        menu_type=SYSTEM_MENU_TYPE,
        button_kind="system",
        label_override="Custom subject",
        label_overridden=True,
        client_text="Старый override",
        client_text_overridden=True,
        system_text="Custom system",
        system_text_overridden=True,
    )

    values = first_connection_values(
        session,
        FirstConnectionDefaults(
            subject="Default subject",
            client_text="Текст из автоматических сообщений",
            system_text="Default system",
        ),
    )

    assert values == {
        "subject": "Custom subject",
        "client_text": "Текст из автоматических сообщений",
        "system_text": "Custom system",
    }


def test_review_system_buttons_api_defaults_save_and_validation():
    session = _session()
    admin = SimpleNamespace(id="admin-1", role="admin")

    payload = settings_bot_button_items.get_review_system_buttons(db=session, user=admin)
    assert [item["text"] for item in payload["buttons"]] == [
        "Оставить отзыв",
        "⭐",
        "⭐⭐",
        "⭐⭐⭐",
        "⭐⭐⭐⭐",
        "⭐⭐⭐⭐⭐",
        "Назад",
        "Завершить отзыв",
    ]
    assert all(item["button_style"] is None for item in payload["buttons"])

    saved = settings_bot_button_items.save_review_system_buttons(
        settings_bot_button_items.SystemButtonGroupUpdate(
            buttons=[
                {"key": "__system__|review|start", "text": "Начать отзыв", "button_style": "primary"},
                {"key": "__system__|review|rate_5", "text": "5 звёзд", "button_style": "success"},
            ]
        ),
        db=session,
        user=admin,
    )
    by_key = {item["key"]: item for item in saved["buttons"]}
    assert by_key["__system__|review|start"]["text"] == "Начать отзыв"
    assert by_key["__system__|review|start"]["button_style"] == "primary"
    assert by_key["__system__|review|rate_5"]["text"] == "5 звёзд"
    assert by_key["__system__|review|rate_5"]["button_style"] == "success"

    reloaded = settings_bot_button_items.get_review_system_buttons(db=session, user=admin)
    reloaded_by_key = {item["key"]: item for item in reloaded["buttons"]}
    assert reloaded_by_key["__system__|review|start"]["text"] == "Начать отзыв"

    with pytest.raises(HTTPException) as invalid_style:
        settings_bot_button_items.save_review_system_buttons(
            settings_bot_button_items.SystemButtonGroupUpdate(
                buttons=[{"key": "__system__|review|start", "text": "Начать отзыв", "button_style": "purple"}]
            ),
            db=session,
            user=admin,
        )
    assert invalid_style.value.status_code == 422

    with pytest.raises(HTTPException) as empty_text:
        settings_bot_button_items.save_review_system_buttons(
            settings_bot_button_items.SystemButtonGroupUpdate(
                buttons=[{"key": "__system__|review|start", "text": "   ", "button_style": None}]
            ),
            db=session,
            user=admin,
        )
    assert empty_text.value.status_code == 422


def test_feedback_system_buttons_api_defaults_save_and_reload():
    session = _session()
    admin = SimpleNamespace(id="admin-1", role="admin")

    payload = settings_bot_button_items.get_feedback_system_buttons(db=session, user=admin)
    assert [item["text"] for item in payload["buttons"]] == ["Предложения и замечания", "Назад"]

    settings_bot_button_items.save_feedback_system_buttons(
        settings_bot_button_items.SystemButtonGroupUpdate(
            buttons=[{"key": "__system__|feedback|back", "text": "Вернуться", "button_style": "danger"}]
        ),
        db=session,
        user=admin,
    )
    reloaded = settings_bot_button_items.get_feedback_system_buttons(db=session, user=admin)
    by_key = {item["key"]: item for item in reloaded["buttons"]}
    assert by_key["__system__|feedback|back"]["text"] == "Вернуться"
    assert by_key["__system__|feedback|back"]["button_style"] == "danger"


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


def _layout_config():
    buttons = {
        key: ButtonSpec(key=f"btn-{key.lower()}", label=key, kind="ticket", text=f"Text {key}")
        for key in ("A", "B", "C", "D")
    }
    config = ButtonConfig(
        menus={
            "root": MenuSpec(
                key="root",
                title="menu-a",
                rows=((buttons["A"].key,), (buttons["B"].key,), (buttons["C"].key,), (buttons["D"].key,)),
            )
        },
        buttons={button.key: button for button in buttons.values()},
        root_menu_key="root",
    )
    refs = tuple(
        TerminalButtonRef(
            source_button_key=button.key,
            label=button.label,
            workflow_type="ticket",
            full_path=button.label,
            technical_key=f"menu-a|ticket|{button.label}",
            instruction_text=None,
        )
        for button in buttons.values()
    )
    return config, refs


def _patch_layout_config(monkeypatch, config, refs, session=None):
    if session is not None:
        for ref in refs:
            if session.query(BotButtonSetting).filter_by(button_key=ref.technical_key).first() is None:
                _seed_row(session, button_key=ref.technical_key, workflow_type=ref.workflow_type,
                          button_path=ref.full_path, button_label=ref.label)
    monkeypatch.setattr(settings_bot_button_structure, "_require_admin", lambda user: None)


def test_save_bot_button_layout_persists_three_button_rows(monkeypatch):
    session = _session()
    config, refs = _layout_config()
    _patch_layout_config(monkeypatch, config, refs, session)
    level_key = "menu-a|level|__root__"

    result = settings_bot_button_structure.save_bot_button_level_layout(
        menu_type="menu-a",
        level_key=level_key,
        payload={
            "rows": [
                {"columns": 3, "button_keys": ["menu-a|ticket|C", "menu-a|ticket|A", "menu-a|ticket|B"]},
                {"columns": 1, "button_keys": ["menu-a|ticket|D"]},
            ]
        },
        db=session,
        user=SimpleNamespace(id="admin-1", role="admin"),
    )

    row = session.query(BotMenuLevelLayout).filter_by(menu_type="menu-a", level_key=level_key).one()
    structure = settings_bot_button_structure._build_bot_menu_structure(session, "menu-a")
    layout_labels = [[node["label"] for node in item["buttons"]] for item in structure.tree_rows]
    assert result["rows"][0]["button_keys"] == ["menu-a|ticket|C", "menu-a|ticket|A", "menu-a|ticket|B"]
    assert row.layout_json[0]["columns"] == 3
    assert layout_labels[:2] == [["C", "A", "B"], ["D"]]


def test_bot_button_layout_rejects_unknown_duplicate_and_too_wide_rows(monkeypatch):
    session = _session()
    config, refs = _layout_config()
    _patch_layout_config(monkeypatch, config, refs, session)
    level_key = "menu-a|level|__root__"
    cases = [
        ({"rows": [{"columns": 1, "button_keys": ["menu-a|ticket|Z"]}]}, "unknown_button_key"),
        ({"rows": [{"columns": 2, "button_keys": ["menu-a|ticket|A", "menu-a|ticket|A"]}]}, "duplicate_button_key"),
        (
            {"rows": [{"columns": 3, "button_keys": ["menu-a|ticket|A", "menu-a|ticket|B", "menu-a|ticket|C", "menu-a|ticket|D"]}]},
            "layout_row_too_wide",
        ),
    ]

    for payload, expected_code in cases:
        try:
            settings_bot_button_structure.save_bot_button_level_layout(
                menu_type="menu-a",
                level_key=level_key,
                payload=payload,
                db=session,
                user=SimpleNamespace(id="admin-1", role="admin"),
            )
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 422
            detail = getattr(exc, "detail", {}) or {}
            error = detail.get("error", {}) if isinstance(detail, dict) else {}
            assert error.get("code") == expected_code
        else:
            raise AssertionError(f"Expected layout validation error: {expected_code}")


def test_bot_button_layout_normalizes_empty_rows(monkeypatch):
    session = _session()
    config, refs = _layout_config()
    _patch_layout_config(monkeypatch, config, refs, session)

    result = settings_bot_button_structure.save_bot_button_level_layout(
        menu_type="menu-a",
        level_key="menu-a|level|__root__",
        payload={
            "rows": [
                {"columns": 1, "button_keys": []},
                ["menu-a|ticket|B"],
                [],
            ]
        },
        db=session,
        user=SimpleNamespace(id="admin-1", role="admin"),
    )

    assert result["rows"] == [{"columns": 1, "button_keys": ["menu-a|ticket|B"]}]
