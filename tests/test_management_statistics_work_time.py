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
from api.app.models import Client, Company, StatsWorkInterval, Ticket, TicketStatus, User
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


def _work_interval(
    company_id: str,
    object_id: str,
    *,
    user_id: str = "agent-a",
    object_type: str = "ticket",
    started_at: datetime = datetime(2026, 9, 2, 10, 0, 0),
    ended_at: datetime | None = datetime(2026, 9, 2, 10, 10, 0),
) -> StatsWorkInterval:
    return StatsWorkInterval(
        id=str(uuid.uuid4()),
        company_id=company_id,
        object_id=object_id,
        object_type=object_type,
        task_kind=None,
        user_id=user_id,
        started_at=started_at,
        ended_at=ended_at,
        end_reason="close" if ended_at is not None else None,
        created_at=started_at,
    )


def _seed_admin_context(session):
    company = Company(id="company-a", name="ACME", created_at=datetime.utcnow())
    session.add(company)
    admin = _user(session, company, "admin-a", role="admin")
    agent = _user(session, company, "agent-a")
    return company, admin, agent


def _get(session, admin: User, **params):
    return _api_client(session, _current_user(admin.id)).get("/api/v1/management/statistics/work-time", params=params)


def test_work_time_single_completed_interval_counts_one_observation():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    session.add(_work_interval(company.id, "ticket-a", user_id=agent.id))
    session.commit()

    response = _get(session, admin)

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0] == {
        "tickets": 1,
        "tasks": 0,
        "objects": 1,

        "average_seconds": 600,
        "median_seconds": 600,
        "user_id": agent.id,
        "full_name": "Agent A",
        "is_active": True,
    }
    assert body["total"] == {
        "tickets": 1,
        "tasks": 0,
        "objects": 1,

        "average_seconds": 600,
        "median_seconds": 600,
    }


def test_work_time_groups_employee_object_before_average_median_and_counts_tasks_once():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    session.add_all(
        [
            _work_interval(company.id, "ticket-a", user_id=agent.id, started_at=datetime(2026, 9, 2, 10, 0), ended_at=datetime(2026, 9, 2, 10, 10)),
            _work_interval(company.id, "ticket-a", user_id=agent.id, started_at=datetime(2026, 9, 2, 11, 0), ended_at=datetime(2026, 9, 2, 11, 20)),
            _work_interval(company.id, "ticket-b", user_id=agent.id, started_at=datetime(2026, 9, 2, 12, 0), ended_at=datetime(2026, 9, 2, 12, 10)),
            _work_interval(company.id, "task-a", object_type="task", user_id=agent.id, started_at=datetime(2026, 9, 2, 13, 0), ended_at=datetime(2026, 9, 2, 13, 20)),
            _work_interval(company.id, "task-a", object_type="task", user_id=agent.id, started_at=datetime(2026, 9, 2, 14, 0), ended_at=datetime(2026, 9, 2, 14, 30)),
        ]
    )
    session.commit()

    body = _get(session, admin).json()

    assert body["items"][0]["tickets"] == 2
    assert body["items"][0]["tasks"] == 1
    assert body["items"][0]["objects"] == 3
    assert "total_seconds" not in body["items"][0]
    assert body["items"][0]["average_seconds"] == 1800
    assert body["items"][0]["median_seconds"] == 1800


def test_work_time_truncates_fractional_seconds_after_employee_object_grouping():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    session.add_all(
        [
            _work_interval(
                company.id,
                "ticket-a",
                user_id=agent.id,
                started_at=datetime(2026, 9, 2, 10, 0, 0),
                ended_at=datetime(2026, 9, 2, 10, 0, 0, 600000),
            ),
            _work_interval(
                company.id,
                "ticket-a",
                user_id=agent.id,
                started_at=datetime(2026, 9, 2, 10, 1, 0),
                ended_at=datetime(2026, 9, 2, 10, 1, 0, 600000),
            ),
        ]
    )
    session.commit()

    body = _get(session, admin).json()

    assert body["items"][0]["objects"] == 1
    assert "total_seconds" not in body["items"][0]
    assert body["items"][0]["average_seconds"] == 1
    assert body["items"][0]["median_seconds"] == 1


def test_work_time_even_median_uses_middle_two_observations():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    session.add_all(
        [
            _work_interval(company.id, "ticket-a", user_id=agent.id, started_at=datetime(2026, 9, 2, 10, 0), ended_at=datetime(2026, 9, 2, 10, 10)),
            _work_interval(company.id, "ticket-b", user_id=agent.id, started_at=datetime(2026, 9, 2, 11, 0), ended_at=datetime(2026, 9, 2, 11, 30)),
        ]
    )
    session.commit()

    body = _get(session, admin).json()

    assert body["items"][0]["objects"] == 2
    assert body["items"][0]["median_seconds"] == 1200


def test_work_time_same_object_is_separate_observation_per_employee_and_total_uses_all_observations():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    other = _user(session, company, "agent-b")
    session.add_all(
        [
            _work_interval(company.id, "ticket-a", user_id=agent.id, started_at=datetime(2026, 9, 2, 10, 0), ended_at=datetime(2026, 9, 2, 10, 20)),
            _work_interval(company.id, "ticket-a", user_id=other.id, started_at=datetime(2026, 9, 2, 11, 0), ended_at=datetime(2026, 9, 2, 11, 40)),
            _work_interval(company.id, "ticket-b", user_id=other.id, started_at=datetime(2026, 9, 2, 12, 0), ended_at=datetime(2026, 9, 2, 13, 40)),
        ]
    )
    session.commit()

    body = _get(session, admin).json()

    assert [item["user_id"] for item in body["items"]] == [other.id, agent.id]
    assert body["items"][0]["objects"] == 2
    assert body["items"][0]["average_seconds"] == 4200
    assert body["items"][1]["objects"] == 1
    assert body["total"] == {
        "tickets": 3,
        "tasks": 0,
        "objects": 3,

        "average_seconds": 3200,
        "median_seconds": 2400,
    }


def test_work_time_period_clipping_boundaries_and_one_sided_filters():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    session.add_all(
        [
            _work_interval(company.id, "from-clip", user_id=agent.id, started_at=datetime(2026, 9, 2, 9, 0), ended_at=datetime(2026, 9, 2, 11, 0)),
            _work_interval(company.id, "to-clip", user_id=agent.id, started_at=datetime(2026, 9, 2, 11, 0), ended_at=datetime(2026, 9, 2, 13, 0)),
            _work_interval(company.id, "covers-period", user_id=agent.id, started_at=datetime(2026, 9, 2, 8, 0), ended_at=datetime(2026, 9, 2, 14, 0)),
            _work_interval(company.id, "ends-at-from", user_id=agent.id, started_at=datetime(2026, 9, 2, 8, 0), ended_at=datetime(2026, 9, 2, 10, 0)),
            _work_interval(company.id, "starts-at-to", user_id=agent.id, started_at=datetime(2026, 9, 2, 12, 0), ended_at=datetime(2026, 9, 2, 13, 0)),
        ]
    )
    session.commit()

    clipped = _get(session, admin, date_from="2026-09-02T10:00:00", date_to="2026-09-02T12:00:00")
    only_from = _get(session, admin, date_from="2026-09-02T10:00:00")
    only_to = _get(session, admin, date_to="2026-09-02T12:00:00")
    invalid = _get(session, admin, date_from="2026-09-02T12:00:00", date_to="2026-09-02T10:00:00")

    assert clipped.status_code == 200
    assert clipped.json()["total"]["objects"] == 3
    assert clipped.json()["total"]["average_seconds"] == 4800
    assert only_from.json()["total"]["objects"] == 4
    assert only_from.json()["total"]["average_seconds"] == 7200
    assert only_to.json()["total"]["objects"] == 4
    assert only_to.json()["total"]["average_seconds"] == 8100
    assert invalid.status_code == 400


def test_work_time_open_interval_uses_single_now_and_clips_to_date_to(monkeypatch):
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    monkeypatch.setattr(management_statistics, "_utcnow", lambda: datetime(2026, 9, 2, 12, 0))
    session.add(_work_interval(company.id, "ticket-a", user_id=agent.id, started_at=datetime(2026, 9, 2, 10, 0), ended_at=None))
    session.commit()

    no_date_to = _get(session, admin)
    date_to_before_now = _get(session, admin, date_to="2026-09-02T11:00:00")
    date_to_after_now = _get(session, admin, date_to="2026-09-02T13:00:00")

    assert no_date_to.json()["total"]["average_seconds"] == 7200
    assert date_to_before_now.json()["total"]["average_seconds"] == 3600
    assert date_to_after_now.json()["total"]["average_seconds"] == 7200


def test_work_time_preserves_inactive_deleted_user_and_hard_deleted_source_ticket():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    inactive = _user(session, company, "inactive-agent", is_active=False)
    deleted = _user(session, company, "deleted-agent")
    client = Client(id="client-a", company_id=company.id, display_name="Client")
    ticket = Ticket(
        id="hard-deleted-ticket",
        company_id=company.id,
        client_id=client.id,
        assignee_id=agent.id,
        subject="Deleted source",
        status=TicketStatus.CLOSED.value,
        closed_at=datetime(2026, 9, 2, 10, 10),
        created_at=datetime(2026, 9, 2, 10, 0),
        updated_at=datetime(2026, 9, 2, 10, 10),
    )
    session.add_all([client, ticket])
    session.flush()
    session.add_all(
        [
            _work_interval(company.id, ticket.id, user_id=agent.id),
            _work_interval(company.id, "inactive-ticket", user_id=inactive.id),
            _work_interval(company.id, "deleted-ticket", user_id=deleted.id),
        ]
    )
    session.delete(ticket)
    session.delete(deleted)
    session.commit()

    body = _get(session, admin).json()

    assert body["total"]["objects"] == 3
    rows = {item["user_id"]: item for item in body["items"]}
    assert rows[agent.id]["objects"] == 1
    assert rows[inactive.id]["is_active"] is False
    assert rows[deleted.id]["full_name"] is None
    assert rows[deleted.id]["is_active"] is None


def test_work_time_user_filter_keeps_only_requested_user():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    other = _user(session, company, "agent-b")
    session.add_all(
        [
            _work_interval(company.id, "ticket-a", user_id=agent.id),
            _work_interval(company.id, "ticket-b", user_id=other.id),
        ]
    )
    session.commit()

    filtered = _get(session, admin, user_id=agent.id).json()
    missing = _get(session, admin, user_id="missing-agent").json()

    assert [item["user_id"] for item in filtered["items"]] == [agent.id]
    assert filtered["total"]["objects"] == 1
    assert missing["items"] == []
    assert missing["total"] == {
        "tickets": 0,
        "tasks": 0,
        "objects": 0,

        "average_seconds": 0,
        "median_seconds": 0,
    }


def test_work_time_rejects_non_admin_roles():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    trainee = _user(session, company, "trainee-a", role="trainee")
    readonly = _user(session, company, "chiki-a", role="chiki_piki")
    session.add(_work_interval(company.id, "ticket-a", user_id=agent.id))
    session.commit()

    assert _get(session, admin).status_code == 200
    for user, role in [(agent, "agent"), (trainee, "trainee"), (readonly, "chiki_piki")]:
        response = _api_client(session, _current_user(user.id, role=role)).get("/api/v1/management/statistics/work-time")
        assert response.status_code == 403
