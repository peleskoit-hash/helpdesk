from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.db import Base
from api.app.models import Company, User
from api.app.routers import users as users_router


def _session():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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


def _client(session) -> TestClient:
    app = FastAPI()
    app.include_router(users_router.router)
    app.dependency_overrides[users_router.get_db] = lambda: session
    app.dependency_overrides[users_router.get_current_user] = _admin_user
    return TestClient(app)


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
    role: str,
    is_active: bool = True,
    availability_status: str = "online",
    avatar_path: str | None = None,
) -> User:
    now = datetime.utcnow() - timedelta(minutes=7)
    user = User(
        id=str(uuid.uuid4()),
        company_id=company_id,
        email=f"{uuid.uuid4().hex}@local.helpdesk",
        login=f"{full_name.lower().replace(' ', '.')}.{uuid.uuid4().hex[:6]}",
        password_hash="hash",
        avatar_path=avatar_path,
        full_name=full_name,
        role=role,
        is_active=is_active,
        availability_status=availability_status,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    session.commit()
    return user


def test_status_board_returns_avatar_contract_and_preserves_existing_fields():
    session = _session()
    company = _company(session)
    with_avatar = _user(
        session,
        company_id=company.id,
        full_name="Anna Agent",
        role="agent",
        availability_status="waiting",
        avatar_path="avatars/anna.webp",
    )
    without_avatar = _user(
        session,
        company_id=company.id,
        full_name="Tim Trainee",
        role="trainee",
        availability_status="lunch",
    )

    result = users_router.status_board(db=session, current_user=_admin_user())
    payload = [item.model_dump() for item in result]

    assert [item["id"] for item in payload] == [with_avatar.id, without_avatar.id]
    assert payload[0] == {
        "id": with_avatar.id,
        "full_name": "Anna Agent",
        "role": "agent",
        "role_label": "Сотрудник",
        "availability_status": "waiting",
        "updated_at": with_avatar.updated_at,
        "avatar_present": True,
        "avatar_url": f"/api/v1/users/{with_avatar.id}/avatar",
    }
    assert payload[1]["full_name"] == "Tim Trainee"
    assert payload[1]["role"] == "trainee"
    assert payload[1]["role_label"] == "Стажёр"
    assert payload[1]["availability_status"] == "lunch"
    assert payload[1]["updated_at"] == without_avatar.updated_at
    assert payload[1]["avatar_present"] is False
    assert payload[1]["avatar_url"] is None


def test_status_board_employee_scope_rules_are_unchanged():
    session = _session()
    company = _company(session)
    agent = _user(session, company_id=company.id, full_name="Beta Agent", role="agent")
    trainee = _user(session, company_id=company.id, full_name="Alpha Trainee", role="trainee")
    _user(session, company_id=company.id, full_name="Admin User", role="admin")
    _user(session, company_id=company.id, full_name="Chiki User", role="chiki_piki")
    _user(session, company_id=company.id, full_name="Inactive Agent", role="agent", is_active=False)

    result = users_router.status_board(db=session, current_user=_admin_user())

    assert [item.id for item in result] == [agent.id, trainee.id]


def test_regular_agent_endpoint_does_not_expose_status_board_avatar_fields():
    session = _session()
    company = _company(session)
    agent = _user(
        session,
        company_id=company.id,
        full_name="Avatar Agent",
        role="agent",
        avatar_path="avatars/avatar-agent.webp",
    )

    response = _client(session).get("/api/v1/users/agents")

    assert response.status_code == 200
    assert response.json()[0]["id"] == agent.id
    assert "avatar_present" not in response.json()[0]
    assert "avatar_url" not in response.json()[0]
