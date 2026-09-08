from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.db import Base
from api.app.models import Client, Company, Ticket, TicketStatus, TicketTaskKind, TicketWorkflowType, User
from api.app.routers.users import get_my_completion_stats


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


def test_completion_stats_counts_normal_task_and_group_child_but_excludes_group_parent():
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
    admin = User(
        id="admin-b",
        company_id=company.id,
        email="admin@example.test",
        login="admin-b",
        full_name="Admin B",
        role="admin",
        is_active=True,
    )
    normal_task = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        assignee_id=agent.id,
        closed_by_user_id=agent.id,
        completed_by_user_id=agent.id,
        completed_counted_at=datetime(2026, 9, 3, 11, 30, 0),
        subject="Normal task",
        workflow_type=TicketWorkflowType.TASK.value,
        task_kind=TicketTaskKind.NORMAL.value,
        status=TicketStatus.CLOSED.value,
        closed_at=datetime(2026, 9, 3, 11, 30, 0),
        created_at=datetime(2026, 9, 3, 11, 0, 0),
        updated_at=datetime(2026, 9, 3, 11, 30, 0),
    )
    group_child = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        assignee_id=agent.id,
        closed_by_user_id=admin.id,
        completed_by_user_id=agent.id,
        completed_counted_at=datetime(2026, 9, 3, 12, 0, 0),
        subject="Group child",
        workflow_type=TicketWorkflowType.TASK.value,
        task_kind=TicketTaskKind.GROUP_CHILD.value,
        status=TicketStatus.CLOSED.value,
        closed_at=datetime(2026, 9, 3, 12, 0, 0),
        created_at=datetime(2026, 9, 3, 11, 0, 0),
        updated_at=datetime(2026, 9, 3, 12, 0, 0),
    )
    group_parent = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        assignee_id=agent.id,
        closed_by_user_id=agent.id,
        completed_by_user_id=agent.id,
        completed_counted_at=datetime(2026, 9, 3, 12, 30, 0),
        subject="Group parent",
        workflow_type=TicketWorkflowType.TASK.value,
        task_kind=TicketTaskKind.GROUP_PARENT.value,
        status=TicketStatus.CLOSED.value,
        closed_at=datetime(2026, 9, 3, 12, 30, 0),
        created_at=datetime(2026, 9, 3, 11, 0, 0),
        updated_at=datetime(2026, 9, 3, 12, 30, 0),
    )
    session.add_all([company, client, agent, admin, normal_task, group_child, group_parent])
    session.commit()

    agent_stats = get_my_completion_stats(
        db=session,
        current_user=SimpleNamespace(id=agent.id, role="agent", real_user_id=agent.id, real_role="agent"),
    )
    admin_stats = get_my_completion_stats(
        db=session,
        current_user=SimpleNamespace(id=admin.id, role="admin", real_user_id=admin.id, real_role="admin"),
    )

    assert agent_stats.calendar["year"].tasks == 2
    assert agent_stats.calendar["month"].tasks == 2
    assert agent_stats.calendar["year"].total == 2
    assert admin_stats.calendar is None
    assert admin_stats.total is None
