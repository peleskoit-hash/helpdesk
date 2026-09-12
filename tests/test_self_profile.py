from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.app.auth import hash_password, verify_password
from api.app.db import Base
from api.app.models import Company, StatsUserStatusInterval, TelegramNotificationAccount, User
from api.app.routers import users as users_router
from api.app.schemas import SelfLoginUpdate, SelfPasswordUpdate, UserStatusUpdate


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


def _user(
    session,
    *,
    company_id: str,
    full_name: str,
    login: str,
    password: str = "secret",
    role: str = "agent",
    legacy_ticket_count: int = 0,
    avatar_path: str | None = None,
) -> User:
    now = datetime.utcnow()
    user = User(
        id=str(uuid.uuid4()),
        company_id=company_id,
        email=f"{login}@local.helpdesk",
        login=login,
        password_hash=hash_password(password),
        avatar_path=avatar_path,
        full_name=full_name,
        role=role,
        is_active=True,
        availability_status="online",
        legacy_ticket_count=legacy_ticket_count,
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
    telegram_user_id: str = "123456789",
    telegram_username: str | None = "ivanov",
) -> TelegramNotificationAccount:
    now = datetime.utcnow()
    account = TelegramNotificationAccount(
        id=str(uuid.uuid4()),
        telegram_user_id=telegram_user_id,
        telegram_chat_id="chat-1",
        telegram_username=telegram_username,
        telegram_display_name="Ivanov",
        bound_user_id=user_id,
        request_status="approved",
        delivery_available=True,
        created_at=now,
        updated_at=now,
    )
    session.add(account)
    session.commit()
    return account


def _current_user(user: User):
    return SimpleNamespace(
        id=user.id,
        role=user.role,
        real_user_id=user.id,
        real_role=user.role,
        is_real_admin=(user.role == "admin"),
    )


def _client(session, current_user) -> TestClient:
    app = FastAPI()
    app.include_router(users_router.router)
    app.dependency_overrides[users_router.get_db] = lambda: session
    app.dependency_overrides[users_router.get_current_user] = lambda: current_user
    return TestClient(app)


def _status_intervals(session, user: User) -> list[StatsUserStatusInterval]:
    return session.query(StatsUserStatusInterval).filter_by(user_id=user.id).order_by(StatsUserStatusInterval.started_at).all()


def test_get_self_profile_returns_current_user_without_sensitive_fields():
    session = _session()
    company = _company(session)
    current = _user(
        session,
        company_id=company.id,
        full_name="Ivan Petrov",
        login="ivan",
        role="agent",
        legacy_ticket_count=7,
        avatar_path="avatars/ivan.webp",
    )
    other = _user(session, company_id=company.id, full_name="Other User", login="other")

    payload = users_router.get_my_profile(db=session, current_user=_current_user(current)).model_dump()

    assert payload["id"] == current.id
    assert payload["id"] != other.id
    assert payload["full_name"] == "Ivan Petrov"
    assert payload["login"] == "ivan"
    assert payload["role"] == "agent"
    assert payload["legacy_ticket_count"] == 7
    assert payload["avatar_path"] == "avatars/ivan.webp"
    assert payload["avatar_present"] is True
    assert "password_hash" not in payload
    assert "is_active" not in payload


def test_update_my_status_records_status_history_interval():
    session = _session()
    company = _company(session)
    current = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan")
    old_started_at = datetime(2026, 9, 11, 9, 0, 0)
    session.add(
        StatsUserStatusInterval(
            id=str(uuid.uuid4()),
            company_id=company.id,
            user_id=current.id,
            status="online",
            started_at=old_started_at,
            ended_at=None,
            created_at=old_started_at,
        )
    )
    session.commit()

    result = users_router.update_my_status(
        UserStatusUpdate(availability_status="waiting"),
        db=session,
        current_user=_current_user(current),
    )

    rows = _status_intervals(session, current)
    assert result.availability_status == "waiting"
    assert rows[0].status == "online"
    assert rows[0].ended_at is not None
    assert rows[1].status == "waiting"
    assert rows[1].started_at == rows[0].ended_at
    assert rows[1].ended_at is None


def test_admin_update_user_status_records_status_history_interval():
    session = _session()
    company = _company(session)
    admin = _user(session, company_id=company.id, full_name="Admin User", login="admin", role="admin")
    target = _user(session, company_id=company.id, full_name="Agent User", login="agent")
    old_started_at = datetime(2026, 9, 11, 9, 0, 0)
    session.add(
        StatsUserStatusInterval(
            id=str(uuid.uuid4()),
            company_id=company.id,
            user_id=target.id,
            status="online",
            started_at=old_started_at,
            ended_at=None,
            created_at=old_started_at,
        )
    )
    session.commit()

    result = users_router.update_user_status(
        target.id,
        UserStatusUpdate(availability_status="lunch"),
        db=session,
        current_user=_current_user(admin),
    )

    rows = _status_intervals(session, target)
    assert result.availability_status == "lunch"
    assert rows[0].status == "online"
    assert rows[0].ended_at is not None
    assert rows[1].status == "lunch"
    assert rows[1].started_at == rows[0].ended_at
    assert rows[1].ended_at is None


def test_get_self_profile_returns_unbound_notification_summary():
    session = _session()
    company = _company(session)
    current = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan")

    payload = users_router.get_my_profile(db=session, current_user=_current_user(current)).model_dump()

    assert payload["telegram_notification"] == {"bound": False, "display": None}


def test_get_self_profile_returns_bound_notification_summary():
    session = _session()
    company = _company(session)
    current = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan")
    _notification_account(session, user_id=current.id, telegram_user_id="123456789", telegram_username="ivanov")

    payload = users_router.get_my_profile(db=session, current_user=_current_user(current)).model_dump()

    assert payload["telegram_notification"] == {
        "bound": True,
        "display": "@ivanov (Tg ID: 123456789)",
    }


def test_self_login_change_updates_only_current_user():
    session = _session()
    company = _company(session)
    current = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan")
    other = _user(session, company_id=company.id, full_name="Other User", login="other")
    original_email = current.email

    result = users_router.update_my_login(
        SelfLoginUpdate(login="ivan.new"),
        db=session,
        current_user=_current_user(current),
    )

    session.refresh(current)
    session.refresh(other)
    assert result.ok is True
    assert result.message == "Данные изменены. Войдите снова."
    assert current.login == "ivan.new"
    assert current.email == original_email
    assert other.login == "other"


def test_self_login_change_rejects_duplicate_login():
    session = _session()
    company = _company(session)
    current = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan")
    _user(session, company_id=company.id, full_name="Other User", login="other")

    with pytest.raises(users_router.HTTPException) as exc:
        users_router.update_my_login(
            SelfLoginUpdate(login="other"),
            db=session,
            current_user=_current_user(current),
        )

    session.refresh(current)
    assert exc.value.status_code == 409
    assert exc.value.detail["error"]["code"] == "login_already_used"
    assert exc.value.detail["error"]["message"] == "Этот логин уже используется"
    assert current.login == "ivan"


def test_self_login_change_allows_same_own_login():
    session = _session()
    company = _company(session)
    current = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan")

    result = users_router.update_my_login(
        SelfLoginUpdate(login="ivan"),
        db=session,
        current_user=_current_user(current),
    )

    session.refresh(current)
    assert result.ok is True
    assert current.login == "ivan"


def test_self_password_change_updates_hash_and_new_password_verifies():
    session = _session()
    company = _company(session)
    current = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan", password="old")
    old_hash = current.password_hash

    result = users_router.update_my_password(
        SelfPasswordUpdate(
            current_password="old",
            new_password="new",
            new_password_confirmation="new",
        ),
        db=session,
        current_user=_current_user(current),
    )

    session.refresh(current)
    assert result.ok is True
    assert current.password_hash != old_hash
    assert verify_password("new", current.password_hash) is True
    assert verify_password("old", current.password_hash) is False


def test_self_password_change_rejects_wrong_current_password_without_changing_hash():
    session = _session()
    company = _company(session)
    current = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan", password="old")
    old_hash = current.password_hash

    with pytest.raises(users_router.HTTPException) as exc:
        users_router.update_my_password(
            SelfPasswordUpdate(
                current_password="wrong",
                new_password="new",
                new_password_confirmation="new",
            ),
            db=session,
            current_user=_current_user(current),
        )

    session.refresh(current)
    assert exc.value.status_code == 400
    assert exc.value.detail["error"]["code"] == "current_password_invalid"
    assert current.password_hash == old_hash


def test_self_password_change_rejects_confirmation_mismatch_without_changing_hash():
    session = _session()
    company = _company(session)
    current = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan", password="old")
    old_hash = current.password_hash

    with pytest.raises(users_router.HTTPException) as exc:
        users_router.update_my_password(
            SelfPasswordUpdate(
                current_password="old",
                new_password="new",
                new_password_confirmation="other",
            ),
            db=session,
            current_user=_current_user(current),
        )

    session.refresh(current)
    assert exc.value.status_code == 400
    assert exc.value.detail["error"]["code"] == "password_confirmation_mismatch"
    assert current.password_hash == old_hash


def test_self_payloads_forbid_user_id():
    with pytest.raises(ValidationError):
        SelfLoginUpdate(login="ivan", user_id="other")
    with pytest.raises(ValidationError):
        SelfPasswordUpdate(
            current_password="old",
            new_password="new",
            new_password_confirmation="new",
            user_id="other",
        )


def test_self_routes_do_not_expose_user_id_login_or_password_paths():
    session = _session()
    company = _company(session)
    current = _user(session, company_id=company.id, full_name="Ivan Petrov", login="ivan")
    other = _user(session, company_id=company.id, full_name="Other User", login="other")
    client = _client(session, _current_user(current))

    login_response = client.patch(f"/api/v1/users/{other.id}/login", json={"login": "stolen"})
    password_response = client.patch(
        f"/api/v1/users/{other.id}/password",
        json={
            "current_password": "secret",
            "new_password": "new",
            "new_password_confirmation": "new",
        },
    )

    session.refresh(other)
    assert login_response.status_code == 404
    assert password_response.status_code == 404
    assert other.login == "other"
    assert verify_password("secret", other.password_hash) is True
