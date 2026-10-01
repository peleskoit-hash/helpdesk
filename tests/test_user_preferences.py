from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import Company, User
from api.app.schemas import UserPreferencesUpdate

try:
    from api.app.routers import users as users_router
except Exception as exc:
    if exc.__class__.__name__ == "ZoneInfoNotFoundError":
        pytest.skip("Europe/Moscow tzdata is unavailable in the host test environment", allow_module_level=True)
    raise


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
    role: str = "agent",
    send_by_enter=None,
    color_enabled=None,
) -> User:
    user = User(
        id=str(uuid.uuid4()),
        company_id=company_id,
        email=None,
        login=f"{full_name.lower().replace(' ', '.')}.{uuid.uuid4().hex[:6]}",
        password_hash=None,
        full_name=full_name,
        role=role,
        is_active=True,
        availability_status="online",
        legacy_ticket_count=0,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    if send_by_enter is not None:
        user.send_by_enter = send_by_enter
    if color_enabled is not None:
        user.color_enabled = color_enabled
    session.add(user)
    session.commit()
    return user


def _current_user(*, effective_user_id: str, real_user_id: str, role: str = "agent"):
    return SimpleNamespace(
        id=effective_user_id,
        role=role,
        real_user_id=real_user_id,
        real_role=role,
        is_real_admin=(role == "admin"),
    )


def test_user_preferences_default_is_false_for_new_user():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov")

    result = users_router.get_my_preferences(
        db=session,
        current_user=_current_user(effective_user_id=user.id, real_user_id=user.id),
    )

    assert result.send_by_enter is False
    assert result.color_enabled is True


def test_user_preferences_patch_true_persists_and_roundtrips():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov")
    current_user = _current_user(effective_user_id=user.id, real_user_id=user.id)

    updated = users_router.update_my_preferences(
        UserPreferencesUpdate(send_by_enter=True),
        db=session,
        current_user=current_user,
    )
    loaded = users_router.get_my_preferences(db=session, current_user=current_user)

    session.refresh(user)
    assert updated.send_by_enter is True
    assert updated.color_enabled is True
    assert user.send_by_enter is True
    assert user.color_enabled is True
    assert loaded.send_by_enter is True
    assert loaded.color_enabled is True


def test_user_preferences_patch_false_after_true_persists():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov", send_by_enter=True)
    current_user = _current_user(effective_user_id=user.id, real_user_id=user.id)

    updated = users_router.update_my_preferences(
        UserPreferencesUpdate(send_by_enter=False),
        db=session,
        current_user=current_user,
    )

    session.refresh(user)
    assert updated.send_by_enter is False
    assert user.send_by_enter is False


def test_user_preferences_patch_color_false_persists_without_changing_send_by_enter():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov", send_by_enter=True)
    current_user = _current_user(effective_user_id=user.id, real_user_id=user.id)

    updated = users_router.update_my_preferences(
        UserPreferencesUpdate(color_enabled=False),
        db=session,
        current_user=current_user,
    )

    session.refresh(user)
    assert updated.color_enabled is False
    assert updated.send_by_enter is True
    assert user.color_enabled is False
    assert user.send_by_enter is True


def test_user_preferences_patch_color_true_after_false_persists():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov", color_enabled=False)
    current_user = _current_user(effective_user_id=user.id, real_user_id=user.id)

    updated = users_router.update_my_preferences(
        UserPreferencesUpdate(color_enabled=True),
        db=session,
        current_user=current_user,
    )

    session.refresh(user)
    assert updated.color_enabled is True
    assert user.color_enabled is True


def test_user_preferences_patch_send_by_enter_does_not_change_color_enabled():
    session = _session()
    company = _company(session)
    user = _user(session, company_id=company.id, full_name="Ivan Petrov", color_enabled=False)
    current_user = _current_user(effective_user_id=user.id, real_user_id=user.id)

    updated = users_router.update_my_preferences(
        UserPreferencesUpdate(send_by_enter=True),
        db=session,
        current_user=current_user,
    )

    session.refresh(user)
    assert updated.send_by_enter is True
    assert updated.color_enabled is False
    assert user.send_by_enter is True
    assert user.color_enabled is False


def test_user_preferences_are_isolated_between_users():
    session = _session()
    company = _company(session)
    user_a = _user(session, company_id=company.id, full_name="User A")
    user_b = _user(session, company_id=company.id, full_name="User B")

    users_router.update_my_preferences(
        UserPreferencesUpdate(send_by_enter=True, color_enabled=False),
        db=session,
        current_user=_current_user(effective_user_id=user_a.id, real_user_id=user_a.id),
    )

    session.refresh(user_a)
    session.refresh(user_b)
    assert user_a.send_by_enter is True
    assert user_a.color_enabled is False
    assert user_b.send_by_enter is False
    assert user_b.color_enabled is True


def test_user_preferences_use_real_user_id_not_effective_user_id():
    session = _session()
    company = _company(session)
    real_user = _user(session, company_id=company.id, full_name="Real Admin", role="admin")
    effective_user = _user(session, company_id=company.id, full_name="Effective Agent", role="agent")

    users_router.update_my_preferences(
        UserPreferencesUpdate(send_by_enter=True, color_enabled=False),
        db=session,
        current_user=_current_user(
            effective_user_id=effective_user.id,
            real_user_id=real_user.id,
            role=effective_user.role,
        ),
    )

    session.refresh(real_user)
    session.refresh(effective_user)
    assert real_user.send_by_enter is True
    assert real_user.color_enabled is False
    assert effective_user.send_by_enter is False
    assert effective_user.color_enabled is True
