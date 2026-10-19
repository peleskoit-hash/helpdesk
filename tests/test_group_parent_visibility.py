"""Group parents are admin-only, including archive exceptions and direct lookups."""
from datetime import datetime
import os
from types import SimpleNamespace
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.app.auth import get_current_user
from api.app.db import Base, get_db
from api.app.models import Client, ClientProfile, Company, Ticket, TicketTaskKind, TicketView, User
from api.app.routers.tickets import router


@pytest.fixture(params=["sqlite", "postgresql"])
def database(request):
    admin = None
    if request.param == "sqlite":
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
    else:
        url = os.environ.get("TICKET_SEARCH_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Set TICKET_SEARCH_TEST_DATABASE_URL for isolated PostgreSQL schemas")
        admin = create_engine(url)
        schema = "parent_visibility_" + uuid.uuid4().hex
        with admin.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {schema}"))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        Base.metadata.create_all(engine)
        yield engine
    finally:
        engine.dispose()
        if admin is not None:
            with admin.begin() as connection:
                connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
            admin.dispose()


@pytest.fixture(params=["admin", "agent", "trainee", "chiki_piki", "future_role"])
def case(request, database):
    engine = database
    with Session(engine, autoflush=False) as db:
        db.add(Company(id="company", name="Synthetic"))
        db.flush()
        db.add_all([User(id="viewer", company_id="company", role=request.param, full_name="Viewer"),
                    User(id="other", company_id="company", role="agent", full_name="Other")])
        db.add(Client(id="client", company_id="company", display_name="Клиент"))
        db.flush()
        db.add(ClientProfile(id="profile", client_id="client", custom_fields={"custom_fio": "Целевой Клиент"}))
        now = datetime(2026, 10, 19, 12)

        def ticket(key, status="new", kind="normal", **values):
            row = Ticket(id=key, ticket_number=len(db.new) + 1, company_id="company", client_id="client",
                         assignee_id="viewer", subject="Общая тема", status=status, task_kind=kind,
                         workflow_type="task", button_config_key="Колл Центр|ticket|test",
                         agent_visible=True, created_at=now, updated_at=now)
            for name, value in values.items():
                setattr(row, name, value)
            db.add(row)
            return row

        for status in ["new", "pending", "unassigned", "closed"]:
            ticket("parent-" + status, status, TicketTaskKind.GROUP_PARENT.value,
                   agent_visible=status != "closed")
        ticket("parent-frozen", kind="group_parent", is_frozen=True)
        ticket("parent-special", kind="group_parent", is_special=True)
        ticket("normal-active")
        ticket("child-active", kind="group_child", parent_ticket_id="parent-new")
        ticket("normal-closed", "closed", assignee_id="other", agent_visible=False)
        ticket("child-closed", "closed", "group_child", assignee_id="other", parent_ticket_id="parent-closed")
        ticket("other-active", assignee_id="other")
        ticket("hidden-active", agent_visible=False)
        ticket("outside-call-center", button_config_key="Other|ticket|test")
        db.flush()
        for key in ["parent-new", "normal-active", "child-active"]:
            db.add(TicketView(id=key, ticket_id=key, user_id="viewer", first_opened_at=now, last_opened_at=now))
        db.commit()
        user = SimpleNamespace(id="viewer", role=request.param, real_user_id="viewer",
                               real_role=request.param, is_real_admin=False)
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: user
        with TestClient(app) as http:
            yield SimpleNamespace(db=db, http=http, role=request.param)


def test_lists_search_pagination_and_counts(case):
    for tab in ["active", "frozen", "closed", "unassigned", "special"]:
        params = {"assignee_ids": "viewer", "limit": 100}
        response = case.http.get("/api/v1/tickets/" + tab, params=params)
        if ((tab == "special" and case.role != "admin")
                or (tab == "unassigned" and case.role not in {"admin", "chiki_piki"})):
            assert response.status_code == 403
            continue
        assert response.status_code == 200, response.text
        baseline = response.json()
        ids = {item["id"] for item in baseline["items"]}
        if case.role == "admin":
            assert any(key.startswith("parent-") for key in ids)
        else:
            assert not any(key.startswith("parent-") for key in ids)
        for query in ["Общая", "цЕЛЕВОЙ"]:
            searched = case.http.get("/api/v1/tickets/" + tab, params=dict(params, query=query, limit=1)).json()
            assert searched["total"] == baseline["total"]
            assert searched["pages"] == max(1, baseline["total"])
            assert all(item["id"] in ids for item in searched["items"])
        if tab == "closed":
            assert {"normal-closed", "child-closed"} <= ids
        if tab == "active":
            assert {"normal-active", "child-active"} <= ids
            if case.role in {"agent", "trainee"}:
                assert not {"other-active", "hidden-active"} & ids
            if case.role == "chiki_piki":
                assert "outside-call-center" not in ids
        counts = case.http.get("/api/v1/tickets/counts", params={"assignee_ids": "viewer"}).json()
        assert counts[tab] == baseline["total"]


def test_direct_card_by_id_and_number(case):
    for key in ["parent-new", "parent-pending", "parent-unassigned", "parent-closed", "parent-frozen", "parent-special"]:
        number = case.db.get(Ticket, key).ticket_number
        for identifier in [key, str(number)]:
            response = case.http.get("/api/v1/tickets/" + identifier)
            assert response.status_code == (200 if case.role == "admin" else 404), response.text
    for key in ["normal-active", "normal-closed", "child-active", "child-closed"]:
        response = case.http.get("/api/v1/tickets/" + key)
        assert response.status_code == 200, response.text


def test_task_panel_excludes_parent_even_if_visible_flag_is_true(case):
    response = case.http.get("/api/v1/tickets/task-panel")
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()}
    assert {"normal-active", "child-active"} <= ids
    assert ("parent-new" in ids) == (case.role == "admin")


def test_nested_ticket_read_paths_share_parent_guard(case):
    if case.role == "admin":
        assert case.http.get("/api/v1/tickets/parent-closed/client-profile").status_code == 200
        return
    for suffix in ["client-profile", "attachments/missing/download"]:
        response = case.http.get("/api/v1/tickets/parent-closed/" + suffix)
        assert response.status_code == 404, response.text
        assert response.json()["detail"]["error"]["code"] == "ticket_not_found"
