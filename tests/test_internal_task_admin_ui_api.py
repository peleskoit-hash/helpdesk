from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.auth import CurrentUser
from api.app.config import settings
from api.app.db import Base, get_db
from api.app.models import (
    Client,
    Company,
    InternalTaskAssignmentMode,
    InternalTaskDefinition,
    InternalTaskDefinitionState,
    InternalTaskOccurrence,
    InternalTaskScheduleKind,
    Ticket,
    TicketStatus,
    TicketTaskKind,
    TicketWorkflowType,
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


def _user(session, company: Company, role: str, *, is_active: bool = True, full_name: str | None = None) -> User:
    user_id = str(uuid.uuid4())
    user = User(
        id=user_id,
        company_id=company.id,
        email=f"{user_id}@example.test",
        login=user_id,
        full_name=full_name or f"{role}-{user_id[:8]}",
        role=role,
        is_active=is_active,
        availability_status="offline",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(user)
    return user


def _payload(**overrides) -> dict:
    future = datetime.now(timezone.utc) + timedelta(days=1)
    payload = {
        "title": "Запланировать проверку",
        "description": "Проверить договор",
        "assignment_mode": InternalTaskAssignmentMode.DISTRIBUTION.value,
        "assignee_user_id": None,
        "display_author": True,
        "responsible_admin_id": None,
        "notify_on_completion": True,
        "scheduled_for": future.isoformat(),
        "deadline_enabled": False,
        "deadline_at": None,
    }
    payload.update(overrides)
    return payload


def _error_code(response) -> str:
    return response.json()["detail"]["error"]["code"]


def _definition(
    session,
    company: Company,
    creator: User,
    responsible: User,
    *,
    title: str,
    schedule_kind: str = InternalTaskScheduleKind.SCHEDULED.value,
    state: str = InternalTaskDefinitionState.ACTIVE.value,
    scheduled_for_utc: datetime | None = None,
    assignee: User | None = None,
) -> InternalTaskDefinition:
    now = datetime.utcnow()
    definition = InternalTaskDefinition(
        id=str(uuid.uuid4()),
        company_id=company.id,
        title=title,
        description=f"{title} description",
        creator_admin_id=creator.id,
        responsible_admin_id=responsible.id,
        display_author=True,
        assignment_mode=InternalTaskAssignmentMode.DIRECT.value if assignee else InternalTaskAssignmentMode.DISTRIBUTION.value,
        assignee_user_id=assignee.id if assignee else None,
        notify_on_completion=True,
        deadline_at_utc=None,
        schedule_kind=schedule_kind,
        timezone_name="Europe/Moscow",
        scheduled_for_utc=scheduled_for_utc or (now + timedelta(days=1)),
        state=state,
        created_at=now,
        updated_at=now,
    )
    session.add(definition)
    return definition


def _materialized(
    session,
    company: Company,
    definition: InternalTaskDefinition,
    assignee: User | None,
    *,
    status: str,
) -> tuple[InternalTaskOccurrence, Ticket]:
    now = datetime.utcnow()
    client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Система")
    ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        assignee_id=assignee.id if assignee else None,
        subject=definition.title,
        status=status,
        workflow_type=TicketWorkflowType.TASK.value,
        task_kind=TicketTaskKind.NORMAL.value,
        created_at=now,
        updated_at=now,
        closed_at=now if status == TicketStatus.CLOSED.value else None,
    )
    occurrence = InternalTaskOccurrence(
        id=str(uuid.uuid4()),
        definition_id=definition.id,
        occurrence_at_utc=definition.scheduled_for_utc or now,
        ticket_id=ticket.id,
        title_snapshot=definition.title,
        description_snapshot=definition.description,
        creator_admin_id=definition.creator_admin_id,
        responsible_admin_id=definition.responsible_admin_id,
        display_author=True,
        effective_assignment_mode=InternalTaskAssignmentMode.DIRECT.value if assignee else InternalTaskAssignmentMode.DISTRIBUTION.value,
        effective_assignee_user_id=assignee.id if assignee else None,
        notify_on_completion=True,
        materialized_at_utc=now,
        created_at=now,
    )
    session.add_all([client, ticket, occurrence])
    return occurrence, ticket


def test_admin_can_create_scheduled_one_off_without_materialization():
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    agent = _user(session, company, "agent")
    session.commit()
    scheduled_for = datetime.now(timezone.utc) + timedelta(days=1)

    response = _client(session, admin).post(
        "/api/v1/management/internal-tasks/scheduled",
        json=_payload(assignment_mode="direct", assignee_user_id=agent.id, scheduled_for=scheduled_for.isoformat()),
    )

    assert response.status_code == 200
    body = response.json()
    definition = session.get(InternalTaskDefinition, body["definition_id"])
    assert definition.schedule_kind == InternalTaskScheduleKind.SCHEDULED.value
    assert definition.state == InternalTaskDefinitionState.ACTIVE.value
    assert abs((definition.scheduled_for_utc.replace(tzinfo=timezone.utc) - scheduled_for).total_seconds()) < 1
    assert definition.assignee_user_id == agent.id
    assert session.execute(select(InternalTaskOccurrence)).scalars().all() == []
    assert session.execute(select(Ticket)).scalars().all() == []


def test_scheduled_create_interprets_naive_datetime_in_app_timezone(monkeypatch):
    monkeypatch.setattr(settings, "app_timezone", "Europe/Moscow")
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    session.commit()

    response = _client(session, admin).post(
        "/api/v1/management/internal-tasks/scheduled",
        json=_payload(scheduled_for="2026-09-17T13:00:00", deadline_enabled=True, deadline_at="2026-09-17T14:00:00"),
    )

    assert response.status_code == 200
    definition = session.get(InternalTaskDefinition, response.json()["definition_id"])
    assert definition.scheduled_for_utc.replace(tzinfo=timezone.utc) == datetime(2026, 9, 17, 10, 0, tzinfo=timezone.utc)
    assert definition.deadline_at_utc.replace(tzinfo=timezone.utc) == datetime(2026, 9, 17, 11, 0, tzinfo=timezone.utc)


def test_scheduled_create_normalizes_aware_datetime_to_utc(monkeypatch):
    monkeypatch.setattr(settings, "app_timezone", "Europe/Moscow")
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    session.commit()

    response = _client(session, admin).post(
        "/api/v1/management/internal-tasks/scheduled",
        json=_payload(scheduled_for="2026-09-17T13:00:00+05:00"),
    )

    assert response.status_code == 200
    definition = session.get(InternalTaskDefinition, response.json()["definition_id"])
    assert definition.scheduled_for_utc.replace(tzinfo=timezone.utc) == datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc)


def test_scheduled_create_requires_admin_and_rejects_past_datetime():
    session = _session()
    company = _company(session)
    agent = _user(session, company, "agent")
    admin = _user(session, company, "admin")
    session.commit()

    forbidden = _client(session, agent).post("/api/v1/management/internal-tasks/scheduled", json=_payload())
    past = _client(session, admin).post(
        "/api/v1/management/internal-tasks/scheduled",
        json=_payload(scheduled_for=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()),
    )

    assert forbidden.status_code == 403
    assert _error_code(forbidden) == "admin_only"
    assert past.status_code == 422
    assert _error_code(past) == "scheduled_for_past"


@pytest.mark.parametrize("role", ["agent", "trainee"])
def test_scheduled_direct_accepts_same_company_support_roles_including_inactive(role):
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    assignee = _user(session, company, role, is_active=False)
    session.commit()

    response = _client(session, admin).post(
        "/api/v1/management/internal-tasks/scheduled",
        json=_payload(assignment_mode="direct", assignee_user_id=assignee.id),
    )

    assert response.status_code == 200
    assert session.get(InternalTaskDefinition, response.json()["definition_id"]).assignee_user_id == assignee.id


def test_scheduled_create_rejects_wrong_role_cross_company_and_missing_assignee():
    session = _session()
    company = _company(session)
    other = _company(session, "Other")
    admin = _user(session, company, "admin")
    wrong_role = _user(session, company, "admin")
    other_agent = _user(session, other, "agent")
    session.commit()
    client = _client(session, admin)

    missing = client.post("/api/v1/management/internal-tasks/scheduled", json=_payload(assignment_mode="direct", assignee_user_id="missing"))
    wrong = client.post("/api/v1/management/internal-tasks/scheduled", json=_payload(assignment_mode="direct", assignee_user_id=wrong_role.id))
    cross = client.post("/api/v1/management/internal-tasks/scheduled", json=_payload(assignment_mode="direct", assignee_user_id=other_agent.id))

    assert missing.status_code == 404
    assert _error_code(missing) == "assignee_not_found"
    assert wrong.status_code == 422
    assert _error_code(wrong) == "assignee_invalid_role"
    assert cross.status_code == 404
    assert _error_code(cross) == "assignee_not_found"


def test_scheduled_create_validates_responsible_admin():
    session = _session()
    company = _company(session)
    other = _company(session, "Other")
    admin = _user(session, company, "admin")
    inactive_admin = _user(session, company, "admin", is_active=False)
    agent = _user(session, company, "agent")
    other_admin = _user(session, other, "admin")
    session.commit()
    client = _client(session, admin)

    ok = client.post("/api/v1/management/internal-tasks/scheduled", json=_payload(responsible_admin_id=inactive_admin.id))
    wrong = client.post("/api/v1/management/internal-tasks/scheduled", json=_payload(responsible_admin_id=agent.id))
    cross = client.post("/api/v1/management/internal-tasks/scheduled", json=_payload(responsible_admin_id=other_admin.id))

    assert ok.status_code == 200
    assert ok.json()["responsible_admin"]["id"] == inactive_admin.id
    assert wrong.status_code == 422
    assert _error_code(wrong) == "responsible_admin_invalid_role"
    assert cross.status_code == 404
    assert _error_code(cross) == "responsible_admin_not_found"


def test_overview_classifies_by_occurrence_and_ticket_status_with_company_and_mine_filters():
    session = _session()
    company = _company(session)
    other = _company(session, "Other")
    admin = _user(session, company, "admin", full_name="Admin One")
    other_admin = _user(session, company, "admin", full_name="Admin Two")
    agent = _user(session, company, "agent")
    foreign_admin = _user(session, other, "admin")
    scheduled = _definition(session, company, admin, other_admin, title="Scheduled mine")
    active_definition = _definition(session, company, other_admin, admin, title="Active mine", state=InternalTaskDefinitionState.FINISHED.value)
    completed_definition = _definition(session, company, other_admin, other_admin, title="Completed other", state=InternalTaskDefinitionState.FINISHED.value)
    scheduled_finished_not_completed = _definition(session, company, admin, admin, title="Finished without occurrence", state=InternalTaskDefinitionState.FINISHED.value)
    foreign_definition = _definition(session, other, foreign_admin, foreign_admin, title="Foreign")
    _materialized(session, company, active_definition, agent, status=TicketStatus.PENDING.value)
    _materialized(session, company, completed_definition, agent, status=TicketStatus.CLOSED.value)
    _materialized(session, other, foreign_definition, None, status=TicketStatus.PENDING.value)
    session.commit()

    all_body = _client(session, admin).get("/api/v1/management/internal-tasks/overview").json()
    mine_body = _client(session, admin).get("/api/v1/management/internal-tasks/overview", params={"scope": "mine"}).json()

    assert [item["title"] for item in all_body["scheduled"]] == [scheduled.title]
    assert [item["title"] for item in all_body["active"]] == [active_definition.title]
    assert [item["title"] for item in all_body["completed"]] == [completed_definition.title]
    assert scheduled_finished_not_completed.title not in [item["title"] for item in all_body["completed"]]
    assert foreign_definition.title not in str(all_body)
    assert [item["title"] for item in mine_body["scheduled"]] == [scheduled.title]
    assert [item["title"] for item in mine_body["active"]] == [active_definition.title]
    assert mine_body["completed"] == []


def test_edit_scheduled_definition_persists_allowed_fields_and_keeps_creator():
    session = _session()
    company = _company(session)
    creator = _user(session, company, "admin")
    editor = _user(session, company, "admin")
    responsible = _user(session, company, "admin")
    assignee = _user(session, company, "trainee")
    definition = _definition(session, company, creator, creator, title="Before")
    session.commit()

    response = _client(session, editor).patch(
        f"/api/v1/management/internal-tasks/scheduled/{definition.id}",
        json=_payload(
            title="After",
            description="New description",
            assignment_mode="direct",
            assignee_user_id=assignee.id,
            display_author=False,
            responsible_admin_id=responsible.id,
            notify_on_completion=False,
            deadline_enabled=True,
            deadline_at=(datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        ),
    )

    assert response.status_code == 200
    session.refresh(definition)
    assert definition.title == "After"
    assert definition.description == "New description"
    assert definition.creator_admin_id == creator.id
    assert definition.assignee_user_id == assignee.id
    assert definition.display_author is False
    assert definition.responsible_admin_id == responsible.id
    assert definition.notify_on_completion is False
    assert definition.schedule_kind == InternalTaskScheduleKind.SCHEDULED.value


def test_edit_rejects_materialized_foreign_invalid_users_and_past_time():
    session = _session()
    company = _company(session)
    other = _company(session, "Other")
    admin = _user(session, company, "admin")
    other_admin = _user(session, other, "admin")
    wrong_assignee = _user(session, company, "admin")
    definition = _definition(session, company, admin, admin, title="Editable")
    materialized_definition = _definition(session, company, admin, admin, title="Done")
    _materialized(session, company, materialized_definition, None, status=TicketStatus.PENDING.value)
    session.commit()
    client = _client(session, admin)

    materialized = client.patch(f"/api/v1/management/internal-tasks/scheduled/{materialized_definition.id}", json=_payload())
    foreign = _client(session, other_admin).patch(f"/api/v1/management/internal-tasks/scheduled/{definition.id}", json=_payload())
    invalid_assignee = client.patch(
        f"/api/v1/management/internal-tasks/scheduled/{definition.id}",
        json=_payload(assignment_mode="direct", assignee_user_id=wrong_assignee.id),
    )
    past = client.patch(
        f"/api/v1/management/internal-tasks/scheduled/{definition.id}",
        json=_payload(scheduled_for=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()),
    )

    assert materialized.status_code == 409
    assert _error_code(materialized) == "scheduled_definition_materialized"
    assert foreign.status_code == 404
    assert _error_code(foreign) == "scheduled_definition_not_found"
    assert invalid_assignee.status_code == 422
    assert _error_code(invalid_assignee) == "assignee_invalid_role"
    assert past.status_code == 422
    assert _error_code(past) == "scheduled_for_past"
