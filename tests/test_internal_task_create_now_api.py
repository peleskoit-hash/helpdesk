from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.auth import CurrentUser
from api.app.db import Base, get_db
from api.app.models import (
    Company,
    InternalTaskAssignmentMode,
    InternalTaskDefinition,
    InternalTaskDefinitionState,
    InternalTaskOccurrence,
    InternalTaskScheduleKind,
    Ticket,
    TicketStatus,
    User,
)
from api.app.routers import internal_tasks


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


def _current_user(user: User) -> CurrentUser:
    return CurrentUser(
        id=user.id,
        role=user.role,
        real_user_id=user.id,
        real_role=user.role,
        is_real_admin=user.role == "admin",
    )


def _client(session, user: User) -> TestClient:
    app = FastAPI()
    app.include_router(internal_tasks.router)
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[internal_tasks.get_current_user] = lambda: _current_user(user)
    return TestClient(app)


def _company(session, name: str = "ACME") -> Company:
    company = Company(id=str(uuid.uuid4()), name=name, is_active=True, created_at=datetime.utcnow())
    session.add(company)
    return company


def _user(
    session,
    company: Company,
    role: str,
    *,
    is_active: bool = True,
    availability_status: str = "offline",
) -> User:
    user_id = str(uuid.uuid4())
    user = User(
        id=user_id,
        company_id=company.id,
        email=f"{user_id}@example.test",
        login=user_id,
        full_name=f"{role}-{user_id[:8]}",
        role=role,
        is_active=is_active,
        availability_status=availability_status,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(user)
    return user


def _payload(**overrides) -> dict:
    payload = {
        "title": "  Проверить договор  ",
        "description": "  Уточнить статус и закрыть вопрос.  ",
        "assignment_mode": InternalTaskAssignmentMode.DISTRIBUTION.value,
        "display_author": True,
        "responsible_admin_id": None,
        "notify_on_completion": False,
        "deadline_enabled": False,
        "deadline_at": None,
    }
    payload.update(overrides)
    return payload


def _error_code(response) -> str:
    return response.json()["detail"]["error"]["code"]


def test_create_now_distribution_creates_definition_occurrence_and_assigned_internal_ticket():
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    online_agent = _user(session, company, "agent", availability_status="online")
    session.commit()

    response = _client(session, admin).post("/api/v1/management/internal-tasks", json=_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    assert body["state"] == InternalTaskDefinitionState.FINISHED.value
    assert body["title"] == "Проверить договор"
    assert body["assignment_mode"] == InternalTaskAssignmentMode.DISTRIBUTION.value
    assert body["effective_assignment_mode"] == InternalTaskAssignmentMode.DISTRIBUTION.value
    assert body["assignee_user_id"] is None
    assert body["effective_assignee_user_id"] is None
    assert body["responsible_admin_id"] == admin.id
    assert body["display_author"] is True
    assert body["notify_on_completion"] is False
    assert body["deadline_at"] is None

    definition = session.get(InternalTaskDefinition, body["definition_id"])
    occurrence = session.get(InternalTaskOccurrence, body["occurrence_id"])
    ticket = session.get(Ticket, body["ticket_id"])
    assert definition.creator_admin_id == admin.id
    assert definition.responsible_admin_id == admin.id
    assert definition.schedule_kind == InternalTaskScheduleKind.IMMEDIATE.value
    assert definition.scheduled_for_utc is None
    assert definition.recurrence_mode is None
    assert definition.cycle_start_at_utc is None
    assert occurrence.definition_id == definition.id
    assert occurrence.display_author is True
    assert occurrence.notify_on_completion is False
    assert ticket.assignee_id == online_agent.id
    assert ticket.status == TicketStatus.NEW.value


@pytest.mark.parametrize("role", ["agent", "trainee"])
def test_create_now_direct_accepts_active_offline_support_user_and_keeps_author_flag_false(role):
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    assignee = _user(session, company, role, availability_status="offline")
    session.commit()

    response = _client(session, admin).post(
        "/api/v1/management/internal-tasks",
        json=_payload(
            assignment_mode=InternalTaskAssignmentMode.DIRECT.value,
            assignee_user_id=assignee.id,
            display_author=False,
            notify_on_completion=True,
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["assignee_user_id"] == assignee.id
    assert body["effective_assignment_mode"] == InternalTaskAssignmentMode.DIRECT.value
    assert body["effective_assignee_user_id"] == assignee.id
    assert body["display_author"] is False
    assert body["notify_on_completion"] is True
    assert session.get(Ticket, body["ticket_id"]).assignee_id == assignee.id


def test_create_now_direct_inactive_agent_is_accepted_and_materializes_as_distribution_fallback():
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    inactive_agent = _user(session, company, "agent", is_active=False, availability_status="online")
    session.commit()

    response = _client(session, admin).post(
        "/api/v1/management/internal-tasks",
        json=_payload(assignment_mode=InternalTaskAssignmentMode.DIRECT.value, assignee_user_id=inactive_agent.id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["assignment_mode"] == InternalTaskAssignmentMode.DIRECT.value
    assert body["assignee_user_id"] == inactive_agent.id
    assert body["effective_assignment_mode"] == InternalTaskAssignmentMode.DISTRIBUTION.value
    assert body["effective_assignee_user_id"] is None
    assert session.get(InternalTaskOccurrence, body["occurrence_id"]).effective_assignee_user_id is None
    assert session.get(Ticket, body["ticket_id"]).assignee_id is None


def test_create_now_uses_provided_inactive_responsible_admin_from_same_company():
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    responsible = _user(session, company, "admin", is_active=False)
    session.commit()

    response = _client(session, admin).post(
        "/api/v1/management/internal-tasks",
        json=_payload(responsible_admin_id=responsible.id),
    )

    assert response.status_code == 200
    assert response.json()["responsible_admin_id"] == responsible.id


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"title": "   "}, "title_required"),
        ({"description": "   "}, "description_required"),
        ({"title": "x" * 256}, "title_too_long"),
        ({"assignment_mode": "unsupported"}, "unsupported_assignment_mode"),
        (
            {"assignment_mode": InternalTaskAssignmentMode.DIRECT.value, "assignee_user_id": None},
            "assignee_required",
        ),
        ({"assignee_user_id": "some-user"}, "assignee_not_allowed"),
        ({"deadline_enabled": True, "deadline_at": None}, "deadline_required"),
        ({"deadline_enabled": False, "deadline_at": "2026-09-16T10:00:00Z"}, "deadline_not_allowed"),
    ],
)
def test_create_now_rejects_invalid_business_payloads(overrides, code):
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    session.commit()

    response = _client(session, admin).post("/api/v1/management/internal-tasks", json=_payload(**overrides))

    assert response.status_code == 422
    assert _error_code(response) == code


def test_create_now_rejects_scheduled_and_recurrence_fields_from_client():
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    session.commit()

    payload = _payload(schedule_kind="scheduled", scheduled_for_utc="2026-09-16T10:00:00Z", recurrence_mode="daily")
    response = _client(session, admin).post("/api/v1/management/internal-tasks", json=payload)

    assert response.status_code == 422


def test_create_now_rejects_direct_assignee_from_other_company_without_leakage():
    session = _session()
    company = _company(session)
    other_company = _company(session, "Other")
    admin = _user(session, company, "admin")
    other_agent = _user(session, other_company, "agent", availability_status="online")
    session.commit()

    response = _client(session, admin).post(
        "/api/v1/management/internal-tasks",
        json=_payload(assignment_mode=InternalTaskAssignmentMode.DIRECT.value, assignee_user_id=other_agent.id),
    )

    assert response.status_code == 404
    assert _error_code(response) == "assignee_not_found"


def test_create_now_rejects_direct_assignee_wrong_role():
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    wrong_role = _user(session, company, "admin")
    session.commit()
    client = _client(session, admin)

    wrong_role_response = client.post(
        "/api/v1/management/internal-tasks",
        json=_payload(assignment_mode=InternalTaskAssignmentMode.DIRECT.value, assignee_user_id=wrong_role.id),
    )

    assert wrong_role_response.status_code == 422
    assert _error_code(wrong_role_response) == "assignee_invalid_role"


def test_create_now_rejects_responsible_admin_from_other_company_or_wrong_role():
    session = _session()
    company = _company(session)
    other_company = _company(session, "Other")
    admin = _user(session, company, "admin")
    other_admin = _user(session, other_company, "admin")
    agent = _user(session, company, "agent")
    session.commit()
    client = _client(session, admin)

    other_company_response = client.post(
        "/api/v1/management/internal-tasks",
        json=_payload(responsible_admin_id=other_admin.id),
    )
    wrong_role_response = client.post(
        "/api/v1/management/internal-tasks",
        json=_payload(responsible_admin_id=agent.id),
    )

    assert other_company_response.status_code == 404
    assert _error_code(other_company_response) == "responsible_admin_not_found"
    assert wrong_role_response.status_code == 422
    assert _error_code(wrong_role_response) == "responsible_admin_invalid_role"


def test_create_now_requires_admin():
    session = _session()
    company = _company(session)
    agent = _user(session, company, "agent")
    session.commit()

    response = _client(session, agent).post("/api/v1/management/internal-tasks", json=_payload())

    assert response.status_code == 403
    assert _error_code(response) == "admin_only"


def test_create_now_rejects_non_admin_trainee():
    session = _session()
    company = _company(session)
    trainee = _user(session, company, "trainee")
    session.commit()

    response = _client(session, trainee).post("/api/v1/management/internal-tasks", json=_payload())

    assert response.status_code == 403
    assert _error_code(response) == "admin_only"


def test_create_now_normalizes_deadline_to_utc():
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    session.commit()

    response = _client(session, admin).post(
        "/api/v1/management/internal-tasks",
        json=_payload(deadline_enabled=True, deadline_at="2026-09-16T13:30:00+03:00"),
    )

    assert response.status_code == 200
    definition = session.get(InternalTaskDefinition, response.json()["definition_id"])
    assert definition.deadline_at_utc.replace(tzinfo=timezone.utc) == datetime(2026, 9, 16, 10, 30, 0, tzinfo=timezone.utc)


def test_create_now_has_no_external_idempotency():
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    session.commit()
    client = _client(session, admin)

    first = client.post("/api/v1/management/internal-tasks", json=_payload())
    second = client.post("/api/v1/management/internal-tasks", json=_payload())

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["definition_id"] != second.json()["definition_id"]
    assert first.json()["ticket_id"] != second.json()["ticket_id"]
    assert session.execute(select(InternalTaskDefinition)).scalars().all()
    assert len(session.execute(select(InternalTaskOccurrence)).scalars().all()) == 2


def test_create_now_rolls_back_definition_when_materialization_fails(monkeypatch):
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    session.commit()

    def fail_materialization(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(internal_tasks, "materialize_internal_task", fail_materialization)

    with pytest.raises(RuntimeError):
        _client(session, admin).post("/api/v1/management/internal-tasks", json=_payload())

    assert session.execute(select(InternalTaskDefinition)).scalars().all() == []
    assert session.execute(select(InternalTaskOccurrence)).scalars().all() == []
    assert session.execute(select(Ticket)).scalars().all() == []
