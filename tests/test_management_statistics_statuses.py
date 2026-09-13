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
from api.app.models import Company, StatsUserStatusInterval, User
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


def _status_interval(
    company_id: str,
    *,
    user_id: str = "agent-a",
    status: str = "online",
    started_at: datetime = datetime(2026, 9, 2, 10, 0, 0),
    ended_at: datetime | None = datetime(2026, 9, 2, 10, 10, 0),
) -> StatsUserStatusInterval:
    return StatsUserStatusInterval(
        id=str(uuid.uuid4()),
        company_id=company_id,
        user_id=user_id,
        status=status,
        started_at=started_at,
        ended_at=ended_at,
        created_at=started_at,
    )


def _seed_admin_context(session):
    company = Company(id="company-a", name="ACME", created_at=datetime.utcnow())
    session.add(company)
    admin = _user(session, company, "admin-a", role="admin")
    agent = _user(session, company, "agent-a")
    return company, admin, agent


def _get(session, admin: User, **params):
    return _api_client(session, _current_user(admin.id)).get("/api/v1/management/statistics/statuses", params=params)


def _zero_total():
    return {
        "offline_seconds": 0,
        "online_seconds": 0,
        "on_ticket_seconds": 0,
        "waiting_seconds": 0,
        "lunch_seconds": 0,
        "total_seconds": 0,
    }


def test_status_stats_single_online_interval_counts_duration():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    session.add(_status_interval(company.id, user_id=agent.id))
    session.commit()

    response = _get(session, admin)

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0] == {
        "offline_seconds": 0,
        "online_seconds": 600,
        "on_ticket_seconds": 0,
        "waiting_seconds": 0,
        "lunch_seconds": 0,
        "total_seconds": 600,
        "user_id": agent.id,
        "full_name": "Agent A",
        "is_active": True,
    }
    assert body["total"]["online_seconds"] == 600
    assert body["total"]["total_seconds"] == 600


def test_status_stats_sums_repeated_status_and_all_v1_status_fields():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    session.add_all(
        [
            _status_interval(company.id, user_id=agent.id, status="online", started_at=datetime(2026, 9, 2, 10, 0), ended_at=datetime(2026, 9, 2, 10, 10)),
            _status_interval(company.id, user_id=agent.id, status="online", started_at=datetime(2026, 9, 2, 11, 0), ended_at=datetime(2026, 9, 2, 11, 20)),
            _status_interval(company.id, user_id=agent.id, status="on_ticket", started_at=datetime(2026, 9, 2, 12, 0), ended_at=datetime(2026, 9, 2, 12, 5)),
            _status_interval(company.id, user_id=agent.id, status="waiting", started_at=datetime(2026, 9, 2, 13, 0), ended_at=datetime(2026, 9, 2, 13, 6)),
            _status_interval(company.id, user_id=agent.id, status="lunch", started_at=datetime(2026, 9, 2, 14, 0), ended_at=datetime(2026, 9, 2, 14, 7)),
            _status_interval(company.id, user_id=agent.id, status="offline", started_at=datetime(2026, 9, 2, 15, 0), ended_at=datetime(2026, 9, 2, 15, 8)),
        ]
    )
    session.commit()

    row = _get(session, admin).json()["items"][0]

    assert row["online_seconds"] == 1800
    assert row["on_ticket_seconds"] == 300
    assert row["waiting_seconds"] == 360
    assert row["lunch_seconds"] == 420
    assert row["offline_seconds"] == 480
    assert row["total_seconds"] == 3360


def test_status_stats_period_clipping_boundaries_and_one_sided_filters():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    session.add_all(
        [
            _status_interval(company.id, user_id=agent.id, started_at=datetime(2026, 9, 2, 9, 0), ended_at=datetime(2026, 9, 2, 11, 0)),
            _status_interval(company.id, user_id=agent.id, status="waiting", started_at=datetime(2026, 9, 2, 11, 0), ended_at=datetime(2026, 9, 2, 13, 0)),
            _status_interval(company.id, user_id=agent.id, status="lunch", started_at=datetime(2026, 9, 2, 8, 0), ended_at=datetime(2026, 9, 2, 14, 0)),
            _status_interval(company.id, user_id=agent.id, status="offline", started_at=datetime(2026, 9, 2, 8, 0), ended_at=datetime(2026, 9, 2, 10, 0)),
            _status_interval(company.id, user_id=agent.id, status="on_ticket", started_at=datetime(2026, 9, 2, 12, 0), ended_at=datetime(2026, 9, 2, 13, 0)),
        ]
    )
    session.commit()

    clipped = _get(session, admin, date_from="2026-09-02T10:00:00", date_to="2026-09-02T12:00:00")
    only_from = _get(session, admin, date_from="2026-09-02T10:00:00")
    only_to = _get(session, admin, date_to="2026-09-02T12:00:00")
    invalid = _get(session, admin, date_from="2026-09-02T12:00:00", date_to="2026-09-02T10:00:00")

    assert clipped.status_code == 200
    assert clipped.json()["total"]["total_seconds"] == 14400
    assert clipped.json()["total"]["offline_seconds"] == 0
    assert clipped.json()["total"]["on_ticket_seconds"] == 0
    assert only_from.json()["total"]["total_seconds"] == 28800
    assert only_to.json()["total"]["total_seconds"] == 32400
    assert invalid.status_code == 400


def test_status_stats_open_interval_uses_single_now_and_clips_to_date_to(monkeypatch):
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    monkeypatch.setattr(management_statistics, "_utcnow", lambda: datetime(2026, 9, 2, 12, 0))
    session.add(_status_interval(company.id, user_id=agent.id, started_at=datetime(2026, 9, 2, 10, 0), ended_at=None))
    session.commit()

    no_date_to = _get(session, admin)
    date_to_before_now = _get(session, admin, date_to="2026-09-02T11:00:00")
    date_to_after_now = _get(session, admin, date_to="2026-09-02T13:00:00")

    assert no_date_to.json()["total"]["online_seconds"] == 7200
    assert date_to_before_now.json()["total"]["online_seconds"] == 3600
    assert date_to_after_now.json()["total"]["online_seconds"] == 7200


def test_status_stats_truncates_fractional_seconds_after_employee_status_sum():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    session.add_all(
        [
            _status_interval(company.id, user_id=agent.id, started_at=datetime(2026, 9, 2, 10, 0, 0), ended_at=datetime(2026, 9, 2, 10, 0, 0, 600000)),
            _status_interval(company.id, user_id=agent.id, started_at=datetime(2026, 9, 2, 10, 1, 0), ended_at=datetime(2026, 9, 2, 10, 1, 0, 600000)),
        ]
    )
    session.commit()

    row = _get(session, admin).json()["items"][0]

    assert row["online_seconds"] == 1
    assert row["total_seconds"] == 1


def test_status_stats_multiple_users_and_upper_total_by_status():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    other = _user(session, company, "agent-b")
    session.add_all(
        [
            _status_interval(company.id, user_id=agent.id, status="online", started_at=datetime(2026, 9, 2, 10, 0), ended_at=datetime(2026, 9, 2, 10, 10)),
            _status_interval(company.id, user_id=other.id, status="waiting", started_at=datetime(2026, 9, 2, 11, 0), ended_at=datetime(2026, 9, 2, 11, 20)),
        ]
    )
    session.commit()

    body = _get(session, admin).json()

    assert [item["user_id"] for item in body["items"]] == [other.id, agent.id]
    assert body["total"]["online_seconds"] == 600
    assert body["total"]["waiting_seconds"] == 1200
    assert body["total"]["total_seconds"] == 1800


def test_status_stats_preserves_inactive_and_deleted_user_metadata():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    inactive = _user(session, company, "inactive-agent", is_active=False)
    deleted = _user(session, company, "deleted-agent")
    session.add_all(
        [
            _status_interval(company.id, user_id=agent.id),
            _status_interval(company.id, user_id=inactive.id),
            _status_interval(company.id, user_id=deleted.id),
        ]
    )
    session.delete(deleted)
    session.commit()

    rows = {item["user_id"]: item for item in _get(session, admin).json()["items"]}

    assert rows[agent.id]["total_seconds"] == 600
    assert rows[inactive.id]["is_active"] is False
    assert rows[deleted.id]["full_name"] is None
    assert rows[deleted.id]["is_active"] is None


def test_status_stats_user_filter_missing_user_and_unknown_status():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    other = _user(session, company, "agent-b")
    session.add_all(
        [
            _status_interval(company.id, user_id=agent.id),
            _status_interval(company.id, user_id=other.id, status="waiting"),
            _status_interval(company.id, user_id=agent.id, status="focus"),
        ]
    )
    session.commit()

    filtered = _get(session, admin, user_id=agent.id).json()
    missing = _get(session, admin, user_id="missing-agent").json()

    assert [item["user_id"] for item in filtered["items"]] == [agent.id]
    assert filtered["total"]["online_seconds"] == 600
    assert filtered["total"]["waiting_seconds"] == 0
    assert missing["items"] == []
    assert missing["total"] == _zero_total()


def test_status_stats_rejects_non_admin_roles():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    trainee = _user(session, company, "trainee-a", role="trainee")
    readonly = _user(session, company, "chiki-a", role="chiki_piki")
    session.add(_status_interval(company.id, user_id=agent.id))
    session.commit()

    assert _get(session, admin).status_code == 200
    for user, role in [(agent, "agent"), (trainee, "trainee"), (readonly, "chiki_piki")]:
        response = _api_client(session, _current_user(user.id, role=role)).get("/api/v1/management/statistics/statuses")
        assert response.status_code == 403
