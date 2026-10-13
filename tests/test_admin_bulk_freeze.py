from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.auth import CurrentUser
from api.app.db import Base, get_db
from api.app.models import (
    Client,
    Company,
    MessageType,
    StatsWorkInterval,
    Ticket,
    TicketMessage,
    TicketStatus,
    TicketTaskKind,
    TicketWorkflowType,
    User,
)
from api.app.routers import tickets


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
    app.include_router(tickets.router)
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[tickets.get_current_user] = lambda: _current_user(user)
    return TestClient(app)


def _company(session) -> Company:
    company = Company(id=str(uuid.uuid4()), name="ACME", is_active=True, created_at=datetime.utcnow())
    session.add(company)
    return company


def _user(session, company: Company, role: str) -> User:
    user_id = str(uuid.uuid4())
    user = User(
        id=user_id,
        company_id=company.id,
        email=f"{user_id}@example.test",
        login=user_id,
        full_name=f"{role}-{user_id[:8]}",
        role=role,
        is_active=True,
        availability_status="online",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(user)
    return user


def _contact(session, company: Company) -> Client:
    client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Client")
    session.add(client)
    return client


def _ticket(
    session,
    company: Company,
    client: Client,
    *,
    assignee: User | None = None,
    status: str = TicketStatus.NEW.value,
    workflow_type: str = TicketWorkflowType.TICKET.value,
    is_frozen: bool = False,
    freeze_until: datetime | None = None,
    is_special: bool = False,
) -> Ticket:
    now = datetime.utcnow()
    ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        assignee_id=assignee.id if assignee else None,
        subject="Test",
        status=status,
        workflow_type=workflow_type,
        task_kind=TicketTaskKind.NORMAL.value,
        is_frozen=is_frozen,
        freeze_until=freeze_until,
        is_special=is_special,
        special_assignee_id=assignee.id if is_special and assignee else None,
        closed_at=now if status == TicketStatus.CLOSED.value else None,
        created_at=now,
        updated_at=now,
    )
    session.add(ticket)
    return ticket


def _open_work_interval(session, ticket: Ticket, user: User) -> StatsWorkInterval:
    object_type = "task" if ticket.workflow_type == TicketWorkflowType.TASK.value else "ticket"
    interval = StatsWorkInterval(
        id=str(uuid.uuid4()),
        company_id=ticket.company_id,
        object_id=ticket.id,
        object_type=object_type,
        task_kind=ticket.task_kind if object_type == "task" else None,
        user_id=user.id,
        started_at=datetime.utcnow() - timedelta(minutes=5),
        ended_at=None,
        end_reason=None,
        created_at=datetime.utcnow() - timedelta(minutes=5),
    )
    session.add(interval)
    return interval


def _system_messages(session, ticket: Ticket) -> list[TicketMessage]:
    return session.execute(
        select(TicketMessage)
        .where(TicketMessage.ticket_id == ticket.id, TicketMessage.message_type == MessageType.SYSTEM.value)
        .order_by(TicketMessage.created_at)
    ).scalars().all()


def test_admin_bulk_freeze_requires_admin():
    session = _session()
    company = _company(session)
    agent = _user(session, company, "agent")
    contact = _contact(session, company)
    ticket = _ticket(session, company, contact)
    session.commit()

    response = _client(session, agent).post("/api/v1/tickets/admin/freeze", json={"ticket_ids": [ticket.id]})

    assert response.status_code == 403
    session.refresh(ticket)
    assert ticket.is_frozen is False
    assert _system_messages(session, ticket) == []


def test_admin_bulk_unfreeze_requires_admin():
    session = _session()
    company = _company(session)
    agent = _user(session, company, "agent")
    contact = _contact(session, company)
    ticket = _ticket(session, company, contact, is_frozen=True)
    session.commit()

    response = _client(session, agent).post("/api/v1/tickets/admin/unfreeze", json={"ticket_ids": [ticket.id]})

    assert response.status_code == 403
    session.refresh(ticket)
    assert ticket.is_frozen is True
    assert _system_messages(session, ticket) == []


def test_admin_bulk_freeze_freezes_tickets_and_tasks_through_lifecycle():
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    agent = _user(session, company, "agent")
    contact = _contact(session, company)
    ticket = _ticket(session, company, contact, assignee=agent)
    task = _ticket(session, company, contact, assignee=agent, workflow_type=TicketWorkflowType.TASK.value)
    ticket_interval = _open_work_interval(session, ticket, agent)
    task_interval = _open_work_interval(session, task, agent)
    session.commit()

    response = _client(session, admin).post(
        "/api/v1/tickets/admin/freeze",
        json={"ticket_ids": [ticket.id, task.id, ticket.id]},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "frozen": 2,
        "already_frozen": 0,
        "invalid": 0,
        "closed": 0,
        "not_found": [],
    }
    session.refresh(ticket)
    session.refresh(task)
    session.refresh(ticket_interval)
    session.refresh(task_interval)
    assert ticket.is_frozen is True
    assert ticket.freeze_until is None
    assert task.is_frozen is True
    assert task.freeze_until is None
    assert ticket_interval.ended_at is not None
    assert ticket_interval.end_reason == "freeze"
    assert task_interval.ended_at is not None
    assert task_interval.end_reason == "freeze"
    assert [message.body for message in _system_messages(session, ticket)] == ["Заявка заморожена"]
    assert [message.body for message in _system_messages(session, task)] == ["Заявка заморожена"]


def test_admin_bulk_unfreeze_unfreezes_tickets_and_tasks_through_lifecycle_without_rerouting():
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    agent = _user(session, company, "agent")
    contact = _contact(session, company)
    ticket = _ticket(session, company, contact, assignee=agent, is_frozen=True, status=TicketStatus.PENDING.value)
    task = _ticket(
        session,
        company,
        contact,
        assignee=agent,
        workflow_type=TicketWorkflowType.TASK.value,
        is_frozen=True,
        status=TicketStatus.PENDING.value,
    )
    session.commit()

    response = _client(session, admin).post(
        "/api/v1/tickets/admin/unfreeze",
        json={"ticket_ids": [ticket.id, task.id, ticket.id]},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "unfrozen": 2,
        "already_unfrozen": 0,
        "closed": 0,
        "not_found": [],
    }
    session.refresh(ticket)
    session.refresh(task)
    assert ticket.is_frozen is False
    assert ticket.freeze_until is None
    assert ticket.status == TicketStatus.PENDING.value
    assert ticket.assignee_id == agent.id
    assert task.is_frozen is False
    assert task.freeze_until is None
    assert task.status == TicketStatus.PENDING.value
    assert task.assignee_id == agent.id
    intervals = session.execute(select(StatsWorkInterval).order_by(StatsWorkInterval.object_id)).scalars().all()
    assert len(intervals) == 2
    assert {interval.object_id for interval in intervals} == {ticket.id, task.id}
    assert all(interval.ended_at is None for interval in intervals)
    assert [message.body for message in _system_messages(session, ticket)] == ["Заявка разморожена"]
    assert [message.body for message in _system_messages(session, task)] == ["Заявка разморожена"]


def test_admin_bulk_freeze_skips_closed_and_already_frozen_without_touching_deadline():
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    agent = _user(session, company, "agent")
    contact = _contact(session, company)
    closed = _ticket(session, company, contact, assignee=agent, status=TicketStatus.CLOSED.value)
    freeze_until = datetime.utcnow() + timedelta(days=3)
    frozen = _ticket(session, company, contact, assignee=agent, is_frozen=True, freeze_until=freeze_until)
    frozen_interval = _open_work_interval(session, frozen, agent)
    missing_id = str(uuid.uuid4())
    session.commit()

    response = _client(session, admin).post(
        "/api/v1/tickets/admin/freeze",
        json={"ticket_ids": [closed.id, frozen.id, missing_id]},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "frozen": 0,
        "already_frozen": 1,
        "invalid": 0,
        "closed": 1,
        "not_found": [missing_id],
    }
    session.refresh(closed)
    session.refresh(frozen)
    session.refresh(frozen_interval)
    assert closed.is_frozen is False
    assert frozen.is_frozen is True
    assert frozen.freeze_until == freeze_until
    assert frozen_interval.ended_at is None
    assert frozen_interval.end_reason is None
    assert _system_messages(session, closed) == []
    assert _system_messages(session, frozen) == []


def test_freeze_rejects_unassigned_ticket_and_bulk_skips_unassigned_and_special():
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    agent = _user(session, company, "agent")
    contact = _contact(session, company)
    unassigned = _ticket(session, company, contact, status=TicketStatus.UNASSIGNED.value)
    without_assignee = _ticket(session, company, contact, status=TicketStatus.NEW.value)
    special = _ticket(session, company, contact, assignee=agent, is_special=True)
    session.commit()
    client = _client(session, admin)

    single_response = client.post(f"/api/v1/tickets/{unassigned.id}/freeze", json={"freeze_until": None})
    bulk_response = client.post(
        "/api/v1/tickets/admin/freeze",
        json={"ticket_ids": [unassigned.id, without_assignee.id, special.id]},
    )

    assert single_response.status_code == 409
    assert single_response.json()["detail"]["error"]["code"] == "freeze_unassigned_ticket"
    assert bulk_response.status_code == 200
    assert bulk_response.json() == {
        "ok": True,
        "frozen": 0,
        "already_frozen": 0,
        "invalid": 3,
        "closed": 0,
        "not_found": [],
    }
    for ticket in (unassigned, without_assignee, special):
        session.refresh(ticket)
        assert ticket.is_frozen is False
        assert _system_messages(session, ticket) == []


def test_admin_can_move_unassigned_ticket_to_special():
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    contact = _contact(session, company)
    ticket = _ticket(session, company, contact, status=TicketStatus.UNASSIGNED.value)
    session.commit()

    response = _client(session, admin).post(f"/api/v1/tickets/{ticket.id}/special")

    assert response.status_code == 200
    session.refresh(ticket)
    assert ticket.is_special is True
    assert ticket.assignee_id == admin.id


def test_admin_bulk_unfreeze_skips_closed_and_already_unfrozen_without_touching_fields():
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    agent = _user(session, company, "agent")
    contact = _contact(session, company)
    closed = _ticket(session, company, contact, assignee=agent, status=TicketStatus.CLOSED.value, is_frozen=True)
    active = _ticket(session, company, contact, assignee=agent, status=TicketStatus.PENDING.value, is_frozen=False)
    missing_id = str(uuid.uuid4())
    session.commit()

    response = _client(session, admin).post(
        "/api/v1/tickets/admin/unfreeze",
        json={"ticket_ids": [closed.id, active.id, missing_id]},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "unfrozen": 0,
        "already_unfrozen": 1,
        "closed": 1,
        "not_found": [missing_id],
    }
    session.refresh(closed)
    session.refresh(active)
    assert closed.is_frozen is True
    assert closed.status == TicketStatus.CLOSED.value
    assert closed.assignee_id == agent.id
    assert active.is_frozen is False
    assert active.status == TicketStatus.PENDING.value
    assert active.assignee_id == agent.id
    assert _system_messages(session, closed) == []
    assert _system_messages(session, active) == []


def test_frozen_special_tickets_stay_in_special_and_out_of_frozen_lists():
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    contact = _contact(session, company)
    special = _ticket(session, company, contact, assignee=admin, is_special=True)
    frozen_special = _ticket(session, company, contact, assignee=admin, is_special=True, is_frozen=True)
    session.commit()
    client = _client(session, admin)

    special_response = client.get("/api/v1/tickets/special")
    frozen_response = client.get("/api/v1/tickets/frozen", params={"assignee_ids": admin.id})
    counts_response = client.get("/api/v1/tickets/counts", params={"assignee_ids": admin.id})

    assert special_response.status_code == 200
    assert frozen_response.status_code == 200
    assert counts_response.status_code == 200
    assert {item["id"] for item in special_response.json()["items"]} == {special.id, frozen_special.id}
    assert [item["id"] for item in frozen_response.json()["items"]] == []
    counts = counts_response.json()
    assert counts["special"] == 2
    assert counts["frozen"] == 0


def test_admin_bulk_freeze_rejects_empty_payload():
    session = _session()
    company = _company(session)
    admin = _user(session, company, "admin")
    session.commit()

    response = _client(session, admin).post("/api/v1/tickets/admin/freeze", json={"ticket_ids": []})

    assert response.status_code == 422
