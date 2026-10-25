from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
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


def _add_status_chain(session, company_id: str, user_id: str, *, count: int, start_hour: int = 8) -> list[StatsUserStatusInterval]:
    rows = []
    statuses = ["online", "waiting"]
    for index in range(count):
        started_at = datetime(2026, 9, 2, start_hour) + timedelta(minutes=index)
        ended_at = datetime(2026, 9, 2, start_hour) + timedelta(minutes=index + 1)
        rows.append(
            _status_interval(
                company_id,
                user_id=user_id,
                status=statuses[index % 2],
                started_at=started_at,
                ended_at=ended_at,
            )
        )
    session.add_all(rows)
    return rows


def _seed_admin_context(session):
    company = Company(id="company-a", name="ACME", created_at=datetime.utcnow())
    session.add(company)
    admin = _user(session, company, "admin-a", role="admin")
    agent = _user(session, company, "agent-a")
    return company, admin, agent


def _get(session, admin: User, **params):
    return _api_client(session, _current_user(admin.id)).get("/api/v1/management/statistics/statuses", params=params)


def _get_transitions(session, admin: User, **params):
    return _api_client(session, _current_user(admin.id)).get("/api/v1/management/statistics/status-transitions", params=params)


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


def test_status_transitions_returns_adjacent_status_change():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    changed_at = datetime(2026, 9, 2, 10, 10)
    session.add_all(
        [
            _status_interval(company.id, user_id=agent.id, status="online", started_at=datetime(2026, 9, 2, 10, 0), ended_at=changed_at),
            _status_interval(company.id, user_id=agent.id, status="waiting", started_at=changed_at, ended_at=datetime(2026, 9, 2, 10, 20)),
        ]
    )
    session.commit()

    response = _get_transitions(session, admin)

    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "user_id": agent.id,
            "full_name": "Agent A",
            "is_active": True,
            "from_status": "online",
            "to_status": "waiting",
            "changed_at": "2026-09-02T10:10:00",
        }
    ]


def test_status_transitions_excludes_same_status_first_interval_and_gaps():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    session.add_all(
        [
            _status_interval(company.id, user_id=agent.id, status="online", started_at=datetime(2026, 9, 2, 8, 0), ended_at=datetime(2026, 9, 2, 8, 10)),
            _status_interval(company.id, user_id=agent.id, status="online", started_at=datetime(2026, 9, 2, 8, 10), ended_at=datetime(2026, 9, 2, 8, 20)),
            _status_interval(company.id, user_id=agent.id, status="waiting", started_at=datetime(2026, 9, 2, 9, 0), ended_at=datetime(2026, 9, 2, 9, 10)),
            _status_interval(company.id, user_id=agent.id, status="online", started_at=datetime(2026, 9, 2, 9, 20), ended_at=datetime(2026, 9, 2, 9, 30)),
        ]
    )
    session.commit()

    assert _get_transitions(session, admin).json()["items"] == []


def test_status_transitions_reactivation_after_tracking_gap_is_not_transition():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    session.add_all(
        [
            _status_interval(company.id, user_id=agent.id, status="online", started_at=datetime(2026, 9, 2, 10, 0), ended_at=datetime(2026, 9, 2, 10, 10)),
            _status_interval(company.id, user_id=agent.id, status="waiting", started_at=datetime(2026, 9, 2, 11, 0), ended_at=datetime(2026, 9, 2, 11, 10)),
        ]
    )
    session.commit()

    assert _get_transitions(session, admin).json()["items"] == []


def test_status_transitions_user_filter_and_started_at_period_filter():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    other = _user(session, company, "agent-b")
    session.add_all(
        [
            _status_interval(company.id, user_id=agent.id, status="online", started_at=datetime(2026, 9, 2, 9, 0), ended_at=datetime(2026, 9, 2, 10, 0)),
            _status_interval(company.id, user_id=agent.id, status="waiting", started_at=datetime(2026, 9, 2, 10, 0), ended_at=datetime(2026, 9, 2, 11, 0)),
            _status_interval(company.id, user_id=agent.id, status="online", started_at=datetime(2026, 9, 2, 11, 0), ended_at=datetime(2026, 9, 2, 12, 0)),
            _status_interval(company.id, user_id=other.id, status="online", started_at=datetime(2026, 9, 2, 9, 30), ended_at=datetime(2026, 9, 2, 10, 30)),
            _status_interval(company.id, user_id=other.id, status="waiting", started_at=datetime(2026, 9, 2, 10, 30), ended_at=datetime(2026, 9, 2, 11, 30)),
        ]
    )
    session.commit()

    filtered = _get_transitions(
        session,
        admin,
        user_id=agent.id,
        date_from="2026-09-02T10:30:00",
        date_to="2026-09-02T12:00:00",
    ).json()

    assert [(item["user_id"], item["from_status"], item["to_status"], item["changed_at"]) for item in filtered["items"]] == [
        (agent.id, "waiting", "online", "2026-09-02T11:00:00")
    ]


def test_status_transitions_company_isolation_and_deleted_user_metadata():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    deleted = _user(session, company, "deleted-agent")
    other_company = Company(id="company-b", name="Other", created_at=datetime.utcnow())
    session.add(other_company)
    other_admin = _user(session, other_company, "other-admin", role="admin")
    session.add_all(
        [
            _status_interval(company.id, user_id=deleted.id, status="online", started_at=datetime(2026, 9, 2, 10, 0), ended_at=datetime(2026, 9, 2, 10, 10)),
            _status_interval(company.id, user_id=deleted.id, status="waiting", started_at=datetime(2026, 9, 2, 10, 10), ended_at=datetime(2026, 9, 2, 10, 20)),
            _status_interval(other_company.id, user_id=other_admin.id, status="online", started_at=datetime(2026, 9, 2, 10, 0), ended_at=datetime(2026, 9, 2, 10, 10)),
            _status_interval(other_company.id, user_id=other_admin.id, status="waiting", started_at=datetime(2026, 9, 2, 10, 10), ended_at=datetime(2026, 9, 2, 10, 20)),
        ]
    )
    session.delete(deleted)
    session.commit()

    items = _get_transitions(session, admin).json()["items"]

    assert len(items) == 1
    assert items[0]["user_id"] == "deleted-agent"
    assert items[0]["full_name"] is None
    assert items[0]["is_active"] is None


def test_status_transitions_ambiguous_previous_interval_is_skipped():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    changed_at = datetime(2026, 9, 2, 10, 10)
    session.add_all(
        [
            _status_interval(company.id, user_id=agent.id, status="online", started_at=datetime(2026, 9, 2, 10, 0), ended_at=changed_at),
            _status_interval(company.id, user_id=agent.id, status="lunch", started_at=datetime(2026, 9, 2, 10, 5), ended_at=changed_at),
            _status_interval(company.id, user_id=agent.id, status="waiting", started_at=changed_at, ended_at=datetime(2026, 9, 2, 10, 20)),
        ]
    )
    session.commit()

    assert _get_transitions(session, admin).json()["items"] == []


def test_status_transitions_loads_history_with_single_status_interval_select():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    other = _user(session, company, "agent-b")
    session.add_all(
        [
            _status_interval(company.id, user_id=agent.id, status="online", started_at=datetime(2026, 9, 2, 10, 0), ended_at=datetime(2026, 9, 2, 10, 10)),
            _status_interval(company.id, user_id=agent.id, status="waiting", started_at=datetime(2026, 9, 2, 10, 10), ended_at=datetime(2026, 9, 2, 10, 20)),
            _status_interval(company.id, user_id=agent.id, status="lunch", started_at=datetime(2026, 9, 2, 10, 20), ended_at=datetime(2026, 9, 2, 10, 30)),
            _status_interval(company.id, user_id=other.id, status="online", started_at=datetime(2026, 9, 2, 11, 0), ended_at=datetime(2026, 9, 2, 11, 10)),
            _status_interval(company.id, user_id=other.id, status="waiting", started_at=datetime(2026, 9, 2, 11, 10), ended_at=datetime(2026, 9, 2, 11, 20)),
        ]
    )
    session.commit()
    counter = {"count": 0}
    engine = session.get_bind()

    def count_status_interval_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
        normalized = statement.lower()
        if normalized.lstrip().startswith("select") and "stats_user_status_intervals" in normalized:
            counter["count"] += 1

    event.listen(engine, "before_cursor_execute", count_status_interval_selects)
    try:
        response = _get_transitions(session, admin)
    finally:
        event.remove(engine, "before_cursor_execute", count_status_interval_selects)

    assert response.status_code == 200
    assert len(response.json()["items"]) == 3
    assert counter["count"] == 1


def test_status_transitions_default_limit_and_cursor_paginates_without_duplicates():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    _add_status_chain(session, company.id, agent.id, count=18)
    session.commit()

    first = _get_transitions(session, admin).json()
    second = _get_transitions(session, admin, cursor=first["next_cursor"]).json()

    first_ids = {(item["changed_at"], item["to_status"]) for item in first["items"]}
    second_ids = {(item["changed_at"], item["to_status"]) for item in second["items"]}
    assert len(first["items"]) == 15
    assert first["has_more"] is True
    assert first["next_cursor"]
    assert len(second["items"]) == 2
    assert second["has_more"] is False
    assert second["next_cursor"] is None
    assert first_ids.isdisjoint(second_ids)


@pytest.mark.parametrize("sort_order", ["asc", "desc"])
def test_status_transitions_cursor_tie_break_with_same_changed_at(sort_order):
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    other = _user(session, company, "agent-b")
    third = _user(session, company, "agent-c")
    changed_at = datetime(2026, 9, 2, 10, 10)
    currents = []
    for index, user_id in enumerate([agent.id, other.id, third.id]):
        previous = _status_interval(
            company.id,
            user_id=user_id,
            status="online",
            started_at=datetime(2026, 9, 2, 10, index, 0),
            ended_at=changed_at,
        )
        current = _status_interval(
            company.id,
            user_id=user_id,
            status="waiting",
            started_at=changed_at,
            ended_at=datetime(2026, 9, 2, 10, 20 + index, 0),
        )
        current.id = f"current-{index}"
        currents.append(current)
        session.add_all([previous, current])
    session.commit()

    first = _get_transitions(session, admin, limit=2, sort_order=sort_order).json()
    second = _get_transitions(session, admin, limit=2, cursor=first["next_cursor"], sort_order=sort_order).json()

    seen = [item["user_id"] for item in first["items"] + second["items"]]
    assert len(first["items"]) == 2
    assert first["has_more"] is True
    assert len(second["items"]) == 1
    assert set(seen) == {agent.id, other.id, third.id}
    assert len(seen) == len(set(seen)) == 3
    assert seen == ([agent.id, other.id, third.id] if sort_order == "asc" else [third.id, other.id, agent.id])


def test_status_transitions_invalid_rows_do_not_consume_page_size():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    _add_status_chain(session, company.id, agent.id, count=17)
    session.add_all(
        [
            _status_interval(company.id, user_id=agent.id, status="online", started_at=datetime(2026, 9, 2, 11, 0), ended_at=datetime(2026, 9, 2, 11, 10)),
            _status_interval(company.id, user_id=agent.id, status="online", started_at=datetime(2026, 9, 2, 11, 10), ended_at=datetime(2026, 9, 2, 11, 20)),
            _status_interval(company.id, user_id=agent.id, status="waiting", started_at=datetime(2026, 9, 2, 12, 0), ended_at=datetime(2026, 9, 2, 12, 10)),
        ]
    )
    session.commit()

    first = _get_transitions(session, admin).json()

    assert len(first["items"]) == 15
    assert first["has_more"] is True


def test_status_transitions_cursor_preserves_user_and_period_semantics():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    other = _user(session, company, "agent-b")
    _add_status_chain(session, company.id, agent.id, count=18)
    _add_status_chain(session, company.id, other.id, count=18, start_hour=9)
    session.commit()

    first = _get_transitions(
        session,
        admin,
        user_id=agent.id,
        date_from="2026-09-02T08:05:00",
        date_to="2026-09-02T08:18:00",
        limit=5,
    ).json()
    second = _get_transitions(
        session,
        admin,
        user_id=agent.id,
        date_from="2026-09-02T08:05:00",
        date_to="2026-09-02T08:18:00",
        limit=5,
        cursor=first["next_cursor"],
    ).json()

    assert first["has_more"] is True
    assert all(item["user_id"] == agent.id for item in first["items"] + second["items"])
    assert all("2026-09-02T08:" in item["changed_at"] for item in first["items"] + second["items"])


def test_status_transitions_rejects_invalid_cursor_and_too_large_limit():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    _add_status_chain(session, company.id, agent.id, count=3)
    session.commit()

    invalid_cursor = _get_transitions(session, admin, cursor="not-a-valid-cursor")
    too_large_limit = _get_transitions(session, admin, limit=101)

    assert invalid_cursor.status_code == 400
    assert invalid_cursor.json()["detail"]["error"]["code"] == "invalid_cursor"
    assert too_large_limit.status_code == 422


def test_status_transitions_rejects_non_admin_roles():
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    trainee = _user(session, company, "trainee-a", role="trainee")
    readonly = _user(session, company, "chiki-a", role="chiki_piki")
    session.add_all(
        [
            _status_interval(company.id, user_id=agent.id, status="online", started_at=datetime(2026, 9, 2, 10, 0), ended_at=datetime(2026, 9, 2, 10, 10)),
            _status_interval(company.id, user_id=agent.id, status="waiting", started_at=datetime(2026, 9, 2, 10, 10), ended_at=datetime(2026, 9, 2, 10, 20)),
        ]
    )
    session.commit()

    assert _get_transitions(session, admin).status_code == 200
    for user, role in [(agent, "agent"), (trainee, "trainee"), (readonly, "chiki_piki")]:
        response = _api_client(session, _current_user(user.id, role=role)).get("/api/v1/management/statistics/status-transitions")
        assert response.status_code == 403


@pytest.mark.parametrize("sort_order", ["asc", "desc"])
def test_status_transitions_full_page_total_sort_and_cursor(sort_order):
    session = _session()
    company, admin, agent = _seed_admin_context(session)
    _add_status_chain(session, company.id, agent.id, count=126)
    session.commit()
    params = dict(limit=50, sort_order=sort_order, include_total=True, user_id=agent.id)
    pages = []
    while True:
        response = _get_transitions(session, admin, **params)
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_count"] == 125
        pages.append(payload["items"])
        if not payload["has_more"]:
            break
        params["cursor"] = payload["next_cursor"]
    assert [len(page) for page in pages] == [50, 50, 25]
    times = [row["changed_at"] for page in pages for row in page]
    assert times == sorted(times, reverse=sort_order == "desc")
    assert len(set(times)) == 125
    missing = _get_transitions(session, admin, include_total=True, user_id="missing").json()
    assert missing["total_count"] == 0
    assert missing["items"] == []

    filtered = _get_transitions(
        session, admin, include_total=True, user_id=agent.id, sort_order=sort_order,
        date_from="2026-09-02T08:02:00Z", date_to="2026-09-02T08:05:00Z",
    ).json()
    assert filtered["total_count"] == len(filtered["items"]) == 3
