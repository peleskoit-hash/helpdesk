from datetime import datetime, timedelta
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.db import Base
from api.app.models import Company, StatsUserStatusInterval, User
from api.app.services.statistics_lifecycle import (
    initialize_user_status_history,
    set_user_availability_status,
    sync_user_status_history_after_profile_update,
)


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


def _company(session) -> Company:
    company = Company(id=str(uuid.uuid4()), name="ACME", created_at=datetime.utcnow())
    session.add(company)
    session.flush()
    return company


def _user(
    session,
    company: Company,
    *,
    role: str = "agent",
    is_active: bool = True,
    availability_status: str = "online",
) -> User:
    now = datetime(2026, 9, 11, 9, 0, 0)
    user = User(
        id=str(uuid.uuid4()),
        company_id=company.id,
        email=f"{uuid.uuid4().hex}@example.test",
        login=f"user-{uuid.uuid4().hex}",
        password_hash="hash",
        full_name="User",
        role=role,
        is_active=is_active,
        availability_status=availability_status,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    session.flush()
    return user


def _interval(
    session,
    user: User,
    *,
    status: str,
    started_at: datetime,
    ended_at: datetime | None = None,
) -> StatsUserStatusInterval:
    interval = StatsUserStatusInterval(
        id=str(uuid.uuid4()),
        company_id=user.company_id,
        user_id=user.id,
        status=status,
        started_at=started_at,
        ended_at=ended_at,
        created_at=started_at,
    )
    session.add(interval)
    session.flush()
    return interval


def _intervals(session, user: User) -> list[StatsUserStatusInterval]:
    return session.execute(
        select(StatsUserStatusInterval)
        .where(StatsUserStatusInterval.user_id == user.id)
        .order_by(StatsUserStatusInterval.started_at, StatsUserStatusInterval.id)
    ).scalars().all()


def test_status_change_closes_current_interval_and_opens_new_one():
    session = _session()
    company = _company(session)
    user = _user(session, company, availability_status="online")
    started_at = datetime(2026, 9, 11, 9, 0, 0)
    changed_at = datetime(2026, 9, 11, 10, 15, 0)
    _interval(session, user, status="online", started_at=started_at)

    set_user_availability_status(session, user, "waiting", now=changed_at)

    rows = _intervals(session, user)
    assert [(row.status, row.started_at, row.ended_at) for row in rows] == [
        ("online", started_at, changed_at),
        ("waiting", changed_at, None),
    ]
    assert user.availability_status == "waiting"
    assert user.updated_at == changed_at


def test_same_status_does_not_split_current_interval():
    session = _session()
    company = _company(session)
    user = _user(session, company, availability_status="online")
    started_at = datetime(2026, 9, 11, 9, 0, 0)
    changed_at = datetime(2026, 9, 11, 10, 0, 0)
    _interval(session, user, status="online", started_at=started_at)

    set_user_availability_status(session, user, "online", now=changed_at)

    rows = _intervals(session, user)
    assert [(row.status, row.started_at, row.ended_at) for row in rows] == [
        ("online", started_at, None),
    ]
    assert user.updated_at == changed_at


def test_status_change_without_open_interval_creates_only_new_status_interval():
    session = _session()
    company = _company(session)
    user = _user(session, company, availability_status="online")
    changed_at = datetime(2026, 9, 11, 10, 40, 0)

    set_user_availability_status(session, user, "waiting", now=changed_at)

    rows = _intervals(session, user)
    assert [(row.status, row.started_at, row.ended_at) for row in rows] == [
        ("waiting", changed_at, None),
    ]


def test_status_change_closes_multiple_open_intervals_and_leaves_one_current_interval():
    session = _session()
    company = _company(session)
    user = _user(session, company, availability_status="online")
    first_started = datetime(2026, 9, 11, 9, 0, 0)
    second_started = datetime(2026, 9, 11, 9, 30, 0)
    changed_at = datetime(2026, 9, 11, 10, 40, 0)
    _interval(session, user, status="online", started_at=first_started)
    _interval(session, user, status="waiting", started_at=second_started)

    set_user_availability_status(session, user, "lunch", now=changed_at)

    rows = _intervals(session, user)
    assert [(row.status, row.started_at, row.ended_at) for row in rows] == [
        ("online", first_started, changed_at),
        ("waiting", second_started, changed_at),
        ("lunch", changed_at, None),
    ]


def test_initialize_status_history_opens_interval_for_active_agent_and_trainee_only():
    session = _session()
    company = _company(session)
    now = datetime(2026, 9, 11, 9, 0, 0)
    active_agent = _user(session, company, role="agent", availability_status="online")
    active_trainee = _user(session, company, role="trainee", availability_status="waiting")
    empty_status_agent = _user(session, company, role="agent", availability_status="")
    inactive_agent = _user(session, company, role="agent", is_active=False, availability_status="online")
    admin = _user(session, company, role="admin", availability_status="online")

    for user in [active_agent, active_trainee, empty_status_agent, inactive_agent, admin]:
        initialize_user_status_history(session, user, now=now)

    assert {
        (row.user_id, row.status, row.started_at, row.ended_at)
        for row in session.execute(select(StatsUserStatusInterval)).scalars().all()
    } == {
        (active_agent.id, "online", now, None),
        (active_trainee.id, "waiting", now, None),
        (empty_status_agent.id, "online", now, None),
    }


def test_deactivation_closes_current_interval_without_opening_new_one():
    session = _session()
    company = _company(session)
    user = _user(session, company, role="agent", is_active=True, availability_status="online")
    started_at = datetime(2026, 9, 11, 9, 0, 0)
    changed_at = datetime(2026, 9, 11, 11, 0, 0)
    _interval(session, user, status="online", started_at=started_at)

    previous_role = user.role
    previous_is_active = user.is_active
    previous_status = user.availability_status
    user.is_active = False
    sync_user_status_history_after_profile_update(
        session,
        user,
        previous_role=previous_role,
        previous_is_active=previous_is_active,
        previous_availability_status=previous_status,
        now=changed_at,
    )

    assert [(row.status, row.started_at, row.ended_at) for row in _intervals(session, user)] == [
        ("online", started_at, changed_at),
    ]


def test_activation_opens_new_interval_with_current_status():
    session = _session()
    company = _company(session)
    user = _user(session, company, role="agent", is_active=False, availability_status="waiting")
    changed_at = datetime(2026, 9, 11, 11, 0, 0)

    previous_role = user.role
    previous_is_active = user.is_active
    previous_status = user.availability_status
    user.is_active = True
    user.availability_status = "online"
    sync_user_status_history_after_profile_update(
        session,
        user,
        previous_role=previous_role,
        previous_is_active=previous_is_active,
        previous_availability_status=previous_status,
        now=changed_at,
    )

    assert [(row.status, row.started_at, row.ended_at) for row in _intervals(session, user)] == [
        ("online", changed_at, None),
    ]


def test_role_change_from_agent_to_admin_closes_interval():
    session = _session()
    company = _company(session)
    user = _user(session, company, role="agent", is_active=True, availability_status="online")
    started_at = datetime(2026, 9, 11, 9, 0, 0)
    changed_at = datetime(2026, 9, 11, 11, 0, 0)
    _interval(session, user, status="online", started_at=started_at)

    previous_role = user.role
    previous_is_active = user.is_active
    previous_status = user.availability_status
    user.role = "admin"
    sync_user_status_history_after_profile_update(
        session,
        user,
        previous_role=previous_role,
        previous_is_active=previous_is_active,
        previous_availability_status=previous_status,
        now=changed_at,
    )

    assert [(row.status, row.started_at, row.ended_at) for row in _intervals(session, user)] == [
        ("online", started_at, changed_at),
    ]


def test_role_change_from_admin_to_active_agent_opens_interval():
    session = _session()
    company = _company(session)
    user = _user(session, company, role="admin", is_active=True, availability_status="lunch")
    changed_at = datetime(2026, 9, 11, 11, 0, 0)

    previous_role = user.role
    previous_is_active = user.is_active
    previous_status = user.availability_status
    user.role = "agent"
    sync_user_status_history_after_profile_update(
        session,
        user,
        previous_role=previous_role,
        previous_is_active=previous_is_active,
        previous_availability_status=previous_status,
        now=changed_at,
    )

    assert [(row.status, row.started_at, row.ended_at) for row in _intervals(session, user)] == [
        ("lunch", changed_at, None),
    ]


def test_agent_to_trainee_without_status_change_does_not_split_interval():
    session = _session()
    company = _company(session)
    user = _user(session, company, role="agent", is_active=True, availability_status="online")
    started_at = datetime(2026, 9, 11, 9, 0, 0)
    changed_at = datetime(2026, 9, 11, 11, 0, 0)
    _interval(session, user, status="online", started_at=started_at)

    previous_role = user.role
    previous_is_active = user.is_active
    previous_status = user.availability_status
    user.role = "trainee"
    sync_user_status_history_after_profile_update(
        session,
        user,
        previous_role=previous_role,
        previous_is_active=previous_is_active,
        previous_availability_status=previous_status,
        now=changed_at,
    )

    assert [(row.status, row.started_at, row.ended_at) for row in _intervals(session, user)] == [
        ("online", started_at, None),
    ]
