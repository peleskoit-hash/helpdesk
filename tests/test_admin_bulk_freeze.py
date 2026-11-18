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
from api.app.services import internal_task_scheduler


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


def _assert_system_history(client, ticket, expected):
    card = client.get(f"/api/v1/tickets/{ticket.id}")
    assert card.status_code == 200, card.text
    assert [message["body"] for message in card.json()["messages"] if message["message_type"] == "system"] == expected


def test_scheduler_unfreezes_due_tickets_and_tasks_once():
    with _session() as db:
        company = _company(db)
        agent = _user(db, company, "agent")
        contact = _contact(db, company)
        now = datetime.utcnow()
        options = dict(assignee=agent, is_frozen=True, freeze_until=now, status=TicketStatus.PENDING.value)
        ticket = _ticket(db, company, contact, **options)
        task = _ticket(db, company, contact, workflow_type=TicketWorkflowType.TASK.value, **options)
        future = _ticket(db, company, contact, **{**options, "freeze_until": now + timedelta(seconds=1)})
        closed = _ticket(db, company, contact, **{**options, "status": TicketStatus.CLOSED.value})
        unfrozen = _ticket(db, company, contact, **{**options, "is_frozen": False})
        no_date = _ticket(db, company, contact, **{**options, "freeze_until": None})
        db.add(TicketMessage(
            id=str(uuid.uuid4()), ticket_id=ticket.id, message_type=MessageType.AGENT.value, body="Reply",
        ))
        db.commit()

        run = internal_task_scheduler.run_ticket_auto_unfreeze_once
        cutoff = now.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=3)))
        sessions = sessionmaker(bind=db.get_bind(), autoflush=False)
        assert run(session_factory=sessions, now_utc=cutoff) == 2
        assert run(session_factory=sessions, now_utc=cutoff) == 0

        db.expire_all()
        for item in (ticket, task):
            assert not item.is_frozen and item.freeze_until is None
            assert item.status == TicketStatus.PENDING.value and item.assignee_id == agent.id
            assert [(msg.body, msg.agent_visible) for msg in _system_messages(db, item)] == [
                ("Заявка разморожена", True),
            ]
            _assert_system_history(_client(db, agent), item, ["Заявка разморожена"])
        assert ticket.auto_close_due_at is not None and task.auto_close_due_at is None
        assert future.is_frozen and future.freeze_until > now
        assert closed.is_frozen and closed.freeze_until == now
        assert not unfrozen.is_frozen and unfrozen.freeze_until == now
        assert no_date.is_frozen and no_date.freeze_until is None
        intervals = db.scalars(select(StatsWorkInterval)).all()
        assert {interval.object_id for interval in intervals} == {ticket.id, task.id}
        assert len(intervals) == 2 and all(interval.ended_at is None for interval in intervals)


def test_scheduler_rolls_back_failed_unfreeze_and_continues(monkeypatch):
    with _session() as db:
        company = _company(db)
        contact = _contact(db, company)
        now = datetime.utcnow()
        failed = _ticket(db, company, contact, is_frozen=True, freeze_until=now)
        succeeds = _ticket(db, company, contact, is_frozen=True, freeze_until=now)
        db.commit()
        failed_id = failed.id
        original = internal_task_scheduler.unfreeze_ticket

        def fail_one(db, ticket, **kwargs):
            original(db, ticket, **kwargs)
            if ticket.id == failed_id:
                raise RuntimeError("test failure after lifecycle")

        monkeypatch.setattr(internal_task_scheduler, "unfreeze_ticket", fail_one)
        sessions = sessionmaker(bind=db.get_bind(), autoflush=False)
        run = internal_task_scheduler.run_ticket_auto_unfreeze_once
        assert run(session_factory=sessions, now_utc=now) == 1
        db.expire_all()
        assert failed.is_frozen and failed.freeze_until == now
        assert not _system_messages(db, failed)
        assert not succeeds.is_frozen and succeeds.freeze_until is None

        monkeypatch.setattr(internal_task_scheduler, "unfreeze_ticket", original)
        assert run(session_factory=sessions, now_utc=now) == 1


@pytest.mark.parametrize("workflow", ["ticket", "task"])
def test_update_freeze_until_logs_only_initial_date(workflow, monkeypatch):
    monkeypatch.setattr(settings, "app_timezone", "Asia/Yekaterinburg")
    expected = "Добавлена дата авторазморозки: 01.01.2099, 10:15"
    with _session() as db:
        company = _company(db)
        agent = _user(db, company, "agent")
        contact = _contact(db, company)
        ticket = _ticket(db, company, contact, assignee=agent, workflow_type=workflow, is_frozen=True)
        db.commit()
        url = f"/api/v1/tickets/{ticket.id}/freeze-until"
        before = {column.key: getattr(ticket, column.key) for column in Ticket.__table__.columns if column.key != "freeze_until"}
        with _client(db, agent) as client:
            assert client.patch(url, json={}).status_code == 422
            assert client.patch(url, json={"freeze_until": "invalid"}).status_code == 422
            assert client.patch(url, json={"freeze_until": "2000-01-01T00:00:00Z"}).status_code == 422
            assert not _system_messages(db, ticket)
            deadline = {"freeze_until": "2099-01-01T08:15:00+03:00"}
            response = client.patch(url, json=deadline)
            assert response.status_code == 200
            assert datetime.fromisoformat(response.json()["freeze_until"].replace("Z", "+00:00")) == datetime(
                2099, 1, 1, 5, 15, tzinfo=timezone.utc,
            )
            db.refresh(ticket)
            assert ticket.freeze_until == datetime(2099, 1, 1, 5, 15)
            assert {key: getattr(ticket, key) for key in before} == before
            assert client.patch(url, json=deadline).status_code == 200
            assert client.patch(url, json={"freeze_until": "2099-01-02T05:15:00Z"}).status_code == 200
            response = client.patch(url, json={"freeze_until": None})
            assert response.status_code == 200 and response.json()["freeze_until"] is None
            db.refresh(ticket)
            assert ticket.freeze_until is None
            assert {key: getattr(ticket, key) for key in before} == before
            assert [(msg.body, msg.agent_visible) for msg in _system_messages(db, ticket)] == [(expected, True)]
            _assert_system_history(client, ticket, [expected])


@pytest.mark.parametrize("is_frozen,status", [(False, "new"), (True, "closed")])
def test_update_freeze_until_rejects_ineligible_tickets(is_frozen, status):
    with _session() as db:
        company = _company(db)
        admin = _user(db, company, "admin")
        contact = _contact(db, company)
        ticket = _ticket(db, company, contact, is_frozen=is_frozen, status=status)
        db.commit()
        with _client(db, admin) as client:
            response = client.patch(
                f"/api/v1/tickets/{ticket.id}/freeze-until", json={"freeze_until": "2099-01-01T05:15:00Z"},
            )
            assert response.status_code == 409
            assert not _system_messages(db, ticket)


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
    with _client(session, agent) as client:
        for item in (ticket, task):
            assert [(msg.body, msg.agent_visible) for msg in _system_messages(session, item)] == [
                ("Заявка заморожена", True),
            ]
            _assert_system_history(client, item, ["Заявка заморожена"])


def test_freeze_with_date_creates_one_visible_message_and_can_unfreeze(monkeypatch):
    monkeypatch.setattr(settings, "app_timezone", "Asia/Yekaterinburg")
    with _session() as db:
        company = _company(db)
        admin = _user(db, company, "admin")
        agent = _user(db, company, "agent")
        ticket = _ticket(db, company, _contact(db, company), assignee=agent)
        db.add(TicketMessage(
            id=str(uuid.uuid4()), ticket_id=ticket.id, message_type=MessageType.SYSTEM.value,
            body="Заявка заморожена", agent_visible=False, created_at=datetime(2025, 1, 1),
        ))
        db.commit()
        url = f"/api/v1/tickets/{ticket.id}"
        with _client(db, admin) as client:
            response = client.post(f"{url}/freeze", json={"freeze_until": "2099-01-01T08:15:00+03:00"})
            assert response.status_code == 200, response.text
            assert ticket.freeze_until == datetime(2099, 1, 1, 5, 15)
            assert client.post(f"{url}/unfreeze").status_code == 200
            expected = ["Заявка заморожена до 01.01.2099, 10:15", "Заявка разморожена"]
            _assert_system_history(client, ticket, ["Заявка заморожена", *expected])
        with _client(db, agent) as client:
            _assert_system_history(client, ticket, expected)
        assert [(msg.body, msg.agent_visible) for msg in _system_messages(db, ticket)] == [
            ("Заявка заморожена", False), *((body, True) for body in expected),
        ]


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
