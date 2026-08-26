from __future__ import annotations

from datetime import datetime
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import Company, TelegramNotificationAccount, User
from api.app.services import employee_telegram_access, employee_telegram_bot, employee_telegram_polling
from api.app.services.telegram_employee_binding import REQUEST_STATUS_APPROVED, REQUEST_STATUS_PENDING


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def _company(session) -> Company:
    company = Company(id=str(uuid.uuid4()), name="ACME", is_active=True, created_at=datetime.utcnow())
    session.add(company)
    session.commit()
    return company


def _account(
    session,
    *,
    telegram_user_id: str,
    telegram_chat_id: str,
    request_status: str,
    bound_user_id: str | None,
    delivery_available: bool,
    is_blocked: bool = False,
    telegram_username: str = "worker",
    telegram_display_name: str = "Worker",
    is_notification_bot_admin: bool = False,
) -> TelegramNotificationAccount:
    now = datetime.utcnow()
    account = TelegramNotificationAccount(
        id=str(uuid.uuid4()),
        telegram_user_id=telegram_user_id,
        telegram_chat_id=telegram_chat_id,
        telegram_username=telegram_username,
        telegram_display_name=telegram_display_name,
        bound_user_id=bound_user_id,
        request_status=request_status,
        delivery_available=delivery_available,
        is_blocked=is_blocked,
        is_notification_bot_admin=is_notification_bot_admin,
        created_at=now,
        updated_at=now,
    )
    session.add(account)
    session.commit()
    return account


def _bound_user(session, *, company_id: str, user_id: str, is_active: bool = True) -> User:
    now = datetime.utcnow()
    user = User(
        id=user_id,
        company_id=company_id,
        email=f"{user_id}@example.test",
        login=user_id,
        password_hash="hash",
        full_name=user_id,
        role="agent",
        is_active=is_active,
        availability_status="online" if is_active else "offline",
        legacy_ticket_count=0,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    session.commit()
    return user


def _callback_data(markup: dict | None) -> list[str]:
    if not markup:
        return []
    return [
        button["callback_data"]
        for row in markup.get("inline_keyboard", [])
        for button in row
        if "callback_data" in button
    ]


def test_process_employee_telegram_update_creates_pending_request_on_start(monkeypatch):
    session = _session()
    _company(session)
    send_calls: list[tuple[str, str, str | None]] = []

    def fake_send_message(chat_id, text, **kwargs):
        send_calls.append((chat_id, text, kwargs.get("bot_token")))
        return {"ok": True}

    monkeypatch.setattr(employee_telegram_bot.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_bot, "send_message", fake_send_message)

    result = employee_telegram_bot.process_employee_telegram_update(
        session,
        {
            "update_id": 101,
            "message": {
                "message_id": 1,
                "text": "/start",
                "chat": {"id": 5001, "type": "private"},
                "from": {
                    "id": 7001,
                    "username": "worker7001",
                    "first_name": "Ivan",
                    "last_name": "Petrov",
                },
            },
        },
    )

    assert result["action"] == "binding_requested"
    saved = session.query(TelegramNotificationAccount).filter_by(telegram_user_id="7001").one()
    assert saved.bound_user_id is None
    assert saved.request_status == "pending"
    assert saved.delivery_available is False
    assert saved.telegram_chat_id == "5001"
    assert saved.telegram_username == "worker7001"
    assert saved.telegram_display_name == "Ivan Petrov"
    assert send_calls == [("5001", employee_telegram_bot.EMPLOYEE_BINDING_ACK_TEXT, "employee-token")]


def test_process_employee_telegram_update_creates_pending_request_on_start_button(monkeypatch):
    session = _session()
    _company(session)
    send_calls: list[tuple[str, str, dict | None]] = []

    def fake_send_message(chat_id, text, **kwargs):
        send_calls.append((chat_id, text, kwargs.get("reply_markup")))
        return {"ok": True}

    monkeypatch.setattr(employee_telegram_bot.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_bot, "send_message", fake_send_message)

    result = employee_telegram_bot.process_employee_telegram_update(
        session,
        {
            "update_id": 1011,
            "message": {
                "message_id": 11,
                "text": employee_telegram_bot.EMPLOYEE_START_BUTTON_TEXT,
                "chat": {"id": 5011, "type": "private"},
                "from": {"id": 7011, "username": "worker7011", "first_name": "Button"},
            },
        },
    )

    saved = session.query(TelegramNotificationAccount).filter_by(telegram_user_id="7011").one()
    assert result["action"] == "binding_requested"
    assert saved.request_status == REQUEST_STATUS_PENDING
    assert saved.bound_user_id is None
    assert send_calls == [
        ("5011", employee_telegram_bot.EMPLOYEE_BINDING_ACK_TEXT, employee_telegram_bot._employee_start_reply_markup())
    ]


def test_process_employee_telegram_update_does_not_reset_approved_binding_on_repeat_start(monkeypatch):
    session = _session()
    _company(session)
    existing = _account(
        session,
        telegram_user_id="7003",
        telegram_chat_id="5003",
        request_status=REQUEST_STATUS_APPROVED,
        bound_user_id="user-7003",
        delivery_available=True,
        telegram_username="worker7003",
        telegram_display_name="Approved Worker",
    )
    original_updated_at = existing.updated_at
    send_calls: list[tuple[str, str, str | None, dict | None]] = []

    def fake_send_message(chat_id, text, **kwargs):
        send_calls.append((chat_id, text, kwargs.get("bot_token"), kwargs.get("reply_markup")))
        return {"ok": True}

    monkeypatch.setattr(employee_telegram_bot.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_bot, "send_message", fake_send_message)

    result = employee_telegram_bot.process_employee_telegram_update(
        session,
        {
            "update_id": 103,
            "message": {
                "message_id": 3,
                "text": "/start",
                "chat": {"id": 5003, "type": "private"},
                "from": {
                    "id": 7003,
                    "username": "worker7003",
                    "first_name": "Approved",
                    "last_name": "Worker",
                },
            },
        },
    )

    saved = session.query(TelegramNotificationAccount).filter_by(telegram_user_id="7003").one()
    assert result["action"] == "menu_shown"
    assert saved.id == existing.id
    assert saved.bound_user_id == "user-7003"
    assert saved.request_status == REQUEST_STATUS_APPROVED
    assert saved.delivery_available is True
    assert saved.updated_at >= original_updated_at
    assert session.query(TelegramNotificationAccount).count() == 1
    assert send_calls == [
        (
            "5003",
            employee_telegram_bot.EMPLOYEE_START_PROMPT_TEXT,
            "employee-token",
            employee_telegram_bot._employee_start_reply_markup(),
        ),
        (
            "5003",
            employee_telegram_bot.EMPLOYEE_MENU_TEXT,
            "employee-token",
            {"inline_keyboard": [[{"text": "Моя привязка", "callback_data": "n:mb"}]]},
        )
    ]


def test_process_employee_telegram_update_does_not_duplicate_pending_request_on_repeat_start(monkeypatch):
    session = _session()
    _company(session)
    existing = _account(
        session,
        telegram_user_id="7004",
        telegram_chat_id="5004",
        request_status=REQUEST_STATUS_PENDING,
        bound_user_id=None,
        delivery_available=False,
        telegram_username="worker7004",
        telegram_display_name="Pending Worker",
    )
    original_updated_at = existing.updated_at
    send_calls: list[tuple[str, str, str | None, dict | None]] = []

    def fake_send_message(chat_id, text, **kwargs):
        send_calls.append((chat_id, text, kwargs.get("bot_token"), kwargs.get("reply_markup")))
        return {"ok": True}

    monkeypatch.setattr(employee_telegram_bot.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_bot, "send_message", fake_send_message)

    result = employee_telegram_bot.process_employee_telegram_update(
        session,
        {
            "update_id": 104,
            "message": {
                "message_id": 4,
                "text": "/start",
                "chat": {"id": 5004, "type": "private"},
                "from": {
                    "id": 7004,
                    "username": "worker7004",
                    "first_name": "Pending",
                    "last_name": "Worker",
                },
            },
        },
    )

    saved = session.query(TelegramNotificationAccount).filter_by(telegram_user_id="7004").one()
    assert result["action"] == "binding_pending"
    assert saved.id == existing.id
    assert saved.bound_user_id is None
    assert saved.request_status == REQUEST_STATUS_PENDING
    assert saved.delivery_available is False
    assert saved.updated_at >= original_updated_at
    assert session.query(TelegramNotificationAccount).count() == 1
    assert send_calls == [
        ("5004", employee_telegram_bot.EMPLOYEE_PENDING_TEXT, "employee-token", employee_telegram_bot._employee_start_reply_markup())
    ]


def test_process_employee_telegram_update_legacy_rejected_blocked_account_becomes_clean_pending(monkeypatch):
    session = _session()
    _company(session)
    existing = _account(
        session,
        telegram_user_id="7005",
        telegram_chat_id="5005",
        request_status="rejected",
        bound_user_id=None,
        delivery_available=False,
        is_blocked=True,
        telegram_username="worker7005",
        telegram_display_name="Blocked Worker",
    )
    original_updated_at = existing.updated_at

    send_calls: list[tuple[str, str, str | None, dict | None]] = []

    def fake_send_message(chat_id, text, **kwargs):
        send_calls.append((chat_id, text, kwargs.get("bot_token"), kwargs.get("reply_markup")))
        return {"ok": True}

    monkeypatch.setattr(employee_telegram_bot.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_bot, "send_message", fake_send_message)

    result = employee_telegram_bot.process_employee_telegram_update(
        session,
        {
            "update_id": 105,
            "message": {
                "message_id": 5,
                "text": "/start",
                "chat": {"id": 5005, "type": "private"},
                "from": {
                    "id": 7005,
                    "username": "worker7005",
                    "first_name": "Blocked",
                    "last_name": "Worker",
                },
            },
        },
    )

    saved = session.query(TelegramNotificationAccount).filter_by(telegram_user_id="7005").one()
    assert result["action"] == "binding_requested"
    assert saved.id == existing.id
    assert saved.is_blocked is False
    assert saved.request_status == REQUEST_STATUS_PENDING
    assert saved.bound_user_id is None
    assert saved.delivery_available is False
    assert saved.is_notification_bot_admin is False
    assert saved.updated_at >= original_updated_at
    assert session.query(TelegramNotificationAccount).count() == 1
    assert send_calls == [
        ("5005", employee_telegram_bot.EMPLOYEE_BINDING_ACK_TEXT, "employee-token", employee_telegram_bot._employee_start_reply_markup())
    ]


def test_process_employee_telegram_update_start_button_shows_user_menu_without_admin_items(monkeypatch):
    session = _session()
    company = _company(session)
    _bound_user(session, company_id=company.id, user_id="user-7006", is_active=True)
    _account(
        session,
        telegram_user_id="7006",
        telegram_chat_id="5006",
        request_status=REQUEST_STATUS_APPROVED,
        bound_user_id="user-7006",
        delivery_available=True,
    )
    send_calls: list[tuple[str, str, str | None, dict | None]] = []

    def fake_send_message(chat_id, text, **kwargs):
        send_calls.append((chat_id, text, kwargs.get("bot_token"), kwargs.get("reply_markup")))
        return {"ok": True}

    monkeypatch.setattr(employee_telegram_bot.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_bot, "send_message", fake_send_message)

    result = employee_telegram_bot.process_employee_telegram_update(
        session,
        {
            "update_id": 106,
            "message": {
                "message_id": 6,
                "text": employee_telegram_bot.EMPLOYEE_START_BUTTON_TEXT,
                "chat": {"id": 5006, "type": "private"},
                "from": {"id": 7006, "username": "worker7006", "first_name": "User"},
            },
        },
    )

    markup = send_calls[0][3]
    button_texts = [button["text"] for row in markup["inline_keyboard"] for button in row]
    assert result["action"] == "menu_shown"
    assert result["bot_role"] == employee_telegram_access.BOT_ROLE_USER
    assert button_texts == ["Моя привязка"]


def test_process_employee_telegram_update_start_button_shows_admin_menu(monkeypatch):
    session = _session()
    company = _company(session)
    _bound_user(session, company_id=company.id, user_id="user-7007", is_active=True)
    admin_account = _account(
        session,
        telegram_user_id="7007",
        telegram_chat_id="5007",
        request_status=REQUEST_STATUS_APPROVED,
        bound_user_id="user-7007",
        delivery_available=True,
    )
    admin_account.is_notification_bot_admin = True
    session.commit()
    send_calls: list[tuple[str, str, str | None, dict | None]] = []

    def fake_send_message(chat_id, text, **kwargs):
        send_calls.append((chat_id, text, kwargs.get("bot_token"), kwargs.get("reply_markup")))
        return {"ok": True}

    monkeypatch.setattr(employee_telegram_bot.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_bot, "send_message", fake_send_message)

    result = employee_telegram_bot.process_employee_telegram_update(
        session,
        {
            "update_id": 107,
            "message": {
                "message_id": 7,
                "text": employee_telegram_bot.EMPLOYEE_START_BUTTON_TEXT,
                "chat": {"id": 5007, "type": "private"},
                "from": {"id": 7007, "username": "worker7007", "first_name": "Admin"},
            },
        },
    )

    markup = send_calls[0][3]
    button_texts = [button["text"] for row in markup["inline_keyboard"] for button in row]
    assert result["action"] == "menu_shown"
    assert result["bot_role"] == employee_telegram_access.BOT_ROLE_ADMIN
    assert button_texts == ["Новые запросы", "Привязанные пользователи", "Заблокированные", "Моя привязка"]


def test_process_employee_telegram_update_start_button_shows_owner_menu_with_pending_count(monkeypatch):
    session = _session()
    _company(session)
    _account(
        session,
        telegram_user_id="7010",
        telegram_chat_id="5010",
        request_status=REQUEST_STATUS_PENDING,
        bound_user_id=None,
        delivery_available=False,
    )
    _account(
        session,
        telegram_user_id="7011",
        telegram_chat_id="5011",
        request_status=REQUEST_STATUS_PENDING,
        bound_user_id=None,
        delivery_available=False,
    )
    send_calls: list[tuple[str, str, str | None, dict | None]] = []

    def fake_send_message(chat_id, text, **kwargs):
        send_calls.append((chat_id, text, kwargs.get("bot_token"), kwargs.get("reply_markup")))
        return {"ok": True}

    monkeypatch.setattr(employee_telegram_bot.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_bot.settings, "telegram_notification_bot_owner_id", "9001")
    monkeypatch.setattr(employee_telegram_bot, "send_message", fake_send_message)

    result = employee_telegram_bot.process_employee_telegram_update(
        session,
        {
            "update_id": 108,
            "message": {
                "message_id": 8,
                "text": employee_telegram_bot.EMPLOYEE_START_BUTTON_TEXT,
                "chat": {"id": 5008, "type": "private"},
                "from": {"id": 9001, "username": "owner9001", "first_name": "Owner"},
            },
        },
    )

    markup = send_calls[0][3]
    button_texts = [button["text"] for row in markup["inline_keyboard"] for button in row]
    assert result["action"] == "menu_shown"
    assert result["bot_role"] == employee_telegram_access.BOT_ROLE_OWNER
    assert button_texts == ["Новые запросы (2)", "Привязанные пользователи", "Заблокированные", "Администраторы"]


def test_process_employee_telegram_update_modern_blocked_binding_stays_blocked(monkeypatch):
    session = _session()
    company = _company(session)
    user = _bound_user(session, company_id=company.id, user_id="user-7012", is_active=True)
    existing = _account(
        session,
        telegram_user_id="7012",
        telegram_chat_id="5012",
        request_status=REQUEST_STATUS_APPROVED,
        bound_user_id=user.id,
        delivery_available=False,
        is_blocked=True,
        telegram_username="old7012",
        telegram_display_name="Old 7012",
    )
    send_calls: list[tuple[str, str, str | None, dict | None]] = []

    def fake_send_message(chat_id, text, **kwargs):
        send_calls.append((chat_id, text, kwargs.get("bot_token"), kwargs.get("reply_markup")))
        return {"ok": True}

    monkeypatch.setattr(employee_telegram_bot.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_bot, "send_message", fake_send_message)

    result = employee_telegram_bot.process_employee_telegram_update(
        session,
        {
            "update_id": 109,
            "message": {
                "message_id": 9,
                "text": "/start",
                "chat": {"id": 5012, "type": "private"},
                "from": {"id": 7012, "username": "new7012", "first_name": "New", "last_name": "Blocked"},
            },
        },
    )

    session.refresh(existing)
    assert result["action"] == "binding_blocked"
    assert existing.request_status == REQUEST_STATUS_APPROVED
    assert existing.is_blocked is True
    assert existing.bound_user_id == user.id
    assert existing.delivery_available is False
    assert existing.telegram_username == "new7012"
    assert existing.telegram_display_name == "New Blocked"
    assert send_calls == [
        ("5012", employee_telegram_bot.EMPLOYEE_BLOCKED_TEXT, "employee-token", employee_telegram_bot._employee_start_reply_markup())
    ]


def test_process_employee_telegram_update_restores_delivery_and_updates_metadata_for_approved_binding(monkeypatch):
    session = _session()
    company = _company(session)
    user = _bound_user(session, company_id=company.id, user_id="user-7013", is_active=True)
    existing = _account(
        session,
        telegram_user_id="7013",
        telegram_chat_id="old-chat",
        request_status=REQUEST_STATUS_APPROVED,
        bound_user_id=user.id,
        delivery_available=False,
        telegram_username="old7013",
        telegram_display_name="Old 7013",
    )
    send_calls: list[tuple[str, str, str | None, dict | None]] = []

    def fake_send_message(chat_id, text, **kwargs):
        send_calls.append((chat_id, text, kwargs.get("bot_token"), kwargs.get("reply_markup")))
        return {"ok": True}

    monkeypatch.setattr(employee_telegram_bot.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_bot, "send_message", fake_send_message)

    result = employee_telegram_bot.process_employee_telegram_update(
        session,
        {
            "update_id": 110,
            "message": {
                "message_id": 10,
                "text": "/start",
                "chat": {"id": 5513, "type": "private"},
                "from": {"id": 7013, "username": "new7013", "first_name": "New", "last_name": "Name"},
            },
        },
    )

    session.refresh(existing)
    assert result["action"] == "menu_shown"
    assert existing.delivery_available is True
    assert existing.telegram_chat_id == "5513"
    assert existing.telegram_username == "new7013"
    assert existing.telegram_display_name == "New Name"
    assert send_calls[0] == (
        "5513",
        employee_telegram_bot.EMPLOYEE_START_PROMPT_TEXT,
        "employee-token",
        employee_telegram_bot._employee_start_reply_markup(),
    )


def test_process_employee_telegram_update_start_sets_reply_keyboard_for_owner(monkeypatch):
    session = _session()
    _company(session)
    send_calls: list[tuple[str, str, str | None, dict | None]] = []

    def fake_send_message(chat_id, text, **kwargs):
        send_calls.append((chat_id, text, kwargs.get("bot_token"), kwargs.get("reply_markup")))
        return {"ok": True}

    monkeypatch.setattr(employee_telegram_bot.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_bot.settings, "telegram_notification_bot_owner_id", "9001")
    monkeypatch.setattr(employee_telegram_bot, "send_message", fake_send_message)

    result = employee_telegram_bot.process_employee_telegram_update(
        session,
        {
            "update_id": 111,
            "message": {
                "message_id": 11,
                "text": "/start",
                "chat": {"id": 5111, "type": "private"},
                "from": {"id": 9001, "username": "owner9001", "first_name": "Owner"},
            },
        },
    )

    assert result["action"] == "menu_shown"
    assert send_calls[0] == (
        "5111",
        employee_telegram_bot.EMPLOYEE_START_PROMPT_TEXT,
        "employee-token",
        employee_telegram_bot._employee_start_reply_markup(),
    )
    assert send_calls[1][1] == employee_telegram_bot.EMPLOYEE_MENU_TEXT


def test_process_employee_telegram_update_ignores_unsupported_messages(monkeypatch):
    session = _session()
    _company(session)

    def fail_send_message(*args, **kwargs):
        raise AssertionError("send_message should not be called for unsupported command")

    monkeypatch.setattr(employee_telegram_bot.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_bot, "send_message", fail_send_message)

    result = employee_telegram_bot.process_employee_telegram_update(
        session,
        {
            "update_id": 102,
            "message": {
                "message_id": 2,
                "text": "hello",
                "chat": {"id": 5002, "type": "private"},
                "from": {"id": 7002, "username": "worker7002", "first_name": "Petr"},
            },
        },
    )

    assert result == {"ok": True, "ignored": True, "reason": "unsupported_command"}
    assert session.query(TelegramNotificationAccount).count() == 0


def _callback_update(*, callback_id: str = "test-callback-1", telegram_user_id: str = "9001", chat_id: str = "5001", data: str) -> dict:
    return {
        "update_id": 301,
        "callback_query": {
            "id": callback_id,
            "from": {"id": int(telegram_user_id), "username": f"user{telegram_user_id}"},
            "message": {"message_id": 1, "chat": {"id": int(chat_id), "type": "private"}},
            "data": data,
        },
    }


def test_callback_new_requests_paginates_twenty_users_and_answers(monkeypatch):
    session = _session()
    _company(session)
    for index in range(21):
        _account(
            session,
            telegram_user_id=f"710{index}",
            telegram_chat_id=f"510{index}",
            request_status=REQUEST_STATUS_PENDING,
            bound_user_id=None,
            delivery_available=False,
            telegram_display_name=f"Pending {index:02d}",
        )
    send_calls: list[tuple[str, str, dict | None]] = []
    answered: list[tuple[str, str | None, str | None]] = []

    def fake_send_message(chat_id, text, **kwargs):
        send_calls.append((chat_id, text, kwargs.get("reply_markup")))
        return {"ok": True}

    monkeypatch.setattr(employee_telegram_bot.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_bot.settings, "telegram_notification_bot_owner_id", "9001")
    monkeypatch.setattr(employee_telegram_bot, "send_message", fake_send_message)
    monkeypatch.setattr(
        employee_telegram_bot,
        "answer_callback_query",
        lambda callback_id, text=None, **kwargs: answered.append((callback_id, text, kwargs.get("bot_token"))) or {"ok": True},
    )

    result = employee_telegram_bot.process_employee_telegram_update(
        session,
        _callback_update(data="n:nr:0"),
    )

    buttons = [button for row in send_calls[0][2]["inline_keyboard"] for button in row]
    pending_buttons = [button for button in buttons if button["callback_data"].startswith("n:rq:")]
    assert result["action"] == "new_requests_shown"
    assert len(pending_buttons) == 20
    assert any(button["text"] == "След. страница" for button in buttons)
    assert answered == [("test-callback-1", None, "employee-token")]


def test_all_generated_notification_bot_callback_data_fit_telegram_limit(monkeypatch):
    session = _session()
    company = _company(session)
    pending_account = _account(
        session,
        telegram_user_id="7601",
        telegram_chat_id="5601",
        request_status=REQUEST_STATUS_PENDING,
        bound_user_id=None,
        delivery_available=False,
    )
    active_user = _bound_user(session, company_id=company.id, user_id=str(uuid.uuid4()), is_active=True)
    active_account = _account(
        session,
        telegram_user_id="7602",
        telegram_chat_id="5602",
        request_status=REQUEST_STATUS_APPROVED,
        bound_user_id=active_user.id,
        delivery_available=True,
    )
    blocked_user = _bound_user(session, company_id=company.id, user_id=str(uuid.uuid4()), is_active=True)
    blocked_account = _account(
        session,
        telegram_user_id="7603",
        telegram_chat_id="5603",
        request_status=REQUEST_STATUS_APPROVED,
        bound_user_id=blocked_user.id,
        delivery_available=False,
        is_blocked=True,
    )
    admin_user = _bound_user(session, company_id=company.id, user_id=str(uuid.uuid4()), is_active=True)
    admin_account = _account(
        session,
        telegram_user_id="7604",
        telegram_chat_id="5604",
        request_status=REQUEST_STATUS_APPROVED,
        bound_user_id=admin_user.id,
        delivery_available=True,
        is_notification_bot_admin=True,
    )
    candidate_user = _bound_user(session, company_id=company.id, user_id=str(uuid.uuid4()), is_active=True)
    candidate_account = _account(
        session,
        telegram_user_id="7605",
        telegram_chat_id="5605",
        request_status=REQUEST_STATUS_APPROVED,
        bound_user_id=candidate_user.id,
        delivery_available=True,
    )
    available_user = _bound_user(session, company_id=company.id, user_id=str(uuid.uuid4()), is_active=True)
    available_account = session.get(User, available_user.id)
    assert available_account is not None
    session.query(TelegramNotificationAccount).filter_by(bound_user_id=available_user.id).delete()
    session.commit()
    send_calls: list[dict | None] = []

    monkeypatch.setattr(employee_telegram_bot.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_bot.settings, "telegram_notification_bot_owner_id", "9001")
    monkeypatch.setattr(
        employee_telegram_bot,
        "send_message",
        lambda _chat_id, _text, **kwargs: send_calls.append(kwargs.get("reply_markup")) or {"ok": True},
    )

    employee_telegram_bot._send_employee_role_menu(session, "5001", employee_telegram_access.EmployeeTelegramAccess("9001", employee_telegram_access.BOT_ROLE_OWNER, None))
    employee_telegram_bot._show_new_requests(session, "5001", 0)
    employee_telegram_bot._show_request_card(session, "5001", pending_account.id)
    employee_telegram_bot._show_bind_user_list(session, "5001", pending_account.id, 0)
    selected_user_ref = employee_telegram_bot._uuid_to_ref(available_user.id)
    employee_telegram_bot._show_bind_confirmation(session, "5001", pending_account.id, selected_user_ref)
    employee_telegram_bot._reject_request_prompt(session, "5001", pending_account.id)
    employee_telegram_bot._show_bound_users(session, "5001", 0)
    employee_telegram_bot._show_bound_card(session, "5001", active_account.id)
    employee_telegram_bot._block_prompt(session, "5001", active_account.id)
    employee_telegram_bot._unbind_prompt(session, "5001", active_account.id)
    employee_telegram_bot._show_blocked_users(session, "5001", 0)
    employee_telegram_bot._show_blocked_card(session, "5001", blocked_account.id)
    employee_telegram_bot._unblock_prompt(session, "5001", blocked_account.id)
    employee_telegram_bot._show_admins(session, "5001", 0)
    employee_telegram_bot._show_admin_card(session, "5001", admin_account.id)
    employee_telegram_bot._admin_revoke_prompt(session, "5001", admin_account.id)
    employee_telegram_bot._show_admin_candidates(session, "5001", 0)
    employee_telegram_bot._admin_grant_prompt(session, "5001", candidate_account.id)
    access = employee_telegram_access.resolve_employee_telegram_access(session, candidate_account.telegram_user_id)
    employee_telegram_bot._show_my_binding(session, "5001", access)
    employee_telegram_bot._self_unbind_prompt(session, "5001", access)

    all_callback_data = [data for markup in send_calls for data in _callback_data(markup)]
    assert all_callback_data
    assert all(len(data.encode("utf-8")) <= 64 for data in all_callback_data)
    assert max(len(data.encode("utf-8")) for data in all_callback_data) <= 64
    bind_list_markup = next(markup for markup in send_calls if markup and any(data.startswith("n:rs:") for data in _callback_data(markup)))
    assert all(len(data.encode("utf-8")) <= 64 for data in _callback_data(bind_list_markup))
    confirmation_markup = next(markup for markup in send_calls if markup and any(data.startswith("n:rc:") for data in _callback_data(markup)))
    assert all(len(data.encode("utf-8")) <= 64 for data in _callback_data(confirmation_markup))


def test_uuid_ref_to_value_accepts_only_canonical_urlsafe_base64():
    user_id = str(uuid.uuid4())
    ref = employee_telegram_bot._uuid_to_ref(user_id)

    assert employee_telegram_bot._uuid_ref_to_value(ref) == user_id
    assert employee_telegram_bot._uuid_ref_to_value("short") is None
    assert employee_telegram_bot._uuid_ref_to_value("!" * employee_telegram_bot.UUID_REF_LENGTH) is None

    non_urlsafe_ref = ref[:-1] + "+"
    assert employee_telegram_bot._uuid_ref_to_value(non_urlsafe_ref) is None

    assert employee_telegram_bot._uuid_ref_to_value(employee_telegram_bot._uuid_to_ref(user_id)) == user_id


def test_uuid_ref_round_trip_returns_original_uuid():
    user_id = str(uuid.uuid4())
    ref = employee_telegram_bot._uuid_to_ref(user_id)

    assert employee_telegram_bot._uuid_ref_to_value(ref) == user_id


def test_callback_reject_requires_confirmation_before_delete(monkeypatch):
    session = _session()
    _company(session)
    pending = _account(
        session,
        telegram_user_id="7201",
        telegram_chat_id="5201",
        request_status=REQUEST_STATUS_PENDING,
        bound_user_id=None,
        delivery_available=False,
    )
    send_calls: list[tuple[str, str, dict | None]] = []

    monkeypatch.setattr(employee_telegram_bot.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_bot.settings, "telegram_notification_bot_owner_id", "9001")
    monkeypatch.setattr(employee_telegram_bot, "send_message", lambda chat_id, text, **kwargs: send_calls.append((chat_id, text, kwargs.get("reply_markup"))) or {"ok": True})
    monkeypatch.setattr(employee_telegram_bot, "answer_callback_query", lambda *args, **kwargs: {"ok": True})

    account_ref = employee_telegram_bot._account_ref(pending.id)
    prompt_result = employee_telegram_bot.process_employee_telegram_update(
        session,
        _callback_update(data=f"n:rr:{account_ref}"),
    )
    assert prompt_result["action"] == "reject_confirmation_shown"
    assert session.query(TelegramNotificationAccount).filter_by(id=pending.id).count() == 1
    assert any(button["callback_data"] == f"n:rrc:{account_ref}" for row in send_calls[-1][2]["inline_keyboard"] for button in row)

    confirm_result = employee_telegram_bot.process_employee_telegram_update(
        session,
        _callback_update(callback_id="test-callback-2", data=f"n:rrc:{account_ref}"),
    )
    assert confirm_result["action"] == "binding_rejected"
    assert session.query(TelegramNotificationAccount).filter_by(id=pending.id).count() == 0


@pytest.mark.parametrize(
    ("data", "callback_id"),
    [
        ("n:rs", "test-callback-missing"),
        ("n:nr:0:extra", "test-callback-extra"),
        ("n:rb:short-ref:not-a-page", "test-callback-page"),
        ("n:rr:not-a-valid-uuid-ref", "test-callback-ref"),
    ],
)
def test_malformed_callback_does_not_raise_or_change_db(monkeypatch, data, callback_id):
    session = _session()
    company = _company(session)
    user = _bound_user(session, company_id=company.id, user_id=str(uuid.uuid4()), is_active=True)
    account = _account(
        session,
        telegram_user_id="7701",
        telegram_chat_id="5701",
        request_status=REQUEST_STATUS_APPROVED,
        bound_user_id=user.id,
        delivery_available=False,
        telegram_username="old7701",
        telegram_display_name="Old 7701",
    )
    original_state = (
        account.telegram_chat_id,
        account.telegram_username,
        account.telegram_display_name,
        account.delivery_available,
        account.updated_at,
    )
    send_calls: list[tuple[str, str]] = []
    answered: list[str] = []

    monkeypatch.setattr(employee_telegram_bot.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_bot, "send_message", lambda chat_id, text, **kwargs: send_calls.append((chat_id, text)) or {"ok": True})
    monkeypatch.setattr(employee_telegram_bot, "answer_callback_query", lambda callback_id, text=None, **kwargs: answered.append(callback_id) or {"ok": True})

    result = employee_telegram_bot.process_employee_telegram_update(
        session,
        _callback_update(callback_id=callback_id, telegram_user_id="7701", chat_id="5701", data=data),
    )

    session.refresh(account)
    assert result["reason"] == "invalid_callback"
    assert answered == [callback_id]
    assert send_calls[-1][1] == employee_telegram_bot.EMPLOYEE_NOT_FOUND_TEXT
    assert (
        account.telegram_chat_id,
        account.telegram_username,
        account.telegram_display_name,
        account.delivery_available,
        account.updated_at,
    ) == original_state


def test_callback_with_trailing_empty_token_is_invalid_and_does_not_change_db(monkeypatch):
    session = _session()
    company = _company(session)
    user = _bound_user(session, company_id=company.id, user_id=str(uuid.uuid4()), is_active=True)
    account = _account(
        session,
        telegram_user_id="7702",
        telegram_chat_id="5702",
        request_status=REQUEST_STATUS_APPROVED,
        bound_user_id=user.id,
        delivery_available=False,
        telegram_username="old7702",
        telegram_display_name="Old 7702",
    )
    original_state = (
        account.telegram_chat_id,
        account.telegram_username,
        account.telegram_display_name,
        account.delivery_available,
        account.updated_at,
    )
    send_calls: list[tuple[str, str]] = []
    answered: list[str] = []

    monkeypatch.setattr(employee_telegram_bot.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_bot, "send_message", lambda chat_id, text, **kwargs: send_calls.append((chat_id, text)) or {"ok": True})
    monkeypatch.setattr(employee_telegram_bot, "answer_callback_query", lambda callback_id, text=None, **kwargs: answered.append(callback_id) or {"ok": True})

    result = employee_telegram_bot.process_employee_telegram_update(
        session,
        _callback_update(callback_id="test-callback-empty-tail", telegram_user_id="7702", chat_id="5702", data="n:mb:"),
    )

    session.refresh(account)
    assert result["reason"] == "invalid_callback"
    assert answered == ["test-callback-empty-tail"]
    assert send_calls[-1][1] == employee_telegram_bot.EMPLOYEE_NOT_FOUND_TEXT
    assert (
        account.telegram_chat_id,
        account.telegram_username,
        account.telegram_display_name,
        account.delivery_available,
        account.updated_at,
    ) == original_state


def test_owner_callback_add_admin_candidates_page_zero_opens_candidate_list(monkeypatch):
    session = _session()
    company = _company(session)
    candidate_user = _bound_user(session, company_id=company.id, user_id=str(uuid.uuid4()), is_active=True)
    _account(
        session,
        telegram_user_id="7801",
        telegram_chat_id="5801",
        request_status=REQUEST_STATUS_APPROVED,
        bound_user_id=candidate_user.id,
        delivery_available=True,
    )
    send_calls: list[tuple[str, str, dict | None]] = []
    answered: list[tuple[str, str | None, str | None]] = []

    monkeypatch.setattr(employee_telegram_bot.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_bot.settings, "telegram_notification_bot_owner_id", "9001")
    monkeypatch.setattr(employee_telegram_bot, "send_message", lambda chat_id, text, **kwargs: send_calls.append((chat_id, text, kwargs.get("reply_markup"))) or {"ok": True})
    monkeypatch.setattr(
        employee_telegram_bot,
        "answer_callback_query",
        lambda callback_id, text=None, **kwargs: answered.append((callback_id, text, kwargs.get("bot_token"))) or {"ok": True},
    )

    result = employee_telegram_bot.process_employee_telegram_update(
        session,
        _callback_update(data="n:aa:0"),
    )

    assert result["action"] == "admin_candidates_shown"
    assert result["count"] == 1
    assert answered == [("test-callback-1", None, "employee-token")]
    assert send_calls
    assert send_calls[-1][0] == "5001"
    assert send_calls[-1][1] == "Выберите сотрудника для роли администратора."
    callback_data = _callback_data(send_calls[-1][2])
    assert any(data.startswith("n:aas:") for data in callback_data)


def test_callback_authorization_rechecked_after_admin_role_removed(monkeypatch):
    session = _session()
    company = _company(session)
    user = _bound_user(session, company_id=company.id, user_id="user-admin-old", is_active=True)
    account = _account(
        session,
        telegram_user_id="7301",
        telegram_chat_id="5301",
        request_status=REQUEST_STATUS_APPROVED,
        bound_user_id=user.id,
        delivery_available=True,
        is_notification_bot_admin=True,
    )
    account.is_notification_bot_admin = False
    session.commit()
    send_calls: list[tuple[str, str]] = []

    monkeypatch.setattr(employee_telegram_bot.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_bot.settings, "telegram_notification_bot_owner_id", "9001")
    monkeypatch.setattr(employee_telegram_bot, "send_message", lambda chat_id, text, **kwargs: send_calls.append((chat_id, text)) or {"ok": True})
    monkeypatch.setattr(employee_telegram_bot, "answer_callback_query", lambda *args, **kwargs: {"ok": True})

    result = employee_telegram_bot.process_employee_telegram_update(
        session,
        _callback_update(telegram_user_id="7301", chat_id="5301", data="n:nr:0"),
    )

    assert result["reason"] == "forbidden"
    assert send_calls[-1][1] == employee_telegram_bot.EMPLOYEE_FORBIDDEN_TEXT


def test_owner_only_admin_management_and_grant_revoke_callbacks(monkeypatch):
    session = _session()
    company = _company(session)
    user = _bound_user(session, company_id=company.id, user_id="user-admin-candidate", is_active=True)
    account = _account(
        session,
        telegram_user_id="7401",
        telegram_chat_id="5401",
        request_status=REQUEST_STATUS_APPROVED,
        bound_user_id=user.id,
        delivery_available=True,
    )
    send_calls: list[tuple[str, str, dict | None]] = []

    monkeypatch.setattr(employee_telegram_bot.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_bot.settings, "telegram_notification_bot_owner_id", "9001")
    monkeypatch.setattr(employee_telegram_bot, "send_message", lambda chat_id, text, **kwargs: send_calls.append((chat_id, text, kwargs.get("reply_markup"))) or {"ok": True})
    monkeypatch.setattr(employee_telegram_bot, "answer_callback_query", lambda *args, **kwargs: {"ok": True})
    account_ref = employee_telegram_bot._account_ref(account.id)

    forbidden = employee_telegram_bot.process_employee_telegram_update(
        session,
        _callback_update(telegram_user_id="7401", chat_id="5401", data="n:ad:0"),
    )
    grant = employee_telegram_bot.process_employee_telegram_update(
        session,
        _callback_update(data=f"n:aac:{account_ref}"),
    )
    session.refresh(account)
    revoke = employee_telegram_bot.process_employee_telegram_update(
        session,
        _callback_update(callback_id="test-callback-3", data=f"n:arc:{account_ref}"),
    )
    session.refresh(account)

    assert forbidden["reason"] == "forbidden"
    assert grant["action"] == "admin_granted"
    assert revoke["action"] == "admin_revoked"
    assert account.is_notification_bot_admin is False


def test_callback_self_unbind_deletes_account_and_notifies_other_effective_admins(monkeypatch):
    session = _session()
    company = _company(session)
    user = _bound_user(session, company_id=company.id, user_id="user-self", is_active=True)
    admin_user = _bound_user(session, company_id=company.id, user_id="user-admin-notify", is_active=True)
    initiator = _account(
        session,
        telegram_user_id="7501",
        telegram_chat_id="5501",
        request_status=REQUEST_STATUS_APPROVED,
        bound_user_id=user.id,
        delivery_available=True,
    )
    _account(
        session,
        telegram_user_id="7502",
        telegram_chat_id="5502",
        request_status=REQUEST_STATUS_APPROVED,
        bound_user_id=admin_user.id,
        delivery_available=True,
        is_notification_bot_admin=True,
    )
    send_calls: list[tuple[str, str]] = []

    monkeypatch.setattr(employee_telegram_bot.settings, "employee_telegram_bot_token", "employee-token")
    monkeypatch.setattr(employee_telegram_bot.settings, "telegram_notification_bot_owner_id", "9001")
    monkeypatch.setattr(employee_telegram_bot, "send_message", lambda chat_id, text, **kwargs: send_calls.append((chat_id, text)) or {"ok": True})
    monkeypatch.setattr(employee_telegram_bot, "answer_callback_query", lambda *args, **kwargs: {"ok": True})

    result = employee_telegram_bot.process_employee_telegram_update(
        session,
        _callback_update(telegram_user_id="7501", chat_id="5501", data="n:suc"),
    )

    assert result["action"] == "self_unbound"
    assert session.query(TelegramNotificationAccount).filter_by(id=initiator.id).count() == 0
    assert "5501" in [chat_id for chat_id, _ in send_calls]
    assert "5502" in [chat_id for chat_id, _ in send_calls]
    assert "9001" in [chat_id for chat_id, _ in send_calls]


class _SessionContext:
    def __init__(self, session):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, exc_type, exc, tb):
        return False


def test_process_employee_update_sync_uses_separate_employee_handler(monkeypatch):
    expected_session = object()
    update = {"update_id": 201, "message": {"text": "/bind"}}
    captured_calls: list[tuple[object, dict]] = []
    expected_result = {"ok": True, "action": "binding_requested"}

    def fake_session_local():
        return _SessionContext(expected_session)

    def fake_process_employee_telegram_update(db, payload):
        captured_calls.append((db, payload))
        return expected_result

    monkeypatch.setattr(employee_telegram_polling, "SessionLocal", fake_session_local)
    monkeypatch.setattr(employee_telegram_bot, "process_employee_telegram_update", fake_process_employee_telegram_update)

    result = employee_telegram_polling._process_employee_update_sync(update)

    assert captured_calls == [(expected_session, update)]
    assert result is expected_result
