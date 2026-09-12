from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import Company, StatsUserStatusInterval, TelegramNotificationAccount, User
from api.app.routers import settings_users as settings_users_router
from api.app.schemas import SettingsUserCreate, SettingsUserUpdate


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def _admin_user():
    return SimpleNamespace(
        id="admin-auth",
        role="admin",
        real_user_id="admin-auth",
        real_role="admin",
        is_real_admin=True,
    )


def _company(session) -> Company:
    company = Company(id="company-1", name="ACME", is_active=True, created_at=datetime.utcnow())
    session.add(company)
    session.commit()
    return company


def _user(session, *, company_id: str, full_name: str, role: str = "agent", login: str | None = None) -> User:
    now = datetime.utcnow()
    user = User(
        id=str(uuid.uuid4()),
        company_id=company_id,
        email=f"{login or full_name}@local.helpdesk",
        login=login or f"{full_name.lower().replace(' ', '.')}.{uuid.uuid4().hex[:6]}",
        password_hash="hash",
        full_name=full_name,
        role=role,
        is_active=True,
        availability_status="online",
        legacy_ticket_count=0,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    session.commit()
    return user


def _notification_account(
    session,
    *,
    user_id: str,
    telegram_user_id: str,
    telegram_username: str | None = None,
    request_status: str = "pending",
) -> TelegramNotificationAccount:
    now = datetime.utcnow()
    account = TelegramNotificationAccount(
        id=str(uuid.uuid4()),
        telegram_user_id=telegram_user_id,
        telegram_chat_id="chat-1",
        telegram_username=telegram_username,
        telegram_display_name="Ivanov",
        bound_user_id=user_id,
        request_status=request_status,
        delivery_available=True,
        created_at=now,
        updated_at=now,
    )
    session.add(account)
    session.commit()
    return account


def _status_intervals(session, user_id: str) -> list[StatsUserStatusInterval]:
    return session.query(StatsUserStatusInterval).filter_by(user_id=user_id).order_by(StatsUserStatusInterval.started_at).all()


def test_list_users_returns_false_and_null_for_unbound_notification_account():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov")

    result = settings_users_router.list_users(db=session, user=_admin_user())

    assert len(result) == 1
    assert result[0].id == user.id
    assert result[0].telegram_notification_bound is False
    assert result[0].telegram_notification_display is None


def test_list_users_returns_bound_notification_account_display_with_username():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov")
    _notification_account(session, user_id=user.id, telegram_user_id="123456789", telegram_username="ivanov")

    result = settings_users_router.list_users(db=session, user=_admin_user())

    assert result[0].telegram_notification_bound is True
    assert result[0].telegram_notification_display == "@ivanov (Tg ID: 123456789)"


def test_list_users_returns_bound_notification_account_display_without_username():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov")
    _notification_account(session, user_id=user.id, telegram_user_id="123456789", telegram_username=None)

    result = settings_users_router.list_users(db=session, user=_admin_user())

    assert result[0].telegram_notification_bound is True
    assert result[0].telegram_notification_display == "Без username (Tg ID: 123456789)"


def test_settings_user_create_schema_forbids_telegram_fields():
    with pytest.raises(ValidationError):
        SettingsUserCreate(
            full_name="Ivan Petrov",
            login="ivan",
            password="secret",
            role="agent",
            telegram_notification_bound=True,
        )


def test_settings_user_update_schema_forbids_telegram_fields():
    with pytest.raises(ValidationError):
        SettingsUserUpdate(
            telegram_notification_display="@ivanov (Tg ID: 123456789)",
        )


def test_settings_user_update_schema_forbids_avatar_path():
    with pytest.raises(ValidationError):
        SettingsUserUpdate(avatar_path="avatars/other.webp")


def test_create_user_still_works_without_telegram_fields():
    session = _session()
    _company(session)

    result = settings_users_router.create_user(
        SettingsUserCreate(
            full_name="Ivan Petrov",
            login="ivan",
            password="secret",
            role="agent",
            is_active=True,
            availability_status="offline",
            legacy_ticket_count=2,
        ),
        db=session,
        user=_admin_user(),
    )

    assert result.login == "ivan"
    assert result.telegram_notification_bound is False
    assert result.telegram_notification_display is None


@pytest.mark.parametrize("role", ["agent", "trainee"])
def test_create_active_support_user_opens_first_status_history_interval(role: str):
    session = _session()
    _company(session)

    result = settings_users_router.create_user(
        SettingsUserCreate(
            full_name="Ivan Petrov",
            login=f"ivan-{role}",
            password="secret",
            role=role,
            is_active=True,
            availability_status="waiting",
        ),
        db=session,
        user=_admin_user(),
    )

    rows = _status_intervals(session, result.id)
    assert len(rows) == 1
    assert rows[0].status == "waiting"
    assert rows[0].ended_at is None


@pytest.mark.parametrize(
    ("role", "is_active"),
    [
        ("agent", False),
        ("admin", True),
        ("chiki_piki", True),
    ],
)
def test_create_non_trackable_user_does_not_open_status_history_interval(role: str, is_active: bool):
    session = _session()
    _company(session)

    result = settings_users_router.create_user(
        SettingsUserCreate(
            full_name="Ivan Petrov",
            login=f"ivan-{role}-{is_active}",
            password="secret",
            role=role,
            is_active=is_active,
            availability_status="waiting",
        ),
        db=session,
        user=_admin_user(),
    )

    assert _status_intervals(session, result.id) == []


def test_update_user_still_works_without_telegram_fields():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan")

    result = settings_users_router.update_user(
        user.id,
        SettingsUserUpdate(full_name="Ivan Sidorov", role="admin", legacy_ticket_count=5),
        db=session,
        user=_admin_user(),
    )

    assert result.full_name == "Ivan Sidorov"
    assert result.role == "admin"
    assert result.legacy_ticket_count == 5
    assert result.telegram_notification_bound is False
    assert result.telegram_notification_display is None


def test_update_user_with_role_and_status_closes_history_once_for_final_non_trackable_state():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan")
    started_at = datetime(2026, 9, 11, 9, 0, 0)
    session.add(
        StatsUserStatusInterval(
            id=str(uuid.uuid4()),
            company_id=company.id,
            user_id=user.id,
            status="online",
            started_at=started_at,
            ended_at=None,
            created_at=started_at,
        )
    )
    session.commit()

    result = settings_users_router.update_user(
        user.id,
        SettingsUserUpdate(role="admin", availability_status="lunch"),
        db=session,
        user=_admin_user(),
    )

    rows = _status_intervals(session, user.id)
    assert result.role == "admin"
    assert result.availability_status == "lunch"
    assert len(rows) == 1
    assert rows[0].status == "online"
    assert rows[0].ended_at is not None


def test_update_user_with_activation_and_status_opens_single_history_interval_for_final_state():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan")
    user.is_active = False
    user.availability_status = "waiting"
    session.commit()

    result = settings_users_router.update_user(
        user.id,
        SettingsUserUpdate(is_active=True, availability_status="online"),
        db=session,
        user=_admin_user(),
    )

    rows = _status_intervals(session, user.id)
    assert result.is_active is True
    assert result.availability_status == "online"
    assert len(rows) == 1
    assert rows[0].status == "online"
    assert rows[0].ended_at is None


def test_reject_telegram_binding_request_deletes_pending_account():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov")
    account = TelegramNotificationAccount(
        id=str(uuid.uuid4()),
        telegram_user_id="tg-reject-1",
        telegram_chat_id="chat-reject-1",
        telegram_username="reject_me",
        telegram_display_name="Reject Me",
        bound_user_id=None,
        request_status="pending",
        delivery_available=False,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(account)
    session.commit()

    result = settings_users_router.reject_telegram_binding_request(
        "tg-reject-1",
        db=session,
        user=_admin_user(),
    )

    assert result.telegram_user_id == "tg-reject-1"
    assert session.query(TelegramNotificationAccount).filter_by(telegram_user_id="tg-reject-1").count() == 0


def test_reject_telegram_binding_request_forbids_approved_account():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov")
    account = _notification_account(
        session,
        user_id=user.id,
        telegram_user_id="tg-reject-approved",
        request_status="approved",
    )

    with pytest.raises(settings_users_router.HTTPException) as exc:
        settings_users_router.reject_telegram_binding_request(
            "tg-reject-approved",
            db=session,
            user=_admin_user(),
        )

    assert exc.value.status_code == 409
    assert session.query(TelegramNotificationAccount).filter_by(id=account.id).count() == 1


def test_approve_telegram_binding_request_forbids_occupied_user_without_rebinding():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov")
    existing = _notification_account(
        session,
        user_id=user.id,
        telegram_user_id="tg-existing-approved",
        request_status="approved",
    )
    pending = TelegramNotificationAccount(
        id=str(uuid.uuid4()),
        telegram_user_id="tg-pending-approve",
        telegram_chat_id="chat-pending-approve",
        telegram_username="pending",
        telegram_display_name="Pending",
        bound_user_id=None,
        request_status="pending",
        delivery_available=False,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(pending)
    session.commit()

    with pytest.raises(settings_users_router.HTTPException) as exc:
        settings_users_router.approve_telegram_binding_request(
            "tg-pending-approve",
            settings_users_router.TelegramBindingApprovePayload(user_id=user.id),
            db=session,
            user=_admin_user(),
        )

    session.refresh(existing)
    session.refresh(pending)
    assert exc.value.status_code == 409
    assert existing.bound_user_id == user.id
    assert existing.request_status == "approved"
    assert pending.bound_user_id is None
    assert pending.request_status == "pending"
    assert session.query(TelegramNotificationAccount).filter_by(request_status="rejected").count() == 0


def test_unbind_user_telegram_deletes_existing_account():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov")
    account = TelegramNotificationAccount(
        id=str(uuid.uuid4()),
        telegram_user_id="tg-unbind-1",
        telegram_chat_id="chat-unbind-1",
        telegram_username="unbind_me",
        telegram_display_name="Unbind Me",
        bound_user_id=user.id,
        request_status="approved",
        delivery_available=True,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(account)
    session.commit()

    result = settings_users_router.unbind_user_telegram(
        user.id,
        db=session,
        user=_admin_user(),
    )

    assert result.telegram_user_id == "tg-unbind-1"
    assert session.query(TelegramNotificationAccount).filter_by(telegram_user_id="tg-unbind-1").count() == 0
