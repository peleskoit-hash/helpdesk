from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app import main
from api.app.db import Base, get_db
from api.app.models import Client, Company, StatsCompletion, Ticket, TicketStatus, User
from api.app.routers import management_statistics


@pytest.fixture(autouse=True)
def _clear_overrides():
    main.app.dependency_overrides.clear()
    yield
    main.app.dependency_overrides.clear()


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


def _api_client(session, user: SimpleNamespace) -> TestClient:
    main.app.dependency_overrides[get_db] = lambda: session
    main.app.dependency_overrides[management_statistics.get_current_user] = lambda: user
    return TestClient(main.app)


def _current_user(user_id: str, role: str = "admin") -> SimpleNamespace:
    return SimpleNamespace(id=user_id, role=role, real_user_id=user_id, real_role=role, is_real_admin=role == "admin")


def _user(session, company: Company, user_id: str, *, role: str = "agent", is_active: bool = True) -> User:
    user = User(
        id=user_id,
        company_id=company.id,
        email=f"{user_id}@example.test",
        login=user_id,
        full_name=user_id.replace("-", " ").title(),
        role=role,
        is_active=is_active,
        availability_status="online",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(user)
    session.flush()
    return user


def _completion(
    company_id: str,
    object_id: str,
    *,
    object_type: str = "ticket",
    user_id: str | None = "agent-a",
    completed_at: datetime = datetime(2026, 9, 2, 12, 0, 0),
    is_completed: bool = True,
) -> StatsCompletion:
    return StatsCompletion(
        id=str(uuid.uuid4()),
        company_id=company_id,
        object_id=object_id,
        object_type=object_type,
        task_kind=None,
        completed_by_user_id=user_id,
        completed_at=completed_at,
        is_completed=is_completed,
        created_at=completed_at,
        updated_at=completed_at,
    )


def test_management_completed_stats_aggregate_stats_completion_without_source_ticket_or_user():
    session = _session()
    company = Company(id="company-a", name="ACME", created_at=datetime.utcnow())
    client = Client(id="client-a", company_id=company.id, display_name="Client")
    session.add_all([company, client])
    admin = _user(session, company, "admin-a", role="admin")
    agent = _user(session, company, "agent-a")
    inactive = _user(session, company, "inactive-agent", is_active=False)
    deleted = _user(session, company, "deleted-agent")
    ticket = Ticket(
        id="hard-deleted-ticket",
        company_id=company.id,
        client_id=client.id,
        assignee_id=agent.id,
        subject="Deleted source",
        status=TicketStatus.CLOSED.value,
        closed_at=datetime(2026, 9, 2, 12, 0, 0),
        created_at=datetime(2026, 9, 2, 11, 0, 0),
        updated_at=datetime(2026, 9, 2, 12, 0, 0),
    )
    session.add(ticket)
    session.flush()
    session.add_all(
        [
            _completion(company.id, ticket.id, object_type="ticket", user_id=agent.id),
            _completion(company.id, "agent-task", object_type="task", user_id=agent.id),
            _completion(company.id, "inactive-task", object_type="task", user_id=inactive.id),
            _completion(company.id, "deleted-ticket", object_type="ticket", user_id=deleted.id),
            _completion(company.id, "reopened", object_type="ticket", user_id=agent.id, is_completed=False),
            _completion(company.id, "unattributed", object_type="task", user_id=None),
            _completion(company.id, "unsupported", object_type="future_unknown_kind", user_id=agent.id),
        ]
    )
    session.delete(ticket)
    session.delete(deleted)
    session.commit()

    response = _api_client(session, _current_user(admin.id)).get("/api/v1/management/statistics/completed")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == {"total": 4, "tasks": 2, "tickets": 2}
    assert [item["user_id"] for item in body["items"]] == ["agent-a", "inactive-agent", "deleted-agent"]
    assert body["items"][0] == {
        "user_id": "agent-a",
        "full_name": "Agent A",
        "is_active": True,
        "tickets": 1,
        "tasks": 1,
        "total": 2,
    }
    assert body["items"][1]["is_active"] is False
    assert body["items"][2]["full_name"] is None
    assert body["items"][2]["is_active"] is None


def test_management_completed_stats_user_filter_and_half_open_date_range():
    session = _session()
    company = Company(id="company-a", name="ACME", created_at=datetime.utcnow())
    session.add(company)
    admin = _user(session, company, "admin-a", role="admin")
    agent = _user(session, company, "agent-a")
    other = _user(session, company, "agent-b")
    session.add_all(
        [
            _completion(company.id, "before", object_type="ticket", user_id=agent.id, completed_at=datetime(2026, 9, 1, 23, 59, 0)),
            _completion(company.id, "inside", object_type="task", user_id=agent.id, completed_at=datetime(2026, 9, 2, 0, 0, 0)),
            _completion(company.id, "boundary", object_type="ticket", user_id=agent.id, completed_at=datetime(2026, 9, 3, 0, 0, 0)),
            _completion(company.id, "other", object_type="ticket", user_id=other.id, completed_at=datetime(2026, 9, 2, 12, 0, 0)),
        ]
    )
    session.commit()

    response = _api_client(session, _current_user(admin.id)).get(
        "/api/v1/management/statistics/completed",
        params={
            "user_id": agent.id,
            "date_from": "2026-09-02T00:00:00",
            "date_to": "2026-09-03T00:00:00",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == {"total": 1, "tasks": 1, "tickets": 0}
    assert body["items"][0]["user_id"] == agent.id
    assert body["items"][0]["total"] == 1

    only_date_from = _api_client(session, _current_user(admin.id)).get(
        "/api/v1/management/statistics/completed",
        params={
            "user_id": agent.id,
            "date_from": "2026-09-02T00:00:00",
        },
    )
    assert only_date_from.status_code == 200
    assert only_date_from.json()["total"] == {"total": 2, "tasks": 1, "tickets": 1}

    only_date_to = _api_client(session, _current_user(admin.id)).get(
        "/api/v1/management/statistics/completed",
        params={
            "user_id": agent.id,
            "date_to": "2026-09-03T00:00:00",
        },
    )
    assert only_date_to.status_code == 200
    assert only_date_to.json()["total"] == {"total": 2, "tasks": 1, "tickets": 1}


def test_management_completed_stats_rejects_invalid_range_and_non_admin():
    session = _session()
    company = Company(id="company-a", name="ACME", created_at=datetime.utcnow())
    session.add(company)
    admin = _user(session, company, "admin-a", role="admin")
    agent = _user(session, company, "agent-a")
    trainee = _user(session, company, "trainee-a", role="trainee")
    readonly = _user(session, company, "chiki-a", role="chiki_piki")
    session.commit()

    invalid_range = _api_client(session, _current_user(admin.id)).get(
        "/api/v1/management/statistics/completed",
        params={"date_from": "2026-09-03T00:00:00", "date_to": "2026-09-02T00:00:00"},
    )

    assert invalid_range.status_code == 400
    for user, role in [(agent, "agent"), (trainee, "trainee"), (readonly, "chiki_piki")]:
        forbidden = _api_client(session, _current_user(user.id, role=role)).get("/api/v1/management/statistics/completed")
        assert forbidden.status_code == 403
