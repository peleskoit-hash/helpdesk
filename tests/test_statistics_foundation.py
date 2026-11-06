from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.db import Base
from api.app.models import (
    Client,
    Company,
    StatsCompletion,
    StatsUserStatusInterval,
    StatsWorkInterval,
    Ticket,
    TicketStatus,
    TicketTaskKind,
    TicketWorkflowType,
    User,
)
from api.app.services import schema_evolution
from api.app.services.statistics_foundation import (
    AVAILABILITY_STATUS_FALLBACK,
    bootstrap_statistics,
    reset_statistics,
)


def _engine():
    return create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _session():
    engine = _engine()
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def _company(db, company_id: str = "company-a") -> Company:
    company = Company(id=company_id, name=company_id, created_at=datetime.utcnow())
    db.add(company)
    return company


def _client(db, company: Company, client_id: str = "client-a") -> Client:
    client = Client(id=client_id, company_id=company.id, display_name=client_id)
    db.add(client)
    return client


def _user(
    db,
    company: Company,
    user_id: str,
    *,
    role: str = "agent",
    is_active: bool = True,
    availability_status: str = "online",
) -> User:
    user = User(
        id=user_id,
        company_id=company.id,
        email=f"{user_id}@example.test",
        login=user_id,
        full_name=user_id,
        role=role,
        is_active=is_active,
        availability_status=availability_status,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(user)
    return user


def _ticket(
    db,
    company: Company,
    client: Client,
    ticket_id: str,
    *,
    assignee_id: str | None,
    workflow_type: str = TicketWorkflowType.TICKET.value,
    task_kind: str = TicketTaskKind.NORMAL.value,
    status: str = TicketStatus.NEW.value,
    is_frozen: bool = False,
) -> Ticket:
    ticket = Ticket(
        id=ticket_id,
        company_id=company.id,
        client_id=client.id,
        assignee_id=assignee_id,
        subject=ticket_id,
        workflow_type=workflow_type,
        task_kind=task_kind,
        status=status,
        is_frozen=is_frozen,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(ticket)
    return ticket


def _completion(company_id: str, object_id: str, *, user_id: str | None = "agent-a") -> StatsCompletion:
    now = datetime(2026, 9, 11, 10, 0, 0)
    return StatsCompletion(
        id=str(uuid.uuid4()),
        company_id=company_id,
        object_id=object_id,
        object_type="ticket",
        task_kind=None,
        completed_by_user_id=user_id,
        completed_at=now,
        is_completed=True,
        created_at=now,
        updated_at=now,
    )


def _work_interval(company_id: str, object_id: str, *, user_id: str = "agent-a") -> StatsWorkInterval:
    now = datetime(2026, 9, 11, 10, 0, 0)
    return StatsWorkInterval(
        id=str(uuid.uuid4()),
        company_id=company_id,
        object_id=object_id,
        object_type="ticket",
        task_kind=None,
        user_id=user_id,
        started_at=now,
        ended_at=None,
        end_reason=None,
        created_at=now,
    )


def _status_interval(company_id: str, user_id: str = "agent-a") -> StatsUserStatusInterval:
    now = datetime(2026, 9, 11, 10, 0, 0)
    return StatsUserStatusInterval(
        id=str(uuid.uuid4()),
        company_id=company_id,
        user_id=user_id,
        status="online",
        started_at=now,
        ended_at=None,
        created_at=now,
    )


def test_stats_schema_evolution_creates_tables_and_is_idempotent(monkeypatch):
    engine = _engine()
    monkeypatch.setattr(schema_evolution, "engine", engine)

    schema_evolution.ensure_stats_foundation_tables()
    schema_evolution.ensure_stats_foundation_tables()

    inspector = inspect(engine)
    assert {"stats_completions", "stats_work_intervals", "stats_user_status_intervals"} <= set(inspector.get_table_names())
    assert "ix_stats_completions_company_completed_at" in {idx["name"] for idx in inspector.get_indexes("stats_completions")}
    assert "ix_stats_work_intervals_company_object" in {idx["name"] for idx in inspector.get_indexes("stats_work_intervals")}
    assert "ix_stats_user_status_intervals_company_ended" in {idx["name"] for idx in inspector.get_indexes("stats_user_status_intervals")}


def test_stats_schema_evolution_does_not_modify_existing_user_or_ticket(monkeypatch):
    session = _session()
    engine = session.get_bind()
    monkeypatch.setattr(schema_evolution, "engine", engine)
    company = _company(session)
    client = _client(session, company)
    user = _user(session, company, "agent-a", availability_status="waiting")
    ticket = _ticket(session, company, client, "ticket-a", assignee_id=user.id)
    session.commit()

    before_user = (user.availability_status, user.is_active, user.updated_at)
    before_ticket = (ticket.assignee_id, ticket.status, ticket.completed_by_user_id, ticket.completed_counted_at)
    schema_evolution.ensure_stats_foundation_tables()
    session.expire_all()

    assert (user.availability_status, user.is_active, user.updated_at) == before_user
    assert (ticket.assignee_id, ticket.status, ticket.completed_by_user_id, ticket.completed_counted_at) == before_ticket


def test_stats_completion_unique_company_object_semantics():
    session = _session()
    session.add(_completion("company-a", "ticket-a"))
    session.commit()

    session.add(_completion("company-a", "ticket-a"))
    with pytest.raises(IntegrityError):
        session.commit()

    session.rollback()
    session.add(_completion("company-b", "ticket-a"))
    session.commit()


def test_stats_survive_source_ticket_hard_delete_and_user_deactivation():
    session = _session()
    company = _company(session)
    client = _client(session, company)
    user = _user(session, company, "agent-a")
    ticket = _ticket(session, company, client, "ticket-a", assignee_id=user.id)
    session.add_all([_completion(company.id, ticket.id, user_id=user.id), _work_interval(company.id, ticket.id, user_id=user.id)])
    session.commit()

    session.delete(ticket)
    session.commit()

    user.is_active = False
    session.commit()

    assert session.query(StatsCompletion).filter_by(company_id=company.id, object_id="ticket-a").count() == 1
    assert session.query(StatsWorkInterval).filter_by(company_id=company.id, object_id="ticket-a").count() == 1


def test_stats_sql_schema_matches_orm_core_contract(monkeypatch):
    engine = _engine()
    monkeypatch.setattr(schema_evolution, "engine", engine)
    schema_evolution.ensure_stats_foundation_tables()

    inspector = inspect(engine)

    completion_columns = {col["name"]: col for col in inspector.get_columns("stats_completions")}
    work_columns = {col["name"]: col for col in inspector.get_columns("stats_work_intervals")}
    status_columns = {col["name"]: col for col in inspector.get_columns("stats_user_status_intervals")}
    assert set(StatsCompletion.__table__.columns.keys()) == set(completion_columns)
    assert set(StatsWorkInterval.__table__.columns.keys()) == set(work_columns)
    assert set(StatsUserStatusInterval.__table__.columns.keys()) == set(status_columns)
    assert completion_columns["task_kind"]["nullable"] == StatsCompletion.__table__.columns["task_kind"].nullable
    assert completion_columns["is_completed"]["nullable"] == StatsCompletion.__table__.columns["is_completed"].nullable
    assert work_columns["ended_at"]["nullable"] == StatsWorkInterval.__table__.columns["ended_at"].nullable
    unique_constraints = inspector.get_unique_constraints("stats_completions")
    assert any(tuple(item["column_names"]) == ("company_id", "object_type", "object_id") for item in unique_constraints)
    assert {
        "ix_stats_completions_company_completed_at",
        "ix_stats_completions_company_user_completed_at",
    } <= {idx["name"] for idx in inspector.get_indexes("stats_completions")}
    assert {
        "ix_stats_work_intervals_company_user_started",
        "ix_stats_work_intervals_company_object",
        "ix_stats_work_intervals_company_ended",
    } <= {idx["name"] for idx in inspector.get_indexes("stats_work_intervals")}
    status_indexes = {idx["name"]: idx for idx in inspector.get_indexes("stats_user_status_intervals")}
    assert {
        "ix_stats_user_status_intervals_company_user_started",
        "ix_stats_user_status_intervals_company_started_id",
        "ix_stats_user_status_intervals_company_ended",
    } <= set(status_indexes)
    assert status_indexes["ix_stats_user_status_intervals_company_started_id"]["column_names"] == ["company_id", "started_at", "id"]


def test_reset_statistics_is_company_scoped_and_preserves_operational_data():
    session = _session()
    company_a = _company(session, "company-a")
    company_b = _company(session, "company-b")
    client_a = _client(session, company_a, "client-a")
    user_a = _user(session, company_a, "agent-a")
    _user(session, company_b, "agent-b")
    ticket = _ticket(session, company_a, client_a, "ticket-a", assignee_id=user_a.id)
    ticket.completed_by_user_id = user_a.id
    ticket.completed_counted_at = datetime(2026, 9, 1, 12, 0, 0)
    session.add_all([
        _completion(company_a.id, "object-a"),
        _work_interval(company_a.id, "object-a"),
        _status_interval(company_a.id),
        _completion(company_b.id, "object-b", user_id="agent-b"),
        _work_interval(company_b.id, "object-b", user_id="agent-b"),
        _status_interval(company_b.id, user_id="agent-b"),
    ])
    session.commit()

    first = reset_statistics(session, company_a.id)
    second = reset_statistics(session, company_a.id)
    session.commit()

    assert first.completions_deleted == 1
    assert first.work_intervals_deleted == 1
    assert first.user_status_intervals_deleted == 1
    assert second.completions_deleted == 0
    assert session.query(StatsCompletion).filter_by(company_id=company_a.id).count() == 0
    assert session.query(StatsCompletion).filter_by(company_id=company_b.id).count() == 1
    assert session.get(Ticket, ticket.id) is not None
    assert session.get(User, user_a.id) is not None
    assert ticket.completed_by_user_id == user_a.id
    assert ticket.completed_counted_at == datetime(2026, 9, 1, 12, 0, 0)


def test_bootstrap_status_intervals_for_active_support_users_only_and_idempotent():
    session = _session()
    company = _company(session)
    _user(session, company, "agent-a", role="agent", availability_status="waiting")
    _user(session, company, "trainee-a", role="trainee", availability_status="lunch")
    _user(session, company, "admin-a", role="admin", availability_status="online")
    _user(session, company, "chiki-a", role="chiki_piki", availability_status="online")
    _user(session, company, "inactive-agent", role="agent", is_active=False, availability_status="offline")
    _user(session, company, "empty-agent", role="agent", availability_status="")
    now = datetime(2026, 9, 11, 9, 30, 0)
    session.commit()

    first = bootstrap_statistics(session, company.id, now=now)
    second = bootstrap_statistics(session, company.id, now=now)
    session.commit()

    rows = session.query(StatsUserStatusInterval).order_by(StatsUserStatusInterval.user_id.asc()).all()
    assert first.user_status_intervals_created == 3
    assert second.user_status_intervals_created == 0
    assert [(row.user_id, row.status, row.started_at, row.ended_at) for row in rows] == [
        ("agent-a", "waiting", now, None),
        ("empty-agent", AVAILABILITY_STATUS_FALLBACK, now, None),
        ("trainee-a", "lunch", now, None),
    ]


def test_bootstrap_work_intervals_for_current_assigned_work_objects_only_and_idempotent():
    session = _session()
    company = _company(session)
    client = _client(session, company)
    agent = _user(session, company, "agent-a", role="agent")
    trainee = _user(session, company, "trainee-a", role="trainee")
    admin = _user(session, company, "admin-a", role="admin")
    _ticket(session, company, client, "ticket-open", assignee_id=agent.id)
    _ticket(session, company, client, "task-normal", assignee_id=agent.id, workflow_type=TicketWorkflowType.TASK.value)
    legacy_normal = _ticket(session, company, client, "task-null-kind", assignee_id=agent.id, workflow_type=TicketWorkflowType.TASK.value)
    legacy_normal.task_kind = None
    _ticket(session, company, client, "task-child", assignee_id=trainee.id, workflow_type=TicketWorkflowType.TASK.value, task_kind=TicketTaskKind.GROUP_CHILD.value)
    _ticket(session, company, client, "task-parent", assignee_id=agent.id, workflow_type=TicketWorkflowType.TASK.value, task_kind=TicketTaskKind.GROUP_PARENT.value)
    _ticket(session, company, client, "ticket-frozen", assignee_id=agent.id, is_frozen=True)
    _ticket(session, company, client, "ticket-closed", assignee_id=agent.id, status=TicketStatus.CLOSED.value)
    _ticket(session, company, client, "ticket-unassigned", assignee_id=None)
    _ticket(session, company, client, "ticket-admin", assignee_id=admin.id)
    _ticket(session, company, client, "auto-task", assignee_id=agent.id, workflow_type=TicketWorkflowType.AUTO.value)
    now = datetime(2026, 9, 11, 10, 15, 0)
    session.commit()

    first = bootstrap_statistics(session, company.id, now=now)
    second = bootstrap_statistics(session, company.id, now=now)
    session.commit()

    rows = session.query(StatsWorkInterval).order_by(StatsWorkInterval.object_id.asc()).all()
    assert first.work_intervals_created == 4
    assert second.work_intervals_created == 0
    assert [(row.object_id, row.user_id, row.object_type, row.task_kind, row.started_at, row.ended_at) for row in rows] == [
        ("task-child", trainee.id, "task", TicketTaskKind.GROUP_CHILD.value, now, None),
        ("task-normal", agent.id, "task", TicketTaskKind.NORMAL.value, now, None),
        ("task-null-kind", agent.id, "task", TicketTaskKind.NORMAL.value, now, None),
        ("ticket-open", agent.id, "ticket", None, now, None),
    ]


def test_bootstrap_work_intervals_skip_unknown_task_kind():
    session = _session()
    company = _company(session)
    client = _client(session, company)
    agent = _user(session, company, "agent-a", role="agent")
    task = _ticket(
        session,
        company,
        client,
        "task-unknown-kind",
        assignee_id=agent.id,
        workflow_type=TicketWorkflowType.TASK.value,
        task_kind="future_unknown_kind",
    )
    session.commit()

    result = bootstrap_statistics(session, company.id, now=datetime(2026, 9, 11, 10, 0, 0))
    session.commit()

    assert result.work_intervals_created == 0
    assert session.query(StatsWorkInterval).filter_by(company_id=company.id, object_id=task.id).count() == 0


def test_bootstrap_does_not_backfill_historical_completion():
    session = _session()
    company = _company(session)
    client = _client(session, company)
    agent = _user(session, company, "agent-a")
    closed = _ticket(session, company, client, "closed-ticket", assignee_id=agent.id, status=TicketStatus.CLOSED.value)
    closed.completed_by_user_id = agent.id
    closed.completed_counted_at = datetime(2026, 9, 1, 12, 0, 0)
    closed.closed_at = datetime(2026, 9, 1, 12, 0, 0)
    session.commit()

    bootstrap_statistics(session, company.id, now=datetime(2026, 9, 11, 10, 0, 0))
    session.commit()

    assert session.query(StatsCompletion).filter_by(company_id=company.id, object_id=closed.id).count() == 0



def test_status_admin_flag_evolution_preserves_legacy_intervals(monkeypatch):
    from sqlalchemy import text
    engine = _engine()
    monkeypatch.setattr(schema_evolution, "engine", engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE stats_user_status_intervals (id VARCHAR(36) PRIMARY KEY, company_id VARCHAR(36), user_id VARCHAR(36), status VARCHAR(40), started_at TIMESTAMP, ended_at TIMESTAMP, created_at TIMESTAMP)"))
        conn.execute(text("INSERT INTO stats_user_status_intervals VALUES ('old', 'company', 'agent', 'online', '2026-09-01', NULL, '2026-09-01')"))
        before = conn.execute(text("SELECT * FROM stats_user_status_intervals")).one()
    schema_evolution.ensure_stats_foundation_tables()
    schema_evolution.ensure_stats_foundation_tables()
    with engine.connect() as conn:
        after = conn.execute(text("SELECT * FROM stats_user_status_intervals")).one()
    assert tuple(after[:-1]) == tuple(before)
    assert after[-1] is None
    column = next(c for c in inspect(engine).get_columns("stats_user_status_intervals") if c["name"] == "changed_by_admin")
    assert column["nullable"] and column["default"] is None
