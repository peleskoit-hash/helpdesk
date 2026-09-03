from __future__ import annotations

from datetime import datetime
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import Company, TelegramNotificationAccount, User
from api.app.services import telegram_employee_binding


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


def _user(session, *, company_id: str, full_name: str, login: str | None = None) -> User:
    now = datetime.utcnow()
    user = User(
        id=str(uuid.uuid4()),
        company_id=company_id,
        email=f"{login or full_name}@local.helpdesk",
        login=login or f"{full_name.lower().replace(' ', '.')}.{uuid.uuid4().hex[:6]}",
        password_hash="hash",
        full_name=full_name,
        role="agent",
        is_active=True,
        availability_status="online",
        legacy_ticket_count=0,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    session.commit()
    return user


def _account(
    session,
    *,
    telegram_user_id: str,
    telegram_chat_id: str | None = None,
    telegram_username: str | None = None,
    telegram_display_name: str | None = None,
    bound_user_id: str | None = None,
    request_status: str = telegram_employee_binding.REQUEST_STATUS_PENDING,
    delivery_available: bool = False,
    is_blocked: bool = False,
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
        created_at=now,
        updated_at=now,
    )
    session.add(account)
    session.commit()
    return account


def test_create_or_update_pending_request_creates_pending_unbound_account():
    session = _session()

    account = telegram_employee_binding.create_or_update_pending_request(
        session,
        telegram_user_id="tg-100",
        telegram_chat_id="chat-100",
        telegram_username="worker100",
        telegram_display_name="Worker 100",
    )
    session.commit()
    session.refresh(account)

    assert account.telegram_user_id == "tg-100"
    assert account.bound_user_id is None
    assert account.request_status == telegram_employee_binding.REQUEST_STATUS_PENDING
    assert account.delivery_available is False
    assert account.is_blocked is False


def test_create_or_update_pending_request_does_not_reset_approved_binding():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov")
    account = _account(
        session,
        telegram_user_id="tg-101",
        telegram_chat_id="chat-old",
        telegram_username="old_user",
        telegram_display_name="Old Name",
        bound_user_id=user.id,
        request_status=telegram_employee_binding.REQUEST_STATUS_APPROVED,
        delivery_available=True,
    )

    updated = telegram_employee_binding.create_or_update_pending_request(
        session,
        telegram_user_id="tg-101",
        telegram_chat_id="chat-new",
        telegram_username="new_user",
        telegram_display_name="New Name",
    )
    session.commit()
    session.refresh(updated)

    assert updated.id == account.id
    assert updated.telegram_chat_id == "chat-new"
    assert updated.telegram_username == "new_user"
    assert updated.telegram_display_name == "New Name"
    assert updated.bound_user_id == user.id
    assert updated.request_status == telegram_employee_binding.REQUEST_STATUS_APPROVED
    assert updated.delivery_available is True


def test_create_or_update_pending_request_does_not_reset_blocked_approved_binding():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov")
    account = _account(
        session,
        telegram_user_id="tg-101b",
        telegram_chat_id="chat-old",
        bound_user_id=user.id,
        request_status=telegram_employee_binding.REQUEST_STATUS_APPROVED,
        delivery_available=True,
        is_blocked=True,
    )

    updated = telegram_employee_binding.create_or_update_pending_request(
        session,
        telegram_user_id="tg-101b",
        telegram_chat_id="chat-new",
        telegram_username="blocked_user",
        telegram_display_name="Blocked Name",
    )
    session.commit()
    session.refresh(updated)

    assert updated.id == account.id
    assert updated.bound_user_id == user.id
    assert updated.request_status == telegram_employee_binding.REQUEST_STATUS_APPROVED
    assert updated.delivery_available is True
    assert updated.is_blocked is True


def test_create_or_update_pending_request_converts_legacy_rejected_blocked_to_clean_pending():
    session = _session()
    account = _account(
        session,
        telegram_user_id="tg-legacy",
        telegram_chat_id="chat-old",
        telegram_username="old_user",
        telegram_display_name="Old Name",
        bound_user_id=None,
        request_status="rejected",
        delivery_available=True,
        is_blocked=True,
    )
    account.is_notification_bot_admin = True
    session.commit()

    updated = telegram_employee_binding.create_or_update_pending_request(
        session,
        telegram_user_id="tg-legacy",
        telegram_chat_id="chat-new",
        telegram_username="new_user",
        telegram_display_name="New Name",
    )
    session.commit()
    session.refresh(updated)

    assert updated.id == account.id
    assert updated.request_status == telegram_employee_binding.REQUEST_STATUS_PENDING
    assert updated.bound_user_id is None
    assert updated.is_blocked is False
    assert updated.is_notification_bot_admin is False
    assert updated.delivery_available is False
    assert updated.telegram_chat_id == "chat-new"
    assert updated.telegram_username == "new_user"
    assert updated.telegram_display_name == "New Name"


def test_approve_binding_binds_pending_and_enables_delivery_only_with_chat_id():
    session = _session()
    company = _company(session)
    user_with_chat = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan")
    user_without_chat = _user(session, company_id=company.id, full_name="Petr Sidorov", login="petr")
    with_chat = _account(session, telegram_user_id="tg-102", telegram_chat_id="chat-102")
    without_chat = _account(session, telegram_user_id="tg-103", telegram_chat_id=None)

    approved_with_chat = telegram_employee_binding.approve_binding(
        session,
        user_id=user_with_chat.id,
        telegram_user_id=with_chat.telegram_user_id,
    )
    approved_without_chat = telegram_employee_binding.approve_binding(
        session,
        user_id=user_without_chat.id,
        telegram_user_id=without_chat.telegram_user_id,
    )
    session.commit()
    session.refresh(approved_with_chat)
    session.refresh(approved_without_chat)

    assert approved_with_chat.bound_user_id == user_with_chat.id
    assert approved_with_chat.request_status == telegram_employee_binding.REQUEST_STATUS_APPROVED
    assert approved_with_chat.delivery_available is True

    assert approved_without_chat.bound_user_id == user_without_chat.id
    assert approved_without_chat.request_status == telegram_employee_binding.REQUEST_STATUS_APPROVED
    assert approved_without_chat.delivery_available is False


def test_approve_binding_rejects_occupied_user_without_rejected_side_effect():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov")
    existing = _account(
        session,
        telegram_user_id="tg-existing",
        telegram_chat_id="chat-existing",
        bound_user_id=user.id,
        request_status=telegram_employee_binding.REQUEST_STATUS_APPROVED,
        delivery_available=True,
    )
    pending = _account(session, telegram_user_id="tg-pending", telegram_chat_id="chat-pending")

    with pytest.raises(telegram_employee_binding.TelegramBindingOperationError) as exc:
        telegram_employee_binding.approve_binding(session, user_id=user.id, telegram_user_id=pending.telegram_user_id)

    session.rollback()
    session.refresh(existing)
    session.refresh(pending)
    assert exc.value.code == "telegram_user_already_bound"
    assert existing.bound_user_id == user.id
    assert existing.request_status == telegram_employee_binding.REQUEST_STATUS_APPROVED
    assert pending.bound_user_id is None
    assert pending.request_status == telegram_employee_binding.REQUEST_STATUS_PENDING
    assert session.query(TelegramNotificationAccount).filter_by(request_status="rejected").count() == 0


def test_approve_binding_rejects_missing_user_and_preserves_pending():
    session = _session()
    pending = _account(session, telegram_user_id="tg-missing-user", telegram_chat_id="chat-pending")

    with pytest.raises(telegram_employee_binding.TelegramBindingOperationError) as exc:
        telegram_employee_binding.approve_binding(session, user_id="missing-user", telegram_user_id=pending.telegram_user_id)

    session.rollback()
    session.refresh(pending)
    assert exc.value.code == "telegram_binding_user_not_found"
    assert pending.request_status == telegram_employee_binding.REQUEST_STATUS_PENDING
    assert pending.bound_user_id is None


def test_approve_binding_rejects_inactive_user_and_preserves_pending():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Inactive User")
    user.is_active = False
    session.commit()
    pending = _account(session, telegram_user_id="tg-inactive-user", telegram_chat_id="chat-pending")

    with pytest.raises(telegram_employee_binding.TelegramBindingOperationError) as exc:
        telegram_employee_binding.approve_binding(session, user_id=user.id, telegram_user_id=pending.telegram_user_id)

    session.rollback()
    session.refresh(pending)
    assert exc.value.code == "telegram_binding_user_inactive"
    assert pending.request_status == telegram_employee_binding.REQUEST_STATUS_PENDING
    assert pending.bound_user_id is None


def test_reject_binding_deletes_only_pending_request():
    session = _session()
    account = _account(
        session,
        telegram_user_id="tg-104",
        telegram_chat_id="chat-104",
        bound_user_id=None,
        request_status=telegram_employee_binding.REQUEST_STATUS_PENDING,
        delivery_available=False,
    )

    rejected = telegram_employee_binding.reject_binding(session, telegram_user_id=account.telegram_user_id)
    session.commit()

    assert rejected is account
    assert session.query(TelegramNotificationAccount).filter_by(telegram_user_id=account.telegram_user_id).count() == 0


def test_reject_binding_forbids_approved_account():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov")
    account = _account(
        session,
        telegram_user_id="tg-104-approved",
        telegram_chat_id="chat-104-approved",
        bound_user_id=user.id,
        request_status=telegram_employee_binding.REQUEST_STATUS_APPROVED,
        delivery_available=True,
    )

    with pytest.raises(telegram_employee_binding.TelegramBindingOperationError):
        telegram_employee_binding.reject_binding(session, telegram_user_id=account.telegram_user_id)

    session.rollback()
    assert session.query(TelegramNotificationAccount).filter_by(telegram_user_id=account.telegram_user_id).count() == 1


def test_unbind_employee_keeps_current_status_behavior_and_disables_delivery():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov")
    account = _account(
        session,
        telegram_user_id="tg-105",
        telegram_chat_id="chat-105",
        bound_user_id=user.id,
        request_status=telegram_employee_binding.REQUEST_STATUS_APPROVED,
        delivery_available=True,
    )

    unbound = telegram_employee_binding.unbind_employee(session, user_id=user.id)
    session.commit()

    assert unbound is account
    assert session.query(TelegramNotificationAccount).filter_by(telegram_user_id=account.telegram_user_id).count() == 0


def test_block_account_preserves_binding_and_status_and_marks_blocked():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov")
    account = _account(
        session,
        telegram_user_id="tg-106",
        telegram_chat_id="chat-106",
        bound_user_id=user.id,
        request_status=telegram_employee_binding.REQUEST_STATUS_APPROVED,
        delivery_available=True,
    )

    blocked = telegram_employee_binding.block_account(session, telegram_user_id=account.telegram_user_id)
    session.commit()
    session.refresh(blocked)

    assert blocked.bound_user_id == user.id
    assert blocked.request_status == telegram_employee_binding.REQUEST_STATUS_APPROVED
    assert blocked.delivery_available is False
    assert blocked.is_blocked is True


def test_touch_notification_interaction_updates_metadata_and_restores_delivery_for_approved():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov")
    account = _account(
        session,
        telegram_user_id="tg-touch",
        telegram_chat_id="chat-old",
        telegram_username="old_user",
        telegram_display_name="Old Name",
        bound_user_id=user.id,
        request_status=telegram_employee_binding.REQUEST_STATUS_APPROVED,
        delivery_available=False,
    )

    updated = telegram_employee_binding.touch_notification_interaction(
        session,
        telegram_user_id="tg-touch",
        telegram_chat_id="chat-new",
        telegram_username="new_user",
        telegram_display_name="New Name",
    )
    session.commit()
    session.refresh(updated)

    assert updated.telegram_chat_id == "chat-new"
    assert updated.telegram_username == "new_user"
    assert updated.telegram_display_name == "New Name"
    assert updated.delivery_available is True


def test_touch_notification_interaction_clears_username_when_explicitly_missing():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov")
    account = _account(
        session,
        telegram_user_id="tg-touch-clear-username",
        telegram_chat_id="chat-old",
        telegram_username="old_user",
        telegram_display_name="Old Name",
        bound_user_id=user.id,
        request_status=telegram_employee_binding.REQUEST_STATUS_APPROVED,
        delivery_available=False,
    )

    updated = telegram_employee_binding.touch_notification_interaction(
        session,
        telegram_user_id="tg-touch-clear-username",
        telegram_chat_id="chat-new",
        telegram_username=None,
        telegram_display_name="New Name",
    )
    session.commit()
    session.refresh(updated)

    assert updated.telegram_chat_id == "chat-new"
    assert updated.telegram_username is None
    assert updated.telegram_display_name == "New Name"
    assert updated.delivery_available is True


def test_touch_notification_interaction_keeps_blocked_binding_blocked_without_restoring_delivery():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov")
    account = _account(
        session,
        telegram_user_id="tg-touch-blocked",
        telegram_chat_id="chat-old",
        bound_user_id=user.id,
        request_status=telegram_employee_binding.REQUEST_STATUS_APPROVED,
        delivery_available=False,
        is_blocked=True,
    )
    original_updated_at = account.updated_at

    updated = telegram_employee_binding.touch_notification_interaction(
        session,
        telegram_user_id="tg-touch-blocked",
        telegram_chat_id="chat-new",
        telegram_username="blocked_user",
        telegram_display_name="Blocked Name",
    )
    session.commit()
    session.refresh(updated)

    assert updated.is_blocked is True
    assert updated.delivery_available is False
    assert updated.telegram_chat_id == "chat-new"
    assert updated.telegram_username == "blocked_user"
    assert updated.telegram_display_name == "Blocked Name"
    assert updated.updated_at == original_updated_at


def test_unblock_account_restores_delivery_when_chat_id_exists():
    session = _session()
    account = _account(
        session,
        telegram_user_id="tg-107",
        telegram_chat_id="chat-107",
        request_status=telegram_employee_binding.REQUEST_STATUS_APPROVED,
        bound_user_id="user-107",
        delivery_available=False,
        is_blocked=True,
    )

    unblocked = telegram_employee_binding.unblock_account(session, telegram_user_id=account.telegram_user_id)
    session.commit()
    session.refresh(unblocked)

    assert unblocked.id == account.id
    assert unblocked.is_blocked is False
    assert unblocked.request_status == telegram_employee_binding.REQUEST_STATUS_APPROVED
    assert unblocked.bound_user_id == "user-107"
    assert unblocked.delivery_available is True
