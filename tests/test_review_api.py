from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import uuid
from zoneinfo import ZoneInfo

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.auth import CurrentUser
from api.app.config import settings
from api.app.db import Base, get_db
from api.app.models import Client, Company, Ticket, TicketReview, TicketReviewCycle, TicketReviewCycleState, User
from api.app.routers import reviews


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


def _client(session, user: CurrentUser):
    app = FastAPI()
    app.include_router(reviews.router)
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[reviews.get_current_user] = lambda: user
    return TestClient(app)


def _seed_base(session):
    company = Company(id="company-1", name="ACME")
    client = Client(id="client-1", company_id=company.id, display_name="Client Live")
    users = [
        User(id="admin-1", company_id=company.id, full_name="Admin User", role="admin", is_active=True),
        User(id="observer-1", company_id=company.id, full_name="Observer User", role="chiki_piki", is_active=True),
        User(id="agent-1", company_id=company.id, full_name="Agent Active", role="agent", is_active=True),
        User(id="trainee-1", company_id=company.id, full_name="Trainee Active", role="trainee", is_active=True),
        User(id="agent-old", company_id=company.id, full_name="Agent Old", role="agent", is_active=False),
        User(id="trainee-old", company_id=company.id, full_name="Trainee Old", role="trainee", is_active=False),
        User(id="agent-empty", company_id=company.id, full_name="Agent Empty", role="agent", is_active=False),
        User(id="trainee-history", company_id=company.id, full_name="Trainee History", role="trainee", is_active=False),
    ]
    ticket = Ticket(id="ticket-1", company_id=company.id, client_id=client.id, assignee_id="agent-1", subject="Live Subject")
    session.add_all([company, client, *users, ticket])
    session.commit()
    return company


def _add_ticket(session, ticket_id: str, company_id: str = "company-1", client_id: str = "client-1", subject: str = "Live Subject"):
    ticket = Ticket(id=ticket_id, company_id=company_id, client_id=client_id, subject=subject)
    session.add(ticket)
    session.flush()
    return ticket


def _add_review(
    session,
    *,
    review_id: str,
    ticket_id: str,
    agent_id: str,
    rated_at: datetime,
    rating: int = 5,
    review_text: str | None = "text",
    is_current: bool = True,
    version_number: int = 1,
    client_name_snapshot: str = "Client Snapshot",
    ticket_subject_snapshot: str = "Subject Snapshot",
):
    cycle = TicketReviewCycle(
        id=f"cycle-{review_id}",
        ticket_id=ticket_id,
        agent_id=agent_id,
        client_id="client-1",
        state=TicketReviewCycleState.COMPLETED.value,
        review_id=review_id,
    )
    review = TicketReview(
        id=review_id,
        ticket_id=ticket_id,
        agent_id=agent_id,
        client_id="client-1",
        cycle_id=cycle.id,
        previous_version_id=None,
        version_number=version_number,
        agent_name_snapshot="Agent Snapshot",
        client_name_snapshot=client_name_snapshot,
        ticket_subject_snapshot=ticket_subject_snapshot,
        rating=rating,
        review_text=review_text,
        rated_at=rated_at,
        is_current=is_current,
        created_at=rated_at,
        updated_at=rated_at,
    )
    session.add_all([cycle, review])
    session.commit()
    return review


def test_admin_gets_employee_selector_with_active_support_and_inactive_support_with_reviews():
    Session = _session_factory()
    session = Session()
    _seed_base(session)
    _add_ticket(session, "ticket-old")
    _add_review(
        session,
        review_id="review-old-agent",
        ticket_id="ticket-old",
        agent_id="agent-old",
        rated_at=datetime(2026, 9, 1, 10, 0, 0),
    )
    _add_ticket(session, "ticket-trainee-history")
    _add_review(
        session,
        review_id="review-old-trainee",
        ticket_id="ticket-trainee-history",
        agent_id="trainee-history",
        rated_at=datetime(2026, 9, 1, 10, 0, 0),
    )

    response = _client(session, _current_user("admin-1", "admin")).get("/api/v1/reviews/employees")

    assert response.status_code == 200
    items = response.json()
    ids = [item["employee_id"] for item in items]
    assert "agent-1" in ids
    assert "trainee-1" in ids
    assert "agent-old" in ids
    assert "trainee-history" in ids
    assert "agent-empty" not in ids
    assert "trainee-old" not in ids
    assert "admin-1" not in ids
    assert "observer-1" not in ids
    assert ids == sorted(ids, key=lambda item_id: next(item["display_name"] for item in items if item["employee_id"] == item_id))
    old_agent = next(item for item in items if item["employee_id"] == "agent-old")
    assert old_agent["role"] == "agent"
    assert old_agent["is_active"] is False


def test_employee_selector_requires_admin():
    Session = _session_factory()
    session = Session()
    _seed_base(session)

    response = _client(session, _current_user("agent-1", "agent")).get("/api/v1/reviews/employees")

    assert response.status_code == 403


def test_admin_without_employee_id_gets_empty_response_not_all_reviews():
    Session = _session_factory()
    session = Session()
    _seed_base(session)
    _add_review(
        session,
        review_id="review-1",
        ticket_id="ticket-1",
        agent_id="agent-1",
        rated_at=datetime(2026, 9, 1, 10, 0, 0),
    )

    response = _client(session, _current_user("admin-1", "admin")).get("/api/v1/reviews")

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "total_count": 0,
        "average_rating": None,
        "min_review_date": None,
        "page": 1,
        "pages": 1,
        "page_size": 30,
    }


def test_admin_gets_current_reviews_for_selected_employee_only():
    Session = _session_factory()
    session = Session()
    _seed_base(session)
    _add_ticket(session, "ticket-2")
    wanted = _add_review(
        session,
        review_id="review-agent",
        ticket_id="ticket-1",
        agent_id="agent-1",
        rated_at=datetime(2026, 9, 2, 10, 0, 0),
    )
    _add_review(
        session,
        review_id="review-trainee",
        ticket_id="ticket-2",
        agent_id="trainee-1",
        rated_at=datetime(2026, 9, 2, 11, 0, 0),
    )

    body = _client(session, _current_user("admin-1", "admin")).get(
        "/api/v1/reviews",
        params={"employee_id": "agent-1"},
    ).json()

    assert body["total_count"] == 1
    assert body["items"][0]["review_id"] == wanted.id
    assert body["items"][0]["agent_id"] == "agent-1"


def test_agent_and_trainee_get_only_own_reviews_and_employee_id_cannot_switch_context():
    Session = _session_factory()
    session = Session()
    _seed_base(session)
    _add_ticket(session, "ticket-2")
    _add_review(
        session,
        review_id="review-agent",
        ticket_id="ticket-1",
        agent_id="agent-1",
        rated_at=datetime(2026, 9, 2, 10, 0, 0),
    )
    _add_review(
        session,
        review_id="review-trainee",
        ticket_id="ticket-2",
        agent_id="trainee-1",
        rated_at=datetime(2026, 9, 2, 11, 0, 0),
    )

    agent_body = _client(session, _current_user("agent-1", "agent")).get(
        "/api/v1/reviews",
        params={"employee_id": "trainee-1"},
    ).json()
    trainee_body = _client(session, _current_user("trainee-1", "trainee")).get("/api/v1/reviews").json()

    assert [item["review_id"] for item in agent_body["items"]] == ["review-agent"]
    assert [item["review_id"] for item in trainee_body["items"]] == ["review-trainee"]


def test_observer_gets_forbidden_for_review_api():
    Session = _session_factory()
    session = Session()
    _seed_base(session)

    response = _client(session, _current_user("observer-1", "chiki_piki")).get("/api/v1/reviews")

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("app_timezone", "selected_date"),
    [
        ("Europe/Moscow", date(2026, 9, 1)),
        ("Europe/Berlin", date(2026, 3, 29)),
        ("Europe/Berlin", date(2026, 10, 25)),
    ],
)
@pytest.mark.parametrize("date_filter", ["both", "date_from", "date_to"])
def test_period_filter_uses_app_timezone_boundaries(monkeypatch, app_timezone, selected_date, date_filter):
    monkeypatch.setattr(settings, "app_timezone", app_timezone)
    Session = _session_factory()
    session = Session()
    _seed_base(session)
    local_timezone = ZoneInfo(app_timezone)
    start = datetime.combine(selected_date, time.min, tzinfo=local_timezone).astimezone(timezone.utc)
    end = datetime.combine(
        selected_date + timedelta(days=1), time.min, tzinfo=local_timezone
    ).astimezone(timezone.utc)
    for review_id, rated_at, rating in [
        ("review-before", start - timedelta(microseconds=1), 1),
        ("review-from", start, 4),
        ("review-to", end - timedelta(microseconds=1), 5),
        ("review-after", end, 2),
    ]:
        ticket_id = f"ticket-{review_id}"
        _add_ticket(session, ticket_id)
        _add_review(
            session,
            review_id=review_id,
            ticket_id=ticket_id,
            agent_id="agent-1",
            rated_at=rated_at,
            rating=rating,
        )

    params = {key: selected_date.isoformat() for key in ("date_from", "date_to") if date_filter in ("both", key)}
    response = _client(session, _current_user("agent-1", "agent")).get(
        "/api/v1/reviews",
        params=params,
    )

    assert response.status_code == 200
    body = response.json()
    expected_ids = {"review-from", "review-to"}
    expected_average = 4.5
    if date_filter == "date_from":
        expected_ids.add("review-after")
        expected_average = 3.67
    elif date_filter == "date_to":
        expected_ids.add("review-before")
        expected_average = 3.33
    assert body["total_count"] == len(expected_ids)
    assert body["average_rating"] == expected_average
    assert {item["review_id"] for item in body["items"]} == expected_ids


def test_history_is_available_for_current_review_inside_period_and_ignored_by_page_and_stats():
    Session = _session_factory()
    session = Session()
    _seed_base(session)
    _add_review(
        session,
        review_id="review-history-old",
        ticket_id="ticket-1",
        agent_id="agent-1",
        rated_at=datetime(2026, 8, 1, 10, 0, 0),
        rating=1,
        is_current=False,
        version_number=1,
    )
    current = _add_review(
        session,
        review_id="review-current",
        ticket_id="ticket-1",
        agent_id="agent-1",
        rated_at=datetime(2026, 9, 2, 10, 0, 0),
        rating=5,
        is_current=True,
        version_number=2,
    )

    client = _client(session, _current_user("agent-1", "agent"))
    body = client.get(
        "/api/v1/reviews",
        params={"date_from": "2026-09-01", "date_to": "2026-09-30", "page_size": 1},
    ).json()
    history = client.get(f"/api/v1/reviews/{current.id}/history").json()

    assert body["total_count"] == 1
    assert body["average_rating"] == 5.0
    assert len(body["items"]) == 1
    assert body["items"][0]["has_history"] is True
    assert [item["review_id"] for item in history["items"]] == ["review-history-old"]


def test_empty_period_stats_are_zero_and_null_average():
    Session = _session_factory()
    session = Session()
    _seed_base(session)
    _add_review(
        session,
        review_id="review-1",
        ticket_id="ticket-1",
        agent_id="agent-1",
        rated_at=datetime(2026, 9, 1, 10, 0, 0),
        rating=5,
    )

    body = _client(session, _current_user("agent-1", "agent")).get(
        "/api/v1/reviews",
        params={"date_from": "2026-09-02", "date_to": "2026-09-03"},
    ).json()

    assert body["total_count"] == 0
    assert body["average_rating"] is None
    assert body["items"] == []


def test_average_rating_counts_whole_period_not_current_page_and_includes_null_text():
    Session = _session_factory()
    session = Session()
    _seed_base(session)
    _add_ticket(session, "ticket-2")
    _add_review(
        session,
        review_id="review-1",
        ticket_id="ticket-1",
        agent_id="agent-1",
        rated_at=datetime(2026, 9, 1, 10, 0, 0),
        rating=5,
        review_text=None,
    )
    _add_review(
        session,
        review_id="review-2",
        ticket_id="ticket-2",
        agent_id="agent-1",
        rated_at=datetime(2026, 9, 2, 10, 0, 0),
        rating=4,
    )

    body = _client(session, _current_user("agent-1", "agent")).get(
        "/api/v1/reviews",
        params={"page_size": 1},
    ).json()

    assert len(body["items"]) == 1
    assert body["total_count"] == 2
    assert body["average_rating"] == 4.5


def test_reviews_are_sorted_by_rated_at_desc_then_id_desc():
    Session = _session_factory()
    session = Session()
    _seed_base(session)
    _add_ticket(session, "ticket-2")
    same_time = datetime(2026, 9, 1, 10, 0, 0)
    _add_review(session, review_id="review-a", ticket_id="ticket-1", agent_id="agent-1", rated_at=same_time)
    _add_review(session, review_id="review-z", ticket_id="ticket-2", agent_id="agent-1", rated_at=same_time)

    body = _client(session, _current_user("agent-1", "agent")).get("/api/v1/reviews").json()

    assert [item["review_id"] for item in body["items"]] == ["review-z", "review-a"]


def test_default_pagination_returns_30_current_reviews():
    Session = _session_factory()
    session = Session()
    _seed_base(session)
    base_time = datetime(2026, 9, 1, 10, 0, 0)
    for index in range(31):
        ticket_id = f"ticket-page-{index:02d}"
        _add_ticket(session, ticket_id)
        _add_review(
            session,
            review_id=f"review-page-{index:02d}",
            ticket_id=ticket_id,
            agent_id="agent-1",
            rated_at=base_time + timedelta(minutes=index),
        )

    body = _client(session, _current_user("agent-1", "agent")).get("/api/v1/reviews").json()

    assert body["page_size"] == 30
    assert body["total_count"] == 31
    assert body["pages"] == 2
    assert len(body["items"]) == 30


def test_has_history_false_when_pair_has_no_previous_versions():
    Session = _session_factory()
    session = Session()
    _seed_base(session)
    _add_review(
        session,
        review_id="review-1",
        ticket_id="ticket-1",
        agent_id="agent-1",
        rated_at=datetime(2026, 9, 1, 10, 0, 0),
    )

    body = _client(session, _current_user("agent-1", "agent")).get("/api/v1/reviews").json()

    assert body["items"][0]["has_history"] is False


@pytest.mark.parametrize("earliest_is_current", [False, True])
def test_min_review_date_uses_app_calendar_date_for_current_and_history(monkeypatch, earliest_is_current):
    monkeypatch.setattr(settings, "app_timezone", "Europe/Moscow")
    Session = _session_factory()
    session = Session()
    _seed_base(session)
    _add_ticket(session, "ticket-2")
    _add_review(
        session,
        review_id="review-history-old",
        ticket_id="ticket-1",
        agent_id="agent-1",
        rated_at=datetime(2026, 8, 1, 22, 30, tzinfo=timezone.utc),
        is_current=earliest_is_current,
    )
    _add_review(
        session,
        review_id="review-current",
        ticket_id="ticket-2",
        agent_id="agent-1",
        rated_at=datetime(2026, 9, 1, 10, 0, 0),
        is_current=True,
    )

    body = _client(session, _current_user("agent-1", "agent")).get(
        "/api/v1/reviews",
        params={"date_from": "2026-09-01"},
    ).json()

    assert body["min_review_date"] == "2026-08-02"
    assert body["items"][0]["rated_at"].startswith("2026-09-01T10:00:00")


def test_min_review_date_is_null_when_employee_has_no_reviews():
    Session = _session_factory()
    session = Session()
    _seed_base(session)

    response = _client(session, _current_user("agent-1", "agent")).get("/api/v1/reviews")

    assert response.status_code == 200
    assert response.json()["min_review_date"] is None


def test_review_text_preserves_line_breaks_and_ticket_subject_snapshot_is_returned():
    Session = _session_factory()
    session = Session()
    _seed_base(session)
    text = "line 1\nline 2\nline 3"
    _add_review(
        session,
        review_id="review-1",
        ticket_id="ticket-1",
        agent_id="agent-1",
        rated_at=datetime(2026, 9, 1, 10, 0, 0),
        review_text=text,
        ticket_subject_snapshot="Snapshot Subject",
    )

    body = _client(session, _current_user("agent-1", "agent")).get("/api/v1/reviews").json()

    assert body["items"][0]["review_text"] == text
    assert body["items"][0]["ticket_subject_snapshot"] == "Snapshot Subject"


def test_history_of_other_employee_is_forbidden_for_support_user():
    Session = _session_factory()
    session = Session()
    _seed_base(session)
    _add_ticket(session, "ticket-2")
    current = _add_review(
        session,
        review_id="review-trainee",
        ticket_id="ticket-2",
        agent_id="trainee-1",
        rated_at=datetime(2026, 9, 2, 10, 0, 0),
    )

    response = _client(session, _current_user("agent-1", "agent")).get(f"/api/v1/reviews/{current.id}/history")

    assert response.status_code == 403
