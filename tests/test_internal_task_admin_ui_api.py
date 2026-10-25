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
from api.app.routers import internal_tasks, tickets
from api.app.services import internal_task_scheduler
from api.app.services.ticket_list import list_tickets_paginated


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


def _tickets_client(session, user: User) -> TestClient:
    app = FastAPI()
    app.include_router(tickets.router)
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[tickets.get_current_user] = lambda: _current_user(user)
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


def _recurring_payload(**overrides) -> dict:
    future = datetime.now(timezone.utc) + timedelta(days=1)
    payload = {
        "title": "Циклическая проверка",
        "description": "Проверить регламент",
        "assignment_mode": InternalTaskAssignmentMode.DISTRIBUTION.value,
        "assignee_user_id": None,
        "display_author": True,
        "responsible_admin_id": None,
        "notify_on_completion": True,
        "deadline_enabled": False,
        "deadline_at": None,
        "recurrence_mode": "daily",
        "recurrence_config": {"times": ["10:00", "16:00"]},
        "cycle_start_at": future.isoformat(),
        "cycle_end_mode": "never",
        "cycle_end_at": None,
        "cycle_max_occurrences": None,
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
    recurrence_mode: str | None = None,
    recurrence_config: dict | None = None,
    cycle_start_at_utc: datetime | None = None,
    cycle_end_mode: str | None = None,
    cycle_end_at_utc: datetime | None = None,
    cycle_max_occurrences: int | None = None,
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
        scheduled_for_utc=None if schedule_kind == InternalTaskScheduleKind.RECURRING.value else (scheduled_for_utc or (now + timedelta(days=1))),
        recurrence_mode=recurrence_mode,
        recurrence_config=recurrence_config,
        cycle_start_at_utc=cycle_start_at_utc,
        cycle_end_mode=cycle_end_mode,
        cycle_end_at_utc=cycle_end_at_utc,
        cycle_max_occurrences=cycle_max_occurrences,
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
    display_author: bool = True,
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
        display_author=display_author,
        effective_assignment_mode=InternalTaskAssignmentMode.DIRECT.value if assignee else InternalTaskAssignmentMode.DISTRIBUTION.value,
        effective_assignee_user_id=assignee.id if assignee else None,
        notify_on_completion=True,
        materialized_at_utc=now,
        created_at=now,
    )
    session.add_all([client, ticket, occurrence])
    return occurrence, ticket


def test_internal_task_ticket_display_author_overrides_visible_requester_only_for_internal_tasks():
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin", full_name="Admin One")
    agent = _user(session, company, "agent")
    show_definition = _definition(session, company, admin, admin, title="Show author", state=InternalTaskDefinitionState.FINISHED.value)
    hide_definition = _definition(session, company, admin, admin, title="Hide author", state=InternalTaskDefinitionState.FINISHED.value)
    _, show_ticket = _materialized(session, company, show_definition, agent, status=TicketStatus.PENDING.value, display_author=True)
    _, hide_ticket = _materialized(session, company, hide_definition, agent, status=TicketStatus.PENDING.value, display_author=False)
    ordinary_client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Ordinary Client")
    ordinary_ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=ordinary_client.id,
        subject="Ordinary",
        status=TicketStatus.PENDING.value,
        workflow_type=TicketWorkflowType.TICKET.value,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add_all([ordinary_client, ordinary_ticket])
    session.commit()

    payload = list_tickets_paginated(
        db=session,
        user=_current_user(admin),
        statuses=[TicketStatus.PENDING.value],
        is_frozen=False,
        sort="created_at",
        order="asc",
        page=1,
        limit=10,
        filter_admin_assignees=False,
    )
    by_subject = {item.subject: item for item in payload.items}

    assert by_subject["Show author"].requester_display_name == "Admin One"
    assert by_subject["Show author"].client_name == "Admin One"
    assert by_subject["Hide author"].requester_display_name == "Система"
    assert by_subject["Hide author"].client_name == "Система"
    assert by_subject["Ordinary"].requester_display_name == "Ordinary Client"
    assert by_subject["Ordinary"].client_name == "Ordinary Client"

    card_show = _tickets_client(session, admin).get(f"/api/v1/tickets/{show_ticket.id}")
    card_hide = _tickets_client(session, admin).get(f"/api/v1/tickets/{hide_ticket.id}")
    card_ordinary = _tickets_client(session, admin).get(f"/api/v1/tickets/{ordinary_ticket.id}")
    assert card_show.status_code == 200
    assert card_show.json()["client_name"] == "Admin One"
    assert card_hide.status_code == 200
    assert card_hide.json()["client_name"] == "Система"
    assert card_ordinary.status_code == 200
    assert card_ordinary.json()["client_name"] == "Ordinary Client"


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
    class FrozenDateTime(datetime):
        @classmethod
        def utcnow(cls):
            return datetime(2026, 9, 16)

    monkeypatch.setattr(internal_tasks, "datetime", FrozenDateTime)
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
    class FrozenDateTime(datetime):
        @classmethod
        def utcnow(cls):
            return datetime(2026, 9, 16)

    monkeypatch.setattr(internal_tasks, "datetime", FrozenDateTime)
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


@pytest.mark.parametrize(
    ("mode", "config", "expected"),
    [
        ("daily", {"times": "16:00,10:00,10.00"}, {"times": ["10:00", "16:00"]}),
        ("selected_weekdays", {"weekdays": [5, 1, 5], "time": "9.05"}, {"weekdays": [1, 5], "time": "09:05"}),
        ("weekly", {"weekday": 3, "time": "10.00"}, {"time": "10:00"}),
        ("monthly", {"day": "last", "time": "10:00"}, {"day": "last", "time": "10:00"}),
        ("every_n_days", {"interval_days": 3, "time": "10:00"}, {"interval_days": 3, "time": "10:00"}),
    ],
)
def test_create_recurring_persists_domain_contract_for_supported_modes(mode, config, expected):
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    session.commit()

    response = _client(session, admin).post(
        "/api/v1/management/internal-tasks/recurring",
        json=_recurring_payload(recurrence_mode=mode, recurrence_config=config),
    )

    assert response.status_code == 200
    definition = session.get(InternalTaskDefinition, response.json()["definition_id"])
    assert definition.schedule_kind == InternalTaskScheduleKind.RECURRING.value
    assert definition.state == InternalTaskDefinitionState.ACTIVE.value
    assert definition.recurrence_mode == mode
    assert definition.recurrence_config == expected
    assert session.execute(select(InternalTaskOccurrence)).scalars().all() == []
    assert session.execute(select(Ticket)).scalars().all() == []


def test_create_recurring_validates_admin_company_users_and_allows_inactive_support_roles():
    session = _session()
    company = _company(session)
    other = _company(session, "Other")
    admin = _user(session, company, "admin")
    agent = _user(session, company, "agent")
    trainee = _user(session, company, "trainee", is_active=False)
    inactive_responsible = _user(session, company, "admin", is_active=False)
    wrong_role = _user(session, company, "admin")
    other_agent = _user(session, other, "agent")
    session.commit()

    forbidden = _client(session, agent).post("/api/v1/management/internal-tasks/recurring", json=_recurring_payload())
    trainee_ok = _client(session, admin).post(
        "/api/v1/management/internal-tasks/recurring",
        json=_recurring_payload(
            assignment_mode="direct",
            assignee_user_id=trainee.id,
            responsible_admin_id=inactive_responsible.id,
        ),
    )
    wrong = _client(session, admin).post(
        "/api/v1/management/internal-tasks/recurring",
        json=_recurring_payload(assignment_mode="direct", assignee_user_id=wrong_role.id),
    )
    cross = _client(session, admin).post(
        "/api/v1/management/internal-tasks/recurring",
        json=_recurring_payload(assignment_mode="direct", assignee_user_id=other_agent.id),
    )

    assert forbidden.status_code == 403
    assert _error_code(forbidden) == "admin_only"
    assert trainee_ok.status_code == 200
    definition = session.get(InternalTaskDefinition, trainee_ok.json()["definition_id"])
    assert definition.assignee_user_id == trainee.id
    assert definition.responsible_admin_id == inactive_responsible.id
    assert wrong.status_code == 422
    assert _error_code(wrong) == "assignee_invalid_role"
    assert cross.status_code == 404
    assert _error_code(cross) == "assignee_not_found"


def test_update_recurring_keeps_creator_kind_and_existing_occurrence_snapshots():
    session = _session()
    company = _company(session)
    creator = _user(session, company, "admin")
    editor = _user(session, company, "admin")
    responsible = _user(session, company, "admin")
    definition = _definition(
        session,
        company,
        creator,
        creator,
        title="Before",
        schedule_kind=InternalTaskScheduleKind.RECURRING.value,
        state=InternalTaskDefinitionState.FINISHED.value,
        recurrence_mode="daily",
        recurrence_config={"times": ["10:00"]},
        cycle_start_at_utc=datetime.utcnow() - timedelta(days=3),
        cycle_end_mode="never",
    )
    occurrence, _ = _materialized(session, company, definition, None, status=TicketStatus.PENDING.value)
    old_snapshot = occurrence.title_snapshot
    session.commit()

    response = _client(session, editor).patch(
        f"/api/v1/management/internal-tasks/recurring/{definition.id}",
        json=_recurring_payload(
            title="After",
            description="Changed",
            responsible_admin_id=responsible.id,
            recurrence_mode="monthly",
            recurrence_config={"day": 31, "time": "11:00"},
            cycle_end_mode="count",
            cycle_max_occurrences=5,
        ),
    )

    assert response.status_code == 200
    session.refresh(definition)
    session.refresh(occurrence)
    assert definition.creator_admin_id == creator.id
    assert definition.schedule_kind == InternalTaskScheduleKind.RECURRING.value
    assert definition.state == InternalTaskDefinitionState.FINISHED.value
    assert definition.title == "After"
    assert definition.recurrence_config == {"day": 31, "time": "11:00"}
    assert occurrence.title_snapshot == old_snapshot


def test_active_recurring_edit_sets_generation_boundary_and_runtime_skips_new_past_candidates(monkeypatch):
    monkeypatch.setattr(settings, "app_timezone", "Europe/Moscow")

    class FrozenDateTime(datetime):
        @classmethod
        def utcnow(cls):
            return datetime(2026, 9, 16, 12, 0)

    session = _session()
    Session = sessionmaker(bind=session.get_bind(), autoflush=False, autocommit=False)
    company = _company(session)
    admin = _user(session, company, "admin")
    definition = _definition(
        session,
        company,
        admin,
        admin,
        title="Editable active",
        schedule_kind=InternalTaskScheduleKind.RECURRING.value,
        recurrence_mode="daily",
        recurrence_config={"times": ["09:00"]},
        cycle_start_at_utc=datetime(2026, 9, 15, 0, 0),
        cycle_end_mode="never",
    )
    session.commit()

    monkeypatch.setattr(internal_tasks, "datetime", FrozenDateTime)
    response = _client(session, admin).patch(
        f"/api/v1/management/internal-tasks/recurring/{definition.id}",
        json=_recurring_payload(
            recurrence_mode="daily",
            recurrence_config={"times": ["10:00"]},
            cycle_start_at="2026-09-15T00:00:00",
        ),
    )

    assert response.status_code == 200
    session.refresh(definition)
    assert definition.generation_not_before_utc == datetime(2026, 9, 16, 12, 0)

    before_future_candidate = internal_task_scheduler.run_internal_task_recurring_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 17, 6, 0),  # 09:00 local, before the first post-edit 10:00 local candidate.
    )
    session.expire_all()
    assert before_future_candidate == 0
    assert session.execute(select(InternalTaskOccurrence)).scalars().all() == []

    after_future_candidate = internal_task_scheduler.run_internal_task_recurring_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 17, 8, 0),  # 11:00 local, after the first post-edit 10:00 local candidate.
    )
    session.expire_all()
    occurrences = session.execute(select(InternalTaskOccurrence)).scalars().all()
    assert after_future_candidate == 1
    assert [item.occurrence_at_utc for item in occurrences] == [datetime(2026, 9, 17, 7, 0)]


def test_active_recurring_edit_accepts_aware_generation_boundary_without_moving_it_back(monkeypatch):
    monkeypatch.setattr(settings, "app_timezone", "Europe/Moscow")

    class FrozenDateTime(datetime):
        @classmethod
        def utcnow(cls):
            return datetime(2026, 9, 16, 12, 0)

    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    definition = _definition(
        session,
        company,
        admin,
        admin,
        title="Aware boundary active",
        schedule_kind=InternalTaskScheduleKind.RECURRING.value,
        recurrence_mode="daily",
        recurrence_config={"times": ["09:00"]},
        cycle_start_at_utc=datetime(2026, 9, 15, 0, 0),
        cycle_end_mode="never",
    )
    definition.generation_not_before_utc = datetime(2026, 9, 16, 13, 0, tzinfo=timezone.utc)
    session.commit()

    monkeypatch.setattr(internal_tasks, "datetime", FrozenDateTime)
    response = _client(session, admin).patch(
        f"/api/v1/management/internal-tasks/recurring/{definition.id}",
        json=_recurring_payload(
            recurrence_mode="daily",
            recurrence_config={"times": ["10:00"]},
            cycle_start_at="2026-09-15T00:00:00",
        ),
    )

    assert response.status_code == 200
    session.refresh(definition)
    assert definition.generation_not_before_utc == datetime(2026, 9, 16, 13, 0)


def test_recurring_pause_resume_sets_generation_bound_and_blocks_foreign_company():
    session = _session()
    company = _company(session)
    other = _company(session, "Other")
    admin = _user(session, company, "admin")
    other_admin = _user(session, other, "admin")
    definition = _definition(
        session,
        company,
        admin,
        admin,
        title="Recurring",
        schedule_kind=InternalTaskScheduleKind.RECURRING.value,
        recurrence_mode="daily",
        recurrence_config={"times": ["10:00"]},
        cycle_start_at_utc=datetime.utcnow() - timedelta(days=1),
        cycle_end_mode="never",
    )
    session.commit()

    pause = _client(session, admin).post(f"/api/v1/management/internal-tasks/recurring/{definition.id}/pause")
    assert pause.status_code == 200
    session.refresh(definition)
    paused_bound = definition.generation_not_before_utc
    assert definition.state == InternalTaskDefinitionState.PAUSED.value
    assert paused_bound is not None

    foreign_resume = _client(session, other_admin).post(f"/api/v1/management/internal-tasks/recurring/{definition.id}/resume")
    assert foreign_resume.status_code == 404
    assert _error_code(foreign_resume) == "recurring_definition_not_found"

    resume = _client(session, admin).post(f"/api/v1/management/internal-tasks/recurring/{definition.id}/resume")
    assert resume.status_code == 200
    session.refresh(definition)
    assert definition.state == InternalTaskDefinitionState.ACTIVE.value
    assert definition.generation_not_before_utc >= paused_bound


def test_delete_actions_disable_definitions_or_delete_only_materialized_ticket():
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    agent = _user(session, company, "agent")
    scheduled = _definition(session, company, admin, admin, title="Scheduled delete")
    recurring = _definition(
        session,
        company,
        admin,
        admin,
        title="Recurring delete",
        schedule_kind=InternalTaskScheduleKind.RECURRING.value,
        recurrence_mode="daily",
        recurrence_config={"times": ["10:00"]},
        cycle_start_at_utc=datetime.utcnow() + timedelta(days=1),
        cycle_end_mode="never",
    )
    materialized_definition = _definition(session, company, admin, admin, title="Materialized delete", state=InternalTaskDefinitionState.FINISHED.value)
    occurrence, ticket = _materialized(session, company, materialized_definition, agent, status=TicketStatus.PENDING.value)
    session.commit()
    client = _client(session, admin)

    scheduled_response = client.delete(f"/api/v1/management/internal-tasks/scheduled/{scheduled.id}")
    recurring_response = client.delete(f"/api/v1/management/internal-tasks/recurring/{recurring.id}")
    materialized_response = client.delete(f"/api/v1/management/internal-tasks/materialized/{ticket.id}")

    assert scheduled_response.status_code == 200
    assert recurring_response.status_code == 200
    assert materialized_response.status_code == 200
    session.refresh(scheduled)
    session.refresh(recurring)
    assert scheduled.state == InternalTaskDefinitionState.DISABLED.value
    assert recurring.state == InternalTaskDefinitionState.DISABLED.value
    assert session.get(Ticket, ticket.id) is None
    assert session.get(InternalTaskOccurrence, occurrence.id) is not None

    overview = client.get("/api/v1/management/internal-tasks/overview").json()
    assert scheduled.title not in str(overview)
    assert recurring.title not in str(overview)


def test_delete_materialized_recurring_task_keeps_occurrence_marker_and_scheduler_does_not_recreate():
    session = _session()
    Session = sessionmaker(bind=session.get_bind(), autoflush=False, autocommit=False)
    company = _company(session)
    admin = _user(session, company, "admin")
    definition = _definition(
        session,
        company,
        admin,
        admin,
        title="Recurring materialized delete",
        schedule_kind=InternalTaskScheduleKind.RECURRING.value,
        recurrence_mode="daily",
        recurrence_config={"times": ["10:00"]},
        cycle_start_at_utc=datetime(2026, 9, 15, 0, 0),
        cycle_end_mode="never",
    )
    occurrence, ticket = _materialized(session, company, definition, None, status=TicketStatus.PENDING.value)
    occurrence.occurrence_at_utc = datetime(2026, 9, 15, 7, 0)
    session.commit()

    response = _client(session, admin).delete(f"/api/v1/management/internal-tasks/materialized/{ticket.id}")
    assert response.status_code == 200
    assert session.get(Ticket, ticket.id) is None
    assert session.get(InternalTaskOccurrence, occurrence.id) is not None

    processed = internal_task_scheduler.run_internal_task_recurring_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 15, 8, 0),
    )
    session.expire_all()

    assert processed == 0
    occurrences = session.execute(select(InternalTaskOccurrence)).scalars().all()
    assert len(occurrences) == 1
    assert occurrences[0].id == occurrence.id
    assert session.execute(select(Ticket)).scalars().all() == []


def test_delete_scheduled_rejects_materialized_definition_and_foreign_company():
    session = _session()
    company = _company(session)
    other = _company(session, "Other")
    admin = _user(session, company, "admin")
    other_admin = _user(session, other, "admin")
    materialized_definition = _definition(session, company, admin, admin, title="Materialized")
    _materialized(session, company, materialized_definition, None, status=TicketStatus.PENDING.value)
    session.commit()

    materialized = _client(session, admin).delete(f"/api/v1/management/internal-tasks/scheduled/{materialized_definition.id}")
    foreign = _client(session, other_admin).delete(f"/api/v1/management/internal-tasks/scheduled/{materialized_definition.id}")

    assert materialized.status_code == 409
    assert _error_code(materialized) == "scheduled_definition_materialized"
    assert foreign.status_code == 404
    assert _error_code(foreign) == "scheduled_definition_not_found"


def test_skip_next_recurring_sets_generation_boundary_after_next_candidate(monkeypatch):
    monkeypatch.setattr(settings, "app_timezone", "UTC")

    class FrozenDateTime(datetime):
        @classmethod
        def utcnow(cls):
            return datetime(2026, 9, 16, 9, 0)

    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    definition = _definition(
        session,
        company,
        admin,
        admin,
        title="Skip next",
        schedule_kind=InternalTaskScheduleKind.RECURRING.value,
        recurrence_mode="daily",
        recurrence_config={"times": ["13:00", "14:00"]},
        cycle_start_at_utc=datetime(2026, 9, 16, 0, 0),
        cycle_end_mode="never",
    )
    session.commit()

    monkeypatch.setattr(internal_tasks, "datetime", FrozenDateTime)
    response = _client(session, admin).post(f"/api/v1/management/internal-tasks/recurring/{definition.id}/skip-next")

    assert response.status_code == 200
    session.refresh(definition)
    assert definition.state == InternalTaskDefinitionState.ACTIVE.value
    assert definition.generation_not_before_utc == datetime(2026, 9, 16, 10, 0, 0, 1)
    assert response.json()["skipped_occurrence_at"].startswith("2026-09-16T10:00:00")
    assert response.json()["definition"]["next_occurrence_at"].startswith("2026-09-16T11:00:00")


def test_skip_next_recurring_rejects_when_no_next_candidate(monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def utcnow(cls):
            return datetime(2026, 9, 16, 12, 0)

    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    definition = _definition(
        session,
        company,
        admin,
        admin,
        title="No next",
        schedule_kind=InternalTaskScheduleKind.RECURRING.value,
        recurrence_mode="daily",
        recurrence_config={"times": ["10:00"]},
        cycle_start_at_utc=datetime(2026, 9, 16, 0, 0),
        cycle_end_mode="at",
        cycle_end_at_utc=datetime(2026, 9, 16, 11, 0),
    )
    session.commit()

    monkeypatch.setattr(internal_tasks, "datetime", FrozenDateTime)
    response = _client(session, admin).post(f"/api/v1/management/internal-tasks/recurring/{definition.id}/skip-next")

    assert response.status_code == 409
    assert _error_code(response) == "recurring_no_next_occurrence"
    session.refresh(definition)
    assert definition.generation_not_before_utc is None


def test_skip_next_recurring_count_mode_does_not_consume_successful_repetition(monkeypatch):
    monkeypatch.setattr(settings, "app_timezone", "Europe/Moscow")

    class FrozenDateTime(datetime):
        @classmethod
        def utcnow(cls):
            return datetime(2026, 9, 16, 9, 0)

    session = _session()
    Session = sessionmaker(bind=session.get_bind(), autoflush=False, autocommit=False)
    company = _company(session)
    admin = _user(session, company, "admin")
    definition = _definition(
        session,
        company,
        admin,
        admin,
        title="Count skip",
        schedule_kind=InternalTaskScheduleKind.RECURRING.value,
        recurrence_mode="daily",
        recurrence_config={"times": ["13:00", "14:00", "15:00"]},
        cycle_start_at_utc=datetime(2026, 9, 16, 0, 0),
        cycle_end_mode="count",
        cycle_max_occurrences=2,
    )
    session.commit()

    monkeypatch.setattr(internal_tasks, "datetime", FrozenDateTime)
    response = _client(session, admin).post(f"/api/v1/management/internal-tasks/recurring/{definition.id}/skip-next")
    assert response.status_code == 200
    session.refresh(definition)
    assert definition.generation_not_before_utc == datetime(2026, 9, 16, 10, 0, 0, 1)
    assert session.execute(select(InternalTaskOccurrence)).scalars().all() == []
    assert definition.state == InternalTaskDefinitionState.ACTIVE.value

    first_processed = internal_task_scheduler.run_internal_task_recurring_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 16, 11, 30),
    )
    session.expire_all()
    definition = session.get(InternalTaskDefinition, definition.id)
    assert first_processed == 1
    successful_occurrence_count = len(session.execute(select(InternalTaskOccurrence)).scalars().all())
    assert successful_occurrence_count == 1
    assert definition.state == InternalTaskDefinitionState.ACTIVE.value

    second_processed = internal_task_scheduler.run_internal_task_recurring_once(
        session_factory=Session,
        now_utc=datetime(2026, 9, 16, 12, 30),
    )
    session.expire_all()
    definition = session.get(InternalTaskDefinition, definition.id)
    occurrences = session.execute(
        select(InternalTaskOccurrence).order_by(InternalTaskOccurrence.occurrence_at_utc.asc())
    ).scalars().all()
    assert second_processed == 1
    assert [item.occurrence_at_utc for item in occurrences] == [
        datetime(2026, 9, 16, 11, 0),
        datetime(2026, 9, 16, 12, 0),
    ]
    assert definition.state == InternalTaskDefinitionState.FINISHED.value


def test_recurring_overview_company_mine_states_and_completed_tab_separation():
    session = _session()
    company = _company(session)
    other = _company(session, "Other")
    admin = _user(session, company, "admin")
    other_admin = _user(session, company, "admin")
    foreign_admin = _user(session, other, "admin")
    active = _definition(
        session,
        company,
        admin,
        other_admin,
        title="Active recurring",
        schedule_kind=InternalTaskScheduleKind.RECURRING.value,
        recurrence_mode="daily",
        recurrence_config={"times": ["10:00"]},
        cycle_start_at_utc=datetime.utcnow() + timedelta(days=1),
        cycle_end_mode="never",
    )
    paused = _definition(
        session,
        company,
        other_admin,
        admin,
        title="Paused recurring",
        schedule_kind=InternalTaskScheduleKind.RECURRING.value,
        state=InternalTaskDefinitionState.PAUSED.value,
        recurrence_mode="daily",
        recurrence_config={"times": ["10:00"]},
        cycle_start_at_utc=datetime.utcnow() + timedelta(days=1),
        cycle_end_mode="never",
    )
    finished = _definition(
        session,
        company,
        other_admin,
        other_admin,
        title="Finished recurring",
        schedule_kind=InternalTaskScheduleKind.RECURRING.value,
        state=InternalTaskDefinitionState.FINISHED.value,
        recurrence_mode="daily",
        recurrence_config={"times": ["10:00"]},
        cycle_start_at_utc=datetime.utcnow() - timedelta(days=3),
        cycle_end_mode="count",
        cycle_max_occurrences=1,
    )
    completed_occurrence_definition = _definition(
        session,
        company,
        other_admin,
        other_admin,
        title="Completed recurring task",
        schedule_kind=InternalTaskScheduleKind.RECURRING.value,
        state=InternalTaskDefinitionState.ACTIVE.value,
        recurrence_mode="daily",
        recurrence_config={"times": ["10:00"]},
        cycle_start_at_utc=datetime.utcnow() - timedelta(days=3),
        cycle_end_mode="never",
    )
    _definition(
        session,
        other,
        foreign_admin,
        foreign_admin,
        title="Foreign recurring",
        schedule_kind=InternalTaskScheduleKind.RECURRING.value,
        recurrence_mode="daily",
        recurrence_config={"times": ["10:00"]},
        cycle_start_at_utc=datetime.utcnow() + timedelta(days=1),
        cycle_end_mode="never",
    )
    _materialized(session, company, active, None, status=TicketStatus.PENDING.value)
    _materialized(session, company, completed_occurrence_definition, None, status=TicketStatus.CLOSED.value)
    session.commit()

    all_body = _client(session, admin).get("/api/v1/management/internal-tasks/overview").json()
    mine_body = _client(session, admin).get("/api/v1/management/internal-tasks/overview", params={"scope": "mine"}).json()

    all_titles = {item["title"] for item in all_body["recurring"]}
    mine_titles = {item["title"] for item in mine_body["recurring"]}
    assert {active.title, paused.title, finished.title}.issubset(all_titles)
    assert "Foreign recurring" not in str(all_body)
    assert active.title in mine_titles
    assert paused.title in mine_titles
    assert finished.title not in mine_titles
    assert active.title in [item["title"] for item in all_body["active"]]
    assert completed_occurrence_definition.title in [item["title"] for item in all_body["completed"]]
    assert active.title in all_titles
    assert completed_occurrence_definition.title in all_titles
    assert finished.title not in [item["title"] for item in all_body["completed"]]


def test_recurring_overview_accepts_postgresql_aware_cycle_timestamps():
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    cycle_start = (datetime.utcnow() + timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
    definition = _definition(
        session,
        company,
        admin,
        admin,
        title="Aware recurring",
        schedule_kind=InternalTaskScheduleKind.RECURRING.value,
        recurrence_mode="daily",
        recurrence_config={"times": ["10:00"]},
        cycle_start_at_utc=cycle_start,
        cycle_end_mode="at",
        cycle_end_at_utc=cycle_start + timedelta(days=2),
    )
    assert internal_tasks._next_recurring_at(session, definition) is not None
    session.commit()

    response = _client(session, admin).get("/api/v1/management/internal-tasks/overview")

    assert response.status_code == 200
    body = response.json()
    recurring = [item for item in body["recurring"] if item["definition_id"] == definition.id]
    assert len(recurring) == 1


def test_recurring_timezone_naive_and_aware_datetime_normalization(monkeypatch):
    monkeypatch.setattr(settings, "app_timezone", "Europe/Moscow")
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    session.commit()

    naive = _client(session, admin).post(
        "/api/v1/management/internal-tasks/recurring",
        json=_recurring_payload(
            cycle_start_at="2026-09-17T13:00:00",
            cycle_end_mode="at",
            cycle_end_at="2026-09-18T13:00:00",
            deadline_enabled=True,
            deadline_at="2026-09-17T14:00:00",
        ),
    )
    aware = _client(session, admin).post(
        "/api/v1/management/internal-tasks/recurring",
        json=_recurring_payload(cycle_start_at="2026-09-17T13:00:00+05:00"),
    )

    assert naive.status_code == 200
    first = session.get(InternalTaskDefinition, naive.json()["definition_id"])
    assert first.cycle_start_at_utc.replace(tzinfo=timezone.utc) == datetime(2026, 9, 17, 10, 0, tzinfo=timezone.utc)
    assert first.cycle_end_at_utc.replace(tzinfo=timezone.utc) == datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc)
    assert first.deadline_at_utc.replace(tzinfo=timezone.utc) == datetime(2026, 9, 17, 11, 0, tzinfo=timezone.utc)
    assert aware.status_code == 200
    second = session.get(InternalTaskDefinition, aware.json()["definition_id"])
    assert second.cycle_start_at_utc.replace(tzinfo=timezone.utc) == datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc)


def test_weekly_anchor_persists_first_selected_weekday_not_before_start(monkeypatch):
    monkeypatch.setattr(settings, "app_timezone", "Europe/Moscow")
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    session.commit()

    response = _client(session, admin).post(
        "/api/v1/management/internal-tasks/recurring",
        json=_recurring_payload(
            recurrence_mode="weekly",
            recurrence_config={"weekday": 3, "time": "10:30"},
            cycle_start_at="2026-09-14T15:00:00",  # Monday 15:00 local; selected Wednesday 10:30 local.
        ),
    )

    assert response.status_code == 200
    definition = session.get(InternalTaskDefinition, response.json()["definition_id"])
    assert definition.recurrence_config == {"time": "10:30"}
    assert definition.cycle_start_at_utc.replace(tzinfo=timezone.utc) == datetime(2026, 9, 16, 7, 30, tzinfo=timezone.utc)


def test_finished_resume_requires_future_end_capacity_after_edit(monkeypatch):
    monkeypatch.setattr(settings, "app_timezone", "Europe/Moscow")

    class FrozenDateTime(datetime):
        @classmethod
        def utcnow(cls):
            return datetime(2026, 9, 17, 12, 0)  # 15:00 local.

    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    count_finished = _definition(
        session,
        company,
        admin,
        admin,
        title="Count done",
        schedule_kind=InternalTaskScheduleKind.RECURRING.value,
        state=InternalTaskDefinitionState.FINISHED.value,
        recurrence_mode="daily",
        recurrence_config={"times": ["10:00"]},
        cycle_start_at_utc=datetime.utcnow() - timedelta(days=3),
        cycle_end_mode="count",
        cycle_max_occurrences=1,
    )
    _materialized(session, company, count_finished, None, status=TicketStatus.CLOSED.value)
    end_at_finished = _definition(
        session,
        company,
        admin,
        admin,
        title="End elapsed",
        schedule_kind=InternalTaskScheduleKind.RECURRING.value,
        state=InternalTaskDefinitionState.FINISHED.value,
        recurrence_mode="daily",
        recurrence_config={"times": ["10:00"]},
        cycle_start_at_utc=datetime(2026, 9, 15, 0, 0),
        cycle_end_mode="at",
        cycle_end_at_utc=datetime(2026, 9, 17, 15, 0),  # 18:00 local, after now but before tomorrow 10:00.
    )
    session.commit()
    client = _client(session, admin)
    monkeypatch.setattr(internal_tasks, "datetime", FrozenDateTime)

    count_blocked = client.post(f"/api/v1/management/internal-tasks/recurring/{count_finished.id}/resume")
    end_at_blocked = client.post(f"/api/v1/management/internal-tasks/recurring/{end_at_finished.id}/resume")
    assert count_blocked.status_code == 409
    assert _error_code(count_blocked) == "recurring_count_exhausted"
    assert end_at_blocked.status_code == 409
    assert _error_code(end_at_blocked) == "recurring_no_future_occurrence"
    session.refresh(end_at_finished)
    assert end_at_finished.state == InternalTaskDefinitionState.FINISHED.value

    patch = client.patch(
        f"/api/v1/management/internal-tasks/recurring/{end_at_finished.id}",
        json=_recurring_payload(
            recurrence_config={"times": ["10:00"]},
            cycle_end_mode="at",
            cycle_end_at="2026-09-18T12:00:00",  # Tomorrow 12:00 local, after tomorrow 10:00 candidate.
            cycle_start_at="2026-09-15T00:00:00",
        ),
    )
    assert patch.status_code == 200
    session.refresh(end_at_finished)
    assert end_at_finished.state == InternalTaskDefinitionState.FINISHED.value

    resume = client.post(f"/api/v1/management/internal-tasks/recurring/{end_at_finished.id}/resume")
    assert resume.status_code == 200
    session.refresh(end_at_finished)
    assert end_at_finished.state == InternalTaskDefinitionState.ACTIVE.value
    assert end_at_finished.generation_not_before_utc == datetime(2026, 9, 17, 12, 0)
    assert internal_tasks._next_recurring_at(session, end_at_finished) == datetime(2026, 9, 18, 7, 0)
