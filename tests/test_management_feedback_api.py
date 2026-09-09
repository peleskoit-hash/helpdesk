from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.auth import CurrentUser
from api.app.db import Base, get_db
from api.app.models import Client, Company, Ticket, TicketFeedback, TicketWorkflowType, User
from api.app.routers import management_feedback


def _session_factory():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _current_user(user_id: str, role: str) -> CurrentUser:
    return CurrentUser(id=user_id, role=role, real_user_id=user_id, real_role=role, is_real_admin=role == "admin")


def _client(session, user: CurrentUser | None = None):
    app = FastAPI()
    app.include_router(management_feedback.router)
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[management_feedback.get_current_user] = lambda: user or _current_user("admin-1", "admin")
    return TestClient(app)


def _seed_base(session):
    company = Company(id="company-1", name="ACME")
    client = Client(id="client-1", company_id=company.id, display_name="Live Client")
    admin = User(id="admin-1", company_id=company.id, full_name="Admin", role="admin", is_active=True)
    agent = User(id="agent-1", company_id=company.id, full_name="Agent", role="agent", is_active=True)
    ticket = Ticket(
        id="ticket-1",
        ticket_number=557,
        company_id=company.id,
        client_id=client.id,
        assignee_id=agent.id,
        subject="Live ticket subject",
        workflow_type=TicketWorkflowType.TICKET.value,
    )
    task = Ticket(
        id="task-1",
        ticket_number=558,
        company_id=company.id,
        client_id=client.id,
        assignee_id=agent.id,
        subject="Live task subject",
        workflow_type=TicketWorkflowType.TASK.value,
    )
    session.add_all([company, client, admin, agent, ticket, task])
    session.commit()


def _add_feedback(
    session,
    *,
    feedback_id: str,
    ticket_id: str,
    submitted_at: datetime,
    client_name_snapshot: str = "Client Snapshot",
    ticket_title_snapshot: str = "Title Snapshot",
    text: str = "Feedback text",
) -> TicketFeedback:
    row = TicketFeedback(
        id=feedback_id,
        ticket_id=ticket_id,
        client_id="client-1",
        client_name_snapshot=client_name_snapshot,
        ticket_title_snapshot=ticket_title_snapshot,
        text=text,
        submitted_at=submitted_at,
        created_at=submitted_at,
        updated_at=submitted_at,
    )
    session.add(row)
    session.commit()
    return row


def test_admin_gets_feedback_list_and_snapshots():
    Session = _session_factory()
    session = Session()
    _seed_base(session)
    row = _add_feedback(
        session,
        feedback_id="feedback-1",
        ticket_id="ticket-1",
        submitted_at=datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc),
        client_name_snapshot="Snapshot Client",
        ticket_title_snapshot="Snapshot Title",
        text="Сделайте удобнее",
    )

    response = _client(session).get("/api/v1/management/feedback")

    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 1
    assert body["page"] == 1
    assert body["page_size"] == 30
    assert body["items"][0] == {
        "feedback_id": row.id,
        "submitted_at": "2026-09-06T10:00:00",
        "client_name_snapshot": "Snapshot Client",
        "ticket_title_snapshot": "Snapshot Title",
        "text": "Сделайте удобнее",
        "ticket_available": True,
        "ticket_id": "ticket-1",
        "ticket_number": 557,
        "workflow_type": "ticket",
    }


def test_agent_and_trainee_cannot_access_feedback():
    Session = _session_factory()
    session = Session()
    _seed_base(session)

    for role in ("agent", "trainee"):
        response = _client(session, _current_user(role + "-1", role)).get("/api/v1/management/feedback")
        assert response.status_code == 403


def test_newest_first_and_pagination():
    Session = _session_factory()
    session = Session()
    _seed_base(session)
    _add_feedback(session, feedback_id="old", ticket_id="ticket-1", submitted_at=datetime(2026, 9, 1, 10, 0))
    _add_feedback(session, feedback_id="new", ticket_id="task-1", submitted_at=datetime(2026, 9, 2, 10, 0))

    body = _client(session).get("/api/v1/management/feedback", params={"page": 2, "page_size": 1}).json()

    assert body["total_count"] == 2
    assert body["page"] == 2
    assert body["pages"] == 2
    assert [item["feedback_id"] for item in body["items"]] == ["old"]


def test_period_filter():
    Session = _session_factory()
    session = Session()
    _seed_base(session)
    _add_feedback(session, feedback_id="before", ticket_id="ticket-1", submitted_at=datetime(2026, 8, 31, 20, 59, tzinfo=timezone.utc))
    _add_feedback(session, feedback_id="inside", ticket_id="task-1", submitted_at=datetime(2026, 9, 1, 21, 0, tzinfo=timezone.utc))

    body = _client(session).get(
        "/api/v1/management/feedback",
        params={"date_from": "2026-09-02", "date_to": "2026-09-02"},
    ).json()

    assert body["total_count"] == 1
    assert body["items"][0]["feedback_id"] == "inside"


def test_search_by_ticket_number_client_title_and_text():
    Session = _session_factory()
    session = Session()
    _seed_base(session)
    _add_feedback(
        session,
        feedback_id="feedback-1",
        ticket_id="ticket-1",
        submitted_at=datetime(2026, 9, 1, 10, 0),
        client_name_snapshot="Мария Клиент",
        ticket_title_snapshot="Заблокировать учетку",
        text="Добавьте кнопку",
    )

    client = _client(session)
    for query in ("557", "Мария", "учетку", "кнопку"):
        body = client.get("/api/v1/management/feedback", params={"query": query}).json()
        assert body["total_count"] == 1
        assert body["items"][0]["feedback_id"] == "feedback-1"


def test_feedback_for_task_returns_task_workflow_type():
    Session = _session_factory()
    session = Session()
    _seed_base(session)
    _add_feedback(session, feedback_id="task-feedback", ticket_id="task-1", submitted_at=datetime(2026, 9, 1, 10, 0))

    body = _client(session).get("/api/v1/management/feedback").json()

    assert body["items"][0]["ticket_available"] is True
    assert body["items"][0]["ticket_number"] == 558
    assert body["items"][0]["workflow_type"] == "task"


def test_missing_joined_ticket_does_not_break_response():
    Session = _session_factory()
    session = Session()
    _seed_base(session)
    _add_feedback(
        session,
        feedback_id="orphan-feedback",
        ticket_id="missing-ticket",
        submitted_at=datetime(2026, 9, 1, 10, 0),
        ticket_title_snapshot="Deleted title",
    )

    body = _client(session).get("/api/v1/management/feedback").json()

    assert body["total_count"] == 1
    item = body["items"][0]
    assert item["ticket_available"] is False
    assert item["ticket_id"] == "missing-ticket"
    assert item["ticket_number"] is None
    assert item["workflow_type"] is None
    assert item["ticket_title_snapshot"] == "Deleted title"
