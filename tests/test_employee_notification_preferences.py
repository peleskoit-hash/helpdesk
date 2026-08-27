from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import Company, EmployeeNotificationPreference, TelegramNotificationAccount, User
from api.app.routers.employee_notification_preferences import (
    EmployeeNotificationPreferencesPayload,
    get_my_notification_bot_preferences,
    restore_my_notification_bot_preferences_from_base,
    update_my_notification_bot_preferences,
)
from api.app.routers import settings_notification_bot_basic
from api.app.routers.settings_notification_bot_basic import NotificationBotBasicUpdatePayload
from api.app.services.automatic_messages import (
    get_notification_bot_basic_definition,
    notification_bot_basic_tag_labels_by_semantic,
    serialize_notification_bot_basic_template,
)


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def _user(session, user_id: str = "user-1"):
    company = Company(id=f"company-{user_id}", name="Company", is_active=True)
    user = User(
        id=user_id,
        company_id=company.id,
        email=None,
        login=user_id,
        password_hash="hash",
        full_name="User One",
        role="agent",
        is_active=True,
    )
    session.add(company)
    session.add(user)
    session.commit()
    return SimpleNamespace(id=user.id, role=user.role)


def _admin():
    return SimpleNamespace(id="admin-1", role="admin")


def _save_base(session, notification_type: str, template: str, **flags):
    return settings_notification_bot_basic.update_notification_bot_basic_template(
        notification_type,
        NotificationBotBasicUpdatePayload(
            enabled=True,
            template=template,
            html_enabled=flags.get("html_enabled", True),
            disable_link_preview=flags.get("disable_link_preview", False),
            send_silently=flags.get("send_silently", False),
        ),
        db=session,
        user=_admin(),
    )


def _payload(
    *,
    use_base_settings: bool | None = None,
    assignment_use_base: bool | None = None,
    message_use_base: bool | None = None,
    assignment: str = "Personal assignment",
    message: str = "Personal message",
    **delivery,
):
    assignment_base = use_base_settings if assignment_use_base is None else assignment_use_base
    message_base = use_base_settings if message_use_base is None else message_use_base
    return EmployeeNotificationPreferencesPayload(
        items=[
            {
                "type": "new_assignment",
                "use_base_settings": assignment_base is not False,
                "template": assignment,
                "html_enabled": True,
            },
            {
                "type": "new_message",
                "use_base_settings": message_base is not False,
                "template": message,
                "html_enabled": True,
            },
        ],
        delivery={
            "transfer_to_agent": delivery.get("transfer_to_agent", True),
            "transfer_from_agent": delivery.get("transfer_from_agent", True),
            "new_message": delivery.get("new_message", True),
        },
    )


def test_defaults_without_personal_row_and_base_mode_reflects_current_base(monkeypatch):
    monkeypatch.setattr(settings_notification_bot_basic, "_require_admin", lambda user: None)
    session = _session()
    user = _user(session)
    _save_base(session, "new_assignment", "Base A")
    _save_base(session, "new_message", "Base M", disable_link_preview=True, send_silently=True)

    result = get_my_notification_bot_preferences(db=session, user=user)

    assert {item["type"]: item["use_base_settings"] for item in result["items"]} == {
        "new_assignment": True,
        "new_message": True,
    }
    assert result["delivery"] == {
        "new_assignment": True,
        "transfer_to_agent": True,
        "transfer_from_agent": True,
        "new_message": True,
    }
    assert session.query(EmployeeNotificationPreference).count() == 0
    assert {item["type"]: item["template"] for item in result["items"]} == {
        "new_assignment": "Base A",
        "new_message": "Base M",
    }
    assert next(item for item in result["items"] if item["type"] == "new_message")["send_silently"] is True


def test_first_disable_inheritance_copies_base_once_and_admin_changes_do_not_override(monkeypatch):
    monkeypatch.setattr(settings_notification_bot_basic, "_require_admin", lambda user: None)
    session = _session()
    user = _user(session)
    _save_base(session, "new_assignment", "Base A v1", disable_link_preview=True)
    _save_base(session, "new_message", "Base M v1", send_silently=True)

    result = update_my_notification_bot_preferences(_payload(use_base_settings=False), db=session, user=user)
    row = session.query(EmployeeNotificationPreference).filter_by(user_id=user.id).one()

    assert {item["type"]: item["use_base_settings"] for item in result["items"]} == {
        "new_assignment": False,
        "new_message": False,
    }
    assert row.personal_new_assignment_template == "Base A v1"
    assert row.personal_new_assignment_disable_link_preview is True
    assert row.personal_new_message_template == "Base M v1"
    assert row.personal_new_message_send_silently is True

    update_my_notification_bot_preferences(
        _payload(use_base_settings=False, assignment="Personal assignment", message="Personal message"),
        db=session,
        user=user,
    )

    _save_base(session, "new_assignment", "Base A v2")
    _save_base(session, "new_message", "Base M v2")
    result = get_my_notification_bot_preferences(db=session, user=user)
    assert {item["type"]: item["template"] for item in result["items"]} == {
        "new_assignment": "Personal assignment",
        "new_message": "Personal message",
    }


def test_first_disable_only_assignment_leaves_message_uninitialized(monkeypatch):
    monkeypatch.setattr(settings_notification_bot_basic, "_require_admin", lambda user: None)
    session = _session()
    user = _user(session)
    _save_base(session, "new_assignment", "Base A v1")
    _save_base(session, "new_message", "Base M v1")

    result = update_my_notification_bot_preferences(
        _payload(assignment_use_base=False, message_use_base=True),
        db=session,
        user=user,
    )
    row = session.query(EmployeeNotificationPreference).filter_by(user_id=user.id).one()

    assert row.personal_new_assignment_template == "Base A v1"
    assert row.personal_new_message_template is None
    assert {item["type"]: item["use_base_settings"] for item in result["items"]} == {
        "new_assignment": False,
        "new_message": True,
    }
    assert {item["type"]: item["template"] for item in result["items"]} == {
        "new_assignment": "Base A v1",
        "new_message": "Base M v1",
    }


def test_first_disable_only_message_leaves_assignment_uninitialized(monkeypatch):
    monkeypatch.setattr(settings_notification_bot_basic, "_require_admin", lambda user: None)
    session = _session()
    user = _user(session)
    _save_base(session, "new_assignment", "Base A v1")
    _save_base(session, "new_message", "Base M v1")

    result = update_my_notification_bot_preferences(
        _payload(assignment_use_base=True, message_use_base=False),
        db=session,
        user=user,
    )
    row = session.query(EmployeeNotificationPreference).filter_by(user_id=user.id).one()

    assert row.personal_new_assignment_template is None
    assert row.personal_new_message_template == "Base M v1"
    assert {item["type"]: item["use_base_settings"] for item in result["items"]} == {
        "new_assignment": True,
        "new_message": False,
    }
    assert {item["type"]: item["template"] for item in result["items"]} == {
        "new_assignment": "Base A v1",
        "new_message": "Base M v1",
    }


def test_base_personal_base_personal_restores_existing_personal_without_second_copy(monkeypatch):
    monkeypatch.setattr(settings_notification_bot_basic, "_require_admin", lambda user: None)
    session = _session()
    user = _user(session)
    _save_base(session, "new_assignment", "Base A v1")
    _save_base(session, "new_message", "Base M v1")
    update_my_notification_bot_preferences(_payload(use_base_settings=False), db=session, user=user)
    update_my_notification_bot_preferences(_payload(use_base_settings=False, assignment="My A", message="My M"), db=session, user=user)

    update_my_notification_bot_preferences(_payload(use_base_settings=True), db=session, user=user)
    _save_base(session, "new_assignment", "Base A v2")
    _save_base(session, "new_message", "Base M v2")
    base_mode = get_my_notification_bot_preferences(db=session, user=user)
    assert {item["type"]: item["template"] for item in base_mode["items"]} == {
        "new_assignment": "Base A v2",
        "new_message": "Base M v2",
    }

    result = update_my_notification_bot_preferences(
        _payload(use_base_settings=False, assignment="Base A v2", message="Base M v2"),
        db=session,
        user=user,
    )

    assert {item["type"]: item["template"] for item in result["items"]} == {
        "new_assignment": "My A",
        "new_message": "My M",
    }


def test_assignment_personal_cycle_does_not_touch_message_base(monkeypatch):
    monkeypatch.setattr(settings_notification_bot_basic, "_require_admin", lambda user: None)
    session = _session()
    user = _user(session)
    _save_base(session, "new_assignment", "Base A v1")
    _save_base(session, "new_message", "Base M v1")
    update_my_notification_bot_preferences(_payload(assignment_use_base=False, message_use_base=True), db=session, user=user)
    update_my_notification_bot_preferences(
        _payload(assignment_use_base=False, message_use_base=True, assignment="My A"),
        db=session,
        user=user,
    )
    update_my_notification_bot_preferences(_payload(assignment_use_base=True, message_use_base=True), db=session, user=user)
    _save_base(session, "new_assignment", "Base A v2")
    _save_base(session, "new_message", "Base M v2")

    result = update_my_notification_bot_preferences(
        _payload(assignment_use_base=False, message_use_base=True, assignment="Base A v2", message="Base M v2"),
        db=session,
        user=user,
    )

    assert {item["type"]: item["use_base_settings"] for item in result["items"]} == {
        "new_assignment": False,
        "new_message": True,
    }
    assert {item["type"]: item["template"] for item in result["items"]} == {
        "new_assignment": "My A",
        "new_message": "Base M v2",
    }


def test_message_personal_cycle_does_not_touch_assignment_base(monkeypatch):
    monkeypatch.setattr(settings_notification_bot_basic, "_require_admin", lambda user: None)
    session = _session()
    user = _user(session)
    _save_base(session, "new_assignment", "Base A v1")
    _save_base(session, "new_message", "Base M v1")
    update_my_notification_bot_preferences(_payload(assignment_use_base=True, message_use_base=False), db=session, user=user)
    update_my_notification_bot_preferences(
        _payload(assignment_use_base=True, message_use_base=False, message="My M"),
        db=session,
        user=user,
    )
    update_my_notification_bot_preferences(_payload(assignment_use_base=True, message_use_base=True), db=session, user=user)
    _save_base(session, "new_assignment", "Base A v2")
    _save_base(session, "new_message", "Base M v2")

    result = update_my_notification_bot_preferences(
        _payload(assignment_use_base=True, message_use_base=False, assignment="Base A v2", message="Base M v2"),
        db=session,
        user=user,
    )

    assert {item["type"]: item["use_base_settings"] for item in result["items"]} == {
        "new_assignment": True,
        "new_message": False,
    }
    assert {item["type"]: item["template"] for item in result["items"]} == {
        "new_assignment": "Base A v2",
        "new_message": "My M",
    }


def test_restore_base_copies_current_base_and_keeps_personal_mode(monkeypatch):
    monkeypatch.setattr(settings_notification_bot_basic, "_require_admin", lambda user: None)
    session = _session()
    user = _user(session)
    update_my_notification_bot_preferences(_payload(use_base_settings=False), db=session, user=user)
    update_my_notification_bot_preferences(_payload(use_base_settings=False, assignment="Old A", message="Old M"), db=session, user=user)
    _save_base(session, "new_assignment", "Fresh A", disable_link_preview=True)
    _save_base(session, "new_message", "Fresh M", send_silently=True)

    result = restore_my_notification_bot_preferences_from_base(
        notification_type="new_assignment",
        db=session,
        user=user,
    )

    assert {item["type"]: item["use_base_settings"] for item in result["items"]} == {
        "new_assignment": False,
        "new_message": False,
    }
    assert {item["type"]: item["template"] for item in result["items"]} == {
        "new_assignment": "Fresh A",
        "new_message": "Old M",
    }
    assert next(item for item in result["items"] if item["type"] == "new_assignment")["disable_link_preview"] is True
    assert next(item for item in result["items"] if item["type"] == "new_message")["send_silently"] is False


def test_restore_message_copies_only_message(monkeypatch):
    monkeypatch.setattr(settings_notification_bot_basic, "_require_admin", lambda user: None)
    session = _session()
    user = _user(session)
    update_my_notification_bot_preferences(_payload(use_base_settings=False), db=session, user=user)
    update_my_notification_bot_preferences(_payload(use_base_settings=False, assignment="Old A", message="Old M"), db=session, user=user)
    _save_base(session, "new_assignment", "Fresh A")
    _save_base(session, "new_message", "Fresh M", send_silently=True)

    result = restore_my_notification_bot_preferences_from_base(
        notification_type="new_message",
        db=session,
        user=user,
    )

    assert {item["type"]: item["template"] for item in result["items"]} == {
        "new_assignment": "Old A",
        "new_message": "Fresh M",
    }
    row = session.query(EmployeeNotificationPreference).filter_by(user_id=user.id).one()
    assert row.personal_new_assignment_template == "Old A"
    assert row.personal_new_message_template == "Fresh M"
    assert row.personal_new_message_send_silently is True


def test_initial_copy_and_restore_keep_empty_disabled_base_as_initialized_snapshot(monkeypatch):
    monkeypatch.setattr(settings_notification_bot_basic, "_require_admin", lambda user: None)
    session = _session()
    user = _user(session)

    settings_notification_bot_basic.update_notification_bot_basic_template(
        "new_assignment",
        NotificationBotBasicUpdatePayload(
            enabled=False,
            template="",
            html_enabled=True,
            disable_link_preview=False,
            send_silently=False,
        ),
        db=session,
        user=_admin(),
    )
    settings_notification_bot_basic.update_notification_bot_basic_template(
        "new_message",
        NotificationBotBasicUpdatePayload(
            enabled=False,
            template="",
            html_enabled=True,
            disable_link_preview=False,
            send_silently=False,
        ),
        db=session,
        user=_admin(),
    )

    update_my_notification_bot_preferences(_payload(use_base_settings=False), db=session, user=user)
    row = session.query(EmployeeNotificationPreference).filter_by(user_id=user.id).one()
    assert row.personal_new_assignment_template == ""
    assert row.personal_new_message_template == ""

    update_my_notification_bot_preferences(_payload(use_base_settings=True), db=session, user=user)
    _save_base(session, "new_assignment", "Base A v2")
    _save_base(session, "new_message", "Base M v2")

    update_my_notification_bot_preferences(
        _payload(use_base_settings=False, assignment="Base A v2", message="Base M v2"),
        db=session,
        user=user,
    )
    row = session.query(EmployeeNotificationPreference).filter_by(user_id=user.id).one()
    assert row.personal_new_assignment_template == ""
    assert row.personal_new_message_template == ""

    settings_notification_bot_basic.update_notification_bot_basic_template(
        "new_assignment",
        NotificationBotBasicUpdatePayload(
            enabled=False,
            template="",
            html_enabled=True,
            disable_link_preview=False,
            send_silently=False,
        ),
        db=session,
        user=_admin(),
    )
    settings_notification_bot_basic.update_notification_bot_basic_template(
        "new_message",
        NotificationBotBasicUpdatePayload(
            enabled=False,
            template="",
            html_enabled=True,
            disable_link_preview=False,
            send_silently=False,
        ),
        db=session,
        user=_admin(),
    )
    restored = restore_my_notification_bot_preferences_from_base(
        notification_type="new_assignment",
        db=session,
        user=user,
    )
    row = session.query(EmployeeNotificationPreference).filter_by(user_id=user.id).one()
    assert {item["type"]: item["use_base_settings"] for item in restored["items"]} == {
        "new_assignment": False,
        "new_message": False,
    }
    assert row.personal_new_assignment_template == ""
    assert row.personal_new_message_template == ""


def test_delivery_switches_are_saved_independently_and_new_assignment_stays_true():
    session = _session()
    user = _user(session)

    result = update_my_notification_bot_preferences(
        _payload(
            use_base_settings=True,
            transfer_to_agent=False,
            transfer_from_agent=False,
            new_message=False,
        ),
        db=session,
        user=user,
    )

    assert {item["type"]: item["use_base_settings"] for item in result["items"]} == {
        "new_assignment": True,
        "new_message": True,
    }
    assert result["delivery"] == {
        "new_assignment": True,
        "transfer_to_agent": False,
        "transfer_from_agent": False,
        "new_message": False,
    }
    assert session.query(EmployeeNotificationPreference).filter_by(user_id=user.id).one().personal_new_assignment_template is None

    reloaded = get_my_notification_bot_preferences(db=session, user=user)
    assert reloaded["delivery"] == {
        "new_assignment": True,
        "transfer_to_agent": False,
        "transfer_from_agent": False,
        "new_message": False,
    }


def test_delivery_payload_cannot_disable_mandatory_new_assignment():
    with pytest.raises(ValidationError):
        EmployeeNotificationPreferencesPayload(
            items=[
                {"type": "new_assignment", "use_base_settings": True},
                {"type": "new_message", "use_base_settings": True},
            ],
            delivery={
                "new_assignment": False,
                "transfer_to_agent": True,
                "transfer_from_agent": True,
                "new_message": True,
            },
        )


def test_preferences_survive_without_telegram_binding_and_binding_status_has_no_technical_fields():
    session = _session()
    user = _user(session)
    update_my_notification_bot_preferences(_payload(use_base_settings=False), db=session, user=user)

    inactive = get_my_notification_bot_preferences(db=session, user=user)
    assert inactive["telegram_binding"] == {"active": False}

    account = TelegramNotificationAccount(
        id="acc-1",
        telegram_user_id="7001",
        telegram_chat_id="chat-1",
        telegram_username="secret",
        telegram_display_name="Secret Name",
        bound_user_id=user.id,
        request_status="approved",
        delivery_available=True,
        is_blocked=False,
        is_notification_bot_admin=False,
    )
    session.add(account)
    session.commit()
    active = get_my_notification_bot_preferences(db=session, user=user)
    assert active["telegram_binding"] == {"active": True}
    assert "telegram_username" not in str(active)
    assert "telegram_chat_id" not in str(active)
    assert "delivery_available" not in str(active)


def test_binding_status_is_active_for_approved_bound_blocked_and_undeliverable_accounts():
    session = _session()
    user = _user(session)

    blocked = TelegramNotificationAccount(
        id="acc-blocked",
        telegram_user_id="7002",
        telegram_chat_id="chat-2",
        telegram_username=None,
        telegram_display_name=None,
        bound_user_id=user.id,
        request_status="approved",
        delivery_available=True,
        is_blocked=True,
        is_notification_bot_admin=False,
    )
    session.add(blocked)
    session.commit()
    result = get_my_notification_bot_preferences(db=session, user=user)
    assert result["telegram_binding"] == {"active": True}

    session.delete(blocked)
    session.commit()

    undeliverable = TelegramNotificationAccount(
        id="acc-undeliverable",
        telegram_user_id="7003",
        telegram_chat_id="chat-3",
        telegram_username=None,
        telegram_display_name=None,
        bound_user_id=user.id,
        request_status="approved",
        delivery_available=False,
        is_blocked=False,
        is_notification_bot_admin=False,
    )
    session.add(undeliverable)
    session.commit()
    result = get_my_notification_bot_preferences(db=session, user=user)
    assert result["telegram_binding"] == {"active": True}


def test_binding_status_is_inactive_for_pending_unbound_account():
    session = _session()
    user = _user(session)
    pending = TelegramNotificationAccount(
        id="acc-pending",
        telegram_user_id="7004",
        telegram_chat_id="chat-4",
        telegram_username=None,
        telegram_display_name=None,
        bound_user_id=None,
        request_status="pending",
        delivery_available=False,
        is_blocked=False,
        is_notification_bot_admin=False,
    )
    session.add(pending)
    session.commit()

    result = get_my_notification_bot_preferences(db=session, user=user)

    assert result["telegram_binding"] == {"active": False}


def test_api_payload_forbids_user_id_and_cannot_edit_another_user():
    session = _session()
    user = _user(session, "user-1")
    other = _user(session, "user-2")

    with pytest.raises(ValidationError):
        EmployeeNotificationPreferencesPayload(
            user_id=other.id,
            items=[
                {"type": "new_assignment", "use_base_settings": True},
                {"type": "new_message", "use_base_settings": True},
            ],
            delivery={"transfer_to_agent": True, "transfer_from_agent": True, "new_message": True},
        )

    update_my_notification_bot_preferences(_payload(use_base_settings=False), db=session, user=user)
    assert session.query(EmployeeNotificationPreference).filter_by(user_id=user.id).count() == 1
    assert session.query(EmployeeNotificationPreference).filter_by(user_id=other.id).count() == 0


def test_invalid_markup_is_rejected_and_static_text_with_special_chars_is_allowed():
    session = _session()
    user = _user(session)

    with pytest.raises(HTTPException) as exc:
        update_my_notification_bot_preferences(_payload(use_base_settings=False), db=session, user=user)
        update_my_notification_bot_preferences(
            _payload(use_base_settings=False, assignment="<b>broken", message="ok"),
            db=session,
            user=user,
        )
    assert exc.value.status_code == 422

    result = update_my_notification_bot_preferences(
        _payload(use_base_settings=False, assignment='Обычный текст < > & "кавычки"', message="ok"),
        db=session,
        user=user,
    )
    assert next(item for item in result["items"] if item["type"] == "new_assignment")["template"] == 'Обычный текст < > & "кавычки"'


def test_personal_page_uses_same_semantic_tag_registry_with_personal_labels():
    session = _session()
    user = _user(session)

    personal = get_my_notification_bot_preferences(db=session, user=user)
    by_type = {item["type"]: item for item in personal["items"]}
    admin_labels = notification_bot_basic_tag_labels_by_semantic(context="admin")
    personal_labels = notification_bot_basic_tag_labels_by_semantic(context="personal")

    assert admin_labels["recipient_telegram_username"] == "[TG @username адресата]"
    assert personal_labels["recipient_telegram_username"] == "[Мой TG @username]"

    for notification_type in ("new_assignment", "new_message"):
        admin_item = serialize_notification_bot_basic_template(
            session,
            get_notification_bot_basic_definition(notification_type),
        )
        assert "[TG @username адресата]" in admin_item["tags"]
        assert "[Мой TG @username]" not in admin_item["tags"]
        assert "[Мой TG @username]" in by_type[notification_type]["tags"]
        assert "[TG @username адресата]" not in by_type[notification_type]["tags"]


def test_personal_context_normalizes_admin_semantic_label_in_template_and_snapshot(monkeypatch):
    monkeypatch.setattr(settings_notification_bot_basic, "_require_admin", lambda user: None)
    session = _session()
    user = _user(session)
    _save_base(session, "new_assignment", "A [TG @username адресата]")
    _save_base(session, "new_message", "M [TG @username адресата]")

    payload = get_my_notification_bot_preferences(db=session, user=user)
    by_type = {item["type"]: item for item in payload["items"]}
    assert by_type["new_assignment"]["template"] == "A [Мой TG @username]"
    assert by_type["new_message"]["template"] == "M [Мой TG @username]"
    assert "[TG @username адресата]" not in by_type["new_assignment"]["template"]
    assert "[TG @username адресата]" not in by_type["new_message"]["template"]
    assert "[Мой TG @username]" in by_type["new_assignment"]["tags"]

    update_my_notification_bot_preferences(_payload(use_base_settings=False), db=session, user=user)
    row = session.query(EmployeeNotificationPreference).filter_by(user_id=user.id).one()
    assert row.personal_new_assignment_template == "A [Мой TG @username]"
    assert row.personal_new_message_template == "M [Мой TG @username]"


def test_restore_base_keeps_personal_semantic_label_for_snapshot(monkeypatch):
    monkeypatch.setattr(settings_notification_bot_basic, "_require_admin", lambda user: None)
    session = _session()
    user = _user(session)
    update_my_notification_bot_preferences(_payload(use_base_settings=False), db=session, user=user)
    _save_base(session, "new_assignment", "A2 [TG @username адресата]")
    _save_base(session, "new_message", "M2 [TG @username адресата]")

    restore_my_notification_bot_preferences_from_base(notification_type="new_assignment", db=session, user=user)
    restore_my_notification_bot_preferences_from_base(notification_type="new_message", db=session, user=user)
    row = session.query(EmployeeNotificationPreference).filter_by(user_id=user.id).one()

    assert row.personal_new_assignment_template == "A2 [Мой TG @username]"
    assert row.personal_new_message_template == "M2 [Мой TG @username]"
