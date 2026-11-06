from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from api.app.routers import users
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.db import Base
from api.app.models import Client, Company, StatsCompletion, Ticket, TicketStatus, User
from api.app.routers.users import get_my_completion_stats


@pytest.fixture(autouse=True)
def freeze_stats_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 3, 15, tzinfo=ZoneInfo("Europe/Moscow")).astimezone(tz)
    monkeypatch.setattr(users, "datetime", Clock)


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


def _completion(
    company_id: str,
    object_id: str,
    *,
    object_type: str = "ticket",
    user_id: str | None = "agent-a",
    completed_at: datetime = datetime(2026, 9, 3, 11, 30, 0),
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


def _current_user(user: User) -> SimpleNamespace:
    return SimpleNamespace(id=user.id, role=user.role, real_user_id=user.id, real_role=user.role)


def test_completion_stats_read_stats_completion_and_preserve_personal_contract():
    session = _session()
    company = Company(id=str(uuid.uuid4()), name="ACME", created_at=datetime.utcnow())
    agent = User(
        id="agent-a",
        company_id=company.id,
        email="agent@example.test",
        login="agent-a",
        full_name="Agent A",
        role="agent",
        is_active=True,
        legacy_ticket_count=7,
    )
    admin = User(
        id="admin-b",
        company_id=company.id,
        email="admin@example.test",
        login="admin-b",
        full_name="Admin B",
        role="admin",
        is_active=True,
    )
    session.add_all(
        [
            company,
            agent,
            admin,
            _completion(company.id, "ticket-1", object_type="ticket"),
            _completion(company.id, "task-normal", object_type="task"),
            _completion(company.id, "task-group-child", object_type="task"),
            _completion(company.id, "reopened", object_type="ticket", is_completed=False),
            _completion(company.id, "unattributed", object_type="task", user_id=None),
            _completion(company.id, "future-kind-not-tracked", object_type="future_unknown_kind"),
        ]
    )
    session.commit()

    agent_stats = get_my_completion_stats(db=session, current_user=_current_user(agent))
    admin_stats = get_my_completion_stats(db=session, current_user=_current_user(admin))

    assert agent_stats.display_name == "Agent A"
    assert agent_stats.role == "agent"
    assert agent_stats.legacy == 7
    assert agent_stats.calendar["year"].tickets == 1
    assert agent_stats.calendar["year"].tasks == 2
    assert agent_stats.calendar["year"].total == 3
    assert agent_stats.calendar["month"].total == 3
    assert admin_stats.calendar is None
    assert admin_stats.total is None


def test_completion_stats_survive_hard_deleted_source_ticket():
    session = _session()
    company = Company(id=str(uuid.uuid4()), name="ACME", created_at=datetime.utcnow())
    client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Client")
    agent = User(
        id="agent-a",
        company_id=company.id,
        email="agent@example.test",
        login="agent-a",
        full_name="Agent A",
        role="agent",
        is_active=True,
    )
    ticket = Ticket(
        id="ticket-hard-deleted",
        company_id=company.id,
        client_id=client.id,
        assignee_id=agent.id,
        subject="Deleted source",
        status=TicketStatus.CLOSED.value,
        closed_at=datetime(2026, 9, 3, 11, 30, 0),
        created_at=datetime(2026, 9, 3, 11, 0, 0),
        updated_at=datetime(2026, 9, 3, 11, 30, 0),
    )
    session.add_all([company, client, agent, ticket])
    session.flush()
    session.add(_completion(company.id, ticket.id, object_type="ticket"))
    session.delete(ticket)
    session.commit()

    stats = get_my_completion_stats(db=session, current_user=_current_user(agent))

    assert stats.calendar["year"].tickets == 1
    assert stats.calendar["year"].total == 1


@pytest.mark.parametrize("now,expected", [
    (datetime(2026, 11, 6, 0, 15), ("2026-11-06", "2026-11-02", "2026-11-01", "2026-01-01")),
    (datetime(2027, 1, 1, 0, 15), ("2027-01-01", "2026-12-28", "2027-01-01", "2027-01-01")),
])
@pytest.mark.parametrize("role", ["agent", "trainee"])
def test_calendar_rolling_boundaries_all_history_and_legacy(monkeypatch, now, expected, role):
    msk = ZoneInfo("Europe/Moscow")
    now = now.replace(tzinfo=msk)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)
    monkeypatch.setattr(users, "datetime", Clock)
    bounds = users._completion_period_bounds()
    assert [bounds[key].isoformat() for key in ("day", "week", "month", "year")] == [date + "T00:00:00+03:00" for date in expected]
    session = _session()
    company = Company(id="company", name="ACME")
    agent = User(id="agent-a", company_id=company.id, email="a@test", login="a", full_name="Agent", role=role, legacy_ticket_count=17)
    session.add_all([company, agent])
    starts = {**bounds, **{f"rolling_{key}": bounds["day"] - timedelta(days=days) for key, days in (("week", 6), ("month", 29), ("year", 364))}}
    dates = [datetime(2020, 1, 1, tzinfo=msk)]
    for start in starts.values():
        dates.extend([start - timedelta(microseconds=1), start, start + timedelta(microseconds=1)])
    for index, date in enumerate(dates):
        # SQLite stores UTC without timezone, as production completion writers do.
        utc = date.astimezone(timezone.utc).replace(tzinfo=None)
        session.add(_completion(company.id, f"ticket-{index}", completed_at=utc))
        session.add(_completion(company.id, f"task-{index}", object_type="task", completed_at=utc))
    session.add(_completion(company.id, "other-agent", user_id="other"))
    session.add(_completion("other-company", "other-company"))
    session.add(_completion(company.id, "reopened", is_completed=False))
    session.commit()
    result = get_my_completion_stats(db=session, current_user=_current_user(agent))
    assert result.legacy == 17
    assert result.total.model_dump() == {"tickets": len(dates), "tasks": len(dates), "total": len(dates) * 2}
    for key, start in starts.items():
        stats = result.rolling[key.removeprefix("rolling_")] if key.startswith("rolling_") else result.calendar[key]
        count = sum(date >= start for date in dates)
        assert stats.model_dump() == {"tickets": count, "tasks": count, "total": count * 2}, key
    session.close()


@pytest.mark.parametrize("role", ["admin", "chiki_piki", "client"])
def test_other_roles_have_no_personal_periods(role):
    session = _session()
    company = Company(id="company", name="ACME")
    user = User(id="user", company_id=company.id, email="u@test", login="u", full_name="User", role=role, legacy_ticket_count=9)
    session.add_all([company, user])
    session.commit()
    result = get_my_completion_stats(db=session, current_user=_current_user(user))
    assert result.total is result.calendar is result.rolling is None
    assert result.legacy == 9
    session.close()
