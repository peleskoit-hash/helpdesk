from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from api.app import main
from api.app.auth import CurrentUser
from api.app.db import get_db


PAGE_ROUTE = "/management/tasks"


def page_client(monkeypatch, role):
    user = None if role is None else CurrentUser(
        id="user-1",
        role=role,
        real_user_id="user-1",
        real_role=role,
        is_real_admin=role == "admin",
    )
    monkeypatch.setattr(main, "_require_page_auth", lambda request, db: user)
    app = FastAPI()
    for route in main.app.routes:
        if route.path == PAGE_ROUTE:
            app.add_api_route(route.path, route.endpoint, methods=["GET"])
    app.dependency_overrides[get_db] = lambda: None
    return TestClient(app)


@pytest.mark.parametrize("role", ["admin", "agent", "trainee", "chiki_piki"])
def test_management_tasks_page_is_admin_only(monkeypatch, role):
    response = page_client(monkeypatch, role).get(PAGE_ROUTE, follow_redirects=False)
    if role != "admin":
        assert response.status_code == 303
        assert response.headers["location"] == "/tickets"
        return
    assert response.status_code == 200
    assert 'data-shell-page="management-tasks"' in response.text
    assert "/static/management_tasks.js?v=20261015a" in response.text
    assert 'id="internalTaskCreateBtn"' in response.text


def test_management_tasks_page_redirects_anonymous_to_login(monkeypatch):
    response = page_client(monkeypatch, None).get(PAGE_ROUTE, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/management/tasks"
