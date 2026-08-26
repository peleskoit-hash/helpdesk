from __future__ import annotations

from datetime import datetime
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import Company, TelegramNotificationAccount, User
from api.app.services import employee_telegram_access
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
    telegram_chat_id: str = "chat-access",
    bound_user_id: str | None = None,
    request_status: str = REQUEST_STATUS_PENDING,
    delivery_available: bool = False,
    is_blocked: bool = False,
    is_notification_bot_admin: bool = False,
) -> TelegramNotificationAccount:
    now = datetime.utcnow()
    account = TelegramNotificationAccount(
        id=str(uuid.uuid4()),
        telegram_user_id=telegram_user_id,
        telegram_chat_id=telegram_chat_id,
        telegram_username="worker",
        telegram_display_name="Worker",
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


def test_owner_is_resolved_from_config(monkeypatch):
    session = _session()
    _company(session)
    monkeypatch.setattr(employee_telegram_access.settings, "telegram_notification_bot_owner_id", "9001")

    access = employee_telegram_access.resolve_employee_telegram_access(session, "9001")

    assert access.bot_role == employee_telegram_access.BOT_ROLE_OWNER
    assert access.is_owner is True
    assert access.can_manage_bindings is True
    assert access.can_manage_admins is True
    assert access.account is None


def test_additional_admin_role_is_resolved_for_bound_approved_account(monkeypatch):
    session = _session()
    company = _company(session)
    _bound_user(session, company_id=company.id, user_id="user-9002", is_active=True)
    _account(
        session,
        telegram_user_id="9002",
        bound_user_id="user-9002",
        request_status=REQUEST_STATUS_APPROVED,
        delivery_available=True,
        is_notification_bot_admin=True,
    )
    monkeypatch.setattr(employee_telegram_access.settings, "telegram_notification_bot_owner_id", None)

    access = employee_telegram_access.resolve_employee_telegram_access(session, "9002")

    assert access.bot_role == employee_telegram_access.BOT_ROLE_ADMIN
    assert access.is_additional_admin is True
    assert access.can_manage_bindings is True
    assert access.can_manage_admins is False


def test_inactive_bound_user_temporarily_loses_additional_admin_effective_role(monkeypatch):
    session = _session()
    company = _company(session)
    _account(
        session,
        telegram_user_id="90021",
        bound_user_id="user-90021",
        request_status=REQUEST_STATUS_APPROVED,
        delivery_available=True,
        is_notification_bot_admin=True,
    )
    inactive_user = session.get(User, "user-90021")
    if inactive_user is None:
        now = datetime.utcnow()
        inactive_user = User(
            id="user-90021",
            company_id=company.id,
            email="inactive@example.test",
            login="inactive-user",
            password_hash="hash",
            full_name="Inactive User",
            role="agent",
            is_active=False,
            availability_status="offline",
            legacy_ticket_count=0,
            created_at=now,
            updated_at=now,
        )
        session.add(inactive_user)
    else:
        inactive_user.is_active = False
    session.commit()
    monkeypatch.setattr(employee_telegram_access.settings, "telegram_notification_bot_owner_id", None)

    access = employee_telegram_access.resolve_employee_telegram_access(session, "90021")

    assert access.bot_role == employee_telegram_access.BOT_ROLE_USER
    assert access.can_manage_bindings is False


def test_count_pending_notification_requests_counts_only_unbound_pending():
    session = _session()
    _company(session)
    _account(session, telegram_user_id="9101", request_status=REQUEST_STATUS_PENDING, bound_user_id=None)
    _account(session, telegram_user_id="9102", request_status=REQUEST_STATUS_PENDING, bound_user_id=None)
    _account(session, telegram_user_id="9103", request_status=REQUEST_STATUS_APPROVED, bound_user_id="user-9103", delivery_available=True)
    _account(session, telegram_user_id="9104", request_status=REQUEST_STATUS_PENDING, bound_user_id="user-9104")
    _account(session, telegram_user_id="9105", request_status=REQUEST_STATUS_PENDING, bound_user_id=None, is_blocked=True)

    count = employee_telegram_access.count_pending_notification_requests(session)

    assert count == 2


def test_inactive_additional_admin_restores_effective_access_after_reactivation(monkeypatch):
    session = _session()
    company = _company(session)
    user = _bound_user(session, company_id=company.id, user_id="user-reactivate", is_active=False)
    _account(
        session,
        telegram_user_id="9201",
        bound_user_id=user.id,
        request_status=REQUEST_STATUS_APPROVED,
        delivery_available=True,
        is_notification_bot_admin=True,
    )
    monkeypatch.setattr(employee_telegram_access.settings, "telegram_notification_bot_owner_id", None)

    inactive_access = employee_telegram_access.resolve_employee_telegram_access(session, "9201")
    user.is_active = True
    session.commit()
    active_access = employee_telegram_access.resolve_employee_telegram_access(session, "9201")

    assert inactive_access.can_manage_bindings is False
    assert active_access.bot_role == employee_telegram_access.BOT_ROLE_ADMIN
    assert active_access.can_manage_bindings is True


def test_blocked_additional_admin_loses_effective_access(monkeypatch):
    session = _session()
    company = _company(session)
    user = _bound_user(session, company_id=company.id, user_id="user-blocked-admin", is_active=True)
    account = _account(
        session,
        telegram_user_id="9202",
        bound_user_id=user.id,
        request_status=REQUEST_STATUS_APPROVED,
        delivery_available=True,
        is_blocked=True,
        is_notification_bot_admin=True,
    )
    monkeypatch.setattr(employee_telegram_access.settings, "telegram_notification_bot_owner_id", None)

    access = employee_telegram_access.resolve_employee_telegram_access(session, "9202")

    assert account.is_notification_bot_admin is True
    assert access.bot_role == employee_telegram_access.BOT_ROLE_NONE
    assert access.can_manage_bindings is False


def test_grant_admin_requires_active_unblocked_approved_binding():
    session = _session()
    company = _company(session)
    active_user = _bound_user(session, company_id=company.id, user_id="user-grant", is_active=True)
    blocked_user = _bound_user(session, company_id=company.id, user_id="user-blocked-grant", is_active=True)
    inactive_user = _bound_user(session, company_id=company.id, user_id="user-inactive-grant", is_active=False)
    active_account = _account(
        session,
        telegram_user_id="9301",
        bound_user_id=active_user.id,
        request_status=REQUEST_STATUS_APPROVED,
        delivery_available=True,
    )
    _account(
        session,
        telegram_user_id="9302",
        bound_user_id=blocked_user.id,
        request_status=REQUEST_STATUS_APPROVED,
        delivery_available=True,
        is_blocked=True,
    )
    _account(
        session,
        telegram_user_id="9303",
        bound_user_id=inactive_user.id,
        request_status=REQUEST_STATUS_APPROVED,
        delivery_available=True,
    )

    granted = employee_telegram_access.grant_notification_bot_admin(session, user_id=active_user.id)
    with pytest.raises(employee_telegram_access.TelegramBindingOperationError):
        employee_telegram_access.grant_notification_bot_admin(session, user_id=blocked_user.id)
    with pytest.raises(employee_telegram_access.TelegramBindingOperationError):
        employee_telegram_access.grant_notification_bot_admin(session, user_id=inactive_user.id)

    assert granted.id == active_account.id
    assert granted.is_notification_bot_admin is True


def test_revoke_additional_admin_role_does_not_remove_binding(monkeypatch):
    session = _session()
    company = _company(session)
    _bound_user(session, company_id=company.id, user_id="user-9003", is_active=True)
    account = _account(
        session,
        telegram_user_id="9003",
        bound_user_id="user-9003",
        request_status=REQUEST_STATUS_APPROVED,
        delivery_available=True,
        is_notification_bot_admin=True,
    )
    monkeypatch.setattr(employee_telegram_access.settings, "telegram_notification_bot_owner_id", None)

    updated = employee_telegram_access.revoke_notification_bot_admin(session, user_id="user-9003")
    session.commit()
    session.refresh(updated)

    access = employee_telegram_access.resolve_employee_telegram_access(session, "9003")

    assert updated.id == account.id
    assert updated.is_notification_bot_admin is False
    assert updated.bound_user_id == "user-9003"
    assert updated.request_status == REQUEST_STATUS_APPROVED
    assert access.bot_role == employee_telegram_access.BOT_ROLE_USER
