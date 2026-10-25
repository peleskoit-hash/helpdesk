from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.db import Base
from api.app.models import Client, Company, Ticket, TicketStatus, User
from api.app.routers import tickets as tickets_router
from api.app.routers import users as users_router


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


def _admin_user():
    return SimpleNamespace(
        id="admin-auth",
        role="admin",
        real_user_id="admin-auth",
        real_role="admin",
        is_real_admin=True,
    )


def _current_user(*, id: str, role: str, real_user_id: str | None = None, real_role: str | None = None, is_real_admin: bool = False):
    return SimpleNamespace(
        id=id,
        role=role,
        real_user_id=real_user_id or id,
        real_role=real_role or role,
        is_real_admin=is_real_admin,
    )


def _client(session) -> TestClient:
    app = FastAPI()
    app.include_router(users_router.router)
    app.dependency_overrides[users_router.get_db] = lambda: session
    app.dependency_overrides[users_router.get_current_user] = _admin_user
    return TestClient(app)


def _tickets_client(session, current_user=None) -> TestClient:
    app = FastAPI()
    app.include_router(tickets_router.router)
    app.dependency_overrides[tickets_router.get_db] = lambda: session
    app.dependency_overrides[tickets_router.get_current_user] = (lambda: current_user) if current_user is not None else _admin_user
    return TestClient(app)


def _company(session) -> Company:
    company = Company(id=str(uuid.uuid4()), name="ACME", is_active=True, created_at=datetime.utcnow())
    session.add(company)
    session.commit()
    return company


def _user(
    session,
    *,
    id: str | None = None,
    company_id: str,
    full_name: str,
    role: str,
    is_active: bool = True,
    availability_status: str = "online",
    avatar_path: str | None = None,
    created_at: datetime | None = None,
) -> User:
    now = created_at or datetime.utcnow() - timedelta(minutes=7)
    user = User(
        id=id or str(uuid.uuid4()),
        company_id=company_id,
        email=f"{uuid.uuid4().hex}@local.helpdesk",
        login=f"{full_name.lower().replace(' ', '.')}.{uuid.uuid4().hex[:6]}",
        password_hash="hash",
        avatar_path=avatar_path,
        full_name=full_name,
        role=role,
        is_active=is_active,
        availability_status=availability_status,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    session.commit()
    return user


def _client_record(session, *, company_id: str, name: str = "Client") -> Client:
    client = Client(id=str(uuid.uuid4()), company_id=company_id, display_name=name)
    session.add(client)
    session.commit()
    return client


def _ticket(
    session,
    *,
    company_id: str,
    client_id: str,
    assignee_id: str | None,
    status: str,
    is_frozen: bool = False,
    is_special: bool = False,
) -> Ticket:
    ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company_id,
        client_id=client_id,
        assignee_id=assignee_id,
        subject="Ticket",
        status=status,
        is_frozen=is_frozen,
        is_special=is_special,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(ticket)
    session.commit()
    return ticket


def test_status_board_returns_avatar_contract_and_preserves_existing_fields():
    session = _session()
    company = _company(session)
    with_avatar = _user(
        session,
        company_id=company.id,
        full_name="Anna Agent",
        role="agent",
        availability_status="waiting",
        avatar_path="avatars/anna.webp",
    )
    without_avatar = _user(
        session,
        company_id=company.id,
        full_name="Tim Trainee",
        role="trainee",
        availability_status="lunch",
    )

    result = users_router.status_board(db=session, current_user=_admin_user())
    payload = [item.model_dump() for item in result]

    assert [item["id"] for item in payload] == [with_avatar.id, without_avatar.id]
    assert payload[0] == {
        "id": with_avatar.id,
        "full_name": "Anna Agent",
        "role": "agent",
        "role_label": "Тех. поддержка",
        "availability_status": "waiting",
        "updated_at": with_avatar.updated_at,
        "avatar_present": True,
        "avatar_url": f"/api/v1/users/{with_avatar.id}/avatar",
    }
    assert payload[1]["full_name"] == "Tim Trainee"
    assert payload[1]["role"] == "trainee"
    assert payload[1]["role_label"] == "Стажёр"
    assert payload[1]["availability_status"] == "lunch"
    assert payload[1]["updated_at"] == without_avatar.updated_at
    assert payload[1]["avatar_present"] is False
    assert payload[1]["avatar_url"] is None


def test_status_board_employee_scope_rules_are_unchanged():
    session = _session()
    company = _company(session)
    agent = _user(session, company_id=company.id, full_name="Beta Agent", role="agent")
    trainee = _user(session, company_id=company.id, full_name="Alpha Trainee", role="trainee")
    _user(session, company_id=company.id, full_name="Admin User", role="admin")
    _user(session, company_id=company.id, full_name="Chiki User", role="chiki_piki")
    _user(session, company_id=company.id, full_name="Inactive Agent", role="agent", is_active=False)

    result = users_router.status_board(db=session, current_user=_admin_user())

    assert [item.id for item in result] == [agent.id, trainee.id]


def test_regular_agent_endpoint_does_not_expose_status_board_avatar_fields():
    session = _session()
    company = _company(session)
    agent = _user(
        session,
        company_id=company.id,
        full_name="Avatar Agent",
        role="agent",
        avatar_path="avatars/avatar-agent.webp",
    )

    response = _client(session).get("/api/v1/users/agents")

    assert response.status_code == 200
    assert response.json()[0]["id"] == agent.id
    assert "avatar_present" not in response.json()[0]
    assert "avatar_url" not in response.json()[0]


def test_agents_endpoint_orders_by_created_at_then_id():
    session = _session()
    company = _company(session)
    newer = _user(
        session,
        id="agent-newer",
        company_id=company.id,
        full_name="A Newer",
        role="agent",
        created_at=datetime(2024, 1, 2, 10, 0, 0),
    )
    same_time_second = _user(
        session,
        id="agent-b",
        company_id=company.id,
        full_name="B Same",
        role="agent",
        created_at=datetime(2024, 1, 1, 10, 0, 0),
    )
    same_time_first = _user(
        session,
        id="agent-a",
        company_id=company.id,
        full_name="Z Same",
        role="trainee",
        created_at=datetime(2024, 1, 1, 10, 0, 0),
    )

    response = _client(session).get("/api/v1/users/agents")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [same_time_first.id, same_time_second.id, newer.id]


def test_ticket_counts_preserve_global_semantics_and_return_admin_by_assignee():
    session = _session()
    company = _company(session)
    client = _client_record(session, company_id=company.id)
    agent_a = _user(session, id="agent-a", company_id=company.id, full_name="Agent A", role="agent")
    agent_b = _user(session, id="agent-b", company_id=company.id, full_name="Agent B", role="trainee")
    inactive = _user(session, id="agent-inactive", company_id=company.id, full_name="Inactive", role="agent", is_active=False)
    _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent_a.id, status=TicketStatus.NEW.value)
    _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent_a.id, status=TicketStatus.PENDING.value, is_frozen=True)
    _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent_b.id, status=TicketStatus.PENDING.value)
    _ticket(session, company_id=company.id, client_id=client.id, assignee_id=inactive.id, status=TicketStatus.NEW.value)
    _ticket(session, company_id=company.id, client_id=client.id, assignee_id=None, status=TicketStatus.UNASSIGNED.value)
    _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent_a.id, status=TicketStatus.CLOSED.value)
    _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent_b.id, status=TicketStatus.NEW.value, is_special=True)

    payload = _tickets_client(session).get(f"/api/v1/tickets/counts?assignee_ids={agent_a.id}&assignee_ids={agent_b.id}&assignee_ids={inactive.id}").json()

    assert payload["active"] == 3
    assert payload["frozen"] == 1
    assert payload["unassigned"] == 1
    assert payload["closed"] == 1
    assert payload["special"] == 1
    assert payload["by_assignee"] == {
        agent_a.id: {"active": 1, "frozen": 1},
        agent_b.id: {"active": 1, "frozen": 0},
    }


def test_ticket_counts_assignee_filter_only_changes_global_active_frozen_not_by_assignee():
    session = _session()
    company = _company(session)
    client = _client_record(session, company_id=company.id)
    agent_a = _user(session, id="agent-a", company_id=company.id, full_name="Agent A", role="agent")
    agent_b = _user(session, id="agent-b", company_id=company.id, full_name="Agent B", role="agent")
    _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent_a.id, status=TicketStatus.NEW.value)
    _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent_b.id, status=TicketStatus.NEW.value)
    _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent_b.id, status=TicketStatus.PENDING.value, is_frozen=True)
    _ticket(session, company_id=company.id, client_id=client.id, assignee_id=None, status=TicketStatus.UNASSIGNED.value)
    _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent_b.id, status=TicketStatus.CLOSED.value)
    _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent_b.id, status=TicketStatus.NEW.value, is_special=True)

    response = _tickets_client(session).get(f"/api/v1/tickets/counts?assignee_ids={agent_a.id}")
    payload = response.json()

    assert response.status_code == 200
    assert payload["active"] == 1
    assert payload["frozen"] == 0
    assert payload["unassigned"] == 1
    assert payload["closed"] == 1
    assert payload["special"] == 1
    assert payload["by_assignee"] == {
        agent_a.id: {"active": 1, "frozen": 0},
        agent_b.id: {"active": 1, "frozen": 1},
    }


def test_ticket_counts_real_admin_impersonation_gets_unrestricted_by_assignee():
    session = _session()
    company = _company(session)
    client = _client_record(session, company_id=company.id)
    admin = _user(session, id="admin-user", company_id=company.id, full_name="Admin", role="admin")
    agent_a = _user(session, id="agent-a", company_id=company.id, full_name="Agent A", role="agent")
    agent_b = _user(session, id="agent-b", company_id=company.id, full_name="Agent B", role="trainee")
    _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent_a.id, status=TicketStatus.NEW.value)
    _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent_b.id, status=TicketStatus.NEW.value)
    _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent_b.id, status=TicketStatus.PENDING.value, is_frozen=True)

    impersonated_admin = _current_user(
        id=agent_a.id,
        role="agent",
        real_user_id=admin.id,
        real_role="admin",
        is_real_admin=True,
    )
    payload = _tickets_client(session, current_user=impersonated_admin).get("/api/v1/tickets/counts").json()

    assert payload["active"] == 1
    assert payload["frozen"] == 0
    assert payload["by_assignee"] == {
        agent_a.id: {"active": 1, "frozen": 0},
        agent_b.id: {"active": 1, "frozen": 1},
    }


def test_ticket_counts_regular_agent_does_not_receive_by_assignee():
    session = _session()
    company = _company(session)
    client = _client_record(session, company_id=company.id)
    agent = _user(session, id="agent-a", company_id=company.id, full_name="Agent A", role="agent")
    _ticket(session, company_id=company.id, client_id=client.id, assignee_id=agent.id, status=TicketStatus.NEW.value)

    payload = _tickets_client(session, current_user=_current_user(id=agent.id, role="agent")).get("/api/v1/tickets/counts").json()

    assert payload["active"] == 1
    assert "by_assignee" not in payload
